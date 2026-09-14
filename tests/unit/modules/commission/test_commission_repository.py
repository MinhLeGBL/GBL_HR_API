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
    #  get_all_sales_data
    # ------------------------------------------------------------------ #

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_all_sales_data_returns_dataframe(self, mock_get_conn, mock_queries_cls):
        """get_all_sales_data returns a populated DataFrame with lowercased columns and upc_clean."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("SALE_ID",), ("UPC",), ("BILL_NUMBER",), ("BILL_SID",),
            ("DOC_STORE_CODE",), ("SALE_DATE",), ("SALE_TIME",),
            ("CUSTOMER_SID",), ("EMPLOYEE_SID",), ("EMPLOYEE_USERNAME",),
            ("STORE_CODE",), ("VENDOR_CODE",), ("IS_JEWELRY",),
            ("CATEGORY",), ("DEPARTMENT",), ("DISCOUNT_RATE",),
            ("REVENUE_WITH_VAT",), ("REVENUE_BEFORE_VAT",),
        ]
        mock_cursor.fetchall.return_value = [
            (1, "111222333", "D001", 9001, "S01", "2025-01-15", "10:30:00",
             201, 101, "john.doe", "S01", "VND", 0, "Shoes", "FWOM",
             0.0, 11000, 10000),
            (2, "444555666", "D002", 9002, "S01", "2025-01-16", "14:00:00",
             202, 102, "jane.smith", "S01", "VHN", 1, "Rings", "WJEW",
             0.1, 5500, 5000),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        df = repo.get_all_sales_data(2025, 1)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        # Columns should be lowercased
        assert "sale_id" in df.columns
        assert "employee_username" in df.columns
        assert "bill_sid" in df.columns
        # sale_id and bill_sid are stringified to preserve 18-digit Oracle SID precision
        assert df.iloc[0]["sale_id"] == "1"
        assert df.iloc[0]["bill_sid"] == "9001"
        assert df.iloc[1]["employee_username"] == "jane.smith"
        # upc_clean column should be added
        assert "upc_clean" in df.columns
        assert df.iloc[0]["upc_clean"] == "111222333"

        # Verify date range parameters were formatted correctly
        call_args = mock_cursor.execute.call_args
        params = call_args[0][1]
        assert params["start_date"] == "2025-01-01 00:00:00"
        assert params["end_date"] == "2025-01-31 23:59:59"

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_all_sales_data_returns_empty_dataframe(
        self, mock_get_conn, mock_queries_cls
    ):
        """get_all_sales_data returns empty DataFrame with expected columns when no data."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("SALE_ID",)]
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        df = repo.get_all_sales_data(2025, 6)

        assert isinstance(df, pd.DataFrame)
        assert df.empty
        expected_columns = [
            "sale_id", "upc", "bill_number", "bill_sid", "doc_store_code",
            "sale_date", "sale_time", "customer_sid", "employee_sid",
            "employee_username", "store_code", "vendor_code", "is_jewelry",
            "category", "department", "discount_rate",
            "revenue_with_vat", "revenue_before_vat", "qty_sold",
        ]
        assert list(df.columns) == expected_columns

        # Verify date range for June
        call_args = mock_cursor.execute.call_args
        params = call_args[0][1]
        assert params["start_date"] == "2025-06-01 00:00:00"
        assert params["end_date"] == "2025-06-30 23:59:59"

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_all_sales_data_preserves_18_digit_sid_precision(
        self, mock_get_conn, mock_queries_cls
    ):
        """Regression test for v1.0.2: 18-digit Oracle SIDs must round-trip
        exactly even when LEFT JOIN walk-ins put NULLs in the column.

        Pandas promotes any numeric column with NULLs to float64, whose
        ~15-digit mantissa silently rounds 18-digit SIDs (this was the
        actual bug that zeroed GL018's flat-rate commission because the
        corrupted SID stopped matching the EMPLOYEE_COMMISSION_EXCEPTIONS
        dict key). The fix stringifies SIDs *before* pandas touches them,
        so precision can't be lost regardless of NULLs.
        """
        # The exact SID hardcoded in EMPLOYEE_COMMISSION_EXCEPTIONS, plus
        # a customer_sid and sale_id of the same magnitude. The walk-in
        # row (row 2) has NULL employee/customer SIDs — this is the case
        # that triggered the float64 promotion in real Oracle data.
        EXACT_EMPLOYEE_SID = "690036963000170943"
        EXACT_CUSTOMER_SID = "690837303000121462"
        EXACT_SALE_ID      = "778100000000000123"
        EXACT_BILL_SID     = "773740427000120629"  # CR #59: bill_sid also stringified

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("SALE_ID",), ("UPC",), ("BILL_NUMBER",), ("BILL_SID",),
            ("DOC_STORE_CODE",), ("SALE_DATE",), ("SALE_TIME",),
            ("CUSTOMER_SID",), ("EMPLOYEE_SID",), ("EMPLOYEE_USERNAME",),
            ("STORE_CODE",), ("VENDOR_CODE",), ("IS_JEWELRY",),
            ("CATEGORY",), ("DEPARTMENT",), ("DISCOUNT_RATE",),
            ("REVENUE_WITH_VAT",), ("REVENUE_BEFORE_VAT",),
        ]
        # oracledb returns Python int for NUMBER columns; the fix must
        # stringify these before they hit pandas.
        mock_cursor.fetchall.return_value = [
            (int(EXACT_SALE_ID), "111", "D001", int(EXACT_BILL_SID),
             "S01", "2026-04-15", "10:30:00",
             int(EXACT_CUSTOMER_SID), int(EXACT_EMPLOYEE_SID), "linh.luu",
             "RWR", "GUC", 0, "Bag", "WRTW", 0.0, 11000, 10000),
            (int(EXACT_SALE_ID) + 1, "222", "D002", None,
             "S01", "2026-04-16", "14:00:00",
             None, None, None,
             None, "VND", 0, "Shoes", "WSHOE", 0.0, 5500, 5000),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        df = repo.get_all_sales_data(2026, 4)

        # SID columns must be object dtype carrying strings — never float64.
        assert df["sale_id"].dtype == object
        assert df["employee_sid"].dtype == object
        assert df["customer_sid"].dtype == object
        assert df["bill_sid"].dtype == object

        # Exact value preservation — string comparison can't be defeated by
        # float64 precision loss the way int comparison was.
        assert df.iloc[0]["sale_id"]      == EXACT_SALE_ID
        assert df.iloc[0]["employee_sid"] == EXACT_EMPLOYEE_SID
        assert df.iloc[0]["customer_sid"] == EXACT_CUSTOMER_SID
        assert df.iloc[0]["bill_sid"]     == EXACT_BILL_SID

        # The dict-membership check that originally failed for GL018,
        # extended to bill_sid (CR #59 uses the same pattern for AR lookup).
        exception_dict = {EXACT_EMPLOYEE_SID: "expected-match"}
        assert df.iloc[0]["employee_sid"] in exception_dict
        bill_map = {EXACT_BILL_SID: "expected-match"}
        assert df.iloc[0]["bill_sid"] in bill_map

        # NULL SIDs from the walk-in row survive as None — not stringified
        # to "None", not coerced to NaN-then-rounded.
        assert df.iloc[1]["employee_sid"] is None
        assert df.iloc[1]["customer_sid"] is None

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

    # ------------------------------------------------------------------ #
    #  get_unpaid_bill_amounts (CR #59 Phase B — AR payable detection)
    # ------------------------------------------------------------------ #

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_unpaid_bill_amounts_ref_sale_sid_settles_bill(
        self, mock_get_conn, mock_queries_cls
    ):
        """A payment with REF_SALE_SID = bill.sid fully settles that bill;
        nothing left in payable map for that bill."""
        from datetime import datetime
        BILL_SID = '100000000000000001'
        OTHER_BILL_SID = '100000000000000002'
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ('CUSTOMER_SID',), ('DOC_SID',), ('DOC_NO',), ('CHARGE_AMOUNT',),
            ('POST_DATE',), ('REF_SALE_SID',), ('SALE_TOTAL_AMT',), ('POST_MONTH',),
        ]
        mock_cursor.fetchall.return_value = [
            ('555', int(BILL_SID),       '2869',  50_000_000,
             datetime(2026, 4, 5),  None,                 50_000_000, '2026-04'),
            ('555', int(OTHER_BILL_SID), '2870',  30_000_000,
             datetime(2026, 4, 10), None,                 30_000_000, '2026-04'),
            # Payment with REF_SALE_SID pointing to the second bill.
            ('555', 999,                 '4381', -30_000_000,
             datetime(2026, 4, 15), int(OTHER_BILL_SID),  0,          '2026-04'),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_unpaid_bill_amounts(2026, 4)

        # Only the first bill (no REF-settled payment) is still open.
        assert set(result.keys()) == {BILL_SID}
        assert result[BILL_SID]['remaining_unpaid'] == 50_000_000
        assert result[BILL_SID]['unpaid_ratio'] == 1.0

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_unpaid_bill_amounts_fifo_oldest_first(
        self, mock_get_conn, mock_queries_cls
    ):
        """A payment with no REF flows FIFO: consumes oldest open bill first."""
        from datetime import datetime
        OLDEST = '200000000000000001'
        NEWER  = '200000000000000002'
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ('CUSTOMER_SID',), ('DOC_SID',), ('DOC_NO',), ('CHARGE_AMOUNT',),
            ('POST_DATE',), ('REF_SALE_SID',), ('SALE_TOTAL_AMT',), ('POST_MONTH',),
        ]
        mock_cursor.fetchall.return_value = [
            ('777', int(OLDEST), '2701', 40_000_000,
             datetime(2026, 4, 1),  None, 40_000_000, '2026-04'),
            ('777', int(NEWER),  '2789', 60_000_000,
             datetime(2026, 4, 5),  None, 60_000_000, '2026-04'),
            # No-REF payment of 70M flows FIFO: 40M to OLDEST (settles it),
            # then 30M to NEWER (leaving 30M remaining).
            ('777', 800,         '4400', -70_000_000,
             datetime(2026, 4, 10), None, 0,          '2026-04'),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_unpaid_bill_amounts(2026, 4)

        assert OLDEST not in result   # fully settled by FIFO
        assert NEWER in result
        assert result[NEWER]['remaining_unpaid'] == 30_000_000
        assert result[NEWER]['unpaid_ratio'] == 0.5

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_unpaid_bill_amounts_filters_to_target_month_only(
        self, mock_get_conn, mock_queries_cls
    ):
        """Bills created BEFORE the target month never appear, even if unpaid.
        Period scoping per CR #59: each month's payable shows only that
        month's bills — prior-month carry-over is handled in its creation
        month, not re-displayed here."""
        from datetime import datetime
        PRIOR = '300000000000000001'
        TARGET = '300000000000000002'
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ('CUSTOMER_SID',), ('DOC_SID',), ('DOC_NO',), ('CHARGE_AMOUNT',),
            ('POST_DATE',), ('REF_SALE_SID',), ('SALE_TOTAL_AMT',), ('POST_MONTH',),
        ]
        mock_cursor.fetchall.return_value = [
            ('888', int(PRIOR),  '2500', 20_000_000,
             datetime(2026, 3, 5), None, 20_000_000, '2026-03'),
            ('888', int(TARGET), '2900', 15_000_000,
             datetime(2026, 4, 12), None, 15_000_000, '2026-04'),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_unpaid_bill_amounts(2026, 4)

        # The March bill is unpaid but filtered out (period scoping).
        assert PRIOR not in result
        assert TARGET in result

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_unpaid_bill_amounts_partial_payment_keeps_residual(
        self, mock_get_conn, mock_queries_cls
    ):
        """Partial payments leave residual in the payable map with the
        correct unpaid_ratio for proportional withholding."""
        from datetime import datetime
        BILL = '400000000000000001'
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ('CUSTOMER_SID',), ('DOC_SID',), ('DOC_NO',), ('CHARGE_AMOUNT',),
            ('POST_DATE',), ('REF_SALE_SID',), ('SALE_TOTAL_AMT',), ('POST_MONTH',),
        ]
        # Bill 100M with REF-targeted payment of 30M → 70M remaining.
        mock_cursor.fetchall.return_value = [
            ('999', int(BILL), '2950', 100_000_000,
             datetime(2026, 4, 1), None, 100_000_000, '2026-04'),
            ('999', 500,       '4500', -30_000_000,
             datetime(2026, 4, 20), int(BILL), 0,    '2026-04'),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        repo = CommissionRepository()
        result = repo.get_unpaid_bill_amounts(2026, 4)

        assert BILL in result
        assert result[BILL]['remaining_unpaid'] == 70_000_000
        assert result[BILL]['unpaid_ratio'] == 0.7

    @patch("app.modules.commission.repository.get_oracle_connection")
    def test_get_unpaid_bill_amounts_connection_failure_returns_empty(
        self, mock_get_conn, mock_queries_cls
    ):
        """Repository returns empty dict instead of raising on conn failure."""
        mock_get_conn.return_value = None
        repo = CommissionRepository()
        result = repo.get_unpaid_bill_amounts(2026, 4)
        assert result == {}

    def test_replay_charge_ledger_static_helper(self, mock_queries_cls):
        """The ledger-replay helper is pure and side-effect-free; test it
        directly without DB mocking for a tight unit on the matching rules."""
        events = [
            {'doc_sid': 'A', 'doc_no': '1', 'charge_amount': 100, 'ref_sale_sid': None,
             'sale_total_amt': 100, 'post_month': '2026-04'},
            {'doc_sid': 'B', 'doc_no': '2', 'charge_amount': 200, 'ref_sale_sid': None,
             'sale_total_amt': 200, 'post_month': '2026-04'},
            # Payment with REF→B settles B; FIFO leaves A open.
            {'doc_sid': 'P1', 'doc_no': '3', 'charge_amount': -200, 'ref_sale_sid': 'B',
             'sale_total_amt': 0, 'post_month': '2026-04'},
        ]
        bills, _payments = CommissionRepository._replay_charge_ledger(events)
        assert bills['A']['remaining'] == 100
        assert bills['B']['remaining'] == 0
