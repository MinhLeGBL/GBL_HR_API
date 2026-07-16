"""
Unit tests for app.modules.reports.repository.ReportsRepository (CR #78).

Oracle is mocked. Covers the connection-failure guard and that both queries
are executed with the same date binds.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.modules.reports.repository import ReportsRepository
from app.modules.reports.queries import ReportsQueries

CONN = 'app.modules.reports.repository.get_oracle_connection'


class TestRevenueFormula:
    """Guards the v1.3.1 fix: di.PRICE is already net of the item discount and
    is a unit price. The net-line expression must NOT re-apply the item discount
    (`di.DISC_PERC`), MUST multiply by `di.QTY`, and MUST keep the document
    discount (`d.DISC_PERC`). Oracle math can't be unit-tested (it's mocked), so
    we pin the SQL shape to prevent a silent regression."""

    def _sql(self):
        return (ReportsQueries.period_totals(ReportsQueries.store_filter(None)[0])
                + ReportsQueries.new_vs_returning(ReportsQueries.store_filter(None)[0]))

    def test_item_discount_not_reapplied(self):
        assert 'di.DISC_PERC' not in self._sql(), (
            'di.PRICE already includes the item discount — applying di.DISC_PERC '
            'double-discounts (the 166M-vs-432M bug).')

    def test_quantity_and_doc_discount_applied(self):
        sql = self._sql()
        assert 'NVL(di.QTY, 0)' in sql, 'PRICE is a unit price — line total needs × QTY'
        assert 'd.DISC_PERC' in sql, 'document-level discount must still be applied'


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

    @patch(CONN)
    def test_no_store_scope_binds_dates_only(self, mock_conn):
        """CR #81: store_sids omitted → only the date binds, no store_* keys,
        and no STORE_SID predicate in the SQL."""
        cur = _cursor((1, 1, 0, 0), (1, 0, 1))
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        ReportsRepository().fetch_period_metrics(date(2026, 6, 1), date(2026, 7, 1))
        for call in cur.execute.call_args_list:
            sql, binds = call.args
            assert binds == {'from_date': date(2026, 6, 1),
                             'to_exclusive': date(2026, 7, 1)}
            assert 'STORE_SID' not in sql

    @patch(CONN)
    def test_store_scope_adds_in_predicate_and_binds(self, mock_conn):
        """CR #81: store_sids → both queries get `STORE_SID IN (...)` and the
        matching store_N binds alongside the dates."""
        cur = _cursor((1, 1, 0, 0), (1, 0, 1))
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        ReportsRepository().fetch_period_metrics(
            date(2026, 6, 1), date(2026, 7, 1), store_sids=[501, 502])
        assert cur.execute.call_count == 2
        for call in cur.execute.call_args_list:
            sql, binds = call.args
            assert 'd.STORE_SID IN (:store_0, :store_1)' in sql
            assert binds == {
                'from_date': date(2026, 6, 1),
                'to_exclusive': date(2026, 7, 1),
                'store_0': 501, 'store_1': 502,
            }


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
        # CR #81: row is now 6 columns — dates + store arrays per side.
        conn, _ = _pg_cursor(
            fetchone=(date(2026, 6, 1), date(2026, 6, 30), [3, 7], None, None, None))
        mock_pg.return_value = conn
        out = ReportsRepository().get_pins(1)
        assert out == {
            'period_a_from': date(2026, 6, 1), 'period_a_to': date(2026, 6, 30),
            'period_a_store_ids': [3, 7],
            'period_b_from': None, 'period_b_to': None, 'period_b_store_ids': None,
        }

    @patch(PG)
    def test_upsert_pins_binds_and_commits(self, mock_pg):
        conn, cur = _pg_cursor()
        mock_pg.return_value = conn
        ReportsRepository().upsert_pins(
            7, date(2026, 6, 1), date(2026, 6, 30), [3, 7], None, None, None)
        sql, params = cur.execute.call_args.args
        assert 'ON CONFLICT (user_id) DO UPDATE' in sql
        assert 'period_a_store_ids' in sql
        assert params == (
            7, date(2026, 6, 1), date(2026, 6, 30), [3, 7], None, None, None)
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    @patch(PG)
    def test_upsert_pins_none_connection_raises(self, mock_pg):
        mock_pg.return_value = None
        with pytest.raises(RuntimeError, match='Postgres connection unavailable'):
            ReportsRepository().upsert_pins(7, None, None, None, None, None, None)


class TestGetStoreSids:
    """CR #81: resolve Postgres stores.id → Oracle STORE.SID."""

    @patch(PG)
    def test_none_connection_raises(self, mock_pg):
        mock_pg.return_value = None
        with pytest.raises(RuntimeError, match='Postgres connection unavailable'):
            ReportsRepository().get_store_sids([3])

    @patch(PG)
    def test_maps_id_to_int_sid(self, mock_pg):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = [(3, '501'), (7, '502')]
        cm = MagicMock()
        cm.__enter__.return_value = cur
        cm.__exit__.return_value = False
        conn.cursor.return_value = cm
        mock_pg.return_value = conn
        out = ReportsRepository().get_store_sids([3, 7])
        assert out == {3: 501, 7: 502}
        conn.close.assert_called_once()

    @patch(PG)
    def test_unmapped_or_null_rp_sid_omitted(self, mock_pg):
        """A store with a null / blank / non-numeric rp_sid is unusable and is
        left out of the result (→ caller rejects it as an invalid id)."""
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = [(3, '501'), (7, None), (9, '  '), (11, 'x')]
        cm = MagicMock()
        cm.__enter__.return_value = cur
        cm.__exit__.return_value = False
        conn.cursor.return_value = cm
        mock_pg.return_value = conn
        out = ReportsRepository().get_store_sids([3, 7, 9, 11])
        assert out == {3: 501}
