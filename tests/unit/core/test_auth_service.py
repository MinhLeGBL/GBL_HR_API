"""
Unit tests for AuthService
Tests password hashing, JWT token management, and database-backed user operations.
All DB calls are mocked via get_postgres_connection.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Patch JWT_CONFIG before the module under test reads it at import time
# ---------------------------------------------------------------------------
_JWT_CFG = {
    'secret_key': 'test-secret-key-for-unit-tests',
    'algorithm': 'HS256',
    'access_token_expire_minutes': 480,
    'refresh_token_expire_days': 7,
}


@pytest.fixture()
def auth_service():
    """Create an AuthService instance with a deterministic JWT config."""
    with patch('app.core.auth.service.JWT_CONFIG', _JWT_CFG):
        from app.core.auth.service import AuthService
        yield AuthService()


# ======================================================================
# Password hashing (pure logic -- no DB mock needed)
# ======================================================================

class TestHashPassword:
    def test_returns_bcrypt_string(self, auth_service):
        hashed = auth_service.hash_password('my_password')
        assert isinstance(hashed, str)
        assert hashed.startswith('$2b$') or hashed.startswith('$2a$')

    def test_different_calls_produce_different_hashes(self, auth_service):
        h1 = auth_service.hash_password('same')
        h2 = auth_service.hash_password('same')
        assert h1 != h2  # different salts


class TestVerifyPassword:
    def test_correct_password_returns_true(self, auth_service):
        hashed = auth_service.hash_password('correct')
        assert auth_service.verify_password('correct', hashed) is True

    def test_wrong_password_returns_false(self, auth_service):
        hashed = auth_service.hash_password('correct')
        assert auth_service.verify_password('wrong', hashed) is False


# ======================================================================
# JWT token management (real encode/decode -- no DB mock needed)
# ======================================================================

class TestCreateAccessToken:
    def test_returns_string(self, auth_service):
        token = auth_service.create_access_token(100000001, 'a@b.com', 'admin')
        assert isinstance(token, str)

    def test_payload_round_trip(self, auth_service):
        token = auth_service.create_access_token(100000001, 'a@b.com', 'admin')
        payload = auth_service.verify_token(token)
        assert payload is not None
        assert payload['sid'] == 100000001
        assert payload['email'] == 'a@b.com'
        assert payload['role'] == 'admin'
        assert payload['type'] == 'access'


class TestCreateRefreshToken:
    def test_returns_string(self, auth_service):
        token = auth_service.create_refresh_token(100000001)
        assert isinstance(token, str)

    def test_payload_round_trip(self, auth_service):
        token = auth_service.create_refresh_token(100000001)
        payload = auth_service.verify_token(token)
        assert payload is not None
        assert payload['sid'] == 100000001
        assert payload['type'] == 'refresh'


class TestVerifyToken:
    def test_valid_token(self, auth_service):
        token = auth_service.create_access_token(1, 'x@y.com', 'staff')
        payload = auth_service.verify_token(token)
        assert payload is not None
        assert payload['email'] == 'x@y.com'

    def test_invalid_token_returns_none(self, auth_service):
        assert auth_service.verify_token('not.a.token') is None

    def test_expired_token_returns_none(self, auth_service):
        import jwt as pyjwt
        from datetime import timedelta
        expired_payload = {
            'sid': 1,
            'email': 'x@y.com',
            'role': 'staff',
            'exp': datetime.now(timezone.utc) - timedelta(hours=1),
            'type': 'access',
        }
        token = pyjwt.encode(expired_payload, _JWT_CFG['secret_key'], algorithm='HS256')
        assert auth_service.verify_token(token) is None


# ======================================================================
# Helper: build a mock connection / cursor pair
# ======================================================================

def _mock_conn_cursor():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor


# ======================================================================
# authenticate()
# ======================================================================

class TestAuthenticate:
    @patch('app.core.auth.service.get_postgres_connection')
    def test_success(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        hashed = auth_service.hash_password('pass123')
        join_dt = MagicMock()
        join_dt.isoformat.return_value = '2024-01-01'

        # cursor.fetchone() is called once for the user SELECT
        mock_cursor.fetchone.return_value = (
            100000001, 'a@b.com', hashed, 'Alice', 'ADMIN', True,
            400000001,                  # department_id
            400000001, 'IT', 'IT Dept', # dept fields
            300000001, 'MQ027', join_dt # employee fields
        )

        # PermissionService is imported locally inside authenticate(), so patch it
        # at its origin module.
        with patch('app.modules.permissions.service.PermissionService') as MockPermSvc:
            MockPermSvc.return_value.get_user_permissions.return_value = []
            result = auth_service.authenticate('a@b.com', 'pass123')

        assert result['success'] is True
        assert 'access_token' in result
        assert 'refresh_token' in result
        assert result['user']['email'] == 'a@b.com'
        assert result['user']['role'] == 'admin'

    @patch('app.core.auth.service.get_postgres_connection')
    def test_invalid_email(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = auth_service.authenticate('no@user.com', 'pass')
        assert result['success'] is False
        assert 'Invalid email or password' in result['error']

    @patch('app.core.auth.service.get_postgres_connection')
    def test_wrong_password(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        hashed = auth_service.hash_password('correct')
        mock_cursor.fetchone.return_value = (
            100000001, 'a@b.com', hashed, 'Alice', 'ADMIN', True,
            400000001, 400000001, 'IT', 'IT Dept', None, None, None,
        )

        result = auth_service.authenticate('a@b.com', 'wrong')
        assert result['success'] is False
        assert 'Invalid email or password' in result['error']

    @patch('app.core.auth.service.get_postgres_connection')
    def test_deactivated_user(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        hashed = auth_service.hash_password('pass')
        mock_cursor.fetchone.return_value = (
            100000001, 'a@b.com', hashed, 'Alice', 'ADMIN', False,
            400000001, 400000001, 'IT', 'IT Dept', None, None, None,
        )

        result = auth_service.authenticate('a@b.com', 'pass')
        assert result['success'] is False
        assert 'deactivated' in result['error'].lower()

    @patch('app.core.auth.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, auth_service):
        mock_get_conn.return_value = None
        result = auth_service.authenticate('a@b.com', 'pass')
        assert result['success'] is False
        assert 'connect' in result['error'].lower()


# ======================================================================
# create_user()
# ======================================================================

class TestCreateUser:
    @patch('app.core.auth.service.get_postgres_connection')
    def test_success(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        created_at = MagicMock()
        created_at.isoformat.return_value = '2024-01-01T00:00:00'

        # _resolve_role_id -> role id
        # email check -> None (no duplicate)
        # dept check -> (400000001,)
        # get_next_sid -> 100000002
        # INSERT -> none
        # get_user_by_sid_result: fetchone for the user
        mock_cursor.fetchone.side_effect = [
            (30001,),       # _resolve_role_id
            None,           # email duplicate check
            (400000001,),   # department check
            (100000002,),   # get_next_sid via nextval
            # get_user_by_sid_result SELECT
            (100000002, 'new@b.com', 'New User', 'STAFF', True,
             created_at, None,           # created_at, last_login
             400000001, 400000001, 'HR', 'Human Resources',  # dept fields
             None, None, None),          # employee fields
        ]

        result = auth_service.create_user(
            email='new@b.com',
            password='pass123',
            full_name='New User',
            role='STAFF',
            department_id=400000001,
        )

        assert result['success'] is True
        assert result['user']['email'] == 'new@b.com'

    @patch('app.core.auth.service.get_postgres_connection')
    def test_duplicate_email(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = [
            (30001,),        # _resolve_role_id
            (100000001,),    # email already exists
        ]

        result = auth_service.create_user('dup@b.com', 'pass', 'Dup')
        assert result['success'] is False
        assert 'already exists' in result['error'].lower()

    @patch('app.core.auth.service.get_postgres_connection')
    def test_invalid_role(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None  # role not found

        result = auth_service.create_user('x@y.com', 'p', 'X', role='INVALID')
        assert result['success'] is False
        assert 'Invalid role' in result['error']

    @patch('app.core.auth.service.get_postgres_connection')
    def test_db_failure(self, mock_get_conn, auth_service):
        mock_get_conn.return_value = None
        result = auth_service.create_user('x@y.com', 'p', 'X')
        assert result['success'] is False


# ======================================================================
# get_user_by_sid()
# ======================================================================

class TestGetUserBySid:
    @patch('app.core.auth.service.get_postgres_connection')
    def test_found(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        created_at = MagicMock()
        created_at.isoformat.return_value = '2024-01-01T00:00:00'

        mock_cursor.fetchone.return_value = (
            100000001, 'a@b.com', 'Alice', 'ADMIN', True,
            created_at, None,
            400000001, 400000001, 'IT', 'IT Dept',
            None, None, None,
        )

        result = auth_service.get_user_by_sid(100000001)
        assert result is not None
        assert result['sid'] == 100000001
        assert result['role'] == 'admin'

    @patch('app.core.auth.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = auth_service.get_user_by_sid(999)
        assert result is None

    @patch('app.core.auth.service.get_postgres_connection')
    def test_db_failure(self, mock_get_conn, auth_service):
        mock_get_conn.return_value = None
        result = auth_service.get_user_by_sid(1)
        assert result is None


# ======================================================================
# get_all_users()
# ======================================================================

class TestGetAllUsers:
    @patch('app.core.auth.service.get_postgres_connection')
    def test_success(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        created_at = MagicMock()
        created_at.isoformat.return_value = '2024-01-01T00:00:00'

        mock_cursor.fetchall.return_value = [
            (100000001, 'a@b.com', 'Alice', 'ADMIN', True,
             created_at, None,
             400000001, 400000001, 'IT', 'IT Dept',
             None, None, None),
        ]

        result = auth_service.get_all_users()
        assert result['success'] is True
        assert len(result['users']) == 1
        assert result['users'][0]['email'] == 'a@b.com'

    @patch('app.core.auth.service.get_postgres_connection')
    def test_empty(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = []

        result = auth_service.get_all_users()
        assert result['success'] is True
        assert result['users'] == []

    @patch('app.core.auth.service.get_postgres_connection')
    def test_db_failure(self, mock_get_conn, auth_service):
        mock_get_conn.return_value = None
        result = auth_service.get_all_users()
        assert result['success'] is False


# ======================================================================
# delete_user()
# ======================================================================

class TestDeleteUser:
    @patch('app.core.auth.service.get_postgres_connection')
    def test_success(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = (100000001,)

        result = auth_service.delete_user(100000001)
        assert result['success'] is True
        assert 'deleted' in result['message'].lower()

    @patch('app.core.auth.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = auth_service.delete_user(999)
        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.core.auth.service.get_postgres_connection')
    def test_db_failure(self, mock_get_conn, auth_service):
        mock_get_conn.return_value = None
        result = auth_service.delete_user(1)
        assert result['success'] is False


# ======================================================================
# change_password()
# ======================================================================

class TestChangePassword:
    @patch('app.core.auth.service.get_postgres_connection')
    def test_success(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        old_hash = auth_service.hash_password('old_pass')
        mock_cursor.fetchone.return_value = (old_hash,)

        result = auth_service.change_password(100000001, 'old_pass', 'new_pass')
        assert result['success'] is True
        assert 'changed' in result['message'].lower()

    @patch('app.core.auth.service.get_postgres_connection')
    def test_user_not_found(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = auth_service.change_password(999, 'old', 'new')
        assert result['success'] is False
        assert 'not found' in result['error'].lower()

    @patch('app.core.auth.service.get_postgres_connection')
    def test_wrong_old_password(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        old_hash = auth_service.hash_password('correct_old')
        mock_cursor.fetchone.return_value = (old_hash,)

        result = auth_service.change_password(100000001, 'wrong_old', 'new')
        assert result['success'] is False
        assert 'incorrect' in result['error'].lower()

    @patch('app.core.auth.service.get_postgres_connection')
    def test_db_failure(self, mock_get_conn, auth_service):
        mock_get_conn.return_value = None
        result = auth_service.change_password(1, 'a', 'b')
        assert result['success'] is False


# ======================================================================
# get_roles()
# ======================================================================

class TestGetRoles:
    @patch('app.core.auth.service.get_postgres_connection')
    def test_success(self, mock_get_conn, auth_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = [
            (30001, 'ADMIN', 'Admin', True),
            (30002, 'MANAGER', 'Manager', True),
            (30003, 'STAFF', 'Staff', True),
        ]

        result = auth_service.get_roles()
        assert result['success'] is True
        assert len(result['roles']) == 3
        assert result['roles'][0]['code'] == 'ADMIN'

    @patch('app.core.auth.service.get_postgres_connection')
    def test_db_failure(self, mock_get_conn, auth_service):
        mock_get_conn.return_value = None
        result = auth_service.get_roles()
        assert result['success'] is False
