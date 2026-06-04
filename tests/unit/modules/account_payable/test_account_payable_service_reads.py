"""
Unit tests for AccountPayableService Phase C (read endpoints).

Mocks the repository + Postgres rate lookups so the tests stay pure
business-logic. Live verification against the shared DB runs separately.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.account_payable.service import (
    AccountPayableService,
    _age_days,
    _release_status_for_payment,
)


FROZEN_TODAY = date(2026, 6, 2)


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------

class TestHelpers:

    def test_age_days_positive(self):
        assert _age_days('2026-05-01', today=FROZEN_TODAY) == 32

    def test_age_days_today(self):
        assert _age_days('2026-06-02', today=FROZEN_TODAY) == 0

    def test_age_days_future_clamped_to_zero(self):
        assert _age_days('2026-12-01', today=FROZEN_TODAY) == 0

    def test_age_days_invalid_date(self):
        assert _age_days('not-a-date', today=FROZEN_TODAY) == 0

    def test_release_status_past_month_released(self):
        status, month = _release_status_for_payment('2026-05-15', today=FROZEN_TODAY)
        assert status == 'released'
        assert month == '2026-05'

    def test_release_status_current_month_pending(self):
        status, month = _release_status_for_payment('2026-06-02', today=FROZEN_TODAY)
        assert status == 'pending'
        assert month == '2026-06'


# ----------------------------------------------------------------------
# Fixtures: a 3-bill, 1-customer ledger with mixed status
# ----------------------------------------------------------------------

@pytest.fixture
def sample_bills():
    """3 bills: B1 open, B2 partial, B3 fully_paid."""
    return {
        'B1': {
            'doc_no': 'D-1', 'doc_store_code': 'HBT',
            'customer_sid': '1001', 'customer_name': 'Alice',
            'original_charge': 1_000_000,
            'total_paid': 0,
            'remaining_unpaid': 1_000_000,
            'status': 'open',
            'created_date': '2026-05-15',
            'last_payment_date': None,
            'sale_total_amt': 1_100_000,
            'post_month': '2026-05',
            'payments': [],
        },
        'B2': {
            'doc_no': 'D-2', 'doc_store_code': 'HBT',
            'customer_sid': '1001', 'customer_name': 'Alice',
            'original_charge': 500_000,
            'total_paid': 200_000,
            'remaining_unpaid': 300_000,
            'status': 'partial',
            'created_date': '2026-04-20',
            'last_payment_date': '2026-05-25',
            'sale_total_amt': 550_000,
            'post_month': '2026-04',
            'payments': [{
                'payment_doc_sid': 'P1', 'payment_doc_no': 'D-P1',
                'payment_date': '2026-05-25',
                'amount_applied': 200_000, 'source': 'fifo',
            }],
        },
        'B3': {
            'doc_no': 'D-3', 'doc_store_code': 'RWR',
            'customer_sid': '1002', 'customer_name': 'Bob',
            'original_charge': 300_000,
            'total_paid': 300_000,
            'remaining_unpaid': 0,
            'status': 'fully_paid',
            'created_date': '2024-03-01',
            'last_payment_date': '2024-04-10',
            'sale_total_amt': 330_000,
            'post_month': '2024-03',
            'payments': [{
                'payment_doc_sid': 'P2', 'payment_doc_no': 'D-P2',
                'payment_date': '2024-04-10',
                'amount_applied': 300_000, 'source': 'ref_sale_sid',
            }],
        },
    }


@pytest.fixture
def sample_items():
    """Line items for the 3 bills above. Mix of employees + categories."""
    return [
        # B1 — 2 items, both GH083
        {
            'bill_sid': 'B1', 'sale_id': 'S1', 'upc': '216335',
            'description': 'Dress A', 'qty': 1,
            'vendor_code': 'AOD', 'is_jewelry': 0, 'category': 'DRESS',
            'department': 'WRTW', 'discount_rate': 0.0,
            'revenue_with_vat': 600_000,
            'employee_sid': 'E1', 'employee_code': 'GH083',
            'employee_name': 'Le Bao Ngoc', 'employee_store_code': 'HBT',
        },
        {
            'bill_sid': 'B1', 'sale_id': 'S2', 'upc': '216336',
            'description': 'Bag B', 'qty': 1,
            'vendor_code': 'AOD', 'is_jewelry': 0, 'category': 'BAG',
            'department': 'WRTW', 'discount_rate': 0.0,
            'revenue_with_vat': 400_000,
            'employee_sid': 'E1', 'employee_code': 'GH083',
            'employee_name': 'Le Bao Ngoc', 'employee_store_code': 'HBT',
        },
        # B2 — 1 item, GL005
        {
            'bill_sid': 'B2', 'sale_id': 'S3', 'upc': '999111',
            'description': 'Necklace', 'qty': 1,
            'vendor_code': 'VHN', 'is_jewelry': 1, 'category': 'NECKLACE',
            'department': 'JWLY', 'discount_rate': 0.0,
            'revenue_with_vat': 500_000,
            'employee_sid': 'E2', 'employee_code': 'GL005',
            'employee_name': 'Pham Van Anh', 'employee_store_code': 'RWR',
        },
        # B3 — 1 item, GL005 (fully paid bill — shouldn't appear in get_employees)
        {
            'bill_sid': 'B3', 'sale_id': 'S4', 'upc': '777222',
            'description': 'Ring', 'qty': 1,
            'vendor_code': 'ROM', 'is_jewelry': 1, 'category': 'EARRINGS',
            'department': 'JWLY', 'discount_rate': 0.0,
            'revenue_with_vat': 300_000,
            'employee_sid': 'E2', 'employee_code': 'GL005',
            'employee_name': 'Pham Van Anh', 'employee_store_code': 'RWR',
        },
    ]


@pytest.fixture
def mock_repo(sample_bills, sample_items):
    repo = MagicMock()
    repo.get_all_bills.return_value = sample_bills
    # When called with a subset of bill_sids, filter accordingly.
    def items_side_effect(bill_sids):
        sids = set(bill_sids)
        return [it for it in sample_items if it['bill_sid'] in sids]
    repo.get_bill_items.side_effect = items_side_effect
    return repo


@pytest.fixture
def service(mock_repo):
    s = AccountPayableService(repository=mock_repo)
    return s


# Patch the date used inside the service so all tests use FROZEN_TODAY.
@pytest.fixture(autouse=True)
def freeze_today():
    with patch('app.modules.account_payable.service._today', return_value=FROZEN_TODAY):
        yield


# ----------------------------------------------------------------------
# get_bills
# ----------------------------------------------------------------------

class TestGetBills:

    def test_returns_billrow_shape(self, service):
        with patch.object(service, '_load_auto_rates', return_value={}):
            with patch.object(service, '_load_custom_rates', return_value={}):
                result = service.get_bills()
        assert result['success']
        data = result['data']
        assert len(data) == 3
        # Newest first
        assert [r['bill_sid'] for r in data] == ['B1', 'B2', 'B3']
        b1 = data[0]
        assert b1['employee_codes'] == ['GH083']
        assert b1['age_days'] == 18  # 2026-06-02 minus 2026-05-15
        # Payments decorated with release_status/release_month.
        b2 = data[1]
        assert b2['payments'][0]['release_status'] == 'released'
        assert b2['payments'][0]['release_month'] == '2026-05'

    def test_status_filter(self, service):
        result = service.get_bills(status='open')
        assert {r['bill_sid'] for r in result['data']} == {'B1'}

    def test_search_case_insensitive(self, service):
        result = service.get_bills(search='ALICE')
        assert {r['bill_sid'] for r in result['data']} == {'B1', 'B2'}

    def test_search_matches_doc_no(self, service):
        result = service.get_bills(search='D-3')
        assert {r['bill_sid'] for r in result['data']} == {'B3'}

    def test_empty_ledger(self, mock_repo):
        mock_repo.get_all_bills.return_value = {}
        result = AccountPayableService(repository=mock_repo).get_bills()
        assert result == {'success': True, 'data': []}


# ----------------------------------------------------------------------
# get_bill_detail
# ----------------------------------------------------------------------

class TestGetBillDetail:

    def test_not_found_returns_404_shape(self, service):
        result = service.get_bill_detail('UNKNOWN')
        assert result['success'] is False
        assert result['not_found'] is True

    def test_detail_includes_items_and_rates(self, service):
        # Mock the rate lookups: B1 item S1 has auto rate, S2 is legacy with custom.
        auto = {
            ('B1', '216335'): {
                'revenue_type': 'fashion', 'fp_or_md': 'fp', 'effective_rate': 0.0025,
            },
        }
        custom = {('B1', '216336'): 0.015}
        with patch.object(service, '_load_auto_rates', return_value=auto):
            with patch.object(service, '_load_custom_rates', return_value=custom):
                result = service.get_bill_detail('B1')
        assert result['success']
        d = result['data']
        assert d['bill_sid'] == 'B1'
        assert d['sale_total_amt'] == 1_100_000
        assert len(d['items']) == 2
        by_upc = {it['upc']: it for it in d['items']}
        assert by_upc['216335']['auto_release_rate'] == 0.0025
        assert by_upc['216335']['custom_release_rate'] is None
        assert by_upc['216335']['revenue_type'] == 'fashion'
        assert by_upc['216335']['fp_or_md'] == 'fp'
        # Legacy item — null auto, custom set, no revenue_type yet.
        assert by_upc['216336']['auto_release_rate'] is None
        assert by_upc['216336']['custom_release_rate'] == 0.015
        assert by_upc['216336']['revenue_type'] is None
        assert by_upc['216336']['fp_or_md'] is None


# ----------------------------------------------------------------------
# get_employees
# ----------------------------------------------------------------------

class TestGetEmployees:

    def test_aggregates_proportional_share_skipping_fully_paid(self, service):
        result = service.get_employees()
        assert result['success']
        data = result['data']
        # GH083 (B1, all open): unpaid_ratio = 1_000_000/1_100_000 ≈ 0.909
        #   share = 1_000_000 * 0.909 ≈ 909_091
        # GL005 (only B2 — B3 is fully_paid so excluded):
        #   unpaid_ratio = 300_000/550_000 ≈ 0.5455
        #   share = 500_000 * 0.5455 ≈ 272_727
        by_code = {e['employee_code']: e for e in data}
        assert set(by_code) == {'GH083', 'GL005'}
        assert by_code['GH083']['total_payable'] == 909_091
        assert by_code['GH083']['open_bills_count'] == 1
        assert by_code['GH083']['store_code'] == 'HBT'
        assert by_code['GL005']['total_payable'] == 272_727
        assert by_code['GL005']['open_bills_count'] == 1
        assert by_code['GL005']['store_code'] == 'RWR'
        # Sorted by total_payable desc.
        assert data[0]['employee_code'] == 'GH083'

    def test_empty_when_no_active_bills(self, mock_repo, sample_bills):
        # Drive all bills into fully_paid by setting total_paid = original.
        # (CR #68: status is now derived from numbers in `_apply_voids_to_bills`,
        # so the previous shortcut of setting `status` directly no longer works.)
        for b in sample_bills.values():
            b['total_paid'] = b['original_charge']
            b['remaining_unpaid'] = 0
            b['status'] = 'fully_paid'
        mock_repo.get_all_bills.return_value = sample_bills
        # _apply_voids_to_bills touches Postgres — stub for unit-test isolation.
        with patch.object(
            AccountPayableService, '_load_voids_by_bill_sid', return_value={},
        ), patch.object(
            AccountPayableService, '_load_manual_allocations', return_value={},
        ):
            result = AccountPayableService(repository=mock_repo).get_employees()
        assert result == {'success': True, 'data': []}


# ----------------------------------------------------------------------
# get_employee_bills
# ----------------------------------------------------------------------

class TestGetEmployeeBills:

    def test_filters_to_one_employee(self, service):
        with patch.object(service, '_load_auto_rates', return_value={}):
            with patch.object(service, '_load_custom_rates', return_value={}):
                result = service.get_employee_bills('GH083')
        assert result['success']
        data = result['data']
        assert len(data) == 1
        slice_ = data[0]
        assert slice_['bill_sid'] == 'B1'
        assert slice_['bill_original_charge'] == 1_000_000
        # employee_share for GH083 on B1 = (600k + 400k) * (1_000_000/1_100_000)
        # = 1_000_000 * 0.909 = 909_091
        assert slice_['employee_share'] == 909_091
        # Items scoped to GH083 only — 2 items on B1.
        assert len(slice_['items']) == 2
        assert all(it['employee_code'] == 'GH083' for it in slice_['items'])

    def test_empty_for_unknown_employee(self, service):
        with patch.object(service, '_load_auto_rates', return_value={}):
            with patch.object(service, '_load_custom_rates', return_value={}):
                result = service.get_employee_bills('GHZZZ')
        assert result == {'success': True, 'data': []}
