"""Unit tests for the tracked In / Out data: parsing, search, import, list, delete."""
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.modules.tracking_system.service import (
    MAX_IMPORT_ROWS, TrackingSystemService, _sid, parse_landed_cost,
)

PG = 'app.modules.tracking_system.service.get_postgres_connection'
ORA = 'app.modules.tracking_system.service.get_oracle_connection'

BIG_SID = 690838565000149556          # > 2**53: must survive as a string


def _ora(rows_per_call):
    """Oracle mock: each execute() makes the next list in `rows_per_call` current."""
    conn = MagicMock()
    cur = conn.cursor.return_value
    state = {'rows': []}
    calls = iter(rows_per_call)

    def execute(sql, binds=None):
        state['rows'] = next(calls)
        cols = list(state['rows'][0].keys()) if state['rows'] else ['x']
        cur.description = [(c.upper(),) for c in cols]

    cur.execute.side_effect = execute
    cur.fetchall.side_effect = lambda: [tuple(r.values()) for r in state['rows']]
    return conn, cur


def _pg(fetchone=None, fetchall=None, rowcount=1):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    if fetchone is not None:
        cur.fetchone.side_effect = list(fetchone)
    if fetchall is not None:
        cur.fetchall.return_value = fetchall
    cur.rowcount = rowcount
    return conn, cur


ITEM_ROW = {'item_sid': BIG_SID, 'upc': '157820', 'alu': 'CGC-SS21-WRTW-TSH',
            'description': 'TEE', 'brand': 'COMME DES GARCONS ', 'department': 'WRTW',
            'season': 'SS21', 'landed_raw': '2188244', 'received_qty': 4}

LINE_ROW = {'line_id': BIG_SID + 1, 'bill_id': BIG_SID + 2, 'bill_no': 'RWP-556',
            'bill_date': '2026-08-03', 'store': 'RWP', 'item_sid': BIG_SID, 'upc': '157820',
            'alu': 'CGC-SS21-WRTW-TSH', 'description': 'TEE', 'brand': 'CDG',
            'department': 'WRTW', 'season': 'SS21', 'qty': -1, 'oracle_revenue': -1166851.85185}


@pytest.fixture
def svc():
    return TrackingSystemService()


class TestParsing:

    @pytest.mark.parametrize('raw,expected', [
        ('10582853', 10582853.0), (' 2188244 ', 2188244.0), ('1,234,567', 1234567.0),
        ('1.234.567', 1234567.0), ('12.5', 12.5),
        ('  -  ', None), ('undefined', None), ('', None), (None, None), ('0', None), ('-5', None),
    ])
    def test_parse_landed_cost(self, raw, expected):
        assert parse_landed_cost(raw) == expected

    @pytest.mark.parametrize('value,expected', [
        (str(BIG_SID), BIG_SID), (BIG_SID, BIG_SID), ('12', 12),
        (True, None), (12.0, None), ('12a', None), ('', None), (None, None), (0, None), (-3, None),
    ])
    def test_sid(self, value, expected):
        assert _sid(value) == expected


