"""
Hand Carry Service.

Manages the `rps.carrier_item` Postgres catalog — the canonical list of
UPCs that are treated as hand-carry, with the per-item product info,
import qty, and prices the frontend uploads via Excel (CR #58).

Used by the commission pipeline (see `CommissionRepository.get_hand_carry_upcs`)
to filter hand-carry items out of the store commission pool while keeping
them eligible for the per-employee `hand_carry` personal-commission line
(CR #56).

Table schema (post CR #58):
    rps.carrier_item
        sid                integer    PK (serial)
        scan_upc           integer    NOT NULL   — UPC
        description        text       — product description (from import)
        brand              text       — vendor name (from import / Oracle)
        category           text       — category (from import / Oracle)
        color              text       — colour (from import / Oracle)
        size               text       — size (from import / Oracle)
        season             text       — e.g. 'SS25' (from import / Oracle)
        quantity_imported  integer    — qty hand-carried in this shipment
        price_before_vat   numeric    — selling price excluding VAT
        price_after_vat    numeric    — selling price including VAT

`quantity_sold` is NOT stored — it's joined live from Oracle (lifetime
sales for the UPC) every time `list_items` is called.
"""
from typing import Any, Dict, List, Optional

from app.core.database import get_oracle_connection, get_postgres_connection
from .queries import HandCarryQueries


# Oracle's parser tops out at 1000 entries in an IN-list. Chunk at 500
# to leave headroom for future growth.
_ORACLE_IN_CHUNK = 500

# Columns selected from `rps.carrier_item` in the order the GET response
# uses. Kept as a module constant so list_items and the backfill script
# can share the layout.
_CATALOG_COLUMNS = (
    'sid', 'scan_upc',
    'description', 'brand', 'category', 'color', 'size', 'season',
    'quantity_imported', 'quantity_sold',
    'price_before_vat', 'price_after_vat',
)
_CATALOG_SELECT = ', '.join(_CATALOG_COLUMNS)


def _validate_upc(raw) -> int:
    """Coerce a value to a positive integer UPC, or raise ValueError."""
    if isinstance(raw, bool):
        raise ValueError("boolean is not a valid UPC")
    upc_int = int(raw)
    if upc_int <= 0:
        raise ValueError("UPC must be a positive integer")
    return upc_int


def _validate_nonneg_int(raw, field_name: str) -> int:
    """Coerce a value to a non-negative integer, or raise ValueError."""
    if raw is None:
        raise ValueError(f"{field_name} is required")
    if isinstance(raw, bool):
        raise ValueError(f"boolean is not a valid value for {field_name}")
    n = int(raw)
    if n < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return n


def _validate_nonneg_number(raw, field_name: str) -> float:
    """Coerce a value to a non-negative number, or raise ValueError."""
    if raw is None:
        raise ValueError(f"{field_name} is required")
    if isinstance(raw, bool):
        raise ValueError(f"boolean is not a valid value for {field_name}")
    x = float(raw)
    if x < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return x


def _opt_str(raw) -> Optional[str]:
    """Return a stripped string or None for blank / missing input."""
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


