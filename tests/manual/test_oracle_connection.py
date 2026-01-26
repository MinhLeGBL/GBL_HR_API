"""
Test script for Oracle database connection
"""
import os
import platform
from app.database.connection import get_oracle_connection

def test_oracle_connection():
    print("=" * 60)
    print("Oracle Database Connection Test")
    print("=" * 60)

    # Check Oracle Instant Client directory
    print("\n1. Checking Oracle Instant Client...")
    if platform.system() == "Windows":
        client_dir = r"E:\Oracle\instantclient_23_0"
        if os.path.exists(client_dir):
            print(f"   [OK] Oracle Instant Client found at: {client_dir}")
        else:
            print(f"   [FAIL] Oracle Instant Client NOT found at: {client_dir}")
            print("   Please install Oracle Instant Client or update the path in:")
            print("   app/database/connection.py")

    # Check database configuration
    print("\n2. Database Configuration:")
    from config.database import DATABASE_CONFIG
    print(f"   Host: {DATABASE_CONFIG['host']}")
    print(f"   Port: {DATABASE_CONFIG['port']}")
    print(f"   Service Name: {DATABASE_CONFIG['service_name']}")
    print(f"   Username: {DATABASE_CONFIG['username']}")
    print(f"   Password: {'*' * len(DATABASE_CONFIG['password'])}")

    # Test connection
    print("\n3. Testing database connection...")
    try:
        conn = get_oracle_connection()
        if conn:
            print("   [OK] Successfully connected to Oracle database!")

            # Test a simple query
            print("\n4. Testing query execution...")
            cursor = conn.cursor()
            cursor.execute("SELECT 'Connection OK' as status, CURRENT_TIMESTAMP as current_time FROM DUAL")
            result = cursor.fetchone()
            print(f"   [OK] Query successful!")
            print(f"   Status: {result[0]}")
            print(f"   Server Time: {result[1]}")

            # Check current schema
            cursor.execute("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM DUAL")
            schema = cursor.fetchone()[0]
            print(f"   Current Schema: {schema}")

            cursor.close()
            conn.close()
            print("\n" + "=" * 60)
            print("CONNECTION TEST PASSED [OK]")
            print("=" * 60)
        else:
            print("   [FAIL] Failed to connect to database")
            print("\n" + "=" * 60)
            print("CONNECTION TEST FAILED [FAIL]")
            print("=" * 60)
    except Exception as e:
        print(f"   [FAIL] Error: {e}")
        print("\n" + "=" * 60)
        print("CONNECTION TEST FAILED [FAIL]")
        print("=" * 60)

if __name__ == "__main__":
    test_oracle_connection()
