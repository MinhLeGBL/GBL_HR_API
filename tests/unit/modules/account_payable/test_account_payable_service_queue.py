"""
Unit tests for AccountPayableService.get_pending_payments (Phase C-5).

Mocks the repository (Oracle reads) and the Postgres helper that loads
reconciliation linkages. Focuses on:
- The 3 queue buckets (unmatched / matched_pending(ref) / matched_pending(manual)).
- "Released" filtering: matched + past month → not in queue.
- `is_overdue` flag for past-month unmatched.
- `suggested_bills` shape: customer's open/partial bills, walk-ins get [].
- `status` query-param filter.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.account_payable.service import AccountPayableService


FROZEN_TODAY = date(2026, 6, 2)   # current month = 2026-06


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def sample_bills():
    """Two open bills for customer 1001, one for 1002. Used as ref-sale-sid
    targets and as suggested_bills sources."""
    return {
        '5001': {
            'doc_no': 'D-5001', 'doc_store_code': 'HBT',
            'customer_sid': '1001', 'customer_name': 'Alice',
            'original_charge': 800_000, 'total_paid': 0,
            'remaining_unpaid': 800_000, 'status': 'open',
            'created_date': '2026-04-10', 'last_payment_date': None,
            'sale_total_amt': 880_000, 'post_month': '2026-04',
            'payments': [],
        },
        '5002': {
            'doc_no': 'D-5002', 'doc_store_code': 'HBT',
            'customer_sid': '1001', 'customer_name': 'Alice',
            'original_charge': 400_000, 'total_paid': 0,
            'remaining_unpaid': 400_000, 'status': 'open',
            'created_date': '2026-05-15', 'last_payment_date': None,
            'sale_total_amt': 440_000, 'post_month': '2026-05',
            'payments': [],
        },
        '5003': {
            'doc_no': 'D-5003', 'doc_store_code': 'RWR',
            'customer_sid': '1002', 'customer_name': 'Bob',
            'original_charge': 200_000, 'total_paid': 200_000,
            'remaining_unpaid': 0, 'status': 'fully_paid',
            'created_date': '2025-12-01', 'last_payment_date': '2026-01-10',
            'sale_total_amt': 220_000, 'post_month': '2025-12',
        },
    }


@pytest.fixture
def sample_payments():
    """5 payment receipts covering every status bucket."""
    return [
        # 1. Current-month unmatched (Alice, no ref, no notes).
        {
            'payment_doc_sid': '9001', 'payment_doc_no': 'P-9001',
            'doc_store_code': 'HBT', 'customer_sid': '1001',
            'customer_name': 'Alice', 'notes_lostdoc': None,
            'ref_sale_sid': None, 'amount': 100_000,
            'payment_date': '2026-06-01',
        },
        # 2. Past-month unmatched → is_overdue=true.
        {
            'payment_doc_sid': '9002', 'payment_doc_no': 'P-9002',
            'doc_store_code': 'HBT', 'customer_sid': '1001',
            'customer_name': 'Alice',
            'notes_lostdoc': 'CLEAR CÔNG NỢ BILL #D-5001',
            'ref_sale_sid': None, 'amount': 200_000,
            'payment_date': '2026-04-15',
        },
        # 3. Current-month REF_SALE_SID → matched_pending(ref_sale_sid).
        {
            'payment_doc_sid': '9003', 'payment_doc_no': 'P-9003',
            'doc_store_code': 'HBT', 'customer_sid': '1001',
            'customer_name': 'Alice', 'notes_lostdoc': None,
            'ref_sale_sid': '5001', 'amount': 300_000,
            'payment_date': '2026-06-02',
        },
        # 4. Past-month REF_SALE_SID → released, NOT in queue.
        {
            'payment_doc_sid': '9004', 'payment_doc_no': 'P-9004',
            'doc_store_code': 'HBT', 'customer_sid': '1001',
            'customer_name': 'Alice', 'notes_lostdoc': None,
            'ref_sale_sid': '5002', 'amount': 50_000,
            'payment_date': '2026-04-20',
        },
        # 5. Walk-in unmatched (customer_sid=None) → suggested_bills=[].
        {
            'payment_doc_sid': '9005', 'payment_doc_no': 'P-9005',
            'doc_store_code': 'RWR', 'customer_sid': None,
            'customer_name': None, 'notes_lostdoc': None,
            'ref_sale_sid': None, 'amount': 75_000,
            'payment_date': '2026-06-01',
        },
    ]


@pytest.fixture
def mock_repo(sample_bills, sample_payments):
    repo = MagicMock()
    repo.get_all_bills.return_value = sample_bills
    repo.get_charge_payments.return_value = sample_payments
    return repo


@pytest.fixture
def service(mock_repo):
    return AccountPayableService(repository=mock_repo)


@pytest.fixture(autouse=True)
def freeze_today():
    with patch('app.modules.account_payable.service._today', return_value=FROZEN_TODAY):
        yield


# ----------------------------------------------------------------------
# get_pending_payments — bucket classification
# ----------------------------------------------------------------------

class TestGetPendingPayments:

    def test_classifies_buckets_correctly(self, service):
        # No manual linkages exist.
        with patch.object(service, '_load_reconciliations', return_value={}):
            result = service.get_pending_payments()
        assert result['success']
        data = result['data']

        by_sid = {p['payment_doc_sid']: p for p in data}

        # 9001 current-month unmatched
        assert by_sid['9001']['status'] == 'unmatched'
        assert by_sid['9001']['match_source'] is None
        assert by_sid['9001']['is_overdue'] is False
        assert by_sid['9001']['allocations'] == []

        # 9002 past-month unmatched → is_overdue
        assert by_sid['9002']['status'] == 'unmatched'
        assert by_sid['9002']['is_overdue'] is True

        # 9003 current-month REF_SALE_SID → matched_pending(ref_sale_sid)
        assert by_sid['9003']['status'] == 'matched_pending'
        assert by_sid['9003']['match_source'] == 'ref_sale_sid'
        assert by_sid['9003']['allocations'][0]['bill_sid'] == '5001'
        assert by_sid['9003']['allocations'][0]['doc_no'] == 'D-5001'
        assert by_sid['9003']['allocations'][0]['amount_applied'] == 300_000
        assert by_sid['9003']['is_overdue'] is False

        # 9004 past-month REF_SALE_SID → released → NOT in queue
        assert '9004' not in by_sid

        # 9005 walk-in unmatched
        assert by_sid['9005']['customer_sid'] is None
        assert by_sid['9005']['suggested_bills'] == []

    def test_manual_linkage_yields_matched_pending(self, service):
        # Inject a manual linkage for 9002 (which becomes past-month → released, filtered out).
        # Use 9001 (current month) instead.
        with patch.object(service, '_load_reconciliations', return_value={
            '9001': [{'bill_sid': '5001', 'amount_applied': 100_000}],
        }):
            result = service.get_pending_payments()
        by_sid = {p['payment_doc_sid']: p for p in result['data']}
        assert by_sid['9001']['status'] == 'matched_pending'
        assert by_sid['9001']['match_source'] == 'manual'
        assert by_sid['9001']['allocations'] == [{
            'bill_sid':       '5001',
            'doc_no':         'D-5001',
            'amount_applied': 100_000,
        }]

    def test_past_month_manual_linkage_is_released_and_filtered(self, service):
        """A manual linkage on a past-month payment → released → not in queue."""
        with patch.object(service, '_load_reconciliations', return_value={
            '9002': [{'bill_sid': '5001', 'amount_applied': 200_000}],
        }):
            result = service.get_pending_payments()
        by_sid = {p['payment_doc_sid']: p for p in result['data']}
        # 9002 was past-month → with manual linkage it's released → not in queue.
        assert '9002' not in by_sid

    def test_suggested_bills_shape(self, service):
        with patch.object(service, '_load_reconciliations', return_value={}):
            result = service.get_pending_payments()
        by_sid = {p['payment_doc_sid']: p for p in result['data']}
        # 9001 belongs to customer 1001 → both 5001 + 5002 are open for them.
        sugg = by_sid['9001']['suggested_bills']
        assert {b['bill_sid'] for b in sugg} == {'5001', '5002'}
        # Sorted oldest-first.
        assert sugg[0]['bill_sid'] == '5001'   # 2026-04-10
        assert sugg[1]['bill_sid'] == '5002'   # 2026-05-15
        # Fully-paid bill 5003 is NOT in the suggestion list.
        assert all(b['bill_sid'] != '5003' for b in sugg)

    def test_status_filter_unmatched(self, service):
        with patch.object(service, '_load_reconciliations', return_value={}):
            result = service.get_pending_payments(status='unmatched')
        sids = {p['payment_doc_sid'] for p in result['data']}
        # 9001 + 9002 + 9005 are unmatched. 9003 is matched. 9004 released.
        assert sids == {'9001', '9002', '9005'}

    def test_status_filter_matched_pending(self, service):
        with patch.object(service, '_load_reconciliations', return_value={}):
            result = service.get_pending_payments(status='matched_pending')
        sids = {p['payment_doc_sid'] for p in result['data']}
        # Only 9003 is matched_pending (ref_sale_sid current month).
        assert sids == {'9003'}

    def test_status_filter_invalid_rejected(self, service):
        result = service.get_pending_payments(status='garbage')
        assert result['success'] is False
        assert 'unmatched' in result['error']

    def test_empty_payments_returns_empty(self, mock_repo):
        mock_repo.get_charge_payments.return_value = []
        with patch.object(AccountPayableService, '_load_reconciliations', return_value={}):
            result = AccountPayableService(repository=mock_repo).get_pending_payments()
        assert result == {'success': True, 'data': []}

    def test_sorted_payment_date_desc(self, service):
        with patch.object(service, '_load_reconciliations', return_value={}):
            result = service.get_pending_payments()
        dates = [p['payment_date'] for p in result['data']]
        assert dates == sorted(dates, reverse=True)
