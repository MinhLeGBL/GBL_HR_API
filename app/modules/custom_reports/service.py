"""
Custom Reports Service.

First report: size-by-brand-season — for a given list of seasons, returns
per (brand, size, season) the qty imported, sold, on-hand, sell-through %,
and average days-to-sell using FIFO unit matching.

The FIFO matching pairs each sold unit with the oldest available receipt
unit for the same item (SKU), computes the elapsed days, then averages
across all matched units in the group (weighted naturally because each
matched unit contributes one day-count).
"""
import re
from collections import deque
from typing import Any, Dict, List, Optional

import pandas as pd

from app.core.database import get_oracle_connection
from .queries import CustomReportsQueries


# Season codes the report accepts — validates against arbitrary user input
# before it gets interpolated into SQL. Alphanumerics only.
SEASON_RE = re.compile(r'^[A-Za-z0-9]+$')

# Default seasons for v0.1 — the user's initial scope was SS25 + SS26
DEFAULT_SEASONS = ('SS25', 'SS26')

# Default brand focus list. Names are exact `vendor.vend_name` values, so
# upstream resolution (e.g. AMQ → ALEXANDER MCQUEEN, MARNI → MARNI
# INTERNATIONAL S.A, MM/MM6 → MAISON MARGIELA MM6) happens once here, not
# in the SQL. ELEVENTY and THE ATTICO have no SS25/SS26 items today but
# remain in the list so they get picked up automatically for future seasons.
FOCUS_BRANDS = (
    'AKRIS',
    'AKRIS PUNTO',
    'ALAIA',
    'ALEXANDER MCQUEEN',
    'COURREGES',
    'ELEVENTY',
    'IBLUES',
    'JIL SANDER',
    'KHAITE',
    'MAISON MARGIELA MM6',
    'MARELLA',
    'MARNI INTERNATIONAL S.A',
    'THE ATTICO',
)

# ----------------------------------------------------------------------
# Size normalization
# ----------------------------------------------------------------------
# Per-brand numeric sizing system used for RTW. Used to look up the
# letter equivalent of a numeric label (e.g. AKRIS uses FR sizing →
# AKRIS '36' = FR 36 = M, while MARNI uses IT → MARNI '36' = IT 36 = XS).
# Unknown brands fall through to IT (most common in the catalog).
BRAND_SIZE_SYSTEMS = {
    'AKRIS':                   'FR',
    'AKRIS PUNTO':             'FR',
    'ALAIA':                   'FR',
    'ALEXANDER MCQUEEN':       'IT',
    'COURREGES':               'FR',
    'ELEVENTY':                'IT',
    'IBLUES':                  'IT',
    'JIL SANDER':              'DE',
    'KHAITE':                  'US',
    'MAISON MARGIELA MM6':     'IT',
    'MARELLA':                 'IT',
    'MARNI INTERNATIONAL S.A': 'IT',
    'THE ATTICO':              'IT',
}
DEFAULT_SIZE_SYSTEM = 'IT'

# Numeric → letter conversion per sizing system (women's RTW).
# Each mapping uses the smallest letter that the numeric represents in
# that system, following modern luxury-fashion industry convention.
RTW_NUMERIC_TO_LETTER = {
    'IT': {
        '32': 'XXS', '34': 'XXS',
        '36': 'XS',
        '38': 'S',
        '40': 'M',
        '42': 'L',
        '44': 'XL',
        '46': 'XXL', '48': 'XXL', '50': 'XXL',
    },
    'FR': {
        '30': 'XXS', '32': 'XXS',
        '34': 'XS',
        '36': 'S',
        '38': 'M',
        '40': 'L',
        '42': 'XL',
        '44': 'XXL', '46': 'XXL',
    },
    'DE': {
        '28': 'XXS', '30': 'XXS',
        '32': 'XS',
        '34': 'S',
        '36': 'M',
        '38': 'L',
        '40': 'XL',
        '42': 'XXL', '44': 'XXL',
    },
    'US': {
        '00': 'XXS', '0': 'XXS',
        '2': 'XS',
        '4': 'S',
        '6': 'M',
        '8': 'L',
        '10': 'XL', '12': 'XL',
        '14': 'XXL', '16': 'XXL',
    },
}

