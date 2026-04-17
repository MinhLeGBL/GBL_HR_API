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
from typing import Any, Dict, List, Optional
from app.core.database import get_postgres_connection
from .repository import CRMRepository
from .rfm import SEGMENTS


class CRMService:
    """Service for CRM RFM analytics. Reads from Postgres cache only."""

    def __init__(self):
        self.repo = CRMRepository()

    # ------------------------------------------------------------------
    # Cache check
    # ------------------------------------------------------------------
    def _ensure_computed(self) -> Optional[Dict]:
        """Return an error dict if scores haven't been computed yet, else None."""
        if self.repo.count_scored_customers() == 0:
            return {'success': False, 'error': 'RFM data not yet computed. Run the recompute job first.'}
        return None

    # ------------------------------------------------------------------
    # Endpoint logic
    # ------------------------------------------------------------------
    def get_customers(self, segment: Optional[str] = None,
                      min_weighted_score: Optional[float] = None) -> Dict[str, Any]:
        """List customers with optional filters. Returns CR #40 response shape."""
        err = self._ensure_computed()
        if err:
            return err

        rows = self.repo.list_customer_scores(
            segment=segment, min_weighted_score=min_weighted_score,
        )
        customers = []
        for r in rows:
            lpd = r['last_purchase_date']
            customers.append({
                'id':               r['customer_sid'],
                'name':             r['name'] or '',
                'email':            r['email'] or '',
                'phone':            r['phone'] or '',
                'recency':          r['recency'],
                'frequency':        r['frequency'],
                'monetary':         int(r['monetary']),
                'r_score':          r['r_score'],
                'f_score':          r['f_score'],
                'm_score':          r['m_score'],
                'weighted_score':   float(r['weighted_score']),
                'segment':          r['segment'],
                'last_purchase':    lpd.isoformat() if lpd else None,
                'top_brand':        r['top_brand'] or '',
                'top_category':     r['top_category'] or '',
                'category_breadth': r['category_breadth'],
            })

        return {'success': True, 'customers': customers, 'total': len(customers)}

    def get_segments_summary(self) -> Dict[str, Any]:
        """Aggregate stats per segment. Returns all 7 segments."""
        err = self._ensure_computed()
        if err:
            return err

        raw = self.repo.aggregate_segment_summary()
        total_customers = sum(r['count'] for r in raw)
        lookup = {r['segment']: r for r in raw}

        segments: List[Dict] = []
        for seg in SEGMENTS:
            r = lookup.get(seg)
            if r:
                pct = round(r['count'] / total_customers * 100, 1) if total_customers else 0
                segments.append({
                    'segment':       seg,
                    'count':         r['count'],
                    'percentage':    pct,
                    'revenue':       int(r['revenue']),
                    'avg_recency':   round(float(r['avg_recency']), 1),
                    'avg_frequency': round(float(r['avg_frequency']), 1),
                    'avg_monetary':  int(r['avg_monetary']),
                })
            else:
                segments.append({
                    'segment': seg, 'count': 0, 'percentage': 0,
                    'revenue': 0, 'avg_recency': 0, 'avg_frequency': 0, 'avg_monetary': 0,
                })

        return {'success': True, 'segments': segments}

    def get_segments_trends(self, months: int = 12) -> Dict[str, Any]:
        """Segment counts over the last N months, pivoted for the frontend chart."""
        snapshots = self.repo.fetch_segment_snapshots(months)
        if not snapshots:
            return {'success': True, 'trends': []}

        # Group by month, then pivot into {month: str, VIC: int, ...}
        month_map: Dict[str, Dict[str, int]] = {}
        for s in snapshots:
            m = s['snapshot_month']
            key = m.strftime('%Y-%m') if hasattr(m, 'strftime') else str(m)[:7]
            if key not in month_map:
                month_map[key] = {seg: 0 for seg in SEGMENTS}
            month_map[key][s['segment']] = s['customer_count']

        trends = []
        for month_str in sorted(month_map):
            row = {'month': month_str}
            row.update(month_map[month_str])
            trends.append(row)

        return {'success': True, 'trends': trends}

    def get_heatmap(self) -> Dict[str, Any]:
        """5×5 (R, F) grid with count and total monetary per cell."""
        err = self._ensure_computed()
        if err:
            return err

        raw_cells = self.repo.aggregate_rf_heatmap()
        lookup = {(c['r'], c['f']): c for c in raw_cells}

        cells = []
        for r in range(1, 6):
            for f in range(1, 6):
                existing = lookup.get((r, f))
                if existing:
                    cells.append({
                        'r': r, 'f': f,
                        'count': existing['count'],
                        'total_monetary': int(existing['total_monetary']),
                    })
                else:
                    cells.append({'r': r, 'f': f, 'count': 0, 'total_monetary': 0})

        return {'success': True, 'cells': cells}

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
