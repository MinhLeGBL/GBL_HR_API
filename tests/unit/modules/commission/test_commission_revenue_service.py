"""
Unit tests for CommissionRevenueService

Tests:
- CommissionRevenueService.init_database()
- CommissionRevenueService.save_revenue_adjustments()
- CommissionRevenueService.get_revenue_breakdown()
- CommissionRevenueService.get_store_view_breakdown()
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
        # CREATE TABLE + 2 migrations + 2 named drops + DO$$ drop + delete invalid types
        # + month check + revenue_type check + CREATE INDEX
        # + CR #28: add store_code col + drop old unique + add new unique + delete old rows
        assert mock_cursor.execute.call_count == 14
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
            ('fashion_fp', 50000000),         # RETURNING from upsert
        ]

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026, 'RWR',
            [{'revenue_type': 'fashion_fp', 'adjustment': 50000000}]
        )

        assert result['success'] is True
        assert result['data']['employee_code'] == 'GL013'
        assert len(result['data']['adjustments']) == 1
        assert result['data']['adjustments'][0]['revenue_type'] == 'fashion_fp'
        mock_conn.commit.assert_called_once()

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_success_multiple_adjustments(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        mock_cursor.fetchone.side_effect = [
            (1,),                              # employee exists
            ('fashion_fp', 50000000),          # RETURNING 1
            ('fashion_md', -10000000),         # RETURNING 2
        ]

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026, 'RWR',
            [
                {'revenue_type': 'fashion_fp', 'adjustment': 50000000},
                {'revenue_type': 'fashion_md', 'adjustment': -10000000},
            ]
        )

        assert result['success'] is True
        assert len(result['data']['adjustments']) == 2

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_connection_failure(self, mock_get_conn):
        mock_get_conn.return_value = None

        result = CommissionRevenueService().save_revenue_adjustments(
            'GL013', 3, 2026, 'RWR', [{'revenue_type': 'fashion_fp', 'adjustment': 100}]
        )

        assert result['success'] is False
        assert 'connect' in result['error'].lower()

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_employee_not_found(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        mock_cursor.fetchone.return_value = None

        result = svc.save_revenue_adjustments(
            'INVALID', 3, 2026, 'RWR', [{'revenue_type': 'fashion_fp', 'adjustment': 100}]
        )

        assert result['success'] is False
        assert result.get('not_found') is True

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_invalid_revenue_type(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        mock_cursor.fetchone.return_value = (1,)  # employee exists

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026, 'RWR', [{'revenue_type': 'invalid_type', 'adjustment': 100}]
        )

        assert result['success'] is False
        assert 'Invalid revenue_type' in result['error']

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_missing_adjustment_key(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        mock_cursor.fetchone.return_value = (1,)

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026, 'RWR', [{'revenue_type': 'fashion_fp'}]
        )

        assert result['success'] is False
        assert 'revenue_type and adjustment' in result['error']

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_missing_revenue_type_key(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        mock_cursor.fetchone.return_value = (1,)

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026, 'RWR', [{'adjustment': 100}]
        )

        assert result['success'] is False
        assert 'revenue_type and adjustment' in result['error']

    @patch('app.modules.commission.service.get_postgres_connection')
    def test_non_integer_adjustment(self, mock_get_conn):
        svc, mock_conn, mock_cursor = self._make_service_with_mock_conn(mock_get_conn)
        mock_cursor.fetchone.return_value = (1,)

        result = svc.save_revenue_adjustments(
            'GL013', 3, 2026, 'RWR', [{'revenue_type': 'fashion_fp', 'adjustment': 'abc'}]
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
            'GL013', 3, 2026, 'RWR', [{'revenue_type': 'fashion_fp', 'adjustment': 100}]
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
        mock_comm.repository.get_all_sales_data.return_value = pd.DataFrame()
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
        assert len(result['revenue_types']) == 8
        assert result['stores'] == []

    @patch('app.modules.commission.service.CommissionStoreSettingsService')
    @patch('app.modules.commission.service.CommissionService')
    @patch('app.modules.commission.service.get_postgres_connection')
    def test_oracle_failure_returns_warning(self, mock_get_conn, MockCommSvc, MockStoreSvc):
        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = []
        mock_comm.repository.get_all_sales_data.side_effect = Exception('Oracle down')

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
        mock_comm.repository.get_all_sales_data.return_value = pd.DataFrame()
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
            'fashion', 'jewelry', 'vhernier',
            'rosa_maria', 'hand_carry', 'suitcase', 'home_decor', 'other'
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
        mock_comm.repository.get_all_sales_data.return_value = pd.DataFrame()
        mock_comm.repository.get_hand_carry_upcs.return_value = []
        # _compute_revenue_by_type returns nested dicts (all 0 when no sales)
        mock_comm._compute_revenue_by_type.return_value = {
            t: {'fp': 0, 'md': 0, 'total': 0} for t in [
                'fashion', 'jewelry', 'vhernier', 'rosa_maria',
                'hand_carry', 'suitcase', 'home_decor', 'other'
            ]
        }

        MockStoreSvc.return_value.get_commission_stores.return_value = {
            'success': True, 'stores': [{'store_code': 'S01', 'store_target': None}]
        }

        # Adjustment: +100 on fashion_fp
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [('GL001', 'RWR', 'fashion_fp', 100)]
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
        # Unified DataFrame from get_all_sales_data includes doc_store_code for store filtering
        sales_df = pd.DataFrame([
            {'employee_username': 'user1', 'upc': 'U1', 'doc_store_code': 'S01',
             'vendor_code': 'ABC', 'is_jewelry': 0, 'discount_rate': 0.0,
             'revenue_with_vat': 1100, 'revenue_before_vat': 1000, 'category': 'BAGS'},
            {'employee_username': 'user1', 'upc': 'U2', 'doc_store_code': 'S01',
             'vendor_code': 'DEF', 'is_jewelry': 0, 'discount_rate': 0.5,
             'revenue_with_vat': 550, 'revenue_before_vat': 500, 'category': 'BAGS'},
        ])
        sales_df['upc_clean'] = sales_df['upc'].str.strip()

        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = [
            {'store_code': 'S01', 'store_name': 'Store 1',
             'employee_code': 'GL001', 'full_name': 'Test',
             'retailpro_username': 'user1', 'personal_target': 0},
        ]
        mock_comm.repository.get_all_sales_data.return_value = sales_df
        mock_comm.repository.get_hand_carry_upcs.return_value = []
        # _compute_revenue_by_type is called twice: once for employee (base amounts),
        # once for store-level (filtered by doc_store_code). Use side_effect.
        # CR #27: Returns nested {fp, md, total} per category
        zero = {'fp': 0, 'md': 0, 'total': 0}
        mock_comm._compute_revenue_by_type.side_effect = [
            # employee call
            {'fashion': {'fp': 1000, 'md': 500, 'total': 1500},
             'jewelry': zero, 'vhernier': zero, 'rosa_maria': zero,
             'hand_carry': zero, 'suitcase': zero, 'home_decor': zero, 'other': zero},
            # store call (filtered by doc_store_code)
            {'fashion': {'fp': 1100, 'md': 550, 'total': 1650},
             'jewelry': zero, 'vhernier': zero, 'rosa_maria': zero,
             'hand_carry': zero, 'suitcase': zero, 'home_decor': zero, 'other': zero},
        ]

        MockStoreSvc.return_value.get_commission_stores.return_value = {
            'success': True, 'stores': [{'store_code': 'S01', 'store_target': None}]
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
        # personal_total_adjusted = sum(adjusted totals) = 1000 + 500 = 1500 (no adj)
        assert emp['personal_total_adjusted'] == 1500
        # CR #24: store totals from unified DataFrame filtered by doc_store_code
        assert store['store_total_vat'] == 1650
        # CR #27: store_fp_total_vat = sum of FP from all categories = 1100
        assert store['store_fp_total_vat'] == 1100


# ===========================================================================
# get_store_view_breakdown  (CR #26)
# ===========================================================================

class TestGetStoreViewBreakdown:
    """Test store-view revenue breakdown grouped by transaction location."""

    def _zero_revenue(self):
        return {t: {'fp': 0, 'md': 0, 'total': 0} for t in [
            'fashion', 'jewelry', 'vhernier', 'rosa_maria',
            'hand_carry', 'suitcase', 'home_decor', 'other'
        ]}

    @patch('app.modules.commission.service.CommissionStoreSettingsService')
    @patch('app.modules.commission.service.CommissionService')
    @patch('app.modules.commission.service.get_postgres_connection')
    def test_empty_sales(self, mock_get_conn, MockCommSvc, MockStoreSvc):
        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = []
        mock_comm.repository.get_all_sales_data.return_value = pd.DataFrame()
        mock_comm.repository.get_hand_carry_upcs.return_value = []

        MockStoreSvc.return_value.get_commission_stores.return_value = {'success': True, 'stores': []}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().get_store_view_breakdown(3, 2026)

        assert result['success'] is True
        assert result['month'] == 3
        assert result['year'] == 2026
        assert len(result['revenue_types']) == 8
        assert result['stores'] == []

    @patch('app.modules.commission.service.CommissionStoreSettingsService')
    @patch('app.modules.commission.service.CommissionService')
    @patch('app.modules.commission.service.get_postgres_connection')
    def test_oracle_failure_returns_warning(self, mock_get_conn, MockCommSvc, MockStoreSvc):
        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = []
        mock_comm.repository.get_all_sales_data.side_effect = Exception('Oracle down')

        MockStoreSvc.return_value.get_commission_stores.return_value = {'success': True, 'stores': []}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().get_store_view_breakdown(3, 2026)

        assert result['success'] is True
        assert 'oracle_warning' in result

    @patch('app.modules.commission.service.CommissionStoreSettingsService')
    @patch('app.modules.commission.service.CommissionService')
    @patch('app.modules.commission.service.get_postgres_connection')
    def test_groups_by_doc_store_code(self, mock_get_conn, MockCommSvc, MockStoreSvc):
        """Sales at different stores produce separate store entries."""
        sales_df = pd.DataFrame([
            {'employee_username': 'user1', 'upc': 'U1', 'doc_store_code': 'RHN',
             'store_code': 'RHN', 'vendor_code': 'ABC', 'is_jewelry': 0,
             'discount_rate': 0.0, 'revenue_with_vat': 1100, 'revenue_before_vat': 1000,
             'category': 'BAGS', 'department': 'FAS'},
            {'employee_username': 'user1', 'upc': 'U2', 'doc_store_code': 'RWT',
             'store_code': 'RHN', 'vendor_code': 'DEF', 'is_jewelry': 0,
             'discount_rate': 0.0, 'revenue_with_vat': 2200, 'revenue_before_vat': 2000,
             'category': 'BAGS', 'department': 'FAS'},
        ])

        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = [
            {'store_code': 'RHN', 'store_name': 'Rolex Ha Noi',
             'employee_code': 'GL001', 'full_name': 'Nguyen A',
             'retailpro_username': 'user1', 'personal_target': 0},
        ]
        mock_comm.repository.get_all_sales_data.return_value = sales_df
        mock_comm.repository.get_hand_carry_upcs.return_value = []
        # _compute_revenue_by_type called 3 times:
        # store-level RHN, contributor user1@RHN, store-level RWT, contributor user1@RWT
        zero = self._zero_revenue()
        mock_comm._compute_revenue_by_type.side_effect = [
            # Store RHN total
            {**zero, 'fashion': {'fp': 1100, 'md': 0, 'total': 1100}},
            # Contributor user1 at RHN
            {**zero, 'fashion': {'fp': 1100, 'md': 0, 'total': 1100}},
            # Store RWT total
            {**zero, 'fashion': {'fp': 2200, 'md': 0, 'total': 2200}},
            # Contributor user1 at RWT
            {**zero, 'fashion': {'fp': 2200, 'md': 0, 'total': 2200}},
        ]

        MockStoreSvc.return_value.get_commission_stores.return_value = {'success': True, 'stores': []}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # First call: store names query; second call: adjustments query
        mock_cursor.fetchall.side_effect = [
            [('RHN', 'Rolex Ha Noi'), ('RWT', 'Rolex Warranted Retailer T')],
            [],
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().get_store_view_breakdown(3, 2026)

        assert result['success'] is True
        assert len(result['stores']) == 2
        store_codes = {s['store_code'] for s in result['stores']}
        assert store_codes == {'RHN', 'RWT'}

    @patch('app.modules.commission.service.CommissionStoreSettingsService')
    @patch('app.modules.commission.service.CommissionService')
    @patch('app.modules.commission.service.get_postgres_connection')
    def test_cross_store_flag(self, mock_get_conn, MockCommSvc, MockStoreSvc):
        """Employee assigned to RHN selling at RWT is marked as cross-store."""
        sales_df = pd.DataFrame([
            {'employee_username': 'user1', 'upc': 'U1', 'doc_store_code': 'RWT',
             'store_code': 'RHN', 'vendor_code': 'ABC', 'is_jewelry': 0,
             'discount_rate': 0.0, 'revenue_with_vat': 1100, 'revenue_before_vat': 1000,
             'category': 'BAGS', 'department': 'FAS'},
        ])

        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = [
            {'store_code': 'RHN', 'store_name': 'Rolex Ha Noi',
             'employee_code': 'GL001', 'full_name': 'Nguyen A',
             'retailpro_username': 'user1', 'personal_target': 0},
        ]
        mock_comm.repository.get_all_sales_data.return_value = sales_df
        mock_comm.repository.get_hand_carry_upcs.return_value = []

        zero = self._zero_revenue()
        mock_comm._compute_revenue_by_type.side_effect = [
            {**zero, 'fashion': {'fp': 1100, 'md': 0, 'total': 1100}},
            {**zero, 'fashion': {'fp': 1100, 'md': 0, 'total': 1100}},
        ]

        MockStoreSvc.return_value.get_commission_stores.return_value = {'success': True, 'stores': []}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [
            [('RHN', 'Rolex Ha Noi'), ('RWT', 'Rolex Warranted T')],
            [],
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().get_store_view_breakdown(3, 2026)

        store = result['stores'][0]
        assert store['store_code'] == 'RWT'
        contributor = store['contributors'][0]
        assert contributor['employee_code'] == 'GL001'
        assert contributor['assigned_store_code'] == 'RHN'
        assert contributor['is_cross_store'] is True

    @patch('app.modules.commission.service.CommissionStoreSettingsService')
    @patch('app.modules.commission.service.CommissionService')
    @patch('app.modules.commission.service.get_postgres_connection')
    def test_sysadmin_contributor(self, mock_get_conn, MockCommSvc, MockStoreSvc):
        """COSM items re-attributed to SYSADMIN appear under transaction store."""
        sales_df = pd.DataFrame([
            {'employee_username': 'SYSADMIN', 'upc': 'U1', 'doc_store_code': 'RHN',
             'store_code': None, 'vendor_code': 'CSM', 'is_jewelry': 0,
             'discount_rate': 0.0, 'revenue_with_vat': 500, 'revenue_before_vat': 454,
             'category': 'COSM', 'department': 'COSM'},
        ])

        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = []
        mock_comm.repository.get_all_sales_data.return_value = sales_df
        mock_comm.repository.get_hand_carry_upcs.return_value = []

        zero = self._zero_revenue()
        mock_comm._compute_revenue_by_type.side_effect = [
            {**zero, 'other': {'fp': 500, 'md': 0, 'total': 500}},
            {**zero, 'other': {'fp': 500, 'md': 0, 'total': 500}},
        ]

        MockStoreSvc.return_value.get_commission_stores.return_value = {'success': True, 'stores': []}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [
            [('RHN', 'Rolex Ha Noi')],
            [],
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().get_store_view_breakdown(3, 2026)

        store = result['stores'][0]
        assert store['store_code'] == 'RHN'
        contributor = store['contributors'][0]
        assert contributor['employee_code'] == 'SYSADMIN'
        assert contributor['full_name'] == 'System (COSM)'
        assert contributor['assigned_store_code'] is None
        assert contributor['is_cross_store'] is False

    @patch('app.modules.commission.service.CommissionStoreSettingsService')
    @patch('app.modules.commission.service.CommissionService')
    @patch('app.modules.commission.service.get_postgres_connection')
    def test_adjustments_applied(self, mock_get_conn, MockCommSvc, MockStoreSvc):
        """CR #28: Adjustments are applied per store for store-view."""
        sales_df = pd.DataFrame([
            {'employee_username': 'user1', 'upc': 'U1', 'doc_store_code': 'RHN',
             'store_code': 'RHN', 'vendor_code': 'ABC', 'is_jewelry': 0,
             'discount_rate': 0.0, 'revenue_with_vat': 1100, 'revenue_before_vat': 1000,
             'category': 'BAGS', 'department': 'FAS'},
        ])

        mock_comm = MockCommSvc.return_value
        mock_comm._get_employees_with_username_for_period.return_value = [
            {'store_code': 'RHN', 'store_name': 'Rolex Ha Noi',
             'employee_code': 'GL001', 'full_name': 'Nguyen A',
             'retailpro_username': 'user1', 'personal_target': 0},
        ]
        mock_comm.repository.get_all_sales_data.return_value = sales_df
        mock_comm.repository.get_hand_carry_upcs.return_value = []

        zero = self._zero_revenue()
        mock_comm._compute_revenue_by_type.side_effect = [
            {**zero, 'fashion': {'fp': 1000, 'md': 0, 'total': 1000}},
            {**zero, 'fashion': {'fp': 1000, 'md': 0, 'total': 1000}},
        ]

        MockStoreSvc.return_value.get_commission_stores.return_value = {'success': True, 'stores': []}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [
            [('RHN', 'Rolex Ha Noi')],
            [('GL001', 'RHN', 'fashion_fp', 200)],  # CR #28: per-store adjustment
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        result = CommissionRevenueService().get_store_view_breakdown(3, 2026)

        contributor = result['stores'][0]['contributors'][0]
        fashion = next(r for r in contributor['revenue'] if r['revenue_type'] == 'fashion')
        assert fashion['fp']['base_amount'] == 1000
        assert fashion['fp']['adjustment'] == 200
        assert fashion['fp']['adjusted_amount'] == 1200
        assert contributor['personal_total_adjusted'] == 1200
