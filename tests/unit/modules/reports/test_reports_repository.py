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
        # PERIOD_TOTALS row: (total, bills, tourist_customers, tourist_revenue)
        cur = _cursor((1_200_000_000, 3450, 45, 100_000_000),
                      (120, 890, 180_000_000))
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        out = ReportsRepository().fetch_period_metrics(
            date(2026, 6, 1), date(2026, 7, 1))

        assert out == {
            'total_revenue':            1_200_000_000,
            'bill_count':               3450,
            'tourist_customers':        45,
            'tourist_customer_revenue': 100_000_000,
            'new_customers':            120,
            'returning_customers':      890,
            'new_customer_revenue':     180_000_000,
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
        cur = _cursor((None, 0, None, None), (0, 0, None))
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        out = ReportsRepository().fetch_period_metrics(
            date(2026, 6, 1), date(2026, 7, 1))
        assert out['total_revenue'] == 0
        assert out['new_customer_revenue'] == 0
        assert out['tourist_customers'] == 0
        assert out['tourist_customer_revenue'] == 0


PG = 'app.modules.reports.repository.get_postgres_connection'


def _pg_cursor(fetchone=None):
    """Postgres cursor used as a context manager (`with conn.cursor() as cur`)."""
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cm = MagicMock()
    cm.__enter__.return_value = cur
    cm.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cm
    return conn, cur


class TestPins:

    @patch(PG)
    def test_get_pins_none_connection_raises(self, mock_pg):
        mock_pg.return_value = None
        with pytest.raises(RuntimeError, match='Postgres connection unavailable'):
            ReportsRepository().get_pins(1)

    @patch(PG)
    def test_get_pins_no_row_returns_none(self, mock_pg):
        conn, _ = _pg_cursor(fetchone=None)
        mock_pg.return_value = conn
        assert ReportsRepository().get_pins(1) is None
        conn.close.assert_called_once()

    @patch(PG)
    def test_get_pins_maps_row(self, mock_pg):
        conn, _ = _pg_cursor(
            fetchone=(date(2026, 6, 1), date(2026, 6, 30), None, None))
        mock_pg.return_value = conn
        out = ReportsRepository().get_pins(1)
        assert out == {
            'period_a_from': date(2026, 6, 1), 'period_a_to': date(2026, 6, 30),
            'period_b_from': None, 'period_b_to': None,
        }

    @patch(PG)
    def test_upsert_pins_binds_and_commits(self, mock_pg):
        conn, cur = _pg_cursor()
        mock_pg.return_value = conn
        ReportsRepository().upsert_pins(
            7, date(2026, 6, 1), date(2026, 6, 30), None, None)
        sql, params = cur.execute.call_args.args
        assert 'ON CONFLICT (user_id) DO UPDATE' in sql
        assert params == (7, date(2026, 6, 1), date(2026, 6, 30), None, None)
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    @patch(PG)
    def test_upsert_pins_none_connection_raises(self, mock_pg):
        mock_pg.return_value = None
        with pytest.raises(RuntimeError, match='Postgres connection unavailable'):
            ReportsRepository().upsert_pins(7, None, None, None, None)
