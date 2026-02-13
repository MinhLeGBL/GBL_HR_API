"""
Unit tests for HR Employee API Routes

Tests all employee endpoints:
- GET    /api/v1/employees           - List all employees
- GET    /api/v1/employees/:sid      - Get employee by SID
- POST   /api/v1/employees           - Create employee
- PUT    /api/v1/employees/:sid      - Update employee
- DELETE /api/v1/employees/:sid      - Delete employee
- POST   /api/v1/employees/:sid/sync-retailpro - Sync RetailPro data
- GET    /api/v1/employees/types     - Get employee types
- GET    /api/v1/employees/contracts - Get contract types
"""
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers: valid request payloads
# ---------------------------------------------------------------------------

def _employee_payload(**overrides):
    """Return a valid payload for creating an employee."""
    payload = {
        'employee_code': 'EMP001',
        'full_name': 'Nguyen Van A',
        'employee_type': 'store',
        'contract': 'permanent',
        'department_id': 1,
        'store_id': 1,
        'join_date': '2024-01-15',
        'is_active': True
    }
    payload.update(overrides)
    return payload


def _mock_employee():
    """Return a mock employee response."""
    return {
        'sid': 300000001,
        'employee_code': 'EMP001',
        'full_name': 'Nguyen Van A',
        'employee_type': 'STORE',
        'contract': 'PERMANENT',
        'department_id': 1,
        'store_id': 1,
        'email': None,
        'is_active': True,
        'join_date': '2024-01-15',
        'department': {'id': 1, 'code': 'RETAIL', 'name': 'Retail'},
        'store': {'id': 1, 'store_code': 'RHN', 'store_name': 'Retail Ha Noi'}
    }


@pytest.fixture
def auth_mocks():
    """Mock auth middleware for admin access."""
    token_payload = {
        'sid': 1,
        'email': 'admin@example.com',
        'role': 'admin',
        'type': 'access'
    }
    user_data = {
        'sid': 1,
        'email': 'admin@example.com',
        'role': 'admin',
        'department_id': 1,
        'department': {'code': 'IT'}
    }
    with patch('app.core.auth.middleware.auth_service.verify_token', return_value=token_payload):
        with patch('app.core.auth.middleware.auth_service.get_user_by_sid', return_value=user_data):
            yield


# ===========================================================================
# GET /api/v1/employees - List all employees
# ===========================================================================

