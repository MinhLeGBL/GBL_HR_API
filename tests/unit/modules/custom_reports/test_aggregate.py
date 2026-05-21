"""Tests for the brand × size × season aggregation step."""
import pandas as pd

from app.modules.custom_reports.service import CustomReportsService


def _items(*rows):
    """rows: (item_sid, brand, size, season, on_hand_qty)"""
    df = pd.DataFrame(rows, columns=['item_sid', 'brand', 'item_size', 'season', 'on_hand_qty'])
    df['item_sid'] = df['item_sid'].astype('int64')
    df['on_hand_qty'] = df['on_hand_qty'].astype(int)
    return df


def _receipts(*rows):
    df = pd.DataFrame(rows, columns=['item_sid', 'post_date', 'qty'])
    df['post_date'] = pd.to_datetime(df['post_date'])
    df['qty'] = df['qty'].astype(int)
    df['item_sid'] = df['item_sid'].astype('int64')
    return df


def _sales(*rows):
    df = pd.DataFrame(rows, columns=['item_sid', 'sale_date', 'qty'])
    df['sale_date'] = pd.to_datetime(df['sale_date'])
    df['qty'] = df['qty'].astype(int)
    df['item_sid'] = df['item_sid'].astype('int64')
    return df


class TestAggregate:

    def test_basic_two_skus_one_group(self):
        # Two SKUs, same brand/size/season → roll up into one row
        items = _items(
            (1, 'ACME', 'M', 'SS25', 2),
            (2, 'ACME', 'M', 'SS25', 0),
        )
        rec = _receipts((1, '2025-01-01', 5),
                        (2, '2025-01-01', 5))
        sal = _sales((1, '2025-01-11', 3),
                     (2, '2025-01-21', 5))
        matched = CustomReportsService._fifo_match(rec, sal)

        rows = CustomReportsService._aggregate(items, rec, sal, matched)

        assert len(rows) == 1
        r = rows[0]
        assert r['brand'] == 'ACME' and r['size'] == 'M' and r['season'] == 'SS25'
        assert r['sku_count'] == 2
        assert r['imported_qty'] == 10
        assert r['sold_qty'] == 8
        assert r['on_hand_qty'] == 2
        assert r['sell_through_pct'] == 80.0
        # avg days: SKU1 3 units × 10d, SKU2 5 units × 20d → (30+100)/8 = 16.25
        assert r['avg_days_to_sell'] == 16.25
        assert r['units_matched'] == 8

    def test_sell_through_pct_handles_zero_imported(self):
        items = _items((1, 'ACME', 'M', 'SS25', 0))
        rows = CustomReportsService._aggregate(
            items, _receipts(), _sales(), _receipts().rename(columns={'post_date': 'days_to_sell'})
        )
        # No receipts → imported=0 → sell_through_pct must be None
        assert rows[0]['sell_through_pct'] is None
        assert rows[0]['avg_days_to_sell'] is None

    def test_filter_by_brand(self):
        items = _items(
            (1, 'ACME', 'M', 'SS25', 1),
            (2, 'OTHER', 'M', 'SS25', 1),
        )
        rec = _receipts((1, '2025-01-01', 5), (2, '2025-01-01', 5))
        sal = _sales((1, '2025-01-06', 1), (2, '2025-01-06', 1))
        matched = CustomReportsService._fifo_match(rec, sal)

        rows = CustomReportsService._aggregate(items, rec, sal, matched, brand='acme')
        assert len(rows) == 1
        assert rows[0]['brand'] == 'ACME'

    def test_filter_by_size(self):
        items = _items(
            (1, 'ACME', 'M', 'SS25', 1),
            (2, 'ACME', 'L', 'SS25', 1),
        )
        rec = _receipts((1, '2025-01-01', 5), (2, '2025-01-01', 5))
        sal = _sales((1, '2025-01-06', 1), (2, '2025-01-06', 1))
        matched = CustomReportsService._fifo_match(rec, sal)

        rows = CustomReportsService._aggregate(items, rec, sal, matched, size='L')
        assert len(rows) == 1
        assert rows[0]['size'] == 'L'

    def test_rows_sorted_by_brand_season_size(self):
        items = _items(
            (1, 'BETA', 'M', 'SS25', 1),
            (2, 'ALPHA', 'L', 'SS26', 1),
            (3, 'ALPHA', 'L', 'SS25', 1),
            (4, 'ALPHA', 'M', 'SS25', 1),
        )
        rec = _receipts()
        sal = _sales()
        matched = CustomReportsService._fifo_match(rec, sal)

        rows = CustomReportsService._aggregate(items, rec, sal, matched)
        order = [(r['brand'], r['season'], r['size']) for r in rows]
        assert order == [
            ('ALPHA', 'SS25', 'L'),
            ('ALPHA', 'SS25', 'M'),
            ('ALPHA', 'SS26', 'L'),
            ('BETA',  'SS25', 'M'),
        ]
