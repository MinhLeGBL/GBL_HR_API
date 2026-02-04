"""
Unit tests for Dashboard API Routes

dashboard_routes imports token_required and manager_required from auth_routes,
so auth mocking must patch app.api.v1.routes.auth_routes.auth_service.
"""
import json
import pytest
from unittest.mock import patch
from app.main import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    return app.test_client()


def _auth_headers():
    return {'Authorization': 'Bearer fake-token', 'Content-Type': 'application/json'}


def _setup_admin_auth(mock_verify, mock_get_user):
    """Admin auth -- passes manager_required (admin is accepted)."""
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': 'admin', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 1,
        'department': {'code': 'IT'}
    }


def _setup_manager_auth(mock_verify, mock_get_user):
    """Manager auth -- passes manager_required."""
    mock_verify.return_value = {
        'sid': 3, 'email': 'mgr@test.com', 'role': 'manager', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 2,
        'department': {'code': 'SALES'}
    }


def _setup_staff_auth(mock_verify, mock_get_user):
    """Staff auth -- fails manager_required."""
    mock_verify.return_value = {
        'sid': 2, 'email': 'staff@test.com', 'role': 'staff', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 2,
        'department': {'code': 'SALES'}
    }


# ==================== POST /api/v1/dashboard/init-db ====================

class TestInitDashboardTables:

    @patch('app.api.v1.routes.dashboard_routes.dashboard_service.init_dashboard_tables')
    def test_init_success(self, mock_init, client):
        mock_init.return_value = {'success': True, 'message': 'Dashboard tables created'}
        resp = client.post('/api/v1/dashboard/init-db')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.api.v1.routes.dashboard_routes.dashboard_service.init_dashboard_tables')
    def test_init_failure(self, mock_init, client):
        mock_init.return_value = {'success': False, 'error': 'DB error'}
        resp = client.post('/api/v1/dashboard/init-db')
        assert resp.status_code == 500
        body = resp.get_json()
        assert body['success'] is False


# ==================== GET /api/v1/dashboard/config/<dept_code> ====================

class TestGetDashboardConfig:

    @patch('app.api.v1.routes.dashboard_routes.dashboard_service.get_dashboard_config')
    @patch('app.api.v1.routes.auth_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_routes.auth_service.verify_token')
    def test_get_config_success(self, mock_verify, mock_get_user, mock_config, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_config.return_value = {
            'success': True,
            'config': {
                'id': 1,
                'department_code': 'SALES',
                'widgets': [
                    {'id': 'w1', 'type': 'total_employees', 'position': 0}
                ]
            }
        }
        resp = client.get('/api/v1/dashboard/config/SALES', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['config']['department_code'] == 'SALES'

    @patch('app.api.v1.routes.dashboard_routes.dashboard_service.get_dashboard_config')
    @patch('app.api.v1.routes.auth_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_routes.auth_service.verify_token')
    def test_get_config_null(self, mock_verify, mock_get_user, mock_config, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_config.return_value = {'success': True, 'config': None}
        resp = client.get('/api/v1/dashboard/config/NEWDEPT', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['config'] is None

    def test_get_config_no_token(self, client):
        resp = client.get('/api/v1/dashboard/config/SALES')
        assert resp.status_code == 401

    @patch('app.api.v1.routes.dashboard_routes.dashboard_service.get_dashboard_config')
    @patch('app.api.v1.routes.auth_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_routes.auth_service.verify_token')
    def test_get_config_service_failure(self, mock_verify, mock_get_user, mock_config, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_config.return_value = {'success': False, 'error': 'DB error'}
        resp = client.get('/api/v1/dashboard/config/SALES', headers=_auth_headers())
        assert resp.status_code == 500


# ==================== PUT /api/v1/dashboard/config/<dept_code> ====================

class TestSaveDashboardConfig:

    @patch('app.api.v1.routes.dashboard_routes.dashboard_service.save_dashboard_config')
    @patch('app.api.v1.routes.auth_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_routes.auth_service.verify_token')
    def test_save_config_success(self, mock_verify, mock_get_user, mock_save, client):
        _setup_manager_auth(mock_verify, mock_get_user)
        mock_save.return_value = {
            'success': True,
            'config': {
                'id': 1,
                'department_code': 'SALES',
                'widgets': [{'id': 'w1', 'type': 'total_employees', 'position': 0}]
            }
        }
        resp = client.put('/api/v1/dashboard/config/SALES',
                          data=json.dumps({
                              'widgets': [{'id': 'w1', 'type': 'total_employees', 'position': 0}]
                          }),
                          headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.api.v1.routes.auth_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_routes.auth_service.verify_token')
    def test_save_config_staff_forbidden(self, mock_verify, mock_get_user, client):
        _setup_staff_auth(mock_verify, mock_get_user)
        resp = client.put('/api/v1/dashboard/config/SALES',
                          data=json.dumps({'widgets': []}),
                          headers=_auth_headers())
        assert resp.status_code == 403

    @patch('app.api.v1.routes.auth_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_routes.auth_service.verify_token')
    def test_save_config_missing_body(self, mock_verify, mock_get_user, client):
        _setup_manager_auth(mock_verify, mock_get_user)
        resp = client.put('/api/v1/dashboard/config/SALES', headers=_auth_headers())
        assert resp.status_code == 400

    @patch('app.api.v1.routes.auth_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_routes.auth_service.verify_token')
    def test_save_config_invalid_widgets_type(self, mock_verify, mock_get_user, client):
        _setup_manager_auth(mock_verify, mock_get_user)
        resp = client.put('/api/v1/dashboard/config/SALES',
                          data=json.dumps({'widgets': 'not-a-list'}),
                          headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'widgets must be an array' in body['error']

    @patch('app.api.v1.routes.auth_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_routes.auth_service.verify_token')
    def test_save_config_invalid_widget_structure(self, mock_verify, mock_get_user, client):
        _setup_manager_auth(mock_verify, mock_get_user)
        resp = client.put('/api/v1/dashboard/config/SALES',
                          data=json.dumps({'widgets': [{'id': 'w1'}]}),
                          headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'id, type, and position' in body['error']


# ==================== GET /api/v1/dashboard/widgets/<widget_type> ====================

class TestGetWidgetData:

    @patch('app.api.v1.routes.dashboard_routes.dashboard_service.get_widget_data')
    @patch('app.api.v1.routes.auth_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_routes.auth_service.verify_token')
    def test_get_widget_data_success(self, mock_verify, mock_get_user, mock_widget, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_widget.return_value = {
            'success': True,
            'data': {'value': 42, 'label': 'Total Employees'}
        }
        resp = client.get('/api/v1/dashboard/widgets/total_employees?department=SALES',
                          headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['data']['value'] == 42

    @patch('app.api.v1.routes.auth_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_routes.auth_service.verify_token')
    def test_get_widget_data_missing_department(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.get('/api/v1/dashboard/widgets/total_employees',
                          headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'department query parameter is required' in body['error']

    @patch('app.api.v1.routes.dashboard_routes.dashboard_service.get_widget_data')
    @patch('app.api.v1.routes.auth_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_routes.auth_service.verify_token')
    def test_get_widget_data_service_error(self, mock_verify, mock_get_user, mock_widget, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_widget.return_value = {'success': False, 'error': 'Unknown widget type'}
        resp = client.get('/api/v1/dashboard/widgets/unknown?department=SALES',
                          headers=_auth_headers())
        assert resp.status_code == 400

    def test_get_widget_data_no_token(self, client):
        resp = client.get('/api/v1/dashboard/widgets/total_employees?department=SALES')
        assert resp.status_code == 401
