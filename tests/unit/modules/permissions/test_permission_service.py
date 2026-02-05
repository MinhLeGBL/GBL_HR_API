"""
Unit tests for PermissionService
Tests department CRUD, section management, and permission queries
with mocked PostgreSQL connection.
"""
import pytest
from unittest.mock import patch, MagicMock
from app.modules.permissions.service import PermissionService


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
    return dt


@pytest.fixture()
def perm_service():
    return PermissionService()


# ======================================================================
# get_all_departments()
# ======================================================================

class TestGetAllDepartments:
    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_success(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        dt = _make_dt()
        mock_cursor.fetchall.return_value = [
            (400000001, 'HR', 'Human Resources', 'HR Department', True, dt),
            (400000002, 'IT', 'Information Technology', 'IT Department', True, dt),
        ]

        result = perm_service.get_all_departments()
        assert result['success'] is True
        assert len(result['departments']) == 2
        assert result['departments'][0]['code'] == 'HR'
        assert result['departments'][0]['created_at'] == '2024-06-15T10:30:00'

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_empty(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = []

        result = perm_service.get_all_departments()
        assert result['success'] is True
        assert result['departments'] == []

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, perm_service):
        mock_get_conn.return_value = None
        result = perm_service.get_all_departments()
        assert result['success'] is False
        assert 'connect' in result['error'].lower()

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_db_exception(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.side_effect = Exception('query failed')

        result = perm_service.get_all_departments()
        assert result['success'] is False
        assert 'query failed' in result['error']


# ======================================================================
# get_department_by_id()
# ======================================================================

class TestGetDepartmentById:
    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_found(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (
            400000001, 'HR', 'Human Resources', 'HR Department', True,
        )

        result = perm_service.get_department_by_id(400000001)
        assert result is not None
        assert result['id'] == 400000001
        assert result['code'] == 'HR'

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = perm_service.get_department_by_id(999)
        assert result is None

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, perm_service):
        mock_get_conn.return_value = None
        result = perm_service.get_department_by_id(1)
        assert result is None


# ======================================================================
# create_department()
# ======================================================================

class TestCreateDepartment:
    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_success(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (
            400000003, 'MKT', 'Marketing', 'Marketing Department', True,
        )

        result = perm_service.create_department('mkt', 'Marketing', 'Marketing Department')
        assert result['success'] is True
        assert result['department']['code'] == 'MKT'
        mock_conn.commit.assert_called_once()

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_duplicate_key(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = Exception(
            'duplicate key value violates unique constraint "departments_code_key"'
        )

        result = perm_service.create_department('HR', 'Human Resources')
        assert result['success'] is False
        assert 'already exists' in result['error'].lower()

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, perm_service):
        mock_get_conn.return_value = None
        result = perm_service.create_department('X', 'X Dept')
        assert result['success'] is False

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_generic_exception(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = Exception('some other error')

        result = perm_service.create_department('NEW', 'New Dept')
        assert result['success'] is False
        assert 'some other error' in result['error']


# ======================================================================
# update_department()
# ======================================================================

class TestUpdateDepartment:
    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_success(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        # UPDATE RETURNING id
        mock_cursor.fetchone.return_value = (400000001,)

        # get_department_by_id opens new connection
        mock_conn2, mock_cursor2 = _mock_conn_cursor()
        mock_get_conn.side_effect = [mock_conn, mock_conn2]
        mock_cursor2.fetchone.return_value = (
            400000001, 'HR', 'Updated HR', 'Updated desc', True,
        )

        result = perm_service.update_department(400000001, {'name': 'Updated HR'})
        assert result['success'] is True
        assert result['department']['name'] == 'Updated HR'

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None  # UPDATE returns nothing

        result = perm_service.update_department(999, {'name': 'X'})
        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_no_valid_fields(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        result = perm_service.update_department(400000001, {'invalid_field': 'x'})
        assert result['success'] is False
        assert 'No valid fields' in result['error']

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, perm_service):
        mock_get_conn.return_value = None
        result = perm_service.update_department(1, {'name': 'X'})
        assert result['success'] is False


# ======================================================================
# delete_department()
# ======================================================================

class TestDeleteDepartment:
    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_success(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        # fetchone calls:
        # 1) COUNT(*) users -> (0,)   (no assigned users)
        # 2) DELETE RETURNING -> (400000001,)
        mock_cursor.fetchone.side_effect = [
            (0,),           # user count
            (400000001,),   # DELETE RETURNING
        ]

        result = perm_service.delete_department(400000001)
        assert result['success'] is True
        assert 'deleted' in result['message'].lower()
        mock_conn.commit.assert_called_once()

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (0,),   # user count
            None,   # DELETE returns nothing
        ]

        result = perm_service.delete_department(999)
        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_has_users(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (3,)  # 3 users assigned

        result = perm_service.delete_department(400000001)
        assert result['success'] is False
        assert 'assigned users' in result['error'].lower()

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, perm_service):
        mock_get_conn.return_value = None
        result = perm_service.delete_department(1)
        assert result['success'] is False


# ======================================================================
# get_all_sections()
# ======================================================================

class TestGetAllSections:
    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_success(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchall.return_value = [
            (1, 'DASHBOARD', 'Dashboard', 'Main dashboard', True,
             'MAIN', 'Main', ['admin', 'manager', 'staff']),
            (2, 'COMMISSION_DATA', 'Commission Data', 'Commission data', True,
             'COMMISSION', 'Commission', ['admin', 'manager']),
        ]

        result = perm_service.get_all_sections()
        assert result['success'] is True
        assert len(result['sections']) == 2
        assert result['sections'][0]['code'] == 'DASHBOARD'
        assert 'admin' in result['sections'][0]['allowed_roles']

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, perm_service):
        mock_get_conn.return_value = None
        result = perm_service.get_all_sections()
        assert result['success'] is False


# ======================================================================
# get_user_permissions()
# ======================================================================

class TestGetUserPermissions:
    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_admin_sees_all(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        # Admin query returns all groups/sections
        mock_cursor.fetchall.return_value = [
            ('MAIN', 'Main', 'DASHBOARD', 'Dashboard'),
            ('COMMISSION', 'Commission', 'COMMISSION_DATA', 'Commission Data'),
            ('ADMINISTRATION', 'Administration', 'USER_MANAGEMENT', 'User Management'),
        ]

        result = perm_service.get_user_permissions('admin', department_id=400000001)
        assert isinstance(result, list)
        assert len(result) == 3
        group_codes = [g['group_code'] for g in result]
        assert 'MAIN' in group_codes
        assert 'ADMINISTRATION' in group_codes

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_non_admin_filtered(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        # First fetchone: resolve department_code from department_id
        mock_cursor.fetchone.return_value = ('HR',)

        # Filtered query returns fewer groups
        mock_cursor.fetchall.return_value = [
            ('MAIN', 'Main', 'DASHBOARD', 'Dashboard'),
            ('COMMISSION', 'Commission', 'COMMISSION_DATA', 'Commission Data'),
        ]

        result = perm_service.get_user_permissions('staff', department_id=400000001)
        assert isinstance(result, list)
        assert len(result) == 2

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_with_department_code(self, mock_get_conn, perm_service):
        """When department_code is provided, no need to resolve from ID."""
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchall.return_value = [
            ('MAIN', 'Main', 'DASHBOARD', 'Dashboard'),
        ]

        result = perm_service.get_user_permissions(
            'manager', department_code='HR',
        )
        assert isinstance(result, list)
        assert len(result) == 1

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, perm_service):
        mock_get_conn.return_value = None
        result = perm_service.get_user_permissions('admin')
        assert result == []

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_db_exception_returns_empty(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.side_effect = Exception('DB error')

        result = perm_service.get_user_permissions('admin')
        assert result == []


# ======================================================================
# update_section_roles()
# ======================================================================

class TestUpdateSectionRoles:
    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_success(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (1,)  # section found

        result = perm_service.update_section_roles('DASHBOARD', ['admin', 'manager'])
        assert result['success'] is True
        assert 'updated' in result['message'].lower()
        mock_conn.commit.assert_called_once()

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_section_not_found(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = perm_service.update_section_roles('NONEXISTENT', ['admin'])
        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_filters_invalid_roles(self, mock_get_conn, perm_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (1,)

        result = perm_service.update_section_roles(
            'DASHBOARD', ['admin', 'invalid_role', 'staff']
        )
        assert result['success'] is True
        # Should have called INSERT for admin and staff only (after DELETE)
        # The execute calls: SELECT + DELETE + INSERT(admin) + INSERT(staff)
        # (invalid_role is skipped)
        insert_calls = [
            c for c in mock_cursor.execute.call_args_list
            if 'INSERT INTO section_permissions' in str(c)
        ]
        assert len(insert_calls) == 2

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, perm_service):
        mock_get_conn.return_value = None
        result = perm_service.update_section_roles('DASHBOARD', ['admin'])
        assert result['success'] is False
