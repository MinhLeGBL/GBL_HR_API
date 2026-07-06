"""
Unit tests for AccountPayableService Phase D (write endpoints).

reconcile / unmatch / set_item_custom_rate. Repository + Postgres are
mocked; tests focus on validation, cumulative-cap math, edit-replace
behavior, and legacy propagation.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.account_payable.service import AccountPayableService


FROZEN_TODAY = date(2026, 6, 2)


# ----------------------------------------------------------------------
# Shared fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def sample_bills():
    """4 bills used by both reconcile and PATCH tests.

    - B1 / B2 are open, both in April 2026 (same post_month) — for
      propagation tests.
    - B3 is open in MAY 2026 — different month, shouldn't propagate.
    - B4 is fully_paid (not eligible for propagation).
    """
    return {
        '1001': {
            'doc_no': 'D-1', 'doc_store_code': 'HBT',
            'customer_sid': '1001', 'customer_name': 'Alice',
            'original_charge': 1_000_000, 'total_paid': 0,
            'remaining_unpaid': 1_000_000, 'status': 'open',
            'created_date': '2026-04-10', 'last_payment_date': None,
            'sale_total_amt': 1_100_000, 'post_month': '2026-04',
            'payments': [],
        },
        '1002': {
            'doc_no': 'D-2', 'doc_store_code': 'HBT',
            'customer_sid': '1001', 'customer_name': 'Alice',
            'original_charge': 500_000, 'total_paid': 0,
            'remaining_unpaid': 500_000, 'status': 'open',
            'created_date': '2026-04-15', 'last_payment_date': None,
            'sale_total_amt': 550_000, 'post_month': '2026-04',
            'payments': [],
        },
        '1003': {
            'doc_no': 'D-3', 'doc_store_code': 'RWR',
            'customer_sid': '1002', 'customer_name': 'Bob',
            'original_charge': 800_000, 'total_paid': 0,
            'remaining_unpaid': 800_000, 'status': 'open',
            'created_date': '2026-05-20', 'last_payment_date': None,
            'sale_total_amt': 880_000, 'post_month': '2026-05',
            'payments': [],
        },
        '1004': {
            'doc_no': 'D-4', 'doc_store_code': 'HBT',
            'customer_sid': '1003', 'customer_name': 'Carol',
            'original_charge': 300_000, 'total_paid': 300_000,
            'remaining_unpaid': 0, 'status': 'fully_paid',
            'created_date': '2026-04-01', 'last_payment_date': '2026-04-20',
            'sale_total_amt': 330_000, 'post_month': '2026-04',
            'payments': [],
        },
    }


@pytest.fixture
def mock_repo(sample_bills):
    repo = MagicMock()
    repo.get_all_bills.return_value = sample_bills
    repo.get_bill_items.return_value = []
    # CR #67: reconcile's _pending_payment_for re-runs get_charge_payments.
    # Default to empty so the embedded payment is None when tests don't set
    # it up explicitly. Tests asserting on the embedded payment override.
    repo.get_charge_payments.return_value = []
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
    """Patch get_postgres_connection in the service module + stub out the
    rate-lookup + manual-allocations + void helpers so the cursor isn't
    asked to multiplex shapes across SQL queries.

    Each test customizes:
      - cur.fetchall.return_value → existing reconciliations [(bill_sid, amount)]
      - cur.fetchone.return_value → cap-baseline SUM tuple
      - cur.rowcount               → DELETE rowcount (for 409 detection)
    """
    with patch('app.modules.account_payable.service.get_postgres_connection') as m:
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = []
        cur.fetchone.return_value = (0,)
        cur.rowcount = 0
        conn.cursor.return_value.__enter__.return_value = cur
        m.return_value = conn
        # Patch the auxiliary loaders so the same cursor isn't asked to
        # multiplex row shapes. `_load_manual_allocations` and CR #68's
        # `_load_voids_by_bill_sid` both run inside `_load_bills`.
        with patch(
            'app.modules.account_payable.service.AccountPayableService._load_auto_rates',
            return_value={},
        ), patch(
            'app.modules.account_payable.service.AccountPayableService._load_custom_rates',
            return_value={},
        ), patch(
            'app.modules.account_payable.service.AccountPayableService._load_manual_allocations',
            return_value={},
        ), patch(
            'app.modules.account_payable.service.AccountPayableService._load_voids_by_bill_sid',
            return_value={},
        ):
            yield m, conn, cur


# ----------------------------------------------------------------------
# reconcile — validation
# ----------------------------------------------------------------------

class TestReconcileValidation:

    def test_bad_payment_doc_sid(self, service):
        r = service.reconcile('not_digits', [{'bill_sid': '1001', 'amount': 100}])
        assert r['success'] is False
        assert 'payment_doc_sid' in r['error']

    def test_non_list_allocations_rejected(self, service):
        """allocations must be a list (CR #67 — empty list now valid,
        but a None/dict/str input should still fail validation)."""
        r = service.reconcile('123', None)  # type: ignore[arg-type]
        assert r['success'] is False
        assert 'list' in r['error']

    def test_allocation_with_zero_amount(self, service):
        r = service.reconcile('123', [{'bill_sid': '1', 'amount': 0}])
        assert r['success'] is False
        assert 'amount' in r['error']

    def test_allocation_with_non_digit_bill_sid(self, service):
        r = service.reconcile('123', [{'bill_sid': 'x', 'amount': 100}])
        assert r['success'] is False


# ----------------------------------------------------------------------
# reconcile — happy paths
# ----------------------------------------------------------------------

class TestReconcileHappyPath:

    def test_first_reconcile_inserts_rows(self, service, mock_repo, mock_pg):
        # Payment exists in Oracle; amount matches our allocation; past month → released.
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        _, _, cur = mock_pg
        # No existing rows for the payment; no rows for the bill either.
        cur.fetchall.return_value = []
        cur.fetchone.return_value = (0,)
        mock_repo.get_bill_items.return_value = []

        r = service.reconcile('9001', [
            {'bill_sid': '1001', 'amount': 400_000},
            {'bill_sid': '1002', 'amount': 200_000},
        ], created_by_user_sid=42)
        assert r['success'] is True
        # Past month → released; current = 2026-06.
        assert r['data']['outcome'] == 'released'
        assert r['data']['affected_months'] == ['2026-04']
        # INSERT was called once via executemany.
        cur.executemany.assert_called_once()
        # No DELETE since no existing rows.
        assert not any(
            'DELETE' in (c.args[0].upper() if c.args else '')
            for c in cur.execute.call_args_list
        )

    def test_current_month_payment_is_matched_pending(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9002', 'doc_no': 'P-2', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-06-01',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        _, _, cur = mock_pg
        cur.fetchall.return_value = []
        cur.fetchone.return_value = (0,)

        r = service.reconcile('9002', [
            {'bill_sid': '1001', 'amount': 600_000},
        ])
        assert r['data']['outcome'] == 'matched_pending'
        assert r['data']['affected_months'] == ['2026-06']
        # CR #67: payment is now always populated (returns None only when
        # the payment isn't in the active Charge window — which we mock
        # via empty get_charge_payments here, so we get None).
        assert r['data']['payment'] is None
        # Current-month outcomes have zero release.
        assert r['data']['total_release_amount'] == 0
        assert r['data']['affected_employee_count'] == 0

    def test_idempotent_same_shape_is_noop(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9003', 'doc_no': 'P-3', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        _, _, cur = mock_pg
        # CR #69: reconcile now does TWO fetchalls — parent rows then
        # per-item rows. Existing rows EXACTLY match the request; no
        # per-item rows on file (proportional historical state).
        cur.fetchall.side_effect = [
            # CR #72: existing parent rows now include tender_category.
            [('1001', 400_000, 'cash_card'), ('1002', 200_000, 'cash_card')],
            [],                                       # existing per-item rows
        ]

        r = service.reconcile('9003', [
            {'bill_sid': '1001', 'amount': 400_000},
            {'bill_sid': '1002', 'amount': 200_000},
        ])
        assert r['success'] is True
        # No DELETE, no INSERT — early return.
        cur.executemany.assert_not_called()


# ----------------------------------------------------------------------
# reconcile — cap + edit + 409
# ----------------------------------------------------------------------

class TestReconcileEdgeCases:

    def test_payment_not_found(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = None
        r = service.reconcile('9999', [{'bill_sid': '1001', 'amount': 100}])
        assert r['success'] is False
        assert r.get('not_found') is True

    def test_allocation_sum_above_amount_rejected(self, service, mock_repo, mock_pg):
        """CR #67: sum > payment.amount is the ONLY sum-based rejection
        (previously: any sum != amount was rejected)."""
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        r = service.reconcile('9001', [{'bill_sid': '1001', 'amount': 700_000}])
        assert r['success'] is False
        assert '600,000' in r['error']
        assert 'must not exceed' in r['error']

    def test_unknown_bill_rejected(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 100, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        r = service.reconcile('9001', [{'bill_sid': '9999', 'amount': 100}])
        assert r['success'] is False
        assert r.get('not_found') is True

    def test_cumulative_cap_overflow(self, service, mock_repo, mock_pg):
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        _, _, cur = mock_pg
        # No existing rows for THIS payment; but B1 already has 800k from other payments.
        # B1.original_charge = 1_000_000. New 600k → 800 + 600 = 1.4M, overflow by 400k.
        cur.fetchall.return_value = []
        cur.fetchone.return_value = (800_000,)

        r = service.reconcile('9001', [{'bill_sid': '1001', 'amount': 600_000}])
        assert r['success'] is False
        assert 'exceed' in r['error']
        assert 'D-1' in r['error']      # bill doc_no in the message
        assert '400,000' in r['error']  # overflow amount

    def test_edit_replace_runs_delete_then_insert(self, service, mock_repo, mock_pg):
        """Existing rows with different shape → DELETE then INSERT in same tx."""
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        _, _, cur = mock_pg
        # Existing: a different allocation shape (one bill, 300k).
        # CR #69: two fetchalls — parents then per-item rows (empty here).
        cur.fetchall.side_effect = [
            [('1001', 300_000, 'cash_card')],   # existing parent rows (CR #72)
            [],                    # existing per-item rows (none — historical proportional)
        ]
        # Other-payment baseline = 0 for all bills.
        cur.fetchone.return_value = (300_000,)   # includes our own row
        cur.rowcount = 1   # DELETE will remove 1 row

        r = service.reconcile('9001', [
            {'bill_sid': '1001', 'amount': 400_000},
            {'bill_sid': '1002', 'amount': 200_000},
        ])
        assert r['success'] is True
        # DELETE happened.
        delete_calls = [
            c for c in cur.execute.call_args_list
            if c.args and 'DELETE' in c.args[0].upper()
        ]
        assert len(delete_calls) == 1
        cur.executemany.assert_called_once()

    def test_concurrent_change_returns_409(self, service, mock_repo, mock_pg):
        """If DELETE rowcount != expected, return 409 (graceful refresh)."""
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        _, _, cur = mock_pg
        # CR #69: two fetchalls — parents then per-item rows (empty here).
        cur.fetchall.side_effect = [
            [('1001', 300_000, 'cash_card'), ('1002', 300_000, 'cash_card')],
            [],
        ]
        cur.fetchone.return_value = (300_000,)
        cur.rowcount = 1   # We expected 2, but only 1 was deleted → conflict.

        r = service.reconcile('9001', [{'bill_sid': '1001', 'amount': 600_000}])
        assert r['success'] is False
        assert r.get('conflict') is True


# ----------------------------------------------------------------------
# reconcile — CR #67 partial / empty / released-edit
# ----------------------------------------------------------------------

class TestReconcileCr67:
    """The new partial-and-empty allocation paths added in CR #67."""

    def test_empty_allocations_succeeds_as_unmatched(
        self, service, mock_repo, mock_pg,
    ):
        """CR #67: allocations=[] is valid — clears all existing linkages
        for the payment. Outcome is `unmatched`."""
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        _, _, cur = mock_pg
        cur.fetchall.return_value = []
        cur.fetchone.return_value = (0,)

        r = service.reconcile('9001', [])

        assert r['success'] is True
        assert r['data']['outcome'] == 'unmatched'
        # No releases (no allocations).
        assert r['data']['total_release_amount'] == 0
        assert r['data']['affected_employee_count'] == 0
        # No INSERT (empty allocations).
        cur.executemany.assert_not_called()

    def test_empty_allocations_with_existing_runs_delete(
        self, service, mock_repo, mock_pg,
    ):
        """CR #67: emptying the allocations on a payment with existing
        rows fires DELETE. Used by frontend's 'clear linkage' button."""
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        _, _, cur = mock_pg
        # CR #69: two fetchalls — parents then per-item rows.
        cur.fetchall.side_effect = [
            # CR #72: existing parent rows include tender_category.
            [('1001', 400_000, 'cash_card'), ('1002', 200_000, 'cash_card')],
            [],
        ]
        cur.fetchone.return_value = (0,)
        cur.rowcount = 2

        r = service.reconcile('9001', [])

        assert r['success'] is True
        assert r['data']['outcome'] == 'unmatched'
        delete_calls = [
            c for c in cur.execute.call_args_list
            if c.args and 'DELETE' in c.args[0].upper()
        ]
        assert len(delete_calls) == 1
        # executemany is invoked with an empty list (no-op at the SQL level);
        # this is fine — what matters is that no rows are inserted.
        executemany_calls = cur.executemany.call_args_list
        assert all(c.args[1] == [] for c in executemany_calls)

    def test_partial_allocation_in_past_month_is_matched_partially_with_release(
        self, service, mock_repo, mock_pg,
    ):
        """CR #67: 0 < sum < amount → matched_partially. For a past-month
        payment, release math still runs on the partial allocation."""
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-04-30',  # past month
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        _, _, cur = mock_pg
        cur.fetchall.return_value = []
        cur.fetchone.return_value = (0,)

        # Bill 1001 has one item; release math at 50% of bill ratio.
        mock_repo.get_bill_items.return_value = [{
            'bill_sid': '1001', 'upc': '12345',
            'revenue_with_vat': 1_000_000, 'employee_code': 'GL005',
        }]
        with patch.object(
            AccountPayableService, '_load_auto_rates',
            return_value={('1001', '12345'): {
                'revenue_type': 'fashion', 'fp_or_md': 'fp',
                'effective_rate': 0.01,
            }},
        ):
            r = service.reconcile('9001', [
                {'bill_sid': '1001', 'amount': 200_000},   # 200k of 600k payment, on a 1M bill
            ])

        assert r['success'] is True
        assert r['data']['outcome'] == 'matched_partially'
        # Release = 1M × 0.01 × (200k / 1M) = 2000.
        assert r['data']['total_release_amount'] == 2_000
        assert r['data']['affected_employee_count'] == 1

    def test_partial_allocation_in_current_month_no_release(
        self, service, mock_repo, mock_pg,
    ):
        """CR #67: current-month + partial → matched_partially, but
        no release computed (release fires at month-close)."""
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9002', 'doc_no': 'P-2', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-06-01',  # current month
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        _, _, cur = mock_pg
        cur.fetchall.return_value = []
        cur.fetchone.return_value = (0,)

        r = service.reconcile('9002', [
            {'bill_sid': '1001', 'amount': 200_000},
        ])
        assert r['data']['outcome'] == 'matched_partially'
        assert r['data']['total_release_amount'] == 0
        assert r['data']['affected_employee_count'] == 0

    def test_embedded_payment_returned_when_in_active_window(
        self, service, mock_repo, mock_pg,
    ):
        """CR #67: when the payment is in the active 24-month Charge
        window, the response embeds the PendingPayment shape (the helper
        re-runs the replay so the embedded row reflects post-write state).

        Asserted as: reconcile DELEGATES to _pending_payment_for and
        embeds whatever it returns. The helper's own status-classification
        is exercised in TestGetPendingPayments.
        """
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        _, _, cur = mock_pg
        cur.fetchall.return_value = []
        cur.fetchone.return_value = (0,)

        # Patch the helper directly — the test focuses on whether reconcile
        # routes its return value into the response, not on the helper's
        # internal re-replay behavior.
        sentinel_payment = {
            'payment_doc_sid': '9001', 'payment_doc_no': 'P-1',
            'status': 'released', 'amount': 600_000,
            'allocations': [{'bill_sid': '1001', 'doc_no': 'D-1',
                             'amount_applied': 600_000}],
            'match_source': 'manual', 'is_overdue': False,
            'suggested_bills': [],
        }
        with patch.object(
            AccountPayableService, '_pending_payment_for',
            return_value=sentinel_payment,
        ):
            r = service.reconcile('9001', [
                {'bill_sid': '1001', 'amount': 600_000},
            ])
        assert r['success'] is True
        assert r['data']['payment'] is sentinel_payment

    def test_embedded_payment_is_none_when_not_in_active_window(
        self, service, mock_repo, mock_pg,
    ):
        """CR #67: if the payment isn't in the active Charge window
        (e.g. older than 24 months), `payment` falls back to None. The
        outcome / release fields are still computed."""
        mock_repo.get_payment_meta.return_value = {
            'doc_sid': '9001', 'doc_no': 'P-1', 'customer_sid': '1001',
            'customer_name': 'Alice', 'doc_store_code': 'HBT',
            'amount': 600_000, 'payment_date': '2026-04-30',
            'notes_lostdoc': None, 'ref_sale_sid': None,
        }
        _, _, cur = mock_pg
        cur.fetchall.return_value = []
        cur.fetchone.return_value = (0,)

        # Default mock_repo.get_charge_payments = [] → helper returns None.
        r = service.reconcile('9001', [
            {'bill_sid': '1001', 'amount': 600_000},
        ])
        assert r['success'] is True
        assert r['data']['payment'] is None
        # Other fields still populated.
        assert r['data']['outcome'] == 'released'
        assert r['data']['affected_months'] == ['2026-04']


