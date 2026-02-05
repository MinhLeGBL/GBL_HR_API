"""
Unit tests for Authentication API Routes
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from app.main import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    return app.test_client()


def _auth_headers():
    return {'Authorization': 'Bearer fake-token', 'Content-Type': 'application/json'}


def _json_headers():
    return {'Content-Type': 'application/json'}


# ---------- helper to set up admin auth patches ----------

def _setup_admin_auth(mock_verify, mock_get_user):
    """Configure mocks so the request passes token_required + admin_required."""
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': 'admin', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 1,
        'department': {'code': 'IT'}
    }


def _setup_staff_auth(mock_verify, mock_get_user):
    """Configure mocks so the request passes token_required but NOT admin_required."""
    mock_verify.return_value = {
        'sid': 2, 'email': 'staff@test.com', 'role': 'staff', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 2,
        'department': {'code': 'SALES'}
    }


# ==================== POST /login ====================

class TestLogin:

    @patch('app.modules.auth.routes.auth_service.authenticate')
    def test_login_success(self, mock_authenticate, client):
        mock_authenticate.return_value = {
            'success': True,
            'access_token': 'access-tok',
            'refresh_token': 'refresh-tok',
            'token_type': 'bearer',
            'user': {'sid': 1, 'email': 'user@test.com', 'full_name': 'Test', 'role': 'staff'}
        }
        resp = client.post('/api/v1/auth/login',
                           data=json.dumps({'email': 'user@test.com', 'password': 'pass123'}),
                           headers=_json_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert 'access_token' in body
        mock_authenticate.assert_called_once_with('user@test.com', 'pass123')

    def test_login_missing_body(self, client):
        # Send JSON null so request.get_json() returns None and the route's
        # "if not data" check triggers the 400 response
        resp = client.post('/api/v1/auth/login',
                           data='null',
                           content_type='application/json')
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'Request body is required' in body['error']

    def test_login_missing_fields(self, client):
        resp = client.post('/api/v1/auth/login',
                           data=json.dumps({'email': 'user@test.com'}),
                           headers=_json_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'required' in body['error'].lower()

    @patch('app.modules.auth.routes.auth_service.authenticate')
    def test_login_invalid_credentials(self, mock_authenticate, client):
        mock_authenticate.return_value = {
            'success': False,
            'error': 'Invalid email or password'
        }
        resp = client.post('/api/v1/auth/login',
                           data=json.dumps({'email': 'bad@test.com', 'password': 'wrong'}),
                           headers=_json_headers())
        assert resp.status_code == 401
        body = resp.get_json()
        assert body['success'] is False


# ==================== POST /refresh ====================

class TestRefreshToken:

    @patch('app.modules.auth.routes.auth_service.refresh_access_token')
    def test_refresh_success(self, mock_refresh, client):
        mock_refresh.return_value = {
            'success': True,
            'access_token': 'new-access-tok',
            'token_type': 'bearer'
        }
        resp = client.post('/api/v1/auth/refresh',
                           data=json.dumps({'refresh_token': 'valid-refresh'}),
                           headers=_json_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['access_token'] == 'new-access-tok'

    def test_refresh_missing_token(self, client):
        resp = client.post('/api/v1/auth/refresh',
                           data=json.dumps({}),
                           headers=_json_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'Refresh token is required' in body['error']

    @patch('app.modules.auth.routes.auth_service.refresh_access_token')
    def test_refresh_invalid_token(self, mock_refresh, client):
        mock_refresh.return_value = {
            'success': False,
            'error': 'Invalid refresh token'
        }
        resp = client.post('/api/v1/auth/refresh',
                           data=json.dumps({'refresh_token': 'expired-tok'}),
                           headers=_json_headers())
        assert resp.status_code == 401
        body = resp.get_json()
        assert body['success'] is False


# ==================== GET /me ====================

class TestGetMe:

    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_get_me_success(self, mock_verify, mock_get_user, client):
        mock_verify.return_value = {
            'sid': 1, 'email': 'me@test.com', 'role': 'admin', 'type': 'access'
        }
        mock_get_user.return_value = {
            'sid': 1,
            'email': 'me@test.com',
            'full_name': 'Test User',
            'role': 'admin',
            'department_id': 1,
            'department': {'code': 'IT'}
        }
        resp = client.get('/api/v1/auth/me', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['user']['email'] == 'me@test.com'

    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_get_me_not_found(self, mock_verify, mock_get_user, client):
        mock_verify.return_value = {
            'sid': 999, 'email': 'ghost@test.com', 'role': 'staff', 'type': 'access'
        }
        # First call from token_required decorator returns user,
        # second call from the handler returns None
        mock_get_user.side_effect = [
            {'department_id': 1, 'department': {'code': 'IT'}},  # token_required
            None  # handler
        ]
        resp = client.get('/api/v1/auth/me', headers=_auth_headers())
        assert resp.status_code == 404
        body = resp.get_json()
        assert body['success'] is False

    def test_get_me_no_token(self, client):
        resp = client.get('/api/v1/auth/me')
        assert resp.status_code == 401
        body = resp.get_json()
        assert body['success'] is False
        assert 'token' in body['error'].lower()


# ==================== POST /change-password ====================

class TestChangePassword:

    @patch('app.modules.auth.routes.auth_service.change_password')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_change_password_success(self, mock_verify, mock_get_user, mock_change, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_change.return_value = {'success': True, 'message': 'Password changed'}
        resp = client.post('/api/v1/auth/change-password',
                           data=json.dumps({'old_password': 'old123', 'new_password': 'new456'}),
                           headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_change_password_short(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.post('/api/v1/auth/change-password',
                           data=json.dumps({'old_password': 'old123', 'new_password': 'ab'}),
                           headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'at least 6 characters' in body['error']

    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_change_password_missing_fields(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.post('/api/v1/auth/change-password',
                           data=json.dumps({'old_password': 'old123'}),
                           headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False


# ==================== POST /register ====================

class TestRegister:

    @patch('app.modules.auth.routes.auth_service.create_user')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_register_success(self, mock_verify, mock_get_user, mock_create, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_create.return_value = {
            'success': True,
            'user': {'sid': 100000002, 'email': 'new@test.com', 'full_name': 'New User', 'role': 'staff'}
        }
        resp = client.post('/api/v1/auth/register',
                           data=json.dumps({
                               'email': 'new@test.com',
                               'password': 'pass123',
                               'full_name': 'New User'
                           }),
                           headers=_auth_headers())
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['success'] is True
        assert body['user']['email'] == 'new@test.com'

    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_register_missing_fields(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.post('/api/v1/auth/register',
                           data=json.dumps({'email': 'new@test.com'}),
                           headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False

    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_register_non_admin_forbidden(self, mock_verify, mock_get_user, client):
        _setup_staff_auth(mock_verify, mock_get_user)
        resp = client.post('/api/v1/auth/register',
                           data=json.dumps({
                               'email': 'new@test.com',
                               'password': 'pass123',
                               'full_name': 'New User'
                           }),
                           headers=_auth_headers())
        assert resp.status_code == 403
        body = resp.get_json()
        assert body['success'] is False


# ==================== GET /users ====================

class TestGetAllUsers:

    @patch('app.modules.auth.routes.auth_service.get_all_users')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_get_all_users_success(self, mock_verify, mock_get_user, mock_all, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_all.return_value = {
            'success': True,
            'users': [{'sid': 1, 'email': 'a@test.com'}]
        }
        resp = client.get('/api/v1/auth/users', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert isinstance(body['users'], list)


# ==================== GET /roles ====================

class TestGetRoles:

    @patch('app.modules.auth.routes.auth_service.get_roles')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_get_roles_success(self, mock_verify, mock_get_user, mock_roles, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_roles.return_value = {
            'success': True,
            'roles': [
                {'id': 30001, 'code': 'ADMIN', 'name': 'Admin', 'is_active': True},
                {'id': 30002, 'code': 'MANAGER', 'name': 'Manager', 'is_active': True},
                {'id': 30003, 'code': 'STAFF', 'name': 'Staff', 'is_active': True}
            ]
        }
        resp = client.get('/api/v1/auth/roles', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['roles']) == 3
