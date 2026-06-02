"""
Unit tests for AccountPayableService.get_pending_payments (CR #62).

Post-CR-#62 contract: the queue consumes the SAME replay output the bill
view returns. The test approach mocks `_load_bills` to inject the replay
state directly, then verifies the queue's status classification per the
CR's behaviour matrix.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.account_payable.service import AccountPayableService


FROZEN_TODAY = date(2026, 6, 2)   # current month = 2026-06


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

def _bill(
    bill_sid, doc_no, customer_sid, customer_name,
    original, remaining, status, created_date, post_month,
    payments=(),
):
    """Build one bill record in the get_all_bills shape (replay output)."""
    return {
        'doc_no': doc_no, 'doc_store_code': 'HBT',
        'customer_sid': customer_sid, 'customer_name': customer_name,
        'original_charge': original,
        'total_paid': original - remaining,
        'remaining_unpaid': remaining,
        'status': status,
        'created_date': created_date, 'last_payment_date': None,
        'sale_total_amt': int(original * 1.1),
        'post_month': post_month,
        'payments': list(payments),
    }


def _chrono(payment_doc_sid, amount, source, payment_date,
            payment_doc_no=None):
    return {
        'payment_doc_sid': payment_doc_sid,
        'payment_doc_no':  payment_doc_no or f'P-{payment_doc_sid}',
        'payment_date':    payment_date,
        'amount_applied':  amount,
        'source':          source,
    }


@pytest.fixture
def sample_payments():
    """6 payment receipts covering every bucket of the CR #62 behaviour matrix."""
    return [
        # 1. Current-month unmatched (no replay application).
        {'payment_doc_sid': '9001', 'payment_doc_no': 'P-9001',
         'doc_store_code': 'HBT', 'customer_sid': '1001',
         'customer_name': 'Alice', 'notes_lostdoc': None,
         'ref_sale_sid': None, 'amount': 100_000,
         'payment_date': '2026-06-01'},
        # 2. Past-month unmatched → is_overdue=true.
        {'payment_doc_sid': '9002', 'payment_doc_no': 'P-9002',
         'doc_store_code': 'HBT', 'customer_sid': '1001',
         'customer_name': 'Alice', 'notes_lostdoc': 'no bills available',
         'ref_sale_sid': None, 'amount': 200_000,
         'payment_date': '2026-04-15'},
        # 3. Current-month REF_SALE_SID applied → matched_pending(ref_sale_sid).
        {'payment_doc_sid': '9003', 'payment_doc_no': 'P-9003',
         'doc_store_code': 'HBT', 'customer_sid': '1001',
         'customer_name': 'Alice', 'notes_lostdoc': None,
         'ref_sale_sid': '5001', 'amount': 300_000,
         'payment_date': '2026-06-02'},
        # 4. Current-month FIFO applied → matched_pending(fifo).
        {'payment_doc_sid': '9004', 'payment_doc_no': 'P-9004',
         'doc_store_code': 'HBT', 'customer_sid': '1001',
         'customer_name': 'Alice', 'notes_lostdoc': None,
         'ref_sale_sid': None, 'amount': 100_000,
         'payment_date': '2026-06-01'},
        # 5. Past-month FIFO-applied → released, NOT in queue.
        {'payment_doc_sid': '9005', 'payment_doc_no': 'P-9005',
         'doc_store_code': 'HBT', 'customer_sid': '1001',
         'customer_name': 'Alice', 'notes_lostdoc': None,
         'ref_sale_sid': None, 'amount': 50_000,
         'payment_date': '2026-04-20'},
        # 6. Walk-in unmatched (customer_sid=None) → suggested_bills=[].
        {'payment_doc_sid': '9006', 'payment_doc_no': 'P-9006',
         'doc_store_code': 'RWR', 'customer_sid': None,
         'customer_name': None, 'notes_lostdoc': None,
         'ref_sale_sid': None, 'amount': 75_000,
         'payment_date': '2026-06-01'},
    ]


