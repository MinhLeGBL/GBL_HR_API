"""
Unit tests for app.modules.reports.routes (CR #78).

Mocks ReportsService + auth middleware. Covers the HTTP layer: required-param
validation, status-code mapping, param wiring, and auth.
"""
import json
from unittest.mock import patch

import pytest

from app.main import create_app

SERVICE = 'app.modules.reports.routes.reports_service'
VERIFY = 'app.core.auth.middleware.auth_service.verify_token'
GET_USER = 'app.core.auth.middleware.auth_service.get_user_by_sid'
URL = '/api/v1/reports/sale-comparison'
_VALID_QS = 'from_a=2026-06-01&to_a=2026-06-30&from_b=2026-05-01&to_b=2026-05-31'


@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _headers():
    return {'Authorization': 'Bearer testtoken'}


def _mock_auth(mock_verify, mock_get_user, role='admin'):
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': role,
        'role_code': role.upper(), 'type': 'access',
    }
    mock_get_user.return_value = {
        'sid': 1, 'role': role, 'role_code': role.upper(),
        'department_id': 400000001, 'department_code': 'IT',
        'department': {'id': 400000001, 'code': 'IT', 'name': 'IT'},
    }


class TestSaleComparisonRoute:

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE)
    def test_200_with_data(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_sale_comparison.return_value = {
            'success': True,
            'period_a': {'from': '2026-06-01', 'to': '2026-06-30'},
            'period_b': {'from': '2026-05-01', 'to': '2026-05-31'},
        }
        resp = client.get(f'{URL}?{_VALID_QS}', headers=_headers())
        assert resp.status_code == 200
        assert json.loads(resp.data)['success'] is True
        # Params wired through as kwargs.
        mock_svc.get_sale_comparison.assert_called_once_with(
            from_a='2026-06-01', to_a='2026-06-30',
            from_b='2026-05-01', to_b='2026-05-31',
        )

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE)
    def test_missing_param_is_400_without_service_call(
            self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        # Drop from_b.
        resp = client.get(
            f'{URL}?from_a=2026-06-01&to_a=2026-06-30&to_b=2026-05-31',
            headers=_headers())
        assert resp.status_code == 400
        body = json.loads(resp.data)
        assert body['code'] == 'INVALID_INPUT'
        assert 'from_b' in body['error']
        mock_svc.get_sale_comparison.assert_not_called()

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE)
    def test_invalid_input_maps_to_400(
            self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_sale_comparison.return_value = {
            'success': False, 'error': 'bad', 'code': 'INVALID_INPUT'}
        resp = client.get(f'{URL}?{_VALID_QS}', headers=_headers())
        assert resp.status_code == 400

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE)
    def test_server_error_maps_to_500(
            self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_sale_comparison.return_value = {
            'success': False, 'error': 'boom', 'code': 'SERVER_ERROR'}
        resp = client.get(f'{URL}?{_VALID_QS}', headers=_headers())
        assert resp.status_code == 500

    def test_requires_auth(self, client):
        """No token → 401, and the endpoint is reachable (registered)."""
        resp = client.get(f'{URL}?{_VALID_QS}')
        assert resp.status_code == 401
