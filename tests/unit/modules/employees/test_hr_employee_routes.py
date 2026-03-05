"""
Unit tests for HR Employee routes

Tests:
- GET    /api/v1/employees
- GET    /api/v1/employees/<sid>
- POST   /api/v1/employees
- PUT    /api/v1/employees/<sid>
- DELETE /api/v1/employees/<sid>
- POST   /api/v1/employees/<sid>/manager-status
- POST   /api/v1/employees/<sid>/sync-retailpro
- GET    /api/v1/employees/types
- GET    /api/v1/employees/contracts
"""
import pytest
from unittest.mock import patch, MagicMock


# ===========================================================================
# Auth helpers
# ===========================================================================

def _auth_headers():
    return {'Authorization': 'Bearer testtoken'}


def _mock_admin_auth(mock_get_user, mock_verify):
    """Set up mocks so the request passes admin_or_hr_it_manager_required."""
    mock_verify.return_value = {
        'sid': 1, 'email': 'admin@test.com', 'role': 'admin',
        'role_code': 'ADMIN', 'type': 'access'
    }
    mock_get_user.return_value = {
        'sid': 1, 'role': 'admin', 'role_code': 'ADMIN',
        'department_code': 'IT'
    }


def _mock_token_auth(mock_get_user, mock_verify):
    """Set up mocks so the request passes token_required."""
    mock_verify.return_value = {
        'sid': 1, 'email': 'user@test.com', 'role': 'staff', 'type': 'access'
    }
    mock_get_user.return_value = None


# ===========================================================================
# GET /api/v1/employees
# ===========================================================================

