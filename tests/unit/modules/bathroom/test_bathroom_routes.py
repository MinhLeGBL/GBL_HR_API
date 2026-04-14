"""
Unit tests for bathroom routes

Tests:
- GET  /api/v1/bathroom/products
- GET  /api/v1/bathroom/brand-settings
- PUT  /api/v1/bathroom/brand-settings
"""
import pytest
from unittest.mock import patch


def _auth_headers():
    return {'Authorization': 'Bearer testtoken'}


def _mock_auth(mock_verify, mock_get_user, role='admin', department_code='IT'):
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': role,
        'role_code': role.upper(), 'type': 'access'
    }
    mock_get_user.return_value = {
        'sid': 1, 'role': role, 'role_code': role.upper(),
        'department_id': 400000001,
        'department_code': department_code,
        'department': {'id': 400000001, 'code': department_code, 'name': department_code}
    }


# ===========================================================================
# GET /api/v1/bathroom/products
# ===========================================================================

class TestGetProducts:
    URL = '/api/v1/bathroom/products'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.bathroom.routes.bathroom_service.get_products')
    def test_success_no_filters(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {
            'success': True,
            'products': [{'brand': 'paffoni', 'model_code': 'ABC123', 'price': 18.0}],
            'total': 1
        }

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['total'] == 1
        mock_svc.assert_called_once_with(brand=None, search=None)

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.bathroom.routes.bathroom_service.get_products')
    def test_with_brand_filter(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'products': [], 'total': 0}

        resp = client.get(f'{self.URL}?brand=paffoni', headers=_auth_headers())

        assert resp.status_code == 200
        mock_svc.assert_called_once_with(brand='paffoni', search=None)

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.bathroom.routes.bathroom_service.get_products')
    def test_with_search_filter(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'products': [], 'total': 0}

        resp = client.get(f'{self.URL}?search=mixer', headers=_auth_headers())

        assert resp.status_code == 200
        mock_svc.assert_called_once_with(brand=None, search='mixer')

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.bathroom.routes.bathroom_service.get_products')
    def test_service_error_returns_500(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': False, 'error': 'DB error'}

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 500

    def test_no_auth_returns_401(self, client):
        resp = client.get(self.URL)

        assert resp.status_code == 401


# ===========================================================================
# GET /api/v1/bathroom/brand-settings
# ===========================================================================

class TestGetBrandSettings:
    URL = '/api/v1/bathroom/brand-settings'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.bathroom.routes.bathroom_service.get_brand_settings')
    def test_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {
            'success': True,
            'brands': [{'name': 'Valsir', 'discount': 0.7}],
            'tax_rates': {'ceramic': 0.087}
        }

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert len(data['brands']) == 1

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.bathroom.routes.bathroom_service.get_brand_settings')
    def test_service_error_returns_500(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': False, 'error': 'DB error'}

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 500

    def test_no_auth_returns_401(self, client):
        resp = client.get(self.URL)

        assert resp.status_code == 401


# ===========================================================================
# PUT /api/v1/bathroom/brand-settings
# ===========================================================================

class TestUpdateBrandSettings:
    URL = '/api/v1/bathroom/brand-settings'

    VALID_PAYLOAD = {
        'brands': [
            {'name': 'Valsir', 'discount': 0.70, 'transport': 0.08, 'margin': 0.20, 'insurance': 0.0035},
        ],
        'tax_rates': {'ceramic': 0.087, 'other': 0.20}
    }

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.bathroom.routes.bathroom_service.update_brand_settings')
    def test_admin_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='admin')
        mock_svc.return_value = {'success': True}

        resp = client.put(self.URL, json=self.VALID_PAYLOAD, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.bathroom.routes.bathroom_service.update_brand_settings')
    def test_manager_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')
        mock_svc.return_value = {'success': True}

        resp = client.put(self.URL, json=self.VALID_PAYLOAD, headers=_auth_headers())

        assert resp.status_code == 200

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_staff_rejected(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='staff')

        resp = client.put(self.URL, json=self.VALID_PAYLOAD, headers=_auth_headers())

        assert resp.status_code == 403

    def test_no_auth_returns_401(self, client):
        resp = client.put(self.URL, json=self.VALID_PAYLOAD)

        assert resp.status_code == 401

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_body_returns_400(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='admin')

        resp = client.put(self.URL, headers=_auth_headers(), content_type='application/json')

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_brands_returns_400(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='admin')

        resp = client.put(self.URL, json={'tax_rates': {'ceramic': 0.087}}, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'brands' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_tax_rates_returns_400(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='admin')

        resp = client.put(self.URL, json={'brands': [{'name': 'Valsir'}]}, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'tax_rates' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.bathroom.routes.bathroom_service.update_brand_settings')
    def test_service_validation_error_returns_400(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='admin')
        mock_svc.return_value = {'success': False, 'error': 'Invalid brand name: Unknown'}

        resp = client.put(self.URL, json=self.VALID_PAYLOAD, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'Invalid brand name' in resp.get_json()['error']
