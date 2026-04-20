"""
Unit tests for app.modules.crm.routes.

Mocks CRMService + auth middleware. Tests HTTP layer: status codes, content
type, query param wiring.
"""
import json
from unittest.mock import patch

import pytest

from app.main import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _auth_headers():
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


SERVICE_PATH = 'app.modules.crm.routes.crm_service'
VERIFY = 'app.core.auth.middleware.auth_service.verify_token'
GET_USER = 'app.core.auth.middleware.auth_service.get_user_by_sid'


# ---------------------------------------------------------------------------
# GET /customers
# ---------------------------------------------------------------------------

class TestGetCustomers:

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_200_with_data(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_customers.return_value = {
            'success': True, 'customers': [{'id': 1}], 'total': 1,
        }
        resp = client.get('/api/v1/crm/customers', headers=_auth_headers())
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['success'] is True
        assert data['total'] == 1

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_503_when_not_computed(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_customers.return_value = {
            'success': False, 'error': 'RFM data not yet computed.',
        }
        resp = client.get('/api/v1/crm/customers', headers=_auth_headers())
        assert resp.status_code == 503

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_query_params_passed(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_customers.return_value = {
            'success': True, 'customers': [], 'total': 0,
        }
        client.get('/api/v1/crm/customers?segment=VIC&min_weighted_score=4.0',
                    headers=_auth_headers())
        mock_svc.get_customers.assert_called_once_with(
            segment='VIC', min_weighted_score=4.0,
        )


# ---------------------------------------------------------------------------
# GET /segments/summary
# ---------------------------------------------------------------------------

class TestGetSegmentsSummary:

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_200(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_segments_summary.return_value = {
            'success': True, 'segments': [],
        }
        resp = client.get('/api/v1/crm/segments/summary', headers=_auth_headers())
        assert resp.status_code == 200

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_503_when_empty(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_segments_summary.return_value = {
            'success': False, 'error': 'not computed',
        }
        resp = client.get('/api/v1/crm/segments/summary', headers=_auth_headers())
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /segments/trends
# ---------------------------------------------------------------------------

class TestGetSegmentsTrends:

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_200_default_months(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_segments_trends.return_value = {
            'success': True, 'trends': [],
        }
        resp = client.get('/api/v1/crm/segments/trends', headers=_auth_headers())
        assert resp.status_code == 200
        mock_svc.get_segments_trends.assert_called_once_with(months=12)

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_custom_months(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_segments_trends.return_value = {'success': True, 'trends': []}
        client.get('/api/v1/crm/segments/trends?months=6', headers=_auth_headers())
        mock_svc.get_segments_trends.assert_called_once_with(months=6)

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_months_clamped_to_range(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_segments_trends.return_value = {'success': True, 'trends': []}
        # Too high → 60
        client.get('/api/v1/crm/segments/trends?months=999', headers=_auth_headers())
        mock_svc.get_segments_trends.assert_called_with(months=60)
        # Too low → 1
        client.get('/api/v1/crm/segments/trends?months=0', headers=_auth_headers())
        mock_svc.get_segments_trends.assert_called_with(months=1)


# ---------------------------------------------------------------------------
# GET /heatmap
# ---------------------------------------------------------------------------

class TestGetHeatmap:

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_200(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_heatmap.return_value = {
            'success': True, 'cells': [{'r': 5, 'f': 5, 'count': 28, 'total_monetary': 7000000000}],
        }
        resp = client.get('/api/v1/crm/heatmap', headers=_auth_headers())
        assert resp.status_code == 200

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_503_when_empty(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_heatmap.return_value = {'success': False, 'error': 'not computed'}
        resp = client.get('/api/v1/crm/heatmap', headers=_auth_headers())
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET/PUT /admin/config
# ---------------------------------------------------------------------------

class TestAdminConfig:

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_get_config(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.get_config.return_value = {
            'success': True, 'config': {'w_recency': 0.3},
        }
        resp = client.get('/api/v1/crm/admin/config', headers=_auth_headers())
        assert resp.status_code == 200

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_put_config_valid(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.update_config.return_value = {'success': True, 'config': {}}
        resp = client.put('/api/v1/crm/admin/config',
                          json={
                              'w_recency': 0.3, 'w_frequency': 0.3, 'w_monetary': 0.4,
                              'e_recency': 0.5, 'e_frequency': 0.3, 'e_monetary': 0.2,
                          },
                          headers=_auth_headers())
        assert resp.status_code == 200

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_put_config_missing_fields(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        resp = client.put('/api/v1/crm/admin/config',
                          json={'w_recency': 0.3},
                          headers=_auth_headers())
        assert resp.status_code == 400
        assert 'Missing fields' in json.loads(resp.data)['error']

    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_put_config_validation_error(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.update_config.return_value = {'success': False, 'error': 'sum to 1.0'}
        resp = client.put('/api/v1/crm/admin/config',
                          json={
                              'w_recency': 0.5, 'w_frequency': 0.5, 'w_monetary': 0.5,
                              'e_recency': 0.5, 'e_frequency': 0.3, 'e_monetary': 0.2,
                          },
                          headers=_auth_headers())
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /admin/recompute
# ---------------------------------------------------------------------------

class TestAdminRecompute:

    @patch('app.modules.crm.routes.recompute')
    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_200_on_success(self, _mock_svc, mock_get_user, mock_verify, mock_recompute, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_recompute.return_value = {'success': True, 'customers_scored': 3800}
        resp = client.post('/api/v1/crm/admin/recompute', headers=_auth_headers())
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['customers_scored'] == 3800

    @patch('app.modules.crm.routes.recompute')
    @patch(VERIFY)
    @patch(GET_USER)
    @patch(SERVICE_PATH)
    def test_500_on_error(self, _mock_svc, mock_get_user, mock_verify, mock_recompute, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_recompute.side_effect = Exception('Oracle down')
        resp = client.post('/api/v1/crm/admin/recompute', headers=_auth_headers())
        assert resp.status_code == 500
        data = json.loads(resp.data)
        assert 'Oracle down' in data['error']