# ----------------------------------------------------------------------
# unmatch
# ----------------------------------------------------------------------

class TestUnmatch:

    def test_invalid_payment_sid(self, service):
        r = service.unmatch('not_digits')
        assert r['success'] is False

    def test_deletes_and_returns_count(self, service, mock_pg):
        _, _, cur = mock_pg
        cur.rowcount = 3
        r = service.unmatch('9001')
        assert r['success'] is True
        assert r['data']['deleted_count'] == 3
        assert r['data']['payment_doc_sid'] == '9001'
        delete_calls = [
            c for c in cur.execute.call_args_list
            if c.args and 'DELETE' in c.args[0].upper()
        ]
        assert len(delete_calls) == 1


# ----------------------------------------------------------------------
# set_item_custom_rate — validation + clear path
# ----------------------------------------------------------------------

class TestSetItemCustomRateValidation:

    def test_invalid_bill_sid(self, service):
        r = service.set_item_custom_rate('x', '123', 0.01)
        assert r['success'] is False

    def test_missing_upc(self, service):
        r = service.set_item_custom_rate('1', '', 0.01)
        assert r['success'] is False

    def test_negative_rate_rejected(self, service):
        r = service.set_item_custom_rate('1', 'X', -0.01)
        assert r['success'] is False

    def test_rate_above_max_rejected(self, service):
        r = service.set_item_custom_rate('1', 'X', 11.0)
        assert r['success'] is False