class HandCarryService:

    # ------------------------------------------------------------------
    # Schema init — idempotent
    # ------------------------------------------------------------------
    # CR #58 extends `rps.carrier_item` from a flat (sid, scan_upc) flag
    # list to a per-UPC catalog with product info + import qty + prices.
    # `quantity_sold` is NOT a column — it's computed live from Oracle.
    def init_database(self) -> Dict[str, Any]:
        """Create / extend the `rps.carrier_item` table. Idempotent."""
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

        try:
            cur = conn.cursor()
            try:
                cur.execute("CREATE SCHEMA IF NOT EXISTS rps")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS rps.carrier_item (
                        sid       SERIAL PRIMARY KEY,
                        scan_upc  INTEGER NOT NULL
                    )
                """)
                # All new columns are nullable so existing 1,270 rows
                # don't need backfill before the migration runs.
                # `quantity_sold` (v0.2.1) is a stored override — when
                # non-NULL, it takes precedence over the live Oracle join.
                # Used for orphan UPCs that aren't in Oracle's catalog
                # (treated as already sold-through).
                cur.execute("""
                    ALTER TABLE rps.carrier_item
                        ADD COLUMN IF NOT EXISTS description       TEXT,
                        ADD COLUMN IF NOT EXISTS brand             TEXT,
                        ADD COLUMN IF NOT EXISTS category          TEXT,
                        ADD COLUMN IF NOT EXISTS color             TEXT,
                        ADD COLUMN IF NOT EXISTS size              TEXT,
                        ADD COLUMN IF NOT EXISTS season            TEXT,
                        ADD COLUMN IF NOT EXISTS quantity_imported INTEGER,
                        ADD COLUMN IF NOT EXISTS quantity_sold     INTEGER,
                        ADD COLUMN IF NOT EXISTS price_before_vat  NUMERIC(14, 2),
                        ADD COLUMN IF NOT EXISTS price_after_vat   NUMERIC(14, 2)
                """)
                # Not unique — duplicates are handled in app code so the
                # import flow can report them rather than raise an IntegrityError.
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS carrier_item_scan_upc_idx
                        ON rps.carrier_item (scan_upc)
                """)
                conn.commit()
            finally:
                cur.close()
        except Exception as e:
            conn.rollback()
            return {'success': False, 'error': f'init failed: {e}'}
        finally:
            conn.close()

        return {'success': True}

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def list_items(self, search: Optional[str] = None) -> Dict[str, Any]:
        """
        Return the full hand-carry catalogue.

        CR #58 extends each item with product info, `quantity_imported`,
        `quantity_sold` (live Oracle lifetime sales), and selling prices.

        Args:
            search: optional partial-UPC substring; case-insensitive contains
                match against `scan_upc` cast to text.
        """
        # 1. Pull the catalog rows from Postgres
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

        try:
            cur = conn.cursor()
            try:
                if search:
                    cur.execute(
                        f"SELECT {_CATALOG_SELECT} FROM rps.carrier_item "
                        "WHERE CAST(scan_upc AS TEXT) ILIKE %s "
                        "ORDER BY sid",
                        (f'%{search}%',),
                    )
                else:
                    cur.execute(
                        f"SELECT {_CATALOG_SELECT} FROM rps.carrier_item ORDER BY sid"
                    )
                rows = cur.fetchall()
            finally:
                cur.close()
        except Exception as e:
            return {'success': False, 'error': f'Query failed: {e}'}
        finally:
            conn.close()

        # 2. Compute lifetime sold qty per UPC from Oracle (live join)
        upcs_in_catalog = [int(r[1]) for r in rows]
        sold_by_upc = self._lifetime_sold_for(upcs_in_catalog)

        items = []
        for r in rows:
            (sid, scan_upc, description, brand, category, color, size,
             season, qty_imp, qty_sold_stored,
             price_before, price_after) = r
            upc_int = int(scan_upc)

            # quantity_sold precedence: stored value (used for orphan
            # UPCs that Oracle no longer recognises) takes priority over
            # the live Oracle join. Active items leave the stored value
            # NULL so sales keep updating in real time.
            if qty_sold_stored is not None:
                quantity_sold = int(qty_sold_stored)
            else:
                quantity_sold = int(sold_by_upc.get(upc_int, 0))

            items.append({
                'id':                int(sid),
                'upc':                upc_int,
                'description':        description,
                'brand':              brand,
                'category':           category,
                'color':              color,
                'size':               size,
                'season':             season,
                'quantity_imported':  int(qty_imp) if qty_imp is not None else None,
                'quantity_sold':      quantity_sold,
                'price_before_vat':   float(price_before) if price_before is not None else None,
                'price_after_vat':    float(price_after) if price_after is not None else None,
            })

        return {'success': True, 'items': items, 'count': len(items)}

    # ------------------------------------------------------------------
    # Oracle joins (shared with backfill script)
    # ------------------------------------------------------------------
    @staticmethod
    def _lifetime_sold_for(upcs: List[int]) -> Dict[int, int]:
        """For a list of UPCs, return {upc: lifetime_sold_qty}.

        Sold qty nets returns (item_type=2) against sales (item_type=1).
        Chunks the IN list at `_ORACLE_IN_CHUNK` to stay under Oracle's
        1000-element limit. Returns an empty dict on Oracle connection
        failure (better to surface zeros than to abort the whole list).
        """
        if not upcs:
            return {}
        # Validate the values are all integers — guards against SQL injection
        # since we string-interpolate the IN list (Oracle bind-array would
        # be more "correct" but heavier here).
        safe = [int(u) for u in upcs]
        result: Dict[int, int] = {}

        conn = get_oracle_connection()
        if conn is None:
            return {}
        try:
            cur = conn.cursor()
            for i in range(0, len(safe), _ORACLE_IN_CHUNK):
                chunk = safe[i:i + _ORACLE_IN_CHUNK]
                upc_list_sql = ', '.join(f"'{u}'" for u in chunk)
                cur.execute(HandCarryQueries.ORACLE_LIFETIME_SOLD.format(upcs=upc_list_sql))
                for row in cur.fetchall():
                    upc_int = int(row[0])
                    result[upc_int] = int(row[1] or 0)
            cur.close()
        except Exception:
            return {}
        finally:
            conn.close()

        return result

    @staticmethod
    def _lifetime_received_for(upcs: List[int]) -> Dict[int, int]:
        """For a list of UPCs, return {upc: lifetime_received_qty} pulled
        from Oracle's posted receiving vouchers (v0.2.2). Used by the
        backfill script to set `quantity_imported` for Oracle-known UPCs
        from the same source Oracle uses for non-hand-carry inventory.

        Chunks the IN list at `_ORACLE_IN_CHUNK`. Returns an empty dict
        on Oracle connection failure.
        """
        if not upcs:
            return {}
        safe = [int(u) for u in upcs]
        result: Dict[int, int] = {}

        conn = get_oracle_connection()
        if conn is None:
            return {}
        try:
            cur = conn.cursor()
            for i in range(0, len(safe), _ORACLE_IN_CHUNK):
                chunk = safe[i:i + _ORACLE_IN_CHUNK]
                upc_list_sql = ', '.join(f"'{u}'" for u in chunk)
                cur.execute(HandCarryQueries.ORACLE_LIFETIME_RECEIVED.format(upcs=upc_list_sql))
                for row in cur.fetchall():
                    upc_int = int(row[0])
                    result[upc_int] = int(row[1] or 0)
            cur.close()
        except Exception:
            return {}
        finally:
            conn.close()

        return result

    @staticmethod
    def fetch_oracle_product_info(upcs: List[int]) -> Dict[int, Dict[str, Any]]:
        """For a list of UPCs, return {upc: {description, brand, category,
        color, item_size, season, price_after_vat, price_before_vat}}.

        Used by the one-time backfill script and as a future read-side
        enricher. Returns an empty dict on connection failure.
        """
        if not upcs:
            return {}
        safe = [int(u) for u in upcs]
        result: Dict[int, Dict[str, Any]] = {}

        conn = get_oracle_connection()
        if conn is None:
            return {}
        try:
            cur = conn.cursor()
            for i in range(0, len(safe), _ORACLE_IN_CHUNK):
                chunk = safe[i:i + _ORACLE_IN_CHUNK]
                upc_list_sql = ', '.join(f"'{u}'" for u in chunk)
                cur.execute(HandCarryQueries.ORACLE_PRODUCT_INFO.format(upcs=upc_list_sql))
                for row in cur.fetchall():
                    (upc, description, brand, category, color, item_size,
                     season, price_after_vat, price_before_vat) = row
                    result[int(upc)] = {
                        'description':       description,
                        'brand':             brand,
                        'category':          category,
                        'color':             color,
                        'size':              item_size,
                        'season':            season,
                        'price_after_vat':   float(price_after_vat) if price_after_vat is not None else None,
                        'price_before_vat':  float(price_before_vat) if price_before_vat is not None else None,
                    }
            cur.close()
        except Exception:
            return {}
        finally:
            conn.close()

        return result

    # ------------------------------------------------------------------
    # Bulk import — CR #58: full-record REPLACE semantics
    # ------------------------------------------------------------------
    def import_records(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bulk upsert hand-carry catalog rows from full records.

        Each record: { upc, description?, brand?, category?, color?, size?,
                       season?, quantity_imported, price_before_vat,
                       price_after_vat }

        Required fields: upc (positive int), quantity_imported
        (non-negative int), price_before_vat (non-negative number),
        price_after_vat (non-negative number).

        Behaviour:
            - If `scan_upc` already exists, **all fields are replaced**
              with the import row's values (the file is the source of
              truth, not an additive log — CR #58).
            - If new, INSERT a new row.
            - Within a single batch, duplicate UPCs collapse to the
              **last** occurrence (matches "file wins" semantics).
            - Invalid rows surface in `errors` keyed by `row_index`;
              the rest of the batch still imports.

        Returns:
            { success, inserted, updated, skipped, errors, total_received }
        """
        if not isinstance(records, list):
            return {'success': False, 'error': "'records' must be an array"}

        normalised: Dict[int, Dict[str, Any]] = {}
        errors: List[Dict[str, Any]] = []

        for idx, rec in enumerate(records):
            try:
                if not isinstance(rec, dict):
                    raise ValueError('each record must be an object')
                upc = _validate_upc(rec.get('upc'))
                normalised[upc] = {
                    'upc':                upc,
                    'description':        _opt_str(rec.get('description')),
                    'brand':              _opt_str(rec.get('brand')),
                    'category':           _opt_str(rec.get('category')),
                    'color':              _opt_str(rec.get('color')),
                    'size':               _opt_str(rec.get('size')),
                    'season':             _opt_str(rec.get('season')),
                    'quantity_imported':  _validate_nonneg_int(rec.get('quantity_imported'), 'quantity_imported'),
                    'price_before_vat':   _validate_nonneg_number(rec.get('price_before_vat'), 'price_before_vat'),
                    'price_after_vat':    _validate_nonneg_number(rec.get('price_after_vat'), 'price_after_vat'),
                }
            except (TypeError, ValueError) as e:
                errors.append({'row_index': idx, 'error': str(e)})

        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

        inserted = updated = 0
        try:
            cur = conn.cursor()
            try:
                # One round-trip to learn which UPCs already exist
                cur.execute('SELECT scan_upc FROM rps.carrier_item')
                existing = {int(r[0]) for r in cur.fetchall()}

                for upc, rec in normalised.items():
                    values = (
                        rec['description'], rec['brand'], rec['category'],
                        rec['color'], rec['size'], rec['season'],
                        rec['quantity_imported'],
                        rec['price_before_vat'], rec['price_after_vat'],
                    )
                    if upc in existing:
                        cur.execute("""
                            UPDATE rps.carrier_item
                               SET description       = %s,
                                   brand             = %s,
                                   category          = %s,
                                   color             = %s,
                                   size              = %s,
                                   season            = %s,
                                   quantity_imported = %s,
                                   price_before_vat  = %s,
                                   price_after_vat   = %s
                             WHERE scan_upc = %s
                        """, (*values, upc))
                        updated += 1
                    else:
                        cur.execute("""
                            INSERT INTO rps.carrier_item (
                                scan_upc, description, brand, category, color,
                                size, season, quantity_imported,
                                price_before_vat, price_after_vat
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (upc, *values))
                        existing.add(upc)
                        inserted += 1

                conn.commit()
            finally:
                cur.close()
        except Exception as e:
            conn.rollback()
            return {'success': False, 'error': f'Import failed: {e}'}
        finally:
            conn.close()

        return {
            'success': True,
            'inserted': inserted,
            'updated': updated,
            'skipped': 0,                          # nothing is skipped in REPLACE mode
            'errors': errors,
            'total_received': len(records),
        }

    # ------------------------------------------------------------------
    # Bulk import — LEGACY UPC-only shape (kept for backward compat)
    # ------------------------------------------------------------------
    def import_upcs(self, upcs: List[Any]) -> Dict[str, Any]:
        """
        Bulk upsert UPCs into `rps.carrier_item`.

        Behaviour:
            - Each input value must be coercible to an integer UPC. Non-integer
              entries are reported in `errors` with their input row index.
            - Existing UPCs are skipped (no duplicates introduced).
            - Returns counts of inserted vs skipped vs errors.

        Args:
            upcs: list of UPC values (ints or numeric strings)

        Returns:
            {
                'success': True,
                'inserted': int, 'skipped': int, 'errors': [{row_index, error}],
                'total_received': int,
            }
        """
        # Validate input shape early
        if not isinstance(upcs, list):
            return {'success': False, 'error': 'upcs must be an array'}

        # Coerce and collect errors
        normalised: List[int] = []
        errors: List[Dict[str, Any]] = []
        for idx, raw in enumerate(upcs):
            try:
                if isinstance(raw, bool):  # bool is subclass of int — reject
                    raise ValueError("boolean is not a valid UPC")
                upc_int = int(raw)
                if upc_int <= 0:
                    raise ValueError("UPC must be a positive integer")
                normalised.append(upc_int)
            except (TypeError, ValueError) as e:
                errors.append({'row_index': idx, 'error': f'invalid UPC: {raw!r} ({e})'})

        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

        inserted = 0
        skipped = 0
        try:
            cur = conn.cursor()
            try:
                # Pull existing UPCs once so we can classify each input
                cur.execute("SELECT scan_upc FROM rps.carrier_item")
                existing = {int(r[0]) for r in cur.fetchall()}

                to_insert = []
                seen_in_batch: set = set()
                for upc in normalised:
                    if upc in existing or upc in seen_in_batch:
                        skipped += 1
                    else:
                        to_insert.append((upc,))
                        seen_in_batch.add(upc)

                if to_insert:
                    cur.executemany(
                        "INSERT INTO rps.carrier_item (scan_upc) VALUES (%s)",
                        to_insert,
                    )
                    inserted = len(to_insert)
                conn.commit()
            finally:
                cur.close()
        except Exception as e:
            conn.rollback()
            return {'success': False, 'error': f'Import failed: {e}'}
        finally:
            conn.close()

        return {
            'success': True,
            'inserted': inserted,
            'skipped': skipped,
            'errors': errors,
            'total_received': len(upcs),
        }

    # ------------------------------------------------------------------
    # Edit / delete
    # ------------------------------------------------------------------
    def update_item(self, item_id: int, upc: Any) -> Dict[str, Any]:
        """Update a single record's UPC."""
        try:
            if isinstance(upc, bool):
                raise ValueError("boolean is not a valid UPC")
            new_upc = int(upc)
            if new_upc <= 0:
                raise ValueError("UPC must be a positive integer")
        except (TypeError, ValueError) as e:
            return {'success': False, 'error': f'invalid UPC: {upc!r} ({e})'}

        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

        try:
            cur = conn.cursor()
            try:
                # Reject if the new UPC already exists on a different row
                cur.execute(
                    "SELECT sid FROM rps.carrier_item WHERE scan_upc = %s AND sid <> %s",
                    (new_upc, item_id),
                )
                if cur.fetchone() is not None:
                    return {'success': False, 'error': f'UPC {new_upc} already exists', 'status': 409}

                cur.execute(
                    "UPDATE rps.carrier_item SET scan_upc = %s WHERE sid = %s RETURNING sid, scan_upc",
                    (new_upc, item_id),
                )
                row = cur.fetchone()
                if row is None:
                    conn.rollback()
                    return {'success': False, 'error': f'id {item_id} not found', 'status': 404}
                conn.commit()
                return {'success': True, 'item': {'id': int(row[0]), 'upc': int(row[1])}}
            finally:
                cur.close()
        except Exception as e:
            conn.rollback()
            return {'success': False, 'error': f'Update failed: {e}'}
        finally:
            conn.close()

    def delete_item(self, item_id: int) -> Dict[str, Any]:
        """Delete a single record by id."""
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

        try:
            cur = conn.cursor()
            try:
                cur.execute(
                    "DELETE FROM rps.carrier_item WHERE sid = %s RETURNING sid",
                    (item_id,),
                )
                row = cur.fetchone()
                if row is None:
                    conn.rollback()
                    return {'success': False, 'error': f'id {item_id} not found', 'status': 404}
                conn.commit()
                return {'success': True, 'id': int(row[0])}
            finally:
                cur.close()
        except Exception as e:
            conn.rollback()
            return {'success': False, 'error': f'Delete failed: {e}'}
        finally:
            conn.close()
