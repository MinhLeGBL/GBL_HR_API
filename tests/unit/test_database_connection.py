import pytest
import platform
import os
from unittest.mock import patch, MagicMock, mock_open
import oracledb

from app.database.connection import get_oracle_connection


class TestOracleConnection:
    
    @patch('app.database.connection.oracledb')
    @patch('app.database.connection.platform')
    @patch('app.database.connection.os')
    def test_get_oracle_connection_success_linux(self, mock_os, mock_platform, mock_oracledb):
        """Test successful connection on Linux"""
        # Setup mocks
        mock_platform.system.return_value = "Linux"
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_oracledb.connect.return_value = mock_connection
        
        # Call function
        result = get_oracle_connection()
        
        # Assertions
        assert result == mock_connection
        mock_oracledb.connect.assert_called_once_with(
            user='reportuser',
            password='report',
            host='118.107.78.35',
            port='1521',
            service_name='rproods'
        )
        mock_cursor.execute.assert_called_once_with("ALTER SESSION SET CURRENT_SCHEMA = RPS")
        mock_oracledb.init_oracle_client.assert_not_called()

    @patch('app.database.connection.oracledb')
    @patch('app.database.connection.platform')
    @patch('app.database.connection.os')
    def test_get_oracle_connection_success_macos(self, mock_os, mock_platform, mock_oracledb):
        """Test successful connection on macOS with Oracle client"""
        # Setup mocks
        mock_platform.system.return_value = "Darwin"
        mock_platform.machine.return_value = "x86_64"
        mock_os.environ.get.return_value = "/Users/test"
        mock_os.path.exists.return_value = True
        
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_oracledb.connect.return_value = mock_connection
        
        # Call function
        result = get_oracle_connection()
        
        # Assertions
        assert result == mock_connection
        mock_oracledb.init_oracle_client.assert_called_once_with(
            lib_dir="/Users/test/Downloads/instantclient_19_16"
        )

    @patch('app.database.connection.oracledb')
    @patch('app.database.connection.platform')
    @patch('app.database.connection.os')
    def test_get_oracle_connection_client_not_found(self, mock_os, mock_platform, mock_oracledb):
        """Test when Oracle client directory doesn't exist"""
        # Setup mocks
        mock_platform.system.return_value = "Darwin"
        mock_platform.machine.return_value = "x86_64"
        mock_os.environ.get.return_value = "/Users/test"
        mock_os.path.exists.return_value = False
        
        # Call function
        result = get_oracle_connection()
        
        # Assertions
        assert result is None
        mock_oracledb.init_oracle_client.assert_not_called()
        mock_oracledb.connect.assert_not_called()

    @patch('app.database.connection.oracledb')
    @patch('app.database.connection.platform')
    def test_get_oracle_connection_database_error(self, mock_platform, mock_oracledb):
        """Test database connection error"""
        # Setup mocks
        mock_platform.system.return_value = "Linux"
        # Create a DatabaseError class that inherits from Exception
        class MockDatabaseError(Exception):
            pass
        
        mock_oracledb.DatabaseError = MockDatabaseError
        mock_oracledb.connect.side_effect = MockDatabaseError("Connection failed")
        
        # Call function
        result = get_oracle_connection()
        
        # Assertions
        assert result is None

    @patch('app.database.connection.oracledb')
    @patch('app.database.connection.platform')
    def test_get_oracle_connection_schema_error(self, mock_platform, mock_oracledb):
        """Test when schema setting fails"""
        # Setup mocks
        mock_platform.system.return_value = "Linux"
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("Schema error")
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_oracledb.connect.return_value = mock_connection
        
        # Call function
        result = get_oracle_connection()
        
        # Assertions
        assert result is None
        mock_connection.close.assert_called_once()

    @patch('app.database.connection.oracledb')
    @patch('app.database.connection.platform')
    @patch('app.database.connection.os')
    def test_get_oracle_connection_windows(self, mock_os, mock_platform, mock_oracledb):
        """Test Oracle client path on Windows"""
        # Setup mocks
        mock_platform.system.return_value = "Windows"
        mock_os.path.exists.return_value = True
        
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_connection.cursor.return_value.__enter__.return_value = mock_cursor
        mock_oracledb.connect.return_value = mock_connection
        
        # Call function
        result = get_oracle_connection()
        
        # Assertions
        assert result == mock_connection
        mock_oracledb.init_oracle_client.assert_called_once_with(
            lib_dir=r"W:\Oracle\instantclient_23_4"
        )