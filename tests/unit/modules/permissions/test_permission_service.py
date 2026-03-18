"""
Unit tests for PermissionService (v2 flat permission model)

Tests:
- Department CRUD
- get_all_section_permissions
- update_section_access
- get_user_permissions_v2 (access resolution logic)
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from app.modules.permissions.service import PermissionService


@pytest.fixture
def service():
    return PermissionService()


# ===========================================================================
# Department Management
# ===========================================================================

class TestGetAllDepartments:

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        now = datetime(2026, 1, 1)
        mock_cursor.fetchall.return_value = [
            (400000001, 'HR', 'Human Resources', 'HR Dept', True, now),
            (400000002, 'IT', 'Information Technology', 'IT Dept', True, now),
        ]

        result = service.get_all_departments()

        assert result['success'] is True
        assert len(result['departments']) == 2
        assert result['departments'][0]['code'] == 'HR'

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_no_connection(self, mock_get_conn, service):
        mock_get_conn.return_value = None

        result = service.get_all_departments()

        assert result['success'] is False

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_db_error(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception('DB error')
        mock_get_conn.return_value = mock_conn

        result = service.get_all_departments()

        assert result['success'] is False
        assert 'DB error' in result['error']


class TestGetDepartmentById:

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_found(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (400000001, 'HR', 'Human Resources', 'HR Dept', True)

        result = service.get_department_by_id(400000001)

        assert result is not None
        assert result['code'] == 'HR'

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = service.get_department_by_id(999)

        assert result is None


class TestCreateDepartment:

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (400000003, 'MKT', 'Marketing', 'Marketing Dept', True)

        result = service.create_department(code='mkt', name='Marketing', description='Marketing Dept')

        assert result['success'] is True
        assert result['department']['code'] == 'MKT'
        mock_conn.commit.assert_called_once()

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_duplicate_code(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = Exception('duplicate key value violates unique constraint')

        result = service.create_department(code='HR', name='HR')

        assert result['success'] is False
        assert 'already exists' in result['error']


class TestDeleteDepartment:

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        # COUNT(*) for users check, then DELETE RETURNING
        mock_cursor.fetchone.side_effect = [(0,), (400000001,)]

        result = service.delete_department(400000001)

        assert result['success'] is True

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_has_users(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (3,)  # 3 users

        result = service.delete_department(400000001)

        assert result['success'] is False
        assert 'assigned users' in result['error']

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [(0,), None]  # no users, no dept

        result = service.delete_department(999)

        assert result['success'] is False
        assert 'not found' in result['error']


# ===========================================================================
# V2 Flat Permission Model
# ===========================================================================

class TestGetAllSectionPermissions:

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        # sections query
        mock_cursor.fetchall.side_effect = [
            [('COMMISSION_DATA', 'Commission Data')],  # sections
            [('HR',)],           # departments
            [('ADMIN',), ('MANAGER',)],  # roles
            [],                  # included users
            [],                  # excluded users
        ]

        result = service.get_all_section_permissions()

        assert result['success'] is True
        assert len(result['sections']) == 1
        section = result['sections'][0]
        assert section['section_code'] == 'COMMISSION_DATA'
        assert section['allowed_departments'] == ['HR']
        assert section['allowed_roles'] == ['ADMIN', 'MANAGER']
        assert section['included_users'] == []
        assert section['excluded_users'] == []

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_no_connection(self, mock_get_conn, service):
        mock_get_conn.return_value = None

        result = service.get_all_section_permissions()

        assert result['success'] is False


class TestUpdateSectionAccess:

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        # section exists check
        mock_cursor.fetchone.return_value = ('COMMISSION_DATA',)
        # dept validation
        mock_cursor.fetchall.side_effect = [
            [('HR',), ('ACC',)],   # valid depts
            [('ADMIN',)],          # valid roles
            [(100000001,)],        # valid user sids
        ]

        result = service.update_section_access(
            section_code='COMMISSION_DATA',
            allowed_departments=['HR', 'ACC'],
            allowed_roles=['ADMIN'],
            included_user_sids=[100000001],
            excluded_user_sids=[]
        )

        assert result['success'] is True
        mock_conn.commit.assert_called_once()

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_section_not_found(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = service.update_section_access(
            section_code='NONEXISTENT',
            allowed_departments=[], allowed_roles=[],
            included_user_sids=[], excluded_user_sids=[]
        )

        assert result['success'] is False
        assert 'not found' in result['error']

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_invalid_department(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.return_value = ('COMMISSION_DATA',)
        mock_cursor.fetchall.return_value = [('HR',)]  # only HR is valid

        result = service.update_section_access(
            section_code='COMMISSION_DATA',
            allowed_departments=['HR', 'INVALID'],
            allowed_roles=[], included_user_sids=[], excluded_user_sids=[]
        )

        assert result['success'] is False
        assert 'Invalid departments' in result['error']

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_invalid_role(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.return_value = ('COMMISSION_DATA',)
        mock_cursor.fetchall.side_effect = [
            [],    # no depts to validate (empty list)
            [],    # no valid roles returned
        ]

        result = service.update_section_access(
            section_code='COMMISSION_DATA',
            allowed_departments=[],
            allowed_roles=['BADROLE'],
            included_user_sids=[], excluded_user_sids=[]
        )

        assert result['success'] is False
        assert 'Invalid roles' in result['error']


class TestGetUserPermissionsV2:

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_admin_gets_all(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchall.return_value = [
            ('DASHBOARD', 'Dashboard'),
            ('COMMISSION_DATA', 'Commission Data'),
            ('ACCESS_MANAGEMENT', 'Access Management'),
        ]

        result = service.get_user_permissions_v2(user_sid=1, user_role='admin')

        assert len(result) == 3
        codes = [p['section_code'] for p in result]
        assert 'DASHBOARD' in codes
        assert 'COMMISSION_DATA' in codes
        assert 'ACCESS_MANAGEMENT' in codes

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_dashboard_always_included(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchall.side_effect = [
            [('DASHBOARD', 'Dashboard'), ('COMMISSION_DATA', 'Commission Data')],  # all sections
            [],   # exclusions
            [],   # inclusions
            [],   # dept sections
            [],   # role sections
        ]

        result = service.get_user_permissions_v2(
            user_sid=100, user_role='staff', department_code='MKT'
        )

        assert len(result) == 1
        assert result[0]['section_code'] == 'DASHBOARD'

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_exclusion_denies_access(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchall.side_effect = [
            [('DASHBOARD', 'Dashboard'), ('COMMISSION_DATA', 'Commission Data')],
            [('COMMISSION_DATA',)],  # excluded from COMMISSION_DATA
            [],   # inclusions
            [('COMMISSION_DATA',)],  # dept match
            [('COMMISSION_DATA',)],  # role match
        ]

        result = service.get_user_permissions_v2(
            user_sid=100, user_role='manager', department_code='HR'
        )

        codes = [p['section_code'] for p in result]
        assert 'DASHBOARD' in codes
        assert 'COMMISSION_DATA' not in codes

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_inclusion_grants_access(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchall.side_effect = [
            [('DASHBOARD', 'Dashboard'), ('COMMISSION_DATA', 'Commission Data')],
            [],                        # no exclusions
            [('COMMISSION_DATA',)],    # included in COMMISSION_DATA
            [],                        # no dept match
            [],                        # no role match
        ]

        result = service.get_user_permissions_v2(
            user_sid=100, user_role='staff', department_code='MKT'
        )

        codes = [p['section_code'] for p in result]
        assert 'COMMISSION_DATA' in codes

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_dept_and_role_match(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchall.side_effect = [
            [('DASHBOARD', 'Dashboard'), ('COMMISSION_DATA', 'Commission Data')],
            [],                        # no exclusions
            [],                        # no inclusions
            [('COMMISSION_DATA',)],    # dept match
            [('COMMISSION_DATA',)],    # role match
        ]

        result = service.get_user_permissions_v2(
            user_sid=100, user_role='manager', department_code='HR'
        )

        codes = [p['section_code'] for p in result]
        assert 'COMMISSION_DATA' in codes

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_dept_match_without_role_denied(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchall.side_effect = [
            [('DASHBOARD', 'Dashboard'), ('COMMISSION_DATA', 'Commission Data')],
            [],                        # no exclusions
            [],                        # no inclusions
            [('COMMISSION_DATA',)],    # dept match
            [],                        # NO role match
        ]

        result = service.get_user_permissions_v2(
            user_sid=100, user_role='staff', department_code='HR'
        )

        codes = [p['section_code'] for p in result]
        assert 'COMMISSION_DATA' not in codes

    @patch('app.modules.permissions.service.get_postgres_connection')
    def test_no_connection_returns_empty(self, mock_get_conn, service):
        mock_get_conn.return_value = None

        result = service.get_user_permissions_v2(user_sid=1, user_role='admin')

        assert result == []
