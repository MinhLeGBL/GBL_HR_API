#!/usr/bin/env python3
"""
Quick script to test Oracle database connection
"""
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.database.connection import get_oracle_connection

def main():
    print("Testing Oracle database connection...")
    
    # Test connection
    conn = get_oracle_connection()
    
    if conn is None:
        print("❌ Connection failed!")
        return False
    
    try:
        print("✅ Connection successful!")
        
        # Test basic query
        with conn.cursor() as cursor:
            cursor.execute("SELECT 'Hello from Oracle!' as message FROM DUAL")
            result = cursor.fetchone()
            print(f"✅ Query result: {result[0]}")
            
        # Test schema
        with conn.cursor() as cursor:
            cursor.execute("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM DUAL")
            schema = cursor.fetchone()[0]
            print(f"✅ Current schema: {schema}")
            
        # Test user
        with conn.cursor() as cursor:
            cursor.execute("SELECT USER FROM DUAL")
            user = cursor.fetchone()[0]
            print(f"✅ Connected as user: {user}")
            
        return True
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        return False
        
    finally:
        conn.close()
        print("✅ Connection closed successfully")

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)