"""
Unit tests for StoreService
Tests CRUD operations for stores with mocked PostgreSQL connection.
"""
import pytest
from unittest.mock import patch, MagicMock
from app.services.store_service import StoreService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_conn_cursor():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor


def _make_created_at():
    dt = MagicMock()
    dt.isoformat.return_value = '2024-06-15T10:30:00'
    return dt


@pytest.fixture()
def store_service():
    return StoreService()


# ======================================================================
# get_all_stores()
# ======================================================================

class TestGetAllStores:
    @patch('app.services.store_service.get_postgres_connection')
    def test_success_with_stores(self, mock_get_conn, store_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        dt = _make_created_at()
        mock_cursor.fetchall.return_value = [
            (200000001, 'S001', 'Store Alpha', 'RP001', dt),
            (200000002, 'S002', 'Store Beta', None, dt),
        ]

        result = store_service.get_all_stores()
        assert result['success'] is True
        assert len(result['stores']) == 2
        assert result['stores'][0]['store_code'] == 'S001'
        assert result['stores'][0]['created_at'] == '2024-06-15T10:30:00'
        assert result['stores'][1]['store_rp_sid'] is None

    @patch('app.services.store_service.get_postgres_connection')
    def test_success_empty(self, mock_get_conn, store_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = []

        result = store_service.get_all_stores()
        assert result['success'] is True
        assert result['stores'] == []

    @patch('app.services.store_service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, store_service):
        mock_get_conn.return_value = None
        result = store_service.get_all_stores()
        assert result['success'] is False
        assert 'connect' in result['error'].lower()

    @patch('app.services.store_service.get_postgres_connection')
    def test_db_exception(self, mock_get_conn, store_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.side_effect = Exception('DB timeout')

        result = store_service.get_all_stores()
        assert result['success'] is False
        assert 'DB timeout' in result['error']


# ======================================================================
# get_store_by_id()
# ======================================================================

class TestGetStoreById:
    @patch('app.services.store_service.get_postgres_connection')
    def test_found(self, mock_get_conn, store_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        dt = _make_created_at()
        mock_cursor.fetchone.return_value = (200000001, 'S001', 'Store Alpha', 'RP001', dt)

        result = store_service.get_store_by_id(200000001)
        assert result is not None
        assert result['id'] == 200000001
        assert result['store_code'] == 'S001'
        assert result['created_at'] == '2024-06-15T10:30:00'

    @patch('app.services.store_service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, store_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = store_service.get_store_by_id(999)
        assert result is None

    @patch('app.services.store_service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, store_service):
        mock_get_conn.return_value = None
        result = store_service.get_store_by_id(1)
        assert result is None

    @patch('app.services.store_service.get_postgres_connection')
    def test_db_exception(self, mock_get_conn, store_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = Exception('Query error')

        result = store_service.get_store_by_id(1)
        assert result is None


# ======================================================================
# create_store()
# ======================================================================

class TestCreateStore:
    @patch('app.services.store_service.get_postgres_connection')
    def test_success(self, mock_get_conn, store_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        dt = _make_created_at()
        mock_cursor.fetchone.return_value = (200000003, 'S003', 'Store Gamma', 'RP003', dt)

        result = store_service.create_store('S003', 'Store Gamma', 'RP003')
        assert result['success'] is True
        assert result['store']['store_code'] == 'S003'
        assert result['store']['id'] == 200000003
        mock_conn.commit.assert_called_once()

    @patch('app.services.store_service.get_postgres_connection')
    def test_success_without_rp_sid(self, mock_get_conn, store_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        dt = _make_created_at()
        mock_cursor.fetchone.return_value = (200000004, 'S004', 'Store Delta', None, dt)

        result = store_service.create_store('S004', 'Store Delta')
        assert result['success'] is True
        assert result['store']['store_rp_sid'] is None

    @patch('app.services.store_service.get_postgres_connection')
    def test_duplicate_store_code(self, mock_get_conn, store_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = Exception(
            'duplicate key value violates unique constraint "stores_store_code_key"'
        )

        result = store_service.create_store('S001', 'Duplicate')
        assert result['success'] is False
        assert 'already exists' in result['error'].lower()

    @patch('app.services.store_service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, store_service):
        mock_get_conn.return_value = None
        result = store_service.create_store('S005', 'Fail')
        assert result['success'] is False
        assert 'connect' in result['error'].lower()

    @patch('app.services.store_service.get_postgres_connection')
    def test_generic_db_error(self, mock_get_conn, store_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = Exception('Some other error')

        result = store_service.create_store('S006', 'Error')
        assert result['success'] is False
        assert 'Some other error' in result['error']


# ======================================================================
# init_database()
# ======================================================================

class TestInitDatabase:
    @patch('app.services.store_service.get_postgres_connection')
    def test_success(self, mock_get_conn, store_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        result = store_service.init_database()
        assert result['success'] is True
        assert 'initialized' in result['message'].lower()
        mock_conn.commit.assert_called_once()

    @patch('app.services.store_service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, store_service):
        mock_get_conn.return_value = None
        result = store_service.init_database()
        assert result['success'] is False

    @patch('app.services.store_service.get_postgres_connection')
    def test_db_exception(self, mock_get_conn, store_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.execute.side_effect = Exception('SQL error')

        result = store_service.init_database()
        assert result['success'] is False
        assert 'SQL error' in result['error']
        mock_conn.rollback.assert_called_once()
