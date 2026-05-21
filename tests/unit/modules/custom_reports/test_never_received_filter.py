"""Tests for the include_never_received filter in get_size_by_brand_season.

The filter happens between fetching from Oracle and the aggregation —
it drops items whose `first_rcvd_date` is NaT/None unless the caller
opts in via include_never_received=True.
"""
from datetime import datetime
from unittest.mock import patch

import pandas as pd

from app.modules.custom_reports.service import CustomReportsService


def _items_with_dates(*rows):
    """rows: (item_sid, brand, size, season, on_hand_qty, first_rcvd_date)
    first_rcvd_date can be a date string or None (catalog ghost)."""
    df = pd.DataFrame(rows, columns=[
        'item_sid', 'brand', 'item_size', 'season', 'on_hand_qty', 'first_rcvd_date',
    ])
    df['item_sid'] = df['item_sid'].astype('int64')
    df['on_hand_qty'] = df['on_hand_qty'].astype(int)
    df['first_rcvd_date'] = pd.to_datetime(df['first_rcvd_date'])
    return df


def _make_fetch_return(items_df):
    """Pretend the Oracle fetch returned these items and no receipts/sales."""
    empty_receipts = pd.DataFrame(columns=['item_sid', 'post_date', 'qty'])
    empty_sales = pd.DataFrame(columns=['item_sid', 'sale_date', 'qty'])
    return items_df, empty_receipts, empty_sales


class TestNeverReceivedFilter:

    @patch.object(CustomReportsService, '_fetch_data')
    def test_default_drops_ghosts(self, mock_fetch):
        items = _items_with_dates(
            (1, 'AKRIS', 'M', 'SS25', 0, '2024-12-03'),    # ghost — never received
            (2, 'AKRIS', 'L', 'SS25', 0, None),            # ghost (NaT)
            (3, 'AKRIS', 'XL', 'SS25', 0, '2025-02-01'),   # real
        )
        mock_fetch.return_value = _make_fetch_return(items)

        result = CustomReportsService().get_size_by_brand_season(
            brands=['AKRIS'], seasons=['SS25'],
        )

        assert result['success']
        rows_by_size = {(r['brand'], r['size']): r for r in result['rows']}
        # Items 1 (M) and 3 (XL) have real first_rcvd_date — preserved.
        # Item 2 (L) has NaT — filtered out by default.
        assert ('AKRIS', 'M') in rows_by_size
        assert ('AKRIS', 'XL') in rows_by_size
        assert ('AKRIS', 'L') not in rows_by_size

    @patch.object(CustomReportsService, '_fetch_data')
    def test_include_keeps_ghosts(self, mock_fetch):
        items = _items_with_dates(
            (1, 'AKRIS', 'M', 'SS25', 0, '2024-12-03'),
            (2, 'AKRIS', 'L', 'SS25', 0, None),            # ghost
            (3, 'AKRIS', 'XL', 'SS25', 0, '2025-02-01'),
        )
        mock_fetch.return_value = _make_fetch_return(items)

        result = CustomReportsService().get_size_by_brand_season(
            brands=['AKRIS'], seasons=['SS25'],
            include_never_received=True,
        )

        assert result['success']
        rows_by_size = {(r['brand'], r['size']): r for r in result['rows']}
        # All three rows preserved
        assert ('AKRIS', 'M') in rows_by_size
        assert ('AKRIS', 'L') in rows_by_size
        assert ('AKRIS', 'XL') in rows_by_size
        # The ghost row's sku_count is 1 and all qty fields are zero
        ghost_row = rows_by_size[('AKRIS', 'L')]
        assert ghost_row['sku_count'] == 1
        assert ghost_row['imported_qty'] == 0
        assert ghost_row['sold_qty'] == 0
        assert ghost_row['on_hand_qty'] == 0

    @patch.object(CustomReportsService, '_fetch_data')
    def test_all_ghosts_returns_empty(self, mock_fetch):
        items = _items_with_dates(
            (1, 'AKRIS', 'M', 'SS25', 0, None),
            (2, 'AKRIS', 'L', 'SS25', 0, None),
        )
        mock_fetch.return_value = _make_fetch_return(items)

        result = CustomReportsService().get_size_by_brand_season(
            brands=['AKRIS'], seasons=['SS25'],
        )

        assert result['success']
        assert result['count'] == 0
        assert result['rows'] == []
