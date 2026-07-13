"""Reports repository (CR #78 Oracle sale-comparison + CR #79 Postgres pins)."""
from datetime import date
from typing import Any, Dict, Optional

from app.core.database import get_oracle_connection, get_postgres_connection
from .queries import ReportsQueries


class ReportsRepository:
    """Runs the sale-comparison Oracle queries and the per-user pin CRUD on
    Postgres. One connection per call, always closed in `finally`."""

    def fetch_period_metrics(self, from_date: date, to_exclusive: date) -> Dict[str, Any]:
        """Aggregate one period.

        Args:
            from_date: inclusive start (date).
            to_exclusive: exclusive end = last day + 1 (date). The caller adds
                the day so the whole `to` day is included regardless of any
                time component on `invc_post_date`.

        Returns a dict with keys: total_revenue, bill_count, new_customers,
        returning_customers, new_customer_revenue. `returning_customer_revenue`
        is derived by the service (total - new).
        """
        binds = {'from_date': from_date, 'to_exclusive': to_exclusive}
        conn = get_oracle_connection()
        # get_oracle_connection() returns None on failure (not raise). Surface a
        # clear error so the service maps it to SERVER_ERROR, and keep the
        # finally safe (no None.close()).
        if conn is None:
            raise RuntimeError('Oracle connection unavailable')
        try:
            cur = conn.cursor()

            cur.execute(ReportsQueries.PERIOD_TOTALS, binds)
            row = cur.fetchone()
            total_revenue = int(row[0] or 0)
            bill_count = int(row[1] or 0)
            tourist_customers = int(row[2] or 0)
            tourist_customer_revenue = int(row[3] or 0)

            cur.execute(ReportsQueries.NEW_VS_RETURNING, binds)
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
    # CR #79 — per-user pinned periods (Postgres, one row per user)
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
                    '''SELECT period_a_from, period_a_to,
                              period_b_from, period_b_to
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
            'period_a_from': row[0], 'period_a_to': row[1],
            'period_b_from': row[2], 'period_b_to': row[3],
        }

    def upsert_pins(
        self, user_id: int,
        a_from: Optional[date], a_to: Optional[date],
        b_from: Optional[date], b_to: Optional[date],
    ) -> None:
        """Replace the user's pins (insert or update the single row). Any side
        passed as (None, None) is stored unpinned."""
        conn = get_postgres_connection()
        if conn is None:
            raise RuntimeError('Postgres connection unavailable')
        try:
            with conn.cursor() as cur:
                cur.execute(
                    '''INSERT INTO live_comparison_pins
                           (user_id, period_a_from, period_a_to,
                            period_b_from, period_b_to, updated_at)
                       VALUES (%s, %s, %s, %s, %s, NOW())
                       ON CONFLICT (user_id) DO UPDATE SET
                           period_a_from = EXCLUDED.period_a_from,
                           period_a_to   = EXCLUDED.period_a_to,
                           period_b_from = EXCLUDED.period_b_from,
                           period_b_to   = EXCLUDED.period_b_to,
                           updated_at    = NOW()''',
                    (user_id, a_from, a_to, b_from, b_to),
                )
            conn.commit()
        finally:
            conn.close()
