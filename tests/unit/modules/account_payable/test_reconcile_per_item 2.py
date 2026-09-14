"""
Unit tests for CR #69 — per-item allocation (proportional vs priority).

Covers:
- `_assign_per_item` priority-fill algorithm
- `reconcile` validation: items[] type, dup UPCs, UPCs in bill, sum
- `reconcile` happy path: priority writes per-item rows + assignments
- `_build_pending_payment_rows` surfaces `items: string[] | null`
- `_reconcile_response` releases only listed items in priority mode
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.account_payable.service import AccountPayableService


FROZEN_TODAY = date(2026, 6, 4)


# ----------------------------------------------------------------------
# Shared fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def sample_bills():
    """One bill 300M with two items: A=200M, B=100M."""
    return {
        '1001': {
            'doc_no': 'D-1', 'doc_store_code': 'HBT',
            'customer_sid': '111', 'customer_name': 'Alice',
            'original_charge': 300_000_000, 'total_paid': 0,
            'total_voided': 0,
            'remaining_unpaid': 300_000_000, 'status': 'open',
            'created_date': '2026-04-10', 'last_payment_date': None,
            'sale_total_amt': 330_000_000, 'post_month': '2026-04',
            'payments': [],
        },
    }


@pytest.fixture
def bill_items():
    return [
        {'bill_sid': '1001', 'sale_id': 'S1', 'upc': 'DRESS-A1',
         'description': 'Dress', 'qty': 1, 'vendor_code': 'AOD',
         'is_jewelry': 0, 'category': 'DRESS', 'department': 'WRTW',
         'discount_rate': 0.0, 'revenue_with_vat': 200_000_000,
         'employee_sid': 'E1', 'employee_code': 'GH001',
         'employee_name': 'Employee A', 'employee_store_code': 'HBT'},
        {'bill_sid': '1001', 'sale_id': 'S2', 'upc': 'JWLY-B1',
         'description': 'Necklace', 'qty': 1, 'vendor_code': 'VHN',
         'is_jewelry': 1, 'category': 'NECKLACE', 'department': 'JWLY',
         'discount_rate': 0.0, 'revenue_with_vat': 100_000_000,
         'employee_sid': 'E2', 'employee_code': 'GL002',
         'employee_name': 'Employee B', 'employee_store_code': 'HBT'},
    ]


@pytest.fixture
def mock_repo(sample_bills, bill_items):
    repo = MagicMock()
    repo.get_all_bills.return_value = sample_bills
    repo.get_bill_items.return_value = bill_items
    repo.get_charge_payments.return_value = []
    return repo


@pytest.fixture
def service(mock_repo):
    s = AccountPayableService(repository=mock_repo)
    s._load_manual_allocations = MagicMock(return_value={})
    s._load_voids_by_bill_sid = MagicMock(return_value={})
    return s


@pytest.fixture(autouse=True)
def freeze_today():
    with patch(
        'app.modules.account_payable.service._today', return_value=FROZEN_TODAY,
    ):
        yield


@pytest.fixture
def mock_pg():
    """Same shape as the writes-test fixture, plus stubs for the per-item
    helpers introduced in CR #69."""
    with patch('app.modules.account_payable.service.get_postgres_connection') as m:
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.fetchone.return_value = (0,)
        cur.rowcount = 0
        conn.cursor.return_value.__enter__.return_value = cur
        m.return_value = conn
        with patch.object(
            AccountPayableService, '_load_auto_rates', return_value={},
        ), patch.object(
            AccountPayableService, '_load_custom_rates', return_value={},
        ), patch.object(
            AccountPayableService, '_load_manual_allocations', return_value={},
        ), patch.object(
            AccountPayableService, '_load_voids_by_bill_sid', return_value={},
        ), patch.object(
            AccountPayableService, '_load_reconciliation_items_for_payments',
            return_value={},
        ):
            yield m, conn, cur


# ----------------------------------------------------------------------
# _assign_per_item — priority-fill algorithm
# ----------------------------------------------------------------------

