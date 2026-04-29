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

    def fetch_product_aggregates(self, group_by: str) -> List[Dict[str, Any]]:
        """
        Fetch per-customer per-(brand|category) aggregates for product analytics.

        Each row is one customer's totals for one brand/category, with their
        own recency (days since their most recent purchase of it). The pooler
        averages these values across customers per segment (CR #49 nested
        approach).

        Args:
            group_by: 'brand' or 'category'.

        Returns:
            List of dicts with keys:
                customer_sid (int), group_name (str),
                items (int, SUM(QTY) for the customer×group),
                revenue (int VND, the customer's total revenue for the group),
                customer_recency_days (int, days since customer's most recent
                                       purchase of the group)
        """
        if group_by == 'brand':
            sql = CRMQueries.PRODUCT_BRAND_AGGREGATES
        elif group_by == 'category':
            sql = CRMQueries.PRODUCT_CATEGORY_AGGREGATES
        else:
            raise ValueError(f"group_by must be 'brand' or 'category', got {group_by!r}")

        conn = get_oracle_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()

        return [
            {
                'customer_sid':          int(r[0]),
                'group_name':            r[1],
                'items':                 int(r[2] or 0),
                'revenue':               int(r[3] or 0),
                'customer_recency_days': int(r[4] or 0),
            }
            for r in rows
        ]

    def fetch_customer_drilldown(self, customer_sid: int) -> Dict[str, Any]:
        """
        Fetch all live aggregates for a single customer's drilldown view.

        Returns a dict with keys:
            totals: {total_monetary, total_bills, fp_revenue, discounted_revenue}
                    (None if customer has zero matching transactions)
            brands: list of {brand_name, revenue, brand_recency_days}
            categories: list of {category_name, revenue}
        """
        conn = get_oracle_connection()
        try:
            cur = conn.cursor()

            cur.execute(CRMQueries.CUSTOMER_DRILLDOWN_TOTALS,
                        {'customer_sid': customer_sid})
            row = cur.fetchone()
            totals = None
            if row and row[1]:  # row[1] = total_bills; None or 0 → no purchases
                totals = {
                    'total_monetary':     int(row[0] or 0),
                    'total_bills':        int(row[1] or 0),
                    'fp_revenue':         int(row[2] or 0),
                    'discounted_revenue': int(row[3] or 0),
                }

            cur.execute(CRMQueries.CUSTOMER_DRILLDOWN_BRANDS,
                        {'customer_sid': customer_sid})
            brands = [
                {
                    'brand_name':         r[0],
                    'revenue':            int(r[1] or 0),
                    'brand_recency_days': int(r[2] or 0),
                }
                for r in cur.fetchall()
            ]

            cur.execute(CRMQueries.CUSTOMER_DRILLDOWN_CATEGORIES,
                        {'customer_sid': customer_sid})
            categories = [
                {
                    'category_name': r[0],
                    'revenue':       int(r[1] or 0),
                }
                for r in cur.fetchall()
            ]
            cur.close()
        finally:
            conn.close()

        return {'totals': totals, 'brands': brands, 'categories': categories}

    def fetch_brand_drilldown(self, brand_name: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Fetch all per-customer aggregates for a single brand's drilldown
        view, across every customer in the 24-month window. The service
        layer filters by segment in Python.

        Returns a dict:
            totals:     [{customer_sid, revenue, items}, ...]
            seasons:    [{customer_sid, raw_season, revenue}, ...]
            categories: [{customer_sid, category_name, revenue}, ...]
        """
        conn = get_oracle_connection()
        try:
            cur = conn.cursor()

            cur.execute(CRMQueries.BRAND_DRILLDOWN_TOTALS,
                        {'brand_name': brand_name})
            totals = [
                {
                    'customer_sid': int(r[0]),
                    'revenue':      int(r[1] or 0),
                    'items':        int(r[2] or 0),
                }
                for r in cur.fetchall()
            ]

            cur.execute(CRMQueries.BRAND_DRILLDOWN_SEASONS,
                        {'brand_name': brand_name})
            seasons = [
                {
                    'customer_sid': int(r[0]),
                    'raw_season':   r[1],
                    'revenue':      int(r[2] or 0),
                }
                for r in cur.fetchall()
            ]

            cur.execute(CRMQueries.BRAND_DRILLDOWN_CATEGORIES,
                        {'brand_name': brand_name})
            categories = [
                {
                    'customer_sid':  int(r[0]),
                    'category_name': r[1],
                    'revenue':       int(r[2] or 0),
                }
                for r in cur.fetchall()
            ]
            cur.close()
        finally:
            conn.close()

        return {'totals': totals, 'seasons': seasons, 'categories': categories}

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

        Three weight sets (CR #46, CR #52):
        - w_*  segmentation weights
        - e_*  engagement-score weights
        - pw_* product-analysis ranking weights
        """
        defaults = {
            'w_recency':  0.3, 'w_frequency':  0.3, 'w_monetary':  0.4,
            'e_recency':  0.5, 'e_frequency':  0.3, 'e_monetary':  0.2,
            'pw_recency': 0.3, 'pw_frequency': 0.3, 'pw_monetary': 0.4,
        }
        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT w_recency,  w_frequency,  w_monetary,
                       e_recency,  e_frequency,  e_monetary,
                       pw_recency, pw_frequency, pw_monetary
                FROM crm_config WHERE id = 1
            """)
            row = cur.fetchone()
            cur.close()
            if not row:
                return defaults
            return {
                'w_recency':  float(row[0]), 'w_frequency':  float(row[1]), 'w_monetary':  float(row[2]),
                'e_recency':  float(row[3]), 'e_frequency':  float(row[4]), 'e_monetary':  float(row[5]),
                'pw_recency': float(row[6]), 'pw_frequency': float(row[7]), 'pw_monetary': float(row[8]),
            }
        except Exception:
            return defaults
        finally:
            conn.close()

    def update_config(self, config: Dict[str, float]) -> bool:
        """Update RFM weights in crm_config. Creates the table if it doesn't exist."""
        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            # Ensure table + default row exist (idempotent)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crm_config (
                    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                    w_recency NUMERIC(3,2) NOT NULL DEFAULT 0.3,
                    w_frequency NUMERIC(3,2) NOT NULL DEFAULT 0.3,
                    w_monetary NUMERIC(3,2) NOT NULL DEFAULT 0.4,
                    e_recency NUMERIC(3,2) NOT NULL DEFAULT 0.5,
                    e_frequency NUMERIC(3,2) NOT NULL DEFAULT 0.3,
                    e_monetary NUMERIC(3,2) NOT NULL DEFAULT 0.2,
                    pw_recency NUMERIC(3,2) NOT NULL DEFAULT 0.3,
                    pw_frequency NUMERIC(3,2) NOT NULL DEFAULT 0.3,
                    pw_monetary NUMERIC(3,2) NOT NULL DEFAULT 0.4,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # Idempotent migration for legacy crm_config tables that
            # pre-date CR #52.
            cur.execute("""
                ALTER TABLE crm_config
                ADD COLUMN IF NOT EXISTS pw_recency NUMERIC(3,2) NOT NULL DEFAULT 0.3
            """)
            cur.execute("""
                ALTER TABLE crm_config
                ADD COLUMN IF NOT EXISTS pw_frequency NUMERIC(3,2) NOT NULL DEFAULT 0.3
            """)
            cur.execute("""
                ALTER TABLE crm_config
                ADD COLUMN IF NOT EXISTS pw_monetary NUMERIC(3,2) NOT NULL DEFAULT 0.4
            """)
            cur.execute("INSERT INTO crm_config (id) VALUES (1) ON CONFLICT DO NOTHING")
            cur.execute("""
                UPDATE crm_config SET
                    w_recency  = %(w_recency)s,  w_frequency  = %(w_frequency)s,  w_monetary  = %(w_monetary)s,
                    e_recency  = %(e_recency)s,  e_frequency  = %(e_frequency)s,  e_monetary  = %(e_monetary)s,
                    pw_recency = %(pw_recency)s, pw_frequency = %(pw_frequency)s, pw_monetary = %(pw_monetary)s,
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

    def replace_product_scores(self, scored_rows: List[Dict[str, Any]]) -> int:
        """
        Atomically replace the entire crm_product_scores table.

        TRUNCATE + bulk INSERT in one transaction so readers see either the
        old snapshot or the new one, never an empty table mid-recompute.
        Both 'brand' and 'category' rows live in the same table — pass them
        all together.

        Args:
            scored_rows: List of dicts with keys group_by, segment, name,
                recency_days, frequency, monetary, r_score, f_score, m_score,
                weighted_score.

        Returns:
            Number of rows inserted.
        """
        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            cur.execute('TRUNCATE TABLE crm_product_scores')
            if not scored_rows:
                conn.commit()
                cur.close()
                return 0

            insert_sql = """
                INSERT INTO crm_product_scores (
                    group_by, segment, name, customer_count,
                    recency_days, frequency, monetary,
                    r_score, f_score, m_score,
                    weighted_score, c_score, compound_score
                ) VALUES (
                    %(group_by)s, %(segment)s, %(name)s, %(customer_count)s,
                    %(recency_days)s, %(frequency)s, %(monetary)s,
                    %(r_score)s, %(f_score)s, %(m_score)s,
                    %(weighted_score)s, %(c_score)s, %(compound_score)s
                )
            """
            cur.executemany(insert_sql, scored_rows)
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

    def list_customer_sids_in_segment(self, segment: str) -> List[int]:
        """Return the customer_sids assigned to ``segment``. [] if none."""
        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                'SELECT customer_sid FROM crm_customer_scores WHERE segment = %s',
                (segment,),
            )
            sids = [int(r[0]) for r in cur.fetchall()]
            cur.close()
            return sids
        finally:
            conn.close()

    def count_product_scores(self) -> int:
        """Cheap existence check used to decide between data and 503."""
        conn = get_postgres_connection()
        try:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM crm_product_scores')
            n = cur.fetchone()[0]
            cur.close()
            return n
        except Exception:
            return 0
        finally:
            conn.close()

    def list_product_scores(self, group_by: str,
                              segment: Optional[str] = None
                              ) -> List[Dict[str, Any]]:
        """
        Fetch product scores filtered by group_by, optionally by segment.

        Returns rows ordered by segment ASC, then weighted_score DESC so the
        service can group_by segment in input order.
        """
        clauses = ['group_by = %s']
        params: List[Any] = [group_by]
        if segment:
            clauses.append('segment = %s')
            params.append(segment)

        sql = f"""
            SELECT segment, name, customer_count,
                   recency_days, frequency, monetary,
                   r_score, f_score, m_score,
                   weighted_score, c_score, compound_score
            FROM crm_product_scores
            WHERE {' AND '.join(clauses)}
            ORDER BY segment ASC, compound_score DESC, name ASC
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
