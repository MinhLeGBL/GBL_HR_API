"""
Unit tests for commission revenue and calculate routes

Tests:
- GET  /api/v1/commission/revenue
- GET  /api/v1/commission/revenue/store-view
- PUT  /api/v1/commission/revenue/adjustments/<employee_code>
- POST /api/v1/commission/calculate
"""
import pytest
from unittest.mock import patch, MagicMock


def _auth_headers():
    return {'Authorization': 'Bearer testtoken'}


def _mock_auth(mock_verify, mock_get_user):
    """Set up auth mocks for a valid admin user."""
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': 'admin',
        'role_code': 'ADMIN', 'type': 'access'
    }
    mock_get_user.return_value = {
        'sid': 1, 'role': 'admin', 'role_code': 'ADMIN',
        'department_code': 'IT'
    }


# ===========================================================================
# GET /api/v1/commission/revenue
# ===========================================================================

class TestGetCommissionRevenue:
    URL = '/api/v1/commission/revenue'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionRevenueService')
    def test_success(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.get_revenue_breakdown.return_value = {
            'success': True,
            'month': 3,
            'year': 2026,
            'revenue_types': [],
            'stores': [],
        }

        resp = client.get(f'{self.URL}?month=3&year=2026', headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['month'] == 3

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_month(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.get(f'{self.URL}?year=2026', headers=_auth_headers())

        assert resp.status_code == 400
        assert 'month' in resp.get_json()['error'].lower()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_year(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.get(f'{self.URL}?month=3', headers=_auth_headers())

        assert resp.status_code == 400
        assert 'month and year' in resp.get_json()['error'].lower()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_invalid_month(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.get(f'{self.URL}?month=13&year=2026', headers=_auth_headers())

        assert resp.status_code == 400
        assert 'month' in resp.get_json()['error'].lower()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_invalid_year_out_of_range(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.get(f'{self.URL}?month=3&year=1999', headers=_auth_headers())

        assert resp.status_code == 400
        assert 'year' in resp.get_json()['error'].lower()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_non_integer_month(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.get(f'{self.URL}?month=abc&year=2026', headers=_auth_headers())

        assert resp.status_code == 400

    def test_no_auth_returns_401(self, client):
        resp = client.get(f'{self.URL}?month=3&year=2026')
        assert resp.status_code == 401

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionRevenueService')
    def test_service_error(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.get_revenue_breakdown.return_value = {
            'success': False, 'error': 'DB error'
        }

        resp = client.get(f'{self.URL}?month=3&year=2026', headers=_auth_headers())

        assert resp.status_code == 500


# ===========================================================================
# GET /api/v1/commission/revenue/store-view  (CR #26)
# ===========================================================================

class TestGetStoreViewRevenue:
    URL = '/api/v1/commission/revenue/store-view'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionRevenueService')
    def test_success(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.get_store_view_breakdown.return_value = {
            'success': True,
            'month': 3,
            'year': 2026,
            'revenue_types': [],
            'stores': [],
        }

        resp = client.get(f'{self.URL}?month=3&year=2026', headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['month'] == 3

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_month(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.get(f'{self.URL}?year=2026', headers=_auth_headers())

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_year(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.get(f'{self.URL}?month=3', headers=_auth_headers())

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_invalid_month(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.get(f'{self.URL}?month=13&year=2026', headers=_auth_headers())

        assert resp.status_code == 400

    def test_no_auth_returns_401(self, client):
        resp = client.get(f'{self.URL}?month=3&year=2026')
        assert resp.status_code == 401

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionRevenueService')
    def test_service_error(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.get_store_view_breakdown.return_value = {
            'success': False, 'error': 'DB error'
        }

        resp = client.get(f'{self.URL}?month=3&year=2026', headers=_auth_headers())

        assert resp.status_code == 500

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionRevenueService')
    def test_exception_returns_500(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.get_store_view_breakdown.side_effect = Exception('boom')

        resp = client.get(f'{self.URL}?month=3&year=2026', headers=_auth_headers())

        assert resp.status_code == 500
        assert 'boom' in resp.get_json()['error']


# ===========================================================================
# PUT /api/v1/commission/revenue/adjustments/<employee_code>
# ===========================================================================

class TestUpdateRevenueAdjustments:
    URL = '/api/v1/commission/revenue/adjustments/GL013'

    def _valid_body(self, **overrides):
        body = {
            'month': 3,
            'year': 2026,
            'store_code': 'RWR',
            'adjustments': [
                {'revenue_type': 'fashion_fp', 'adjustment': 50000000},
            ],
        }
        body.update(overrides)
        return body

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionRevenueService')
    def test_success(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.save_revenue_adjustments.return_value = {
            'success': True,
            'data': {
                'employee_code': 'GL013',
                'month': 3,
                'year': 2026,
                'store_code': 'RWR',
                'adjustments': [{'revenue_type': 'fashion_fp', 'adjustment': 50000000}],
            }
        }

        resp = client.put(self.URL, json=self._valid_body(), headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_store_code(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        body = {'month': 3, 'year': 2026, 'adjustments': []}
        resp = client.put(self.URL, json=body, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'store_code' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_empty_store_code(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        body = {'month': 3, 'year': 2026, 'store_code': '', 'adjustments': []}
        resp = client.put(self.URL, json=body, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'store_code' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_adjustments_field(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        body = {'month': 3, 'year': 2026, 'store_code': 'RWR'}
        resp = client.put(self.URL, json=body, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'adjustments' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_month(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        body = {'year': 2026, 'adjustments': []}
        resp = client.put(self.URL, json=body, headers=_auth_headers())

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_adjustments_not_list(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        body = self._valid_body(adjustments='not-a-list')
        resp = client.put(self.URL, json=body, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'array' in resp.get_json()['error'].lower()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_no_json_body(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.put(self.URL, data='not json', headers=_auth_headers(),
                          content_type='text/plain')

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionRevenueService')
    def test_employee_not_found(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.save_revenue_adjustments.return_value = {
            'success': False, 'error': 'Employee not found: GL999', 'not_found': True
        }

        resp = client.put(self.URL, json=self._valid_body(), headers=_auth_headers())

        assert resp.status_code == 404

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionRevenueService')
    def test_validation_error(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.save_revenue_adjustments.return_value = {
            'success': False, 'error': 'Invalid revenue_type'
        }

        resp = client.put(self.URL, json=self._valid_body(), headers=_auth_headers())

        assert resp.status_code == 400

    def test_no_auth_returns_401(self, client):
        resp = client.put(self.URL, json=self._valid_body())
        assert resp.status_code == 401

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_year_out_of_range(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        body = self._valid_body(year=2200)
        resp = client.put(self.URL, json=body, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'year' in resp.get_json()['error'].lower()


# ===========================================================================
# POST /api/v1/commission/calculate
# ===========================================================================

class TestCalculateCommission:
    URL = '/api/v1/commission/calculate'

    def _valid_store(self):
        return {
            'store_code': 'RWR',
            'store_target': 11000000000,
            'fp_ratio_target': 60,
            'store_total': 6800000000,
            'store_fp_total': 4300000000,
            'store_achievement': 61.8,
            'store_fp_ratio': 63.2,
            'store_eligible': True,
            'employees': [{
                'employee_code': 'GL013',
                'is_manager': True,
                'contract': 'permanent',
                'personal_target': 650000000,
                'personal_total': 680000000,
                'personal_achievement': 104.6,
                'personal_eligible': True,
                'revenue': [{
                    'revenue_type': 'fashion',
                    'fp_base': 400000000,
                    'fp_adjustment': 50000000,
                    'md_base': 80000000,
                    'md_adjustment': 0
                }]
            }]
        }

    def _valid_body(self, **overrides):
        body = {'month': 3, 'year': 2026, 'stores': [self._valid_store()]}
        body.update(overrides)
        return body

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionService')
    def test_success(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.calculate_commissions_v2.return_value = {
            'success': True,
            'month': 3,
            'year': 2026,
            'stores': [],
        }

        resp = client.post(self.URL, json=self._valid_body(), headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['month'] == 3

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_month(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.post(self.URL, json={'year': 2026, 'stores': []}, headers=_auth_headers())

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_year(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.post(self.URL, json={'month': 3, 'stores': []}, headers=_auth_headers())

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_stores(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.post(self.URL, json={'month': 3, 'year': 2026}, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'stores' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_empty_stores(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.post(self.URL, json={'month': 3, 'year': 2026, 'stores': []},
                           headers=_auth_headers())

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_store_missing_required_field(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        store = self._valid_store()
        del store['store_code']

        resp = client.post(self.URL, json={'month': 3, 'year': 2026, 'stores': [store]},
                           headers=_auth_headers())

        assert resp.status_code == 400
        assert 'store_code' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_employee_missing_required_field(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        store = self._valid_store()
        del store['employees'][0]['employee_code']

        resp = client.post(self.URL, json={'month': 3, 'year': 2026, 'stores': [store]},
                           headers=_auth_headers())

        assert resp.status_code == 400
        assert 'employee_code' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_invalid_month(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.post(self.URL, json={'month': 0, 'year': 2026, 'stores': [self._valid_store()]},
                           headers=_auth_headers())

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_no_json_body(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.post(self.URL, data='not json', headers=_auth_headers(),
                           content_type='text/plain')

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionService')
    def test_service_error(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.calculate_commissions_v2.return_value = {
            'success': False, 'error': 'Calculation failed'
        }

        resp = client.post(self.URL, json=self._valid_body(), headers=_auth_headers())

        assert resp.status_code == 500

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionService')
    def test_exception_returns_500(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.calculate_commissions_v2.side_effect = Exception('boom')

        resp = client.post(self.URL, json=self._valid_body(), headers=_auth_headers())

        assert resp.status_code == 500
        assert 'boom' in resp.get_json()['error']

    def test_no_auth_returns_401(self, client):
        resp = client.post(self.URL, json={'month': 3, 'year': 2026, 'stores': []})
        assert resp.status_code == 401