class TestAssignPerItem:

    def test_single_item_takes_full_amount(self):
        result = AccountPayableService._assign_per_item(
            items_in_order=['A'],
            item_revenue_by_upc={'A': 200_000_000},
            amount=200_000_000,
        )
        assert result == [('A', 200_000_000)]

    def test_single_item_partial(self):
        result = AccountPayableService._assign_per_item(
            items_in_order=['A'],
            item_revenue_by_upc={'A': 200_000_000},
            amount=80_000_000,
        )
        assert result == [('A', 80_000_000)]

    def test_two_items_first_overflows_into_second(self):
        # Amount 230M, items A=100M B=100M C=50M.
        # → A takes 100M, B takes 100M, C takes 30M, stop (remaining=0).
        result = AccountPayableService._assign_per_item(
            items_in_order=['A', 'B', 'C'],
            item_revenue_by_upc={'A': 100_000_000, 'B': 100_000_000, 'C': 50_000_000},
            amount=230_000_000,
        )
        assert result == [('A', 100_000_000), ('B', 100_000_000), ('C', 30_000_000)]

    def test_amount_exhausted_short_circuits_loop(self):
        # Amount 50M, items A=100M B=200M. Only A is touched (50M); B not in output.
        result = AccountPayableService._assign_per_item(
            items_in_order=['A', 'B'],
            item_revenue_by_upc={'A': 100_000_000, 'B': 200_000_000},
            amount=50_000_000,
        )
        assert result == [('A', 50_000_000)]

    def test_order_matters(self):
        # Same items, different order → different assignments.
        a = AccountPayableService._assign_per_item(
            items_in_order=['A', 'B'],
            item_revenue_by_upc={'A': 100_000_000, 'B': 100_000_000},
            amount=150_000_000,
        )
        b = AccountPayableService._assign_per_item(
            items_in_order=['B', 'A'],
            item_revenue_by_upc={'A': 100_000_000, 'B': 100_000_000},
            amount=150_000_000,
        )
        assert a == [('A', 100_000_000), ('B', 50_000_000)]
        assert b == [('B', 100_000_000), ('A', 50_000_000)]

    def test_overflow_raises_value_error(self):
        # Amount 300M, items total 200M → can't absorb.
        with pytest.raises(ValueError, match='exceeds combined revenue'):
            AccountPayableService._assign_per_item(
                items_in_order=['A', 'B'],
                item_revenue_by_upc={'A': 100_000_000, 'B': 100_000_000},
                amount=300_000_000,
            )


# ----------------------------------------------------------------------
# reconcile — CR #69 validation
# ----------------------------------------------------------------------