class TestGetAllEmployees:
    URL = '/api/v1/employees'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.get_all_employees')
    def test_success(self, mock_get_all, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_get_all.return_value = {
            'success': True,
            'employees': [{'sid': 1, 'employee_code': 'GL013'}]
        }

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['employees']) == 1

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.get_all_employees')
    def test_with_filters(self, mock_get_all, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_get_all.return_value = {'success': True, 'employees': []}

        resp = client.get(self.URL + '?department_code=IT&role=staff', headers=_auth_headers())

        assert resp.status_code == 200
        mock_get_all.assert_called_once_with(department_code='IT', role='staff')

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.get_all_employees')
    def test_server_error(self, mock_get_all, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_get_all.return_value = {'success': False, 'error': 'DB error'}

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 500


# ===========================================================================
# GET /api/v1/employees/<sid>
# ===========================================================================

class TestGetEmployee:
    URL = '/api/v1/employees/300000001'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.get_employee_by_sid')
    def test_success(self, mock_get, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_get.return_value = {
            'success': True,
            'employee': {'sid': 300000001, 'employee_code': 'GL013'}
        }

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['employee']['sid'] == 300000001

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.get_employee_by_sid')
    def test_not_found(self, mock_get, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_get.return_value = {'success': False, 'error': 'Employee not found'}

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 404


# ===========================================================================
# POST /api/v1/employees
# ===========================================================================

class TestCreateEmployee:
    URL = '/api/v1/employees'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.create_employee')
    def test_success(self, mock_create, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_create.return_value = {
            'success': True,
            'employee': {'sid': 300000002, 'employee_code': 'EMP001'}
        }

        resp = client.post(self.URL, json={
            'employee_code': 'EMP001',
            'full_name': 'Test Employee',
            'employee_type': 'store',
            'contract': 'permanent',
            'department_id': 1,
            'store_id': 1,
            'join_date': '2026-01-15'
        }, headers=_auth_headers())

        assert resp.status_code == 201
        body = resp.get_json()
        assert body['success'] is True

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_required_field(self, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)

        resp = client.post(self.URL, json={
            'employee_code': 'EMP001',
            'full_name': 'Test'
            # missing: employee_type, contract, department_id, join_date
        }, headers=_auth_headers())

        assert resp.status_code == 400
        body = resp.get_json()
        assert 'Missing required field' in body['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_no_body(self, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)

        resp = client.post(self.URL, headers=_auth_headers(),
                          content_type='application/json')

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.create_employee')
    def test_service_error(self, mock_create, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_create.return_value = {'success': False, 'error': 'Employee code already exists'}

        resp = client.post(self.URL, json={
            'employee_code': 'DUPE',
            'full_name': 'Test',
            'employee_type': 'store',
            'contract': 'permanent',
            'department_id': 1,
            'store_id': 1,
            'join_date': '2026-01-15'
        }, headers=_auth_headers())

        assert resp.status_code == 400


# ===========================================================================
# PUT /api/v1/employees/<sid>
# ===========================================================================

class TestUpdateEmployee:
    URL = '/api/v1/employees/300000001'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.update_employee')
    def test_success(self, mock_update, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_update.return_value = {
            'success': True,
            'employee': {'sid': 300000001, 'full_name': 'Updated Name'}
        }

        resp = client.put(self.URL, json={'full_name': 'Updated Name'},
                         headers=_auth_headers())

        assert resp.status_code == 200
        mock_update.assert_called_once_with(300000001, {'full_name': 'Updated Name'})

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_no_body(self, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)

        resp = client.put(self.URL, headers=_auth_headers(),
                         content_type='application/json')

        assert resp.status_code == 400

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_no_valid_fields(self, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)

        resp = client.put(self.URL, json={'employee_code': 'CANT_CHANGE'},
                         headers=_auth_headers())

        assert resp.status_code == 400
        assert 'No valid fields' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.update_employee')
    def test_not_found(self, mock_update, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_update.return_value = {'success': False, 'error': 'Employee not found'}

        resp = client.put(self.URL, json={'full_name': 'X'}, headers=_auth_headers())

        assert resp.status_code == 404

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.update_employee')
    def test_filters_allowed_fields(self, mock_update, mock_get_user, mock_verify, client):
        """Only allowed fields should be passed to service."""
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_update.return_value = {'success': True, 'employee': {'sid': 300000001}}

        resp = client.put(self.URL, json={
            'full_name': 'New',
            'store_id': 2,
            'employee_code': 'BLOCKED',  # not allowed
            'random_field': 'ignored',
        }, headers=_auth_headers())

        assert resp.status_code == 200
        passed_updates = mock_update.call_args[0][1]
        assert 'full_name' in passed_updates
        assert 'store_id' in passed_updates
        assert 'employee_code' not in passed_updates
        assert 'random_field' not in passed_updates


# ===========================================================================
# DELETE /api/v1/employees/<sid>
# ===========================================================================

class TestDeleteEmployee:
    URL = '/api/v1/employees/300000001'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.delete_employee')
    def test_success(self, mock_delete, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_delete.return_value = {'success': True}

        resp = client.delete(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.delete_employee')
    def test_not_found(self, mock_delete, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_delete.return_value = {'success': False, 'error': 'Employee not found'}

        resp = client.delete(self.URL, headers=_auth_headers())

        assert resp.status_code == 404


# ===========================================================================
# POST /api/v1/employees/<sid>/manager-status
# ===========================================================================

class TestManagerStatus:
    URL = '/api/v1/employees/300000001/manager-status'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.set_manager_status')
    def test_success(self, mock_set, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_set.return_value = {
            'success': True,
            'is_manager': True,
            'effective_from': '2026-03-04'
        }

        resp = client.post(self.URL, json={'is_manager': True},
                          headers=_auth_headers())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['is_manager'] is True
        mock_set.assert_called_once_with(300000001, True)

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_is_manager(self, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)

        resp = client.post(self.URL, json={}, headers=_auth_headers())

        assert resp.status_code == 400
        assert 'is_manager' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_non_boolean_is_manager(self, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)

        resp = client.post(self.URL, json={'is_manager': 'yes'},
                          headers=_auth_headers())

        assert resp.status_code == 400
        assert 'boolean' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.set_manager_status')
    def test_not_found(self, mock_set, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_set.return_value = {'success': False, 'error': 'Employee not found'}

        resp = client.post(self.URL, json={'is_manager': True},
                          headers=_auth_headers())

        assert resp.status_code == 404


# ===========================================================================
# POST /api/v1/employees/<sid>/sync-retailpro
# ===========================================================================

class TestSyncRetailPro:
    URL = '/api/v1/employees/300000001/sync-retailpro'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.sync_retailpro_data')
    def test_success(self, mock_sync, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_sync.return_value = {
            'success': True,
            'employee': {'sid': 300000001, 'retailpro_sid': '690001'}
        }

        resp = client.post(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.sync_retailpro_data')
    def test_not_found(self, mock_sync, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_sync.return_value = {'success': False, 'error': 'Employee not found'}

        resp = client.post(self.URL, headers=_auth_headers())

        assert resp.status_code == 404

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.sync_retailpro_data')
    def test_no_retailpro_account(self, mock_sync, mock_get_user, mock_verify, client):
        _mock_admin_auth(mock_get_user, mock_verify)
        mock_sync.return_value = {
            'success': False,
            'error': 'No RetailPro account found for employee code EMP001'
        }

        resp = client.post(self.URL, headers=_auth_headers())

        assert resp.status_code == 400


# ===========================================================================
# GET /api/v1/employees/types
# ===========================================================================

class TestGetEmployeeTypes:
    URL = '/api/v1/employees/types'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.get_employee_types')
    def test_success(self, mock_get_types, mock_get_user, mock_verify, client):
        _mock_token_auth(mock_get_user, mock_verify)
        mock_get_types.return_value = {
            'success': True,
            'employee_types': [
                {'id': 10001, 'code': 'OFFICE', 'name': 'Office', 'is_active': True}
            ]
        }

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        assert len(resp.get_json()['employee_types']) == 1


# ===========================================================================
# GET /api/v1/employees/contracts
# ===========================================================================

class TestGetContractTypes:
    URL = '/api/v1/employees/contracts'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.employees.routes.hr_employee_service.get_contract_types')
    def test_success(self, mock_get_contracts, mock_get_user, mock_verify, client):
        _mock_token_auth(mock_get_user, mock_verify)
        mock_get_contracts.return_value = {
            'success': True,
            'contract_types': [
                {'id': 20001, 'code': 'PERMANENT', 'name': 'Permanent', 'is_active': True}
            ]
        }

        resp = client.get(self.URL, headers=_auth_headers())

        assert resp.status_code == 200
        assert len(resp.get_json()['contract_types']) == 1