# ----------------------------------------------------------------------
# set_item_custom_rate — clear path
# ----------------------------------------------------------------------

class TestSetItemCustomRateClear:

    def test_clear_deletes_only_targeted_row(self, service, mock_repo, mock_pg):
        # Bill exists in the ledger so the bill-detail lookup succeeds.
        mock_repo.get_bill_items.return_value = []
        _, _, cur = mock_pg
        cur.fetchall.return_value = []
        cur.fetchone.return_value = None    # detail lookup → not found (acceptable)

        r = service.set_item_custom_rate('1001', '123', None)
        # The DELETE ran exactly once.
        delete_calls = [
            c for c in cur.execute.call_args_list
            if c.args and 'DELETE' in c.args[0].upper()
        ]
        assert len(delete_calls) == 1
        # Propagation counts are zero.
        assert r['data']['propagated_to_bill_count'] == 0
        assert r['data']['propagated_item_count'] == 0


# ----------------------------------------------------------------------
# Classifier — unit tests for _classify_item_bucket
# ----------------------------------------------------------------------

class TestClassifierBucket:

    def test_hand_carry(self):
        bucket = AccountPayableService._classify_item_bucket(
            {'vendor_code': 'CGI', 'upc': 'HC1', 'department': 'WRTW',
             'is_jewelry': 0, 'category': 'BAG', 'discount_rate': 0.0},
            hand_carry_upcs={'HC1'}, creation_month='2026-05',
        )
        assert bucket == ('hand_carry', None)

    def test_suitcase_returns_none(self):
        bucket = AccountPayableService._classify_item_bucket(
            {'vendor_code': 'TVL', 'upc': 'X', 'department': 'BAGS',
             'is_jewelry': 0, 'category': 'TRAVEL', 'discount_rate': 0.0},
            hand_carry_upcs=set(), creation_month='2026-05',
        )
        assert bucket is None

    def test_home_decor(self):
        bucket = AccountPayableService._classify_item_bucket(
            {'vendor_code': 'IKE', 'upc': 'X', 'department': 'HOME',
             'is_jewelry': 0, 'category': 'DECOR', 'discount_rate': 0.0},
            hand_carry_upcs=set(), creation_month='2026-05',
        )
        assert bucket == ('home_decor', None)

    def test_hea_pre_cutoff_is_other(self):
        bucket = AccountPayableService._classify_item_bucket(
            {'vendor_code': 'HEA', 'upc': 'X', 'department': 'COSM',
             'is_jewelry': 0, 'category': 'CREAM', 'discount_rate': 0.0},
            hand_carry_upcs=set(), creation_month='2026-03',
        )
        assert bucket == ('other', None)

    def test_hea_post_cutoff_is_fashion(self):
        bucket = AccountPayableService._classify_item_bucket(
            {'vendor_code': 'HEA', 'upc': 'X', 'department': 'COSM',
             'is_jewelry': 0, 'category': 'CREAM', 'discount_rate': 0.0},
            hand_carry_upcs=set(), creation_month='2026-04',
        )
        assert bucket == ('fashion', 'fp')

    def test_vhernier(self):
        bucket = AccountPayableService._classify_item_bucket(
            {'vendor_code': 'VHN', 'upc': 'J1', 'department': 'JWLY',
             'is_jewelry': 1, 'category': 'NECKLACE', 'discount_rate': 0.0},
            hand_carry_upcs=set(), creation_month='2026-05',
        )
        assert bucket == ('vhernier', None)

    def test_fashion_fp_vs_md_by_discount(self):
        fp = AccountPayableService._classify_item_bucket(
            {'vendor_code': 'GUC', 'upc': 'X', 'department': 'WRTW',
             'is_jewelry': 0, 'category': 'SHIRT', 'discount_rate': 0.15},
            hand_carry_upcs=set(), creation_month='2026-05',
        )
        md = AccountPayableService._classify_item_bucket(
            {'vendor_code': 'GUC', 'upc': 'Y', 'department': 'WRTW',
             'is_jewelry': 0, 'category': 'SHIRT', 'discount_rate': 0.45},
            hand_carry_upcs=set(), creation_month='2026-05',
        )
        assert fp == ('fashion', 'fp')
        assert md == ('fashion', 'md')


