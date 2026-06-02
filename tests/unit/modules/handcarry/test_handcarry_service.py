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


# Post CR #64: catalog is just (sid, scan_upc, quantity_sold)
def _catalog_row(sid, scan_upc, quantity_sold=None):
    return (sid, scan_upc, quantity_sold)


class TestListItems:
    """CR #64: list_items pulls UPC flags from Postgres and joins Oracle live
    for product info / imported qty / sold qty."""

    @patch('app.modules.handcarry.service.HandCarryService._lifetime_adjusted_in_for')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_received_for')
    @patch('app.modules.handcarry.service.HandCarryService.fetch_oracle_product_info')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_sold_for')
    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_returns_items_with_oracle_joined_fields(
        self, mock_conn, mock_sold, mock_info, mock_recv, mock_adj,
    ):
        conn, cur = _mock_conn(fetchall=[
            _catalog_row(1, 12345),
            _catalog_row(2, 67890),
        ])
        mock_conn.return_value = conn
        mock_sold.return_value = {12345: 7, 67890: 2}
        mock_recv.return_value = {12345: 10, 67890: 5}
        mock_adj.return_value = {}
        mock_info.return_value = {
            12345: {
                'description': 'GUC-Marmont bag', 'brand': 'GUCCI', 'category': 'BAG',
                'color': 'BLACK', 'size': 'M', 'season': 'SS25',
                'price_before_vat': 12000000.0, 'price_after_vat': 13200000.0,
            },
            # UPC 67890 deliberately omitted — Oracle has no info for it.
        }

        result = HandCarryService().list_items()

        assert result['success'] is True
        assert result['count'] == 2

        first = result['items'][0]
        assert first == {
            'id': 1, 'upc': 12345,
            'description': 'GUC-Marmont bag', 'brand': 'GUCCI', 'category': 'BAG',
            'color': 'BLACK', 'size': 'M', 'season': 'SS25',
            'quantity_imported': 10, 'quantity_sold': 7,
            'price_before_vat': 12000000.0, 'price_after_vat': 13200000.0,
        }

        # UPC 67890 missing from Oracle product info → nulls
        second = result['items'][1]
        assert second['description'] is None
        assert second['brand'] is None
        assert second['price_before_vat'] is None
        assert second['quantity_imported'] == 5      # received qty still present
        assert second['quantity_sold'] == 2

        args, _ = cur.execute.call_args
        assert 'ORDER BY sid' in args[0]
        assert 'ILIKE' not in args[0]
        # The SELECT no longer pulls denormalized columns.
        assert 'description' not in args[0].lower()
        assert 'brand' not in args[0].lower()

    @patch('app.modules.handcarry.service.HandCarryService._lifetime_adjusted_in_for')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_received_for')
    @patch('app.modules.handcarry.service.HandCarryService.fetch_oracle_product_info')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_sold_for')
    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_quantity_sold_defaults_to_zero(
        self, mock_conn, mock_sold, mock_info, mock_recv, mock_adj,
    ):
        conn, _cur = _mock_conn(fetchall=[_catalog_row(1, 12345)])
        mock_conn.return_value = conn
        mock_sold.return_value = {}        # Oracle: no sales for any UPC
        mock_info.return_value = {}
        mock_recv.return_value = {}
        mock_adj.return_value = {}

        result = HandCarryService().list_items()

        assert result['items'][0]['quantity_sold'] == 0
        assert result['items'][0]['quantity_imported'] is None   # nothing received

    @patch('app.modules.handcarry.service.HandCarryService._lifetime_adjusted_in_for')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_received_for')
    @patch('app.modules.handcarry.service.HandCarryService.fetch_oracle_product_info')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_sold_for')
    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_stored_quantity_sold_override_pairs_imported_and_sold(
        self, mock_conn, mock_sold, mock_info, mock_recv, mock_adj,
    ):
        """Orphan UPCs (Oracle no longer recognises them) carry a stored
        `quantity_sold` override. The response must mirror it onto
        `quantity_imported` too — the original v0.2.2 semantic was
        'qty_imp = qty_sold = override' (= fully sold-through orphan).
        Otherwise the response would falsely show 'sold N but never received'.
        """
        conn, _cur = _mock_conn(fetchall=[
            _catalog_row(1, 12345, quantity_sold=5),   # orphan: override frozen
            _catalog_row(2, 67890, quantity_sold=None),  # active: use live
        ])
        mock_conn.return_value = conn
        mock_sold.return_value = {12345: 0, 67890: 4}
        mock_info.return_value = {}
        mock_recv.return_value = {67890: 7}
        mock_adj.return_value = {}

        result = HandCarryService().list_items()

        by_upc = {it['upc']: it for it in result['items']}
        # Orphan: override mirrored to BOTH qty_imp and qty_sold.
        assert by_upc[12345]['quantity_sold'] == 5
        assert by_upc[12345]['quantity_imported'] == 5
        # Active: live Oracle values for each independently.
        assert by_upc[67890]['quantity_sold'] == 4
        assert by_upc[67890]['quantity_imported'] == 7

    @patch('app.modules.handcarry.service.HandCarryService._lifetime_adjusted_in_for')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_received_for')
    @patch('app.modules.handcarry.service.HandCarryService.fetch_oracle_product_info')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_sold_for')
    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_search_filter_uses_ilike(
        self, mock_conn, mock_sold, mock_info, mock_recv, mock_adj,
    ):
        conn, cur = _mock_conn(fetchall=[_catalog_row(3, 12399)])
        mock_conn.return_value = conn
        mock_sold.return_value = {}
        mock_info.return_value = {}
        mock_recv.return_value = {}
        mock_adj.return_value = {}

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

    # --- CR #65: union of voucher receipts + adjustment-in + sentinel ---

    @patch('app.modules.handcarry.service.HandCarryService._lifetime_adjusted_in_for')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_received_for')
    @patch('app.modules.handcarry.service.HandCarryService.fetch_oracle_product_info')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_sold_for')
    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_quantity_imported_unions_vouchers_and_adjustment_in(
        self, mock_conn, mock_sold, mock_info, mock_recv, mock_adj,
    ):
        """CR #65: items received via direct adjustment (ADJ_TYPE=1) must
        count toward quantity_imported, not just voucher receipts."""
        conn, _cur = _mock_conn(fetchall=[
            _catalog_row(1, 12345),  # voucher only
            _catalog_row(2, 67890),  # adjustment only (was returning null pre-fix)
            _catalog_row(3, 11111),  # both
        ])
        mock_conn.return_value = conn
        mock_sold.return_value = {}
        mock_info.return_value = {}
        mock_recv.return_value = {12345: 10, 11111: 3}
        mock_adj.return_value  = {67890: 4, 11111: 2}

        result = HandCarryService().list_items()
        by_upc = {it['upc']: it for it in result['items']}

        assert by_upc[12345]['quantity_imported'] == 10        # voucher only
        assert by_upc[67890]['quantity_imported'] == 4         # adj only — bug fixed
        assert by_upc[11111]['quantity_imported'] == 5         # 3 + 2

    @patch('app.modules.handcarry.service.HandCarryService._lifetime_adjusted_in_for')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_received_for')
    @patch('app.modules.handcarry.service.HandCarryService.fetch_oracle_product_info')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_sold_for')
    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_sentinel_rule_floors_imported_to_sold_when_oracle_underreports(
        self, mock_conn, mock_sold, mock_info, mock_recv, mock_adj,
    ):
        """CR #65 sentinel: when sold > received-from-Oracle, floor
        quantity_imported to quantity_sold. Sales are proof of stock."""
        conn, _cur = _mock_conn(fetchall=[
            _catalog_row(1, 12345),  # 0 received, 3 sold → floor to 3
            _catalog_row(2, 67890),  # 2 received, 5 sold → floor to 5
            _catalog_row(3, 11111),  # 10 received, 4 sold → no floor (no underreport)
        ])
        mock_conn.return_value = conn
        mock_sold.return_value = {12345: 3, 67890: 5, 11111: 4}
        mock_info.return_value = {}
        mock_recv.return_value = {67890: 2, 11111: 10}
        mock_adj.return_value  = {}

        result = HandCarryService().list_items()
        by_upc = {it['upc']: it for it in result['items']}

        # 12345: no received, 3 sold → inferred to 3
        assert by_upc[12345]['quantity_imported'] == 3
        assert by_upc[12345]['quantity_sold']     == 3
        # 67890: under-reported (2 < 5) → inferred to 5
        assert by_upc[67890]['quantity_imported'] == 5
        # 11111: received > sold → no floor needed
        assert by_upc[11111]['quantity_imported'] == 10

    @patch('app.modules.handcarry.service.HandCarryService._lifetime_adjusted_in_for')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_received_for')
    @patch('app.modules.handcarry.service.HandCarryService.fetch_oracle_product_info')
    @patch('app.modules.handcarry.service.HandCarryService._lifetime_sold_for')
    @patch('app.modules.handcarry.service.get_postgres_connection')
    def test_sentinel_does_not_fire_when_sold_is_zero(
        self, mock_conn, mock_sold, mock_info, mock_recv, mock_adj,
    ):
        """Items with no sales shouldn't be floored to anything — they
        legitimately have quantity_imported=null when Oracle has no
        record of receiving them."""
        conn, _cur = _mock_conn(fetchall=[_catalog_row(1, 12345)])
        mock_conn.return_value = conn
        mock_sold.return_value = {}        # no sales
        mock_info.return_value = {}
        mock_recv.return_value = {}        # no receipts
        mock_adj.return_value  = {}        # no adjustments

        result = HandCarryService().list_items()
        assert result['items'][0]['quantity_imported'] is None   # not floored to 0
        assert result['items'][0]['quantity_sold'] == 0


# Note: CR #58's `import_records` (full-record REPLACE) path was dropped in
# CR #64. Its 5 tests used to live here; the catalog now stores only UPCs,
# so the simpler `import_upcs` path (covered by TestImportUpcs below) is
# the only import contract.


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