# Letter sizes are universal already.
LETTER_SIZES = {'XXS', 'XS', 'S', 'M', 'L', 'XL', 'XXL', 'XXXL'}

# "One size" markers — items with these labels are dropped from the
# size report (jewelry, bags, scarves, etc.).
ONE_SIZE_LABELS = {
    'OS', 'O/S', 'O', 'U', 'TU', 'ONE SIZE', 'ONESIZE', 'UNI', 'TAGLIA UNICA',
}

# Departments that have meaningful sized items. Items in other
# departments are dropped from the size report.
SIZED_DEPARTMENTS = {'WRTW', 'MRTW', 'WSHO', 'MSHO'}

# Departments where the item_size is an EU shoe size and is kept as-is
# (no letter normalization makes sense — a 38 shoe ≠ a 38.5 shoe).
SHOE_DEPARTMENTS = {'WSHO', 'MSHO'}


def is_meaningful_size(raw_size) -> bool:
    """True iff raw_size is a usable label (not NULL, empty, or one-size)."""
    if raw_size is None:
        return False
    if not isinstance(raw_size, str):
        return False
    s = raw_size.strip().upper()
    if not s:
        return False
    if s in ONE_SIZE_LABELS:
        return False
    return True


def normalize_size(raw_size: str, brand: str, department: str) -> str:
    """
    Convert a raw `item_size` label into a canonical bucket.

    - Shoe departments → return the size as-is (EU numeric is already
      universal across brands).
    - Hybrid notation like '40/M' or '42/L' → take the letter half (it's
      pre-normalized by the merchant).
    - Letter sizes (XS, S, M, L, …) → upper-cased letter.
    - Numeric sizes → looked up in RTW_NUMERIC_TO_LETTER using the brand's
      sizing system.

    Falls through to the raw value (upper-cased) if no rule matches —
    surfaces as an "unmapped" bucket in the report so a human can extend
    the conversion table.
    """
    if not is_meaningful_size(raw_size):
        return ''   # caller is expected to have filtered these out
    s = raw_size.strip()
    dept = (department or '').upper()

    # Shoes: don't translate.
    if dept in SHOE_DEPARTMENTS:
        return s

    s_upper = s.upper()

    # Pre-normalized hybrid like '40/M', '42/L', 'L/XL'. Take the rightmost
    # part if it's a known letter; otherwise leave the whole token alone.
    if '/' in s_upper:
        parts = [p.strip() for p in s_upper.split('/') if p.strip()]
        for part in reversed(parts):
            if part in LETTER_SIZES:
                return part

    # Plain letter sizes
    if s_upper in LETTER_SIZES:
        return s_upper

    # Numeric — look up in the brand's sizing-system table
    system = BRAND_SIZE_SYSTEMS.get(brand, DEFAULT_SIZE_SYSTEM)
    table = RTW_NUMERIC_TO_LETTER.get(system, {})
    if s in table:
        return table[s]

    # No match — surface raw so it shows up in the report as an outlier
    return s_upper