# ----------------------------------------------------------------------
# Legacy propagation
# ----------------------------------------------------------------------

class TestLegacyPropagation:

    def test_no_propagation_for_non_legacy_target(self, service, mock_repo, mock_pg):
        """When target already has an auto rate, only the targeted row is upserted."""
        targeted_item = {
            'bill_sid': '1001', 'upc': '111', 'vendor_code': 'GUC',
            'department': 'WRTW', 'is_jewelry': 0, 'category': 'SHIRT',
            'discount_rate': 0.0, 'qty': 1, 'revenue_with_vat': 100_000,
        }
        mock_repo.get_bill_items.side_effect = lambda sids: (
            [targeted_item] if sids == ['1001'] else []
        )
        # Stub _wrap_with_bill_detail so we don't need to mock the rate lookups
        # that get_bill_detail would invoke after the write.
        with patch.object(service, '_load_auto_rates', return_value={
            ('1001', '111'): {
                'revenue_type': 'fashion', 'fp_or_md': 'fp', 'effective_rate': 0.0025,
            },
        }):
            with patch.object(service, '_wrap_with_bill_detail',
                              side_effect=lambda b, c1, c2: {
                                  'success': True,
                                  'data': {
                                      'bill': None,
                                      'propagated_to_bill_count': c1,
                                      'propagated_item_count':    c2,
                                  },
                              }):
                r = service.set_item_custom_rate('1001', '111', 0.01)
        assert r['success'] is True
        assert r['data']['propagated_to_bill_count'] == 0
        assert r['data']['propagated_item_count'] == 0

    def test_propagation_when_target_is_legacy(self, service, mock_repo, mock_pg):
        """Legacy target → propagate to other legacy items in same month + category."""
        # Targeted item: legacy fashion FP on B1 (April 2026).
        targeted = {
            'bill_sid': '1001', 'upc': '111', 'vendor_code': 'GUC',
            'department': 'WRTW', 'is_jewelry': 0, 'category': 'SHIRT',
            'discount_rate': 0.0, 'qty': 1, 'revenue_with_vat': 100_000,
        }
        # Candidate items on B1, B2 (Apr) and B3 (May), and B4 (fully_paid).
        candidates = [
            targeted,
            # B1 has another legacy item — should propagate.
            {
                'bill_sid': '1001', 'upc': '222', 'vendor_code': 'GUC',
                'department': 'WRTW', 'is_jewelry': 0, 'category': 'PANTS',
                'discount_rate': 0.1, 'qty': 1, 'revenue_with_vat': 80_000,
            },
            # B2 (Apr) has a legacy fashion FP item — should propagate.
            {
                'bill_sid': '1002', 'upc': '333', 'vendor_code': 'GUC',
                'department': 'WRTW', 'is_jewelry': 0, 'category': 'DRESS',
                'discount_rate': 0.05, 'qty': 1, 'revenue_with_vat': 50_000,
            },
            # B2 (Apr) has a legacy fashion MD item — different fp_or_md, skip.
            {
                'bill_sid': '1002', 'upc': '444', 'vendor_code': 'GUC',
                'department': 'WRTW', 'is_jewelry': 0, 'category': 'JACKET',
                'discount_rate': 0.50, 'qty': 1, 'revenue_with_vat': 50_000,
            },
            # B2 has a NON-legacy fashion FP item (already has auto rate) — skip.
            {
                'bill_sid': '1002', 'upc': '555', 'vendor_code': 'GUC',
                'department': 'WRTW', 'is_jewelry': 0, 'category': 'BAG',
                'discount_rate': 0.0, 'qty': 1, 'revenue_with_vat': 70_000,
            },
        ]
        # get_bill_items called twice: once with ['1001'] for targeted, once
        # with the candidate-bill-sid list for propagation candidates.
        def items_side_effect(sids):
            if sids == ['1001']:
                return [c for c in candidates if c['bill_sid'] == '1001' and c['upc'] == '111']
            return [c for c in candidates if c['bill_sid'] in sids]
        mock_repo.get_bill_items.side_effect = items_side_effect

        # Auto rates: target is legacy (no row), 555 has an auto rate.
        auto_returns = [
            # First call: for the targeted bill_sid='1001' only.
            {},
            # Second call: for the propagation candidate bills.
            {('1002', '555'): {
                'revenue_type': 'fashion', 'fp_or_md': 'fp', 'effective_rate': 0.0025,
            }},
        ]
        with patch.object(service, '_load_auto_rates', side_effect=auto_returns):
            with patch.object(service, '_load_hand_carry_upcs', return_value=set()):
                with patch.object(service, '_wrap_with_bill_detail',
                                  side_effect=lambda b, c1, c2: {
                                      'success': True,
                                      'data': {'bill': None,
                                               'propagated_to_bill_count': c1,
                                               'propagated_item_count': c2},
                                  }):
                    r = service.set_item_custom_rate('1001', '111', 0.015)
        assert r['success'] is True
        # 222 (1001, same category) + 333 (1002, fashion-fp, legacy) = 2 across 2 bills.
        # Targeted item NOT counted (exclude_key); 444 (md) and 555 (non-legacy) excluded.
        assert r['data']['propagated_item_count'] == 2
        assert r['data']['propagated_to_bill_count'] == 2

    def test_propagation_skips_different_month(self, service, mock_repo, mock_pg):
        """B3 (May) shouldn't propagate when target is on B1 (April)."""
        targeted = {
            'bill_sid': '1001', 'upc': '111', 'vendor_code': 'GUC',
            'department': 'WRTW', 'is_jewelry': 0, 'category': 'SHIRT',
            'discount_rate': 0.0, 'qty': 1, 'revenue_with_vat': 100_000,
        }
        # B3 (May) has a legacy fashion FP item — different month, skip.
        candidates = [
            targeted,
            {
                'bill_sid': '1003', 'upc': '999', 'vendor_code': 'GUC',
                'department': 'WRTW', 'is_jewelry': 0, 'category': 'DRESS',
                'discount_rate': 0.0, 'qty': 1, 'revenue_with_vat': 50_000,
            },
        ]
        def items_side_effect(sids):
            if sids == ['1001']:
                return [targeted]
            return [c for c in candidates if c['bill_sid'] in sids]
        mock_repo.get_bill_items.side_effect = items_side_effect

        with patch.object(service, '_load_auto_rates', side_effect=[{}, {}]):
            with patch.object(service, '_load_hand_carry_upcs', return_value=set()):
                with patch.object(service, '_wrap_with_bill_detail',
                                  side_effect=lambda b, c1, c2: {
                                      'success': True,
                                      'data': {'bill': None,
                                               'propagated_to_bill_count': c1,
                                               'propagated_item_count': c2},
                                  }):
                    r = service.set_item_custom_rate('1001', '111', 0.015)
        assert r['data']['propagated_item_count'] == 0
        assert r['data']['propagated_to_bill_count'] == 0
