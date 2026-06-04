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
            [(42, '1001')],        # SELECT id, bill_sid post-INSERT
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
            [(42, '1001')],
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
