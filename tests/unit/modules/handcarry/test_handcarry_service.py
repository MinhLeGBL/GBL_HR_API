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


class TestListItems:

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_returns_all_items(self, mock_conn):
        conn, cur = _mock_conn(fetchall=[(1, 12345), (2, 67890)])
        mock_conn.return_value = conn

        result = HandCarryService().list_items()

        assert result['success'] is True
        assert result['count'] == 2
        assert result['items'] == [{'id': 1, 'upc': 12345}, {'id': 2, 'upc': 67890}]
        args, _ = cur.execute.call_args
        assert 'ORDER BY sid' in args[0]
        assert 'ILIKE' not in args[0]

    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_search_filter_uses_ilike(self, mock_conn):
        conn, cur = _mock_conn(fetchall=[(3, 12399)])
        mock_conn.return_value = conn

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