class TestSearch:

    @pytest.mark.parametrize('q', [None, '', ' a ', 'x' * 51])
    def test_query_length_is_checked_before_oracle(self, svc, q):
        with patch(ORA) as ora:
            for fn in (svc.search_items, svc.search_lines):
                assert fn(q)['code'] == 'INVALID_INPUT'
        ora.assert_not_called()

    def test_items_parse_landed_cost_and_send_sid_as_string(self, svc):
        conn, cur = _ora([[ITEM_ROW, {**ITEM_ROW, 'item_sid': 2, 'landed_raw': 'undefined'}]])
        with patch(ORA, return_value=conn):
            r = svc.search_items('cgc-ss21')
        assert r['success'] and r['truncated'] is False
        first, second = r['items']
        assert first['item_sid'] == str(BIG_SID)
        assert first['landed_unit_cost'] == 2188244.0 and first['received_qty'] == 4.0
        assert first['brand'] == 'COMME DES GARCONS'
        assert second['landed_unit_cost'] is None
        binds = cur.execute.call_args.args[1]
        assert binds['upc_prefix'] == 'cgc-ss21%' and binds['alu_like'] == '%CGC-SS21%'

    def test_lines_find_items_first_then_their_lines(self, svc):
        # '157820' is all digits, so it is also tried as a bill number (no hit here).
        conn, cur = _ora([[], [{'sid': BIG_SID}, {'sid': 7}], [LINE_ROW]])
        with patch(ORA, return_value=conn):
            r = svc.search_lines('157820')
        line = r['lines'][0]
        assert line['line_id'] == str(BIG_SID + 1) and line['bill_id'] == str(BIG_SID + 2)
        assert line['qty'] == -1.0 and line['oracle_revenue'] == -1166851.85
        sql, binds = cur.execute.call_args.args
        assert ':s0, :s1' in sql and binds['s0'] == BIG_SID and binds['s1'] == 7

    def test_lines_with_no_matching_item_skip_the_second_query(self, svc):
        conn, cur = _ora([[]])
        with patch(ORA, return_value=conn):
            assert svc.search_lines('zz') == {'success': True, 'lines': [], 'truncated': False}
        assert cur.execute.call_count == 1

    def test_oracle_down(self, svc):
        with patch(ORA, return_value=None):
            assert svc.search_items('abc')['code'] == 'SERVER_ERROR'


class TestImportIn:

    def test_reads_oracle_and_upserts_snapshot(self, svc):
        ora, _ = _ora([[ITEM_ROW]])
        pg, cur = _pg(fetchone=[(True,)])
        with patch(ORA, return_value=ora), patch(PG, return_value=pg):
            r = svc.import_in(9, [{'item_sid': str(BIG_SID), 'qty': 2, 'entered_unit_cost': 2300000}])
        assert r == {'success': True, 'inserted': 1, 'updated': 0}
        sql, vals = cur.execute.call_args.args
        assert 'ON CONFLICT (item_sid, (COALESCE(year, 0))) DO UPDATE' in sql
        assert 'item_sid = EXCLUDED' not in sql and 'year = EXCLUDED' not in sql   # the key is not updated
        assert vals[0] == BIG_SID                       # back to an int for BIGINT
        assert vals[7:11] == [None, Decimal('2'), Decimal('2300000'), 2188244.0]   # year None = all-time
        assert vals[-2:] == [9, 9]
        pg.commit.assert_called_once()

    def test_reimport_counts_as_update(self, svc):
        ora, _ = _ora([[ITEM_ROW]])
        pg, _ = _pg(fetchone=[(False,)])
        with patch(ORA, return_value=ora), patch(PG, return_value=pg):
            r = svc.import_in(9, [{'item_sid': str(BIG_SID), 'qty': 1, 'entered_unit_cost': 1}])
        assert r == {'success': True, 'inserted': 0, 'updated': 1}

    @pytest.mark.parametrize('row,error', [
        ({'item_sid': 1.5, 'qty': 1, 'entered_unit_cost': 1}, 'item_sid must be a SID string'),
        ({'item_sid': '1', 'qty': 0, 'entered_unit_cost': 1}, 'qty must be greater than 0'),
        ({'item_sid': '1', 'qty': 1, 'entered_unit_cost': -1}, 'entered_unit_cost must be 0 or more'),
        ({'item_sid': '1', 'qty': 1, 'entered_unit_cost': 'abc'}, 'entered_unit_cost must be 0 or more'),
        ({'item_sid': '1', 'qty': True, 'entered_unit_cost': 1}, 'qty must be greater than 0'),
    ])
    def test_invalid_rows_are_rejected_without_writing(self, svc, row, error):
        ora, _ = _ora([])
        with patch(ORA, return_value=ora), patch(PG) as pg:
            r = svc.import_in(9, [row])
        assert r['code'] == 'INVALID_INPUT' and r['errors'][0]['error'] == error
        pg.assert_not_called()

    def test_item_without_landed_cost_is_rejected(self, svc):
        ora, _ = _ora([[{**ITEM_ROW, 'landed_raw': '  -  '}]])
        with patch(ORA, return_value=ora), patch(PG) as pg:
            r = svc.import_in(9, [{'item_sid': str(BIG_SID), 'qty': 1, 'entered_unit_cost': 1}])
        assert r['errors'][0]['error'] == 'Item has no landed cost in Oracle'
        pg.assert_not_called()

    def test_one_bad_row_rejects_the_whole_import(self, svc):
        ora, cur = _ora([[ITEM_ROW]])                     # ONE batch query for both rows
        with patch(ORA, return_value=ora), patch(PG) as pg:
            r = svc.import_in(9, [{'item_sid': str(BIG_SID), 'qty': 1, 'entered_unit_cost': 1},
                                  {'item_sid': '6', 'qty': 1, 'entered_unit_cost': 1}])
        assert cur.execute.call_count == 1
        assert [e['index'] for e in r['errors']] == [1]
        assert r['errors'][0]['error'] == 'Item not found in Oracle'
        pg.assert_not_called()

    @pytest.mark.parametrize('items', [None, [], 'x', [{}] * (MAX_IMPORT_ROWS + 1)])
    def test_body_shape(self, svc, items):
        with patch(ORA) as ora:
            assert svc.import_in(9, items)['code'] == 'INVALID_INPUT'
        ora.assert_not_called()


