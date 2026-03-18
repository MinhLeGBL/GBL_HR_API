"""
Unit tests for permission routes (v2 flat permission model)

Tests:
- GET  /api/v1/departments
- GET  /api/v1/departments/<id>
- POST /api/v1/departments
- PUT  /api/v1/departments/<id>
- DELETE /api/v1/departments/<id>
- GET  /api/v1/permissions/me
- GET  /api/v1/sections/permissions
- PUT  /api/v1/sections/<code>/access
"""
import pytest
from unittest.mock import patch, MagicMock


def _auth_headers():
    return {'Authorization': 'Bearer testtoken'}


def _mock_auth(mock_verify, mock_get_user, role='admin', department_code='IT'):
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': role,
        'role_code': role.upper(), 'type': 'access'
    }
    mock_get_user.return_value = {
        'sid': 1, 'role': role, 'role_code': role.upper(),
        'department_code': department_code
    }


# ===========================================================================
# GET /api/v1/departments
# ===========================================================================

class TestGetDepartments:
    URL = '/api/v1/departments'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.get_all_departments')
    def test_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'departments': [{'code': 'HR'}]}

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
        assert len(resp.get_json()['departments']) == 1

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.get_all_departments')
    def test_db_error(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': False, 'error': 'DB error'}

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 500

    def test_no_auth(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 401


# ===========================================================================
# GET /api/v1/departments/<id>
# ===========================================================================

class TestGetDepartmentById:
    URL = '/api/v1/departments/400000001'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.get_department_by_id')
    def test_found(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'id': 400000001, 'code': 'HR', 'name': 'Human Resources'}

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.get_json()['department']['code'] == 'HR'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.get_department_by_id')
    def test_not_found(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = None

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 404


# ===========================================================================
# POST /api/v1/departments
# ===========================================================================

class TestCreateDepartment:
    URL = '/api/v1/departments'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.create_department')
    def test_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'department': {'code': 'MKT'}}

        resp = client.post(self.URL, json={'code': 'MKT', 'name': 'Marketing'},
                           headers=_auth_headers())

        assert resp.status_code == 201

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_fields(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.post(self.URL, json={'code': 'MKT'}, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'required' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_no_body(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.post(self.URL, headers=_auth_headers(),
                           content_type='application/json')

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_non_admin_rejected(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='staff')

        resp = client.post(self.URL, json={'code': 'MKT', 'name': 'Marketing'},
                           headers=_auth_headers())

        assert resp.status_code == 403


# ===========================================================================
# DELETE /api/v1/departments/<id>
# ===========================================================================

class TestDeleteDepartment:
    URL = '/api/v1/departments/400000001'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.delete_department')
    def test_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True, 'message': 'Deleted'}

        resp = client.delete(self.URL, headers=_auth_headers())

        assert resp.status_code == 200

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.delete_department')
    def test_has_users(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': False, 'error': 'Cannot delete department with assigned users'}

        resp = client.delete(self.URL, headers=_auth_headers())

        assert resp.status_code == 400


# ===========================================================================
# GET /api/v1/permissions/me
# ===========================================================================

class TestGetMyPermissions:
    URL = '/api/v1/permissions/me'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.get_user_permissions_v2')
    def test_returns_flat_list(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = [
            {'section_code': 'DASHBOARD', 'section_name': 'Dashboard'},
            {'section_code': 'COMMISSION_DATA', 'section_name': 'Commission Data'},
        ]

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert len(data['permissions']) == 2
        assert data['permissions'][0]['section_code'] == 'DASHBOARD'

    def test_no_auth(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 401


# ===========================================================================
# GET /api/v1/sections/permissions
# ===========================================================================

class TestGetSectionPermissions:
    URL = '/api/v1/sections/permissions'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.get_all_section_permissions')
    def test_admin_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {
            'success': True,
            'sections': [{'section_code': 'COMMISSION_DATA', 'allowed_departments': ['HR']}]
        }

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        assert len(resp.get_json()['sections']) == 1

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.get_user_permissions_v2')
    def test_non_admin_with_access_management(self, mock_perms, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')
        mock_perms.return_value = [
            {'section_code': 'ACCESS_MANAGEMENT', 'section_name': 'Access Management'}
        ]

        with patch('app.modules.permissions.routes.permission_service.get_all_section_permissions') as mock_svc:
            mock_svc.return_value = {'success': True, 'sections': []}
            resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.get_user_permissions_v2')
    def test_non_admin_without_access_denied(self, mock_perms, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='staff')
        mock_perms.return_value = [
            {'section_code': 'DASHBOARD', 'section_name': 'Dashboard'}
        ]

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 403


# ===========================================================================
# PUT /api/v1/sections/<code>/access
# ===========================================================================

class TestUpdateSectionAccess:
    URL = '/api/v1/sections/COMMISSION_DATA/access'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.update_section_access')
    def test_admin_success(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True}

        resp = client.put(self.URL, json={
            'allowed_departments': ['HR'],
            'allowed_roles': ['ADMIN'],
            'included_user_sids': [],
            'excluded_user_sids': []
        }, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_no_body(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.put(self.URL, headers=_auth_headers(),
                          content_type='application/json')

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_invalid_type_departments(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)

        resp = client.put(self.URL, json={
            'allowed_departments': 'not_a_list',
        }, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'array' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_access_management_non_admin_rejected(self, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')

        resp = client.put('/api/v1/sections/ACCESS_MANAGEMENT/access', json={
            'allowed_departments': ['IT'],
            'allowed_roles': ['ADMIN'],
        }, headers=_auth_headers())

        assert resp.status_code == 403
        assert 'admin' in resp.get_json()['error'].lower()

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.get_user_permissions_v2')
    def test_non_admin_with_access_management_can_update(self, mock_perms, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user, role='manager')
        mock_perms.return_value = [
            {'section_code': 'ACCESS_MANAGEMENT', 'section_name': 'Access Management'}
        ]

        with patch('app.modules.permissions.routes.permission_service.update_section_access') as mock_svc:
            mock_svc.return_value = {'success': True}
            resp = client.put(self.URL, json={
                'allowed_departments': ['HR'],
                'allowed_roles': ['MANAGER'],
            }, headers=_auth_headers())

        assert resp.status_code == 200

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.update_section_access')
    def test_validation_error(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': False, 'error': 'Invalid departments: FAKE'}

        resp = client.put(self.URL, json={
            'allowed_departments': ['FAKE'],
        }, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'Invalid' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.permissions.routes.permission_service.update_section_access')
    def test_departments_uppercased(self, mock_svc, mock_get_user, mock_verify, client):
        _mock_auth(mock_verify, mock_get_user)
        mock_svc.return_value = {'success': True}

        client.put(self.URL, json={
            'allowed_departments': ['hr', 'acc'],
        }, headers=_auth_headers())

        call_args = mock_svc.call_args
        assert call_args.kwargs['allowed_departments'] == ['HR', 'ACC']
