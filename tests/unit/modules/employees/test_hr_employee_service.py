"""
Unit tests for HR Employee Service

Tests all service methods:
- init_database
- get_all_employees
- get_employee_by_sid
- create_employee
- update_employee
- delete_employee
- sync_retailpro_data
- get_employee_types
- get_contract_types
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import date, datetime

from app.modules.employees.service import HREmployeeService


@pytest.fixture
def service():
    """Create HREmployeeService instance."""
    return HREmployeeService()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_cursor_with_employee_row():
    """Create a mock cursor that returns an employee row."""
    cursor = MagicMock()
    cursor.fetchone.return_value = (
        300000001,                   # sid
        'EMP001',                    # employee_code
        'Nguyen Van A',              # full_name
        10002,                       # employee_type_id
        'STORE',                     # employee_type (et.code)
        20001,                       # contract_type_id
        'PERMANENT',                 # contract (ct.code)
        1,                           # department_id
        1,                           # store_id
        None,                        # email
        None,                        # retailpro_username
        None,                        # retailpro_sid
        True,                        # is_active
        date(2024, 1, 15),           # join_date
        datetime(2024, 1, 15, 10, 0),  # created_at
        datetime(2024, 1, 15, 10, 0),  # updated_at
    )
    return cursor


# ===========================================================================
# get_all_employees
# ===========================================================================

class TestGetAllEmployees:
    """Tests for get_all_employees method."""

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        # Employee row
        mock_cursor.fetchall.return_value = [(
            300000001, 'EMP001', 'Nguyen Van A', 10002, 'STORE',
            20001, 'PERMANENT', 1, 1, None, None, None, True,
            date(2024, 1, 15), datetime(2024, 1, 15), datetime(2024, 1, 15)
        )]
        # Department info (called in _get_department_info)
        mock_cursor.fetchone.side_effect = [
            (1, 'RETAIL', 'Retail'),  # department
            (1, 'RHN', 'Retail Ha Noi', 'store-sid'),  # store
        ]

        result = service.get_all_employees()

        assert result['success'] is True
        assert len(result['employees']) == 1
        assert result['employees'][0]['employee_code'] == 'EMP001'

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_connection_failure(self, mock_get_conn, service):
        mock_get_conn.return_value = None

        result = service.get_all_employees()

        assert result['success'] is False
        assert 'Failed to connect' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_with_department_filter(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = []

        result = service.get_all_employees(department_code='HR')

        assert result['success'] is True
        # Verify the query was built with JOIN and WHERE clause
        call_args = mock_cursor.execute.call_args
        query = call_args[0][0]
        assert 'JOIN departments d' in query
        assert 'UPPER(d.code) = %s' in query

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_with_role_filter(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = []

        result = service.get_all_employees(role='manager')

        assert result['success'] is True
        call_args = mock_cursor.execute.call_args
        query = call_args[0][0]
        assert 'JOIN users u' in query
        assert 'JOIN roles r' in query

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_exception_handling(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.execute.side_effect = Exception('Database error')

        result = service.get_all_employees()

        assert result['success'] is False
        assert 'Database error' in result['error']


# ===========================================================================
# get_employee_by_sid
# ===========================================================================

class TestGetEmployeeBySid:
    """Tests for get_employee_by_sid method."""

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.side_effect = [
            # Employee row
            (300000001, 'EMP001', 'Nguyen Van A', 10002, 'STORE',
             20001, 'PERMANENT', 1, 1, None, None, None, True,
             date(2024, 1, 15), datetime(2024, 1, 15), datetime(2024, 1, 15)),
            # Department info
            (1, 'RETAIL', 'Retail'),
            # Store info
            (1, 'RHN', 'Retail Ha Noi', 'store-sid'),
        ]

        result = service.get_employee_by_sid(300000001)

        assert result['success'] is True
        assert result['employee']['sid'] == 300000001

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = service.get_employee_by_sid(999999999)

        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_connection_failure(self, mock_get_conn, service):
        mock_get_conn.return_value = None

        result = service.get_employee_by_sid(300000001)

        assert result['success'] is False


# ===========================================================================
# create_employee
# ===========================================================================

class TestCreateEmployee:
    """Tests for create_employee method."""

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        # Resolve type/contract IDs
        mock_cursor.fetchone.side_effect = [
            (10002,),  # employee_type_id for STORE
            (20001,),  # contract_type_id for PERMANENT
            (300000002,),  # RETURNING sid from INSERT
            # Then get_employee_by_sid calls:
            (300000002, 'EMP002', 'Test User', 10002, 'STORE',
             20001, 'PERMANENT', 1, 1, None, None, None, True,
             date(2024, 1, 15), datetime(2024, 1, 15), datetime(2024, 1, 15)),
            (1, 'RETAIL', 'Retail'),
            (1, 'RHN', 'Retail Ha Noi', 'store-sid'),
        ]

        result = service.create_employee(
            employee_code='EMP002',
            full_name='Test User',
            employee_type='store',
            contract='permanent',
            department_id=1,
            join_date='2024-01-15',
            store_id=1
        )

        assert result['success'] is True

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_invalid_employee_type(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None  # Type not found

        result = service.create_employee(
            employee_code='EMP002',
            full_name='Test User',
            employee_type='invalid_type',
            contract='permanent',
            department_id=1,
            join_date='2024-01-15'
        )

        assert result['success'] is False
        assert 'Invalid employee_type' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_invalid_contract(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (10002,),  # employee_type_id valid
            None,      # contract not found
        ]

        result = service.create_employee(
            employee_code='EMP002',
            full_name='Test User',
            employee_type='store',
            contract='invalid_contract',
            department_id=1,
            join_date='2024-01-15',
            store_id=1
        )

        assert result['success'] is False
        assert 'Invalid contract' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_store_employee_requires_store_id(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (10002,),  # employee_type_id for STORE
            (20001,),  # contract_type_id
        ]

        result = service.create_employee(
            employee_code='EMP002',
            full_name='Test User',
            employee_type='store',
            contract='permanent',
            department_id=1,
            join_date='2024-01-15',
            store_id=None  # Missing store_id
        )

        assert result['success'] is False
        assert 'store_id is required' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_office_employee_requires_email(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (10001,),  # employee_type_id for OFFICE
            (20001,),  # contract_type_id
        ]

        result = service.create_employee(
            employee_code='EMP002',
            full_name='Test User',
            employee_type='office',
            contract='permanent',
            department_id=1,
            join_date='2024-01-15',
            email=None  # Missing email
        )

        assert result['success'] is False
        assert 'email is required' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_duplicate_employee_code(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (10002,),  # employee_type_id
            (20001,),  # contract_type_id
        ]
        # Simulate unique constraint violation
        mock_cursor.execute.side_effect = [
            None, None,  # Type/contract lookups
            Exception('duplicate key value violates unique constraint "employees_employee_code_key"')
        ]

        result = service.create_employee(
            employee_code='EMP001',  # Duplicate
            full_name='Test User',
            employee_type='store',
            contract='permanent',
            department_id=1,
            join_date='2024-01-15',
            store_id=1
        )

        assert result['success'] is False
        assert 'already exists' in result['error']


# ===========================================================================
# update_employee
# ===========================================================================

class TestUpdateEmployee:
    """Tests for update_employee method."""

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.side_effect = [
            (300000001,),  # Employee exists check
            (300000001,),  # RETURNING sid from UPDATE
            # get_employee_by_sid calls:
            (300000001, 'EMP001', 'Updated Name', 10002, 'STORE',
             20001, 'PERMANENT', 1, 1, None, None, None, True,
             date(2024, 1, 15), datetime(2024, 1, 15), datetime(2024, 1, 15)),
            (1, 'RETAIL', 'Retail'),
            (1, 'RHN', 'Retail Ha Noi', 'store-sid'),
        ]

        result = service.update_employee(300000001, {'full_name': 'Updated Name'})

        assert result['success'] is True

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None  # Employee not found

        result = service.update_employee(999999999, {'full_name': 'Updated'})

        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_resolve_employee_type_code(self, mock_get_conn, service):
        """Test that employee_type code is resolved to ID."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.side_effect = [
            (300000001,),  # Employee exists
            (10001,),      # employee_type_id for OFFICE
            (300000001,),  # RETURNING sid
            # get_employee_by_sid:
            (300000001, 'EMP001', 'Test', 10001, 'OFFICE',
             20001, 'PERMANENT', 1, None, 'test@email.com', None, None, True,
             date(2024, 1, 15), datetime(2024, 1, 15), datetime(2024, 1, 15)),
            (1, 'RETAIL', 'Retail'),
            None,  # No store
        ]

        result = service.update_employee(300000001, {'employee_type': 'office'})

        assert result['success'] is True

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_invalid_employee_type(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (300000001,),  # Employee exists
            None,          # Type not found
        ]

        result = service.update_employee(300000001, {'employee_type': 'invalid'})

        assert result['success'] is False
        assert 'Invalid employee_type' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_no_valid_fields(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (300000001,)  # Employee exists

        result = service.update_employee(300000001, {'invalid_field': 'value'})

        assert result['success'] is False
        assert 'No valid fields' in result['error']


# ===========================================================================
# delete_employee
# ===========================================================================

class TestDeleteEmployee:
    """Tests for delete_employee method."""

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (300000001,)  # Deleted SID

        result = service.delete_employee(300000001)

        assert result['success'] is True
        mock_conn.commit.assert_called_once()

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = service.delete_employee(999999999)

        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_exception_rollback(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.execute.side_effect = Exception('FK violation')

        result = service.delete_employee(300000001)

        assert result['success'] is False
        mock_conn.rollback.assert_called_once()


# ===========================================================================
# sync_retailpro_data
# ===========================================================================

class TestSyncRetailProData:
    """Tests for sync_retailpro_data method."""

    @patch('app.modules.employees.service.get_oracle_connection')
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_pg_conn, mock_oracle_conn, service):
        # PostgreSQL mock
        pg_conn = MagicMock()
        pg_cursor = MagicMock()
        pg_conn.cursor.return_value = pg_cursor
        mock_pg_conn.return_value = pg_conn

        # Oracle mock
        oracle_conn = MagicMock()
        oracle_cursor = MagicMock()
        oracle_conn.cursor.return_value = oracle_cursor
        mock_oracle_conn.return_value = oracle_conn

        pg_cursor.fetchone.side_effect = [
            ('EMP001',),  # Get employee_code
            # After update, get_employee_by_sid calls:
            (300000001, 'EMP001', 'Nguyen Van A', 10002, 'STORE',
             20001, 'PERMANENT', 1, 1, None, 'NGUYEN_A', '708336140000166083', True,
             date(2024, 1, 15), datetime(2024, 1, 15), datetime(2024, 1, 15)),
            (1, 'RETAIL', 'Retail'),
            (1, 'RHN', 'Retail Ha Noi', 'store-sid'),
        ]

        oracle_cursor.fetchone.return_value = (
            708336140000166083,  # RETAILPRO_SID
            'NGUYEN_A'          # RETAILPRO_USERNAME
        )

        result = service.sync_retailpro_data(300000001)

        assert result['success'] is True
        assert result['employee']['retailpro_username'] == 'NGUYEN_A'

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_employee_not_found(self, mock_pg_conn, service):
        pg_conn = MagicMock()
        pg_cursor = MagicMock()
        pg_conn.cursor.return_value = pg_cursor
        mock_pg_conn.return_value = pg_conn
        pg_cursor.fetchone.return_value = None

        result = service.sync_retailpro_data(999999999)

        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.employees.service.get_oracle_connection')
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_no_retailpro_account(self, mock_pg_conn, mock_oracle_conn, service):
        pg_conn = MagicMock()
        pg_cursor = MagicMock()
        pg_conn.cursor.return_value = pg_cursor
        mock_pg_conn.return_value = pg_conn
        pg_cursor.fetchone.return_value = ('EMP001',)

        oracle_conn = MagicMock()
        oracle_cursor = MagicMock()
        oracle_conn.cursor.return_value = oracle_cursor
        mock_oracle_conn.return_value = oracle_conn
        oracle_cursor.fetchone.return_value = None  # No RetailPro account

        result = service.sync_retailpro_data(300000001)

        assert result['success'] is False
        assert 'No RetailPro account found' in result['error']

    @patch('app.modules.employees.service.get_oracle_connection')
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_oracle_connection_failure(self, mock_pg_conn, mock_oracle_conn, service):
        pg_conn = MagicMock()
        pg_cursor = MagicMock()
        pg_conn.cursor.return_value = pg_cursor
        mock_pg_conn.return_value = pg_conn
        pg_cursor.fetchone.return_value = ('EMP001',)

        mock_oracle_conn.return_value = None  # Oracle connection failed

        result = service.sync_retailpro_data(300000001)

        assert result['success'] is False
        assert 'Failed to connect to RetailPro' in result['error']

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_postgres_connection_failure(self, mock_pg_conn, service):
        mock_pg_conn.return_value = None

        result = service.sync_retailpro_data(300000001)

        assert result['success'] is False
        assert 'Failed to connect to PostgreSQL' in result['error']


# ===========================================================================
# get_employee_types
# ===========================================================================

class TestGetEmployeeTypes:
    """Tests for get_employee_types method."""

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchall.return_value = [
            (10001, 'OFFICE', 'Office', True),
            (10002, 'STORE', 'Store', True),
        ]

        result = service.get_employee_types()

        assert result['success'] is True
        assert len(result['employee_types']) == 2
        assert result['employee_types'][0]['code'] == 'OFFICE'

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_connection_failure(self, mock_get_conn, service):
        mock_get_conn.return_value = None

        result = service.get_employee_types()

        assert result['success'] is False


# ===========================================================================
# get_contract_types
# ===========================================================================

class TestGetContractTypes:
    """Tests for get_contract_types method."""

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchall.return_value = [
            (20001, 'PERMANENT', 'Permanent', True),
            (20002, 'INTERN', 'Intern', True),
            (20003, 'PROBATION', 'Probation', True),
        ]

        result = service.get_contract_types()

        assert result['success'] is True
        assert len(result['contract_types']) == 3

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_exception_handling(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.execute.side_effect = Exception('Table not found')

        result = service.get_contract_types()

        assert result['success'] is False
        assert 'Table not found' in result['error']
