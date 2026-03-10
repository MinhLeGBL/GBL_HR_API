"""
Unit tests for Commission API Routes

Tests all 4 commission endpoints:
- POST /api/v1/commission/personal/calculate
- POST /api/v1/commission/batch/calculate
- POST /api/v1/commission/store/calculate-v2
- GET  /api/v1/commission/store/calculate-from-sheet
"""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers: valid request payloads
# ---------------------------------------------------------------------------

def _personal_payload(**overrides):
    """Return a valid payload for the personal/calculate endpoint."""
    payload = {
        'month': 6,
        'year': 2025,
        'employees': [
            {'employee_id': 'EMP001', 'target': 100000000, 'employee_name': 'Alice'},
            {'employee_id': 'EMP002', 'target': 80000000, 'employee_name': 'Bob'},
        ],
    }
    payload.update(overrides)
    return payload




def _store_v2_payload(**overrides):
    """Return a valid payload for the store/calculate-v2 endpoint."""
    payload = {
        'store_code': 'RHN',
        'store_target': 500000000,
        'store_fp_ratio_target': 0.6,
        'query_date': {
            'from_date': '2025-06-01 00:00:00',
            'to_date': '2025-06-30 23:59:59',
        },
        'employees': [
            {
                'employee_code': 'EMP001',
                'full_name': 'Alice',
                'seniority': 2,
                'personal_target': 100000000,
                'working_day_count': 26,
                'is_manager': False,
                'is_probation': False,
            },
        ],
    }
    payload.update(overrides)
    return payload


# ===========================================================================
# 1. POST /api/v1/commission/personal/calculate
# ===========================================================================

class TestCalculatePersonalCommission:
    """Tests for the personal commission calculation endpoint."""

    URL = '/api/v1/commission/personal/calculate'

    @patch('app.modules.commission.routes.CommissionService')
    def test_success(self, MockCommissionService, client):
        mock_service = MockCommissionService.return_value
        mock_service.calculate_personal_commissions.return_value = {
            'success': True,
            'data': [
                {'employee_id': 'EMP001', 'commission': 5000000},
                {'employee_id': 'EMP002', 'commission': 3000000},
            ],
        }

        resp = client.post(self.URL, json=_personal_payload())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['data']) == 2
        mock_service.calculate_personal_commissions.assert_called_once_with(
            month=6,
            year=2025,
            employees=_personal_payload()['employees'],
        )

    def test_missing_month(self, client):
        payload = _personal_payload()
        del payload['month']
        resp = client.post(self.URL, json=payload)

        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'month' in body['error']

    def test_missing_year(self, client):
        payload = _personal_payload()
        del payload['year']
        resp = client.post(self.URL, json=payload)

        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'year' in body['error']

    def test_missing_employees(self, client):
        payload = _personal_payload()
        del payload['employees']
        resp = client.post(self.URL, json=payload)

        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'employees' in body['error']

    def test_invalid_month_too_low(self, client):
        resp = client.post(self.URL, json=_personal_payload(month=0))

        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'month must be between 1 and 12' in body['error']

    def test_invalid_month_too_high(self, client):
        resp = client.post(self.URL, json=_personal_payload(month=13))

        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'month must be between 1 and 12' in body['error']

    def test_empty_employees_list(self, client):
        resp = client.post(self.URL, json=_personal_payload(employees=[]))

        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'non-empty list' in body['error']

    def test_employee_missing_employee_id(self, client):
        payload = _personal_payload(employees=[{'target': 100000000}])
        resp = client.post(self.URL, json=payload)

        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'employee_id or target' in body['error']

    def test_employee_missing_target(self, client):
        payload = _personal_payload(employees=[{'employee_id': 'EMP001'}])
        resp = client.post(self.URL, json=payload)

        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'employee_id or target' in body['error']

    @patch('app.modules.commission.routes.CommissionService')
    def test_service_returns_error(self, MockCommissionService, client):
        mock_service = MockCommissionService.return_value
        mock_service.calculate_personal_commissions.return_value = {
            'success': False,
            'error': 'No revenue data found for given period',
        }

        resp = client.post(self.URL, json=_personal_payload())

        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'No revenue data found' in body['error']

    @patch('app.modules.commission.routes.CommissionService')
    def test_exception_returns_500(self, MockCommissionService, client):
        mock_service = MockCommissionService.return_value
        mock_service.calculate_personal_commissions.side_effect = RuntimeError(
            'Database connection lost'
        )

        resp = client.post(self.URL, json=_personal_payload())

        assert resp.status_code == 500
        body = resp.get_json()
        assert body['success'] is False
        assert 'Database connection lost' in body['error']



# ===========================================================================
# 3. POST /api/v1/commission/store/calculate-v2
# ===========================================================================

