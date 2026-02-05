"""
Unit tests for HREmployeeService
Tests CRUD operations for employees with mocked PostgreSQL connection.

The _EMPLOYEE_SELECT query returns 16 columns:
  (sid, employee_code, full_name, employee_type_id, et_code,
   contract_type_id, ct_code, department_id, store_id, email,
   retailpro_username, retailpro_sid, is_active, join_date,
   created_at, updated_at)

After _build_employee_dict, the service calls _get_department_info and
_get_store_info which each do an additional cursor.fetchone, so the mock
must return the right sequence of values.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from app.modules.employees.service import HREmployeeService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_conn_cursor():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor


def _make_dt(iso='2024-06-15T10:30:00'):
    dt = MagicMock()
    dt.isoformat.return_value = iso
    # Ensure hasattr(dt, 'isoformat') is True (MagicMock does this by default)
    return dt


def _employee_row(
    sid=300000001,
    employee_code='MQ027',
    full_name='Le Minh',
    employee_type_id=10001,
    et_code='OFFICE',
    contract_type_id=20001,
    ct_code='PERMANENT',
    department_id=400000001,
    store_id=None,
    email='minh@test.com',
    retailpro_username='MINH_LE',
    retailpro_sid='708336140000166083',
    is_active=True,
    join_date=None,
    created_at=None,
    updated_at=None,
):
    if join_date is None:
        join_date = _make_dt('2024-01-01')
    if created_at is None:
        created_at = _make_dt('2024-01-01T00:00:00')
    if updated_at is None:
        updated_at = _make_dt('2024-01-01T00:00:00')
    return (
        sid, employee_code, full_name, employee_type_id, et_code,
        contract_type_id, ct_code, department_id, store_id, email,
        retailpro_username, retailpro_sid, is_active, join_date,
        created_at, updated_at,
    )


def _dept_row(dept_id=400000001, code='IT', name='Information Technology'):
    return (dept_id, code, name)


def _store_row(store_id=200000001, code='S001', name='Store Alpha', rp_sid='RP001'):
    return (store_id, code, name, rp_sid)


@pytest.fixture()
def hr_service():
    return HREmployeeService()


# ======================================================================
# get_all_employees()
# ======================================================================

class TestGetAllEmployees:
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        row = _employee_row()
        mock_cursor.fetchall.return_value = [row]

        # After _build_employee_dict, the service calls:
        #   _get_department_info -> cursor.fetchone (dept row)
        #   _get_store_info     -> store_id is None, so no fetchone
        mock_cursor.fetchone.return_value = _dept_row()

        result = hr_service.get_all_employees()
        assert result['success'] is True
        assert len(result['employees']) == 1
        emp = result['employees'][0]
        assert emp['sid'] == 300000001
        assert emp['employee_code'] == 'MQ027'
        assert emp['department'] is not None
        assert emp['department']['code'] == 'IT'

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success_with_store(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        row = _employee_row(store_id=200000001, et_code='STORE')
        mock_cursor.fetchall.return_value = [row]

        # For this employee: _get_department_info + _get_store_info
        mock_cursor.fetchone.side_effect = [
            _dept_row(),
            _store_row(),
        ]

        result = hr_service.get_all_employees()
        assert result['success'] is True
        emp = result['employees'][0]
        assert emp['store'] is not None
        assert emp['store']['store_code'] == 'S001'

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_empty(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = []

        result = hr_service.get_all_employees()
        assert result['success'] is True
        assert result['employees'] == []

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, hr_service):
        mock_get_conn.return_value = None
        result = hr_service.get_all_employees()
        assert result['success'] is False
        assert 'connect' in result['error'].lower()


# ======================================================================
# get_employee_by_sid()
# ======================================================================

class TestGetEmployeeBySid:
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_found(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        row = _employee_row()
        # fetchone calls: 1) employee row, 2) dept info
        mock_cursor.fetchone.side_effect = [
            row,
            _dept_row(),
        ]

        result = hr_service.get_employee_by_sid(300000001)
        assert result['success'] is True
        assert result['employee']['sid'] == 300000001

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = hr_service.get_employee_by_sid(999)
        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, hr_service):
        mock_get_conn.return_value = None
        result = hr_service.get_employee_by_sid(1)
        assert result['success'] is False


# ======================================================================
# create_employee()
# ======================================================================

class TestCreateEmployee:
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success_office(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        # Sequence of fetchone calls inside create_employee:
        # 1) _resolve_employee_type_id -> (10001,)
        # 2) _resolve_contract_type_id -> (20001,)
        # 3) INSERT RETURNING sid -> (300000002,)
        mock_cursor.fetchone.side_effect = [
            (10001,),     # employee_type_id
            (20001,),     # contract_type_id
            (300000002,), # RETURNING sid
        ]

        # After create, it calls get_employee_by_sid which opens a NEW connection
        # So we mock get_postgres_connection to return a second connection
        mock_conn2, mock_cursor2 = _mock_conn_cursor()
        mock_get_conn.side_effect = [mock_conn, mock_conn2]

        row = _employee_row(sid=300000002, employee_code='MQ028')
        mock_cursor2.fetchone.side_effect = [row, _dept_row()]

        result = hr_service.create_employee(
            employee_code='MQ028',
            full_name='New Employee',
            employee_type='OFFICE',
            contract='PERMANENT',
            department_id=400000001,
            join_date='2024-06-01',
            email='new@test.com',
        )

        assert result['success'] is True
        assert result['employee']['sid'] == 300000002

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_invalid_employee_type(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None  # type not found

        result = hr_service.create_employee(
            employee_code='X', full_name='X', employee_type='INVALID',
            contract='PERMANENT', department_id=1, join_date='2024-01-01',
        )
        assert result['success'] is False
        assert 'Invalid employee_type' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_invalid_contract_type(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (10001,),  # employee_type valid
            None,      # contract type not found
        ]

        result = hr_service.create_employee(
            employee_code='X', full_name='X', employee_type='OFFICE',
            contract='INVALID', department_id=1, join_date='2024-01-01',
            email='x@y.com',
        )
        assert result['success'] is False
        assert 'Invalid contract' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_store_employee_missing_store_id(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (10002,),  # employee_type STORE
            (20001,),  # contract
        ]

        result = hr_service.create_employee(
            employee_code='X', full_name='X', employee_type='STORE',
            contract='PERMANENT', department_id=1, join_date='2024-01-01',
        )
        assert result['success'] is False
        assert 'store_id is required' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_office_employee_missing_email(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (10001,),  # employee_type OFFICE
            (20001,),  # contract
        ]

        result = hr_service.create_employee(
            employee_code='X', full_name='X', employee_type='OFFICE',
            contract='PERMANENT', department_id=1, join_date='2024-01-01',
        )
        assert result['success'] is False
        assert 'email is required' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, hr_service):
        mock_get_conn.return_value = None
        result = hr_service.create_employee(
            employee_code='X', full_name='X', employee_type='OFFICE',
            contract='PERMANENT', department_id=1, join_date='2024-01-01',
            email='x@y.com',
        )
        assert result['success'] is False

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_duplicate_employee_code(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (10001,),  # employee_type
            (20001,),  # contract
        ]
        mock_cursor.execute.side_effect = [
            None, None,  # _resolve calls
            Exception('duplicate key value violates unique constraint employee_code'),
        ]

        result = hr_service.create_employee(
            employee_code='MQ027', full_name='Dup', employee_type='OFFICE',
            contract='PERMANENT', department_id=1, join_date='2024-01-01',
            email='dup@test.com',
        )
        assert result['success'] is False
        assert 'already exists' in result['error'].lower()


# ======================================================================
# update_employee()
# ======================================================================

class TestUpdateEmployee:
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        # fetchone calls:
        # 1) employee exists check -> (300000001,)
        # 2) UPDATE RETURNING sid -> (300000001,)
        mock_cursor.fetchone.side_effect = [
            (300000001,),  # employee exists
            (300000001,),  # UPDATE RETURNING
        ]

        # get_employee_by_sid opens new connection
        mock_conn2, mock_cursor2 = _mock_conn_cursor()
        mock_get_conn.side_effect = [mock_conn, mock_conn2]

        row = _employee_row(full_name='Updated Name')
        mock_cursor2.fetchone.side_effect = [row, _dept_row()]

        result = hr_service.update_employee(300000001, {'full_name': 'Updated Name'})
        assert result['success'] is True
        assert result['employee']['full_name'] == 'Updated Name'

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_employee_not_found(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None  # employee doesn't exist

        result = hr_service.update_employee(999, {'full_name': 'X'})
        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_no_valid_fields(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (300000001,)  # employee exists

        result = hr_service.update_employee(300000001, {'invalid_field': 'value'})
        assert result['success'] is False
        assert 'No valid fields' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, hr_service):
        mock_get_conn.return_value = None
        result = hr_service.update_employee(1, {'full_name': 'X'})
        assert result['success'] is False


# ======================================================================
# delete_employee()
# ======================================================================

class TestDeleteEmployee:
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (300000001,)

        result = hr_service.delete_employee(300000001)
        assert result['success'] is True
        mock_conn.commit.assert_called_once()

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = hr_service.delete_employee(999)
        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, hr_service):
        mock_get_conn.return_value = None
        result = hr_service.delete_employee(1)
        assert result['success'] is False


# ======================================================================
# get_employee_types()
# ======================================================================

class TestGetEmployeeTypes:
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = [
            (10001, 'OFFICE', 'Office', True),
            (10002, 'STORE', 'Store', True),
        ]

        result = hr_service.get_employee_types()
        assert result['success'] is True
        assert len(result['employee_types']) == 2
        assert result['employee_types'][0]['code'] == 'OFFICE'

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, hr_service):
        mock_get_conn.return_value = None
        result = hr_service.get_employee_types()
        assert result['success'] is False


# ======================================================================
# get_contract_types()
# ======================================================================

class TestGetContractTypes:
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn, hr_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = [
            (20001, 'PERMANENT', 'Permanent', True),
            (20002, 'INTERN', 'Intern', True),
            (20003, 'PROBATION', 'Probation', True),
        ]

        result = hr_service.get_contract_types()
        assert result['success'] is True
        assert len(result['contract_types']) == 3
        assert result['contract_types'][2]['code'] == 'PROBATION'

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, hr_service):
        mock_get_conn.return_value = None
        result = hr_service.get_contract_types()
        assert result['success'] is False
