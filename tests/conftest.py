import pytest
from unittest.mock import MagicMock, patch
from app.main import create_app


@pytest.fixture
def app():
    """Create Flask test app."""
    app = create_app()
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    """Create Flask test client."""
    return app.test_client()


@pytest.fixture
def mock_pg_connection():
    """Mock PostgreSQL connection and cursor."""
    with patch('app.database.connection.get_postgres_connection') as mock_get_conn:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        yield mock_conn, mock_cursor


@pytest.fixture
def mock_oracle_connection():
    """Mock Oracle database connection and cursor."""
    with patch('app.database.connection.get_oracle_connection') as mock_get_conn:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn
        yield mock_conn, mock_cursor


@pytest.fixture
def mock_auth_token():
    """Mock a valid JWT token and user for protected endpoints."""
    user_data = {
        'sid': 1,
        'email': 'test@example.com',
        'role': 'admin',
        'role_code': 'ADMIN',
        'full_name': 'Test User',
        'department_id': 1,
        'department_code': 'IT'
    }
    with patch('app.services.auth_service.AuthService.verify_token') as mock_verify:
        mock_verify.return_value = user_data
        yield user_data
