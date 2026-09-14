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
            # CR #72: REF_SALE_SID auto-matches are always cash_card.
            'tender_category': 'cash_card',
        }

    def test_partial_ref_payment_leaves_remaining(self):
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology([
            _evt('B1', 1_000_000, '2026-04-01'),
            _evt('PAY1', -400_000, '2026-04-10', ref='B1'),
        ])
        assert bills['B1']['remaining'] == 600_000
        assert bills['B1']['payments_chrono'][0]['amount_applied'] == 400_000
        assert bills['B1']['payments_chrono'][0]['source'] == 'ref_sale_sid'

    def test_ref_payment_exceeding_bill_leaves_payment_remainder_unmatched(self):
        """v2.0.0: when a REF_SALE_SID payment's magnitude exceeds the
        referenced bill's remaining balance, the bill is fully cleared
        and the payment's leftover stays UNMATCHED — it does NOT spill
        over to other open bills (that would be FIFO, which we removed).

        Mirrors the real bill 2978 scenario: a "Payment on Account →
        Charge" entry intended to clear bill 2767 (71.2M) and partially
        bill 2797 (the second part was never auto-linked because
        REF_SALE_SID only points at one target).
        """
        bills, payments = AccountPayableRepository._replay_charge_ledger_with_chronology([
            _evt('B1', 300_000, '2026-04-01'),     # small target
            _evt('B2', 500_000, '2026-04-02'),     # another open bill — must NOT auto-fill
            _evt('PAY1', -800_000, '2026-04-10', ref='B1'),  # 500k leftover after B1
        ])
        # B1 fully paid by the REF payment.
        assert bills['B1']['remaining'] == 0
        assert bills['B1']['payments_chrono'][0]['source'] == 'ref_sale_sid'
        assert bills['B1']['payments_chrono'][0]['amount_applied'] == 300_000
        # B2 stays open — leftover does NOT spill via FIFO.
        assert bills['B2']['remaining'] == 500_000
        assert bills['B2']['payments_chrono'] == []
        # Payment side: 500k of the original 800k stays unmatched.
        pay = next(p for p in payments if p['doc_sid'] == 'PAY1')
        assert pay['amount'] == 800_000
        assert pay['remaining'] == 500_000

    def test_payments_list_tracks_remaining_across_mixed_allocations(self):
        """v2.0.0 contract on the returned `payments` list: every payment
        event appears with its `remaining` reflecting the unallocated
        portion, regardless of whether the allocation came from REF,
        manual, or stayed empty.

        Same customer ledger covering three payment fates in one replay.
        """
        events = [
            _evt('B1', 1_000_000, '2026-04-01'),
            _evt('B2',   500_000, '2026-04-02'),
            _evt('B3',   400_000, '2026-04-03'),
            # PAY1 fully consumed by REF on B1.
            _evt('PAY1', -1_000_000, '2026-04-05', ref='B1'),
            # PAY2 fully consumed by manual on B2.
            _evt('PAY2',   -500_000, '2026-04-10'),
            # PAY3 unreferenced and not in manual map → stays fully unmatched.
            _evt('PAY3',   -400_000, '2026-04-12'),
        ]
        manual = {'PAY2': [{'bill_sid': 'B2', 'amount_applied': 500_000}]}
        bills, payments = AccountPayableRepository._replay_charge_ledger_with_chronology(
            events, manual_allocations_by_payment=manual,
        )
        # Bill side
        assert bills['B1']['remaining'] == 0      # paid by PAY1 (ref)
        assert bills['B2']['remaining'] == 0      # paid by PAY2 (manual)
        assert bills['B3']['remaining'] == 400_000  # untouched — no FIFO
        # Payment-side `remaining` for each: 0 / 0 / full magnitude.
        by_doc = {p['doc_sid']: p for p in payments}
        assert by_doc['PAY1']['amount'] == 1_000_000
        assert by_doc['PAY1']['remaining'] == 0
        assert by_doc['PAY2']['amount'] == 500_000
        assert by_doc['PAY2']['remaining'] == 0
        assert by_doc['PAY3']['amount'] == 400_000
        assert by_doc['PAY3']['remaining'] == 400_000
        # And — crucially — every payment event is in the returned list,
        # even the unmatched one (this is the queue endpoint's input).
        assert {p['doc_sid'] for p in payments} == {'PAY1', 'PAY2', 'PAY3'}

    def test_unreferenced_payments_stay_unmatched(self):
        """v2.0.0: payments without REF_SALE_SID or manual allocation
        leave every bill open (no FIFO fallback). Their full magnitude
        stays in the returned `payments` list as `remaining`."""
        bills, payments = AccountPayableRepository._replay_charge_ledger_with_chronology([
            _evt('B1', 500_000, '2026-04-01'),
            _evt('B2', 700_000, '2026-04-02'),
            _evt('PAY1', -900_000, '2026-04-10'),   # no ref, no manual → stays unmatched
        ])
        # Neither bill receives any allocation.
        assert bills['B1']['remaining'] == 500_000
        assert bills['B2']['remaining'] == 700_000
        assert bills['B1']['payments_chrono'] == []
        assert bills['B2']['payments_chrono'] == []
        # The full 900k stays unmatched on the payment side.
        assert any(p['doc_sid'] == 'PAY1' and p['remaining'] == 900_000 for p in payments)

    def test_ref_payment_applies_unreferenced_stays_unmatched(self):
        """v2.0.0: a REF_SALE_SID payment goes to its target; a separate
        unreferenced payment for a different bill is NOT auto-allocated."""
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology([
            _evt('B1', 500_000, '2026-04-01'),
            _evt('B2', 700_000, '2026-04-02'),
            _evt('PAY1', -300_000, '2026-04-10'),               # no ref → stays unmatched
            _evt('PAY2', -700_000, '2026-04-15', ref='B2'),    # ref → B2
        ])
        # B1 unaffected (no FIFO).
        assert bills['B1']['remaining'] == 500_000
        assert bills['B1']['payments_chrono'] == []
        # B2 fully paid via ref.
        assert bills['B2']['remaining'] == 0
        assert bills['B2']['payments_chrono'][0]['source'] == 'ref_sale_sid'

    def test_chronology_sorted_by_date_within_bill(self):
        """Pass 1 (ref) and Pass 2 (manual) can append out of date order;
        the per-bill chronology must come back sorted by payment_date."""
        events = [
            _evt('B1', 1_000_000, '2026-04-01'),
            _evt('PAY1', -300_000, '2026-04-15'),              # manual → B1 (300k), later date
            _evt('PAY2', -200_000, '2026-04-10', ref='B1'),    # ref → B1 (200k), earlier date
        ]
        manual = {'PAY1': [{'bill_sid': 'B1', 'amount_applied': 300_000}]}
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology(
            events, manual_allocations_by_payment=manual,
        )
        # Pass 1 runs ref first (Apr-10), Pass 2 then manual (Apr-15).
        # Algorithm appends pass-1 first, but sort restores chronology.
        dates = [p['payment_date'] for p in bills['B1']['payments_chrono']]
        assert dates == sorted(dates)
        assert dates == ['2026-04-10', '2026-04-15']

    def test_orphan_ref_sale_sid_stays_unmatched(self):
        """v2.0.0: a payment with a REF_SALE_SID pointing at no known bill
        does NOT fall through to FIFO — it stays unmatched until a manual
        reconciliation is added."""
        bills, payments = AccountPayableRepository._replay_charge_ledger_with_chronology([
            _evt('B1', 500_000, '2026-04-01'),
            _evt('PAY1', -500_000, '2026-04-10', ref='NONEXISTENT'),
        ])
        # B1 untouched.
        assert bills['B1']['remaining'] == 500_000
        assert bills['B1']['payments_chrono'] == []
        # Payment full magnitude stays unallocated.
        assert any(p['doc_sid'] == 'PAY1' and p['remaining'] == 500_000 for p in payments)

    def test_empty_events_yield_no_bills(self):
        bills, payments = AccountPayableRepository._replay_charge_ledger_with_chronology([])
        assert bills == {}
        assert payments == []

    # ----------------------- CR #62: Pass 2 (manual) -----------------------

    def test_manual_pass_routes_payment_to_targeted_bill(self):
        """Manual allocation routes the payment to the targeted bill —
        the only auto allocation path besides REF_SALE_SID (v2.0.0)."""
        events = [
            _evt('B1', 500_000, '2026-04-01'),
            _evt('B2', 500_000, '2026-04-02'),
            _evt('PAY1', -500_000, '2026-04-10'),   # no ref, no manual default
        ]
        manual = {'PAY1': [{'bill_sid': 'B2', 'amount_applied': 500_000}]}
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology(
            events, manual_allocations_by_payment=manual,
        )
        # Manual sent it to B2; B1 stays open (no FIFO).
        assert bills['B1']['remaining'] == 500_000
        assert bills['B2']['remaining'] == 0
        assert bills['B2']['payments_chrono'][0]['source'] == 'manual'

    def test_manual_pass_runs_after_ref_sale_sid(self):
        """REF_SALE_SID is tier 1 — manual only sees the leftover of
        the payment magnitude."""
        events = [
            _evt('B1', 300_000, '2026-04-01'),
            _evt('B2', 700_000, '2026-04-02'),
            _evt('PAY1', -500_000, '2026-04-10', ref='B1'),   # ref consumes 300k → 200k left
        ]
        # Manual wants 500k to B2, but only 200k of the payment remains.
        manual = {'PAY1': [{'bill_sid': 'B2', 'amount_applied': 500_000}]}
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology(
            events, manual_allocations_by_payment=manual,
        )
        # B1: fully paid by ref (300k).
        assert bills['B1']['remaining'] == 0
        assert bills['B1']['payments_chrono'][0]['source'] == 'ref_sale_sid'
        # B2: 200k consumed by manual (the leftover); 500k remaining.
        assert bills['B2']['remaining'] == 500_000
        manual_entry = bills['B2']['payments_chrono'][0]
        assert manual_entry['source'] == 'manual'
        assert manual_entry['amount_applied'] == 200_000

    def test_manual_pass_skips_unknown_bill(self):
        """v2.0.0: a manual row referencing a bill from a different
        customer's ledger silently skips here — the row will fire in
        that customer's replay. With no FIFO fallback, the payment stays
        unmatched in this customer's slice."""
        events = [
            _evt('B1', 500_000, '2026-04-01'),
            _evt('PAY1', -500_000, '2026-04-10'),
        ]
        manual = {'PAY1': [{'bill_sid': 'B_OTHER_CUSTOMER', 'amount_applied': 500_000}]}
        bills, payments = AccountPayableRepository._replay_charge_ledger_with_chronology(
            events, manual_allocations_by_payment=manual,
        )
        # B1 stays open — manual's target wasn't in this customer's ledger.
        assert bills['B1']['remaining'] == 500_000
        assert bills['B1']['payments_chrono'] == []
        assert any(p['doc_sid'] == 'PAY1' and p['remaining'] == 500_000 for p in payments)

    def test_manual_allocation_caps_at_bill_remaining(self):
        """Manual amount > bill.remaining only applies the bill's remaining capacity."""
        events = [
            _evt('B1', 200_000, '2026-04-01'),
            _evt('PAY1', -500_000, '2026-04-10'),
        ]
        manual = {'PAY1': [{'bill_sid': 'B1', 'amount_applied': 500_000}]}
        bills, _ = AccountPayableRepository._replay_charge_ledger_with_chronology(
            events, manual_allocations_by_payment=manual,
        )
        # Only 200k applied; payment still has 300k remaining.
        assert bills['B1']['remaining'] == 0
        manual_entry = bills['B1']['payments_chrono'][0]
        assert manual_entry['source'] == 'manual'
        assert manual_entry['amount_applied'] == 200_000


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
        # v2.0.0: only REF_SALE_SID is auto-applied (no FIFO).
        # - Cust 1001: B1=1M open + B2=500k open + B3=300k fully paid via REF
        rows = [
            # Customer 1001
            ('1001', 'Customer A', 'B1', 'D-1', 'HBT', 1_000_000,
             None, 1_000_000, None, '2026-04-01', '2026-04'),
            ('1001', 'Customer A', 'B2', 'D-2', 'HBT',   500_000,
             None,   500_000, None, '2026-04-02', '2026-04'),
            ('1001', 'Customer A', 'B3', 'D-3', 'HBT',   300_000,
             None,   300_000, None, '2026-04-03', '2026-04'),
            # 300k payment with ref to B3 (fully pays it)
            ('1001', 'Customer A', 'P1', 'D-P1', 'HBT', -300_000,
             'B3',         0, None, '2026-04-10', '2026-04'),
            # 200k payment with no ref — stays unmatched (was FIFO pre-v2.0.0)
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

        # B1 stays open — no REF, no manual → no auto allocation.
        assert result['B1']['status'] == 'open'
        assert result['B1']['remaining_unpaid'] == 1_000_000
        assert result['B1']['total_paid'] == 0
        assert result['B1']['payments'] == []
        # B2 stays open.
        assert result['B2']['status'] == 'open'
        assert result['B2']['remaining_unpaid'] == 500_000
        # B3 fully paid via REF.
        assert result['B3']['status'] == 'fully_paid'
        assert result['B3']['remaining_unpaid'] == 0
        assert result['B3']['total_paid'] == 300_000
        assert result['B3']['payments'][0]['source'] == 'ref_sale_sid'

    @patch('app.modules.account_payable.repository.get_oracle_connection')
    def test_connection_failure_returns_empty(self, mock_get_conn):
        mock_get_conn.return_value = None
        assert AccountPayableRepository().get_all_bills() == {}


# ----------------------------------------------------------------------
# CR #71 — split-tender aggregation (regression guard)
# ----------------------------------------------------------------------

class TestSplitTenderAggregation:
    """CR #71: payments with multiple `Charge` tender rows (e.g. split
    MC + Gift Certificate) must collapse into ONE event per doc_sid in
    the ledger query. Without aggregation, the replay treats each row
    as a separate payment event with the same doc_sid, and Pass 2
    (manual) re-applies the operator's reconciliation row once per
    event — silently doubling the allocation against the bill.

    These tests guard against re-introducing the bug by:
      1. Asserting the query string contains `SUM(t.amount)` + `GROUP BY`.
      2. Exercising `get_all_bills` end-to-end with a mocked multi-row
         Oracle response — the input shape the bug produces when the
         aggregation is missing.
    """

    def test_query_aggregates_charge_tender_per_doc(self):
        """The SQL must SUM tender amounts per doc_sid and GROUP BY all
        the doc-level columns. Lightweight static check that catches a
        future refactor stripping the aggregation."""
        from app.modules.account_payable.queries import AccountPayableQueries
        sql = AccountPayableQueries.ORACLE_ALL_CHARGE_LEDGER
        assert 'SUM(t.amount)' in sql
        assert 'GROUP BY' in sql
        # Spot-check that the GROUP BY includes the key doc-level columns.
        assert 'd.sid' in sql
        assert 'd.doc_no' in sql

    @patch('app.modules.account_payable.repository.get_oracle_connection')
    def test_get_all_bills_post_fix_treats_one_payment_per_doc(self, mock_get_conn):
        """With the SQL fix in place, Oracle returns one row per doc_sid
        even for split-tender payments. `get_all_bills` should treat
        payment 1088 (charge_amount = -232.5M, aggregated from -190M +
        -42.5M) as a single payment event — Pass 2's manual allocation
        applies exactly once, leaving 42.5M as `remaining` on the
        payment side and bill 501's remaining_unpaid = 42.5M.

        If a regression breaks the SQL aggregation, the bug would
        re-emerge: TWO events for the same doc_sid → manual row applied
        twice → bill incorrectly fully_paid. This test would NOT catch
        that case directly (the bug shape isn't simulated here), but
        the companion query-string check above does.
        """
        # Bill 501 (232.5M outstanding) + payment 1088 (232.5M, no ref).
        # SUM-aggregated SQL output: one row per doc.
        rows = [
            # Customer 1001 — bill 501 (positive Charge)
            ('1001', 'Le Thi Van', 'B-501', 'D-501', 'RWR', 232_500_000,
             None, 232_500_000, None, '2024-04-30', '2024-04'),
            # Customer 1001 — payment 1088 (aggregated Charge magnitude)
            ('1001', 'Le Thi Van', 'P-1088', 'D-1088', 'RWR', -232_500_000,
             None, 0, None, '2024-10-31', '2024-10'),
        ]
        columns = [
            'customer_sid', 'customer_name', 'doc_sid', 'doc_no', 'doc_store_code',
            'charge_amount', 'ref_sale_sid', 'sale_total_amt', 'notes_lostdoc',
            'post_date_str', 'post_month',
        ]
        cur = MagicMock()
        cur.fetchall.return_value = rows
        cur.description = [(c.upper(),) for c in columns]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        mock_get_conn.return_value = conn

        # Operator's manual reconciliation: 190M of payment 1088 → bill 501.
        manual = {'P-1088': [{'bill_sid': 'B-501', 'amount_applied': 190_000_000}]}
        result = AccountPayableRepository().get_all_bills(
            manual_allocations_by_payment=manual,
        )

        bill = result['B-501']
        # Bill is partially paid: 190M against 232.5M → 42.5M remaining.
        assert bill['total_paid'] == 190_000_000
        assert bill['remaining_unpaid'] == 42_500_000
        assert bill['status'] == 'partial'
        # EXACTLY ONE chrono entry from the manual reconciliation (pre-fix:
        # two entries — 190M + 42.5M).
        chrono = bill['payments']
        assert len(chrono) == 1
        assert chrono[0]['payment_doc_sid'] == 'P-1088'
        assert chrono[0]['amount_applied'] == 190_000_000
        assert chrono[0]['source'] == 'manual'
