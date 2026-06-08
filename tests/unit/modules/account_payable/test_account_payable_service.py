"""
Unit tests for AccountPayableService (CR #60).

Phase A: init_database — schema creation for `payable_reconciliations` and
`payable_item_custom_rates`. Subsequent phases add tests for read/write
endpoints as their bodies land.
"""
from unittest.mock import patch, MagicMock

from app.modules.account_payable.service import AccountPayableService


class TestInitDatabase:

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_success_creates_all_tables(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = AccountPayableService().init_database()

        assert result == {'success': True}
        # payable_reconciliations (1) + 2 indexes
        # + payable_reconciliation_items (1) + 1 index (CR #69)
        # + payable_payment_remakes (1) + 1 index (CR #70)
        # + payable_item_custom_rates (1)
        # + payable_bill_voids (1) + 1 index (CR #68) = 10 execute calls
        assert mock_cursor.execute.call_count == 10
        mock_conn.commit.assert_called_once()

        # Spot-check that every target table and key column appears in the executed SQL.
        executed_sql = ' '.join(
            call.args[0] if call.args else '' for call in mock_cursor.execute.call_args_list
        )
        assert 'payable_reconciliations' in executed_sql
        assert 'payable_reconciliation_items' in executed_sql
        assert 'payable_payment_remakes' in executed_sql
        assert 'payable_item_custom_rates' in executed_sql
        assert 'payable_bill_voids' in executed_sql
        assert 'custom_release_rate' in executed_sql
        assert 'idx_payable_recon_bill' in executed_sql
        assert 'idx_payable_recon_payment' in executed_sql
        assert 'idx_payable_reco_items_upc' in executed_sql
        assert 'idx_payable_payment_remakes_active' in executed_sql
        assert 'idx_payable_bill_voids_bill_sid' in executed_sql

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_connection_failure_returns_error(self, mock_get_conn):
        mock_get_conn.return_value = None

        result = AccountPayableService().init_database()

        assert result['success'] is False
        assert 'connect' in result['error'].lower()

    @patch('app.modules.account_payable.service.get_postgres_connection')
    def test_sql_error_rolls_back_and_closes(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception('schema error')
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = AccountPayableService().init_database()

        assert result['success'] is False
        assert 'schema error' in result['error']
        mock_conn.rollback.assert_called_once()
        mock_conn.close.assert_called_once()
