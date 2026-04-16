"""
CRM Service — RFM-based customer segmentation for luxury fashion retail.

Architecture:
- Oracle (RPS.CUSTOMER + DOCUMENT) is the source of truth for customer + transaction data.
- PostgreSQL caches the derived RFM scores (crm_customer_scores) and monthly
  segment snapshots (crm_segment_snapshots).
- A daily background job (scripts/jobs/crm_recompute.py) refreshes both tables.
- This service reads from PostgreSQL only. If the cache is empty, endpoints
  return 503 (RFM not yet computed).
"""
from typing import Any, Dict
from app.core.database import get_postgres_connection


class CRMService:
    """Service for CRM RFM analytics. Reads from Postgres cache only."""

    # ------------------------------------------------------------------
    # Database initialization (called by scripts/database/init_db.py)
    # ------------------------------------------------------------------
    def init_database(self) -> Dict[str, Any]:
        """Create CRM tables if they do not exist."""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Current RFM state — one row per customer, fully overwritten on each recompute.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS crm_customer_scores (
                    customer_sid       BIGINT PRIMARY KEY,
                    name               TEXT,
                    email              TEXT,
                    phone              TEXT,
                    recency            INTEGER NOT NULL,
                    frequency          INTEGER NOT NULL,
                    monetary           BIGINT  NOT NULL,
                    r_score            SMALLINT NOT NULL CHECK (r_score BETWEEN 1 AND 5),
                    f_score            SMALLINT NOT NULL CHECK (f_score BETWEEN 1 AND 5),
                    m_score            SMALLINT NOT NULL CHECK (m_score BETWEEN 1 AND 5),
                    weighted_score     NUMERIC(3,2) NOT NULL,
                    segment            TEXT NOT NULL,
                    top_brand          TEXT,
                    top_category       TEXT,
                    category_breadth   INTEGER NOT NULL DEFAULT 0,
                    last_purchase_date DATE,
                    computed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_crm_scores_segment
                    ON crm_customer_scores (segment)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_crm_scores_weighted_desc
                    ON crm_customer_scores (weighted_score DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_crm_scores_rf
                    ON crm_customer_scores (r_score, f_score)
            """)

            # Monthly snapshot of segment distribution — append-only history for trend chart.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS crm_segment_snapshots (
                    id              BIGSERIAL PRIMARY KEY,
                    snapshot_month  DATE NOT NULL,
                    segment         TEXT NOT NULL,
                    customer_count  INTEGER NOT NULL DEFAULT 0,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (snapshot_month, segment)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_crm_snapshots_month
                    ON crm_segment_snapshots (snapshot_month DESC)
            """)

            conn.commit()
            cursor.close()
            return {'success': True}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}

        finally:
            if conn:
                conn.close()