class CustomReportsService:

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------
    def get_size_by_brand_season(
        self,
        seasons: Optional[List[str]] = None,
        brands: Optional[List[str]] = None,
        size: Optional[str] = None,
        include_never_received: bool = False,
    ) -> Dict[str, Any]:
        """
        Brand × Size × Season report.

        Args:
            seasons: list of season codes (e.g. ['SS25', 'SS26']). Each
                must match ^[A-Za-z0-9]+$. Defaults to ('SS25', 'SS26').
            brands: list of vendor_name values to keep. Case-insensitive
                exact match. When None (default), no brand filter is
                applied — the report covers every vendor. Pass `['*']`
                for the same behaviour explicitly, or pass `list(FOCUS_BRANDS)`
                to restrict to the 13 curated brands.
            size: optional item_size filter (case-insensitive exact).
            include_never_received: when False (default), SKUs whose
                `first_rcvd_date` is NULL — i.e. catalog ghosts that have
                never been physically received — are dropped. Setting True
                keeps them in the report with all-zero qty metrics.

        Returns:
            {
                'success': True,
                'seasons': [...],
                'rows': [
                    {
                        'brand', 'size', 'season',
                        'sku_count',
                        'imported_qty', 'sold_qty', 'on_hand_qty',
                        'sell_through_pct',           # sold/imported × 100
                        'avg_days_to_sell',           # FIFO unit-weighted
                        'median_days_to_sell',
                        'units_matched',              # sanity vs sold_qty
                    }, ...
                ],
                'count': int,
            }
        """
        season_list = list(seasons) if seasons else list(DEFAULT_SEASONS)
        for s in season_list:
            if not SEASON_RE.match(s):
                return {'success': False, 'error': f'invalid season code: {s!r}'}

        seasons_sql = ', '.join(f"'{s.upper()}'" for s in season_list)

        # Resolve brand filter: None or ['*'] → no filter (all vendors).
        # Pass a list of vendor names to restrict (FOCUS_BRANDS remains
        # available as a curated preset callers can opt into).
        if brands is None or (len(brands) == 1 and brands[0] == '*'):
            brand_filter = None
        else:
            brand_filter = list(brands)

        # 1. Pull all three datasets from Oracle in one connection
        items_df, receipts_df, sales_df = self._fetch_data(seasons_sql)
        if items_df is None:
            return {'success': False, 'error': 'Failed to fetch data from Oracle'}

        # 1a. Drop catalog ghosts (never physically received) unless caller opts in.
        # These inflate sku_count without contributing to any qty metric.
        if (not include_never_received
                and len(items_df) > 0
                and 'first_rcvd_date' in items_df.columns):
            items_df = items_df[items_df['first_rcvd_date'].notna()].copy()

        # 1b. Drop items with no meaningful size (NULL, empty, one-size labels
        # like 'OS', 'U', 'TU'). This naturally removes jewelry, bags,
        # accessories — categories that don't have a useful size dimension.
        if len(items_df) > 0:
            items_df = items_df[items_df['item_size'].apply(is_meaningful_size)].copy()

        # 1c. Drop items in departments outside SIZED_DEPARTMENTS (anything
        # other than WRTW/MRTW/WSHO/MSHO — accessories etc. shouldn't appear
        # in a size report even if they happen to carry a number label).
        if len(items_df) > 0:
            items_df = items_df[
                items_df['department'].fillna('').str.upper().isin(SIZED_DEPARTMENTS)
            ].copy()

        if len(items_df) == 0:
            return {
                'success': True,
                'seasons': season_list,
                'brands': brand_filter,
                'rows': [],
                'count': 0,
            }

        # 2. FIFO-match sold units to receipt batches per item
        matched_df = self._fifo_match(receipts_df, sales_df)

        # 3. Aggregate to (brand, size, season)
        rows = self._aggregate(items_df, receipts_df, sales_df, matched_df,
                               brands=brand_filter, size=size)

        return {
            'success': True,
            'seasons': season_list,
            'brands': brand_filter,
            'rows': rows,
            'count': len(rows),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _fetch_data(self, seasons_sql: str):
        """Returns (items_df, receipts_df, sales_df) or (None, None, None) on error."""
        conn = get_oracle_connection()
        if conn is None:
            return None, None, None

        try:
            cur = conn.cursor()
            try:
                cur.execute(CustomReportsQueries.SIZE_REPORT_ITEMS.format(seasons=seasons_sql))
                items_cols = [d[0].lower() for d in cur.description]
                items_rows = cur.fetchall()

                cur.execute(CustomReportsQueries.SIZE_REPORT_RECEIPTS.format(seasons=seasons_sql))
                rec_cols = [d[0].lower() for d in cur.description]
                rec_rows = cur.fetchall()

                cur.execute(CustomReportsQueries.SIZE_REPORT_SALES.format(seasons=seasons_sql))
                sales_cols = [d[0].lower() for d in cur.description]
                sales_rows = cur.fetchall()
            finally:
                cur.close()
        except Exception as e:
            return None, None, None
        finally:
            conn.close()

        items_df = pd.DataFrame(items_rows, columns=items_cols)
        receipts_df = pd.DataFrame(rec_rows, columns=rec_cols)
        sales_df = pd.DataFrame(sales_rows, columns=sales_cols)

        # Coerce types
        for df, qty_col in ((items_df, 'on_hand_qty'),
                            (receipts_df, 'qty'),
                            (sales_df, 'qty')):
            if qty_col in df.columns:
                df[qty_col] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0).astype(int)
        if 'item_sid' in items_df.columns:
            items_df['item_sid'] = items_df['item_sid'].astype('int64')
        if 'item_sid' in receipts_df.columns:
            receipts_df['item_sid'] = receipts_df['item_sid'].astype('int64')
        if 'item_sid' in sales_df.columns:
            sales_df['item_sid'] = sales_df['item_sid'].astype('int64')

        # Drop sales with no date or qty<=0
        sales_df = sales_df[(sales_df['qty'] > 0) & sales_df['sale_date'].notna()].copy()
        receipts_df = receipts_df[(receipts_df['qty'] > 0) & receipts_df['post_date'].notna()].copy()

        return items_df, receipts_df, sales_df

    @staticmethod
    def _fifo_match(receipts_df: pd.DataFrame, sales_df: pd.DataFrame) -> pd.DataFrame:
        """
        For each item, pair sold units with the oldest available receipt
        units. Returns a DataFrame with one row per matched unit:
            [item_sid, days_to_sell, qty]
        Sold units exceeding total receipts are dropped (data anomaly).
        """
        if len(sales_df) == 0:
            return pd.DataFrame(columns=['item_sid', 'days_to_sell', 'qty'])

        # Pre-sort by item then date
        receipts_sorted = receipts_df.sort_values(['item_sid', 'post_date'])
        sales_sorted = sales_df.sort_values(['item_sid', 'sale_date'])

        receipts_by_item: Dict[int, List[List]] = {}
        for item_sid, group in receipts_sorted.groupby('item_sid', sort=False):
            # mutable [date, remaining_qty] per receipt batch
            receipts_by_item[int(item_sid)] = [
                [r['post_date'], int(r['qty'])] for _, r in group.iterrows()
            ]

        matches: List[Dict[str, Any]] = []
        for item_sid, group in sales_sorted.groupby('item_sid', sort=False):
            queue = receipts_by_item.get(int(item_sid))
            if not queue:
                continue
            queue_dq = deque(queue)
            for _, s in group.iterrows():
                remaining = int(s['qty'])
                sale_date = s['sale_date']
                while remaining > 0 and queue_dq:
                    r_date, r_qty = queue_dq[0]
                    take = remaining if remaining <= r_qty else r_qty
                    days = (sale_date.date() - r_date.date()).days if hasattr(sale_date, 'date') else (sale_date - r_date).days
                    matches.append({
                        'item_sid': int(item_sid),
                        'days_to_sell': days,
                        'qty': take,
                    })
                    remaining -= take
                    r_qty -= take
                    if r_qty == 0:
                        queue_dq.popleft()
                    else:
                        queue_dq[0] = [r_date, r_qty]

        return pd.DataFrame(matches, columns=['item_sid', 'days_to_sell', 'qty'])

    @staticmethod
    def _aggregate(
        items_df: pd.DataFrame,
        receipts_df: pd.DataFrame,
        sales_df: pd.DataFrame,
        matched_df: pd.DataFrame,
        brands: Optional[List[str]] = None,
        size: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Roll matched + raw counts up to (brand, department, normalized_size)."""
        # Per-item totals
        rec_per_item = receipts_df.groupby('item_sid')['qty'].sum() if len(receipts_df) else pd.Series(dtype='int64')
        sales_per_item = sales_df.groupby('item_sid')['qty'].sum() if len(sales_df) else pd.Series(dtype='int64')

        if len(matched_df):
            matched_per_item_qty = matched_df.groupby('item_sid')['qty'].sum()
            matched_df = matched_df.assign(qty_days=matched_df['qty'] * matched_df['days_to_sell'])
            matched_per_item_qty_days = matched_df.groupby('item_sid')['qty_days'].sum()
        else:
            matched_per_item_qty = pd.Series(dtype='int64')
            matched_per_item_qty_days = pd.Series(dtype='int64')

        item_stats = items_df.copy()
        item_stats['imported_qty'] = item_stats['item_sid'].map(rec_per_item).fillna(0).astype(int)
        item_stats['sold_qty'] = item_stats['item_sid'].map(sales_per_item).fillna(0).astype(int)
        item_stats['matched_qty'] = item_stats['item_sid'].map(matched_per_item_qty).fillna(0).astype(int)
        item_stats['matched_qty_days'] = item_stats['item_sid'].map(matched_per_item_qty_days).fillna(0).astype('int64')

        # Normalize each item's size based on its brand + department
        item_stats['normalized_size'] = item_stats.apply(
            lambda r: normalize_size(r['item_size'], r['brand'], r['department']),
            axis=1,
        )

        # Apply filters
        if brands:
            wanted = {b.upper() for b in brands}
            item_stats = item_stats[item_stats['brand'].fillna('').str.upper().isin(wanted)]
        if size is not None:
            item_stats = item_stats[item_stats['normalized_size'].fillna('').str.upper() == size.upper()]

        # Group by (brand, department, normalized_size)
        grouped = item_stats.groupby(
            ['brand', 'department', 'normalized_size'], dropna=False
        ).agg(
            sku_count=('item_sid', 'count'),
            imported_qty=('imported_qty', 'sum'),
            sold_qty=('sold_qty', 'sum'),
            on_hand_qty=('on_hand_qty', 'sum'),
            matched_qty=('matched_qty', 'sum'),
            matched_qty_days=('matched_qty_days', 'sum'),
            raw_sizes=('item_size', lambda s: ', '.join(
                sorted({str(x) for x in s if pd.notna(x)})
            )),
        ).reset_index()

        # Compute derived fields
        rows: List[Dict[str, Any]] = []
        for _, r in grouped.iterrows():
            imported = int(r['imported_qty'])
            sold = int(r['sold_qty'])
            matched = int(r['matched_qty'])
            matched_qty_days = int(r['matched_qty_days'])
            sell_through_pct = round(sold / imported * 100, 2) if imported > 0 else None
            avg_days = round(matched_qty_days / matched, 2) if matched > 0 else None

            rows.append({
                'brand': r['brand'],
                'department': r['department'],
                'normalized_size': r['normalized_size'],
                'sku_count': int(r['sku_count']),
                'imported_qty': imported,
                'sold_qty': sold,
                'on_hand_qty': int(r['on_hand_qty']),
                'sell_through_pct': sell_through_pct,
                'avg_days_to_sell': avg_days,
                'units_matched': matched,
                'raw_sizes': r['raw_sizes'],
            })

        # Sort: brand asc, department asc, then normalized_size in a sensible
        # order (letter sizes XXS→XXL first, then numeric ascending, then other)
        letter_order = {s: i for i, s in enumerate(
            ['XXS', 'XS', 'S', 'M', 'L', 'XL', 'XXL', 'XXXL']
        )}

        def _size_key(ns):
            ns_str = ns if isinstance(ns, str) else ''
            if ns_str in letter_order:
                return (0, letter_order[ns_str], '')
            try:
                return (1, float(ns_str), '')
            except (TypeError, ValueError):
                return (2, 0, ns_str)

        def _str_key(s):
            return (s if isinstance(s, str) else '').upper()

        rows.sort(key=lambda x: (
            _str_key(x['brand']),
            _str_key(x['department']),
            _size_key(x['normalized_size']),
        ))
        return rows
