"""Unit tests for SaleThroughService — Oracle is mocked at the connection layer."""
from unittest.mock import MagicMock, patch

from app.modules.sale_through.service import SaleThroughService


def _mock_oracle(rows):
    """Build a mock that mimics get_oracle_connection() returning rows from the report query."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


# Column order returned by BRAND_SEASON_CATEGORY:
# (vendor_name, season, category, sku_count, imported_qty, sold_qty,
#  on_hand_qty, transferred_out_qty, in_transit_qty, adjustment_qty)


class TestBrandSeasonCategoryReport:

    @patch('app.modules.sale_through.service.get_oracle_connection')
    def test_basic_aggregation_and_sell_through(self, mock_conn):
        mock_conn.return_value = _mock_oracle([
            ('PROENZA SCHOULER', 'SS25', 'DRESS', 12, 100, 75, 25, 0, 0, 0),
            ('TRAVELITE',        'SS25', 'BAG',    3, 3149, 3054, 95, 0, 0, 0),
        ])

        result = SaleThroughService().get_brand_season_category_report()

        assert result['success'] is True
        assert result['count'] == 2
        first = result['rows'][0]
        assert first['brand'] == 'PROENZA SCHOULER'
        assert first['imported_qty'] == 100
        assert first['sold_qty'] == 75
        assert first['on_hand_qty'] == 25
        assert first['in_transit_qty'] == 0
        assert first['actual_on_hand_qty'] == 25
        assert first['sell_through_pct'] == 75.0
        # Identity check on the second row (real-data sanity case from earlier verification)
        second = result['rows'][1]
        assert second['imported_qty'] - second['sold_qty'] == second['actual_on_hand_qty']

    @patch('app.modules.sale_through.service.get_oracle_connection')
    def test_actual_on_hand_includes_in_transit(self, mock_conn):
        # Items in transit have left the source store's iq snapshot but
        # not yet been added to the destination's. Actual on-hand must add them back.
        mock_conn.return_value = _mock_oracle([
            ('BRAND A', 'SS25', 'DRESS', 1, 100, 60, 30, 0, 10, 0),
        ])

        row = SaleThroughService().get_brand_season_category_report()['rows'][0]

        assert row['on_hand_qty'] == 30           # raw iq snapshot
        assert row['in_transit_qty'] == 10        # transfers in flight
        assert row['actual_on_hand_qty'] == 40    # 30 + 10

    @patch('app.modules.sale_through.service.get_oracle_connection')
    def test_zero_imported_returns_null_sell_through(self, mock_conn):
        mock_conn.return_value = _mock_oracle([
            ('VENDOR X', 'FW24', 'TOP', 1, 0, 0, 0, 0, 0, 0),
        ])

        result = SaleThroughService().get_brand_season_category_report()

        assert result['rows'][0]['sell_through_pct'] is None

    @patch('app.modules.sale_through.service.get_oracle_connection')
    def test_filter_by_brand(self, mock_conn):
        mock_conn.return_value = _mock_oracle([
            ('PROENZA SCHOULER', 'SS25', 'DRESS', 1, 10, 5, 5, 0, 0, 0),
            ('TRAVELITE',        'SS25', 'BAG',   1, 10, 5, 5, 0, 0, 0),
        ])

        result = SaleThroughService().get_brand_season_category_report(brand='travelite')

        assert result['count'] == 1
        assert result['rows'][0]['brand'] == 'TRAVELITE'

    @patch('app.modules.sale_through.service.get_oracle_connection')
    def test_filter_by_season_and_category(self, mock_conn):
        mock_conn.return_value = _mock_oracle([
            ('A', 'SS25', 'DRESS', 1, 10, 5, 5, 0, 0, 0),
            ('A', 'FW24', 'DRESS', 1, 10, 5, 5, 0, 0, 0),
            ('A', 'SS25', 'BAG',   1, 10, 5, 5, 0, 0, 0),
        ])

        result = SaleThroughService().get_brand_season_category_report(
            season='SS25', category='DRESS',
        )

        assert result['count'] == 1
        assert result['rows'][0]['season'] == 'SS25'
        assert result['rows'][0]['category'] == 'DRESS'

    @patch('app.modules.sale_through.service.get_oracle_connection')
    def test_oracle_connection_failure(self, mock_conn):
        mock_conn.return_value = None

        result = SaleThroughService().get_brand_season_category_report()

        assert result['success'] is False
        assert 'Failed to connect' in result['error']

    @patch('app.modules.sale_through.service.get_oracle_connection')
    def test_null_groupings_passed_through(self, mock_conn):
        mock_conn.return_value = _mock_oracle([
            (None, None, None, 5, 50, 20, 30, 0, 0, 0),
        ])

        result = SaleThroughService().get_brand_season_category_report()

        row = result['rows'][0]
        assert row['brand'] is None
        assert row['season'] is None
        assert row['category'] is None
        assert row['sell_through_pct'] == 40.0
