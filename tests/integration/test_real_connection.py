import pytest
from app.database.connection import get_oracle_connection


class TestRealOracleConnection:
    """Integration tests for real Oracle database connection"""
    
    @pytest.mark.integration
    def test_real_oracle_connection(self):
        """Test actual connection to Oracle database"""
        conn = get_oracle_connection()
        
        if conn is None:
            pytest.skip("Oracle connection failed - check database availability")
        
        try:
            # Test basic connection
            assert conn is not None, "Connection should not be None"
            
            # Test simple query
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM DUAL")
                result = cursor.fetchone()
                assert result is not None, "Should be able to execute simple query"
                assert result[0] == 1, "Query result should be 1"
                
            # Test schema is set correctly
            with conn.cursor() as cursor:
                cursor.execute("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM DUAL")
                schema = cursor.fetchone()[0]
                assert schema == 'RPS', f"Expected schema 'RPS', got '{schema}'"
                
        finally:
            conn.close()
    
    @pytest.mark.integration
    def test_connection_properties(self):
        """Test connection properties and metadata"""
        conn = get_oracle_connection()
        
        if conn is None:
            pytest.skip("Oracle connection failed - check database availability")
            
        try:
            # Test connection version info
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM V$VERSION WHERE ROWNUM = 1")
                version = cursor.fetchone()
                assert version is not None, "Should be able to get Oracle version"
                
            # Test user info
            with conn.cursor() as cursor:
                cursor.execute("SELECT USER FROM DUAL")
                user = cursor.fetchone()[0]
                assert user is not None, "Should be able to get current user"
                
        finally:
            conn.close()
    
    @pytest.mark.integration
    def test_multiple_connections(self):
        """Test creating multiple connections"""
        connections = []
        
        try:
            # Create multiple connections
            for i in range(3):
                conn = get_oracle_connection()
                if conn is None:
                    pytest.skip("Oracle connection failed - check database availability")
                connections.append(conn)
            
            # Test all connections work
            for i, conn in enumerate(connections):
                with conn.cursor() as cursor:
                    cursor.execute(f"SELECT {i+1} FROM DUAL")
                    result = cursor.fetchone()[0]
                    assert result == i+1, f"Connection {i} should work"
                    
        finally:
            # Clean up connections
            for conn in connections:
                if conn:
                    conn.close()