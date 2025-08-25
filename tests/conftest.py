"""
Pytest configuration and fixtures
"""
import pytest
import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture
def sample_database_config():
    """Sample database configuration for testing"""
    return {
        'host': 'test-host',
        'port': '1521',
        'service_name': 'test-service',
        'username': 'test-user',
        'password': 'test-password',
        'encoding': 'UTF-8'
    }


@pytest.fixture
def mock_oracle_connection():
    """Mock Oracle connection for testing"""
    from unittest.mock import MagicMock
    
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None
    
    return conn, cursor