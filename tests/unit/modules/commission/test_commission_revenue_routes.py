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
                'adjustments': [{'revenue_type': 'fashion_fp', 'adjustment': 50000000}],
            }
        }

        resp = client.put(self.URL, json=self._valid_body(), headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_adjustments_field(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        body = {'month': 3, 'year': 2026}
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

    def _valid_body(self, **overrides):
        body = {'month': 3, 'year': 2026}
        body.update(overrides)
        return body

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionService')
    def test_success(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.calculate_commissions_for_period.return_value = {
            'success': True,
            'month': 3,
            'year': 2026,
            'employees': [],
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

        resp = client.post(self.URL, json={'year': 2026}, headers=_auth_headers())

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_year(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.post(self.URL, json={'month': 3}, headers=_auth_headers())

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_invalid_month(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.post(self.URL, json={'month': 0, 'year': 2026}, headers=_auth_headers())

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
        MockService.return_value.calculate_commissions_for_period.return_value = {
            'success': False, 'error': 'Calculation failed'
        }

        resp = client.post(self.URL, json=self._valid_body(), headers=_auth_headers())

        assert resp.status_code == 500

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionService')
    def test_exception_returns_500(self, MockService, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        MockService.return_value.calculate_commissions_for_period.side_effect = Exception('boom')

        resp = client.post(self.URL, json=self._valid_body(), headers=_auth_headers())

        assert resp.status_code == 500
        assert 'boom' in resp.get_json()['error']

    def test_no_auth_returns_401(self, client):
        resp = client.post(self.URL, json={'month': 3, 'year': 2026})
        assert resp.status_code == 401