class TestImportOut:

    def test_reads_the_line_from_oracle(self, svc):
        ora, _ = _ora([[LINE_ROW]])
        pg, cur = _pg(fetchone=[(True,)])
        with patch(ORA, return_value=ora), patch(PG, return_value=pg):
            r = svc.import_out(9, [{'line_id': str(BIG_SID + 1), 'entered_revenue': -900000}])
        assert r['success'] and r['inserted'] == 1
        sql, vals = cur.execute.call_args.args
        assert 'ON CONFLICT (line_id)' in sql
        assert vals[:3] == [BIG_SID + 1, BIG_SID + 2, 'RWP-556']
        assert vals[13:15] == [-1166851.85, Decimal('-900000')]

    @pytest.mark.parametrize('row,error', [
        ({'line_id': 3, 'entered_revenue': None}, 'entered_revenue must be a number'),
        ({'line_id': 'x', 'entered_revenue': 1}, 'line_id must be a SID string'),
    ])
    def test_invalid_rows(self, svc, row, error):
        ora, _ = _ora([])
        with patch(ORA, return_value=ora), patch(PG) as pg:
            assert svc.import_out(9, [row])['errors'][0]['error'] == error
        pg.assert_not_called()

    def test_line_not_in_oracle(self, svc):
        ora, _ = _ora([[]])
        with patch(ORA, return_value=ora), patch(PG) as pg:
            r = svc.import_out(9, [{'line_id': '3', 'entered_revenue': 1}])
        assert r['errors'][0]['error'] == 'Bill line not found in Oracle'
        pg.assert_not_called()


class TestListAndDelete:

    def test_in_rows_carry_tracking_cost(self, svc):
        row = (1, BIG_SID, '157820', 'ALU', 'TEE', 'CDG', 'WRTW', 'SS21', 2025,
               Decimal('3'), Decimal('2300000'), Decimal('2188244'), None)
        pg, _ = _pg(fetchall=[row])
        with patch(PG, return_value=pg):
            r = svc.list_in()['rows'][0]
        assert r['item_sid'] == str(BIG_SID) and r['year'] == 2025
        assert r['tracking_cost'] == (2300000 - 2188244) * 3

    def test_out_rows_carry_tracking_revenue(self, svc):
        row = (1, BIG_SID + 1, BIG_SID + 2, 'RWP-556', None, 'RWP', BIG_SID, '157820', 'ALU',
               'TEE', 'CDG', 'WRTW', 'SS21', Decimal('1'), Decimal('1166851.85'),
               Decimal('1400000'), None)
        pg, _ = _pg(fetchall=[row])
        with patch(PG, return_value=pg):
            r = svc.list_out()['rows'][0]
        assert r['line_id'] == str(BIG_SID + 1)
        assert r['tracking_revenue'] == 233148.15

    def test_delete(self, svc):
        pg, cur = _pg(rowcount=1)
        with patch(PG, return_value=pg):
            assert svc.delete('out', 4) == {'success': True}
        assert 'tracking_out_lines' in cur.execute.call_args.args[0]

    def test_delete_missing_row(self, svc):
        pg, _ = _pg(rowcount=0)
        with patch(PG, return_value=pg):
            assert svc.delete('in', 4)['code'] == 'NOT_FOUND'

    def test_delete_unknown_list_never_builds_sql(self, svc):
        with patch(PG) as pg:
            assert svc.delete('users; drop', 4)['code'] == 'INVALID_INPUT'
        pg.assert_not_called()


