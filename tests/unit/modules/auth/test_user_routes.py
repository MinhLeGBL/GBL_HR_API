"""
Unit tests for user management routes (user_routes.py)

Tests:
- GET    /api/v1/users
- GET    /api/v1/users/:sid
- POST   /api/v1/users
- PUT    /api/v1/users/:sid
- POST   /api/v1/users/:sid/reset-password
- DELETE /api/v1/users/:sid
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
# GET /api/v1/users
# ===========================================================================

class TestGetAllUsers:
    URL = '/api/v1/users'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.get_all_users')
    def test_admin_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'users': [{'sid': 1, 'email': 'a@b.com'}]}

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
        assert len(resp.get_json()['users']) == 1

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.get_all_users')
    def test_it_manager_allowed(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager', department_code='IT')
        mock_svc.return_value = {'success': True, 'users': []}

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_non_it_manager_rejected(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager', department_code='HR')

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 403

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_staff_rejected(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='staff')

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 403

    def test_no_auth(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 401


# ===========================================================================
# GET /api/v1/users/:sid
# ===========================================================================

class TestGetUser:
    URL = '/api/v1/users/100000001'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_found(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        # get_user_by_sid is called by both middleware and the route
        # middleware call returns auth user, route call returns target user
        mock_get_user.side_effect = [
            {'sid': 1, 'role': 'admin', 'role_code': 'ADMIN', 'department_code': 'IT', 'department': {'id': 400000001, 'code': 'IT', 'name': 'IT'}},
            {'sid': 100000001, 'email': 'user@test.com', 'role': 'staff'},
        ]

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.get_json()['user']['sid'] == 100000001

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_not_found(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        # middleware returns auth user, route returns None
        mock_get_user.side_effect = [
            {'sid': 1, 'role': 'admin', 'role_code': 'ADMIN', 'department_code': 'IT', 'department': {'id': 400000001, 'code': 'IT', 'name': 'IT'}},
            None,
        ]

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 404


# ===========================================================================
# POST /api/v1/users
# ===========================================================================

class TestCreateUser:
    URL = '/api/v1/users'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.create_user')
    def test_admin_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'user': {'sid': 100000002, 'email': 'new@test.com'}}

        resp = client.post(self.URL, json={
            'email': 'new@test.com',
            'full_name': 'New User',
            'password': '123456',
            'role': 'staff'
        }, headers=_auth_headers())

        assert resp.status_code == 201
        assert resp.get_json()['success'] is True

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_required_field(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.post(self.URL, json={
            'email': 'new@test.com',
            'full_name': 'New User',
        }, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'Missing required field' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_no_body(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.post(self.URL, headers=_auth_headers(),
                           content_type='application/json')

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_staff_rejected(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='staff')

        resp = client.post(self.URL, json={
            'email': 'new@test.com', 'full_name': 'New', 'password': '123', 'role': 'staff'
        }, headers=_auth_headers())

        assert resp.status_code == 403

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_manager_cannot_create_admin(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')

        resp = client.post(self.URL, json={
            'email': 'new@test.com', 'full_name': 'New', 'password': '123', 'role': 'admin'
        }, headers=_auth_headers())

        assert resp.status_code == 403
        assert 'admin' in resp.get_json()['error'].lower()


# ===========================================================================
# PUT /api/v1/users/:sid
# ===========================================================================

class TestUpdateUser:
    URL = '/api/v1/users/100000001'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.update_user')
    def test_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'user': {'sid': 100000001}}

        resp = client.put(self.URL, json={'role': 'manager'}, headers=_auth_headers())

        assert resp.status_code == 200

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_no_body(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.put(self.URL, headers=_auth_headers(),
                          content_type='application/json')

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_no_valid_fields(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.put(self.URL, json={'invalid_field': 'value'}, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'No valid fields' in resp.get_json()['error']


# ===========================================================================
# POST /api/v1/users/:sid/reset-password
# ===========================================================================

class TestResetPassword:
    URL = '/api/v1/users/100000002/reset-password'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.reset_password')
    def test_admin_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True}

        resp = client.post(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.reset_password')
    def test_manager_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')
        mock_svc.return_value = {'success': True}

        resp = client.post(self.URL, headers=_auth_headers())

        assert resp.status_code == 200

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_staff_rejected(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='staff')

        resp = client.post(self.URL, headers=_auth_headers())

        assert resp.status_code == 403

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.reset_password')
    def test_user_not_found(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': False, 'error': 'User not found'}

        resp = client.post(self.URL, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'not found' in resp.get_json()['error'].lower()

    def test_no_auth(self, client):
        resp = client.post(self.URL)
        assert resp.status_code == 401


# ===========================================================================
# DELETE /api/v1/users/:sid
# ===========================================================================

class TestDeleteUser:
    URL = '/api/v1/users/100000002'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.delete_user')
    def test_admin_success(self, mock_delete, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        # middleware returns auth user, route get_user_by_sid returns target
        mock_get_user.side_effect = [
            {'sid': 1, 'role': 'admin', 'role_code': 'ADMIN', 'department_code': 'IT', 'department': {'id': 400000001, 'code': 'IT', 'name': 'IT'}},
            {'sid': 100000002, 'role': 'staff'},
        ]
        mock_delete.return_value = {'success': True, 'message': 'User deleted'}

        resp = client.delete(self.URL, headers=_auth_headers())

        assert resp.status_code == 200

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_cannot_delete_self(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        # Try to delete self (sid=1)
        resp = client.delete('/api/v1/users/1', headers=_auth_headers())

        assert resp.status_code == 400
        assert 'yourself' in resp.get_json()['error'].lower()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_manager_cannot_delete_admin(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')
        mock_get_user.side_effect = [
            {'sid': 1, 'role': 'manager', 'role_code': 'MANAGER', 'department_code': 'IT', 'department': {'id': 400000001, 'code': 'IT', 'name': 'IT'}},
            {'sid': 100000002, 'role': 'admin'},
        ]

        resp = client.delete(self.URL, headers=_auth_headers())

        assert resp.status_code == 403

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_staff_rejected(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='staff')
        mock_get_user.side_effect = [
            {'sid': 1, 'role': 'staff', 'role_code': 'STAFF', 'department_code': 'HR', 'department': {'id': 400000002, 'code': 'HR', 'name': 'HR'}},
            {'sid': 100000002, 'role': 'staff'},
        ]

        resp = client.delete(self.URL, headers=_auth_headers())

        assert resp.status_code == 403


# ===========================================================================
# The single-admin invariant
#
# Admin bypasses every permission check (PermissionService resolution rule 1),
# so granting the role through the API grants everything. There is exactly one
# admin account and it is changed by a direct database operation — see the
# module docstring in app/modules/auth/user_routes.py.
# ===========================================================================

class TestAdminRoleNotAssignable:
    """`role: admin` is refused on create and update, for every caller."""

    CREATE_URL = '/api/v1/users'
    UPDATE_URL = '/api/v1/users/100000010'

    def _body(self, role):
        return {
            'email': 'new@test.com', 'full_name': 'New User',
            'password': 'secret123', 'role': role,
        }

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.create_user')
    def test_create_admin_refused_even_for_an_admin(
            self, mock_create, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='admin')
        res = client.post(self.CREATE_URL, json=self._body('admin'),
                          headers=_auth_headers())
        assert res.status_code == 403
        assert 'cannot be assigned' in res.get_json()['error']
        # Refused before it reaches the service.
        mock_create.assert_not_called()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.create_user')
    def test_create_admin_refused_case_insensitively(
            self, mock_create, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='admin')
        for value in ('Admin', 'ADMIN', ' admin '):
            res = client.post(self.CREATE_URL, json=self._body(value),
                              headers=_auth_headers())
            assert res.status_code == 403, value
        mock_create.assert_not_called()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.create_user')
    def test_create_manager_still_allowed(
            self, mock_create, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='admin')
        mock_create.return_value = {'success': True, 'user': {'sid': 2}}
        res = client.post(self.CREATE_URL, json=self._body('manager'),
                          headers=_auth_headers())
        assert res.status_code == 201

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.update_user')
    def test_promotion_to_admin_refused(
            self, mock_update, mock_get_user, mock_verify, client):
        """The escalation path that used to exist: an IT manager promoting
        themselves or anyone else to admin."""
        _mock_auth(mock_verify, mock_get_user, role='manager', department_code='IT')
        res = client.put(self.UPDATE_URL, json={'role': 'admin'},
                         headers=_auth_headers())
        assert res.status_code == 403
        assert 'cannot be assigned' in res.get_json()['error']
        mock_update.assert_not_called()


class TestAdminRowProtected:
    """The admin row cannot be stripped of its powers through the API: with the
    role ungrantable, any of these would leave zero admins for good."""

    URL = '/api/v1/users/100000001'


    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes._target_is_admin')
    @patch('app.modules.auth.user_routes.auth_service.update_user')
    def test_admin_cannot_be_demoted(
            self, mock_update, mock_lookup, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='admin')
        mock_lookup.return_value = True
        res = client.put(self.URL, json={'role': 'manager'}, headers=_auth_headers())
        assert res.status_code == 403
        assert 'role cannot be changed' in res.get_json()['error']
        mock_update.assert_not_called()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes._target_is_admin')
    @patch('app.modules.auth.user_routes.auth_service.update_user')
    def test_admin_cannot_be_deactivated(
            self, mock_update, mock_lookup, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='admin')
        mock_lookup.return_value = True
        res = client.put(self.URL, json={'is_active': False}, headers=_auth_headers())
        assert res.status_code == 403
        assert 'cannot be deactivated' in res.get_json()['error']
        mock_update.assert_not_called()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes._target_is_admin')
    @patch('app.modules.auth.user_routes.auth_service.update_user')
    def test_admin_may_still_be_reactivated(
            self, mock_update, mock_lookup, mock_get_user, mock_verify, client):
        """Only deactivation is blocked — is_active=True must stay possible."""
        _mock_auth(mock_verify, mock_get_user, role='admin')
        mock_lookup.return_value = True
        mock_update.return_value = {'success': True, 'user': {'sid': 100000001}}
        res = client.put(self.URL, json={'is_active': True}, headers=_auth_headers())
        assert res.status_code == 200

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes._target_is_admin')
    @patch('app.modules.auth.user_routes.auth_service.update_user')
    def test_admin_department_may_still_change(
            self, mock_update, mock_lookup, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='admin')
        mock_lookup.return_value = True
        mock_update.return_value = {'success': True, 'user': {'sid': 100000001}}
        res = client.put(self.URL, json={'department_id': 400000003},
                         headers=_auth_headers())
        assert res.status_code == 200

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes._target_is_admin')
    @patch('app.modules.auth.user_routes.auth_service.delete_user')
    def test_admin_cannot_be_deleted_by_someone_else(
            self, mock_delete, mock_lookup, mock_get_user, mock_verify, client):
        """The self-delete check only stops the admin deleting itself. This is
        anyone ELSE deleting it, which the self-check never covered."""
        mock_verify.return_value = {
            'sid': 100000010, 'email': 'it@test.com', 'role': 'manager',
            'role_code': 'MANAGER', 'type': 'access',
        }
        mock_get_user.return_value = {
            'sid': 100000010, 'role': 'manager', 'role_code': 'MANAGER',
            'department_id': 400000003, 'department_code': 'IT',
            'department': {'id': 400000003, 'code': 'IT', 'name': 'IT'},
        }
        mock_lookup.return_value = True
        res = client.delete(self.URL, headers=_auth_headers())
        assert res.status_code == 403
        assert 'cannot be deleted' in res.get_json()['error']
        mock_delete.assert_not_called()


class TestUserCreationGate:
    """Creating a user assigns a role, so it needs the same authority as
    changing one: admin, or a manager in IT. It used to accept ANY manager."""

    URL = '/api/v1/users'
    BODY = {'email': 'n@test.com', 'full_name': 'N', 'password': 'secret123',
            'role': 'manager'}

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.create_user')
    def test_non_it_manager_refused(
            self, mock_create, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager', department_code='ACC')
        res = client.post(self.URL, json=self.BODY, headers=_auth_headers())
        assert res.status_code == 403
        mock_create.assert_not_called()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.create_user')
    def test_it_manager_allowed(
            self, mock_create, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager', department_code='IT')
        mock_create.return_value = {'success': True, 'user': {'sid': 2}}
        res = client.post(self.URL, json=self.BODY, headers=_auth_headers())
        assert res.status_code == 201

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.auth.user_routes.auth_service.create_user')
    def test_staff_refused(self, mock_create, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='staff', department_code='IT')
        res = client.post(self.URL, json=self.BODY, headers=_auth_headers())
        assert res.status_code == 403
        mock_create.assert_not_called()
