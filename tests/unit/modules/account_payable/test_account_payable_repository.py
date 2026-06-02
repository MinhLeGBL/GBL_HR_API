"""
Unit tests for AccountPayableRepository — Phase B (CR #60).

Focuses on `_replay_charge_ledger_with_chronology` since that's the
load-bearing pure-Python logic. The Oracle wrappers (`get_all_bills`,
`get_bill_items`) get one mock-cursor smoke test each — fuller coverage
comes from end-to-end verification against the shared DB later.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.modules.account_payable.repository import AccountPayableRepository


def _evt(
    doc_sid, charge_amount, post_date,
    ref=None, doc_no=None, sale_total=None,
    customer_sid='1001', store='HBT',
):
    """Build one ledger event row in the shape the replay expects."""
    return {
        'doc_sid':         doc_sid,
        'doc_no':          doc_no or f'D-{doc_sid}',
        'doc_store_code':  store,
        'customer_sid':    customer_sid,
        'customer_name':   'Customer A',
        'charge_amount':   charge_amount,
        'sale_total_amt':  sale_total if sale_total is not None else charge_amount,
        'post_date_str':   post_date,
        'post_month':      post_date[:7],
        'ref_sale_sid':    ref,
    }


class TestReplayChronology:
    """Pure-logic tests for `_replay_charge_ledger_with_chronology`."""

    def test_single_open_bill_no_payments(self):
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology([
            _evt('B1', 1_000_000, '2026-04-01'),
        ])
        assert bills['B1']['remaining'] == 1_000_000
        assert bills['B1']['payments_chrono'] == []

    def test_ref_sale_sid_fully_pays_targeted_bill(self):
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology([
            _evt('B1', 1_000_000, '2026-04-01'),
            _evt('PAY1', -1_000_000, '2026-04-10', ref='B1'),
        ])
        assert bills['B1']['remaining'] == 0
        assert len(bills['B1']['payments_chrono']) == 1
        p = bills['B1']['payments_chrono'][0]
        assert p == {
            'payment_doc_sid': 'PAY1',
            'payment_doc_no':  'D-PAY1',
            'payment_date':    '2026-04-10',
            'amount_applied':  1_000_000,
            'source':          'ref_sale_sid',
        }

    def test_partial_ref_payment_leaves_remaining(self):
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology([
            _evt('B1', 1_000_000, '2026-04-01'),
            _evt('PAY1', -400_000, '2026-04-10', ref='B1'),
        ])
        assert bills['B1']['remaining'] == 600_000
        assert bills['B1']['payments_chrono'][0]['amount_applied'] == 400_000
        assert bills['B1']['payments_chrono'][0]['source'] == 'ref_sale_sid'

    def test_fifo_pays_oldest_first(self):
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology([
            _evt('B1', 500_000, '2026-04-01'),
            _evt('B2', 700_000, '2026-04-02'),
            _evt('PAY1', -900_000, '2026-04-10'),   # no ref → FIFO
        ])
        # 900k consumes all of B1 (500k) then 400k of B2 (700k)
        assert bills['B1']['remaining'] == 0
        assert bills['B2']['remaining'] == 300_000
        assert bills['B1']['payments_chrono'][0]['amount_applied'] == 500_000
        assert bills['B1']['payments_chrono'][0]['source'] == 'fifo'
        assert bills['B2']['payments_chrono'][0]['amount_applied'] == 400_000
        assert bills['B2']['payments_chrono'][0]['source'] == 'fifo'

    def test_ref_payment_wins_over_fifo_even_if_later(self):
        """A REF_SALE_SID payment goes to its target even if a later
        FIFO payment could have settled the same bill."""
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology([
            _evt('B1', 500_000, '2026-04-01'),
            _evt('B2', 700_000, '2026-04-02'),
            _evt('PAY1', -300_000, '2026-04-10'),               # FIFO → B1
            _evt('PAY2', -700_000, '2026-04-15', ref='B2'),    # ref → B2
        ])
        # B1 takes the FIFO 300k → 200k remaining
        # B2 takes the ref 700k → fully paid
        assert bills['B1']['remaining'] == 200_000
        assert bills['B2']['remaining'] == 0
        # B1 has the FIFO payment; B2 has the ref payment
        assert bills['B1']['payments_chrono'][0]['source'] == 'fifo'
        assert bills['B2']['payments_chrono'][0]['source'] == 'ref_sale_sid'

    def test_chronology_sorted_by_date_within_bill(self):
        """Pass 1 (ref) then Pass 2 (fifo) can append out of date order;
        the per-bill chronology must come back sorted by payment_date."""
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology([
            _evt('B1', 1_000_000, '2026-04-01'),
            _evt('PAY1', -300_000, '2026-04-15'),              # FIFO → B1 (300k)
            _evt('PAY2', -200_000, '2026-04-10', ref='B1'),    # ref → B1 (200k), earlier date
        ])
        # Pass 1 runs ref first (Apr-10), pass 2 then FIFO (Apr-15).
        # Algorithm appends pass-1 first, but sort restores chronology.
        dates = [p['payment_date'] for p in bills['B1']['payments_chrono']]
        assert dates == sorted(dates)
        assert dates == ['2026-04-10', '2026-04-15']

    def test_orphan_ref_sale_sid_falls_through_to_fifo(self):
        """A payment with a REF_SALE_SID that doesn't match any bill in
        the customer's ledger should fall through to FIFO (commission's
        algorithm — orphan refs are not silently discarded)."""
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology([
            _evt('B1', 500_000, '2026-04-01'),
            _evt('PAY1', -500_000, '2026-04-10', ref='NONEXISTENT'),
        ])
        assert bills['B1']['remaining'] == 0
        assert bills['B1']['payments_chrono'][0]['source'] == 'fifo'

    def test_empty_events_yield_no_bills(self):
        bills, payments = AccountPayableRepository._replay_charge_ledger_with_chronology([])
        assert bills == {}
        assert payments == []


class TestGetBillItemsValidation:
    """`get_bill_items` validates bill_sid inputs before touching Oracle."""

    def test_non_digit_sid_raises(self):
        repo = AccountPayableRepository()
        with pytest.raises(ValueError, match='numeric strings'):
            repo.get_bill_items(['not_a_sid'])

    def test_empty_list_returns_empty_without_querying(self):
        repo = AccountPayableRepository()
        # Should not even open a connection. If it did, the test environment
        # would emit an SSH tunnel error.
        with patch('app.modules.account_payable.repository.get_oracle_connection') as m:
            assert repo.get_bill_items([]) == []
            m.assert_not_called()


class TestGetAllBillsSmoke:
    """One mock-cursor smoke test to confirm get_all_bills wires up the
    replay correctly. End-to-end correctness is verified against the
    shared DB later."""

    @patch('app.modules.account_payable.repository.get_oracle_connection')
    def test_classifies_status_correctly(self, mock_get_conn):
        # Build a fake Oracle response: 3 customers worth of events.
        # - Cust 1001: B1=1M open + B2=500k partial(200k paid) + B3=300k fully paid
        rows = [
            # Customer 1001
            ('1001', 'Customer A', 'B1', 'D-1', 'HBT', 1_000_000,
             None, 1_000_000, None, '2026-04-01', '2026-04'),
            ('1001', 'Customer A', 'B2', 'D-2', 'HBT',   500_000,
             None,   500_000, None, '2026-04-02', '2026-04'),
            ('1001', 'Customer A', 'B3', 'D-3', 'HBT',   300_000,
             None,   300_000, None, '2026-04-03', '2026-04'),
            # 500k payment with ref to B3 (fully pays it)
            ('1001', 'Customer A', 'P1', 'D-P1', 'HBT', -300_000,
             'B3',         0, None, '2026-04-10', '2026-04'),
            # 200k FIFO payment → goes to B1 (oldest open)
            ('1001', 'Customer A', 'P2', 'D-P2', 'HBT', -200_000,
             None,         0, None, '2026-04-15', '2026-04'),
        ]
        columns = [
            'customer_sid', 'customer_name', 'doc_sid', 'doc_no', 'doc_store_code',
            'charge_amount', 'ref_sale_sid', 'sale_total_amt', 'notes_lostdoc',
            'post_date_str', 'post_month',
        ]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows
        mock_cursor.description = [(c.upper(),) for c in columns]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = AccountPayableRepository().get_all_bills()

        assert result['B1']['status'] == 'partial'
        assert result['B1']['remaining_unpaid'] == 800_000      # 1M - 200k FIFO
        assert result['B1']['total_paid'] == 200_000
        assert result['B2']['status'] == 'open'
        assert result['B2']['remaining_unpaid'] == 500_000
        assert result['B3']['status'] == 'fully_paid'
        assert result['B3']['remaining_unpaid'] == 0
        assert result['B3']['total_paid'] == 300_000
        # B3 has one ref-source payment; B1 has one fifo-source payment
        assert result['B3']['payments'][0]['source'] == 'ref_sale_sid'
        assert result['B1']['payments'][0]['source'] == 'fifo'

    @patch('app.modules.account_payable.repository.get_oracle_connection')
    def test_connection_failure_returns_empty(self, mock_get_conn):
        mock_get_conn.return_value = None
        assert AccountPayableRepository().get_all_bills() == {}
