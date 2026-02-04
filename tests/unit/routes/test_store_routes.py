"""
Unit tests for Store API Routes
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


def _setup_admin_auth(mock_verify):
    """Configure mock so request passes token_required (store_routes version has no get_user_by_sid in decorator)."""
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': 'admin', 'type': 'access'
    }


def _setup_staff_auth(mock_verify):
    mock_verify.return_value = {
        'sid': 2, 'email': 'staff@test.com', 'role': 'staff', 'type': 'access'
    }


def _setup_hr_manager_auth(mock_verify):
    mock_verify.return_value = {
        'sid': 3, 'email': 'hr@test.com', 'role': 'manager', 'type': 'access'
    }


# ==================== GET /api/v1/stores ====================

class TestGetAllStores:

    @patch('app.api.v1.routes.store_routes.store_service.get_all_stores')
    @patch('app.api.v1.routes.store_routes.auth_service.verify_token')
    def test_get_all_stores_success(self, mock_verify, mock_get_all, client):
        _setup_admin_auth(mock_verify)
        mock_get_all.return_value = {
            'success': True,
            'stores': [
                {'id': 1, 'store_code': 'ST001', 'store_name': 'Store One', 'store_rp_sid': 'RP001'}
            ]
        }
        resp = client.get('/api/v1/stores', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['stores']) == 1

    @patch('app.api.v1.routes.store_routes.store_service.get_all_stores')
    @patch('app.api.v1.routes.store_routes.auth_service.verify_token')
    def test_get_all_stores_failure(self, mock_verify, mock_get_all, client):
        _setup_admin_auth(mock_verify)
        mock_get_all.return_value = {'success': False, 'error': 'Database error'}
        resp = client.get('/api/v1/stores', headers=_auth_headers())
        assert resp.status_code == 500
        body = resp.get_json()
        assert body['success'] is False

    def test_get_all_stores_no_token(self, client):
        resp = client.get('/api/v1/stores')
        assert resp.status_code == 401
        body = resp.get_json()
        assert body['success'] is False
        assert 'token' in body['error'].lower()


# ==================== POST /api/v1/stores ====================

class TestCreateStore:

    @patch('app.api.v1.routes.store_routes.store_service.create_store')
    @patch('app.api.v1.routes.store_routes.auth_service.verify_token')
    def test_create_store_success(self, mock_verify, mock_create, client):
        _setup_admin_auth(mock_verify)
        mock_create.return_value = {
            'success': True,
            'store': {'id': 2, 'store_code': 'ST002', 'store_name': 'Store Two'}
        }
        resp = client.post('/api/v1/stores',
                           data=json.dumps({'store_code': 'ST002', 'store_name': 'Store Two'}),
                           headers=_auth_headers())
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.api.v1.routes.store_routes.auth_service.verify_token')
    def test_create_store_missing_body(self, mock_verify, client):
        _setup_admin_auth(mock_verify)
        # Send JSON null so request.get_json() returns None, triggering the 400 path
        resp = client.post('/api/v1/stores',
                           data='null',
                           headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False

    @patch('app.api.v1.routes.store_routes.auth_service.verify_token')
    def test_create_store_missing_fields(self, mock_verify, client):
        _setup_admin_auth(mock_verify)
        resp = client.post('/api/v1/stores',
                           data=json.dumps({'store_code': 'ST002'}),
                           headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'required' in body['error'].lower()

    @patch('app.api.v1.routes.store_routes.store_service.create_store')
    @patch('app.api.v1.routes.store_routes.auth_service.verify_token')
    def test_create_store_service_error(self, mock_verify, mock_create, client):
        _setup_admin_auth(mock_verify)
        mock_create.return_value = {'success': False, 'error': 'Duplicate store code'}
        resp = client.post('/api/v1/stores',
                           data=json.dumps({'store_code': 'ST001', 'store_name': 'Dup'}),
                           headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False


# ==================== POST /api/v1/stores/init ====================

class TestInitStoresTable:

    @patch('app.api.v1.routes.store_routes.store_service.init_database')
    @patch('app.api.v1.routes.store_routes.auth_service.verify_token')
    def test_init_stores_admin_success(self, mock_verify, mock_init, client):
        _setup_admin_auth(mock_verify)
        mock_init.return_value = {'success': True, 'message': 'Stores table created'}
        resp = client.post('/api/v1/stores/init', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.api.v1.routes.store_routes.store_service.init_database')
    @patch('app.api.v1.routes.store_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.store_routes.auth_service.verify_token')
    def test_init_stores_hr_manager_success(self, mock_verify, mock_get_user, mock_init, client):
        _setup_hr_manager_auth(mock_verify)
        mock_get_user.return_value = {'department_id': 1, 'department': {'code': 'HR'}}
        mock_init.return_value = {'success': True, 'message': 'Stores table created'}
        resp = client.post('/api/v1/stores/init', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.api.v1.routes.store_routes.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.store_routes.auth_service.verify_token')
    def test_init_stores_staff_forbidden(self, mock_verify, mock_get_user, client):
        _setup_staff_auth(mock_verify)
        mock_get_user.return_value = {'department_id': 2, 'department': {'code': 'SALES'}}
        resp = client.post('/api/v1/stores/init', headers=_auth_headers())
        assert resp.status_code == 403
        body = resp.get_json()
        assert body['success'] is False

    @patch('app.api.v1.routes.store_routes.store_service.init_database')
    @patch('app.api.v1.routes.store_routes.auth_service.verify_token')
    def test_init_stores_failure(self, mock_verify, mock_init, client):
        _setup_admin_auth(mock_verify)
        mock_init.return_value = {'success': False, 'error': 'DB error'}
        resp = client.post('/api/v1/stores/init', headers=_auth_headers())
        assert resp.status_code == 500
        body = resp.get_json()
        assert body['success'] is False
