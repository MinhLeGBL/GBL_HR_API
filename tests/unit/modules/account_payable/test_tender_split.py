"""
Unit tests for CR #72 — split-tender reconciliation (Gift Certificate
allocation separate from cash/card).

Covers:
- `_summarize_tender_breakdown`: classification + per-category amount totals.
- `_is_commission_releasing_tender`: GC excluded, everything else included.
- Repository `get_tender_breakdowns`: bind-list expansion, dict shape.
- Reconcile validation: tender_category accepted/rejected, per-category caps.
- Reconcile persistence: tender_category column written.
- `compute_released_for_month`: GC entries excluded from release math.
- Replay: tender_category propagated into payments_chrono.
- PendingPayment shape: tender_breakdown + amounts surfaced.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.account_payable.repository import AccountPayableRepository
from app.modules.account_payable.service import AccountPayableService


FROZEN_TODAY = date(2026, 6, 8)


# ----------------------------------------------------------------------
# Helpers — `_summarize_tender_breakdown`
# ----------------------------------------------------------------------

class TestSummarizeTenderBreakdown:

    def test_empty_input_returns_zeroes(self):
        legs, releasing, gift = AccountPayableService._summarize_tender_breakdown([])
        assert legs == []
        assert releasing == 0
        assert gift == 0

    def test_cash_only_payment(self):
        """Single MC tender + offsetting Charge → 100% cash/card."""
        raw = [
            {'tender_sid': 'T1', 'tender_name': 'MC',     'amount':  500_000},
            {'tender_sid': 'T2', 'tender_name': 'Charge', 'amount': -500_000},
        ]
        legs, releasing, gift = AccountPayableService._summarize_tender_breakdown(raw)
        assert releasing == 500_000
        assert gift == 0
        # Wire shape: 2 legs, both with is_commission_releasing flag.
        assert len(legs) == 2
        assert legs[0]['tender_name'] == 'MC'
        assert legs[0]['is_commission_releasing'] is True
        assert legs[1]['tender_name'] == 'Charge'

    def test_split_mc_plus_gift_certificate(self):
        """Canonical split: MC + GC → both totals populated."""
        raw = [
            {'tender_sid': 'T1', 'tender_name': 'MC',               'amount':  190_000_000},
            {'tender_sid': 'T2', 'tender_name': 'Charge',           'amount': -190_000_000},
            {'tender_sid': 'T3', 'tender_name': 'Gift Certificate', 'amount':   42_500_000},
            {'tender_sid': 'T4', 'tender_name': 'Charge',           'amount':  -42_500_000},
        ]
        legs, releasing, gift = AccountPayableService._summarize_tender_breakdown(raw)
        assert releasing == 190_000_000
        assert gift == 42_500_000
        # Per-leg `is_commission_releasing` is True for MC, False for GC.
        names_to_releasing = {leg['tender_name']: leg['is_commission_releasing']
                              for leg in legs}
        assert names_to_releasing['MC'] is True
        assert names_to_releasing['Gift Certificate'] is False

    def test_negative_charge_legs_ignored_in_totals(self):
        """Charge legs (always negative) must NOT contribute to either total."""
        raw = [
            {'tender_sid': 'T1', 'tender_name': 'Cash',   'amount':  100_000},
            {'tender_sid': 'T2', 'tender_name': 'Charge', 'amount': -100_000},
        ]
        _, releasing, gift = AccountPayableService._summarize_tender_breakdown(raw)
        assert releasing == 100_000
        assert gift == 0

    def test_multiple_gc_legs_sum_to_gift_total(self):
        """Edge case: two GC legs combined on the same doc → summed in total."""
        raw = [
            {'tender_sid': 'T1', 'tender_name': 'Gift Certificate', 'amount': 30_000_000},
            {'tender_sid': 'T2', 'tender_name': 'Charge',           'amount': -30_000_000},
            {'tender_sid': 'T3', 'tender_name': 'Gift Certificate', 'amount': 15_000_000},
            {'tender_sid': 'T4', 'tender_name': 'Charge',           'amount': -15_000_000},
        ]
        _, releasing, gift = AccountPayableService._summarize_tender_breakdown(raw)
        assert releasing == 0
        assert gift == 45_000_000


class TestIsCommissionReleasingTender:

    def test_gift_certificate_excluded(self):
        assert AccountPayableService._is_commission_releasing_tender('Gift Certificate') is False

    def test_common_money_in_tenders_included(self):
        for name in ('MC', 'Cash', 'VISA', 'AMEX', 'Bank Transfer'):
            assert AccountPayableService._is_commission_releasing_tender(name) is True, (
                f'{name!r} should release commission'
            )

    def test_unknown_tender_defaults_to_releasing(self):
        """Unknown / future tender names release by default. Only explicit
        non-releasing names (GC today) are filtered out."""
        assert AccountPayableService._is_commission_releasing_tender('SomeNewTender') is True
        assert AccountPayableService._is_commission_releasing_tender(None) is True


# ----------------------------------------------------------------------
# Repository — `get_tender_breakdowns`
# ----------------------------------------------------------------------

class TestRepositoryGetTenderBreakdowns:

    def test_empty_list_returns_empty_without_query(self):
        repo = AccountPayableRepository()
        assert repo.get_tender_breakdowns([]) == {}

    def test_rejects_non_numeric_sid(self):
        repo = AccountPayableRepository()
        with pytest.raises(ValueError, match='numeric'):
            repo.get_tender_breakdowns(['1234', 'not_digits'])

    @patch('app.modules.account_payable.repository.get_oracle_connection')
    def test_groups_legs_by_payment_doc_sid(self, mock_get_conn):
        """Rows from Oracle are grouped by payment_doc_sid with stable order
        (the SQL ORDER BY t.doc_sid, t.sid is preserved by the dict insertion)."""
        cur = MagicMock()
        cur.description = [
            ('PAYMENT_DOC_SID',), ('TENDER_SID',), ('TENDER_NAME',), ('AMOUNT',),
        ]
        cur.fetchall.return_value = [
            ('1000', 'T1', 'MC', 100_000),
            ('1000', 'T2', 'Charge', -100_000),
            ('2000', 'T3', 'Gift Certificate', 50_000),
            ('2000', 'T4', 'Charge', -50_000),
        ]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        mock_get_conn.return_value = conn

        repo = AccountPayableRepository()
        out = repo.get_tender_breakdowns(['1000', '2000'])

        assert set(out.keys()) == {'1000', '2000'}
        assert out['1000'] == [
            {'tender_sid': 'T1', 'tender_name': 'MC', 'amount': 100_000},
            {'tender_sid': 'T2', 'tender_name': 'Charge', 'amount': -100_000},
        ]
        assert out['2000'][0]['tender_name'] == 'Gift Certificate'

    @patch('app.modules.account_payable.repository.get_oracle_connection')
    def test_connection_failure_returns_empty(self, mock_get_conn):
        mock_get_conn.return_value = None
        repo = AccountPayableRepository()
        assert repo.get_tender_breakdowns(['1234']) == {}


# ----------------------------------------------------------------------
# Replay — tender_category propagation
# ----------------------------------------------------------------------

class TestReplayTenderCategoryPropagation:

    def _evt(self, doc_sid, amount, date_str, ref=None):
        return {
            'doc_sid': doc_sid, 'doc_no': f'D-{doc_sid}',
            'doc_store_code': 'HBT', 'customer_sid': '1', 'customer_name': 'A',
            'charge_amount': amount, 'post_date_str': date_str,
            'post_month': date_str[:7],
            'ref_sale_sid': ref, 'sale_total_amt': abs(amount),
        }

    def test_ref_sale_sid_match_is_cash_card(self):
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology([
            self._evt('B1', 1_000_000, '2026-04-01'),
            self._evt('PAY1', -1_000_000, '2026-04-10', ref='B1'),
        ])
        assert bills['B1']['payments_chrono'][0]['tender_category'] == 'cash_card'

    def test_manual_default_cash_card(self):
        """Manual rows without tender_category default to cash_card."""
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology(
            events=[
                self._evt('B1', 1_000_000, '2026-04-01'),
                self._evt('PAY1', -1_000_000, '2026-04-10'),
            ],
            manual_allocations_by_payment={
                'PAY1': [{'bill_sid': 'B1', 'amount_applied': 1_000_000}],
            },
        )
        chrono = bills['B1']['payments_chrono'][0]
        assert chrono['source'] == 'manual'
        assert chrono['tender_category'] == 'cash_card'

    def test_manual_gift_certificate_propagates(self):
        """A manual row tagged GC carries that label into the chrono entry."""
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology(
            events=[
                self._evt('B1', 500_000, '2026-04-01'),
                self._evt('PAY1', -500_000, '2026-04-10'),
            ],
            manual_allocations_by_payment={
                'PAY1': [{
                    'bill_sid': 'B1', 'amount_applied': 500_000,
                    'tender_category': 'gift_certificate',
                }],
            },
        )
        chrono = bills['B1']['payments_chrono'][0]
        assert chrono['tender_category'] == 'gift_certificate'

    def test_split_tender_creates_two_chrono_entries(self):
        """A payment with BOTH cash_card and GC rows against the same bill
        produces TWO chrono entries (one per tender_category)."""
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology(
            events=[
                self._evt('B1', 1_000_000, '2026-04-01'),
                # Total ledger payment magnitude = 700k (will fan-out).
                self._evt('PAY1', -700_000, '2026-04-10'),
            ],
            manual_allocations_by_payment={
                'PAY1': [
                    {'bill_sid': 'B1', 'amount_applied': 500_000,
                     'tender_category': 'cash_card'},
                    {'bill_sid': 'B1', 'amount_applied': 200_000,
                     'tender_category': 'gift_certificate'},
                ],
            },
        )
        chronos = bills['B1']['payments_chrono']
        assert len(chronos) == 2
        cats = sorted(c['tender_category'] for c in chronos)
        assert cats == ['cash_card', 'gift_certificate']
        # Both consume against the bill's remaining.
        assert bills['B1']['remaining'] == 300_000


# ----------------------------------------------------------------------
# Reconcile validation — tender_category
# ----------------------------------------------------------------------

@pytest.fixture
def sample_bills():
    return {
        '1001': {
            'doc_no': 'D-1', 'doc_store_code': 'HBT',
            'customer_sid': '1', 'customer_name': 'A',
            'original_charge': 1_000_000, 'total_paid': 0,
            'remaining_unpaid': 1_000_000, 'status': 'open',
            'created_date': '2026-04-10', 'last_payment_date': None,
            'sale_total_amt': 1_100_000, 'post_month': '2026-04',
            'payments': [], 'total_voided': 0,
        },
    }


@pytest.fixture
def mock_repo(sample_bills):
    repo = MagicMock()
    repo.get_all_bills.return_value = sample_bills
    repo.get_bill_items.return_value = []
    repo.get_charge_payments.return_value = []
    # Split-tender payment: 600k MC + 400k GC = 1M Charge.
    repo.get_tender_breakdowns.return_value = {
        '9001': [
            {'tender_sid': 'T1', 'tender_name': 'MC', 'amount': 600_000},
            {'tender_sid': 'T2', 'tender_name': 'Charge', 'amount': -600_000},
            {'tender_sid': 'T3', 'tender_name': 'Gift Certificate', 'amount': 400_000},
            {'tender_sid': 'T4', 'tender_name': 'Charge', 'amount': -400_000},
        ],
    }
    repo.get_payment_meta.return_value = {
        'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '1',
        'customer_name': 'A', 'doc_store_code': 'HBT',
        'amount': 1_000_000, 'payment_date': '2026-04-30',
        'notes_lostdoc': None, 'ref_sale_sid': None,
    }
    return repo


@pytest.fixture
def service(mock_repo):
    return AccountPayableService(repository=mock_repo)


@pytest.fixture(autouse=True)
def freeze_today():
    with patch('app.modules.account_payable.service._today', return_value=FROZEN_TODAY):
        yield


@pytest.fixture
def mock_pg():
    with patch('app.modules.account_payable.service.get_postgres_connection') as m:
        conn = MagicMock(); cur = MagicMock()
        cur.fetchall.side_effect = [[], []]
        cur.fetchone.return_value = (0,)
        cur.rowcount = 0
        conn.cursor.return_value.__enter__.return_value = cur
        m.return_value = conn
        with patch.object(AccountPayableService, '_load_auto_rates', return_value={}), \
             patch.object(AccountPayableService, '_load_custom_rates', return_value={}), \
             patch.object(AccountPayableService, '_load_manual_allocations', return_value={}), \
             patch.object(AccountPayableService, '_load_voids_by_bill_sid', return_value={}):
            yield m, conn, cur


class TestReconcileTenderCategoryValidation:

    def test_invalid_tender_category_rejected(self, service):
        r = service.reconcile('9001', [{
            'bill_sid': '1001', 'amount': 100_000,
            'tender_category': 'paypal',
        }])
        assert r['success'] is False
        assert 'tender_category' in r['error']

    def test_missing_tender_category_defaults_to_cash_card(self, service, mock_pg):
        """Pre-CR-#72 clients (no tender_category in the request body) must
        still work — backend defaults to 'cash_card'."""
        r = service.reconcile('9001', [{'bill_sid': '1001', 'amount': 600_000}])
        assert r['success'] is True
        _, _, cur = mock_pg
        # INSERT row passes 'cash_card' for tender_category.
        insert_params = cur.executemany.call_args_list[0].args[1]
        assert len(insert_params) == 1
        # Params: (payment_doc_sid, bill_sid, amount, tender_category, created_by)
        assert insert_params[0][3] == 'cash_card'

    def test_gc_allocation_exceeds_gc_cap_rejected(self, service):
        """Payment 9001 has 400k of GC tender. Trying to allocate 500k of
        GC must be rejected with a per-category cap error."""
        r = service.reconcile('9001', [{
            'bill_sid': '1001', 'amount': 500_000,
            'tender_category': 'gift_certificate',
        }])
        assert r['success'] is False
        assert 'gift-certificate' in r['error']
        assert '500,000' in r['error']
        assert '400,000' in r['error']

    def test_cash_allocation_exceeds_cash_cap_rejected(self, service):
        """Payment 9001 has 600k of MC tender. Allocating 700k cash_card
        plus 1k GC overflows the MC cap (not the total)."""
        r = service.reconcile('9001', [
            {'bill_sid': '1001', 'amount': 700_000, 'tender_category': 'cash_card'},
            {'bill_sid': '1001', 'amount':   1_000, 'tender_category': 'gift_certificate'},
        ])
        assert r['success'] is False
        assert 'cash/card' in r['error']
        assert '700,000' in r['error']
        assert '600,000' in r['error']

    def test_split_allocation_within_caps_accepted(self, service, mock_pg):
        """Fully-split allocation: 600k cash + 400k GC against the same
        bill must succeed and write TWO rows to payable_reconciliations."""
        r = service.reconcile('9001', [
            {'bill_sid': '1001', 'amount': 600_000, 'tender_category': 'cash_card'},
            {'bill_sid': '1001', 'amount': 400_000, 'tender_category': 'gift_certificate'},
        ])
        assert r['success'] is True
        assert r['data']['outcome'] == 'released'  # past month, fully allocated
        _, _, cur = mock_pg
        insert_params = cur.executemany.call_args_list[0].args[1]
        assert len(insert_params) == 2
        categories = sorted(p[3] for p in insert_params)
        assert categories == ['cash_card', 'gift_certificate']


# ----------------------------------------------------------------------
# Reconcile response — only cash_card releases commission
# ----------------------------------------------------------------------

class TestReconcileResponseGCExcludesRelease:

    def test_gc_only_past_month_releases_zero(self, service, mock_repo, mock_pg):
        """Past-month payment fully allocated as GC → total_release_amount = 0
        (GC doesn't release commission)."""
        # Item on bill so the release math has something to compute.
        mock_repo.get_bill_items.return_value = [{
            'bill_sid': '1001', 'sale_id': 'S1', 'upc': 'DRESS-A',
            'description': 'Test', 'qty': 1, 'vendor_code': 'JQM',
            'is_jewelry': 0, 'category': 'OTHER', 'department': 'WRTW',
            'discount_rate': 0.0, 'revenue_with_vat': 1_000_000,
            'employee_sid': 'E1', 'employee_code': 'GH001',
            'employee_name': 'Alice', 'employee_store_code': 'HBT',
        }]

        # Allocate 400k GC → past month → release should be 0.
        r = service.reconcile('9001', [{
            'bill_sid': '1001', 'amount': 400_000,
            'tender_category': 'gift_certificate',
        }])
        assert r['success'] is True
        # 400k of 1M → matched_partially (past month).
        assert r['data']['outcome'] == 'matched_partially'
        assert r['data']['total_release_amount'] == 0
        assert r['data']['affected_employee_count'] == 0


# ----------------------------------------------------------------------
# compute_released_for_month — GC entries skipped
# ----------------------------------------------------------------------

class TestComputeReleasedFiltersGiftCertificate:

    def _bill_with_payments(self, payments_chrono):
        return {
            '1001': {
                'doc_no': 'D-1', 'doc_store_code': 'HBT',
                'customer_sid': '1', 'customer_name': 'A',
                'original_charge': 1_000_000, 'total_paid': sum(p['amount_applied'] for p in payments_chrono),
                'remaining_unpaid': 1_000_000 - sum(p['amount_applied'] for p in payments_chrono),
                'status': 'partial',
                'created_date': '2026-04-10', 'last_payment_date': payments_chrono[0]['payment_date'],
                'sale_total_amt': 1_100_000, 'post_month': '2026-04',
                'payments': payments_chrono,
            },
        }

    def _item(self, employee_code='GH001'):
        return {
            'bill_sid': '1001', 'sale_id': 'S1', 'upc': 'DRESS-A',
            'description': 'Test', 'qty': 1, 'vendor_code': 'JQM',
            'is_jewelry': 0, 'category': 'OTHER', 'department': 'WRTW',
            'discount_rate': 0.0, 'revenue_with_vat': 1_000_000,
            'employee_sid': 'E1', 'employee_code': employee_code,
            'employee_name': 'Alice', 'employee_store_code': 'HBT',
        }

    def _build_service(self, bills, items, auto_rates=None):
        repo = MagicMock()
        repo.get_bill_items.return_value = items
        svc = AccountPayableService(repository=repo)
        svc._load_bills = MagicMock(return_value=bills)
        svc._load_reconciliation_items_for_payments = MagicMock(return_value={})
        svc._load_auto_rates = MagicMock(return_value=auto_rates or {})
        svc._load_custom_rates = MagicMock(return_value={})
        svc._load_active_remakes = MagicMock(return_value={})
        return svc

    def test_gc_only_payment_releases_zero(self):
        """A bill paid entirely via GC in the target month → no release."""
        bills = self._bill_with_payments([{
            'payment_doc_sid': 'PAY1', 'payment_doc_no': 'P-1',
            'payment_date': '2026-04-30', 'amount_applied': 1_000_000,
            'source': 'manual', 'tender_category': 'gift_certificate',
        }])
        svc = self._build_service(bills, [self._item()], auto_rates={
            ('1001', 'DRESS-A'): {
                'revenue_type': 'fashion', 'fp_or_md': 'fp', 'effective_rate': 0.015,
            },
        })
        result = svc.compute_released_for_month(2026, 4)
        assert result == {}

    def test_cash_only_payment_releases_full(self):
        """Sanity: a cash_card payment releases as before."""
        bills = self._bill_with_payments([{
            'payment_doc_sid': 'PAY1', 'payment_doc_no': 'P-1',
            'payment_date': '2026-04-30', 'amount_applied': 1_000_000,
            'source': 'manual', 'tender_category': 'cash_card',
        }])
        svc = self._build_service(bills, [self._item()], auto_rates={
            ('1001', 'DRESS-A'): {
                'revenue_type': 'fashion', 'fp_or_md': 'fp', 'effective_rate': 0.015,
            },
        })
        result = svc.compute_released_for_month(2026, 4)
        # revenue_with_vat 1M / 1.1 × 0.015 × (1M / 1M) = 13,636
        assert result == {'GH001': {'fashion_fp': 13_636}}

    def test_split_payment_releases_only_cash_card_portion(self):
        """Same bill, same month, two chrono entries: 600k cash_card + 400k GC.
        Release math sees only the 600k portion → factor = 0.6, half of full."""
        bills = self._bill_with_payments([
            {'payment_doc_sid': 'PAY1', 'payment_doc_no': 'P-1',
             'payment_date': '2026-04-30', 'amount_applied': 600_000,
             'source': 'manual', 'tender_category': 'cash_card'},
            {'payment_doc_sid': 'PAY1', 'payment_doc_no': 'P-1',
             'payment_date': '2026-04-30', 'amount_applied': 400_000,
             'source': 'manual', 'tender_category': 'gift_certificate'},
        ])
        svc = self._build_service(bills, [self._item()], auto_rates={
            ('1001', 'DRESS-A'): {
                'revenue_type': 'fashion', 'fp_or_md': 'fp', 'effective_rate': 0.015,
            },
        })
        result = svc.compute_released_for_month(2026, 4)
        # factor = 600k / 1M = 0.6 → 1M/1.1 × 0.015 × 0.6 = 8,182
        assert result == {'GH001': {'fashion_fp': 8_182}}


# ----------------------------------------------------------------------
# PendingPayment shape — tender breakdown surfaced
# ----------------------------------------------------------------------

class TestPendingPaymentTenderBreakdownSurface:
    """The queue projection must surface tender_breakdown +
    commission_releasing_amount + gift_certificate_amount on every row."""

    def _payment_meta(self, doc_sid='9001'):
        return {
            'payment_doc_sid': doc_sid, 'payment_doc_no': f'P-{doc_sid}',
            'doc_store_code': 'HBT', 'customer_sid': '1',
            'customer_name': 'A', 'notes_lostdoc': None,
            'ref_sale_sid': None, 'amount': 1_000_000,
            'payment_date': '2026-04-15',
        }

    def test_split_tender_surface(self):
        repo = MagicMock()
        repo.get_charge_payments.return_value = [self._payment_meta()]
        repo.get_tender_breakdowns.return_value = {
            '9001': [
                {'tender_sid': 'T1', 'tender_name': 'MC', 'amount': 600_000},
                {'tender_sid': 'T2', 'tender_name': 'Charge', 'amount': -600_000},
                {'tender_sid': 'T3', 'tender_name': 'Gift Certificate',
                 'amount': 400_000},
                {'tender_sid': 'T4', 'tender_name': 'Charge', 'amount': -400_000},
            ],
        }
        svc = AccountPayableService(repository=repo)
        svc._load_bills = MagicMock(return_value={})
        svc._load_reconciliation_items_for_payments = MagicMock(return_value={})
        svc._load_active_remakes = MagicMock(return_value={})

        result = svc.get_pending_payments()
        assert result['success']
        row = result['data'][0]
        assert row['commission_releasing_amount'] == 600_000
        assert row['gift_certificate_amount'] == 400_000
        assert len(row['tender_breakdown']) == 4
        # Sanity: invariant — cash + gift == payment.amount.
        assert row['commission_releasing_amount'] + row['gift_certificate_amount'] == row['amount']