class TestReconcileItemsValidation:

    def _payment(self, payment_date='2026-06-01'):
        return {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '111',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 200_000_000, 'payment_date': payment_date,
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }

    def test_non_list_items_rejected(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = self._payment()
        r = service.reconcile('9001', [{
            'bill_sid': '1001', 'amount': 200_000_000,
            'items': 'not-a-list',
        }])
        assert r['success'] is False
        assert 'list' in r['error']

    def test_empty_items_normalized_to_proportional(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = self._payment()
        # Empty list MUST behave as null per CR #69 — accepted, treated as proportional.
        # We expect this to succeed and write a parent row WITHOUT per-item rows.
        _, _, cur = mock_pg
        cur.fetchall.side_effect = [[], []]
        cur.fetchone.return_value = (0,)
        r = service.reconcile('9001', [{
            'bill_sid': '1001', 'amount': 200_000_000,
            'items': [],
        }])
        assert r['success'] is True
        # One executemany call for the parent INSERT; NO call for the items
        # (since empty list → proportional → no per-item rows).
        em_calls = cur.executemany.call_args_list
        assert len(em_calls) == 1
        assert 'INSERT INTO payable_reconciliations' in em_calls[0].args[0]

    def test_dup_upc_in_items_rejected(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = self._payment()
        r = service.reconcile('9001', [{
            'bill_sid': '1001', 'amount': 200_000_000,
            'items': ['DRESS-A1', 'DRESS-A1'],
        }])
        assert r['success'] is False
        assert 'duplicate' in r['error']

    def test_upc_not_on_bill_rejected(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = self._payment()
        r = service.reconcile('9001', [{
            'bill_sid': '1001', 'amount': 200_000_000,
            'items': ['UNKNOWN-UPC'],
        }])
        assert r['success'] is False
        assert 'not part of bill' in r['error']

    def test_amount_exceeds_items_revenue_rejected(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = self._payment()
        # Item DRESS-A1 = 200M. Amount 220M would overflow (no room in just this item).
        # NOTE: payment.amount = 200M, so we can't test overflow > payment.amount here
        # — but we CAN test "amount exceeds selected items' combined revenue":
        # use a 100M jewelry item with a 150M amount.
        r = service.reconcile('9001', [{
            'bill_sid': '1001', 'amount': 150_000_000,
            'items': ['JWLY-B1'],   # only 100M revenue
        }])
        assert r['success'] is False
        assert 'combined revenue' in r['error']
        assert '150,000,000' in r['error']
        assert '100,000,000' in r['error']

    def test_non_string_upc_rejected(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = self._payment()
        r = service.reconcile('9001', [{
            'bill_sid': '1001', 'amount': 200_000_000,
            'items': [123],  # int, not string
        }])
        assert r['success'] is False
        assert 'UPC' in r['error']


# ----------------------------------------------------------------------
# reconcile — CR #69 happy paths
# ----------------------------------------------------------------------

class TestReconcileItemsHappyPath:

    def _payment(self, payment_date='2026-06-01'):
        return {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '111',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 200_000_000, 'payment_date': payment_date,
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }

    def test_priority_writes_per_item_rows(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = self._payment()
        _, _, cur = mock_pg
        # No existing rows; SELECT after INSERT returns the new parent id.
        cur.fetchall.side_effect = [
            [],                    # existing parent rows
            [],                    # existing per-item rows
            # CR #72: post-INSERT SELECT now also reads tender_category.
            [(42, '1001', 'cash_card')],
        ]
        cur.fetchone.return_value = (0,)

        r = service.reconcile('9001', [{
            'bill_sid': '1001', 'amount': 200_000_000,
            'items': ['DRESS-A1'],
        }])
        assert r['success'] is True
        # Two executemany calls: parent INSERT + items INSERT (CR #69).
        em_calls = cur.executemany.call_args_list
        assert len(em_calls) == 2
        # Parent INSERT.
        assert 'INSERT INTO payable_reconciliations' in em_calls[0].args[0]
        # Items INSERT.
        assert 'INSERT INTO payable_reconciliation_items' in em_calls[1].args[0]
        # Item row: (reconciliation_id=42, upc='DRESS-A1', order_index=0, amount_assigned=200M).
        assert em_calls[1].args[1] == [(42, 'DRESS-A1', 0, 200_000_000)]

    def test_proportional_writes_no_per_item_rows(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = self._payment()
        _, _, cur = mock_pg
        cur.fetchall.side_effect = [[], []]
        cur.fetchone.return_value = (0,)
        r = service.reconcile('9001', [{
            'bill_sid': '1001', 'amount': 200_000_000,
            'items': None,
        }])
        assert r['success'] is True
        # Only the parent INSERT — no items INSERT.
        em_calls = cur.executemany.call_args_list
        assert len(em_calls) == 1
        assert 'INSERT INTO payable_reconciliations' in em_calls[0].args[0]

    def test_priority_release_math_in_response(self, service, mock_repo, mock_pg):
        """Past-month + priority on one item → release math uses that item's
        assignment ratio, not the bill's paid ratio."""
        mock_repo.get_payment_meta.return_value = self._payment(
            payment_date='2026-04-30',  # past month
        )
        _, _, cur = mock_pg
        cur.fetchall.side_effect = [
            [],
            [],
            # CR #72: post-INSERT SELECT now includes tender_category.
            [(42, '1001', 'cash_card')],
        ]
        cur.fetchone.return_value = (0,)

        # Set an auto-rate row for DRESS-A1: fashion_fp at 1.5%.
        with patch.object(
            AccountPayableService, '_load_auto_rates',
            return_value={('1001', 'DRESS-A1'): {
                'revenue_type': 'fashion', 'fp_or_md': 'fp',
                'effective_rate': 0.015,
            }},
        ):
            r = service.reconcile('9001', [{
                'bill_sid': '1001', 'amount': 200_000_000,
                'items': ['DRESS-A1'],
            }])

        assert r['success'] is True
        # Release math for DRESS-A1 in priority mode:
        # revenue_with_vat = 200M → revenue_basis = 200M/1.1 = 181,818,181.82
        # share_ratio = amount_assigned / item.revenue_with_vat = 200M / 200M = 1.0
        # released = revenue_with_vat × rate × share_ratio = 200M × 0.015 × 1.0 = 3,000,000
        # (Existing math is rev_with_vat × effective × share_ratio. VAT divisor
        # is NOT applied in _reconcile_response — only in compute_released_for_month.)
        assert r['data']['total_release_amount'] == 3_000_000
        assert r['data']['affected_employee_count'] == 1   # only DRESS-A1's employee


# ----------------------------------------------------------------------
# CR #69 — gaps flagged in PR #34 review: round-trip + release + mixed
# ----------------------------------------------------------------------

class TestLoadReconciliationItemsForPayments:
    """Verify the SQL helper's projection — the JOIN's ORDER BY must
    preserve order_index so the queue's items[] field round-trips
    correctly. PR #34 review flagged this as a coverage gap."""

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_groups_rows_by_payment_bill_pair_in_order(self, mock_get_conn):
        # Mock the Postgres JOIN returning rows for two (pay, bill) pairs,
        # each with multiple items in increasing order_index.
        # CR #72: returned rows now include r.tender_category as the 3rd col.
        rows = [
            # ('PAY1', '1001', 'cash_card'): 3 items in priority order
            ('PAY1', '1001', 'cash_card', 'UPC-A', 0, 100_000),
            ('PAY1', '1001', 'cash_card', 'UPC-B', 1, 80_000),
            ('PAY1', '1001', 'cash_card', 'UPC-C', 2, 20_000),
            # ('PAY1', '1002', 'cash_card'): single item
            ('PAY1', '1002', 'cash_card', 'UPC-D', 0, 50_000),
            # ('PAY2', '1003', 'cash_card'): two items
            ('PAY2', '1003', 'cash_card', 'UPC-E', 0, 75_000),
            ('PAY2', '1003', 'cash_card', 'UPC-F', 1, 25_000),
        ]
        cur = MagicMock()
        cur.fetchall.return_value = rows
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        mock_get_conn.return_value = conn

        out = AccountPayableService()._load_reconciliation_items_for_payments(
            ['PAY1', 'PAY2']
        )

        # Three distinct (payment, bill, tender_category) keys.
        assert set(out.keys()) == {
            ('PAY1', '1001', 'cash_card'),
            ('PAY1', '1002', 'cash_card'),
            ('PAY2', '1003', 'cash_card'),
        }

        # ('PAY1', '1001', 'cash_card') items preserve order_index 0 → 2.
        first = out[('PAY1', '1001', 'cash_card')]
        assert [r['upc'] for r in first] == ['UPC-A', 'UPC-B', 'UPC-C']
        assert [r['order_index'] for r in first] == [0, 1, 2]
        assert [r['amount_assigned'] for r in first] == [100_000, 80_000, 20_000]

        # Single-item case.
        assert out[('PAY1', '1002', 'cash_card')] == [
            {'upc': 'UPC-D', 'order_index': 0, 'amount_assigned': 50_000},
        ]

        # Cross-payment grouping works.
        assert [r['upc'] for r in out[('PAY2', '1003', 'cash_card')]] == ['UPC-E', 'UPC-F']

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_empty_input_returns_empty_dict_no_query(self, mock_get_conn):
        out = AccountPayableService()._load_reconciliation_items_for_payments([])
        assert out == {}
        mock_get_conn.assert_not_called()

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_connection_failure_returns_empty(self, mock_get_conn):
        mock_get_conn.return_value = None
        out = AccountPayableService()._load_reconciliation_items_for_payments(['PAY1'])
        assert out == {}


class TestComputeReleasedForMonthPriority:
    """Verify the per-item factor in compute_released_for_month —
    priority mode releases only listed items. PR #34 review flagged
    that test_released_accumulator covers proportional only."""

    def _setup_bill_with_2_items(self):
        """Bill 300M with two items A (200M, GH001) and B (100M, GL002)
        and one in-month payment of 200M."""
        bill_sid = '1001'
        bills = {
            bill_sid: {
                'doc_no': 'D-1001', 'doc_store_code': 'HBT',
                'customer_sid': '111', 'customer_name': 'Alice',
                'original_charge': 300_000_000, 'total_paid': 200_000_000,
                'total_voided': 0,
                'remaining_unpaid': 100_000_000, 'status': 'partial',
                'created_date': '2026-04-10', 'last_payment_date': '2026-04-30',
                'sale_total_amt': 330_000_000, 'post_month': '2026-04',
                'payments': [{
                    'payment_doc_sid': 'PAY1', 'payment_doc_no': 'P-1',
                    'payment_date': '2026-04-30',
                    'amount_applied': 200_000_000, 'source': 'manual',
                }],
            },
        }
        items = [
            {'bill_sid': bill_sid, 'sale_id': 'S1', 'upc': 'DRESS-A1',
             'description': 'Dress', 'qty': 1, 'vendor_code': 'AOD',
             'is_jewelry': 0, 'category': 'DRESS', 'department': 'WRTW',
             'discount_rate': 0.0, 'revenue_with_vat': 200_000_000,
             'employee_sid': 'E1', 'employee_code': 'GH001',
             'employee_name': 'Employee A', 'employee_store_code': 'HBT'},
            {'bill_sid': bill_sid, 'sale_id': 'S2', 'upc': 'JWLY-B1',
             'description': 'Necklace', 'qty': 1, 'vendor_code': 'AOD',  # non-VHN so it stays as fashion
             'is_jewelry': 0, 'category': 'OTHER', 'department': 'WRTW',
             'discount_rate': 0.0, 'revenue_with_vat': 100_000_000,
             'employee_sid': 'E2', 'employee_code': 'GL002',
             'employee_name': 'Employee B', 'employee_store_code': 'HBT'},
        ]
        return bill_sid, bills, items

    def _build_service(self, bills, items, per_item_rows=None, auto_rates=None):
        repo = MagicMock()
        repo.get_bill_items.return_value = items
        svc = AccountPayableService(repository=repo)
        svc._load_bills = MagicMock(return_value=bills)
        svc._load_manual_allocations = MagicMock(return_value={})
        svc._load_voids_by_bill_sid = MagicMock(return_value={})
        svc._load_reconciliation_items_for_payments = MagicMock(
            return_value=per_item_rows or {}
        )
        svc._load_auto_rates = MagicMock(return_value=auto_rates or {})
        svc._load_custom_rates = MagicMock(return_value={})
        return svc

    def test_proportional_releases_both_items(self):
        """No priority rows → proportional → both items share allocation_factor =
        200M / 300M = 0.6667."""
        bill_sid, bills, items = self._setup_bill_with_2_items()
        svc = self._build_service(
            bills, items,
            auto_rates={
                (bill_sid, 'DRESS-A1'): {
                    'revenue_type': 'fashion', 'fp_or_md': 'fp',
                    'effective_rate': 0.015,
                },
                (bill_sid, 'JWLY-B1'): {
                    'revenue_type': 'fashion', 'fp_or_md': 'fp',
                    'effective_rate': 0.01,
                },
            },
        )
        result = svc.compute_released_for_month(2026, 4)
        # Item A: 200M / 1.1 × 0.015 × (200M / 300M) ≈ 1,818,181
        # Item B: 100M / 1.1 × 0.01 × (200M / 300M)  ≈ 606,060
        assert 'GH001' in result and 'GL002' in result
        assert result['GH001'] == {'fashion_fp': 1_818_182}    # ~1.82M
        assert result['GL002'] == {'fashion_fp': 606_061}      # ~0.61M

    def test_priority_releases_only_listed_item(self):
        """Priority `["DRESS-A1"]` → only employee A releases; employee
        B gets 0 (no row in result dict)."""
        bill_sid, bills, items = self._setup_bill_with_2_items()
        svc = self._build_service(
            bills, items,
            per_item_rows={
                ('PAY1', bill_sid, 'cash_card'): [{
                    'upc': 'DRESS-A1', 'order_index': 0,
                    'amount_assigned': 200_000_000,
                }],
            },
            auto_rates={
                (bill_sid, 'DRESS-A1'): {
                    'revenue_type': 'fashion', 'fp_or_md': 'fp',
                    'effective_rate': 0.015,
                },
                (bill_sid, 'JWLY-B1'): {
                    'revenue_type': 'fashion', 'fp_or_md': 'fp',
                    'effective_rate': 0.01,
                },
            },
        )
        result = svc.compute_released_for_month(2026, 4)
        # Item A: 200M / 1.1 × 0.015 × (200M / 200M = 1.0) ≈ 2,727,272 (full release)
        # Item B: factor = 0 → no contribution → no entry
        assert 'GH001' in result
        assert result['GH001'] == {'fashion_fp': 2_727_273}
        # Employee B is excluded entirely (no key in result).
        assert 'GL002' not in result

    def test_priority_partial_amount_releases_proportionally(self):
        """Priority `["DRESS-A1"]` with amount 100M (half the item's
        200M revenue) → factor = 0.5 → half release on A only."""
        bill_sid, bills, items = self._setup_bill_with_2_items()
        # Override the bill's in-month payment to 100M.
        bills[bill_sid]['payments'] = [{
            'payment_doc_sid': 'PAY1', 'payment_doc_no': 'P-1',
            'payment_date': '2026-04-30',
            'amount_applied': 100_000_000, 'source': 'manual',
        }]
        svc = self._build_service(
            bills, items,
            per_item_rows={
                ('PAY1', bill_sid, 'cash_card'): [{
                    'upc': 'DRESS-A1', 'order_index': 0,
                    'amount_assigned': 100_000_000,
                }],
            },
            auto_rates={
                (bill_sid, 'DRESS-A1'): {
                    'revenue_type': 'fashion', 'fp_or_md': 'fp',
                    'effective_rate': 0.015,
                },
            },
        )
        result = svc.compute_released_for_month(2026, 4)
        # 200M / 1.1 × 0.015 × (100M / 200M = 0.5) ≈ 1,363,636
        assert result == {'GH001': {'fashion_fp': 1_363_636}}


class TestReconcileMixedMode:
    """Per-allocation independence: one priority + one proportional in
    the SAME reconcile request. PR #34 review flagged that this combo
    isn't exercised end-to-end (only validation accepts it)."""

    @pytest.fixture
    def mixed_bills(self):
        return {
            '1001': {
                'doc_no': 'D-1001', 'doc_store_code': 'HBT',
                'customer_sid': '111', 'customer_name': 'Alice',
                'original_charge': 200_000_000, 'total_paid': 0,
                'total_voided': 0,
                'remaining_unpaid': 200_000_000, 'status': 'open',
                'created_date': '2026-04-10', 'last_payment_date': None,
                'sale_total_amt': 220_000_000, 'post_month': '2026-04',
                'payments': [],
            },
            '1002': {
                'doc_no': 'D-1002', 'doc_store_code': 'HBT',
                'customer_sid': '111', 'customer_name': 'Alice',
                'original_charge': 100_000_000, 'total_paid': 0,
                'total_voided': 0,
                'remaining_unpaid': 100_000_000, 'status': 'open',
                'created_date': '2026-04-15', 'last_payment_date': None,
                'sale_total_amt': 110_000_000, 'post_month': '2026-04',
                'payments': [],
            },
        }

    @pytest.fixture
    def mixed_bill_items(self):
        # Bill 1001: single 200M item (priority will target it).
        # Bill 1002: 100M item (proportional, no items field).
        return [
            {'bill_sid': '1001', 'sale_id': 'S1', 'upc': 'DRESS-A1',
             'description': 'Dress', 'qty': 1, 'vendor_code': 'AOD',
             'is_jewelry': 0, 'category': 'DRESS', 'department': 'WRTW',
             'discount_rate': 0.0, 'revenue_with_vat': 200_000_000,
             'employee_sid': 'E1', 'employee_code': 'GH001',
             'employee_name': 'A', 'employee_store_code': 'HBT'},
            {'bill_sid': '1002', 'sale_id': 'S2', 'upc': 'BAG-B1',
             'description': 'Bag', 'qty': 1, 'vendor_code': 'AOD',
             'is_jewelry': 0, 'category': 'BAG', 'department': 'WRTW',
             'discount_rate': 0.0, 'revenue_with_vat': 100_000_000,
             'employee_sid': 'E2', 'employee_code': 'GL002',
             'employee_name': 'B', 'employee_store_code': 'HBT'},
        ]

    def test_mixed_priority_and_proportional_in_single_request(
        self, mixed_bills, mixed_bill_items,
    ):
        """Reconcile with one priority allocation and one proportional
        allocation in the same payment. Backend writes priority rows
        only for the priority allocation."""
        repo = MagicMock()
        repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '111',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 250_000_000, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        repo.get_all_bills.return_value = mixed_bills
        repo.get_bill_items.return_value = mixed_bill_items
        repo.get_charge_payments.return_value = []

        svc = AccountPayableService(repository=repo)
        svc._load_manual_allocations = MagicMock(return_value={})
        svc._load_voids_by_bill_sid = MagicMock(return_value={})

        with patch(
            'app.modules.account_payable.service.get_postgres_connection',
        ) as mock_conn, patch.object(
            AccountPayableService, '_load_auto_rates', return_value={},
        ), patch.object(
            AccountPayableService, '_load_custom_rates', return_value={},
        ), patch.object(
            AccountPayableService, '_load_reconciliation_items_for_payments',
            return_value={},
        ):
            cur = MagicMock()
            cur.fetchall.side_effect = [
                [],                       # existing parent rows
                [],                       # existing per-item rows
                # CR #72: post-INSERT SELECT now also reads tender_category.
            [(42, '1001', 'cash_card'), (43, '1002', 'cash_card')],
            ]
            cur.fetchone.return_value = (0,)
            cur.rowcount = 0
            conn = MagicMock()
            conn.cursor.return_value.__enter__.return_value = cur
            mock_conn.return_value = conn

            r = svc.reconcile('9001', [
                # Priority on bill 1001 → writes per-item row for DRESS-A1
                {'bill_sid': '1001', 'amount': 150_000_000,
                 'items': ['DRESS-A1']},
                # Proportional on bill 1002 → no per-item rows
                {'bill_sid': '1002', 'amount': 100_000_000, 'items': None},
            ])

        assert r['success'] is True
        # Two executemany calls: parent INSERT + items INSERT.
        em_calls = cur.executemany.call_args_list
        assert len(em_calls) == 2
        # Parent INSERT for both bills.
        parent_rows = em_calls[0].args[1]
        assert len(parent_rows) == 2
        # Items INSERT: only ONE row (priority allocation only).
        items_rows = em_calls[1].args[1]
        assert len(items_rows) == 1
        # Row is (reconciliation_id=42, upc='DRESS-A1', order_index=0, amount_assigned=150M).
        assert items_rows[0] == (42, 'DRESS-A1', 0, 150_000_000)
