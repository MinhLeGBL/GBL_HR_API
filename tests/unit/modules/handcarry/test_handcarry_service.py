"""Unit tests for HandCarryService — PostgreSQL mocked at the connection layer."""
from unittest.mock import MagicMock, patch

from app.modules.handcarry.service import HandCarryService


def _mock_conn(fetchall=None, fetchone=None):
    cur = MagicMock()
    cur.fetchall.return_value = fetchall if fetchall is not None else []
    cur.fetchone.return_value = fetchone
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


# Full 12-column row layout matching `_CATALOG_COLUMNS` in service.py
def _catalog_row(sid, scan_upc, description=None, brand=None, category=None,
                 color=None, size=None, season=None, quantity_imported=None,
                 quantity_sold=None,
                 price_before_vat=None, price_after_vat=None):
    return (sid, scan_upc, description, brand, category, color, size, season,
            quantity_imported, quantity_sold,
            price_before_vat, price_after_vat)


class TestListItems:

    @patch('app.modules.handcarry.service.HandCarryService._lifetime_sold_for')
    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_returns_all_items_with_extended_fields(self, mock_conn, mock_sold):
        conn, cur = _mock_conn(fetchall=[
            _catalog_row(1, 12345, description='Marmont bag', brand='GUCCI',
                         category='BAG', color='BLACK', size='M', season='SS25',
                         quantity_imported=10, price_before_vat=12000000, price_after_vat=13200000),
            _catalog_row(2, 67890, brand='PRADA', quantity_imported=5,
                         price_before_vat=5000000, price_after_vat=5500000),
        ])
        mock_conn.return_value = conn
        mock_sold.return_value = {12345: 7, 67890: 2}

        result = HandCarryService().list_items()

        assert result['success'] is True
        assert result['count'] == 2
        first = result['items'][0]
        assert first == {
            'id': 1, 'upc': 12345,
            'description': 'Marmont bag', 'brand': 'GUCCI', 'category': 'BAG',
            'color': 'BLACK', 'size': 'M', 'season': 'SS25',
            'quantity_imported': 10, 'quantity_sold': 7,
            'price_before_vat': 12000000.0, 'price_after_vat': 13200000.0,
        }
        # UPC 67890 has no description / category etc. → nulls preserved
        second = result['items'][1]
        assert second['description'] is None
        assert second['brand'] == 'PRADA'
        assert second['quantity_sold'] == 2

        args, _ = cur.execute.call_args
        assert 'ORDER BY sid' in args[0]
        assert 'ILIKE' not in args[0]

    @patch('app.modules.handcarry.service.HandCarryService._lifetime_sold_for')
    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_quantity_sold_defaults_to_zero(self, mock_conn, mock_sold):
        conn, _cur = _mock_conn(fetchall=[_catalog_row(1, 12345, quantity_imported=3)])
        mock_conn.return_value = conn
        mock_sold.return_value = {}   # Oracle returned nothing

        result = HandCarryService().list_items()

        assert result['items'][0]['quantity_sold'] == 0
        assert result['items'][0]['quantity_imported'] == 3

    @patch('app.modules.handcarry.service.HandCarryService._lifetime_sold_for')
    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_stored_quantity_sold_takes_precedence_over_oracle(self, mock_conn, mock_sold):
        """Orphan rows with stored quantity_sold ignore the Oracle live join."""
        conn, _cur = _mock_conn(fetchall=[
            _catalog_row(1, 12345, quantity_imported=5, quantity_sold=5),   # orphan, frozen
            _catalog_row(2, 67890, quantity_imported=10),                   # active, live
        ])
        mock_conn.return_value = conn
        # Oracle would say UPC 12345 had no sales, UPC 67890 had 4 sales
        mock_sold.return_value = {12345: 0, 67890: 4}

        result = HandCarryService().list_items()

        items_by_upc = {it['upc']: it for it in result['items']}
        # Orphan: stored override wins even though Oracle says 0
        assert items_by_upc[12345]['quantity_sold'] == 5
        # Active: stored is NULL → use Oracle's 4
        assert items_by_upc[67890]['quantity_sold'] == 4

    @patch('app.modules.handcarry.service.HandCarryService._lifetime_sold_for')
    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_search_filter_uses_ilike(self, mock_conn, mock_sold):
        conn, cur = _mock_conn(fetchall=[_catalog_row(3, 12399)])
        mock_conn.return_value = conn
        mock_sold.return_value = {}

        result = HandCarryService().list_items(search='123')

        assert result['success'] is True
        args, _ = cur.execute.call_args
        assert 'ILIKE' in args[0]
        assert args[1] == ('%123%',)

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_connection_failure(self, mock_conn):
        mock_conn.return_value = None
        result = HandCarryService().list_items()
        assert result['success'] is False
        assert 'connect' in result['error'].lower()


