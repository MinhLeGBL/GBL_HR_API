"""
Unit tests for Permission and Department API Routes

NOTE: permission_routes imports token_required and admin_required from auth_routes,
so the auth_service instance to patch is on auth_routes, NOT permission_routes.
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
    """Admin auth -- patches on auth_routes (where token_required/admin_required live)."""
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': 'admin', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 1,
        'department': {'code': 'IT'}
    }


def _setup_staff_auth(mock_verify, mock_get_user):
    mock_verify.return_value = {
        'sid': 2, 'email': 'staff@test.com', 'role': 'staff', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 2,
        'department': {'code': 'SALES'}
    }


# ==================== GET /api/v1/departments ====================

class TestGetDepartments:

    @patch('app.modules.permissions.routes.permission_service.get_all_departments')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_get_departments_success(self, mock_verify, mock_get_user, mock_depts, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_depts.return_value = {
            'success': True,
            'departments': [
                {'id': 1, 'code': 'HR', 'name': 'Human Resources'}
            ]
        }
        resp = client.get('/api/v1/departments', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['departments']) == 1

    def test_get_departments_no_token(self, client):
        resp = client.get('/api/v1/departments')
        assert resp.status_code == 401

    @patch('app.modules.permissions.routes.permission_service.get_all_departments')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_get_departments_failure(self, mock_verify, mock_get_user, mock_depts, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_depts.return_value = {'success': False, 'error': 'DB error'}
        resp = client.get('/api/v1/departments', headers=_auth_headers())
        assert resp.status_code == 500


# ==================== GET /api/v1/departments/<dept_id> ====================

class TestGetDepartmentById:

    @patch('app.modules.permissions.routes.permission_service.get_department_by_id')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_get_department_success(self, mock_verify, mock_get_user, mock_dept, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_dept.return_value = {'id': 1, 'code': 'HR', 'name': 'Human Resources'}
        resp = client.get('/api/v1/departments/1', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['department']['code'] == 'HR'

    @patch('app.modules.permissions.routes.permission_service.get_department_by_id')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_get_department_not_found(self, mock_verify, mock_get_user, mock_dept, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_dept.return_value = None
        resp = client.get('/api/v1/departments/999', headers=_auth_headers())
        assert resp.status_code == 404
        body = resp.get_json()
        assert body['success'] is False


# ==================== POST /api/v1/departments ====================

class TestCreateDepartment:

    @patch('app.modules.permissions.routes.permission_service.create_department')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_create_department_success(self, mock_verify, mock_get_user, mock_create, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_create.return_value = {
            'success': True,
            'department': {'id': 5, 'code': 'MARKETING', 'name': 'Marketing'}
        }
        resp = client.post('/api/v1/departments',
                           data=json.dumps({'code': 'MARKETING', 'name': 'Marketing'}),
                           headers=_auth_headers())
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_create_department_missing_fields(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.post('/api/v1/departments',
                           data=json.dumps({'code': 'MARKETING'}),
                           headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'required' in body['error'].lower()

    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_create_department_non_admin_forbidden(self, mock_verify, mock_get_user, client):
        _setup_staff_auth(mock_verify, mock_get_user)
        resp = client.post('/api/v1/departments',
                           data=json.dumps({'code': 'MARKETING', 'name': 'Marketing'}),
                           headers=_auth_headers())
        assert resp.status_code == 403


# ==================== PUT /api/v1/departments/<dept_id> ====================

class TestUpdateDepartment:

    @patch('app.modules.permissions.routes.permission_service.update_department')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_update_department_success(self, mock_verify, mock_get_user, mock_update, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_update.return_value = {
            'success': True,
            'department': {'id': 1, 'code': 'HR', 'name': 'HR Updated'}
        }
        resp = client.put('/api/v1/departments/1',
                          data=json.dumps({'name': 'HR Updated'}),
                          headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_update_department_missing_body(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.put('/api/v1/departments/1', headers=_auth_headers())
        assert resp.status_code == 400


# ==================== DELETE /api/v1/departments/<dept_id> ====================

class TestDeleteDepartment:

    @patch('app.modules.permissions.routes.permission_service.delete_department')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_delete_department_success(self, mock_verify, mock_get_user, mock_delete, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_delete.return_value = {'success': True, 'message': 'Department deleted'}
        resp = client.delete('/api/v1/departments/1', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.modules.permissions.routes.permission_service.delete_department')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_delete_department_failure(self, mock_verify, mock_get_user, mock_delete, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_delete.return_value = {'success': False, 'error': 'Department has assigned users'}
        resp = client.delete('/api/v1/departments/1', headers=_auth_headers())
        assert resp.status_code == 400


# ==================== GET /api/v1/sections ====================

class TestGetSections:

    @patch('app.modules.permissions.routes.permission_service.get_all_sections')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_get_sections_success(self, mock_verify, mock_get_user, mock_sections, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_sections.return_value = {
            'success': True,
            'sections': [
                {'id': 1, 'code': 'DASHBOARD', 'name': 'Dashboard', 'allowed_roles': ['admin']}
            ]
        }
        resp = client.get('/api/v1/sections', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_get_sections_non_admin_forbidden(self, mock_verify, mock_get_user, client):
        _setup_staff_auth(mock_verify, mock_get_user)
        resp = client.get('/api/v1/sections', headers=_auth_headers())
        assert resp.status_code == 403


# ==================== PUT /api/v1/sections/<code>/roles ====================

class TestUpdateSectionRoles:

    @patch('app.modules.permissions.routes.permission_service.update_section_roles')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_update_section_roles_success(self, mock_verify, mock_get_user, mock_update, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_update.return_value = {'success': True, 'message': 'Section roles updated successfully'}
        resp = client.put('/api/v1/sections/dashboard/roles',
                          data=json.dumps({'roles': ['admin', 'manager']}),
                          headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_update_section_roles_missing_roles(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.put('/api/v1/sections/dashboard/roles',
                          data=json.dumps({'not_roles': []}),
                          headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'roles must be an array' in body['error']

    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_update_section_roles_invalid_type(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.put('/api/v1/sections/dashboard/roles',
                          data=json.dumps({'roles': 'not-a-list'}),
                          headers=_auth_headers())
        assert resp.status_code == 400


# ==================== GET /api/v1/permissions/me ====================

class TestGetMyPermissions:

    @patch('app.modules.permissions.routes.permission_service.get_user_permissions')
    @patch('app.modules.auth.routes.auth_service.get_user_by_sid')
    @patch('app.modules.auth.routes.auth_service.verify_token')
    def test_get_my_permissions_success(self, mock_verify, mock_get_user, mock_perms, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_perms.return_value = [
            {
                'group_code': 'MAIN',
                'group_name': 'Main',
                'sections': [
                    {'section_code': 'DASHBOARD', 'section_name': 'Dashboard'}
                ]
            }
        ]
        resp = client.get('/api/v1/permissions/me', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert isinstance(body['permissions'], list)

    def test_get_my_permissions_no_token(self, client):
        resp = client.get('/api/v1/permissions/me')
        assert resp.status_code == 401
