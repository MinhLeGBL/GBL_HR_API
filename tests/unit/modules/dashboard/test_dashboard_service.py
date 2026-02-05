"""
Unit tests for DashboardService
Tests dashboard configuration, widget data retrieval, and table initialization
with mocked PostgreSQL connection.
"""
import pytest
from unittest.mock import patch, MagicMock
from app.modules.dashboard.service import DashboardService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_conn_cursor():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor


def _make_dt(iso='2024-06-15T10:30:00'):
    dt = MagicMock()
    dt.isoformat.return_value = iso
    return dt


@pytest.fixture()
def dashboard_service():
    return DashboardService()


# ======================================================================
# get_dashboard_config()
# ======================================================================

class TestGetDashboardConfig:
    @patch('app.modules.dashboard.service.get_postgres_connection')
    def test_config_exists(self, mock_get_conn, dashboard_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        dt = _make_dt()
        # First fetchone: config row
        mock_cursor.fetchone.return_value = (1, 'HR', dt, 100000001)

        # fetchall: widgets for this config
        mock_cursor.fetchall.return_value = [
            ('widget-1', 'total_employees', 0),
            ('widget-2', 'active_employees', 1),
        ]

        result = dashboard_service.get_dashboard_config('HR')
        assert result['success'] is True
        assert result['config'] is not None
        assert result['config']['department_code'] == 'HR'
        assert len(result['config']['widgets']) == 2
        assert result['config']['widgets'][0]['id'] == 'widget-1'
        assert result['config']['widgets'][0]['type'] == 'total_employees'
        assert result['config']['updated_at'] == '2024-06-15T10:30:00'

    @patch('app.modules.dashboard.service.get_postgres_connection')
    def test_config_not_found(self, mock_get_conn, dashboard_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.return_value = None  # no config for this dept

        result = dashboard_service.get_dashboard_config('UNKNOWN')
        assert result['success'] is True
        assert result['config'] is None

    @patch('app.modules.dashboard.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, dashboard_service):
        mock_get_conn.return_value = None
        result = dashboard_service.get_dashboard_config('HR')
        assert result['success'] is False
        assert 'connect' in result['error'].lower()

    @patch('app.modules.dashboard.service.get_postgres_connection')
    def test_db_exception(self, mock_get_conn, dashboard_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = Exception('query error')

        result = dashboard_service.get_dashboard_config('HR')
        assert result['success'] is False
        assert 'query error' in result['error']


# ======================================================================
# save_dashboard_config()
# ======================================================================

class TestSaveDashboardConfig:
    @patch('app.modules.dashboard.service.get_postgres_connection')
    def test_create_new_config(self, mock_get_conn, dashboard_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        dt = _make_dt()

        # fetchone calls:
        # 1) Check existing config -> None (new)
        # 2) INSERT RETURNING id -> (1,)
        # 3) Re-fetch config row after commit -> (1, 'HR', dt, 100000001)
        mock_cursor.fetchone.side_effect = [
            None,                         # no existing config
            (1,),                         # INSERT RETURNING id
            (1, 'HR', dt, 100000001),     # re-fetch config
        ]

        # fetchall for saved widgets
        mock_cursor.fetchall.return_value = [
            ('w-1', 'total_employees', 0),
        ]

        widgets = [{'id': 'w-1', 'type': 'total_employees', 'position': 0}]
        result = dashboard_service.save_dashboard_config('HR', widgets, updated_by=100000001)

        assert result['success'] is True
        assert result['config']['department_code'] == 'HR'
        assert len(result['config']['widgets']) == 1
        mock_conn.commit.assert_called_once()

    @patch('app.modules.dashboard.service.get_postgres_connection')
    def test_update_existing_config(self, mock_get_conn, dashboard_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        dt = _make_dt()

        # fetchone calls:
        # 1) Check existing config -> (5,) existing config_id
        # 2) Re-fetch config row -> (5, 'IT', dt, 100000001)
        mock_cursor.fetchone.side_effect = [
            (5,),                           # existing config
            (5, 'IT', dt, 100000001),       # re-fetch config
        ]

        # fetchall for saved widgets
        mock_cursor.fetchall.return_value = [
            ('w-10', 'monthly_sales', 0),
            ('w-11', 'attendance_rate', 1),
        ]

        widgets = [
            {'id': 'w-10', 'type': 'monthly_sales', 'position': 0},
            {'id': 'w-11', 'type': 'attendance_rate', 'position': 1},
        ]
        result = dashboard_service.save_dashboard_config('IT', widgets, updated_by=100000001)

        assert result['success'] is True
        assert result['config']['id'] == 5
        assert len(result['config']['widgets']) == 2
        mock_conn.commit.assert_called_once()

    @patch('app.modules.dashboard.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, dashboard_service):
        mock_get_conn.return_value = None
        result = dashboard_service.save_dashboard_config('HR', [], updated_by=1)
        assert result['success'] is False
        assert 'connect' in result['error'].lower()

    @patch('app.modules.dashboard.service.get_postgres_connection')
    def test_db_exception(self, mock_get_conn, dashboard_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.fetchone.side_effect = Exception('insert failed')

        result = dashboard_service.save_dashboard_config('HR', [])
        assert result['success'] is False
        assert 'insert failed' in result['error']
        mock_conn.rollback.assert_called_once()


# ======================================================================
# get_widget_data()
# ======================================================================

class TestGetWidgetData:
    def test_valid_type_total_employees(self, dashboard_service):
        """get_widget_data returns static/mock data -- no DB needed."""
        result = dashboard_service.get_widget_data('total_employees', 'HR')
        assert result['success'] is True
        assert 'data' in result
        assert result['data']['value'] == 156
        assert result['data']['label'] == 'Total Employees'

    def test_valid_type_active_employees(self, dashboard_service):
        result = dashboard_service.get_widget_data('active_employees', 'HR')
        assert result['success'] is True
        assert result['data']['value'] == 148

    def test_valid_type_pending_approvals(self, dashboard_service):
        result = dashboard_service.get_widget_data('pending_approvals', 'IT')
        assert result['success'] is True
        assert result['data']['value'] == 12

    def test_valid_type_total_commissions(self, dashboard_service):
        result = dashboard_service.get_widget_data('total_commissions', 'HR')
        assert result['success'] is True
        assert result['data']['value'] == '$45,230'

    def test_valid_type_monthly_sales(self, dashboard_service):
        result = dashboard_service.get_widget_data('monthly_sales', 'HR')
        assert result['success'] is True
        assert result['data']['value'] == '$128,450'

    def test_valid_type_open_positions(self, dashboard_service):
        result = dashboard_service.get_widget_data('open_positions', 'HR')
        assert result['success'] is True
        assert result['data']['value'] == 8

    def test_valid_type_attendance_rate(self, dashboard_service):
        result = dashboard_service.get_widget_data('attendance_rate', 'HR')
        assert result['success'] is True
        assert result['data']['value'] == '94.5%'

    def test_invalid_widget_type(self, dashboard_service):
        result = dashboard_service.get_widget_data('nonexistent_type', 'HR')
        assert result['success'] is False
        assert 'Invalid widget type' in result['error']

    def test_all_known_types_succeed(self, dashboard_service):
        """Every type listed in WIDGET_TYPES should return success."""
        for wtype in DashboardService.WIDGET_TYPES:
            result = dashboard_service.get_widget_data(wtype, 'HR')
            assert result['success'] is True, f'Failed for widget type: {wtype}'
            assert 'data' in result


# ======================================================================
# init_dashboard_tables()
# ======================================================================

class TestInitDashboardTables:
    @patch('app.modules.dashboard.service.get_postgres_connection')
    def test_success(self, mock_get_conn, dashboard_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn

        result = dashboard_service.init_dashboard_tables()
        assert result['success'] is True
        assert 'initialized' in result['message'].lower()
        mock_conn.commit.assert_called_once()

    @patch('app.modules.dashboard.service.get_postgres_connection')
    def test_db_connection_failure(self, mock_get_conn, dashboard_service):
        mock_get_conn.return_value = None
        result = dashboard_service.init_dashboard_tables()
        assert result['success'] is False
        assert 'connect' in result['error'].lower()

    @patch('app.modules.dashboard.service.get_postgres_connection')
    def test_db_exception(self, mock_get_conn, dashboard_service):
        mock_conn, mock_cursor = _mock_conn_cursor()
        mock_get_conn.return_value = mock_conn
        mock_cursor.execute.side_effect = Exception('CREATE TABLE failed')

        result = dashboard_service.init_dashboard_tables()
        assert result['success'] is False
        assert 'CREATE TABLE failed' in result['error']
        mock_conn.rollback.assert_called_once()
