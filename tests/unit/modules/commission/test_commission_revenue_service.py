"""
Unit tests for CommissionRevenueService

Tests:
- CommissionRevenueService.init_database()
- CommissionRevenueService.save_revenue_adjustments()
- CommissionRevenueService.get_revenue_breakdown()
"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
import pandas as pd

from app.modules.commission.service import CommissionRevenueService


# ===========================================================================
# init_database
# ===========================================================================

class TestInitDatabase:

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_success(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().init_database()

        assert result['success'] is True
        assert mock_cursor.execute.call_count == 2  # CREATE TABLE + CREATE INDEX
        mock_conn.commit.assert_called_once()
        mock_cursor.close.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_connection_failure(self, mock_get_conn):
        mock_get_conn.return_value = None

        result = CommissionRevenueService().init_database()

        assert result['success'] is False
        assert 'connect' in result['error'].lower()

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_sql_error_rolls_back(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception('SQL error')
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().init_database()

        assert result['success'] is False
        assert 'SQL error' in result['error']
        mock_conn.rollback.assert_called_once()
        mock_conn.close.assert_called_once()


# ===========================================================================
# save_revenue_adjustments
# ===========================================================================

class TestSaveRevenueAdjustments:

    def _make_service_with_mock_conn(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        return CommissionRevenueService(), mock_conn, mock_cursor

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_success_single_adjustment(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        # Employee exists
        mock_cursor.fetchone.side_effect = [
            (1,),                             # employee exists check
            ('full_price', 50000000),         # RETURNING from upsert
        ]

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026,
            [{'revenue_type': 'full_price', 'adjustment': 50000000}]
        )

        assert result['success'] is True
        assert result['data']['employee_code'] == 'GL013'
        assert len(result['data']['adjustments']) == 1
        assert result['data']['adjustments'][0]['revenue_type'] == 'full_price'
        mock_conn.commit.assert_called_once()

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_success_multiple_adjustments(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        mock_cursor.fetchone.side_effect = [
            (1,),                              # employee exists
            ('full_price', 50000000),          # RETURNING 1
            ('markdown', -10000000),           # RETURNING 2
        ]

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026,
            [
                {'revenue_type': 'full_price', 'adjustment': 50000000},
                {'revenue_type': 'markdown', 'adjustment': -10000000},
            ]
        )

        assert result['success'] is True
        assert len(result['data']['adjustments']) == 2

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_connection_failure(self, mock_get_conn):
        mock_get_conn.return_value = None

        result = CommissionRevenueService().save_revenue_adjustments(
            'GL013', 3, 2026, [{'revenue_type': 'full_price', 'adjustment': 100}]
        )

        assert result['success'] is False
        assert 'connect' in result['error'].lower()

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_employee_not_found(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        mock_cursor.fetchone.return_value = None

        result = svc.save_revenue_adjustments(
            'INVALID', 3, 2026, [{'revenue_type': 'full_price', 'adjustment': 100}]
        )

        assert result['success'] is False
        assert result.get('not_found') is True

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_invalid_revenue_type(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        mock_cursor.fetchone.return_value = (1,)  # employee exists

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026, [{'revenue_type': 'invalid_type', 'adjustment': 100}]
        )

        assert result['success'] is False
        assert 'Invalid revenue_type' in result['error']

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_missing_adjustment_key(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        mock_cursor.fetchone.return_value = (1,)

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026, [{'revenue_type': 'full_price'}]
        )

        assert result['success'] is False
        assert 'revenue_type and adjustment' in result['error']

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_missing_revenue_type_key(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        mock_cursor.fetchone.return_value = (1,)

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026, [{'adjustment': 100}]
        )

        assert result['success'] is False
        assert 'revenue_type and adjustment' in result['error']

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_non_integer_adjustment(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        mock_cursor.fetchone.return_value = (1,)

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026, [{'revenue_type': 'full_price', 'adjustment': 'abc'}]
        )

        assert result['success'] is False
        assert 'must be an integer' in result['error']

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_db_error_rolls_back(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        # Employee exists, then upsert fails
        mock_cursor.fetchone.side_effect = [(1,)]
        mock_cursor.execute.side_effect = [None, None, Exception('DB write error')]
        # 1st execute: employee exists check, 2nd: fetchone succeeds, 3rd: upsert fails

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026, [{'revenue_type': 'full_price', 'adjustment': 100}]
        )

        assert result['success'] is False
        mock_conn.rollback.assert_called_once()


# ===========================================================================
# get_revenue_breakdown
# ===========================================================================

class TestGetRevenueBreakdown:
    """Test revenue breakdown with mocked dependencies."""

    @patch('app.modules.commission.service.CommissionStoreSettingsService')
    @patch('app.modules.commission.service.CommissionService')
    @patch('app.modules.commission.service.get_postgres_connection')
    def test_success_with_empty_employees(self, mock_get_conn, MockCommSvc, MockStoreSvc):
        # CommissionService mock — no employees
        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = []
        mock_comm.repository.get_personal_commission_sales_data.return_value = pd.DataFrame()
        mock_comm.repository.get_hand_carry_upcs.return_value = []

        # Store settings mock
        MockStoreSvc.return_value.get_commission_stores.return_value = {'success': True, 'stores': []}

        # _load_adjustments needs PG connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().get_revenue_breakdown(3, 2026)

        assert result['success'] is True
        assert result['month'] == 3
        assert result['year'] == 2026
        assert len(result['revenue_types']) == 7
        assert result['stores'] == []

    @patch('app.modules.commission.service.CommissionStoreSettingsService')
    @patch('app.modules.commission.service.CommissionService')
    @patch('app.modules.commission.service.get_postgres_connection')
    def test_oracle_failure_returns_warning(self, mock_get_conn, MockCommSvc, MockStoreSvc):
        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = []
        mock_comm.repository.get_personal_commission_sales_data.side_effect = Exception('Oracle down')

        MockStoreSvc.return_value.get_commission_stores.return_value = {'success': True, 'stores': []}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().get_revenue_breakdown(3, 2026)

        assert result['success'] is True
        assert 'oracle_warning' in result
        assert 'Oracle' in result['oracle_warning']

    @patch('app.modules.commission.service.CommissionStoreSettingsService')
    @patch('app.modules.commission.service.CommissionService')
    @patch('app.modules.commission.service.get_postgres_connection')
    def test_revenue_types_in_response(self, mock_get_conn, MockCommSvc, MockStoreSvc):
        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = []
        mock_comm.repository.get_personal_commission_sales_data.return_value = pd.DataFrame()
        mock_comm.repository.get_hand_carry_upcs.return_value = []

        MockStoreSvc.return_value.get_commission_stores.return_value = {'success': True, 'stores': []}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().get_revenue_breakdown(3, 2026)

        type_codes = [t['code'] for t in result['revenue_types']]
        assert type_codes == [
            'full_price', 'markdown', 'jewelry', 'vhernier',
            'rosa_maria', 'hand_carry', 'suitcase'
        ]

    @patch('app.modules.commission.service.CommissionStoreSettingsService')
    @patch('app.modules.commission.service.CommissionService')
    @patch('app.modules.commission.service.get_postgres_connection')
    def test_vat_totals_returned(self, mock_get_conn, MockCommSvc, MockStoreSvc):
        """personal_total_vat and store_total_vat reflect raw Oracle totals before adjustments."""
        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = [
            {'store_code': 'S01', 'store_name': 'Store 1',
             'employee_code': 'GL001', 'full_name': 'Test',
             'retailpro_username': None, 'personal_target': 0},
        ]
        mock_comm.repository.get_personal_commission_sales_data.return_value = pd.DataFrame()
        mock_comm.repository.get_hand_carry_upcs.return_value = []
        # _compute_revenue_by_type returns a dict of base amounts (all 0 when no sales)
        mock_comm._compute_revenue_by_type.return_value = {}
        # CR #21: location-based store totals from Oracle
        mock_comm.repository.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 0, 'ACTUAL_FULL_PRICE_REVENUE': 0
        }

        MockStoreSvc.return_value.get_commission_stores.return_value = {
            'success': True, 'stores': [{'store_code': 'S01', 'store_target': None}]
        }

        # Adjustment: +100 on full_price
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [('GL001', 'full_price', 100)]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().get_revenue_breakdown(3, 2026)

        store = result['stores'][0]
        emp = store['employees'][0]
        # No Oracle sales → personal_total_vat = 0
        assert emp['personal_total_vat'] == 0
        # Adjustment adds 100 → personal_total_adjusted = 100
        assert emp['personal_total_adjusted'] == 100
        # CR #21: Store totals from location-based Oracle query (0 when no sales)
        assert store['store_total_vat'] == 0
        assert store['store_fp_total_vat'] == 0
        assert store['store_total_adjusted'] == 100

    @patch('app.modules.commission.service.CommissionStoreSettingsService')
    @patch('app.modules.commission.service.CommissionService')
    @patch('app.modules.commission.service.get_postgres_connection')
    def test_vat_totals_from_oracle_sales(self, mock_get_conn, MockCommSvc, MockStoreSvc):
        """CR #12/#13: personal_total_vat uses revenue_with_vat; store_fp_total_vat sums FP with-VAT."""
        # Oracle sales: 2 items — 1 full_price (discount 0%), 1 markdown (discount 50%)
        sales_df = pd.DataFrame([
            {'employee_username': 'user1', 'upc': 'U1', 'vendor_code': 'ABC',
             'is_jewelry': 0, 'discount_rate': 0.0,
             'revenue_with_vat': 1100, 'revenue_before_vat': 1000, 'category': 'BAGS'},
            {'employee_username': 'user1', 'upc': 'U2', 'vendor_code': 'DEF',
             'is_jewelry': 0, 'discount_rate': 0.5,
             'revenue_with_vat': 550, 'revenue_before_vat': 500, 'category': 'BAGS'},
        ])
        sales_df['upc_clean'] = sales_df['upc'].str.strip()

        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = [
            {'store_code': 'S01', 'store_name': 'Store 1',
             'employee_code': 'GL001', 'full_name': 'Test',
             'retailpro_username': 'user1', 'personal_target': 0},
        ]
        mock_comm.repository.get_personal_commission_sales_data.return_value = sales_df
        mock_comm.repository.get_hand_carry_upcs.return_value = []
        # base amounts use revenue_before_vat: FP=1000, MD=500
        mock_comm._compute_revenue_by_type.return_value = {
            'full_price': 1000, 'markdown': 500,
        }
        # _separate_sales_by_type returns cascaded DataFrames
        mock_comm._separate_sales_by_type.return_value = {
            'hand_carry': pd.DataFrame(),
            'suitcase': pd.DataFrame(),
            'jewelry': pd.DataFrame(),
            'non_jewelry': sales_df,  # both items are non-jewelry
        }

        MockStoreSvc.return_value.get_commission_stores.return_value = {
            'success': True, 'stores': [{'store_code': 'S01', 'store_target': None}]
        }

        # CR #21: location-based store totals from Oracle
        mock_comm.repository.get_store_sales_data.return_value = {
            'ACTUAL_REVENUE': 1650, 'ACTUAL_FULL_PRICE_REVENUE': 1100
        }

        # No adjustments
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().get_revenue_breakdown(3, 2026)

        store = result['stores'][0]
        emp = store['employees'][0]
        # personal_total_vat = sum(revenue_with_vat) = 1100 + 550 = 1650
        assert emp['personal_total_vat'] == 1650
        # personal_total_adjusted = sum(base_amounts) = 1000 + 500 = 1500 (before-VAT, no adj)
        assert emp['personal_total_adjusted'] == 1500
        # CR #21: store_total_vat from location-based Oracle query
        assert store['store_total_vat'] == 1650
        # CR #21: store_fp_total_vat from location-based Oracle query
        assert store['store_fp_total_vat'] == 1100
