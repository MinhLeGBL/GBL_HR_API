"""
Unit tests for CommissionRepository

Mocks Oracle and PostgreSQL connections to test repository methods in isolation.
"""
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from app.modules.commission.repository import CommissionRepository


@patch("app.modules.commission.repository.CommissionQueries")
class TestCommissionRepository:
    """Tests for CommissionRepository"""

    # ------------------------------------------------------------------ #
    #  execute_query
    # ------------------------------------------------------------------ #

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_execute_query_success_with_params(self, mock_get_conn, mock_queries_cls):
        """execute_query returns list of dicts when given parameters."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("COL1",), ("COL2",)]
        mock_cursor.fetchall.return_value = [("val1", "val2")]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.execute_query("SELECT 1", {"key": "value"})

        assert result == [{"COL1": "val1", "COL2": "val2"}]
        mock_cursor.execute.assert_called_once_with("SELECT 1", {"key": "value"})
        mock_conn.close.assert_called_once()

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_execute_query_success_without_params(self, mock_get_conn, mock_queries_cls):
        """execute_query calls cursor.execute without parameters when none provided."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("ID",)]
        mock_cursor.fetchall.return_value = [(1,), (2,)]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.execute_query("SELECT id FROM t")

        assert result == [{"ID": 1}, {"ID": 2}]
        mock_cursor.execute.assert_called_once_with("SELECT id FROM t")
        mock_conn.close.assert_called_once()

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_execute_query_connection_failure(self, mock_get_conn, mock_queries_cls):
        """execute_query raises Exception when connection returns None."""
        mock_get_conn.return_value = None

        repo = CommissionRepository()
        with pytest.raises(Exception, match="Failed to connect to database"):
            repo.execute_query("SELECT 1")

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_execute_query_execution_error(self, mock_get_conn, mock_queries_cls):
        """execute_query propagates exception on query execution error and closes connection."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("ORA-00942: table or view does not exist")
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        with pytest.raises(Exception, match="ORA-00942"):
            repo.execute_query("SELECT * FROM nonexistent")

        mock_conn.close.assert_called_once()

    # ------------------------------------------------------------------ #
    #  get_store_sales_data
    # ------------------------------------------------------------------ #

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_store_sales_data_returns_first_result(self, mock_get_conn, mock_queries_cls):
        """get_store_sales_data returns the first row as a dict."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("STORE_CODE",), ("STORE_NAME",),
            ("ACTUAL_FULL_PRICE_REVENUE",), ("ACTUAL_DISCOUNTED_REVENUE",),
            ("ACTUAL_JEWELRY_REVENUE",), ("ACTUAL_SUITCASE_REVENUE",),
        ]
        mock_cursor.fetchall.return_value = [
            ("S01", "Store One", 80000, 20000, 5000, 1000),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_store_sales_data("S01", "2025-01-01 00:00:00", "2025-01-31 23:59:59")

        assert result == {
            "STORE_CODE": "S01",
            "STORE_NAME": "Store One",
            "ACTUAL_FULL_PRICE_REVENUE": 80000,
            "ACTUAL_DISCOUNTED_REVENUE": 20000,
            "ACTUAL_JEWELRY_REVENUE": 5000,
            "ACTUAL_SUITCASE_REVENUE": 1000,
        }

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_store_sales_data_returns_empty_dict_when_no_results(
        self, mock_get_conn, mock_queries_cls
    ):
        """get_store_sales_data returns {} when query yields no rows."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("STORE_CODE",)]
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_store_sales_data("ZZZ", "2025-01-01 00:00:00", "2025-01-31 23:59:59")

        assert result == {}

    # ------------------------------------------------------------------ #
    #  get_employee_sales_data
    # ------------------------------------------------------------------ #

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_employee_sales_data_returns_list(self, mock_get_conn, mock_queries_cls):
        """get_employee_sales_data returns list of employee sales dicts."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("EMPLOYEE_CODE",), ("EMPLOYEE_SID",), ("EMPLOYEE_USERNAME",),
            ("EMPLOYEE_STORE_CODE",), ("EMPLOYEE_FULL_NAME",),
            ("EMPLOYEE_REVENUE",), ("EMPLOYEE_FP_REVENUE",), ("EMPLOYEE_DISCOUNTED_REVENUE",),
        ]
        mock_cursor.fetchall.return_value = [
            ("E001", 101, "john.doe", "S01", "John Doe", 50000, 40000, 10000),
            ("E002", 102, "jane.smith", "S01", "Jane Smith", 30000, 25000, 5000),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_employee_sales_data("S01", "2025-01-01 00:00:00", "2025-01-31 23:59:59")

        assert len(result) == 2
        assert result[0]["EMPLOYEE_CODE"] == "E001"
        assert result[0]["EMPLOYEE_REVENUE"] == 50000
        assert result[1]["EMPLOYEE_USERNAME"] == "jane.smith"

    # ------------------------------------------------------------------ #
    #  get_employee_info
    # ------------------------------------------------------------------ #

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_employee_info_returns_list(self, mock_get_conn, mock_queries_cls):
        """get_employee_info returns list of employee info dicts."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("EMPLOYEE_CODE",), ("FULL_NAME",), ("STORE_CODE",)]
        mock_cursor.fetchall.return_value = [
            ("E001", "John Doe", "S01"),
            ("E002", "Jane Smith", "S01"),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_employee_info("S01")

        assert len(result) == 2
        assert result[0] == {"EMPLOYEE_CODE": "E001", "FULL_NAME": "John Doe", "STORE_CODE": "S01"}
        assert result[1]["FULL_NAME"] == "Jane Smith"

    # ------------------------------------------------------------------ #
    #  get_personal_commission_sales_data
    # ------------------------------------------------------------------ #

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_personal_commission_sales_data_returns_dataframe(
        self, mock_get_conn, mock_queries_cls
    ):
        """get_personal_commission_sales_data returns a populated DataFrame."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("sale_id",), ("upc",), ("employee_sid",), ("employee_username",),
            ("bill_number",), ("store_code",), ("sale_date",), ("sale_time",),
            ("revenue_with_vat",), ("revenue_before_vat",), ("discount_rate",),
            ("is_jewelry",), ("vendor_code",), ("category",), ("department",),
        ]
        mock_cursor.fetchall.return_value = [
            (1, "111222333", 101, "john.doe", "D001", "S01", "2025-01-15",
             "10:30:00", 11000, 10000, 0.0, 0, "VND", "Shoes", "FWOM"),
            (2, "444555666", 102, "jane.smith", "D002", "S01", "2025-01-16",
             "14:00:00", 5500, 5000, 0.1, 1, "VHN", "Rings", "WJEW"),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        df = repo.get_personal_commission_sales_data(2025, 1)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert df.iloc[0]["sale_id"] == 1
        assert df.iloc[1]["employee_username"] == "jane.smith"

        # Verify date range parameters were formatted correctly
        call_args = mock_cursor.execute.call_args
        params = call_args[0][1]
        assert params["start_date"] == "2025-01-01 00:00:00"
        assert params["end_date"] == "2025-01-31 23:59:59"

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_personal_commission_sales_data_returns_empty_dataframe(
        self, mock_get_conn, mock_queries_cls
    ):
        """get_personal_commission_sales_data returns empty DataFrame with expected columns when no data."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("sale_id",)]
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        df = repo.get_personal_commission_sales_data(2025, 6)

        assert isinstance(df, pd.DataFrame)
        assert df.empty
        expected_columns = [
            "sale_id", "employee_id", "store_id", "sale_date", "sale_time",
            "sale_datetime", "revenue_before_vat", "discount_rate", "department",
        ]
        assert list(df.columns) == expected_columns

    # ------------------------------------------------------------------ #
    #  get_multiple_stores_sales_data
    # ------------------------------------------------------------------ #

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_multiple_stores_sales_data_multiple_stores(
        self, mock_get_conn, mock_queries_cls
    ):
        """get_multiple_stores_sales_data returns dict mapping store codes to sales data."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # The method calls execute_query (and thus get_oracle_connection) once per store.
        # We simulate two successive calls with different results.
        mock_cursor.description = [
            ("STORE_CODE",), ("STORE_NAME",),
            ("ACTUAL_FULL_PRICE_REVENUE",), ("ACTUAL_DISCOUNTED_REVENUE",),
            ("ACTUAL_JEWELRY_REVENUE",), ("ACTUAL_SUITCASE_REVENUE",),
        ]
        mock_cursor.fetchall.side_effect = [
            [("S01", "Store One", 80000, 20000, 0, 0)],
            [("S02", "Store Two", 150000, 50000, 0, 0)],
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_multiple_stores_sales_data(
            ["S01", "S02"], "2025-01-01 00:00:00", "2025-01-31 23:59:59"
        )

        assert "S01" in result
        assert "S02" in result
        assert result["S01"]["ACTUAL_FULL_PRICE_REVENUE"] == 80000
        assert result["S02"]["ACTUAL_FULL_PRICE_REVENUE"] == 150000

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_multiple_stores_sales_data_empty_results(
        self, mock_get_conn, mock_queries_cls
    ):
        """get_multiple_stores_sales_data omits stores with no data."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("STORE_CODE",)]
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_multiple_stores_sales_data(
            ["S01", "S02"], "2025-01-01 00:00:00", "2025-01-31 23:59:59"
        )

        assert result == {}

    # ------------------------------------------------------------------ #
    #  get_multiple_stores_employee_sales_data
    # ------------------------------------------------------------------ #

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_multiple_stores_employee_sales_data_returns_dict_mapping(
        self, mock_get_conn, mock_queries_cls
    ):
        """get_multiple_stores_employee_sales_data returns dict mapping store codes to employee lists."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("EMPLOYEE_CODE",), ("EMPLOYEE_REVENUE",),
        ]
        mock_cursor.fetchall.side_effect = [
            [("E001", 50000), ("E002", 30000)],
            [("E003", 70000)],
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_multiple_stores_employee_sales_data(
            ["S01", "S02"], "2025-01-01 00:00:00", "2025-01-31 23:59:59"
        )

        assert "S01" in result
        assert "S02" in result
        assert len(result["S01"]) == 2
        assert len(result["S02"]) == 1
        assert result["S02"][0]["EMPLOYEE_CODE"] == "E003"

    # ------------------------------------------------------------------ #
    #  get_hand_carry_upcs
    # ------------------------------------------------------------------ #

    @patch("app.modules.commission.repository.get_postgres_connection")
    def test_get_hand_carry_upcs_success(self, mock_get_pg_conn, mock_queries_cls):
        """get_hand_carry_upcs returns list of UPC strings from PostgreSQL."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("UPC001",), ("UPC002",), ("UPC003",)]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_pg_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_hand_carry_upcs()

        assert result == ["UPC001", "UPC002", "UPC003"]
        mock_cursor.execute.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch("app.modules.commission.repository.get_postgres_connection")
    def test_get_hand_carry_upcs_connection_failure(self, mock_get_pg_conn, mock_queries_cls):
        """get_hand_carry_upcs returns empty list when PostgreSQL connection fails."""
        mock_get_pg_conn.return_value = None

        repo = CommissionRepository()
        result = repo.get_hand_carry_upcs()

        assert result == []

    @patch("app.modules.commission.repository.get_postgres_connection")
    def test_get_hand_carry_upcs_query_error(self, mock_get_pg_conn, mock_queries_cls):
        """get_hand_carry_upcs returns empty list on query execution error."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("relation does not exist")
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_pg_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_hand_carry_upcs()

        assert result == []
        mock_conn.close.assert_called_once()
