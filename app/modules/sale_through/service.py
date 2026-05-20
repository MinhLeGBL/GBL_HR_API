"""
Sale-Through Report Service.

Reads from RPS (Oracle, read-only). No PostgreSQL writes — this report is
computed on demand from live transactional data.
"""
from typing import Any, Dict, List, Optional

from app.core.database import get_oracle_connection
from .queries import SaleThroughQueries


class SaleThroughService:

    def get_brand_season_category_report(
        self,
        brand: Optional[str] = None,
        season: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Aggregate imported / sold / on-hand quantities by brand × season × category.

        Filters are applied in Python after the heavy aggregation runs in Oracle,
        because the inner item-level scan dominates query time and adding WHERE
        clauses to the outer SELECT does not push down past the GROUP BY.

        Returns:
            {
                'success': True,
                'rows': [
                    {
                        'brand': str|None,
                        'season': str|None,
                        'category': str|None,
                        'sku_count': int,
                        'imported_qty': int,
                        'sold_qty': int,
                        'on_hand_qty': int,           # raw iq.qty snapshot
                        'in_transit_qty': int,        # in-transit transfers (excluded from iq)
                        'actual_on_hand_qty': int,    # on_hand_qty + in_transit_qty
                        'sell_through_pct': float|None,   # sold / imported
                        ...
                    },
                    ...
                ],
                'count': int,
            }
        """
        conn = get_oracle_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to Oracle'}

        try:
            cur = conn.cursor()
            try:
                cur.execute(SaleThroughQueries.BRAND_SEASON_CATEGORY)
                raw = cur.fetchall()
            finally:
                cur.close()
        except Exception as e:
            return {'success': False, 'error': f'Query failed: {e}'}
        finally:
            conn.close()

        rows: List[Dict[str, Any]] = []
        for r in raw:
            (vendor_name, season_v, category_v, sku_count,
             imported_qty, sold_qty, on_hand_qty,
             transferred_out_qty, in_transit_qty, adjustment_qty) = r

            if brand is not None and (vendor_name or '').upper() != brand.upper():
                continue
            if season is not None and (season_v or '').upper() != season.upper():
                continue
            if category is not None and (category_v or '').upper() != category.upper():
                continue

            imported = int(imported_qty or 0)
            sold = int(sold_qty or 0)
            on_hand_snapshot = int(on_hand_qty or 0)
            in_transit = int(in_transit_qty or 0)
            # In-transit stock has left the source store's iq snapshot but has
            # not yet been added to the destination's. Add it back to get the
            # true company-wide on-hand.
            actual_on_hand = on_hand_snapshot + in_transit
            sell_through_pct = round(sold / imported * 100, 2) if imported > 0 else None

            rows.append({
                'brand': vendor_name,
                'season': season_v,
                'category': category_v,
                'sku_count': int(sku_count or 0),
                'imported_qty': imported,
                'sold_qty': sold,
                'on_hand_qty': on_hand_snapshot,
                'in_transit_qty': in_transit,
                'actual_on_hand_qty': actual_on_hand,
                'transferred_out_qty': int(transferred_out_qty or 0),
                'adjustment_qty': int(adjustment_qty or 0),
                'sell_through_pct': sell_through_pct,
            })

        return {'success': True, 'rows': rows, 'count': len(rows)}
