"""Reports repository (CR #78 Oracle sale-comparison + CR #79 Postgres pins)."""
from datetime import date
from typing import Any, Dict, Optional

from app.core.database import get_oracle_connection, get_postgres_connection
from .queries import ReportsQueries


class ReportsRepository:
    """Runs the sale-comparison Oracle queries and the per-user pin CRUD on
    Postgres. One connection per call, always closed in `finally`."""

    def fetch_period_metrics(
        self, from_date: date, to_exclusive: date,
        store_sids: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Aggregate one period.

        Args:
            from_date: inclusive start (date).
            to_exclusive: exclusive end = last day + 1 (date). The caller adds
                the day so the whole `to` day is included regardless of any
                time component on `invc_post_date`.
            store_sids: CR #81 — optional list of Oracle STORE.SID ints to scope
                the period to (union). None/empty → all stores.

        Returns a dict with keys: total_revenue, bill_count, new_customers,
        returning_customers, new_customer_revenue. `returning_customer_revenue`
        is derived by the service (total - new).
        """
        store_filter, store_binds = ReportsQueries.store_filter(store_sids)
        binds = {'from_date': from_date, 'to_exclusive': to_exclusive, **store_binds}
        conn = get_oracle_connection()
        # get_oracle_connection() returns None on failure (not raise). Surface a
        # clear error so the service maps it to SERVER_ERROR, and keep the
        # finally safe (no None.close()).
        if conn is None:
            raise RuntimeError('Oracle connection unavailable')
        try:
            cur = conn.cursor()

            cur.execute(ReportsQueries.period_totals(store_filter), binds)
            row = cur.fetchone()
            total_revenue = int(row[0] or 0)
            bill_count = int(row[1] or 0)
            tourist_customers = int(row[2] or 0)
            tourist_customer_revenue = int(row[3] or 0)

            cur.execute(ReportsQueries.new_vs_returning(store_filter), binds)
            row = cur.fetchone()
            new_customers = int(row[0] or 0)
            returning_customers = int(row[1] or 0)
            new_customer_revenue = int(row[2] or 0)

            cur.close()
        finally:
            conn.close()

        return {
            'total_revenue':            total_revenue,
            'bill_count':               bill_count,
            'tourist_customers':        tourist_customers,
            'tourist_customer_revenue': tourist_customer_revenue,
            'new_customers':            new_customers,
            'returning_customers':      returning_customers,
            'new_customer_revenue':     new_customer_revenue,
        }

    # ------------------------------------------------------------------
    # CR #81 — resolve frontend store ids (Postgres stores.id) to Oracle SIDs
    # ------------------------------------------------------------------
    def get_store_sids(self, store_ids: list) -> Dict[int, int]:
        """Map `GET /stores` ids → Oracle STORE.SID ints.

        Only stores that exist AND carry a usable `store_rp_sid` appear in the
        result. An id the caller passed that is absent from the returned dict is
        therefore either unknown or unmapped — the service treats both as an
        invalid store id (400).
        """
        conn = get_postgres_connection()
        if conn is None:
            raise RuntimeError('Postgres connection unavailable')
        try:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT id, store_rp_sid FROM stores WHERE id = ANY(%s)',
                    (list(store_ids),),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        out: Dict[int, int] = {}
        for store_id, rp_sid in rows:
            if rp_sid is None or str(rp_sid).strip() == '':
                continue  # store exists but has no Retail Pro mapping — unusable
            try:
                out[int(store_id)] = int(rp_sid)
            except (TypeError, ValueError):
                continue  # non-numeric rp_sid — treat as unmapped
        return out

    # ------------------------------------------------------------------
    # CR #79 — per-user pinned periods (Postgres, one row per user)
    # CR #81 — each side also carries an optional store_ids array.
    # ------------------------------------------------------------------
    def get_pins(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Return the user's pin row as a dict, or None if they have no row
        (i.e. nothing pinned yet)."""
        conn = get_postgres_connection()
        if conn is None:
            raise RuntimeError('Postgres connection unavailable')
        try:
            with conn.cursor() as cur:
                cur.execute(
                    '''SELECT period_a_from, period_a_to, period_a_store_ids,
                              period_b_from, period_b_to, period_b_store_ids
                       FROM live_comparison_pins
                       WHERE user_id = %s''',
                    (user_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return {
            'period_a_from': row[0], 'period_a_to': row[1], 'period_a_store_ids': row[2],
            'period_b_from': row[3], 'period_b_to': row[4], 'period_b_store_ids': row[5],
        }

    def upsert_pins(
        self, user_id: int,
        a_from: Optional[date], a_to: Optional[date], a_store_ids: Optional[list],
        b_from: Optional[date], b_to: Optional[date], b_store_ids: Optional[list],
    ) -> None:
        """Replace the user's pins (insert or update the single row). Any side
        passed as (None, None, None) is stored unpinned. `*_store_ids` is a list
        of store ids or None (→ all stores)."""
        conn = get_postgres_connection()
        if conn is None:
            raise RuntimeError('Postgres connection unavailable')
        try:
            with conn.cursor() as cur:
                cur.execute(
                    '''INSERT INTO live_comparison_pins
                           (user_id, period_a_from, period_a_to, period_a_store_ids,
                            period_b_from, period_b_to, period_b_store_ids, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                       ON CONFLICT (user_id) DO UPDATE SET
                           period_a_from      = EXCLUDED.period_a_from,
                           period_a_to        = EXCLUDED.period_a_to,
                           period_a_store_ids = EXCLUDED.period_a_store_ids,
                           period_b_from      = EXCLUDED.period_b_from,
                           period_b_to        = EXCLUDED.period_b_to,
                           period_b_store_ids = EXCLUDED.period_b_store_ids,
                           updated_at         = NOW()''',
                    (user_id, a_from, a_to, a_store_ids,
                     b_from, b_to, b_store_ids),
                )
            conn.commit()
        finally:
            conn.close()
