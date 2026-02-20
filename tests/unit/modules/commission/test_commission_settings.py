"""
Unit tests for commission settings endpoints and service

Tests:
- GET  /api/v1/commission/employees?month=M&year=Y
- PUT  /api/v1/commission/employees/<employee_code>
- CommissionSettingsService.init_database()
- CommissionSettingsService.get_commission_employees()
- CommissionSettingsService.update_commission_settings()
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import date


# ===========================================================================
# Auth helpers — mock the middleware so tests don't need a real JWT
# ===========================================================================

def _auth_headers():
    """Return a dummy Authorization header accepted by the mocked middleware."""
    return {'Authorization': 'Bearer testtoken'}


# ---------------------------------------------------------------------------
# 1. GET /api/v1/commission/employees
# ---------------------------------------------------------------------------

class TestGetCommissionEmployees:
    URL = '/api/v1/commission/employees'

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionSettingsService')
    def test_success(self, MockService, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        mock_svc = MockService.return_value
        mock_svc.get_commission_employees.return_value = {
            'success': True,
            'month': 12,
            'year': 2025,
            'stores': [
                {
                    'store_code': 'RWT',
                    'store_name': 'Retail West',
                    'employees': [
                        {
                            'employee_code': 'GL013',
                            'full_name': 'Nguyen Van A',
                            'join_date': '2010-07-14',
                            'contract': 'permanent',
                            'is_manager': True,
                            'personal_target': 650000000,
                            'working_day': None
                        }
                    ]
                }
            ]
        }

        resp = client.get(self.URL + '?month=12&year=2025', headers=_auth_headers())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['month'] == 12
        assert body['year'] == 2025
        assert len(body['stores']) == 1
        assert body['stores'][0]['store_code'] == 'RWT'
        mock_svc.get_commission_employees.assert_called_once_with(12, 2025)

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_month(self, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        resp = client.get(self.URL + '?year=2025', headers=_auth_headers())

        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'month' in body['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_year(self, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        resp = client.get(self.URL + '?month=12', headers=_auth_headers())

        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert 'year' in body['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_invalid_month_too_low(self, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        resp = client.get(self.URL + '?month=0&year=2025', headers=_auth_headers())

        assert resp.status_code == 400
        assert 'month must be between 1 and 12' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_invalid_month_too_high(self, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        resp = client.get(self.URL + '?month=13&year=2025', headers=_auth_headers())

        assert resp.status_code == 400
        assert 'month must be between 1 and 12' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_non_integer_params(self, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        resp = client.get(self.URL + '?month=abc&year=2025', headers=_auth_headers())

        assert resp.status_code == 400
        assert 'integer' in resp.get_json()['error']

    def test_no_auth_token(self, client):
        resp = client.get(self.URL + '?month=12&year=2025')
        assert resp.status_code == 401

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionSettingsService')
    def test_service_failure_returns_500(self, MockService, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'admin', 'type': 'access'}
        mock_get_user.return_value = None

        mock_svc = MockService.return_value
        mock_svc.get_commission_employees.return_value = {
            'success': False,
            'error': 'DB connection failed'
        }

        resp = client.get(self.URL + '?month=12&year=2025', headers=_auth_headers())

        assert resp.status_code == 500
        assert resp.get_json()['success'] is False


# ===========================================================================
# 2. PUT /api/v1/commission/employees/<employee_code>
# ===========================================================================

def _put_payload(**overrides):
    payload = {
        'month': 12,
        'year': 2025,
        'is_manager': True,
        'personal_target': 650000000,
        'working_day': 22
    }
    payload.update(overrides)
    return payload


class TestUpdateCommissionEmployee:
    URL = '/api/v1/commission/employees/GL013'

    def _manager_headers(self):
        return {'Authorization': 'Bearer testtoken'}

    def _mock_manager(self, mock_verify, mock_get_user):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'manager', 'type': 'access'}
        mock_get_user.return_value = None

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionSettingsService')
    def test_success(self, MockService, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        mock_svc = MockService.return_value
        mock_svc.update_commission_settings.return_value = {
            'success': True,
            'data': {
                'employee_code': 'GL013',
                'month': 12,
                'year': 2025,
                'is_manager': True,
                'personal_target': 650000000,
                'working_day': 22
            }
        }

        resp = client.put(self.URL, json=_put_payload(), headers=self._manager_headers())

        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['data']['employee_code'] == 'GL013'
        assert body['data']['working_day'] == 22
        mock_svc.update_commission_settings.assert_called_once_with(
            employee_code='GL013',
            month=12,
            year=2025,
            is_manager=True,
            personal_target=650000000,
            working_day=22
        )

    def test_no_auth_token(self, client):
        resp = client.put(self.URL, json=_put_payload())
        assert resp.status_code == 401

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_staff_role_forbidden(self, mock_get_user, mock_verify, client):
        mock_verify.return_value = {'sid': 1, 'email': 'x@x.com', 'role': 'staff', 'type': 'access'}
        mock_get_user.return_value = None

        resp = client.put(self.URL, json=_put_payload(), headers=self._manager_headers())

        assert resp.status_code == 403

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_missing_required_field(self, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        for field in ['month', 'year', 'is_manager', 'personal_target', 'working_day']:
            payload = _put_payload()
            del payload[field]
            resp = client.put(self.URL, json=payload, headers=self._manager_headers())

            assert resp.status_code == 400
            assert field in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_invalid_month(self, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        resp = client.put(self.URL, json=_put_payload(month=0), headers=self._manager_headers())
        assert resp.status_code == 400
        assert 'month must be between 1 and 12' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_is_manager_not_boolean(self, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        resp = client.put(self.URL, json=_put_payload(is_manager='yes'), headers=self._manager_headers())
        assert resp.status_code == 400
        assert 'is_manager must be a boolean' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    def test_no_body(self, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        # Send empty JSON object — triggers the "Request body is required" check
        resp = client.put(self.URL, json={}, headers=self._manager_headers())
        assert resp.status_code == 400
        assert 'required' in resp.get_json()['error']

    @patch('app.core.auth.middleware.auth_service.verify_token')
    @patch('app.core.auth.middleware.auth_service.get_user_by_sid')
    @patch('app.modules.commission.routes.CommissionSettingsService')
    def test_service_failure_returns_500(self, MockService, mock_get_user, mock_verify, client):
        self._mock_manager(mock_verify, mock_get_user)

        mock_svc = MockService.return_value
        mock_svc.update_commission_settings.return_value = {
            'success': False,
            'error': 'DB error'
        }

        resp = client.put(self.URL, json=_put_payload(), headers=self._manager_headers())
        assert resp.status_code == 500
        assert resp.get_json()['success'] is False


# ===========================================================================
# 3. CommissionSettingsService unit tests
# ===========================================================================

class TestCommissionSettingsServiceInitDatabase:

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_success(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionSettingsService
        result = CommissionSettingsService().init_database()

        assert result['success'] is True
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_no_connection(self, mock_get_conn):
        mock_get_conn.return_value = None

        from app.modules.commission.service import CommissionSettingsService
        result = CommissionSettingsService().init_database()

        assert result['success'] is False
        assert 'PostgreSQL' in result['error']

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_db_exception_rolls_back(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception('syntax error')
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionSettingsService
        result = CommissionSettingsService().init_database()

        assert result['success'] is False
        mock_conn.rollback.assert_called_once()
        mock_conn.close.assert_called_once()


class TestCommissionSettingsServiceGetEmployees:

    def _make_cursor(self, rows, columns):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows
        mock_cursor.description = [(col,) for col in columns]
        return mock_cursor

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_success_groups_by_store(self, mock_get_conn):
        columns = [
            'store_code', 'store_name', 'employee_code', 'full_name',
            'join_date', 'contract', 'is_manager', 'personal_target', 'working_day'
        ]
        rows = [
            ('RWT', 'Retail West', 'GL013', 'Nguyen A', date(2010, 7, 14), 'PERMANENT', True, 650000000, None),
            ('RWT', 'Retail West', 'GL014', 'Nguyen B', date(2015, 3, 1),  'PERMANENT', False, 500000000, 22),
            ('HBT', 'Retail HBT',  'GL020', 'Tran C',   date(2018, 9, 5),  'PROBATION', False, None,      None),
        ]
        mock_cursor = self._make_cursor(rows, columns)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionSettingsService
        result = CommissionSettingsService().get_commission_employees(12, 2025)

        assert result['success'] is True
        assert result['month'] == 12
        assert result['year'] == 2025
        assert len(result['stores']) == 2

        rwt = next(s for s in result['stores'] if s['store_code'] == 'RWT')
        assert len(rwt['employees']) == 2
        assert rwt['employees'][0]['join_date'] == '2010-07-14'
        assert rwt['employees'][0]['contract'] == 'permanent'

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_no_connection(self, mock_get_conn):
        mock_get_conn.return_value = None

        from app.modules.commission.service import CommissionSettingsService
        result = CommissionSettingsService().get_commission_employees(12, 2025)

        assert result['success'] is False

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_empty_result(self, mock_get_conn):
        columns = [
            'store_code', 'store_name', 'employee_code', 'full_name',
            'join_date', 'contract', 'is_manager', 'personal_target', 'working_day'
        ]
        mock_cursor = self._make_cursor([], columns)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionSettingsService
        result = CommissionSettingsService().get_commission_employees(12, 2025)

        assert result['success'] is True
        assert result['stores'] == []


class TestCommissionSettingsServiceUpdate:

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_success(self, mock_get_conn):
        returned_row = ('GL013', 12, 2025, True, 650000000, 22)
        columns = ['employee_code', 'month', 'year', 'is_manager', 'personal_target', 'working_day']

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = returned_row
        mock_cursor.description = [(col,) for col in columns]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionSettingsService
        result = CommissionSettingsService().update_commission_settings(
            employee_code='GL013', month=12, year=2025,
            is_manager=True, personal_target=650000000, working_day=22
        )

        assert result['success'] is True
        assert result['data']['employee_code'] == 'GL013'
        assert result['data']['working_day'] == 22
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_no_connection(self, mock_get_conn):
        mock_get_conn.return_value = None

        from app.modules.commission.service import CommissionSettingsService
        result = CommissionSettingsService().update_commission_settings(
            employee_code='GL013', month=12, year=2025,
            is_manager=True, personal_target=650000000, working_day=22
        )

        assert result['success'] is False

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_db_exception_rolls_back(self, mock_get_conn):
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception('unique violation')

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        from app.modules.commission.service import CommissionSettingsService
        result = CommissionSettingsService().update_commission_settings(
            employee_code='GL013', month=12, year=2025,
            is_manager=True, personal_target=650000000, working_day=22
        )

        assert result['success'] is False
        mock_conn.rollback.assert_called_once()
        mock_conn.close.assert_called_once()
