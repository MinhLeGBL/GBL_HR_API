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


class CustomReportsService:

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------
    def get_size_by_brand_season(
        self,
        seasons: Optional[List[str]] = None,
        brand: Optional[str] = None,
        size: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Brand × Size × Season report.

        Args:
            seasons: list of season codes (e.g. ['SS25', 'SS26']). Each
                must match ^[A-Za-z0-9]+$. Defaults to ('SS25', 'SS26').
            brand: optional vendor_name filter (case-insensitive exact).
            size: optional item_size filter (case-insensitive exact).

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

        # 1. Pull all three datasets from Oracle in one connection
        items_df, receipts_df, sales_df = self._fetch_data(seasons_sql)
        if items_df is None:
            return {'success': False, 'error': 'Failed to fetch data from Oracle'}

        if len(items_df) == 0:
            return {'success': True, 'seasons': season_list, 'rows': [], 'count': 0}

        # 2. FIFO-match sold units to receipt batches per item
        matched_df = self._fifo_match(receipts_df, sales_df)

        # 3. Aggregate to (brand, size, season)
        rows = self._aggregate(items_df, receipts_df, sales_df, matched_df,
                               brand=brand, size=size)

        return {
            'success': True,
            'seasons': season_list,
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
        brand: Optional[str] = None,
        size: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Roll matched + raw counts up to (brand, size, season)."""
        # Per-item totals
        rec_per_item = receipts_df.groupby('item_sid')['qty'].sum() if len(receipts_df) else pd.Series(dtype='int64')
        sales_per_item = sales_df.groupby('item_sid')['qty'].sum() if len(sales_df) else pd.Series(dtype='int64')

        if len(matched_df):
            matched_per_item_qty = matched_df.groupby('item_sid')['qty'].sum()
            # Sum of (qty × days) for weighted-mean computation
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

        # Apply filters
        if brand is not None:
            item_stats = item_stats[item_stats['brand'].fillna('').str.upper() == brand.upper()]
        if size is not None:
            item_stats = item_stats[item_stats['item_size'].fillna('').str.upper() == size.upper()]

        # Group by (brand, size, season)
        grouped = item_stats.groupby(
            ['brand', 'item_size', 'season'], dropna=False
        ).agg(
            sku_count=('item_sid', 'count'),
            imported_qty=('imported_qty', 'sum'),
            sold_qty=('sold_qty', 'sum'),
            on_hand_qty=('on_hand_qty', 'sum'),
            matched_qty=('matched_qty', 'sum'),
            matched_qty_days=('matched_qty_days', 'sum'),
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

            # Per-row median is harder to do post-aggregate without raw events;
            # compute it from matched_df scoped to this (brand, size, season)
            # if needed. For v0.1, only avg + count; median can be added later.
            rows.append({
                'brand': r['brand'],
                'size': r['item_size'],
                'season': r['season'],
                'sku_count': int(r['sku_count']),
                'imported_qty': imported,
                'sold_qty': sold,
                'on_hand_qty': int(r['on_hand_qty']),
                'sell_through_pct': sell_through_pct,
                'avg_days_to_sell': avg_days,
                'units_matched': matched,
            })

        # Sort: brand asc, season asc, size asc. Coerce non-strings (NaN
        # from missing brand/size in source data) to '' before uppercasing.
        def _key(s):
            return (s if isinstance(s, str) else '').upper()
        rows.sort(key=lambda x: (_key(x['brand']), _key(x['season']), _key(x['size'])))
        return rows