class TestImportRecords:
    """CR #58: full-record REPLACE semantics."""

    def _full_record(self, upc, **overrides):
        rec = {
            'upc': upc,
            'description': 'Test product',
            'brand': 'TEST',
            'category': 'BAG',
            'color': 'BLACK',
            'size': 'M',
            'season': 'SS25',
            'quantity_imported': 10,
            'price_before_vat': 1000,
            'price_after_vat': 1100,
        }
        rec.update(overrides)
        return rec

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_all_new_records_inserted(self, mock_conn):
        conn, cur = _mock_conn(fetchall=[])
        mock_conn.return_value = conn

        records = [self._full_record(100), self._full_record(200)]
        result = HandCarryService().import_records(records)

        assert result['success'] is True
        assert result['inserted'] == 2
        assert result['updated'] == 0
        assert result['skipped'] == 0
        assert result['errors'] == []
        assert result['total_received'] == 2

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_existing_records_are_updated(self, mock_conn):
        conn, cur = _mock_conn(fetchall=[(100,), (200,)])
        mock_conn.return_value = conn

        records = [self._full_record(100), self._full_record(200), self._full_record(300)]
        result = HandCarryService().import_records(records)

        assert result['inserted'] == 1   # 300
        assert result['updated'] == 2    # 100, 200
        assert result['skipped'] == 0

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_duplicate_upc_in_batch_last_wins(self, mock_conn):
        conn, cur = _mock_conn(fetchall=[])
        mock_conn.return_value = conn

        records = [
            self._full_record(500, quantity_imported=1),
            self._full_record(500, quantity_imported=99),  # this one wins
        ]
        result = HandCarryService().import_records(records)

        # The duplicate collapses to a single INSERT
        assert result['inserted'] == 1
        assert result['updated'] == 0

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_required_fields_validation(self, mock_conn):
        conn, _ = _mock_conn(fetchall=[])
        mock_conn.return_value = conn

        records = [
            {'upc': 100},                                    # missing qty_imp + prices
            {'upc': 200, 'quantity_imported': -1,            # negative qty
             'price_before_vat': 0, 'price_after_vat': 0},
            {'upc': 300, 'quantity_imported': 5,
             'price_before_vat': 1000, 'price_after_vat': 1100},  # OK
            {'upc': 'abc', 'quantity_imported': 5,
             'price_before_vat': 1000, 'price_after_vat': 1100},  # bad upc
            'not a dict',                                    # bad shape
        ]
        result = HandCarryService().import_records(records)

        assert result['inserted'] == 1   # only the 3rd row
        assert len(result['errors']) == 4
        assert {e['row_index'] for e in result['errors']} == {0, 1, 3, 4}

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_non_array_input_rejected(self, mock_conn):
        result = HandCarryService().import_records("not a list")
        assert result['success'] is False
        assert "'records' must be an array" in result['error']
        mock_conn.assert_not_called()


class TestImportUpcs:

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_all_new_upcs_inserted(self, mock_conn):
        conn, cur = _mock_conn(fetchall=[])  # nothing existing
        mock_conn.return_value = conn

        result = HandCarryService().import_upcs([100, 200, 300])

        assert result['success'] is True
        assert result['inserted'] == 3
        assert result['skipped'] == 0
        assert result['errors'] == []
        assert result['total_received'] == 3
        conn.commit.assert_called_once()

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_existing_upcs_are_skipped(self, mock_conn):
        conn, cur = _mock_conn(fetchall=[(100,), (200,)])
        mock_conn.return_value = conn

        result = HandCarryService().import_upcs([100, 200, 300, 400])

        assert result['inserted'] == 2  # 300, 400
        assert result['skipped'] == 2   # 100, 200

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_duplicates_in_batch_only_inserted_once(self, mock_conn):
        conn, cur = _mock_conn(fetchall=[])
        mock_conn.return_value = conn

        result = HandCarryService().import_upcs([500, 500, 500])

        assert result['inserted'] == 1
        assert result['skipped'] == 2

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_invalid_values_collected_in_errors(self, mock_conn):
        conn, cur = _mock_conn(fetchall=[])
        mock_conn.return_value = conn

        result = HandCarryService().import_upcs([100, 'abc', -5, None, True, 200])

        assert result['inserted'] == 2  # only 100 and 200 succeed
        assert len(result['errors']) == 4
        # Each error has a row_index pointing at original position
        rows_with_errors = [e['row_index'] for e in result['errors']]
        assert rows_with_errors == [1, 2, 3, 4]

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_non_array_input_rejected(self, mock_conn):
        result = HandCarryService().import_upcs("not a list")
        assert result['success'] is False
        assert 'array' in result['error'].lower()
        mock_conn.assert_not_called()


class TestUpdateItem:

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_update_success(self, mock_conn):
        cur = MagicMock()
        # 1st fetchone: dup-check returns None (no conflict). 2nd: UPDATE RETURNING.
        cur.fetchone.side_effect = [None, (7, 99999)]
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        result = HandCarryService().update_item(7, 99999)

        assert result['success'] is True
        assert result['item'] == {'id': 7, 'upc': 99999}
        conn.commit.assert_called_once()

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_update_duplicate_upc_returns_409(self, mock_conn):
        cur = MagicMock()
        cur.fetchone.return_value = (999,)  # dup-check finds another row
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        result = HandCarryService().update_item(7, 12345)

        assert result['success'] is False
        assert result['status'] == 409

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_update_missing_id_returns_404(self, mock_conn):
        cur = MagicMock()
        cur.fetchone.side_effect = [None, None]  # dup-check clean, UPDATE finds nothing
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        result = HandCarryService().update_item(999, 12345)

        assert result['success'] is False
        assert result['status'] == 404

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_invalid_upc_rejected(self, mock_conn):
        result = HandCarryService().update_item(1, -1)
        assert result['success'] is False
        assert 'invalid UPC' in result['error']


class TestDeleteItem:

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_delete_success(self, mock_conn):
        cur = MagicMock()
        cur.fetchone.return_value = (5,)
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        result = HandCarryService().delete_item(5)

        assert result['success'] is True
        assert result['id'] == 5

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_delete_missing_id_returns_404(self, mock_conn):
        cur = MagicMock()
        cur.fetchone.return_value = None
        conn = MagicMock()
        conn.cursor.return_value = cur
        mock_conn.return_value = conn

        result = HandCarryService().delete_item(999)

        assert result['success'] is False
        assert result['status'] == 404
