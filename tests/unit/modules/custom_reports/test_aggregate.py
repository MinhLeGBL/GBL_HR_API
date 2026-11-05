"""Tests for the brand × size × season aggregation step."""
import pandas as pd

from app.modules.custom_reports.service import CustomReportsService


def _items(*rows):
    """rows: (item_sid, brand, raw_size, _season_ignored, on_hand_qty, [department])

    Tuple layout is kept compatible with the legacy 5-element form that older
    tests used. `season` is ignored (no longer a grouping dim). Pass a 6th
    element to set department; defaults to 'WRTW' so items survive the
    sized-department filter.
    """
    normalized = []
    for row in rows:
        if len(row) == 6:
            sid, brand, item_size, _season, on_hand, dept = row
        elif len(row) == 5:
            sid, brand, item_size, _season, on_hand = row
            dept = 'WRTW'
        else:
            raise ValueError(f'unexpected row arity: {row}')
        normalized.append((sid, brand, item_size, on_hand, dept))
    df = pd.DataFrame(normalized, columns=['item_sid', 'brand', 'item_size', 'on_hand_qty', 'department'])
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
        # Two SKUs, same brand/department/size → roll up into one row
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
        assert r['brand'] == 'ACME'
        assert r['department'] == 'WRTW'
        assert r['normalized_size'] == 'M'
        assert r['sku_count'] == 2
        assert r['imported_qty'] == 10
        assert r['sold_qty'] == 8
        assert r['on_hand_qty'] == 2
        assert r['sell_through_pct'] == 80.0
        # avg days: SKU1 3 units × 10d, SKU2 5 units × 20d → (30+100)/8 = 16.25
        assert r['avg_days_to_sell'] == 16.25
        assert r['units_matched'] == 8
        assert r['raw_sizes'] == 'M'

    def test_sell_through_pct_handles_zero_imported(self):
        items = _items((1, 'ACME', 'M', 'SS25', 0))
        rows = CustomReportsService._aggregate(
            items, _receipts(), _sales(), _receipts().rename(columns={'post_date': 'days_to_sell'})
        )
        # No receipts → imported=0 → sell_through_pct must be None
        assert rows[0]['sell_through_pct'] is None
        assert rows[0]['avg_days_to_sell'] is None

    def test_filter_by_single_brand(self):
        items = _items(
            (1, 'ACME', 'M', 'SS25', 1),
            (2, 'OTHER', 'M', 'SS25', 1),
        )
        rec = _receipts((1, '2025-01-01', 5), (2, '2025-01-01', 5))
        sal = _sales((1, '2025-01-06', 1), (2, '2025-01-06', 1))
        matched = CustomReportsService._fifo_match(rec, sal)

        rows = CustomReportsService._aggregate(items, rec, sal, matched, brands=['acme'])
        assert len(rows) == 1
        assert rows[0]['brand'] == 'ACME'

    def test_filter_by_multiple_brands(self):
        items = _items(
            (1, 'ACME', 'M', 'SS25', 1),
            (2, 'BETA', 'M', 'SS25', 1),
            (3, 'GAMMA', 'M', 'SS25', 1),
        )
        rec = _receipts(
            (1, '2025-01-01', 5),
            (2, '2025-01-01', 5),
            (3, '2025-01-01', 5),
        )
        sal = _sales(
            (1, '2025-01-06', 1),
            (2, '2025-01-06', 1),
            (3, '2025-01-06', 1),
        )
        matched = CustomReportsService._fifo_match(rec, sal)

        rows = CustomReportsService._aggregate(
            items, rec, sal, matched, brands=['ACME', 'GAMMA']
        )
        assert len(rows) == 2
        kept = {r['brand'] for r in rows}
        assert kept == {'ACME', 'GAMMA'}

    def test_no_brand_filter_when_none(self):
        items = _items(
            (1, 'ACME', 'M', 'SS25', 1),
            (2, 'BETA', 'M', 'SS25', 1),
        )
        rec = _receipts()
        sal = _sales()
        matched = CustomReportsService._fifo_match(rec, sal)

        rows = CustomReportsService._aggregate(items, rec, sal, matched, brands=None)
        assert len(rows) == 2

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
        assert rows[0]['normalized_size'] == 'L'

    def test_rows_sorted_by_brand_department_size(self):
        # Note: season is no longer a grouping dimension, so two items in
        # different seasons (sids 2, 3) merge into one (ALPHA, WRTW, L) row.
        items = _items(
            (1, 'BETA', 'M', '_', 1),
            (2, 'ALPHA', 'L', '_', 1),
            (3, 'ALPHA', 'L', '_', 1),
            (4, 'ALPHA', 'M', '_', 1),
        )
        rec = _receipts()
        sal = _sales()
        matched = CustomReportsService._fifo_match(rec, sal)

        rows = CustomReportsService._aggregate(items, rec, sal, matched)
        # Letter sort order: L → M means M before L? No: XXS, XS, S, M, L, XL.
        # So M < L in the canonical order.
        order = [(r['brand'], r['department'], r['normalized_size']) for r in rows]
        assert order == [
            ('ALPHA', 'WRTW', 'M'),
            ('ALPHA', 'WRTW', 'L'),
            ('BETA',  'WRTW', 'M'),
        ]
        # The two ALPHA-L items should have merged into one row with sku_count=2
        alpha_l = next(r for r in rows if r['brand'] == 'ALPHA' and r['normalized_size'] == 'L')
        assert alpha_l['sku_count'] == 2
