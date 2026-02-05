"""
Unit tests for Employee API Routes (RetailPro / Oracle employees)

These are public endpoints (no auth required).
Each handler creates a local `service = EmployeeService()`, so we patch the class
at the module level: app.api.v1.routes.employee_routes.EmployeeService.
Similarly, EmployeeListResponse.format is patched at the same module.
"""
import pytest
from unittest.mock import patch, MagicMock
from app.main import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    return app.test_client()


# ==================== GET /api/v1/rp-employees/ ====================

class TestGetEmployees:

    @patch('app.api.v1.routes.employee_routes.EmployeeListResponse')
    @patch('app.api.v1.routes.employee_routes.EmployeeService')
    def test_get_employees_success(self, MockService, MockResponse, client):
        mock_instance = MagicMock()
        MockService.return_value = mock_instance
        mock_instance.get_all_employees.return_value = [
            {'employee_code': 'E001', 'name': 'Alice'},
            {'employee_code': 'E002', 'name': 'Bob'}
        ]
        MockResponse.format.return_value = [
            {'employee_code': 'E001', 'name': 'Alice'},
            {'employee_code': 'E002', 'name': 'Bob'}
        ]

        resp = client.get('/api/v1/rp-employees/')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['data']) == 2
        MockResponse.format.assert_called_once()

    @patch('app.api.v1.routes.employee_routes.EmployeeListResponse')
    @patch('app.api.v1.routes.employee_routes.EmployeeService')
    def test_get_employees_empty(self, MockService, MockResponse, client):
        mock_instance = MagicMock()
        MockService.return_value = mock_instance
        mock_instance.get_all_employees.return_value = []
        MockResponse.format.return_value = []

        resp = client.get('/api/v1/rp-employees/')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['data'] == []

    @patch('app.api.v1.routes.employee_routes.EmployeeService')
    def test_get_employees_service_exception(self, MockService, client):
        mock_instance = MagicMock()
        MockService.return_value = mock_instance
        mock_instance.get_all_employees.side_effect = Exception('Oracle connection failed')

        resp = client.get('/api/v1/rp-employees/')
        assert resp.status_code == 500
        body = resp.get_json()
        assert body['success'] is False
        assert 'Oracle connection failed' in body['error']


# ==================== GET /api/v1/rp-employees/<employee_code> ====================

class TestGetEmployeeByCode:

    @patch('app.api.v1.routes.employee_routes.EmployeeService')
    def test_get_employee_by_code_success(self, MockService, client):
        mock_instance = MagicMock()
        MockService.return_value = mock_instance
        mock_instance.get_employee_by_code.return_value = {
            'employee_code': 'E001',
            'name': 'Alice',
            'store_code': 'ST001'
        }

        resp = client.get('/api/v1/rp-employees/E001')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['data']['employee_code'] == 'E001'
        mock_instance.get_employee_by_code.assert_called_once_with('E001')

    @patch('app.api.v1.routes.employee_routes.EmployeeService')
    def test_get_employee_by_code_not_found(self, MockService, client):
        mock_instance = MagicMock()
        MockService.return_value = mock_instance
        mock_instance.get_employee_by_code.return_value = None

        resp = client.get('/api/v1/rp-employees/NONEXIST')
        assert resp.status_code == 404
        body = resp.get_json()
        assert body['success'] is False
        assert 'Employee not found' in body['error']

    @patch('app.api.v1.routes.employee_routes.EmployeeService')
    def test_get_employee_by_code_exception(self, MockService, client):
        mock_instance = MagicMock()
        MockService.return_value = mock_instance
        mock_instance.get_employee_by_code.side_effect = Exception('DB error')

        resp = client.get('/api/v1/rp-employees/E001')
        assert resp.status_code == 500
        body = resp.get_json()
        assert body['success'] is False
        assert 'DB error' in body['error']


# ==================== GET /api/v1/rp-employees/store/<store_code> ====================

class TestGetEmployeesByStore:

    @patch('app.api.v1.routes.employee_routes.EmployeeListResponse')
    @patch('app.api.v1.routes.employee_routes.EmployeeService')
    def test_get_employees_by_store_success(self, MockService, MockResponse, client):
        mock_instance = MagicMock()
        MockService.return_value = mock_instance
        mock_instance.get_employees_by_store.return_value = [
            {'employee_code': 'E001', 'name': 'Alice'}
        ]
        MockResponse.format.return_value = [
            {'employee_code': 'E001', 'name': 'Alice'}
        ]

        resp = client.get('/api/v1/rp-employees/store/ST001')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert len(body['data']) == 1
        mock_instance.get_employees_by_store.assert_called_once_with('ST001')

    @patch('app.api.v1.routes.employee_routes.EmployeeListResponse')
    @patch('app.api.v1.routes.employee_routes.EmployeeService')
    def test_get_employees_by_store_empty(self, MockService, MockResponse, client):
        mock_instance = MagicMock()
        MockService.return_value = mock_instance
        mock_instance.get_employees_by_store.return_value = []
        MockResponse.format.return_value = []

        resp = client.get('/api/v1/rp-employees/store/ST999')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['data'] == []

    @patch('app.api.v1.routes.employee_routes.EmployeeService')
    def test_get_employees_by_store_exception(self, MockService, client):
        mock_instance = MagicMock()
        MockService.return_value = mock_instance
        mock_instance.get_employees_by_store.side_effect = Exception('Connection timeout')

        resp = client.get('/api/v1/rp-employees/store/ST001')
        assert resp.status_code == 500
        body = resp.get_json()
        assert body['success'] is False
        assert 'Connection timeout' in body['error']