@pytest.fixture
def sample_bills():
    """4 bills. Embedded chronology mirrors what the replay would emit:
       - 5001 → fully paid by REF_SALE_SID payment 9003 (current month)
       - 5002 → partially paid by FIFO from payment 9004 + fully closed
                via past-month FIFO payment 9005 (this past-month payment
                should drop out of the queue as released)
       - 5004 → open, no payments. Used as suggested_bills fodder.
       - 5003 → unrelated, fully paid long ago.
    """
    return {
        '5001': _bill(
            '5001', 'D-5001', '1001', 'Alice',
            original=300_000, remaining=0, status='fully_paid',
            created_date='2026-05-30', post_month='2026-05',
            payments=[_chrono('9003', 300_000, 'ref_sale_sid', '2026-06-02')],
        ),
        '5002': _bill(
            '5002', 'D-5002', '1001', 'Alice',
            original=150_000, remaining=0, status='fully_paid',
            created_date='2026-04-01', post_month='2026-04',
            payments=[
                _chrono('9004', 100_000, 'fifo', '2026-06-01'),
                _chrono('9005',  50_000, 'fifo', '2026-04-20'),
            ],
        ),
        '5004': _bill(
            '5004', 'D-5004', '1001', 'Alice',
            original=400_000, remaining=400_000, status='open',
            created_date='2026-05-15', post_month='2026-05',
            payments=[],
        ),
        '5003': _bill(
            '5003', 'D-5003', '1002', 'Bob',
            original=200_000, remaining=0, status='fully_paid',
            created_date='2025-12-01', post_month='2025-12',
            payments=[],
        ),
    }


@pytest.fixture
def mock_repo(sample_payments):
    repo = MagicMock()
    repo.get_charge_payments.return_value = sample_payments
    return repo


@pytest.fixture
def service(mock_repo, sample_bills):
    s = AccountPayableService(repository=mock_repo)
    # Inject replay output directly so the queue logic is the only thing under test.
    s._load_bills = MagicMock(return_value=sample_bills)
    return s


@pytest.fixture(autouse=True)
def freeze_today():
    with patch('app.modules.account_payable.service._today', return_value=FROZEN_TODAY):
        yield


# ----------------------------------------------------------------------
# get_pending_payments — CR #62 behaviour matrix
# ----------------------------------------------------------------------

