"""Reports repository — read-only Oracle aggregation (CR #78)."""
from datetime import date
from typing import Any, Dict

from app.core.database import get_oracle_connection
from .queries import ReportsQueries


class ReportsRepository:
    """Runs the sale-comparison Oracle queries. One connection per call,
    always closed in `finally` (mirrors the CRM repository pattern)."""

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
        try:
            cur = conn.cursor()

            cur.execute(ReportsQueries.PERIOD_TOTALS, binds)
            row = cur.fetchone()
            total_revenue = int(row[0] or 0)
            bill_count = int(row[1] or 0)

            cur.execute(ReportsQueries.NEW_VS_RETURNING, binds)
            row = cur.fetchone()
            new_customers = int(row[0] or 0)
            returning_customers = int(row[1] or 0)
            new_customer_revenue = int(row[2] or 0)

            cur.close()
        finally:
            conn.close()

        return {
            'total_revenue':        total_revenue,
            'bill_count':           bill_count,
            'new_customers':        new_customers,
            'returning_customers':  returning_customers,
            'new_customer_revenue': new_customer_revenue,
        }