class TestCalculateStoreCommissionV2:
    """Tests for the store commission v2 calculation endpoint."""

    URL = '/api/v1/commission/store/calculate-v2'

    @patch('app.modules.commission.routes.CommissionService')
    def test_success(self, MockCommissionService, client):
        mock_result = {
            'eligible': True,
            'store_code': 'RHN',
            'achievement_pct': 1.05,
            'actual_fp_ratio': 0.65,
            'store_target': 500000000,
            'actual_revenue': 525000000,
            'store_pool': 20000000,
            'total_employee_count': 1,
            'total_working_days': 26,
            'employees': [
                {
                    'employee_code': 'EMP001',
                    'full_name': 'Alice',
                    'seniority': 2,
                    'is_manager': False,
                    'is_probation': False,
                    'working_day_count': 26,
                    'store_code': 'RHN',
                    'individual_share': 14000000,
                    'equal_share': 6000000,
                    'manager_bonus': 0,
                    'total_store_commission': 20000000,
                },
            ],
        }
        mock_service = MockCommissionService.return_value
        mock_service.calculate_store_commission_v2.return_value = mock_result

        resp = client.post(self.URL, json=_store_v2_payload())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['data']['eligible'] is True
        assert body['data']['store_code'] == 'RHN'
        mock_service.calculate_store_commission_v2.assert_called_once_with(
            store_code='RHN',
            store_target=500000000,
            store_fp_ratio_target=0.6,
            query_date={
                'from_date': '2025-06-01 00:00:00',
                'to_date': '2025-06-30 23:59:59',
            },
            employees=_store_v2_payload()['employees'],
        )

    def test_missing_store_code(self, client):
        payload = _store_v2_payload()
        del payload['store_code']
        resp = client.post(self.URL, json=payload)

        assert resp.status_code == 400
        assert 'store_code' in resp.get_json()['error']

    def test_missing_store_target(self, client):
        payload = _store_v2_payload()
        del payload['store_target']
        resp = client.post(self.URL, json=payload)

        assert resp.status_code == 400
        assert 'store_target' in resp.get_json()['error']

    def test_missing_store_fp_ratio_target(self, client):
        payload = _store_v2_payload()
        del payload['store_fp_ratio_target']
        resp = client.post(self.URL, json=payload)

        assert resp.status_code == 400
        assert 'store_fp_ratio_target' in resp.get_json()['error']

    def test_missing_query_date(self, client):
        payload = _store_v2_payload()
        del payload['query_date']
        resp = client.post(self.URL, json=payload)

        assert resp.status_code == 400
        assert 'query_date' in resp.get_json()['error']

    def test_missing_employees(self, client):
        payload = _store_v2_payload()
        del payload['employees']
        resp = client.post(self.URL, json=payload)

        assert resp.status_code == 400
        assert 'employees' in resp.get_json()['error']

    def test_invalid_query_date_not_dict(self, client):
        resp = client.post(
            self.URL, json=_store_v2_payload(query_date='2025-06-01')
        )

        assert resp.status_code == 400
        body = resp.get_json()
        assert 'query_date must be an object' in body['error']

    def test_invalid_query_date_missing_from_date(self, client):
        resp = client.post(
            self.URL,
            json=_store_v2_payload(
                query_date={'to_date': '2025-06-30 23:59:59'}
            ),
        )

        assert resp.status_code == 400
        body = resp.get_json()
        assert 'from_date and to_date' in body['error']

    def test_invalid_query_date_missing_to_date(self, client):
        resp = client.post(
            self.URL,
            json=_store_v2_payload(
                query_date={'from_date': '2025-06-01 00:00:00'}
            ),
        )

        assert resp.status_code == 400
        body = resp.get_json()
        assert 'from_date and to_date' in body['error']

    def test_invalid_fp_ratio_too_high(self, client):
        resp = client.post(
            self.URL, json=_store_v2_payload(store_fp_ratio_target=1.1)
        )

        assert resp.status_code == 400
        body = resp.get_json()
        assert 'store_fp_ratio_target must be between 0.0 and 1.0' in body['error']

    def test_invalid_fp_ratio_negative(self, client):
        resp = client.post(
            self.URL, json=_store_v2_payload(store_fp_ratio_target=-0.5)
        )

        assert resp.status_code == 400
        body = resp.get_json()
        assert 'store_fp_ratio_target must be between 0.0 and 1.0' in body['error']

    def test_empty_employees(self, client):
        resp = client.post(self.URL, json=_store_v2_payload(employees=[]))

        assert resp.status_code == 400
        body = resp.get_json()
        assert 'non-empty list' in body['error']

    def test_employee_missing_employee_code(self, client):
        bad_emp = {
            'full_name': 'Alice',
            'seniority': 2,
            'personal_target': 100000000,
            'working_day_count': 26,
            'is_manager': False,
            'is_probation': False,
        }
        resp = client.post(self.URL, json=_store_v2_payload(employees=[bad_emp]))

        assert resp.status_code == 400
        body = resp.get_json()
        assert 'employee_code' in body['error']

    def test_employee_missing_is_manager(self, client):
        bad_emp = {
            'employee_code': 'EMP001',
            'full_name': 'Alice',
            'seniority': 2,
            'personal_target': 100000000,
            'working_day_count': 26,
            'is_probation': False,
        }
        resp = client.post(self.URL, json=_store_v2_payload(employees=[bad_emp]))

        assert resp.status_code == 400
        body = resp.get_json()
        assert 'is_manager' in body['error']

    def test_employee_missing_working_day_count(self, client):
        bad_emp = {
            'employee_code': 'EMP001',
            'full_name': 'Alice',
            'seniority': 2,
            'personal_target': 100000000,
            'is_manager': False,
            'is_probation': False,
        }
        resp = client.post(self.URL, json=_store_v2_payload(employees=[bad_emp]))

        assert resp.status_code == 400
        body = resp.get_json()
        assert 'working_day_count' in body['error']

    @patch('app.modules.commission.routes.CommissionService')
    def test_exception_returns_500(self, MockCommissionService, client):
        mock_service = MockCommissionService.return_value
        mock_service.calculate_store_commission_v2.side_effect = RuntimeError(
            'Oracle timeout'
        )

        resp = client.post(self.URL, json=_store_v2_payload())

        assert resp.status_code == 500
        body = resp.get_json()
        assert body['success'] is False
        assert 'Oracle timeout' in body['error']