class TestGetPendingPayments:

    def test_full_behaviour_matrix(self, service):
        result = service.get_pending_payments()
        assert result['success']
        by_sid = {p['payment_doc_sid']: p for p in result['data']}

        # 9001: current-month, no replay application → unmatched
        assert by_sid['9001']['status'] == 'unmatched'
        assert by_sid['9001']['match_source'] is None
        assert by_sid['9001']['is_overdue'] is False
        assert by_sid['9001']['allocations'] == []

        # 9002: past-month, no replay application → unmatched + overdue
        assert by_sid['9002']['status'] == 'unmatched'
        assert by_sid['9002']['is_overdue'] is True

        # 9003: current-month, REF_SALE_SID applied to 5001 → matched_pending(ref_sale_sid)
        assert by_sid['9003']['status'] == 'matched_pending'
        assert by_sid['9003']['match_source'] == 'ref_sale_sid'
        assert by_sid['9003']['allocations'] == [{
            'bill_sid': '5001', 'doc_no': 'D-5001', 'amount_applied': 300_000,
        }]
        assert by_sid['9003']['is_overdue'] is False

        # 9004: current-month, FIFO applied → matched_pending(fifo). CR #62 KEY CASE.
        assert by_sid['9004']['status'] == 'matched_pending'
        assert by_sid['9004']['match_source'] == 'fifo'
        assert by_sid['9004']['allocations'] == [{
            'bill_sid': '5002', 'doc_no': 'D-5002', 'amount_applied': 100_000,
        }]
        assert by_sid['9004']['is_overdue'] is False

        # 9005: past-month, FIFO applied → released → NOT in queue
        assert '9005' not in by_sid

        # 9006: walk-in unmatched
        assert by_sid['9006']['customer_sid'] is None
        assert by_sid['9006']['suggested_bills'] == []

    def test_priority_order_when_payment_spans_multiple_sources(
        self, service, sample_bills, sample_payments,
    ):
        """If a payment is applied via BOTH ref_sale_sid and fifo, the
        higher-priority source wins for match_source."""
        # Override 9004's chronology to include both fifo + ref_sale_sid hits.
        sample_bills['5004']['payments'].append(
            _chrono('9004', 50_000, 'ref_sale_sid', '2026-06-01'),
        )
        service._load_bills = MagicMock(return_value=sample_bills)
        result = service.get_pending_payments()
        row = next(p for p in result['data'] if p['payment_doc_sid'] == '9004')
        # ref_sale_sid > fifo in priority order.
        assert row['match_source'] == 'ref_sale_sid'

    def test_partial_application_in_current_month_is_matched_pending(
        self, service, sample_bills,
    ):
        """sum(allocations) < payment.amount + current month → still matched_pending.
        The remainder is implicit deposit-on-account."""
        # 9004's payment is 100_000; we configured a 100_000 FIFO hit. Now
        # override to apply only 60_000 (partial) and verify the queue still
        # treats it as matched_pending.
        sample_bills['5002']['payments'] = [
            _chrono('9004', 60_000, 'fifo', '2026-06-01'),
        ]
        service._load_bills = MagicMock(return_value=sample_bills)
        result = service.get_pending_payments()
        row = next(p for p in result['data'] if p['payment_doc_sid'] == '9004')
        assert row['status'] == 'matched_pending'
        assert row['allocations'][0]['amount_applied'] == 60_000
        # Frontend infers partial-release from sum(allocations) < amount.
        assert row['amount'] == 100_000

    def test_partial_application_in_past_month_still_releases(
        self, service, sample_bills,
    ):
        """Past-month + ANY application → release per CR §"Edge cases #1"."""
        # Override 9005 to be a partial application.
        sample_bills['5002']['payments'] = [
            _chrono('9004', 100_000, 'fifo', '2026-06-01'),
            _chrono('9005',  10_000, 'fifo', '2026-04-20'),   # was 50_000
        ]
        service._load_bills = MagicMock(return_value=sample_bills)
        result = service.get_pending_payments()
        # 9005 still drops from the queue (partial release is fine).
        sids = {p['payment_doc_sid'] for p in result['data']}
        assert '9005' not in sids

    def test_suggested_bills_shape(self, service):
        result = service.get_pending_payments()
        row = next(p for p in result['data'] if p['payment_doc_sid'] == '9001')
        # Alice (1001) has 5004 open (the only open bill in the fixture).
        sids = {b['bill_sid'] for b in row['suggested_bills']}
        assert sids == {'5004'}
        # Sorted oldest-first.
        assert row['suggested_bills'][0]['bill_sid'] == '5004'

    def test_status_filter_unmatched(self, service):
        result = service.get_pending_payments(status='unmatched')
        sids = {p['payment_doc_sid'] for p in result['data']}
        # 9001 + 9002 + 9006 are unmatched. 9003/9004 are matched. 9005 released.
        assert sids == {'9001', '9002', '9006'}

    def test_status_filter_matched_pending(self, service):
        result = service.get_pending_payments(status='matched_pending')
        sids = {p['payment_doc_sid'] for p in result['data']}
        # Both REF_SALE_SID (9003) and FIFO (9004) are matched_pending.
        assert sids == {'9003', '9004'}

    def test_status_filter_invalid_rejected(self, service):
        result = service.get_pending_payments(status='garbage')
        assert result['success'] is False
        assert 'unmatched' in result['error']

    def test_empty_payments_returns_empty(self, mock_repo, sample_bills):
        mock_repo.get_charge_payments.return_value = []
        svc = AccountPayableService(repository=mock_repo)
        svc._load_bills = MagicMock(return_value=sample_bills)
        result = svc.get_pending_payments()
        assert result == {'success': True, 'data': []}

    def test_sorted_payment_date_desc(self, service):
        result = service.get_pending_payments()
        dates = [p['payment_date'] for p in result['data']]
        assert dates == sorted(dates, reverse=True)
