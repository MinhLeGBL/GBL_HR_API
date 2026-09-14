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
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional
from app.core.database import get_postgres_connection
from .repository import CRMRepository
from .rfm import SEGMENTS


# Strip the alpha prefix from a season code like "SS25" → "SS", "FW24" → "FW".
# Anything else (None, empty, no leading letters) → "Unknown".
_SEASON_PREFIX = re.compile(r'^([A-Za-z]+)')


def _season_prefix(raw: Optional[str]) -> str:
    if not raw:
        return 'Unknown'
    match = _SEASON_PREFIX.match(raw.strip())
    if not match:
        return 'Unknown'
    return match.group(1).upper()


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
            return {
                'success': False,
                'error':   'RFM data not yet computed. Run the recompute job first.',
                'code':    'NOT_COMPUTED',
            }
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
                # 18-digit BIGINT — JSON-serialize as string so JS clients
                # don't lose precision past Number.MAX_SAFE_INTEGER (CR #55).
                'id':               str(r['customer_sid']),
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
                'engagement_score': float(r.get('engagement_score') or 0),
                'segment':          r['segment'],
                'last_purchase':    lpd.isoformat() if lpd else None,
                'top_brand':        r['top_brand'] or '',
                'top_category':     r['top_category'] or '',
                'category_breadth': r['category_breadth'],
                # CR #77 (frontend): per-customer FP/MD split — revenue (VND)
                # and units sold. Same binary <=30% rule as the segment
                # summary; names match SegmentSummary (total_*/count_*).
                'total_full_price': int(r.get('fp_revenue') or 0),
                'total_discounted': int(r.get('discounted_revenue') or 0),
                'count_full_price': int(r.get('fp_units') or 0),
                'count_discounted': int(r.get('discounted_units') or 0),
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
                    # CR #76 (frontend): FP/MD split — revenue (VND) and units sold.
                    'total_full_price':  int(r['total_full_price']),
                    'total_discounted':  int(r['total_discounted']),
                    'count_full_price':  int(r['count_full_price']),
                    'count_discounted':  int(r['count_discounted']),
                })
            else:
                segments.append({
                    'segment': seg, 'count': 0, 'percentage': 0,
                    'revenue': 0, 'avg_recency': 0, 'avg_frequency': 0, 'avg_monetary': 0,
                    'total_full_price': 0, 'total_discounted': 0,
                    'count_full_price': 0, 'count_discounted': 0,
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
    # Product analysis (CR #48)
    # ------------------------------------------------------------------
    def get_product_analysis(self, group_by: str,
                              segment: Optional[str] = None) -> Dict[str, Any]:
        """
        Return per-segment ranked brand or category lists from the cached
        crm_product_scores table.

        Args:
            group_by: 'brand' or 'category'.
            segment: Optional — if provided, only that segment's list is
                returned (still keyed by segment in the response).

        Returns:
            {success: True, groups: {<segment>: [row, ...]}} on success;
            error dict (caller sends 503) when product scores haven't been
            computed yet.
        """
        if self.repo.count_product_scores() == 0:
            return {'success': False, 'error': 'RFM data not yet computed. Run the recompute job first.'}

        rows = self.repo.list_product_scores(group_by=group_by, segment=segment)

        groups: Dict[str, List[Dict]] = {seg: [] for seg in SEGMENTS} if not segment else {segment: []}
        for r in rows:
            seg = r['segment']
            groups.setdefault(seg, []).append({
                'name':           r['name'],
                'customer_count': int(r['customer_count']),
                'recency_days':   float(r['recency_days']),
                'frequency':      float(r['frequency']),
                'monetary':       int(r['monetary']),
                'r_score':        int(r['r_score']),
                'f_score':        int(r['f_score']),
                'm_score':        int(r['m_score']),
                'weighted_score': float(r['weighted_score']),
                'c_score':        int(r['c_score']),
                'compound_score': float(r['compound_score']),
            })

        return {'success': True, 'groups': groups}

    # ------------------------------------------------------------------
    # Drilldowns (CR #53, CR #54) — live Oracle, click-triggered
    # ------------------------------------------------------------------
    def get_customer_drilldown(self, customer_sid: int) -> Dict[str, Any]:
        """
        Per-customer drilldown for the popup dialog. Live Oracle aggregation
        scoped to the rolling 24-month window.

        Returns:
            {success: True, drilldown: {...}} on success;
            {success: False, error: ...} for 503 (RFM not computed) and 404
            (customer has no transactions in window).
        """
        err = self._ensure_computed()
        if err:
            return err

        raw = self.repo.fetch_customer_drilldown(customer_sid)
        if not raw['totals']:
            return {
                'success': False,
                'error':   'Customer not found or has no transactions',
                'code':    'NOT_FOUND',
            }

        brand_rows = sorted(raw['brands'],
                            key=lambda b: (-b['revenue'], b['brand_name'] or ''))
        category_rows = sorted(raw['categories'],
                               key=lambda c: (-c['revenue'], c['category_name'] or ''))

        avg_brand_recency = (
            sum(b['brand_recency_days'] for b in brand_rows) / len(brand_rows)
            if brand_rows else 0.0
        )

        return {
            'success': True,
            'drilldown': {
                'total_monetary':         raw['totals']['total_monetary'],
                'total_bills':            raw['totals']['total_bills'],
                'avg_brand_recency_days': round(avg_brand_recency, 2),
                'brand_distribution': [
                    {'name': b['brand_name'] or '(Unknown)', 'monetary': b['revenue']}
                    for b in brand_rows
                ],
                'category_distribution': [
                    {'name': c['category_name'], 'monetary': c['revenue']}
                    for c in category_rows
                ],
                # CR #76 (frontend): FP/MD split — revenue (VND) + units sold.
                'price_distribution': {
                    'full_price':       raw['totals']['fp_revenue'],
                    'discounted':       raw['totals']['discounted_revenue'],
                    'full_price_units': raw['totals']['fp_units'],
                    'discounted_units': raw['totals']['discounted_units'],
                },
            },
        }

    def get_brand_drilldown(self, brand_name: str, segment: str) -> Dict[str, Any]:
        """
        Per-brand drilldown for the popup dialog. Live Oracle aggregation +
        Python-side segment filter (avoids the 1,000-element IN-list limit).

        Returns:
            {success: True, drilldown: {...}} on success;
            error dict with `code` ('INVALID_SEGMENT', 'NOT_COMPUTED',
            or 'NOT_FOUND') that the route layer maps to a status code.
        """
        # Validate the segment before checking the cache: a malformed request
        # is a 400 even if the cache is empty (which would otherwise short-
        # circuit to 503).
        if segment not in SEGMENTS:
            return {
                'success': False,
                'error':   f'Invalid segment: {segment}',
                'code':    'INVALID_SEGMENT',
            }

        err = self._ensure_computed()
        if err:
            return err

        segment_sids = set(self.repo.list_customer_sids_in_segment(segment))
        raw = self.repo.fetch_brand_drilldown(brand_name)

        if not raw['totals']:
            return {
                'success': False,
                'error':   'Brand not found',
                'code':    'NOT_FOUND',
            }

        # Headline metrics — split each customer's contribution by segment
        # membership.
        revenue_in_segment = 0
        revenue_all_segments = 0
        items_in_segment = 0
        for r in raw['totals']:
            revenue_all_segments += r['revenue']
            if r['customer_sid'] in segment_sids:
                revenue_in_segment += r['revenue']
                items_in_segment   += r['items']

        # Distribution helper: pool revenue by name (segment vs all) and emit
        # sorted [{name, monetary}, ...] lists.
        def _pool(rows: List[Dict], key: str) -> tuple:
            seg_d:  Dict[str, int] = defaultdict(int)
            all_d:  Dict[str, int] = defaultdict(int)
            for row in rows:
                name = row[key]
                all_d[name] += row['revenue']
                if row['customer_sid'] in segment_sids:
                    seg_d[name] += row['revenue']
            seg_list = sorted(
                ({'name': n, 'monetary': v} for n, v in seg_d.items()),
                key=lambda x: (-x['monetary'], x['name']),
            )
            all_list = sorted(
                ({'name': n, 'monetary': v} for n, v in all_d.items()),
                key=lambda x: (-x['monetary'], x['name']),
            )
            return seg_list, all_list

        # Season pooling first parses the alpha prefix from each row.
        season_rows = [
            {'customer_sid': r['customer_sid'],
             'season_name': _season_prefix(r['raw_season']),
             'revenue':     r['revenue']}
            for r in raw['seasons']
        ]
        season_seg, season_all = _pool(season_rows, 'season_name')
        category_seg, category_all = _pool(raw['categories'], 'category_name')

        return {
            'success': True,
            'drilldown': {
                'revenue_in_segment':            revenue_in_segment,
                'revenue_all_segments':          revenue_all_segments,
                'items_in_segment':              items_in_segment,
                'season_distribution_segment':   season_seg,
                'season_distribution_all':       season_all,
                'category_distribution_segment': category_seg,
                'category_distribution_all':     category_all,
            },
        }

    # ------------------------------------------------------------------
    # Admin: RFM config management
    # ------------------------------------------------------------------
    def get_config(self) -> Dict[str, Any]:
        """Get current RFM weights configuration."""
        config = self.repo.get_config()
        return {'success': True, 'config': config}

    def update_config(self, config: Dict[str, float]) -> Dict[str, Any]:
        """
        Update RFM weights. Validates each of the three weight sets
        (segmentation, engagement, product-analysis) sums to 1.0 and that
        every value is in [0, 1].
        """
        w_sum  = round(config.get('w_recency', 0)  + config.get('w_frequency', 0)  + config.get('w_monetary', 0),  2)
        e_sum  = round(config.get('e_recency', 0)  + config.get('e_frequency', 0)  + config.get('e_monetary', 0),  2)
        pw_sum = round(config.get('pw_recency', 0) + config.get('pw_frequency', 0) + config.get('pw_monetary', 0), 2)

        if w_sum != 1.0:
            return {'success': False, 'error': f'Weighted score coefficients must sum to 1.0, got {w_sum}'}
        if e_sum != 1.0:
            return {'success': False, 'error': f'Engagement score coefficients must sum to 1.0, got {e_sum}'}
        if pw_sum != 1.0:
            return {'success': False, 'error': f'Product-analysis weight coefficients must sum to 1.0, got {pw_sum}'}

        for key in ('w_recency',  'w_frequency',  'w_monetary',
                    'e_recency',  'e_frequency',  'e_monetary',
                    'pw_recency', 'pw_frequency', 'pw_monetary'):
            val = config.get(key, 0)
            if val < 0 or val > 1:
                return {'success': False, 'error': f'{key} must be between 0 and 1, got {val}'}

        self.repo.update_config(config)
        return {'success': True, 'config': config}

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
                    engagement_score   NUMERIC(3,2) NOT NULL DEFAULT 0,
                    segment            TEXT NOT NULL,
                    top_brand          TEXT,
                    top_category       TEXT,
                    category_breadth   INTEGER NOT NULL DEFAULT 0,
                    last_purchase_date DATE,
                    fp_revenue         BIGINT  NOT NULL DEFAULT 0,
                    discounted_revenue BIGINT  NOT NULL DEFAULT 0,
                    fp_units           INTEGER NOT NULL DEFAULT 0,
                    discounted_units   INTEGER NOT NULL DEFAULT 0,
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
            # CR #45: add engagement_score column if table already existed without it
            cursor.execute("""
                ALTER TABLE crm_customer_scores
                ADD COLUMN IF NOT EXISTS engagement_score NUMERIC(3,2) NOT NULL DEFAULT 0
            """)
            # CR #76 (frontend): FP/MD split columns for legacy tables.
            cursor.execute("""
                ALTER TABLE crm_customer_scores
                ADD COLUMN IF NOT EXISTS fp_revenue         BIGINT  NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS discounted_revenue BIGINT  NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS fp_units           INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS discounted_units   INTEGER NOT NULL DEFAULT 0
            """)

            # Per-segment per-brand/category product analytics (CR #48,
            # methodology revised by CR #49, customer_count + compound score
            # added by CR #50/#51). Fully overwritten each recompute.
            # group_by distinguishes the 'brand' vs 'category' rows so the
            # table covers both.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS crm_product_scores (
                    id              BIGSERIAL PRIMARY KEY,
                    group_by        TEXT NOT NULL CHECK (group_by IN ('brand', 'category')),
                    segment         TEXT NOT NULL,
                    name            TEXT NOT NULL,
                    customer_count  INTEGER NOT NULL DEFAULT 0,
                    recency_days    NUMERIC(10,2) NOT NULL,
                    frequency       NUMERIC(10,2) NOT NULL,
                    monetary        BIGINT  NOT NULL,
                    r_score         SMALLINT NOT NULL CHECK (r_score BETWEEN 1 AND 5),
                    f_score         SMALLINT NOT NULL CHECK (f_score BETWEEN 1 AND 5),
                    m_score         SMALLINT NOT NULL CHECK (m_score BETWEEN 1 AND 5),
                    weighted_score  NUMERIC(3,2) NOT NULL,
                    c_score         SMALLINT NOT NULL DEFAULT 0,
                    compound_score  NUMERIC(4,2) NOT NULL DEFAULT 0,
                    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (group_by, segment, name)
                )
            """)
            # CR #49: widen frequency from INTEGER to NUMERIC(10,2) on legacy
            # tables created under the CR #48 schema.
            cursor.execute("""
                DO $$
                BEGIN
                    IF (
                        SELECT data_type FROM information_schema.columns
                        WHERE table_name = 'crm_product_scores'
                          AND column_name = 'frequency'
                    ) = 'integer' THEN
                        ALTER TABLE crm_product_scores
                            ALTER COLUMN frequency TYPE NUMERIC(10,2);
                    END IF;
                END $$;
            """)
            # CR #50: customer_count column.
            cursor.execute("""
                ALTER TABLE crm_product_scores
                ADD COLUMN IF NOT EXISTS customer_count INTEGER NOT NULL DEFAULT 0
            """)
            # CR #51: c_score + compound_score columns.
            # Defaults are 0 (intentionally outside the recompute's 1..5
            # range) so legacy rows roundtrip the migration; the next
            # recompute overwrites every row with valid values.
            cursor.execute("""
                ALTER TABLE crm_product_scores
                ADD COLUMN IF NOT EXISTS c_score SMALLINT NOT NULL DEFAULT 0
            """)
            cursor.execute("""
                ALTER TABLE crm_product_scores
                ADD COLUMN IF NOT EXISTS compound_score NUMERIC(4,2) NOT NULL DEFAULT 0
            """)
            # CR #51: lookup index now keyed on compound_score (primary sort
            # key replaces weighted_score). Drop the old index by name then
            # recreate idempotently with the new column.
            cursor.execute("DROP INDEX IF EXISTS idx_crm_product_scores_lookup")
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_crm_product_scores_lookup
                    ON crm_product_scores (group_by, segment, compound_score DESC)
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

            # RFM configuration — single row, stores all tunable weights.
            # Seeded with defaults on first run; updated via admin endpoint.
            #
            # Three weight sets:
            #   w_*  — segmentation (decides which customer is VIC, Loyalist, etc.)
            #   e_*  — engagement (recency-heavy, drives outreach prioritization)
            #   pw_* — product analysis ranking (CR #52, ranks brands/categories
            #          within an already-formed segment; defaults mirror w_*)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS crm_config (
                    id                      INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                    w_recency               NUMERIC(3,2) NOT NULL DEFAULT 0.3,
                    w_frequency             NUMERIC(3,2) NOT NULL DEFAULT 0.3,
                    w_monetary              NUMERIC(3,2) NOT NULL DEFAULT 0.4,
                    e_recency               NUMERIC(3,2) NOT NULL DEFAULT 0.5,
                    e_frequency             NUMERIC(3,2) NOT NULL DEFAULT 0.3,
                    e_monetary              NUMERIC(3,2) NOT NULL DEFAULT 0.2,
                    pw_recency              NUMERIC(3,2) NOT NULL DEFAULT 0.3,
                    pw_frequency            NUMERIC(3,2) NOT NULL DEFAULT 0.3,
                    pw_monetary             NUMERIC(3,2) NOT NULL DEFAULT 0.4,
                    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # Seed default row if empty
            cursor.execute("""
                INSERT INTO crm_config (id) VALUES (1) ON CONFLICT DO NOTHING
            """)
            # CR #52: product-analysis weight columns on legacy tables.
            cursor.execute("""
                ALTER TABLE crm_config
                ADD COLUMN IF NOT EXISTS pw_recency NUMERIC(3,2) NOT NULL DEFAULT 0.3
            """)
            cursor.execute("""
                ALTER TABLE crm_config
                ADD COLUMN IF NOT EXISTS pw_frequency NUMERIC(3,2) NOT NULL DEFAULT 0.3
            """)
            cursor.execute("""
                ALTER TABLE crm_config
                ADD COLUMN IF NOT EXISTS pw_monetary NUMERIC(3,2) NOT NULL DEFAULT 0.4
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
