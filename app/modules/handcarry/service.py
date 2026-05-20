"""
Hand Carry Service.

Manages the `rps.carrier_item` Postgres table — the canonical list of UPCs
that are flagged as hand-carry. Used by the commission pipeline (see
`CommissionRepository.get_hand_carry_upcs`) to filter hand-carry items out
of the store commission pool while keeping them eligible for the
per-employee `hand_carry` personal-commission line (CR #56).

Table:
    rps.carrier_item
        sid       integer  PRIMARY KEY (auto-increment via carrier_item_sid_seq)
        scan_upc  integer  NOT NULL  — the UPC value (12345, 15611, …)
"""
from typing import Any, Dict, List, Optional

from app.core.database import get_postgres_connection


class HandCarryService:

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def list_items(self, search: Optional[str] = None) -> Dict[str, Any]:
        """
        Return all hand-carry UPCs.

        Args:
            search: optional partial-UPC substring; case-insensitive contains
                match against the integer UPC cast as text.
        """
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

        try:
            cur = conn.cursor()
            try:
                if search:
                    cur.execute(
                        "SELECT sid, scan_upc FROM rps.carrier_item "
                        "WHERE CAST(scan_upc AS TEXT) ILIKE %s "
                        "ORDER BY sid",
                        (f'%{search}%',),
                    )
                else:
                    cur.execute(
                        "SELECT sid, scan_upc FROM rps.carrier_item ORDER BY sid"
                    )
                rows = cur.fetchall()
            finally:
                cur.close()
        except Exception as e:
            return {'success': False, 'error': f'Query failed: {e}'}
        finally:
            conn.close()

        items = [{'id': int(r[0]), 'upc': int(r[1])} for r in rows]
        return {'success': True, 'items': items, 'count': len(items)}

    # ------------------------------------------------------------------
    # Bulk import
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
