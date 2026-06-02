"""
Hand Carry Service.

Manages the `rps.carrier_item` Postgres catalog — a simple UPC flag list
identifying which items should be treated as hand-carry. All product info
(description, brand, category, color, size, season, prices) is sourced
live from Oracle on read (CR #64).

Used by the commission pipeline (see `CommissionRepository.get_hand_carry_upcs`)
to filter hand-carry items out of the store commission pool while keeping
them eligible for the per-employee `hand_carry` personal-commission line
(CR #56).

Table schema (post CR #64):
    rps.carrier_item
        sid             integer  PK (serial)
        scan_upc        integer  NOT NULL  — UPC flagged as hand-carry
        quantity_sold   integer  NULL      — override for orphan UPCs that
                                              Oracle no longer recognises
                                              (treated as already sold-through).
                                              When NULL, live Oracle sales
                                              count is used.

Every other field on the GET response (description / brand / category /
color / size / season / quantity_imported / quantity_sold / prices) is
joined live from Oracle on every list call.
"""
from typing import Any, Dict, List, Optional

from app.core.database import get_oracle_connection, get_postgres_connection
from .queries import HandCarryQueries


# Oracle's parser tops out at 1000 entries in an IN-list. Chunk at 500
# to leave headroom for future growth.
_ORACLE_IN_CHUNK = 500

# Columns selected from `rps.carrier_item`. CR #64 reverted the table to
# a UPC flag list; `quantity_sold` remains as an orphan-UPC override.
_CATALOG_COLUMNS = ('sid', 'scan_upc', 'quantity_sold')
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
                # CR #64: catalog reverts to a UPC flag list — all product
                # info is sourced live from Oracle. Keep `quantity_sold`
                # as an orphan-UPC override (non-NULL value takes precedence
                # over the live Oracle sales count — used for items Oracle
                # no longer recognises so they show as already sold-through).
                cur.execute("""
                    ALTER TABLE rps.carrier_item
                        ADD COLUMN IF NOT EXISTS quantity_sold INTEGER
                """)
                # CR #64 migration: drop the denormalized product columns
                # that CR #58 introduced. Idempotent — IF EXISTS makes
                # re-running the init script safe.
                cur.execute("""
                    ALTER TABLE rps.carrier_item
                        DROP COLUMN IF EXISTS description,
                        DROP COLUMN IF EXISTS brand,
                        DROP COLUMN IF EXISTS category,
                        DROP COLUMN IF EXISTS color,
                        DROP COLUMN IF EXISTS size,
                        DROP COLUMN IF EXISTS season,
                        DROP COLUMN IF EXISTS quantity_imported,
                        DROP COLUMN IF EXISTS price_before_vat,
                        DROP COLUMN IF EXISTS price_after_vat
                """)
                # Not unique — duplicates handled in app code so the
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
        Return the full hand-carry catalogue (CR #64).

        Postgres holds just the UPC flag list + an optional quantity_sold
        override. Product info, prices, and lifetime imported/sold counts
        come live from Oracle on every call.

        Args:
            search: optional partial-UPC substring; case-insensitive contains
                match against `scan_upc` cast to text.
        """
        # 1. Pull the UPC flag list + override from Postgres
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

        upcs = [int(r[1]) for r in rows]

        # 2. Live Oracle joins — single round-trip each (with internal chunking)
        product_info = self.fetch_oracle_product_info(upcs)
        received_by_upc = self._lifetime_received_for(upcs)
        sold_by_upc = self._lifetime_sold_for(upcs)

        items = []
        for sid, scan_upc, qty_sold_stored in rows:
            upc_int = int(scan_upc)
            info = product_info.get(upc_int, {})

            # quantity_sold precedence: stored override (for orphan UPCs
            # Oracle no longer recognises) wins; otherwise live Oracle count.
            if qty_sold_stored is not None:
                quantity_sold = int(qty_sold_stored)
            else:
                quantity_sold = int(sold_by_upc.get(upc_int, 0))

            items.append({
                'id':                 int(sid),
                'upc':                upc_int,
                'description':        info.get('description'),
                'brand':              info.get('brand'),
                'category':           info.get('category'),
                'color':              info.get('color'),
                'size':               info.get('size'),
                'season':             info.get('season'),
                'quantity_imported':  int(received_by_upc.get(upc_int, 0)) if upc_int in received_by_upc else None,
                'quantity_sold':      quantity_sold,
                'price_before_vat':   info.get('price_before_vat'),
                'price_after_vat':    info.get('price_after_vat'),
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
    # Bulk import — CR #64: UPC-only (the catalog is a flag list)
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
