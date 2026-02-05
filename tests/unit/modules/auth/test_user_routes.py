"""
Unit tests for User Management API Routes

user_routes has its own token_required and admin_or_it_manager_required decorators,
so the auth_service to patch is at app.modules.auth.user_routes.auth_service.
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
    """Admin auth - passes admin_or_it_manager_required."""
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': 'admin', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 1,
        'department': {'code': 'IT'}
    }


def _setup_it_manager_auth(mock_verify, mock_get_user):
    """IT manager auth - passes admin_or_it_manager_required."""
    mock_verify.return_value = {
        'sid': 3, 'email': 'it@test.com', 'role': 'manager', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 1,
        'department': {'code': 'IT'}
    }


def _setup_staff_auth(mock_verify, mock_get_user):
    """Staff auth - fails admin_or_it_manager_required."""
    mock_verify.return_value = {
        'sid': 2, 'email': 'staff@test.com', 'role': 'staff', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 2,
        'department': {'code': 'SALES'}
    }


def _setup_manager_auth(mock_verify, mock_get_user):
    """Non-IT manager auth - passes token_required but NOT admin_or_it_manager_required."""
    mock_verify.return_value = {
        'sid': 4, 'email': 'mgr@test.com', 'role': 'manager', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 2,
        'department': {'code': 'SALES'}
    }


# ==================== GET /api/v1/users ====================

class TestGetAllUsers:

    @patch('app.modules.auth.user_routes.auth_service.get_all_users')
    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_get_all_users_admin_success(self, mock_verify, mock_get_user, mock_all, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_all.return_value = {
            'success': True,
            'users': [{'sid': 100000001, 'email': 'john@test.com', 'role': 'manager'}]
        }
        resp = client.get('/api/v1/users', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert isinstance(body['users'], list)

    @patch('app.modules.auth.user_routes.auth_service.get_all_users')
    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_get_all_users_it_manager_success(self, mock_verify, mock_get_user, mock_all, client):
        _setup_it_manager_auth(mock_verify, mock_get_user)
        mock_all.return_value = {'success': True, 'users': []}
        resp = client.get('/api/v1/users', headers=_auth_headers())
        assert resp.status_code == 200

    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_get_all_users_staff_forbidden(self, mock_verify, mock_get_user, client):
        _setup_staff_auth(mock_verify, mock_get_user)
        resp = client.get('/api/v1/users', headers=_auth_headers())
        assert resp.status_code == 403

    def test_get_all_users_no_token(self, client):
        resp = client.get('/api/v1/users')
        assert resp.status_code == 401


# ==================== GET /api/v1/users/<sid> ====================

class TestGetUser:

    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_get_user_success(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        # Third call is the handler's own call to get_user_by_sid(sid)
        mock_get_user.side_effect = [
            # token_required decorator call
            {'department_id': 1, 'department': {'code': 'IT'}},
            # handler call with route param sid=100000002
            {'sid': 100000002, 'email': 'target@test.com', 'role': 'staff',
             'department_id': 2, 'department': {'code': 'SALES'}}
        ]
        resp = client.get('/api/v1/users/100000002', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['user']['sid'] == 100000002

    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_get_user_not_found(self, mock_verify, mock_get_user, client):
        mock_verify.return_value = {
            'sid': 1, 'email': 'admin@test.com', 'role': 'admin', 'type': 'access'
        }
        mock_get_user.side_effect = [
            {'department_id': 1, 'department': {'code': 'IT'}},  # token_required
            None  # handler: user not found
        ]
        resp = client.get('/api/v1/users/999999999', headers=_auth_headers())
        assert resp.status_code == 404


# ==================== POST /api/v1/users ====================

class TestCreateUser:

    @patch('app.modules.auth.user_routes.auth_service.create_user')
    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_create_user_admin_success(self, mock_verify, mock_get_user, mock_create, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_create.return_value = {
            'success': True,
            'user': {'sid': 100000010, 'email': 'new@test.com', 'role': 'staff'}
        }
        resp = client.post('/api/v1/users',
                           data=json.dumps({
                               'email': 'new@test.com',
                               'full_name': 'New User',
                               'password': 'pass123',
                               'role': 'staff'
                           }),
                           headers=_auth_headers())
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_create_user_staff_forbidden(self, mock_verify, mock_get_user, client):
        _setup_staff_auth(mock_verify, mock_get_user)
        resp = client.post('/api/v1/users',
                           data=json.dumps({
                               'email': 'new@test.com',
                               'full_name': 'New',
                               'password': 'pass123',
                               'role': 'staff'
                           }),
                           headers=_auth_headers())
        assert resp.status_code == 403

    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_create_user_missing_fields(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.post('/api/v1/users',
                           data=json.dumps({'email': 'new@test.com'}),
                           headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'Missing required field' in body['error']

    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_create_admin_user_by_non_admin_forbidden(self, mock_verify, mock_get_user, client):
        _setup_manager_auth(mock_verify, mock_get_user)
        resp = client.post('/api/v1/users',
                           data=json.dumps({
                               'email': 'new@test.com',
                               'full_name': 'Admin',
                               'password': 'pass123',
                               'role': 'admin'
                           }),
                           headers=_auth_headers())
        assert resp.status_code == 403
        body = resp.get_json()
        assert 'Only admin can create admin users' in body['error']


# ==================== PUT /api/v1/users/<sid> ====================

class TestUpdateUser:

    @patch('app.modules.auth.user_routes.auth_service.update_user')
    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_update_user_success(self, mock_verify, mock_get_user, mock_update, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_update.return_value = {
            'success': True,
            'user': {'sid': 100000002, 'role': 'manager'}
        }
        resp = client.put('/api/v1/users/100000002',
                          data=json.dumps({'role': 'manager'}),
                          headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_update_user_no_valid_fields(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.put('/api/v1/users/100000002',
                          data=json.dumps({'invalid_field': 'value'}),
                          headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'No valid fields' in body['error']

    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_update_user_missing_body(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.put('/api/v1/users/100000002', headers=_auth_headers())
        assert resp.status_code == 400


# ==================== DELETE /api/v1/users/<sid> ====================

class TestDeleteUser:

    @patch('app.modules.auth.user_routes.auth_service.delete_user')
    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_delete_user_admin_success(self, mock_verify, mock_get_user, mock_delete, client):
        mock_verify.return_value = {
            'sid': 1, 'email': 'admin@test.com', 'role': 'admin', 'type': 'access'
        }
        mock_get_user.side_effect = [
            # token_required decorator
            {'department_id': 1, 'department': {'code': 'IT'}},
            # handler: target user lookup
            {'sid': 100000002, 'role': 'staff'}
        ]
        mock_delete.return_value = {'success': True}
        resp = client.delete('/api/v1/users/100000002', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_delete_user_self_forbidden(self, mock_verify, mock_get_user, client):
        mock_verify.return_value = {
            'sid': 1, 'email': 'admin@test.com', 'role': 'admin', 'type': 'access'
        }
        mock_get_user.return_value = {'department_id': 1, 'department': {'code': 'IT'}}
        resp = client.delete('/api/v1/users/1', headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'Cannot delete yourself' in body['error']

    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_delete_user_not_found(self, mock_verify, mock_get_user, client):
        mock_verify.return_value = {
            'sid': 1, 'email': 'admin@test.com', 'role': 'admin', 'type': 'access'
        }
        mock_get_user.side_effect = [
            {'department_id': 1, 'department': {'code': 'IT'}},  # token_required
            None  # handler: target not found
        ]
        resp = client.delete('/api/v1/users/999999999', headers=_auth_headers())
        assert resp.status_code == 404

    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_delete_admin_by_non_admin_forbidden(self, mock_verify, mock_get_user, client):
        mock_verify.return_value = {
            'sid': 4, 'email': 'mgr@test.com', 'role': 'manager', 'type': 'access'
        }
        mock_get_user.side_effect = [
            {'department_id': 2, 'department': {'code': 'SALES'}},  # token_required
            {'sid': 1, 'role': 'admin'}  # handler: target is admin
        ]
        resp = client.delete('/api/v1/users/1', headers=_auth_headers())
        assert resp.status_code == 403
        body = resp.get_json()
        assert 'Only admin can delete admin users' in body['error']

    @patch('app.modules.auth.user_routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.verify_token')
    def test_delete_user_by_staff_forbidden(self, mock_verify, mock_get_user, client):
        mock_verify.return_value = {
            'sid': 2, 'email': 'staff@test.com', 'role': 'staff', 'type': 'access'
        }
        mock_get_user.side_effect = [
            {'department_id': 2, 'department': {'code': 'SALES'}},  # token_required
            {'sid': 100000002, 'role': 'staff'}  # handler: target user
        ]
        resp = client.delete('/api/v1/users/100000002', headers=_auth_headers())
        assert resp.status_code == 403
