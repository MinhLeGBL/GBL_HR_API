"""
Store Service - PostgreSQL-based store management for HR frontend
"""
from typing import Dict, Any, List, Optional
from app.core.database.connection import get_postgres_connection


class StoreService:
    """Service for managing stores in PostgreSQL"""

    def init_database(self) -> Dict[str, Any]:
        """Initialize the stores table"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()

            # Store IDs: 9-digit starting with 2 (200000001+)
            cursor.execute('''
                CREATE SEQUENCE IF NOT EXISTS store_id_seq
                START WITH 200000001
                INCREMENT BY 1
                NO MAXVALUE
                NO CYCLE
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS stores (
                    id BIGINT PRIMARY KEY DEFAULT nextval('store_id_seq'),
                    store_code VARCHAR(50) UNIQUE NOT NULL,
                    store_name VARCHAR(255) NOT NULL,
                    store_rp_sid VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_stores_code ON stores(store_code)
            ''')

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'Stores table initialized'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def get_all_stores(self) -> Dict[str, Any]:
        """Get all stores"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, store_code, store_name, store_rp_sid, created_at
                FROM stores
                ORDER BY store_name
            ''')

            columns = ['id', 'store_code', 'store_name', 'store_rp_sid', 'created_at']
            stores = []
            for row in cursor.fetchall():
                store = dict(zip(columns, row))
                if store['created_at']:
                    store['created_at'] = store['created_at'].isoformat()
                stores.append(store)

            cursor.close()
            return {'success': True, 'stores': stores}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def get_store_by_id(self, store_id: int) -> Optional[Dict[str, Any]]:
        """Get a store by ID"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return None

            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, store_code, store_name, store_rp_sid, created_at
                FROM stores WHERE id = %s
            ''', (store_id,))

            row = cursor.fetchone()
            cursor.close()

            if row:
                columns = ['id', 'store_code', 'store_name', 'store_rp_sid', 'created_at']
                store = dict(zip(columns, row))
                if store['created_at']:
                    store['created_at'] = store['created_at'].isoformat()
                return store

            return None

        except Exception as e:
            print(f"Error getting store: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def create_store(self, store_code: str, store_name: str,
                     store_rp_sid: str = None) -> Dict[str, Any]:
        """Create a new store"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO stores (store_code, store_name, store_rp_sid)
                VALUES (%s, %s, %s)
                RETURNING id, store_code, store_name, store_rp_sid, created_at
            ''', (store_code, store_name, store_rp_sid))

            row = cursor.fetchone()
            conn.commit()

            columns = ['id', 'store_code', 'store_name', 'store_rp_sid', 'created_at']
            store = dict(zip(columns, row))
            if store['created_at']:
                store['created_at'] = store['created_at'].isoformat()

            cursor.close()
            return {'success': True, 'store': store}

        except Exception as e:
            if conn:
                conn.rollback()
            error_msg = str(e)
            if 'unique constraint' in error_msg.lower() or 'duplicate' in error_msg.lower():
                return {'success': False, 'error': 'Store code already exists'}
            return {'success': False, 'error': error_msg}
        finally:
            if conn:
                conn.close()
