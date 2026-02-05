"""
Unit tests for HR Employee API Routes

hr_employee_routes imports decorators from auth_middleware, so the auth_service
instance to patch is on auth_middleware, NOT hr_employee_routes.
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
    """Configure mocks for admin auth (passes admin_or_hr_it_manager_required)."""
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': 'admin', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 1,
        'department': {'code': 'IT'}
    }


def _setup_hr_manager_auth(mock_verify, mock_get_user):
    """Configure mocks for HR manager auth (passes admin_or_hr_it_manager_required)."""
    mock_verify.return_value = {
        'sid': 3, 'email': 'hr@test.com', 'role': 'manager', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 1,
        'department': {'code': 'HR'}
    }


def _setup_staff_auth(mock_verify, mock_get_user):
    """Configure mocks for staff auth (fails admin_or_hr_it_manager_required)."""
    mock_verify.return_value = {
        'sid': 2, 'email': 'staff@test.com', 'role': 'staff', 'type': 'access'
    }
    mock_get_user.return_value = {
        'department_id': 2,
        'department': {'code': 'SALES'}
    }


# ==================== GET /api/v1/employees ====================

class TestGetAllEmployees:

    @patch('app.api.v1.routes.hr_employee_routes.hr_employee_service.get_all_employees')
    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_get_all_employees_success(self, mock_verify, mock_get_user, mock_get_all, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_get_all.return_value = {
            'success': True,
            'employees': [
                {'sid': 300000001, 'employee_code': 'EMP001', 'full_name': 'Nguyen Van A'}
            ]
        }
        resp = client.get('/api/v1/employees', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['employees']) == 1

    @patch('app.api.v1.routes.hr_employee_routes.hr_employee_service.get_all_employees')
    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_get_all_employees_with_filters(self, mock_verify, mock_get_user, mock_get_all, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_get_all.return_value = {'success': True, 'employees': []}
        resp = client.get('/api/v1/employees?department_code=HR&role=staff', headers=_auth_headers())
        assert resp.status_code == 200
        mock_get_all.assert_called_once_with(department_code='HR', role='staff')

    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_get_all_employees_staff_forbidden(self, mock_verify, mock_get_user, client):
        _setup_staff_auth(mock_verify, mock_get_user)
        resp = client.get('/api/v1/employees', headers=_auth_headers())
        assert resp.status_code == 403
        body = resp.get_json()
        assert body['success'] is False

    def test_get_all_employees_no_token(self, client):
        resp = client.get('/api/v1/employees')
        assert resp.status_code == 401


# ==================== GET /api/v1/employees/<sid> ====================

class TestGetEmployeeBySid:

    @patch('app.api.v1.routes.hr_employee_routes.hr_employee_service.get_employee_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_get_employee_success(self, mock_verify, mock_get_user, mock_get_emp, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_get_emp.return_value = {
            'success': True,
            'employee': {'sid': 300000001, 'employee_code': 'EMP001', 'full_name': 'Test'}
        }
        resp = client.get('/api/v1/employees/300000001', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['employee']['sid'] == 300000001

    @patch('app.api.v1.routes.hr_employee_routes.hr_employee_service.get_employee_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_get_employee_not_found(self, mock_verify, mock_get_user, mock_get_emp, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_get_emp.return_value = {'success': False, 'error': 'Employee not found'}
        resp = client.get('/api/v1/employees/999999999', headers=_auth_headers())
        assert resp.status_code == 404
        body = resp.get_json()
        assert body['success'] is False


# ==================== POST /api/v1/employees ====================

class TestCreateEmployee:

    def _valid_employee_data(self):
        return {
            'employee_code': 'EMP010',
            'full_name': 'New Employee',
            'employee_type': 'office',
            'contract': 'permanent',
            'department_id': 1,
            'join_date': '2024-01-15'
        }

    @patch('app.api.v1.routes.hr_employee_routes.hr_employee_service.create_employee')
    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_create_employee_success(self, mock_verify, mock_get_user, mock_create, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_create.return_value = {
            'success': True,
            'employee': {'sid': 300000010, 'employee_code': 'EMP010'}
        }
        resp = client.post('/api/v1/employees',
                           data=json.dumps(self._valid_employee_data()),
                           headers=_auth_headers())
        assert resp.status_code == 201
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_create_employee_missing_body(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.post('/api/v1/employees', headers=_auth_headers())
        assert resp.status_code == 400

    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_create_employee_missing_required_field(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        data = {'employee_code': 'EMP010', 'full_name': 'Test'}
        resp = client.post('/api/v1/employees',
                           data=json.dumps(data),
                           headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'Missing required field' in body['error']


# ==================== PUT /api/v1/employees/<sid> ====================

class TestUpdateEmployee:

    @patch('app.api.v1.routes.hr_employee_routes.hr_employee_service.update_employee')
    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_update_employee_success(self, mock_verify, mock_get_user, mock_update, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_update.return_value = {
            'success': True,
            'employee': {'sid': 300000001, 'full_name': 'Updated Name'}
        }
        resp = client.put('/api/v1/employees/300000001',
                          data=json.dumps({'full_name': 'Updated Name'}),
                          headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_update_employee_no_valid_fields(self, mock_verify, mock_get_user, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        resp = client.put('/api/v1/employees/300000001',
                          data=json.dumps({'invalid_field': 'value'}),
                          headers=_auth_headers())
        assert resp.status_code == 400
        body = resp.get_json()
        assert 'No valid fields' in body['error']

    @patch('app.api.v1.routes.hr_employee_routes.hr_employee_service.update_employee')
    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_update_employee_not_found(self, mock_verify, mock_get_user, mock_update, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_update.return_value = {'success': False, 'error': 'Employee not found'}
        resp = client.put('/api/v1/employees/999999999',
                          data=json.dumps({'full_name': 'X'}),
                          headers=_auth_headers())
        assert resp.status_code == 404


# ==================== DELETE /api/v1/employees/<sid> ====================

class TestDeleteEmployee:

    @patch('app.api.v1.routes.hr_employee_routes.hr_employee_service.delete_employee')
    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_delete_employee_success(self, mock_verify, mock_get_user, mock_delete, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_delete.return_value = {'success': True}
        resp = client.delete('/api/v1/employees/300000001', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.api.v1.routes.hr_employee_routes.hr_employee_service.delete_employee')
    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_delete_employee_not_found(self, mock_verify, mock_get_user, mock_delete, client):
        _setup_admin_auth(mock_verify, mock_get_user)
        mock_delete.return_value = {'success': False, 'error': 'Employee not found'}
        resp = client.delete('/api/v1/employees/999999999', headers=_auth_headers())
        assert resp.status_code == 404


# ==================== GET /api/v1/employees/types ====================

class TestGetEmployeeTypes:

    @patch('app.api.v1.routes.hr_employee_routes.hr_employee_service.get_employee_types')
    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_get_employee_types_success(self, mock_verify, mock_get_user, mock_types, client):
        # token_required only -- staff can access
        mock_verify.return_value = {
            'sid': 2, 'email': 'staff@test.com', 'role': 'staff', 'type': 'access'
        }
        mock_get_user.return_value = {'department_id': 2, 'department': {'code': 'SALES'}}
        mock_types.return_value = {
            'success': True,
            'employee_types': [
                {'id': 10001, 'code': 'OFFICE', 'name': 'Office', 'is_active': True},
                {'id': 10002, 'code': 'STORE', 'name': 'Store', 'is_active': True}
            ]
        }
        resp = client.get('/api/v1/employees/types', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['employee_types']) == 2


# ==================== GET /api/v1/employees/contracts ====================

class TestGetContractTypes:

    @patch('app.api.v1.routes.hr_employee_routes.hr_employee_service.get_contract_types')
    @patch('app.api.v1.routes.auth_middleware.auth_service.get_user_by_sid')
    @patch('app.api.v1.routes.auth_middleware.auth_service.verify_token')
    def test_get_contract_types_success(self, mock_verify, mock_get_user, mock_contracts, client):
        mock_verify.return_value = {
            'sid': 2, 'email': 'staff@test.com', 'role': 'staff', 'type': 'access'
        }
        mock_get_user.return_value = {'department_id': 2, 'department': {'code': 'SALES'}}
        mock_contracts.return_value = {
            'success': True,
            'contract_types': [
                {'id': 20001, 'code': 'PERMANENT', 'name': 'Permanent', 'is_active': True},
                {'id': 20002, 'code': 'INTERN', 'name': 'Intern', 'is_active': True}
            ]
        }
        resp = client.get('/api/v1/employees/contracts', headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['contract_types']) == 2
