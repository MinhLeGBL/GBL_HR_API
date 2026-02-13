"""
Store Service - PostgreSQL-based store management for HR frontend
"""
from typing import Dict, Any, List, Optional
from app.core.database.connection import get_postgres_connection, get_oracle_connection


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

    def sync_stores_from_retailpro(self) -> Dict[str, Any]:
        """
        Sync stores from Oracle RetailPro to PostgreSQL.

        Fetches all active stores from RetailPro and upserts them into PostgreSQL.
        Uses store_code as the unique key for upsert.

        Returns:
            Dict with success status, counts of inserted/updated stores
        """
        pg_conn = None
        oracle_conn = None
        try:
            # Step 1: Connect to Oracle RetailPro
            oracle_conn = get_oracle_connection()
            if not oracle_conn:
                return {'success': False, 'error': 'Failed to connect to RetailPro database'}

            oracle_cursor = oracle_conn.cursor()
            oracle_cursor.execute("""
                SELECT
                    s.SID as STORE_SID,
                    s.STORE_CODE,
                    s.STORE_NAME
                FROM STORE s
                WHERE s.ACTIVE = 1
                ORDER BY s.STORE_CODE
            """)

            stores = oracle_cursor.fetchall()
            oracle_cursor.close()
            oracle_conn.close()
            oracle_conn = None

            if not stores:
                return {'success': True, 'message': 'No stores found in RetailPro', 'inserted': 0, 'updated': 0}

            # Step 2: Connect to PostgreSQL and upsert stores
            pg_conn = get_postgres_connection()
            if not pg_conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = pg_conn.cursor()

            inserted = 0
            updated = 0

            for store_sid, store_code, store_name in stores:
                # Check if store exists
                cursor.execute('SELECT id FROM stores WHERE store_code = %s', (store_code,))
                existing = cursor.fetchone()

                if existing:
                    # Update existing store
                    cursor.execute('''
                        UPDATE stores
                        SET store_name = %s, store_rp_sid = %s
                        WHERE store_code = %s
                    ''', (store_name, str(store_sid), store_code))
                    updated += 1
                else:
                    # Insert new store
                    cursor.execute('''
                        INSERT INTO stores (store_code, store_name, store_rp_sid)
                        VALUES (%s, %s, %s)
                    ''', (store_code, store_name, str(store_sid)))
                    inserted += 1

            pg_conn.commit()
            cursor.close()

            return {
                'success': True,
                'message': f'Synced {inserted + updated} stores from RetailPro',
                'inserted': inserted,
                'updated': updated
            }

        except Exception as e:
            if pg_conn:
                pg_conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if oracle_conn:
                oracle_conn.close()
            if pg_conn:
                pg_conn.close()
