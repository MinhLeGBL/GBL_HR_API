"""
Unit tests for app.modules.crm.repository.CRMRepository.

Mocks both Oracle and Postgres connections — no real DB required.
Focus is on transformation correctness (rows → dicts) and SQL composition,
not on the SQL itself (queries are tested live in dev with real Oracle).
"""
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.modules.crm.repository import CRMRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn(rows=None, description=None):
    """Build a mock DB connection with cursor returning fixed rows."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = rows or []
    cur.fetchone.return_value = (rows[0] if rows else None) if rows else (0,)
    cur.description = description or []
    cur.rowcount = len(rows) if rows else 0
    conn.cursor.return_value = cur
    return conn, cur


# ===========================================================================
# Oracle reads
# ===========================================================================

class TestFetchCustomerAggregates:

    @patch('app.modules.crm.repository.get_oracle_connection')
    def test_basic_transformation(self, mock_oracle):
        rows = [
            (12345, 'Nguyen Thi A', 'a@example.com', 30, 5, 100_000_000,
             datetime(2026, 3, 1)),
            (67890, '  Trim Me  ', None, 200, 1, 5_000_000,
             datetime(2025, 9, 15)),
        ]
        mock_oracle.return_value, _ = _make_conn(rows)

        repo = CRMRepository()
        result = repo.fetch_customer_aggregates()

        assert len(result) == 2
        assert result[0] == {
            'customer_sid': 12345,
            'name': 'Nguyen Thi A',
            'email': 'a@example.com',
            'recency': 30,
            'frequency': 5,
            'monetary': 100_000_000,
            'last_purchase_date': date(2026, 3, 1),
        }
        # name is trimmed; email stays None
        assert result[1]['name'] == 'Trim Me'
        assert result[1]['email'] is None

    @patch('app.modules.crm.repository.get_oracle_connection')
    def test_handles_null_name_and_zero_monetary(self, mock_oracle):
        rows = [(1, None, None, 50, 1, None, datetime(2026, 1, 1))]
        mock_oracle.return_value, _ = _make_conn(rows)

        result = CRMRepository().fetch_customer_aggregates()
        assert result[0]['name'] == ''
        assert result[0]['monetary'] == 0

    @patch('app.modules.crm.repository.get_oracle_connection')
    def test_empty_result(self, mock_oracle):
        mock_oracle.return_value, _ = _make_conn(rows=[])
        assert CRMRepository().fetch_customer_aggregates() == []


class TestFetchTopBrandCategory:

    @patch('app.modules.crm.repository.get_oracle_connection')
    def test_keyed_by_customer_sid(self, mock_oracle):
        rows = [
            (12345, 'AKRIS', 'WOMEN', 3),
            (67890, 'HERMES', 'BAGS', 1),
        ]
        mock_oracle.return_value, _ = _make_conn(rows)

        result = CRMRepository().fetch_top_brand_category()
        assert result == {
            12345: {'top_brand': 'AKRIS', 'top_category': 'WOMEN', 'category_breadth': 3},
            67890: {'top_brand': 'HERMES', 'top_category': 'BAGS', 'category_breadth': 1},
        }

    @patch('app.modules.crm.repository.get_oracle_connection')
    def test_handles_null_breadth(self, mock_oracle):
        # Customer in DCS join missed → breadth comes back as None
        rows = [(99, 'BRAND', 'CAT', None)]
        mock_oracle.return_value, _ = _make_conn(rows)
        result = CRMRepository().fetch_top_brand_category()
        assert result[99]['category_breadth'] == 0


class TestFetchCustomerPhones:

    @patch('app.modules.crm.repository.get_oracle_connection')
    def test_returns_dict(self, mock_oracle):
        rows = [(123, '0909363636'), (456, '0982610062')]
        mock_oracle.return_value, _ = _make_conn(rows)
        assert CRMRepository().fetch_customer_phones() == {
            123: '0909363636', 456: '0982610062',
        }

    @patch('app.modules.crm.repository.get_oracle_connection')
    def test_empty(self, mock_oracle):
        mock_oracle.return_value, _ = _make_conn(rows=[])
        assert CRMRepository().fetch_customer_phones() == {}


# ===========================================================================
# Postgres writes
# ===========================================================================

class TestReplaceCustomerScores:

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_truncate_then_insert(self, mock_pg):
        conn, cur = _make_conn([])
        cur.rowcount = 2
        mock_pg.return_value = conn

        scored = [
            {'customer_sid': 1, 'name': 'A', 'email': None, 'phone': '0909',
             'recency': 30, 'frequency': 5, 'monetary': 1000,
             'r_score': 5, 'f_score': 4, 'm_score': 3, 'weighted_score': 3.7,
             'segment': 'Loyalist', 'top_brand': 'X', 'top_category': 'Y',
             'category_breadth': 2, 'last_purchase_date': date(2026, 3, 1)},
            {'customer_sid': 2, 'name': 'B', 'email': None, 'phone': None,
             'recency': 100, 'frequency': 1, 'monetary': 50,
             'r_score': 4, 'f_score': 1, 'm_score': 1, 'weighted_score': 1.6,
             'segment': 'Prospect', 'top_brand': None, 'top_category': None,
             'category_breadth': 0, 'last_purchase_date': date(2026, 1, 5)},
        ]
        n = CRMRepository().replace_customer_scores(scored)

        assert n == 2
        # Verify TRUNCATE was called first
        truncate_calls = [c for c in cur.execute.call_args_list
                          if 'TRUNCATE' in str(c)]
        assert len(truncate_calls) == 1
        # executemany was called for the bulk insert
        cur.executemany.assert_called_once()
        # transaction committed
        conn.commit.assert_called_once()

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_empty_input_returns_zero_no_db_call(self, mock_pg):
        # When there are no rows, we shouldn't even open a connection
        result = CRMRepository().replace_customer_scores([])
        assert result == 0
        mock_pg.assert_not_called()

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_rolls_back_on_error(self, mock_pg):
        conn = MagicMock()
        cur = MagicMock()
        # Bulk insert fails after TRUNCATE — must rollback
        cur.executemany.side_effect = Exception('boom')
        conn.cursor.return_value = cur
        mock_pg.return_value = conn

        with pytest.raises(Exception, match='boom'):
            CRMRepository().replace_customer_scores([
                {'customer_sid': 1, 'name': 'A', 'email': None, 'phone': None,
                 'recency': 30, 'frequency': 5, 'monetary': 1000,
                 'r_score': 5, 'f_score': 4, 'm_score': 3, 'weighted_score': 3.7,
                 'segment': 'Loyalist', 'top_brand': None, 'top_category': None,
                 'category_breadth': 0, 'last_purchase_date': date(2026, 3, 1)},
            ])
        conn.rollback.assert_called_once()


class TestUpsertSegmentSnapshot:

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_inserts_one_row_per_segment(self, mock_pg):
        conn, cur = _make_conn([])
        mock_pg.return_value = conn

        n = CRMRepository().upsert_segment_snapshot(
            date(2026, 4, 1),
            {'VIC': 38, 'Loyalist': 78, 'Lapsed': 75},
        )
        assert n == 3
        assert cur.execute.call_count == 3
        # Each call uses ON CONFLICT for idempotency
        for call in cur.execute.call_args_list:
            assert 'ON CONFLICT' in call[0][0]

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_empty_input(self, mock_pg):
        assert CRMRepository().upsert_segment_snapshot(date(2026, 4, 1), {}) == 0
        mock_pg.assert_not_called()


# ===========================================================================
# Postgres reads
# ===========================================================================

class TestCountScoredCustomers:

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_returns_count(self, mock_pg):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = (3805,)
        conn.cursor.return_value = cur
        mock_pg.return_value = conn

        assert CRMRepository().count_scored_customers() == 3805


class TestListCustomerScores:

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_no_filters(self, mock_pg):
        cols = ['customer_sid', 'name', 'segment']
        rows = [(1, 'Alice', 'VIC')]
        conn, cur = _make_conn(rows, [(c, None, None, None, None, None, None) for c in cols])
        # description format expected by repo: cur.description[i][0] = name
        cur.description = [(c,) for c in cols]
        mock_pg.return_value = conn

        result = CRMRepository().list_customer_scores()
        assert result == [{'customer_sid': 1, 'name': 'Alice', 'segment': 'VIC'}]

        # No WHERE clause when no filters
        executed_sql = cur.execute.call_args[0][0]
        assert 'WHERE' not in executed_sql

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_segment_filter(self, mock_pg):
        conn, cur = _make_conn([])
        cur.description = [('customer_sid',)]
        mock_pg.return_value = conn

        CRMRepository().list_customer_scores(segment='VIC')
        executed_sql, params = cur.execute.call_args[0]
        assert 'segment = %s' in executed_sql
        assert params == ['VIC']

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_both_filters(self, mock_pg):
        conn, cur = _make_conn([])
        cur.description = [('customer_sid',)]
        mock_pg.return_value = conn

        CRMRepository().list_customer_scores(segment='Loyalist', min_weighted_score=3.5)
        executed_sql, params = cur.execute.call_args[0]
        assert 'segment = %s' in executed_sql
        assert 'weighted_score >= %s' in executed_sql
        assert params == ['Loyalist', 3.5]


class TestAggregateSegmentSummary:

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_basic_aggregation(self, mock_pg):
        cols = ['segment', 'count', 'revenue', 'avg_recency', 'avg_frequency', 'avg_monetary']
        rows = [
            ('VIC', 38, 9_500_000_000, 35, 14.2, 250_000_000),
            ('Lapsed', 75, 100_000, 600, 1.0, 1333),
        ]
        conn, cur = _make_conn(rows)
        cur.description = [(c,) for c in cols]
        mock_pg.return_value = conn

        result = CRMRepository().aggregate_segment_summary()
        assert len(result) == 2
        assert result[0]['segment'] == 'VIC'
        assert result[0]['count'] == 38


class TestFetchSegmentSnapshots:

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_fetches_with_month_param(self, mock_pg):
        conn, cur = _make_conn([])
        cur.description = [('snapshot_month',), ('segment',), ('customer_count',)]
        mock_pg.return_value = conn

        CRMRepository().fetch_segment_snapshots(months=12)
        executed_sql, params = cur.execute.call_args[0]
        assert 'snapshot_month' in executed_sql
        # months-1 because we want N months INCLUDING current
        assert params == (11,)

    def test_invalid_months_returns_empty(self):
        # No DB call when months < 1
        with patch('app.modules.crm.repository.get_postgres_connection') as m:
            assert CRMRepository().fetch_segment_snapshots(months=0) == []
            m.assert_not_called()


class TestAggregateRfHeatmap:

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_returns_cells(self, mock_pg):
        cols = ['r', 'f', 'count', 'total_monetary']
        rows = [(5, 5, 28, 7_000_000_000), (5, 4, 15, 1_800_000_000)]
        conn, cur = _make_conn(rows)
        cur.description = [(c,) for c in cols]
        mock_pg.return_value = conn

        result = CRMRepository().aggregate_rf_heatmap()
        assert len(result) == 2
        assert result[0] == {'r': 5, 'f': 5, 'count': 28, 'total_monetary': 7_000_000_000}


# ===========================================================================
# Product analysis (CR #48)
# ===========================================================================

class TestFetchProductAggregates:

    @patch('app.modules.crm.repository.get_oracle_connection')
    def test_brand_dispatch(self, mock_oracle):
        rows = [(123, 'GUCCI', 4, 50_000_000, 30)]
        conn, cur = _make_conn(rows)
        mock_oracle.return_value = conn

        result = CRMRepository().fetch_product_aggregates(group_by='brand')
        assert result == [{
            'customer_sid': 123, 'group_name': 'GUCCI',
            'items': 4, 'revenue': 50_000_000, 'customer_recency_days': 30,
        }]
        # The brand SQL should have been used (sanity-check it joined VENDOR)
        executed_sql = cur.execute.call_args[0][0]
        assert 'VENDOR' in executed_sql
        assert 'SUM(NVL(di.QTY' in executed_sql
        assert 'MAX(' in executed_sql

    @patch('app.modules.crm.repository.get_oracle_connection')
    def test_category_dispatch(self, mock_oracle):
        rows = [(123, 'WOMEN - DRESS', 2, 10_000_000, 15)]
        conn, cur = _make_conn(rows)
        mock_oracle.return_value = conn

        result = CRMRepository().fetch_product_aggregates(group_by='category')
        assert result[0]['group_name'] == 'WOMEN - DRESS'
        assert result[0]['items'] == 2
        assert result[0]['customer_recency_days'] == 15
        executed_sql = cur.execute.call_args[0][0]
        # Category SQL uses DCS + INVN_SBS_EXTEND, not VENDOR
        assert 'DCS' in executed_sql
        assert 'INVN_SBS_EXTEND' in executed_sql

    def test_invalid_group_by(self):
        with pytest.raises(ValueError):
            CRMRepository().fetch_product_aggregates(group_by='color')


class TestReplaceProductScores:

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_truncate_then_insert(self, mock_pg):
        conn, cur = _make_conn([])
        cur.rowcount = 2
        mock_pg.return_value = conn

        rows = [
            {'group_by': 'brand', 'segment': 'VIC', 'name': 'GUCCI',
             'customer_count': 23,
             'recency_days': 12.5, 'frequency': 50, 'monetary': 100_000_000,
             'r_score': 5, 'f_score': 5, 'm_score': 5,
             'weighted_score': 5.0, 'c_score': 5, 'compound_score': 5.0},
            {'group_by': 'brand', 'segment': 'VIC', 'name': 'PRADA',
             'customer_count': 15,
             'recency_days': 30.0, 'frequency': 25, 'monetary': 40_000_000,
             'r_score': 4, 'f_score': 4, 'm_score': 4,
             'weighted_score': 4.0, 'c_score': 4, 'compound_score': 4.0},
        ]
        inserted = CRMRepository().replace_product_scores(rows)
        assert inserted == 2
        # First call should be TRUNCATE; executemany used for INSERT
        first_sql = cur.execute.call_args_list[0][0][0]
        assert 'TRUNCATE' in first_sql
        cur.executemany.assert_called_once()

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_empty_truncates_only(self, mock_pg):
        conn, cur = _make_conn([])
        mock_pg.return_value = conn

        inserted = CRMRepository().replace_product_scores([])
        assert inserted == 0
        cur.executemany.assert_not_called()


class TestListProductScores:

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_filter_group_by_only(self, mock_pg):
        cols = ['segment', 'name', 'customer_count', 'recency_days',
                'frequency', 'monetary', 'r_score', 'f_score', 'm_score',
                'weighted_score', 'c_score', 'compound_score']
        rows = [('VIC', 'GUCCI', 23, 12.5, 50, 100_000_000, 5, 5, 5,
                 5.0, 5, 5.0)]
        conn, cur = _make_conn(rows)
        cur.description = [(c,) for c in cols]
        mock_pg.return_value = conn

        result = CRMRepository().list_product_scores(group_by='brand')
        assert len(result) == 1
        assert result[0]['customer_count'] == 23
        assert result[0]['compound_score'] == 5.0
        executed_sql, params = cur.execute.call_args[0]
        assert 'group_by = %s' in executed_sql
        assert 'segment = %s' not in executed_sql
        # CR #51: primary sort is compound_score
        assert 'compound_score DESC' in executed_sql
        assert params == ['brand']

    @patch('app.modules.crm.repository.get_postgres_connection')
    def test_filter_group_by_and_segment(self, mock_pg):
        cols = ['segment', 'name', 'customer_count', 'recency_days',
                'frequency', 'monetary', 'r_score', 'f_score', 'm_score',
                'weighted_score', 'c_score', 'compound_score']
        conn, cur = _make_conn([])
        cur.description = [(c,) for c in cols]
        mock_pg.return_value = conn

        CRMRepository().list_product_scores(group_by='category', segment='VIC')
        executed_sql, params = cur.execute.call_args[0]
        assert 'segment = %s' in executed_sql
        assert params == ['category', 'VIC']