class TestBillSearch:

    @pytest.mark.parametrize('q,expected', [
        ('RWP-556', ('RWP', 556)), ('rwp 556', ('RWP', 556)), ('RWP556', ('RWP', 556)),
        (' 556 ', (None, 556)), ('CGC-SS21', None), ('RWP-', None), ('12345678901', None),
    ])
    def test_parse_bill_no(self, q, expected):
        from app.modules.tracking_system.service import parse_bill_no
        assert parse_bill_no(q) == expected

    def _line(self, line_id, bill_no, date):
        return {**LINE_ROW, 'line_id': line_id, 'bill_no': bill_no, 'bill_date': date}

    def test_store_prefixed_bill_searches_that_store_then_items(self, svc):
        bill_rows = [self._line(1, 'RWP-556', '2026-08-03')]
        conn, cur = _ora([bill_rows, []])                 # bill query, then no matching items
        with patch(ORA, return_value=conn):
            r = svc.search_lines('RWP-556')
        assert [l['bill_no'] for l in r['lines']] == ['RWP-556']
        sql, binds = cur.execute.call_args_list[0].args
        assert 'd.doc_no = :doc_no' in sql and binds['doc_no'] == 556 and binds['store'] == 'RWP'

    def test_bare_number_matches_bills_in_every_store_and_upc_prefixes(self, svc):
        bills = [self._line(1, 'RWP-556', '2026-08-01'), self._line(2, 'RWR-556', '2026-08-05')]
        upc_lines = [self._line(3, 'RWT-900', '2026-09-01'), self._line(1, 'RWP-556', '2026-08-01')]
        conn, cur = _ora([bills, [{'sid': 5}], upc_lines])
        with patch(ORA, return_value=conn):
            r = svc.search_lines('556')
        assert cur.execute.call_args_list[0].args[1]['store'] is None
        # Bill matches first (newest first), then the UPC match; the duplicate line is merged.
        assert [l['bill_no'] for l in r['lines']] == ['RWR-556', 'RWP-556', 'RWT-900']

    def test_non_bill_text_skips_the_bill_query(self, svc):
        conn, cur = _ora([[]])
        with patch(ORA, return_value=conn):
            svc.search_lines('CGC-SS21')
        assert cur.execute.call_count == 1
        assert 'doc_no' not in cur.execute.call_args.args[0]


class TestBatching:

    def test_import_fetches_items_in_chunks_not_one_query_each(self, svc):
        from app.modules.tracking_system.service import ORACLE_IN_CHUNK
        n = ORACLE_IN_CHUNK + 3
        rows = [{**ITEM_ROW, 'item_sid': i + 1} for i in range(n)]
        ora, cur = _ora([rows[:ORACLE_IN_CHUNK], rows[ORACLE_IN_CHUNK:]])
        pg, _ = _pg(fetchone=[(True,)] * n)
        with patch(ORA, return_value=ora), patch(PG, return_value=pg):
            r = svc.import_in(9, [{'item_sid': str(i + 1), 'qty': 1, 'entered_unit_cost': 1} for i in range(n)])
        assert r == {'success': True, 'inserted': n, 'updated': 0}
        assert cur.execute.call_count == 2

    def test_the_same_item_twice_is_rejected(self, svc):
        with patch(ORA) as ora, patch(PG) as pg:
            ora.return_value, _ = _ora([[ITEM_ROW]])
            r = svc.import_in(9, [{'item_sid': str(BIG_SID), 'qty': 1, 'entered_unit_cost': 1}] * 2)
        assert r['errors'] == [{'index': 1, 'item_sid': str(BIG_SID), 'error': 'Same item and year as row 1'}]
        pg.assert_not_called()