class TestGetAllEmployees:
    """Tests for listing all employees."""

    URL = '/api/v1/employees'

    @patch('app.modules.employees.routes.hr_employee_service.get_all_employees')
    def test_success(self, mock_service, client, auth_mocks):
        mock_service.return_value = {
            'success': True,
            'employees': [_mock_employee()]
        }

        resp = client.get(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['employees']) == 1
        assert body['employees'][0]['employee_code'] == 'EMP001'

    @patch('app.modules.employees.routes.hr_employee_service.get_all_employees')
    def test_with_department_filter(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': True, 'employees': []}

        resp = client.get(f'{self.URL}?department_code=HR',
                         headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 200
        mock_service.assert_called_once_with(department_code='HR', role=None)

    @patch('app.modules.employees.routes.hr_employee_service.get_all_employees')
    def test_with_role_filter(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': True, 'employees': []}

        resp = client.get(f'{self.URL}?role=manager',
                         headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 200
        mock_service.assert_called_once_with(department_code=None, role='manager')

    @patch('app.modules.employees.routes.hr_employee_service.get_all_employees')
    def test_service_error_returns_500(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': False, 'error': 'Database connection failed'}

        resp = client.get(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 500

    def test_missing_token_returns_401(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, client):
        with patch('app.core.auth.middleware.auth_service.verify_token', return_value=None):
            resp = client.get(self.URL, headers={'Authorization': 'Bearer invalid-token'})
        assert resp.status_code == 401


# ===========================================================================
# GET /api/v1/employees/:sid - Get employee by SID
# ===========================================================================

class TestGetEmployee:
    """Tests for getting a single employee."""

    URL = '/api/v1/employees/300000001'

    @patch('app.modules.employees.routes.hr_employee_service.get_employee_by_sid')
    def test_success(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': True, 'employee': _mock_employee()}

        resp = client.get(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['employee']['sid'] == 300000001

    @patch('app.modules.employees.routes.hr_employee_service.get_employee_by_sid')
    def test_not_found(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': False, 'error': 'Employee not found'}

        resp = client.get(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 404

    @patch('app.modules.employees.routes.hr_employee_service.get_employee_by_sid')
    def test_service_error_returns_500(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': False, 'error': 'Database error'}

        resp = client.get(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 500


# ===========================================================================
# POST /api/v1/employees - Create employee
# ===========================================================================

class TestCreateEmployee:
    """Tests for creating an employee."""

    URL = '/api/v1/employees'

    @patch('app.modules.employees.routes.hr_employee_service.create_employee')
    def test_success(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': True, 'employee': _mock_employee()}

        resp = client.post(self.URL, json=_employee_payload(),
                          headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 201
        body = resp.get_json()
        assert body['success'] is True

    def test_missing_body(self, client, auth_mocks):
        # Send empty JSON to avoid 415 Unsupported Media Type
        # Empty dict {} is falsy in Python, so route returns "Request body is required"
        resp = client.post(self.URL, json={}, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 400
        assert 'Request body is required' in resp.get_json()['error']

    @pytest.mark.parametrize('missing_field', [
        'employee_code', 'full_name', 'employee_type', 'contract', 'department_id', 'join_date'
    ])
    def test_missing_required_field(self, missing_field, client, auth_mocks):
        payload = _employee_payload()
        del payload[missing_field]

        resp = client.post(self.URL, json=payload,
                          headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 400
        assert missing_field in resp.get_json()['error']

    @patch('app.modules.employees.routes.hr_employee_service.create_employee')
    def test_service_error_returns_400(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': False, 'error': 'Employee code already exists'}

        resp = client.post(self.URL, json=_employee_payload(),
                          headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 400
        assert 'already exists' in resp.get_json()['error']


# ===========================================================================
# PUT /api/v1/employees/:sid - Update employee
# ===========================================================================

class TestUpdateEmployee:
    """Tests for updating an employee."""

    URL = '/api/v1/employees/300000001'

    @patch('app.modules.employees.routes.hr_employee_service.update_employee')
    def test_success(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': True, 'employee': _mock_employee()}

        resp = client.put(self.URL, json={'full_name': 'Updated Name'},
                         headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 200
        mock_service.assert_called_once_with(300000001, {'full_name': 'Updated Name'})

    def test_missing_body(self, client, auth_mocks):
        # Send empty JSON to avoid 415 Unsupported Media Type
        # Empty dict {} is falsy in Python, so route returns "Request body is required"
        resp = client.put(self.URL, json={}, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 400
        assert 'Request body is required' in resp.get_json()['error']

    def test_no_valid_fields(self, client, auth_mocks):
        resp = client.put(self.URL, json={'invalid_field': 'value'},
                         headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 400
        assert 'No valid fields' in resp.get_json()['error']

    @patch('app.modules.employees.routes.hr_employee_service.update_employee')
    def test_not_found(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': False, 'error': 'Employee not found'}

        resp = client.put(self.URL, json={'full_name': 'Updated'},
                         headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 404

    @patch('app.modules.employees.routes.hr_employee_service.update_employee')
    def test_filters_disallowed_fields(self, mock_service, client, auth_mocks):
        """Ensure employee_code cannot be updated."""
        mock_service.return_value = {'success': True, 'employee': _mock_employee()}

        resp = client.put(self.URL,
                         json={'employee_code': 'NEW001', 'full_name': 'Valid'},
                         headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 200
        # employee_code should be filtered out, only full_name passed
        mock_service.assert_called_once_with(300000001, {'full_name': 'Valid'})


# ===========================================================================
# DELETE /api/v1/employees/:sid - Delete employee
# ===========================================================================

class TestDeleteEmployee:
    """Tests for deleting an employee."""

    URL = '/api/v1/employees/300000001'

    @patch('app.modules.employees.routes.hr_employee_service.delete_employee')
    def test_success(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': True}

        resp = client.delete(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    @patch('app.modules.employees.routes.hr_employee_service.delete_employee')
    def test_not_found(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': False, 'error': 'Employee not found'}

        resp = client.delete(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 404


# ===========================================================================
# POST /api/v1/employees/:sid/sync-retailpro - Sync RetailPro data
# ===========================================================================

class TestSyncRetailPro:
    """Tests for syncing RetailPro data."""

    URL = '/api/v1/employees/300000001/sync-retailpro'

    @patch('app.modules.employees.routes.hr_employee_service.sync_retailpro_data')
    def test_success(self, mock_service, client, auth_mocks):
        employee = _mock_employee()
        employee['retailpro_sid'] = '708336140000166083'
        employee['retailpro_username'] = 'NGUYEN_VAN_A'
        mock_service.return_value = {'success': True, 'employee': employee}

        resp = client.post(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['employee']['retailpro_username'] == 'NGUYEN_VAN_A'

    @patch('app.modules.employees.routes.hr_employee_service.sync_retailpro_data')
    def test_employee_not_found(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': False, 'error': 'Employee not found'}

        resp = client.post(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 404

    @patch('app.modules.employees.routes.hr_employee_service.sync_retailpro_data')
    def test_no_retailpro_account(self, mock_service, client, auth_mocks):
        mock_service.return_value = {
            'success': False,
            'error': 'No RetailPro account found for employee code EMP001'
        }

        resp = client.post(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 400
        assert 'No RetailPro account' in resp.get_json()['error']


# ===========================================================================
# GET /api/v1/employees/types - Get employee types
# ===========================================================================

class TestGetEmployeeTypes:
    """Tests for getting employee types lookup."""

    URL = '/api/v1/employees/types'

    @patch('app.modules.employees.routes.hr_employee_service.get_employee_types')
    def test_success(self, mock_service, client, auth_mocks):
        mock_service.return_value = {
            'success': True,
            'employee_types': [
                {'id': 10001, 'code': 'OFFICE', 'name': 'Office', 'is_active': True},
                {'id': 10002, 'code': 'STORE', 'name': 'Store', 'is_active': True}
            ]
        }

        resp = client.get(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['employee_types']) == 2

    @patch('app.modules.employees.routes.hr_employee_service.get_employee_types')
    def test_service_error(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': False, 'error': 'Database error'}

        resp = client.get(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 500


# ===========================================================================
# GET /api/v1/employees/contracts - Get contract types
# ===========================================================================

class TestGetContractTypes:
    """Tests for getting contract types lookup."""

    URL = '/api/v1/employees/contracts'

    @patch('app.modules.employees.routes.hr_employee_service.get_contract_types')
    def test_success(self, mock_service, client, auth_mocks):
        mock_service.return_value = {
            'success': True,
            'contract_types': [
                {'id': 20001, 'code': 'PERMANENT', 'name': 'Permanent', 'is_active': True},
                {'id': 20002, 'code': 'INTERN', 'name': 'Intern', 'is_active': True},
                {'id': 20003, 'code': 'PROBATION', 'name': 'Probation', 'is_active': True}
            ]
        }

        resp = client.get(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['contract_types']) == 3

    @patch('app.modules.employees.routes.hr_employee_service.get_contract_types')
    def test_service_error(self, mock_service, client, auth_mocks):
        mock_service.return_value = {'success': False, 'error': 'Database error'}

        resp = client.get(self.URL, headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 500


# ===========================================================================
# Authorization tests
# ===========================================================================

class TestAuthorization:
    """Tests for authorization requirements."""

    def test_staff_cannot_access_employees(self, client):
        """Staff role should be denied access."""
        token_payload = {
            'sid': 2,
            'email': 'staff@example.com',
            'role': 'staff',
            'type': 'access'
        }
        user_data = {
            'sid': 2,
            'email': 'staff@example.com',
            'role': 'staff',
            'department_id': 1,
            'department': {'code': 'SALES'}
        }

        with patch('app.core.auth.middleware.auth_service.verify_token', return_value=token_payload):
            with patch('app.core.auth.middleware.auth_service.get_user_by_sid', return_value=user_data):
                resp = client.get('/api/v1/employees',
                                 headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 403

    def test_hr_manager_can_access_employees(self, client):
        """HR manager should have access."""
        token_payload = {
            'sid': 3,
            'email': 'hr_manager@example.com',
            'role': 'manager',
            'type': 'access'
        }
        user_data = {
            'sid': 3,
            'email': 'hr_manager@example.com',
            'role': 'manager',
            'department_id': 2,
            'department': {'code': 'HR'}
        }

        with patch('app.core.auth.middleware.auth_service.verify_token', return_value=token_payload):
            with patch('app.core.auth.middleware.auth_service.get_user_by_sid', return_value=user_data):
                with patch('app.modules.employees.routes.hr_employee_service.get_all_employees') as mock_service:
                    mock_service.return_value = {'success': True, 'employees': []}
                    resp = client.get('/api/v1/employees',
                                     headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 200

    def test_sales_manager_cannot_access_employees(self, client):
        """Sales manager (non-HR/IT) should be denied."""
        token_payload = {
            'sid': 4,
            'email': 'sales_manager@example.com',
            'role': 'manager',
            'type': 'access'
        }
        user_data = {
            'sid': 4,
            'email': 'sales_manager@example.com',
            'role': 'manager',
            'department_id': 3,
            'department': {'code': 'SALES'}
        }

        with patch('app.core.auth.middleware.auth_service.verify_token', return_value=token_payload):
            with patch('app.core.auth.middleware.auth_service.get_user_by_sid', return_value=user_data):
                resp = client.get('/api/v1/employees',
                                 headers={'Authorization': 'Bearer test-token'})

        assert resp.status_code == 403
