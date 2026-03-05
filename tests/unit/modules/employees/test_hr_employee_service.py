"""
Unit tests for HREmployeeService

Tests:
- get_all_employees()
- get_employee_by_sid()
- create_employee()
- update_employee()
- delete_employee()
- get_employee_types()
- get_contract_types()
- set_manager_status()
- sync_retailpro_data()
- CR #16: status snapshot writes on create/update/manager-status
"""
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import date, datetime

from app.modules.employees.service import HREmployeeService


# ===========================================================================
# Shared helpers
# ===========================================================================

def _make_employee_row(
    sid=300000001,
    employee_code='GL013',
    full_name='Nguyen Van A',
    employee_type_id=10002,
    employee_type='STORE',
    contract_type_id=20001,
    contract='PERMANENT',
    department_id=1,
    store_id=1,
    email=None,
    retailpro_username='NGUYEN_A',
    retailpro_sid='690001',
    is_active=True,
    join_date=date(2020, 1, 15),
    created_at=datetime(2026, 1, 1, 0, 0),
    updated_at=datetime(2026, 1, 1, 0, 0),
    is_manager=False,
):
    """Return a tuple matching the _EMPLOYEE_SELECT column order."""
    return (
        sid, employee_code, full_name, employee_type_id, employee_type,
        contract_type_id, contract, department_id, store_id, email,
        retailpro_username, retailpro_sid, is_active, join_date,
        created_at, updated_at, is_manager,
    )


# ===========================================================================
# get_all_employees
# ===========================================================================

