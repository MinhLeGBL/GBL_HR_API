"""
CRM Repository — data access for both Oracle (read-only) and PostgreSQL (cache).

Two distinct responsibilities:
  - fetch_*  : read raw aggregates from Oracle (used by recompute job only)
  - get_*    : read scored/cached data from Postgres (used by API endpoints)
  - persist_*: write computed scores back to Postgres (used by recompute job only)
"""
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.core.database import get_oracle_connection, get_postgres_connection
from .queries import CRMQueries


class CRMRepository:
    """Data access for CRM RFM pipeline."""

    # ==================================================================
    # Oracle reads (recompute job)
    # ==================================================================

    def fetch_customer_aggregates(self) -> List[Dict[str, Any]]:
        """
        Fetch raw RFM inputs for every active customer with at least one
        purchase in the rolling 24-month window.

        Returns:
            List of dicts with keys:
                customer_sid (int), name (str), email (str|None),
                recency (int days), frequency (int),
                monetary (int VND), last_purchase_date (date)
        """
        conn = get_oracle_connection()
        try:
            cur = conn.cursor()
            cur.execute(CRMQueries.CUSTOMER_RFM_AGGREGATES)
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()

        return [
            {
                'customer_sid':       int(r[0]),
                'name':               (r[1] or '').strip(),
                'email':              r[2],
                'recency':            int(r[3]),
                'frequency':          int(r[4]),
                'monetary':           int(r[5] or 0),
                'last_purchase_date': r[6].date() if hasattr(r[6], 'date') else r[6],
            }
            for r in rows
        ]

    def fetch_top_brand_category(self) -> Dict[int, Dict[str, Any]]:
        """
        Fetch top brand, top category, and category breadth per customer.

        Returns:
            Dict keyed by customer_sid with values:
                {top_brand: str|None, top_category: str|None, category_breadth: int}
        """
        conn = get_oracle_connection()
        try:
            cur = conn.cursor()
            cur.execute(CRMQueries.CUSTOMER_TOP_BRAND_CATEGORY)
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()

        return {
            int(r[0]): {
                'top_brand':        r[1],
                'top_category':     r[2],
                'category_breadth': int(r[3] or 0),
            }
            for r in rows
        }

    def fetch_customer_phones(self) -> Dict[int, str]:
        """Fetch primary phone per customer. Customers without a phone are absent."""
        conn = get_oracle_connection()
        try:
            cur = conn.cursor()
            cur.execute(CRMQueries.CUSTOMER_PHONE)
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()

        return {int(r[0]): r[1] for r in rows}

    # ==================================================================
    # PostgreSQL config (RFM weights)
    # ==================================================================

    def get_config(self) -> Dict[str, float]:
        """
        Read RFM config from crm_config table. Returns default weights
        if the table doesn't exist or is empty.
        """
        defaults = {
            'w_recency': 0.3, 'w_frequency': 0.3, 'w_monetary': 0.4,
            'e_recency': 0.5, 'e_frequency': 0.3, 'e_monetary': 0.2,
        }
        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT w_recency, w_frequency, w_monetary,
                       e_recency, e_frequency, e_monetary
                FROM crm_config WHERE id = 1
            """)
            row = cur.fetchone()
            cur.close()
            if not row:
                return defaults
            return {
                'w_recency': float(row[0]), 'w_frequency': float(row[1]), 'w_monetary': float(row[2]),
                'e_recency': float(row[3]), 'e_frequency': float(row[4]), 'e_monetary': float(row[5]),
            }
        except Exception:
            return defaults
        finally:
            conn.close()

    def update_config(self, config: Dict[str, float]) -> bool:
        """Update RFM weights in crm_config. Returns True on success."""
        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE crm_config SET
                    w_recency = %(w_recency)s, w_frequency = %(w_frequency)s, w_monetary = %(w_monetary)s,
                    e_recency = %(e_recency)s, e_frequency = %(e_frequency)s, e_monetary = %(e_monetary)s,
                    updated_at = NOW()
                WHERE id = 1
            """, config)
            conn.commit()
            cur.close()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ==================================================================
    # Oracle reads — backfill (parameterized date)
    # ==================================================================

    def fetch_customer_aggregates_as_of(self, as_of_date: datetime) -> List[Dict[str, Any]]:
        """Same as fetch_customer_aggregates but computed 'as of' a historical date."""
        conn = get_oracle_connection()
        try:
            cur = conn.cursor()
            sql = CRMQueries.as_of_query('CUSTOMER_RFM_AGGREGATES')
            cur.execute(sql, {'as_of_date': as_of_date})
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()

        return [
            {
                'customer_sid':       int(r[0]),
                'name':               (r[1] or '').strip(),
                'email':              r[2],
                'recency':            int(r[3]),
                'frequency':          int(r[4]),
                'monetary':           int(r[5] or 0),
                'last_purchase_date': r[6].date() if hasattr(r[6], 'date') else r[6],
            }
            for r in rows
        ]

    def fetch_top_brand_category_as_of(self, as_of_date: datetime) -> Dict[int, Dict[str, Any]]:
        """Same as fetch_top_brand_category but computed 'as of' a historical date."""
        conn = get_oracle_connection()
        try:
            cur = conn.cursor()
            sql = CRMQueries.as_of_query('CUSTOMER_TOP_BRAND_CATEGORY')
            cur.execute(sql, {'as_of_date': as_of_date})
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()

        return {
            int(r[0]): {
                'top_brand':        r[1],
                'top_category':     r[2],
                'category_breadth': int(r[3] or 0),
            }
            for r in rows
        }

    # ==================================================================
    # PostgreSQL writes (recompute job)
    # ==================================================================

    def replace_customer_scores(self, scored_customers: List[Dict[str, Any]]) -> int:
        """
        Atomically replace the entire crm_customer_scores table.

        TRUNCATE + bulk INSERT in one transaction — readers see either the old
        snapshot or the new one, never an empty table mid-recompute.

        Args:
            scored_customers: List of dicts produced by score_customers() and
                enriched with name/email/phone/top_brand/top_category/
                category_breadth/last_purchase_date fields.

        Returns:
            Number of rows inserted.
        """
        if not scored_customers:
            return 0

        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            cur.execute('TRUNCATE TABLE crm_customer_scores')

            # Build value tuples in stable column order
            insert_sql = """
                INSERT INTO crm_customer_scores (
                    customer_sid, name, email, phone,
                    recency, frequency, monetary,
                    r_score, f_score, m_score,
                    weighted_score, engagement_score, segment,
                    top_brand, top_category, category_breadth,
                    last_purchase_date
                ) VALUES (
                    %(customer_sid)s, %(name)s, %(email)s, %(phone)s,
                    %(recency)s, %(frequency)s, %(monetary)s,
                    %(r_score)s, %(f_score)s, %(m_score)s,
                    %(weighted_score)s, %(engagement_score)s, %(segment)s,
                    %(top_brand)s, %(top_category)s, %(category_breadth)s,
                    %(last_purchase_date)s
                )
            """
            cur.executemany(insert_sql, scored_customers)
            inserted = cur.rowcount
            conn.commit()
            cur.close()
            return inserted
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def upsert_segment_snapshot(self, snapshot_month: date,
                                 segment_counts: Dict[str, int]) -> int:
        """
        Insert/update segment counts for the given month.

        Uses ON CONFLICT to overwrite if the (month, segment) already exists —
        means each day's recompute updates that day's snapshot, and the final
        recompute of the month becomes the persistent end-of-month value.

        Args:
            snapshot_month: First day of the snapshot month (date(2026, 4, 1)).
            segment_counts: {segment_name: count} for all 7 segments.

        Returns:
            Number of segment rows upserted.
        """
        if not segment_counts:
            return 0

        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            for segment, count in segment_counts.items():
                cur.execute("""
                    INSERT INTO crm_segment_snapshots (snapshot_month, segment, customer_count)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (snapshot_month, segment) DO UPDATE
                        SET customer_count = EXCLUDED.customer_count
                """, (snapshot_month, segment, count))
            conn.commit()
            cur.close()
            return len(segment_counts)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ==================================================================
    # PostgreSQL reads (API endpoints)
    # ==================================================================

    def count_scored_customers(self) -> int:
        """Cheap check used to decide whether to return data or 503."""
        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM crm_customer_scores')
            n = cur.fetchone()[0]
            cur.close()
            return n
        finally:
            conn.close()

    def list_customer_scores(self, segment: Optional[str] = None,
                              min_weighted_score: Optional[float] = None
                              ) -> List[Dict[str, Any]]:
        """
        List customers with optional segment and weighted-score filters.

        Returns rows ordered by weighted_score DESC for stable presentation
        (top customers first within any filter).
        """
        clauses = []
        params: List[Any] = []
        if segment:
            clauses.append('segment = %s')
            params.append(segment)
        if min_weighted_score is not None:
            clauses.append('weighted_score >= %s')
            params.append(min_weighted_score)

        where = f'WHERE {" AND ".join(clauses)}' if clauses else ''
        sql = f"""
            SELECT customer_sid, name, email, phone,
                   recency, frequency, monetary,
                   r_score, f_score, m_score,
                   weighted_score, engagement_score, segment,
                   top_brand, top_category, category_breadth,
                   last_purchase_date
            FROM crm_customer_scores
            {where}
            ORDER BY weighted_score DESC, customer_sid ASC
        """

        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            conn.close()

    def aggregate_segment_summary(self) -> List[Dict[str, Any]]:
        """
        Aggregate counts, percentages, revenue, and average R/F/M per segment.

        Returns one row per segment that has at least one customer. The route
        layer is responsible for filling in zero-count segments to make the
        full 7-segment payload.
        """
        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    segment,
                    COUNT(*)                                   AS count,
                    COALESCE(SUM(monetary), 0)                 AS revenue,
                    COALESCE(AVG(recency), 0)                  AS avg_recency,
                    COALESCE(AVG(frequency), 0)                AS avg_frequency,
                    COALESCE(AVG(monetary), 0)                 AS avg_monetary
                FROM crm_customer_scores
                GROUP BY segment
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            conn.close()

    def fetch_segment_snapshots(self, months: int) -> List[Dict[str, Any]]:
        """
        Fetch the most recent N months of segment snapshots.

        Args:
            months: Number of months to look back (must be >= 1).

        Returns:
            List of {snapshot_month: date, segment: str, customer_count: int}
            in ascending chronological order.
        """
        if months < 1:
            return []

        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT snapshot_month, segment, customer_count
                FROM crm_segment_snapshots
                WHERE snapshot_month >= (DATE_TRUNC('month', CURRENT_DATE)
                                         - (%s || ' months')::INTERVAL)::DATE
                ORDER BY snapshot_month ASC, segment ASC
            """, (months - 1,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            conn.close()

    def aggregate_rf_heatmap(self) -> List[Dict[str, Any]]:
        """
        Aggregate counts and total monetary value per (R, F) cell of the 5×5 grid.

        Returns only non-empty cells. The route fills in zeros for the
        remaining cells so the response always contains all 25.
        """
        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    r_score AS r,
                    f_score AS f,
                    COUNT(*)                                AS count,
                    COALESCE(SUM(monetary), 0)              AS total_monetary
                FROM crm_customer_scores
                GROUP BY r_score, f_score
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            conn.close()