class TestLookupUpcs:

    def test_reports_found_missing_and_ambiguous_in_request_order(self, svc):
        rows = [{**ITEM_ROW, 'upc': '157820'},
                {**ITEM_ROW, 'item_sid': 2, 'upc': '300'}, {**ITEM_ROW, 'item_sid': 3, 'upc': '300'}]
        by_year = [{'item_sid': BIG_SID, 'yr': 2025, 'qty': 3}, {'item_sid': BIG_SID, 'yr': 2026, 'qty': 1}]
        conn, cur = _ora([rows, by_year])
        with patch(ORA, return_value=conn):
            r = svc.lookup_upcs(['300', '157820', '999', 157820])
        assert [(x['upc'], len(x['matches'])) for x in r['results']] == [('300', 2), ('157820', 1), ('999', 0)]
        assert r['results'][1]['matches'][0]['landed_unit_cost'] == 2188244.0
        sql, binds = cur.execute.call_args_list[0].args
        assert 'i.upc IN (:v0, :v1, :v2)' in sql
        assert binds == {'v0': 300, 'v1': 157820, 'v2': 999}      # numbers, not strings
        # Received qty per year, for the Excel import's Year column.
        assert r['results'][1]['matches'][0]['received_by_year'] == {'2025': 3.0, '2026': 1.0}
        assert r['results'][0]['matches'][0]['received_by_year'] == {}

    @pytest.mark.parametrize('upcs', [None, [], '157820', ['12a'], ['-1'], [True], ['1' * 16], ['1'] * 1001])
    def test_rejects_bad_input_before_oracle(self, svc, upcs):
        with patch(ORA) as ora:
            assert svc.lookup_upcs(upcs)['code'] == 'INVALID_INPUT'
        ora.assert_not_called()


class TestYear:

    @pytest.mark.parametrize('value,expected', [
        (2025, 2025), ('2026', 2026), (' 2024 ', 2024), (2000, 2000), (2100, 2100),
        (1999, None), (2101, None), ('25', None), (True, None), (2025.0, None), ('abc', None),
    ])
    def test_year_parsing(self, value, expected):
        from app.modules.tracking_system.service import _year
        assert _year(value) == expected

    def test_same_item_in_two_years_is_two_rows(self, svc):
        ora, _ = _ora([[ITEM_ROW]])
        pg, cur = _pg(fetchone=[(True,), (True,)])
        with patch(ORA, return_value=ora), patch(PG, return_value=pg):
            r = svc.import_in(9, [
                {'item_sid': str(BIG_SID), 'qty': 3, 'entered_unit_cost': 1, 'year': 2025},
                {'item_sid': str(BIG_SID), 'qty': 1, 'entered_unit_cost': 2, 'year': '2026'},
            ])
        assert r == {'success': True, 'inserted': 2, 'updated': 0}
        assert [c.args[1][7] for c in cur.execute.call_args_list] == [2025, 2026]

    @pytest.mark.parametrize('year', [1999, 'x', 2025.5])
    def test_bad_year_is_rejected(self, svc, year):
        ora, _ = _ora([])
        with patch(ORA, return_value=ora), patch(PG) as pg:
            r = svc.import_in(9, [{'item_sid': '1', 'qty': 1, 'entered_unit_cost': 1, 'year': year}])
        assert r['errors'][0]['error'] == 'year must be 2000-2100'
        pg.assert_not_called()

    def test_blank_year_means_all_time(self, svc):
        ora, _ = _ora([[ITEM_ROW]])
        pg, cur = _pg(fetchone=[(True,)])
        with patch(ORA, return_value=ora), patch(PG, return_value=pg):
            svc.import_in(9, [{'item_sid': str(BIG_SID), 'qty': 1, 'entered_unit_cost': 1, 'year': ''}])
        assert cur.execute.call_args.args[1][7] is None