class TestGetAllEmployees:

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchall.return_value = [_make_employee_row()]
        # _get_department_info
        mock_cursor.fetchone.side_effect = [
            (1, 'SALES', 'Sales'),       # department info
            (1, 'RWT', 'Retail West', 'rp1'),  # store info
        ]

        svc = HREmployeeService()
        result = svc.get_all_employees()

        assert result['success'] is True
        assert len(result['employees']) == 1
        emp = result['employees'][0]
        assert emp['employee_code'] == 'GL013'
        assert emp['department']['code'] == 'SALES'
        assert emp['store']['store_code'] == 'RWT'

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_no_connection(self, mock_get_conn):
        mock_get_conn.return_value = None
        svc = HREmployeeService()
        result = svc.get_all_employees()
        assert result['success'] is False

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_with_department_filter(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = []

        svc = HREmployeeService()
        result = svc.get_all_employees(department_code='IT')

        assert result['success'] is True
        # Check that the query was executed with the department filter
        executed_query = mock_cursor.execute.call_args[0][0]
        assert 'UPPER(d.code)' in executed_query


# ===========================================================================
# get_employee_by_sid
# ===========================================================================

class TestGetEmployeeBySid:

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.side_effect = [
            _make_employee_row(),          # main query
            (1, 'SALES', 'Sales'),         # department info
            (1, 'RWT', 'Retail West', 'rp1'),  # store info
        ]

        svc = HREmployeeService()
        result = svc.get_employee_by_sid(300000001)

        assert result['success'] is True
        assert result['employee']['sid'] == 300000001

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        svc = HREmployeeService()
        result = svc.get_employee_by_sid(999)

        assert result['success'] is False
        assert 'not found' in result['error'].lower()


# ===========================================================================
# create_employee
# ===========================================================================

class TestCreateEmployee:

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success_store_employee(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        # Sequence: resolve type, resolve contract, INSERT RETURNING sid,
        # store_history insert, _upsert_status_snapshot,
        # then get_employee_by_sid re-fetches connection
        mock_cursor.fetchone.side_effect = [
            (10002,),    # resolve employee type 'STORE'
            (20001,),    # resolve contract 'PERMANENT'
            (300000002,),  # INSERT RETURNING sid
        ]

        # get_employee_by_sid is called after commit — mock at service level
        svc = HREmployeeService()
        svc.get_employee_by_sid = MagicMock(return_value={
            'success': True,
            'employee': {'sid': 300000002, 'employee_code': 'EMP001'}
        })

        result = svc.create_employee(
            employee_code='EMP001', full_name='Test Employee',
            employee_type='store', contract='permanent',
            department_id=1, join_date='2026-01-15',
            store_id=5, is_active=True
        )

        assert result['success'] is True
        assert result['employee']['sid'] == 300000002
        mock_conn.commit.assert_called_once()

        # Verify _upsert_status_snapshot was called (the UPSERT SQL)
        all_execute_calls = [str(c) for c in mock_cursor.execute.call_args_list]
        upsert_calls = [c for c in all_execute_calls if 'employee_status_history' in c]
        assert len(upsert_calls) >= 1, "Expected upsert to employee_status_history"

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_store_employee_requires_store_id(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.side_effect = [
            (10002,),  # resolve type
            (20001,),  # resolve contract
        ]

        svc = HREmployeeService()
        result = svc.create_employee(
            employee_code='EMP001', full_name='Test',
            employee_type='store', contract='permanent',
            department_id=1, join_date='2026-01-15'
        )

        assert result['success'] is False
        assert 'store_id' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_office_employee_requires_email(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.side_effect = [
            (10001,),  # resolve type OFFICE
            (20001,),  # resolve contract
        ]

        svc = HREmployeeService()
        result = svc.create_employee(
            employee_code='EMP002', full_name='Test',
            employee_type='office', contract='permanent',
            department_id=1, join_date='2026-01-15'
        )

        assert result['success'] is False
        assert 'email' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_invalid_employee_type(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None  # type not found

        svc = HREmployeeService()
        result = svc.create_employee(
            employee_code='EMP001', full_name='Test',
            employee_type='invalid', contract='permanent',
            department_id=1, join_date='2026-01-15'
        )

        assert result['success'] is False
        assert 'employee_type' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_status_snapshot_period_matches_join_date(self, mock_get_conn):
        """CR #16: Initial snapshot period should be join_date's month/year."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.side_effect = [
            (10001,),      # resolve type OFFICE
            (20001,),      # resolve contract
            (300000003,),  # INSERT RETURNING sid
        ]

        svc = HREmployeeService()
        svc.get_employee_by_sid = MagicMock(return_value={
            'success': True, 'employee': {'sid': 300000003}
        })

        result = svc.create_employee(
            employee_code='EMP003', full_name='Test Office',
            employee_type='office', contract='permanent',
            department_id=1, join_date='2025-07-20',
            email='test@example.com'
        )

        assert result['success'] is True
        # Find the upsert call and verify period is July 2025
        for c in mock_cursor.execute.call_args_list:
            sql = str(c[0][0]) if c[0] else ''
            if 'employee_status_history' in sql and 'INSERT' in sql:
                params = c[0][1]
                # params: (sid, month, year, is_active, store_id, dept, contract, is_manager)
                assert params[1] == 7, f"Expected period_month=7, got {params[1]}"
                assert params[2] == 2025, f"Expected period_year=2025, got {params[2]}"
                break


# ===========================================================================
# update_employee
# ===========================================================================

class TestUpdateEmployee:

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success_name_update_no_snapshot(self, mock_get_conn):
        """Non-tracked field (full_name) should NOT trigger a status snapshot."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.side_effect = [
            (300000001, 1),   # exists check (sid, store_id)
            (300000001,),     # UPDATE RETURNING sid
        ]

        svc = HREmployeeService()
        svc.get_employee_by_sid = MagicMock(return_value={
            'success': True, 'employee': {'sid': 300000001}
        })

        result = svc.update_employee(300000001, {'full_name': 'New Name'})

        assert result['success'] is True
        # No upsert to employee_status_history
        all_sql = [str(c[0][0]) for c in mock_cursor.execute.call_args_list if c[0]]
        snapshot_calls = [s for s in all_sql if 'employee_status_history' in s and 'INSERT' in s]
        assert len(snapshot_calls) == 0

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_store_id_change_triggers_snapshot(self, mock_get_conn):
        """CR #16: Tracked field (store_id) change should trigger a status snapshot."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.side_effect = [
            (300000001, 1),           # exists check (sid, current_store_id=1)
            (300000001,),             # UPDATE RETURNING sid
            (True, 2, 1, 20001),     # SELECT current state after update
            (False,),                 # SELECT is_manager from status history
        ]

        svc = HREmployeeService()
        svc.get_employee_by_sid = MagicMock(return_value={
            'success': True, 'employee': {'sid': 300000001}
        })

        result = svc.update_employee(300000001, {'store_id': 2})

        assert result['success'] is True
        # Should have upsert to employee_status_history
        all_sql = [str(c[0][0]) for c in mock_cursor.execute.call_args_list if c[0]]
        snapshot_calls = [s for s in all_sql if 'employee_status_history' in s and 'INSERT' in s]
        assert len(snapshot_calls) >= 1

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        svc = HREmployeeService()
        result = svc.update_employee(999, {'full_name': 'X'})

        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_no_valid_fields(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (300000001, 1)  # exists

        svc = HREmployeeService()
        result = svc.update_employee(300000001, {'employee_code': 'NEW'})

        assert result['success'] is False
        assert 'No valid fields' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_is_active_change_triggers_snapshot(self, mock_get_conn):
        """CR #16: is_active is a tracked field."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.side_effect = [
            (300000001, 1),           # exists check
            (300000001,),             # UPDATE RETURNING
            (False, 1, 1, 20001),    # SELECT current state
            (True,),                  # SELECT is_manager from history
        ]

        svc = HREmployeeService()
        svc.get_employee_by_sid = MagicMock(return_value={
            'success': True, 'employee': {'sid': 300000001}
        })

        result = svc.update_employee(300000001, {'is_active': False})

        assert result['success'] is True
        all_sql = [str(c[0][0]) for c in mock_cursor.execute.call_args_list if c[0]]
        snapshot_calls = [s for s in all_sql if 'employee_status_history' in s and 'INSERT' in s]
        assert len(snapshot_calls) >= 1


# ===========================================================================
# delete_employee
# ===========================================================================

class TestDeleteEmployee:

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (300000001,)

        svc = HREmployeeService()
        result = svc.delete_employee(300000001)

        assert result['success'] is True
        mock_conn.commit.assert_called_once()

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        svc = HREmployeeService()
        result = svc.delete_employee(999)

        assert result['success'] is False
        assert 'not found' in result['error'].lower()


# ===========================================================================
# get_employee_types / get_contract_types
# ===========================================================================

class TestLookups:

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_get_employee_types(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = [
            (10001, 'OFFICE', 'Office', True),
            (10002, 'STORE', 'Store', True),
        ]

        svc = HREmployeeService()
        result = svc.get_employee_types()

        assert result['success'] is True
        assert len(result['employee_types']) == 2
        assert result['employee_types'][0]['code'] == 'OFFICE'

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_get_contract_types(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = [
            (20001, 'PERMANENT', 'Permanent', True),
            (20002, 'INTERN', 'Intern', True),
        ]

        svc = HREmployeeService()
        result = svc.get_contract_types()

        assert result['success'] is True
        assert len(result['contract_types']) == 2


# ===========================================================================
# set_manager_status
# ===========================================================================

class TestSetManagerStatus:

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.side_effect = [
            (300000001,),                     # employee exists
            (True, date(2026, 3, 4)),         # INSERT RETURNING
            (True, 1, 1, 20001),              # SELECT employee state for snapshot
        ]

        svc = HREmployeeService()
        result = svc.set_manager_status(300000001, True)

        assert result['success'] is True
        assert result['is_manager'] is True
        assert result['effective_from'] == '2026-03-04'
        mock_conn.commit.assert_called_once()

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        svc = HREmployeeService()
        result = svc.set_manager_status(999, True)

        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_inserts_status_snapshot(self, mock_get_conn):
        """CR #16: set_manager_status should upsert to employee_status_history."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.side_effect = [
            (300000001,),                     # employee exists
            (True, date(2026, 3, 4)),         # INSERT RETURNING
            (True, 5, 2, 20001),              # SELECT employee state for snapshot
        ]

        svc = HREmployeeService()
        result = svc.set_manager_status(300000001, True)

        assert result['success'] is True
        # Verify snapshot upsert was called
        all_sql = [str(c[0][0]) for c in mock_cursor.execute.call_args_list if c[0]]
        snapshot_calls = [s for s in all_sql if 'employee_status_history' in s and 'INSERT' in s]
        assert len(snapshot_calls) >= 1


# ===========================================================================
# sync_retailpro_data
# ===========================================================================

class TestSyncRetailproData:

    @patch('app.modules.employees.service.get_oracle_connection')
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_pg_conn, mock_oracle_conn):
        # PostgreSQL mocks
        pg_conn = MagicMock()
        pg_cursor = MagicMock()
        pg_conn.cursor.return_value = pg_cursor
        mock_pg_conn.return_value = pg_conn

        pg_cursor.fetchone.return_value = ('EMP001',)  # employee_code lookup

        # Oracle mocks
        oracle_conn = MagicMock()
        oracle_cursor = MagicMock()
        oracle_conn.cursor.return_value = oracle_cursor
        mock_oracle_conn.return_value = oracle_conn

        oracle_cursor.fetchone.return_value = (690001, 'NGUYEN_A')

        svc = HREmployeeService()
        svc.get_employee_by_sid = MagicMock(return_value={
            'success': True,
            'employee': {'sid': 300000001, 'retailpro_sid': '690001'}
        })

        result = svc.sync_retailpro_data(300000001)

        assert result['success'] is True
        pg_conn.commit.assert_called_once()

    @patch('app.modules.employees.service.get_oracle_connection')
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_employee_not_found(self, mock_pg_conn, mock_oracle_conn):
        pg_conn = MagicMock()
        pg_cursor = MagicMock()
        pg_conn.cursor.return_value = pg_cursor
        mock_pg_conn.return_value = pg_conn
        pg_cursor.fetchone.return_value = None

        svc = HREmployeeService()
        result = svc.sync_retailpro_data(999)

        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.employees.service.get_oracle_connection')
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_no_retailpro_account(self, mock_pg_conn, mock_oracle_conn):
        pg_conn = MagicMock()
        pg_cursor = MagicMock()
        pg_conn.cursor.return_value = pg_cursor
        mock_pg_conn.return_value = pg_conn
        pg_cursor.fetchone.return_value = ('EMP001',)

        oracle_conn = MagicMock()
        oracle_cursor = MagicMock()
        oracle_conn.cursor.return_value = oracle_cursor
        mock_oracle_conn.return_value = oracle_conn
        oracle_cursor.fetchone.return_value = None

        svc = HREmployeeService()
        result = svc.sync_retailpro_data(300000001)

        assert result['success'] is False
        assert 'No RetailPro account' in result['error']
