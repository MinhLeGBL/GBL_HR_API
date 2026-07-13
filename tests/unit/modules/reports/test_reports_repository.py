"""
Unit tests for app.modules.reports.repository.ReportsRepository (CR #78).

Oracle is mocked. Covers the connection-failure guard and that both queries
are executed with the same date binds.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.reports.repository import ReportsRepository

CONN = 'app.modules.reports.repository.get_oracle_connection'


def _cursor(totals_row, split_row):
    cur = MagicMock()
    cur.fetchone.side_effect = [totals_row, split_row]
    return cur


class TestFetchPeriodMetrics:

    @patch(CONN)
    def test_none_connection_raises_cleanly(self, mock_conn):
        """When Oracle is down get_oracle_connection() returns None — the repo
        must raise (not AttributeError from a None.close() in finally)."""
        mock_conn.return_value = None
        with pytest.raises(RuntimeError, match='Oracle connection unavailable'):
            ReportsRepository().fetch_period_metrics(
                date(2026, 6, 1), date(2026, 7, 1))

    @patch(CONN)
    def test_maps_rows_and_binds(self, mock_conn):
        cur = _cursor((1_200_000_000, 3450), (120, 890, 180_000_000))
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        out = ReportsRepository().fetch_period_metrics(
            date(2026, 6, 1), date(2026, 7, 1))

        assert out == {
            'total_revenue':        1_200_000_000,
            'bill_count':           3450,
            'new_customers':        120,
            'returning_customers':  890,
            'new_customer_revenue': 180_000_000,
        }
        # Both queries run with identical date binds; conn is closed.
        assert cur.execute.call_count == 2
        for call in cur.execute.call_args_list:
            assert call.args[1] == {
                'from_date': date(2026, 6, 1),
                'to_exclusive': date(2026, 7, 1),
            }
        conn.close.assert_called_once()

    @patch(CONN)
    def test_null_aggregates_coalesce_to_zero(self, mock_conn):
        """Empty period → Oracle returns NULLs → repo coalesces to 0."""
        cur = _cursor((None, 0), (0, 0, None))
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        out = ReportsRepository().fetch_period_metrics(
            date(2026, 6, 1), date(2026, 7, 1))
        assert out['total_revenue'] == 0
        assert out['new_customer_revenue'] == 0
