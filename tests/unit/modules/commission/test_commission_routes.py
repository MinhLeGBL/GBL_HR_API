"""
Unit tests for Commission API Routes

Tests:
- POST /api/v1/commission/personal/calculate
- POST /api/v1/commission/store/calculate-v2
"""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _auth_headers():
    return {'Authorization': 'Bearer testtoken'}


def _mock_auth(mock_verify, mock_get_user):
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': 'admin',
        'role_code': 'ADMIN', 'type': 'access'
    }
    mock_get_user.return_value = {
        'sid': 1, 'role': 'admin', 'role_code': 'ADMIN',
        'department_code': 'IT'
    }


# ---------------------------------------------------------------------------
# Helpers: valid request payloads
# ---------------------------------------------------------------------------

def _personal_payload(**overrides):
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
    URL = '/api/v1/commission/personal/calculate'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionService')
    def test_success(self, MockCommissionService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_service = MockCommissionService.return_value
        mock_service.calculate_personal_commissions.return_value = {
            'success': True,
            'data': [
                {'employee_id': 'EMP001', 'commission': 5000000},
                {'employee_id': 'EMP002', 'commission': 3000000},
            ],
        }

        resp = client.post(self.URL, json=_personal_payload(), headers=_auth_headers())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['data']) == 2

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_month(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        payload = _personal_payload()
        del payload['month']
        resp = client.post(self.URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'month' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_year(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        payload = _personal_payload()
        del payload['year']
        resp = client.post(self.URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'year' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_employees(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        payload = _personal_payload()
        del payload['employees']
        resp = client.post(self.URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'employees' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_invalid_month_too_low(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        resp = client.post(self.URL, json=_personal_payload(month=0), headers=_auth_headers())

        assert resp.status_code == 400
        assert 'month must be between 1 and 12' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_empty_employees_list(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        resp = client.post(self.URL, json=_personal_payload(employees=[]), headers=_auth_headers())

        assert resp.status_code == 400
        assert 'non-empty list' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_employee_missing_employee_id(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        payload = _personal_payload(employees=[{'target': 100000000}])
        resp = client.post(self.URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'employee_id or target' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionService')
    def test_service_returns_error(self, MockCommissionService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_service = MockCommissionService.return_value
        mock_service.calculate_personal_commissions.return_value = {
            'success': False,
            'error': 'No revenue data found for given period',
        }

        resp = client.post(self.URL, json=_personal_payload(), headers=_auth_headers())

        assert resp.status_code == 400
        assert 'No revenue data found' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionService')
    def test_exception_returns_500(self, MockCommissionService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_service = MockCommissionService.return_value
        mock_service.calculate_personal_commissions.side_effect = RuntimeError('Database connection lost')

        resp = client.post(self.URL, json=_personal_payload(), headers=_auth_headers())

        assert resp.status_code == 500
        assert 'Database connection lost' in resp.get_json()['error']

    def test_no_auth_returns_401(self, client):
        resp = client.post(self.URL, json=_personal_payload())
        assert resp.status_code == 401


# ===========================================================================
# 2. POST /api/v1/commission/store/calculate-v2
# ===========================================================================

class TestCalculateStoreCommissionV2:
    URL = '/api/v1/commission/store/calculate-v2'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionService')
    def test_success(self, MockCommissionService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
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
            'employees': [{
                'employee_code': 'EMP001', 'full_name': 'Alice',
                'seniority': 2, 'is_manager': False, 'is_probation': False,
                'working_day_count': 26, 'store_code': 'RHN',
                'individual_share': 14000000, 'equal_share': 6000000,
                'manager_bonus': 0, 'total_store_commission': 20000000,
            }],
        }
        mock_service = MockCommissionService.return_value
        mock_service.calculate_store_commission_v2.return_value = mock_result

        resp = client.post(self.URL, json=_store_v2_payload(), headers=_auth_headers())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['data']['eligible'] is True
        assert body['data']['store_code'] == 'RHN'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_store_code(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        payload = _store_v2_payload()
        del payload['store_code']
        resp = client.post(self.URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'store_code' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_store_target(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        payload = _store_v2_payload()
        del payload['store_target']
        resp = client.post(self.URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'store_target' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_query_date(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        payload = _store_v2_payload()
        del payload['query_date']
        resp = client.post(self.URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'query_date' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_employees(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        payload = _store_v2_payload()
        del payload['employees']
        resp = client.post(self.URL, json=payload, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'employees' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_empty_employees(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        resp = client.post(self.URL, json=_store_v2_payload(employees=[]), headers=_auth_headers())

        assert resp.status_code == 400
        assert 'non-empty list' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionService')
    def test_exception_returns_500(self, MockCommissionService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_service = MockCommissionService.return_value
        mock_service.calculate_store_commission_v2.side_effect = RuntimeError('Oracle timeout')

        resp = client.post(self.URL, json=_store_v2_payload(), headers=_auth_headers())

        assert resp.status_code == 500
        assert 'Oracle timeout' in resp.get_json()['error']

    def test_no_auth_returns_401(self, client):
        resp = client.post(self.URL, json=_store_v2_payload())
        assert resp.status_code == 401
