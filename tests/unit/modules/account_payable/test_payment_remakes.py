"""
Unit tests for CR #70 — remake / corrective payment tag.

Covers:
- `_load_active_remakes` helper (read + group)
- `tag_remake` validation, idempotency, and the side effect of dropping
  existing reconciliations
- `untag_remake` validation + UPDATE-active-row path
- `_build_pending_payment_rows` remake override on the PendingPayment shape
- `compute_released_for_month` defensive filter excluding remake payments
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.account_payable.service import AccountPayableService


FROZEN_TODAY = date(2026, 6, 8)


# ----------------------------------------------------------------------
# Shared fixtures
# ----------------------------------------------------------------------

def _payment(payment_doc_sid, amount=200_000_000, payment_date='2026-04-30'):
    return {
        'payment_doc_sid': payment_doc_sid, 'payment_doc_no': f'P-{payment_doc_sid}',
        'doc_store_code': 'HBT', 'customer_sid': '111',
        'customer_name': 'Alice', 'notes_lostdoc': None,
        'ref_sale_sid': None, 'amount': amount,
        'payment_date': payment_date,
    }


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.get_all_bills.return_value = {}
    repo.get_bill_items.return_value = []
    repo.get_charge_payments.return_value = [_payment('9001')]
    repo.get_payment_meta.return_value = {
        'doc_sid': '9001', 'doc_no': 'P-9001', 'customer_sid': '111',
        'customer_name': 'Alice', 'doc_store_code': 'HBT',
        'amount': 200_000_000, 'payment_date': '2026-04-30',
        'notes_lostdoc': None, 'ref_sale_sid': None,
    }
    return repo


@pytest.fixture
def service(mock_repo):
    s = AccountPayableService(repository=mock_repo)
    s._load_manual_allocations = MagicMock(return_value={})
    s._load_voids_by_bill_sid = MagicMock(return_value={})
    s._load_reconciliation_items_for_payments = MagicMock(return_value={})
    return s


@pytest.fixture(autouse=True)
def freeze_today():
    with patch('app.modules.account_payable.service._today', return_value=FROZEN_TODAY):
        yield


# ----------------------------------------------------------------------
# _load_active_remakes — read + group
# ----------------------------------------------------------------------

class TestLoadActiveRemakes:

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_returns_map_of_active_rows(self, mock_conn):
        cur = MagicMock()
        cur.fetchall.return_value = [
            ('9001', '2026-06-08', 'finance@example.com'),
            ('9002', '2026-06-07', 'manager@example.com'),
        ]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        mock_conn.return_value = conn

        out = AccountPayableService()._load_active_remakes()
        assert out == {
            '9001': {'tagged_at': '2026-06-08', 'tagged_by': 'finance@example.com'},
            '9002': {'tagged_at': '2026-06-07', 'tagged_by': 'manager@example.com'},
        }
        # Spot-check: query filters on `untagged_at IS NULL`.
        assert 'untagged_at IS NULL' in cur.execute.call_args.args[0]

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_connection_failure_returns_empty(self, mock_conn):
        mock_conn.return_value = None
        assert AccountPayableService()._load_active_remakes() == {}


# ----------------------------------------------------------------------
# tag_remake — validation
# ----------------------------------------------------------------------

class TestTagRemakeValidation:

    def test_non_digit_payment_sid_rejected(self, service):
        r = service.tag_remake('abc', 'finance@example.com')
        assert r['success'] is False
        assert 'numeric' in r['error']

    def test_empty_tagged_by_rejected(self, service):
        r = service.tag_remake('9001', '')
        assert r['success'] is False
        assert 'tagged_by' in r['error']

    def test_unknown_payment_returns_not_found(self, mock_repo, service):
        # _pending_payment_for relies on get_charge_payments — empty list = not found.
        mock_repo.get_charge_payments.return_value = []
        r = service.tag_remake('9001', 'finance@example.com')
        assert r['success'] is False
        assert r.get('not_found') is True

    def test_matched_pending_rejected(self, service):
        # Inject a fake _pending_payment_for return so we control the state.
        service._pending_payment_for = MagicMock(return_value={
            'payment_doc_sid': '9001', 'status': 'matched_pending',
            'match_source': 'manual',
        })
        r = service.tag_remake('9001', 'finance@example.com')
        assert r['success'] is False
        assert 'matched_pending' in r['error']

    def test_released_rejected(self, service):
        service._pending_payment_for = MagicMock(return_value={
            'payment_doc_sid': '9001', 'status': 'released',
            'match_source': 'manual',
        })
        r = service.tag_remake('9001', 'finance@example.com')
        assert r['success'] is False
        assert 'released' in r['error']

    def test_ref_sale_sid_match_source_rejected(self, service):
        # Even matched_partially with ref_sale_sid is rejected.
        service._pending_payment_for = MagicMock(return_value={
            'payment_doc_sid': '9001', 'status': 'matched_partially',
            'match_source': 'ref_sale_sid',
        })
        r = service.tag_remake('9001', 'finance@example.com')
        assert r['success'] is False
        assert 'REF_SALE_SID' in r['error']

    def test_already_remake_returns_idempotent_noop(self, service):
        current = {
            'payment_doc_sid': '9001', 'status': 'remake',
            'match_source': None, 'remake_tagged_by': 'finance@example.com',
        }
        service._pending_payment_for = MagicMock(return_value=current)
        r = service.tag_remake('9001', 'someone-else@example.com')
        assert r['success'] is True
        assert r['data'] is current   # exact same object — no write happened


# ----------------------------------------------------------------------
# tag_remake — happy path (write side effect verified)
# ----------------------------------------------------------------------

class TestTagRemakeHappyPath:

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_tag_drops_reconciliations_and_inserts_remake_row(
        self, mock_conn, service,
    ):
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        mock_conn.return_value = conn

        # Stage 1: existing status is matched_partially (has allocations).
        # Stage 2 (post-write): status flips to remake.
        states = iter([
            {'payment_doc_sid': '9001', 'status': 'matched_partially',
             'match_source': 'manual'},
            {'payment_doc_sid': '9001', 'status': 'remake',
             'match_source': None,
             'remake_tagged_at': '2026-06-08',
             'remake_tagged_by': 'finance@example.com'},
        ])
        service._pending_payment_for = MagicMock(side_effect=lambda _: next(states))

        r = service.tag_remake('9001', 'finance@example.com')
        assert r['success'] is True
        assert r['data']['status'] == 'remake'
        assert r['data']['remake_tagged_by'] == 'finance@example.com'

        # Two execute calls: DELETE reconciliations + INSERT remake row.
        execs = [c.args[0] for c in cur.execute.call_args_list if c.args]
        assert any('DELETE FROM payable_reconciliations' in s for s in execs)
        assert any('INSERT INTO payable_payment_remakes' in s for s in execs)
        conn.commit.assert_called_once()

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_tag_on_unmatched_skips_no_reconciliations_to_drop(
        self, mock_conn, service,
    ):
        """Unmatched → tag should still DELETE (no-op if no rows) + INSERT."""
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        mock_conn.return_value = conn

        states = iter([
            {'payment_doc_sid': '9001', 'status': 'unmatched',
             'match_source': None},
            {'payment_doc_sid': '9001', 'status': 'remake',
             'match_source': None,
             'remake_tagged_at': '2026-06-08',
             'remake_tagged_by': 'finance@example.com'},
        ])
        service._pending_payment_for = MagicMock(side_effect=lambda _: next(states))

        r = service.tag_remake('9001', 'finance@example.com')
        assert r['success'] is True
        # INSERT executed regardless.
        assert any('INSERT INTO payable_payment_remakes' in c.args[0]
                   for c in cur.execute.call_args_list if c.args)


# ----------------------------------------------------------------------
# untag_remake — validation + happy path
# ----------------------------------------------------------------------

class TestUntagRemake:

    def test_non_digit_payment_sid_rejected(self, service):
        r = service.untag_remake('abc', 'finance@example.com')
        assert r['success'] is False
        assert 'numeric' in r['error']

    def test_empty_untagged_by_rejected(self, service):
        r = service.untag_remake('9001', '')
        assert r['success'] is False
        assert 'untagged_by' in r['error']

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_not_tagged_returns_400(self, mock_conn, service):
        cur = MagicMock()
        cur.rowcount = 0  # No active row updated → not tagged.
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        mock_conn.return_value = conn
        r = service.untag_remake('9001', 'finance@example.com')
        assert r['success'] is False
        assert 'not currently tagged' in r['error']
        conn.rollback.assert_called_once()

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_untag_updates_active_row(self, mock_conn, service):
        cur = MagicMock()
        cur.rowcount = 1
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        mock_conn.return_value = conn
        service._pending_payment_for = MagicMock(return_value={
            'payment_doc_sid': '9001', 'status': 'unmatched',
            'remake_tagged_at': None, 'remake_tagged_by': None,
        })
        r = service.untag_remake('9001', 'finance@example.com')
        assert r['success'] is True
        # UPDATE includes the WHERE untagged_at IS NULL clause.
        update_sql = cur.execute.call_args.args[0]
        assert 'UPDATE payable_payment_remakes' in update_sql
        assert 'untagged_at IS NULL' in update_sql
        conn.commit.assert_called_once()
        # Returned payment is the post-untag state.
        assert r['data']['status'] == 'unmatched'
        assert r['data']['remake_tagged_at'] is None


# ----------------------------------------------------------------------
# Queue projection — remake override
# ----------------------------------------------------------------------

class TestQueueRemakeOverride:
    """`_build_pending_payment_rows` must override status to 'remake'
    and drop allocations when the payment has an active tag."""

    def test_remake_override_clears_allocations_and_match_source(self, mock_repo):
        # One payment 9001 with an existing manual allocation, but tagged remake.
        svc = AccountPayableService(repository=mock_repo)
        svc._load_manual_allocations = MagicMock(return_value={})
        svc._load_voids_by_bill_sid = MagicMock(return_value={})
        svc._load_reconciliation_items_for_payments = MagicMock(return_value={})
        svc._load_active_remakes = MagicMock(return_value={
            '9001': {'tagged_at': '2026-06-08', 'tagged_by': 'finance@example.com'},
        })

        rows = svc._build_pending_payment_rows(
            payments_meta=[_payment('9001', payment_date='2026-04-30')],
            bills={},
            payment_allocations={
                '9001': [{
                    'bill_sid': '1001', 'doc_no': 'D-1',
                    'amount_applied': 100_000_000,
                    'source': 'manual', 'payment_date': '2026-04-30',
                }],
            },
        )
        assert len(rows) == 1
        row = rows[0]
        assert row['status'] == 'remake'
        assert row['allocations'] == []          # Display drops the stale alloc.
        assert row['match_source'] is None
        assert row['is_overdue'] is False        # Remake is never overdue.
        assert row['remake_tagged_at'] == '2026-06-08'
        assert row['remake_tagged_by'] == 'finance@example.com'
        assert row['suggested_bills'] == []      # No suggestions for remake.

    def test_non_remake_payment_audit_fields_are_null(self, mock_repo):
        svc = AccountPayableService(repository=mock_repo)
        svc._load_manual_allocations = MagicMock(return_value={})
        svc._load_voids_by_bill_sid = MagicMock(return_value={})
        svc._load_reconciliation_items_for_payments = MagicMock(return_value={})
        svc._load_active_remakes = MagicMock(return_value={})

        rows = svc._build_pending_payment_rows(
            payments_meta=[_payment('9002', payment_date='2026-06-01')],
            bills={},
            payment_allocations={},
        )
        row = rows[0]
        assert row['status'] == 'unmatched'
        assert row['remake_tagged_at'] is None
        assert row['remake_tagged_by'] is None

    def test_remake_filter_returns_only_tagged_payments(self, mock_repo):
        # Two payments, only 9001 is tagged.
        svc = AccountPayableService(repository=mock_repo)
        svc._load_manual_allocations = MagicMock(return_value={})
        svc._load_voids_by_bill_sid = MagicMock(return_value={})
        svc._load_reconciliation_items_for_payments = MagicMock(return_value={})
        svc._load_active_remakes = MagicMock(return_value={
            '9001': {'tagged_at': '2026-06-08', 'tagged_by': 'x@y.com'},
        })

        rows = svc._build_pending_payment_rows(
            payments_meta=[
                _payment('9001', payment_date='2026-06-01'),
                _payment('9002', payment_date='2026-06-01'),
            ],
            bills={},
            payment_allocations={},
            status_filter='remake',
        )
        sids = {r['payment_doc_sid'] for r in rows}
        assert sids == {'9001'}


# ----------------------------------------------------------------------
# compute_released_for_month — defensive skip on remake
# ----------------------------------------------------------------------

class TestReleaseSkipsRemake:

    def _setup(self, remake_pay_sids=None):
        repo = MagicMock()
        # One bill, one item, one in-month payment 9001 with a manual reconciliation.
        bills = {
            '1001': {
                'doc_no': 'D-1001', 'doc_store_code': 'HBT',
                'customer_sid': '111', 'customer_name': 'Alice',
                'original_charge': 200_000_000, 'total_paid': 200_000_000,
                'total_voided': 0,
                'remaining_unpaid': 0, 'status': 'fully_paid',
                'created_date': '2026-04-10', 'last_payment_date': '2026-04-30',
                'sale_total_amt': 220_000_000, 'post_month': '2026-04',
                'payments': [{
                    'payment_doc_sid': '9001', 'payment_doc_no': 'P-1',
                    'payment_date': '2026-04-30',
                    'amount_applied': 200_000_000, 'source': 'manual',
                }],
            },
        }
        items = [{
            'bill_sid': '1001', 'sale_id': 'S1', 'upc': 'DRESS-A1',
            'description': 'Dress', 'qty': 1, 'vendor_code': 'AOD',
            'is_jewelry': 0, 'category': 'DRESS', 'department': 'WRTW',
            'discount_rate': 0.0, 'revenue_with_vat': 200_000_000,
            'employee_sid': 'E1', 'employee_code': 'GH001',
            'employee_name': 'A', 'employee_store_code': 'HBT',
        }]
        repo.get_bill_items.return_value = items
        svc = AccountPayableService(repository=repo)
        svc._load_bills = MagicMock(return_value=bills)
        svc._load_reconciliation_items_for_payments = MagicMock(return_value={})
        svc._load_auto_rates = MagicMock(return_value={
            ('1001', 'DRESS-A1'): {
                'revenue_type': 'fashion', 'fp_or_md': 'fp',
                'effective_rate': 0.015,
            },
        })
        svc._load_custom_rates = MagicMock(return_value={})
        svc._load_active_remakes = MagicMock(return_value={
            sid: {'tagged_at': '2026-06-08', 'tagged_by': 'x@y.com'}
            for sid in (remake_pay_sids or [])
        })
        return svc

    def test_release_includes_non_remake_payments(self):
        """Baseline: no remake tag → standard release math runs."""
        svc = self._setup(remake_pay_sids=[])
        result = svc.compute_released_for_month(2026, 4)
        # 200M with-VAT / 1.1 × 0.015 × 1.0 ≈ 2,727,272.
        assert 'GH001' in result
        assert result['GH001'] == {'fashion_fp': 2_727_273}

    def test_release_skips_remake_tagged_payment(self):
        """Defensive: payment 9001 tagged as remake → release math
        skips it even though a stray reconciliation row would imply
        otherwise. No employee receives commission for the bill."""
        svc = self._setup(remake_pay_sids=['9001'])
        result = svc.compute_released_for_month(2026, 4)
        assert result == {}
