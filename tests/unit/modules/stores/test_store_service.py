"""
Unit tests for Store Service

Tests all service methods:
- init_database
- get_all_stores
- get_store_by_id
- create_store
- sync_stores_from_retailpro
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from app.modules.stores.service import StoreService


@pytest.fixture
def service():
    """Create StoreService instance."""
    return StoreService()


# ===========================================================================
# init_database
# ===========================================================================

class TestInitDatabase:
    """Tests for init_database method."""

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = service.init_database()

        assert result['success'] is True
        assert 'initialized' in result['message']
        mock_conn.commit.assert_called_once()

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_connection_failure(self, mock_get_conn, service):
        mock_get_conn.return_value = None

        result = service.init_database()

        assert result['success'] is False
        assert 'Failed to connect' in result['error']

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_exception_rollback(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.execute.side_effect = Exception('SQL error')

        result = service.init_database()

        assert result['success'] is False
        mock_conn.rollback.assert_called_once()


# ===========================================================================
# get_all_stores
# ===========================================================================

class TestGetAllStores:
    """Tests for get_all_stores method."""

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchall.return_value = [
            (200000001, 'RHN', 'Retail Ha Noi', 'store-sid-1', datetime(2024, 1, 1)),
            (200000002, 'RWD', 'Retail Westgate Diamond', 'store-sid-2', datetime(2024, 1, 1)),
        ]

        result = service.get_all_stores()

        assert result['success'] is True
        assert len(result['stores']) == 2
        assert result['stores'][0]['store_code'] == 'RHN'
        assert result['stores'][1]['store_code'] == 'RWD'

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_empty_list(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchall.return_value = []

        result = service.get_all_stores()

        assert result['success'] is True
        assert result['stores'] == []

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_connection_failure(self, mock_get_conn, service):
        mock_get_conn.return_value = None

        result = service.get_all_stores()

        assert result['success'] is False
        assert 'Failed to connect' in result['error']

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_exception_handling(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.execute.side_effect = Exception('Database error')

        result = service.get_all_stores()

        assert result['success'] is False
        assert 'Database error' in result['error']


# ===========================================================================
# get_store_by_id
# ===========================================================================

class TestGetStoreById:
    """Tests for get_store_by_id method."""

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.return_value = (
            200000001, 'RHN', 'Retail Ha Noi', 'store-sid-1', datetime(2024, 1, 1)
        )

        result = service.get_store_by_id(200000001)

        assert result is not None
        assert result['id'] == 200000001
        assert result['store_code'] == 'RHN'

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_not_found(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None

        result = service.get_store_by_id(999999999)

        assert result is None

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_connection_failure(self, mock_get_conn, service):
        mock_get_conn.return_value = None

        result = service.get_store_by_id(200000001)

        assert result is None

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_exception_returns_none(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.execute.side_effect = Exception('Query error')

        result = service.get_store_by_id(200000001)

        assert result is None


# ===========================================================================
# create_store
# ===========================================================================

class TestCreateStore:
    """Tests for create_store method."""

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_success(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        mock_cursor.fetchone.return_value = (
            200000003, 'NEW', 'New Store', 'store-sid-3', datetime(2024, 1, 1)
        )

        result = service.create_store(
            store_code='NEW',
            store_name='New Store',
            store_rp_sid='store-sid-3'
        )

        assert result['success'] is True
        assert result['store']['store_code'] == 'NEW'
        mock_conn.commit.assert_called_once()

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_connection_failure(self, mock_get_conn, service):
        mock_get_conn.return_value = None

        result = service.create_store('NEW', 'New Store')

        assert result['success'] is False
        assert 'Failed to connect' in result['error']

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_duplicate_store_code(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.execute.side_effect = Exception(
            'duplicate key value violates unique constraint "stores_store_code_key"'
        )

        result = service.create_store('RHN', 'Duplicate Store')

        assert result['success'] is False
        assert 'already exists' in result['error']
        mock_conn.rollback.assert_called_once()

    @patch('app.modules.stores.service.get_postgres_connection')
    def test_generic_error(self, mock_get_conn, service):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_cursor.execute.side_effect = Exception('Some other error')

        result = service.create_store('NEW', 'New Store')

        assert result['success'] is False
        assert 'Some other error' in result['error']


# ===========================================================================
# sync_stores_from_retailpro
# ===========================================================================

class TestSyncStoresFromRetailPro:
    """Tests for sync_stores_from_retailpro method."""

    @patch('app.modules.stores.service.get_postgres_connection')
    @patch('app.modules.stores.service.get_oracle_connection')
    def test_success_insert_new(self, mock_oracle_conn, mock_pg_conn, service):
        """Test inserting new stores from RetailPro."""
        # Oracle mock
        oracle_conn = MagicMock()
        oracle_cursor = MagicMock()
        oracle_conn.cursor.return_value = oracle_cursor
        mock_oracle_conn.return_value = oracle_conn

        oracle_cursor.fetchall.return_value = [
            (12345, 'NEW1', 'New Store 1'),
            (12346, 'NEW2', 'New Store 2'),
        ]

        # PostgreSQL mock
        pg_conn = MagicMock()
        pg_cursor = MagicMock()
        pg_conn.cursor.return_value = pg_cursor
        mock_pg_conn.return_value = pg_conn

        # Both stores don't exist
        pg_cursor.fetchone.side_effect = [None, None]

        result = service.sync_stores_from_retailpro()

        assert result['success'] is True
        assert result['inserted'] == 2
        assert result['updated'] == 0
        pg_conn.commit.assert_called_once()

    @patch('app.modules.stores.service.get_postgres_connection')
    @patch('app.modules.stores.service.get_oracle_connection')
    def test_success_update_existing(self, mock_oracle_conn, mock_pg_conn, service):
        """Test updating existing stores from RetailPro."""
        # Oracle mock
        oracle_conn = MagicMock()
        oracle_cursor = MagicMock()
        oracle_conn.cursor.return_value = oracle_cursor
        mock_oracle_conn.return_value = oracle_conn

        oracle_cursor.fetchall.return_value = [
            (12345, 'RHN', 'Updated Retail Ha Noi'),
        ]

        # PostgreSQL mock
        pg_conn = MagicMock()
        pg_cursor = MagicMock()
        pg_conn.cursor.return_value = pg_cursor
        mock_pg_conn.return_value = pg_conn

        # Store exists
        pg_cursor.fetchone.return_value = (200000001,)

        result = service.sync_stores_from_retailpro()

        assert result['success'] is True
        assert result['inserted'] == 0
        assert result['updated'] == 1

    @patch('app.modules.stores.service.get_postgres_connection')
    @patch('app.modules.stores.service.get_oracle_connection')
    def test_mixed_insert_update(self, mock_oracle_conn, mock_pg_conn, service):
        """Test mix of inserts and updates."""
        # Oracle mock
        oracle_conn = MagicMock()
        oracle_cursor = MagicMock()
        oracle_conn.cursor.return_value = oracle_cursor
        mock_oracle_conn.return_value = oracle_conn

        oracle_cursor.fetchall.return_value = [
            (12345, 'RHN', 'Retail Ha Noi'),      # Existing
            (12346, 'NEW', 'New Store'),          # New
            (12347, 'RWD', 'Retail Westgate'),    # Existing
        ]

        # PostgreSQL mock
        pg_conn = MagicMock()
        pg_cursor = MagicMock()
        pg_conn.cursor.return_value = pg_cursor
        mock_pg_conn.return_value = pg_conn

        # First and third exist, second doesn't
        pg_cursor.fetchone.side_effect = [
            (200000001,),  # RHN exists
            None,          # NEW doesn't exist
            (200000002,),  # RWD exists
        ]

        result = service.sync_stores_from_retailpro()

        assert result['success'] is True
        assert result['inserted'] == 1
        assert result['updated'] == 2

    @patch('app.modules.stores.service.get_oracle_connection')
    def test_oracle_connection_failure(self, mock_oracle_conn, service):
        mock_oracle_conn.return_value = None

        result = service.sync_stores_from_retailpro()

        assert result['success'] is False
        assert 'Failed to connect to RetailPro' in result['error']

    @patch('app.modules.stores.service.get_postgres_connection')
    @patch('app.modules.stores.service.get_oracle_connection')
    def test_postgres_connection_failure(self, mock_oracle_conn, mock_pg_conn, service):
        # Oracle works
        oracle_conn = MagicMock()
        oracle_cursor = MagicMock()
        oracle_conn.cursor.return_value = oracle_cursor
        mock_oracle_conn.return_value = oracle_conn
        oracle_cursor.fetchall.return_value = [(12345, 'RHN', 'Store')]

        # PostgreSQL fails
        mock_pg_conn.return_value = None

        result = service.sync_stores_from_retailpro()

        assert result['success'] is False
        assert 'Failed to connect to PostgreSQL' in result['error']

    @patch('app.modules.stores.service.get_postgres_connection')
    @patch('app.modules.stores.service.get_oracle_connection')
    def test_no_stores_in_retailpro(self, mock_oracle_conn, mock_pg_conn, service):
        """Test when no stores exist in RetailPro."""
        oracle_conn = MagicMock()
        oracle_cursor = MagicMock()
        oracle_conn.cursor.return_value = oracle_cursor
        mock_oracle_conn.return_value = oracle_conn
        oracle_cursor.fetchall.return_value = []  # No stores

        result = service.sync_stores_from_retailpro()

        assert result['success'] is True
        assert 'No stores found' in result['message']
        assert result['inserted'] == 0
        assert result['updated'] == 0

    @patch('app.modules.stores.service.get_postgres_connection')
    @patch('app.modules.stores.service.get_oracle_connection')
    def test_exception_rollback(self, mock_oracle_conn, mock_pg_conn, service):
        """Test rollback on exception."""
        # Oracle mock
        oracle_conn = MagicMock()
        oracle_cursor = MagicMock()
        oracle_conn.cursor.return_value = oracle_cursor
        mock_oracle_conn.return_value = oracle_conn
        oracle_cursor.fetchall.return_value = [(12345, 'RHN', 'Store')]

        # PostgreSQL mock - fails on cursor operation
        pg_conn = MagicMock()
        pg_cursor = MagicMock()
        pg_conn.cursor.return_value = pg_cursor
        mock_pg_conn.return_value = pg_conn
        pg_cursor.execute.side_effect = Exception('Database error')

        result = service.sync_stores_from_retailpro()

        assert result['success'] is False
        pg_conn.rollback.assert_called_once()
