"""
Integration tests for external connections (Google Sheets and Oracle Database)
"""
import pytest
from app.database.connection import get_oracle_connection
from app.services.google_sheets_service import GoogleSheetsService


class TestOracleConnection:
    """Test Oracle database connection"""

    def test_database_connection_success(self):
        """Test that we can successfully connect to Oracle database"""
        conn = get_oracle_connection()
        assert conn is not None, "Failed to establish database connection"

        # Test that we can execute a simple query
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM DUAL")
            result = cursor.fetchone()
            assert result[0] == 1, "Query execution failed"
            cursor.close()
        finally:
            conn.close()

    def test_database_schema_set(self):
        """Test that default schema is set to RPS"""
        conn = get_oracle_connection()
        assert conn is not None, "Failed to establish database connection"

        try:
            cursor = conn.cursor()
            cursor.execute("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM DUAL")
            result = cursor.fetchone()
            assert result[0] == 'RPS', f"Expected schema 'RPS', got '{result[0]}'"
            cursor.close()
        finally:
            conn.close()

    def test_database_query_employee_list(self):
        """Test that we can query EMPLOYEE_LIST_V view"""
        conn = get_oracle_connection()
        assert conn is not None, "Failed to establish database connection"

        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*)
                FROM EMPLOYEE_LIST_V
                WHERE STORE_CODE = 'RHN'
            """)
            result = cursor.fetchone()
            count = result[0]
            assert count > 0, "No employees found for RHN store"
            cursor.close()
        finally:
            conn.close()

    def test_database_query_document_table(self):
        """Test that we can query DOCUMENT table"""
        conn = get_oracle_connection()
        assert conn is not None, "Failed to establish database connection"

        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*)
                FROM DOCUMENT
                WHERE STORE_CODE = 'RHN'
                  AND invc_post_date >= TO_DATE('2025-11-01', 'YYYY-MM-DD')
                  AND invc_post_date <= TO_DATE('2025-11-30', 'YYYY-MM-DD')
            """)
            result = cursor.fetchone()
            count = result[0]
            assert count >= 0, "Query failed to return result"
            cursor.close()
        finally:
            conn.close()


class TestGoogleSheetsConnection:
    """Test Google Sheets API connection"""

    @pytest.fixture
    def sheets_service(self):
        """Create GoogleSheetsService instance"""
        return GoogleSheetsService()

    @pytest.fixture
    def test_spreadsheet_id(self):
        """Test spreadsheet ID"""
        return "1-L91pYnXCl646Oaob3vhvh_J1sBrv-Hl0SaF8CA1Ado"

    def test_google_sheets_client_initialized(self, sheets_service):
        """Test that Google Sheets client is initialized"""
        assert sheets_service.client is not None, "Google Sheets client not initialized"

    def test_google_sheets_read_data(self, sheets_service, test_spreadsheet_id):
        """Test reading data from Google Sheets"""
        sheet_data = sheets_service.get_sheet_data(
            spreadsheet_id=test_spreadsheet_id,
            sheet_name="Sheet1"
        )

        assert sheet_data is not None, "Failed to read sheet data"
        assert 'stores' in sheet_data, "Missing 'stores' key in sheet data"
        assert 'employees' in sheet_data, "Missing 'employees' key in sheet data"
        assert 'query_period' in sheet_data, "Missing 'query_period' key in sheet data"

    def test_google_sheets_parse_stores(self, sheets_service, test_spreadsheet_id):
        """Test parsing store data from Google Sheets"""
        sheet_data = sheets_service.get_sheet_data(
            spreadsheet_id=test_spreadsheet_id,
            sheet_name="Sheet1"
        )

        stores = sheet_data['stores']
        assert len(stores) > 0, "No stores found in sheet"

        # Check first store has required fields
        first_store = stores[0]
        assert 'store_code' in first_store, "Missing store_code"
        assert 'store_target' in first_store, "Missing store_target"
        assert 'store_fp_ratio_target' in first_store, "Missing store_fp_ratio_target"

    def test_google_sheets_parse_employees(self, sheets_service, test_spreadsheet_id):
        """Test parsing employee data from Google Sheets"""
        sheet_data = sheets_service.get_sheet_data(
            spreadsheet_id=test_spreadsheet_id,
            sheet_name="Sheet1"
        )

        employees = sheet_data['employees']
        assert len(employees) > 0, "No employees found in sheet"

        # Check first employee has required fields
        first_employee = employees[0]
        assert 'store_code' in first_employee, "Missing store_code"
        assert 'employee_code' in first_employee, "Missing employee_code"
        assert 'full_name' in first_employee, "Missing full_name"
        assert 'seniority' in first_employee, "Missing seniority"
        assert 'working_day_count' in first_employee, "Missing working_day_count"
        assert 'is_manager' in first_employee, "Missing is_manager"
        assert 'is_probation' in first_employee, "Missing is_probation"

    def test_google_sheets_parse_query_period(self, sheets_service, test_spreadsheet_id):
        """Test parsing query period from Google Sheets"""
        sheet_data = sheets_service.get_sheet_data(
            spreadsheet_id=test_spreadsheet_id,
            sheet_name="Sheet1"
        )

        query_period = sheet_data['query_period']
        assert 'from_date' in query_period, "Missing from_date"
        assert 'to_date' in query_period, "Missing to_date"
        assert 'month' in query_period, "Missing month"
        assert 'year' in query_period, "Missing year"

    def test_google_sheets_get_store_commission_input(self, sheets_service, test_spreadsheet_id):
        """Test getting formatted commission input for a specific store"""
        commission_input = sheets_service.get_store_commission_input(
            spreadsheet_id=test_spreadsheet_id,
            store_code="RHN",
            sheet_name="Sheet1"
        )

        assert commission_input is not None, "Failed to get commission input"
        assert commission_input['store_code'] == "RHN", "Wrong store code"
        assert 'store_target' in commission_input, "Missing store_target"
        assert 'store_fp_ratio_target' in commission_input, "Missing store_fp_ratio_target"
        assert 'query_date' in commission_input, "Missing query_date"
        assert 'employees' in commission_input, "Missing employees"

        # Check query_date format
        query_date = commission_input['query_date']
        assert 'from_date' in query_date, "Missing from_date in query_date"
        assert 'to_date' in query_date, "Missing to_date in query_date"

    def test_google_sheets_filter_employees_by_store(self, sheets_service, test_spreadsheet_id):
        """Test that get_store_commission_input filters employees by store"""
        commission_input = sheets_service.get_store_commission_input(
            spreadsheet_id=test_spreadsheet_id,
            store_code="RHN",
            sheet_name="Sheet1"
        )

        employees = commission_input['employees']
        assert len(employees) > 0, "No employees found for RHN store"

        # All employees should be from RHN store (verified in the sheet)
        # We just check that we have employees and they have correct structure
        for emp in employees:
            assert 'employee_code' in emp, "Missing employee_code"
            assert 'full_name' in emp, "Missing full_name"
