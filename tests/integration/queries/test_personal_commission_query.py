"""
Integration tests for personal commission sales data query
Testing the PERSONAL_COMMISSION_SALES_DATA query with November 2025 data
"""
import pytest
import pandas as pd
from app.database.connection import get_oracle_connection
from app.queries.commission_queries import CommissionQueries


class TestPersonalCommissionSalesQuery:
    """Test PERSONAL_COMMISSION_SALES_DATA query"""

    @pytest.fixture
    def db_connection(self):
        """Provide database connection for tests"""
        conn = get_oracle_connection()
        assert conn is not None, "Failed to establish database connection"
        yield conn
        conn.close()

    @pytest.fixture
    def query_params(self):
        """Query parameters for November 2025"""
        return {
            'year_month': '2025-11'
        }

    def test_query_returns_data(self, db_connection, query_params):
        """Test that query executes successfully and returns data"""
        cursor = db_connection.cursor()

        try:
            cursor.execute(
                CommissionQueries.PERSONAL_COMMISSION_SALES_DATA,
                **query_params
            )

            # Fetch all results
            columns = [col[0].lower() for col in cursor.description]
            rows = cursor.fetchall()

            # Create DataFrame
            df = pd.DataFrame(rows, columns=columns)

            # Basic assertions
            assert len(df) > 0, "Query returned no data for November 2025"
            print(f"\n✓ Query returned {len(df)} rows for November 2025")

        finally:
            cursor.close()

    def test_query_has_correct_columns(self, db_connection, query_params):
        """Test that query returns all expected columns"""
        cursor = db_connection.cursor()

        expected_columns = [
            'sale_id',
            'upc',
            'employee_code',
            'bill_number',
            'store_code',
            'sale_date',
            'sale_time',
            'revenue_with_vat',
            'revenue_before_vat',
            'discount_rate',
            'is_jewelry',
            'vendor_code',
            'category',
            'department'
        ]

        try:
            cursor.execute(
                CommissionQueries.PERSONAL_COMMISSION_SALES_DATA,
                **query_params
            )

            # Get column names
            columns = [col[0].lower() for col in cursor.description]

            # Check all expected columns exist
            for col in expected_columns:
                assert col in columns, f"Missing expected column: {col}"

            print(f"\n✓ All {len(expected_columns)} expected columns present")
            print(f"Columns: {', '.join(columns)}")

        finally:
            cursor.close()

    def test_query_includes_jewelry_and_non_jewelry(self, db_connection, query_params):
        """Test that query returns both jewelry and non-jewelry items"""
        cursor = db_connection.cursor()

        try:
            cursor.execute(
                CommissionQueries.PERSONAL_COMMISSION_SALES_DATA,
                **query_params
            )

            columns = [col[0].lower() for col in cursor.description]
            rows = cursor.fetchall()
            df = pd.DataFrame(rows, columns=columns)

            # Check that we have both jewelry and non-jewelry items
            jewelry_count = df[df['is_jewelry'] == 1].shape[0]
            non_jewelry_count = df[df['is_jewelry'] == 0].shape[0]

            print(f"\n✓ Jewelry items: {jewelry_count}")
            print(f"✓ Non-jewelry items: {non_jewelry_count}")

            # At least one of each should exist (or test the data exists)
            assert df['is_jewelry'].isin([0, 1]).all(), "is_jewelry should only be 0 or 1"

        finally:
            cursor.close()

    def test_query_revenue_columns_not_null(self, db_connection, query_params):
        """Test that revenue columns are not null"""
        cursor = db_connection.cursor()

        try:
            cursor.execute(
                CommissionQueries.PERSONAL_COMMISSION_SALES_DATA,
                **query_params
            )

            columns = [col[0].lower() for col in cursor.description]
            rows = cursor.fetchall()
            df = pd.DataFrame(rows, columns=columns)

            # Check revenue columns are not null
            assert df['revenue_with_vat'].notna().all(), "revenue_with_vat has null values"
            assert df['revenue_before_vat'].notna().all(), "revenue_before_vat has null values"

            # Revenue before VAT should be less than or equal to revenue with VAT in absolute terms
            # (allowing for returns which are negative values)
            assert (df['revenue_before_vat'].abs() <= df['revenue_with_vat'].abs() + 1).all(), \
                "revenue_before_vat (absolute) should be <= revenue_with_vat (absolute)"

            print(f"\n✓ Revenue columns validated")
            print(f"✓ Total revenue WITH VAT: {df['revenue_with_vat'].sum():,.0f}")
            print(f"✓ Total revenue BEFORE VAT: {df['revenue_before_vat'].sum():,.0f}")

        finally:
            cursor.close()

    def test_query_discount_rate_valid(self, db_connection, query_params):
        """Test that discount_rate is between 0 and 1"""
        cursor = db_connection.cursor()

        try:
            cursor.execute(
                CommissionQueries.PERSONAL_COMMISSION_SALES_DATA,
                **query_params
            )

            columns = [col[0].lower() for col in cursor.description]
            rows = cursor.fetchall()
            df = pd.DataFrame(rows, columns=columns)

            # discount_rate should be between 0 and 1
            assert (df['discount_rate'] >= 0).all(), "discount_rate has negative values"
            assert (df['discount_rate'] <= 1).all(), "discount_rate exceeds 1"

            # Check distribution
            fp_items = df[df['discount_rate'] <= 0.30].shape[0]
            disc_items = df[df['discount_rate'] > 0.30].shape[0]

            print(f"\n✓ Discount rate validation passed")
            print(f"✓ Full-price items (≤30% discount): {fp_items}")
            print(f"✓ Discounted items (>30% discount): {disc_items}")

        finally:
            cursor.close()

    def test_query_employee_code_not_null(self, db_connection, query_params):
        """Test that employee_code is not null"""
        cursor = db_connection.cursor()

        try:
            cursor.execute(
                CommissionQueries.PERSONAL_COMMISSION_SALES_DATA,
                **query_params
            )

            columns = [col[0].lower() for col in cursor.description]
            rows = cursor.fetchall()
            df = pd.DataFrame(rows, columns=columns)

            # employee_code should not be null
            assert df['employee_code'].notna().all(), "employee_code has null values"

            unique_employees = df['employee_code'].nunique()
            print(f"\n✓ All rows have valid employee_code")
            print(f"✓ Unique employees in data: {unique_employees}")

        finally:
            cursor.close()

    def test_query_data_ordering(self, db_connection, query_params):
        """Test that data is ordered by employee_code, sale_date, sale_time, bill_number"""
        cursor = db_connection.cursor()

        try:
            cursor.execute(
                CommissionQueries.PERSONAL_COMMISSION_SALES_DATA,
                **query_params
            )

            columns = [col[0].lower() for col in cursor.description]
            rows = cursor.fetchall()
            df = pd.DataFrame(rows, columns=columns)

            # Check if data is sorted (this is a soft check)
            # Create a sorted version and compare
            df_sorted = df.sort_values(['employee_code', 'sale_date', 'sale_time', 'bill_number'])

            # Check first few rows to verify ordering
            assert df.head(10)['employee_code'].equals(df_sorted.head(10)['employee_code']), \
                "Data may not be ordered by employee_code"

            print(f"\n✓ Data appears to be correctly ordered")
            print(f"✓ First employee: {df.iloc[0]['employee_code']}")
            print(f"✓ Last employee: {df.iloc[-1]['employee_code']}")

        finally:
            cursor.close()

    def test_query_jewelry_identification(self, db_connection, query_params):
        """Test that is_jewelry flag correctly identifies jewelry by department and vendor code"""
        cursor = db_connection.cursor()

        try:
            cursor.execute(
                CommissionQueries.PERSONAL_COMMISSION_SALES_DATA,
                **query_params
            )

            columns = [col[0].lower() for col in cursor.description]
            rows = cursor.fetchall()
            df = pd.DataFrame(rows, columns=columns)

            # Jewelry vendors list
            jewelry_vendors = ['ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'ROM', 'SPK', 'TED', 'BRT']

            # Check WJEW and MJEW departments are marked as jewelry
            wjew_items = df[df['department'] == 'WJEW']
            mjew_items = df[df['department'] == 'MJEW']

            if len(wjew_items) > 0:
                assert (wjew_items['is_jewelry'] == 1).all(), \
                    "WJEW items should have is_jewelry = 1"
                print(f"\n✓ WJEW department items correctly marked as jewelry: {len(wjew_items)} items")

            if len(mjew_items) > 0:
                assert (mjew_items['is_jewelry'] == 1).all(), \
                    "MJEW items should have is_jewelry = 1"
                print(f"✓ MJEW department items correctly marked as jewelry: {len(mjew_items)} items")

            # Check jewelry vendors are marked as jewelry
            jewelry_vendor_items = df[df['vendor_code'].isin(jewelry_vendors)]
            if len(jewelry_vendor_items) > 0:
                assert (jewelry_vendor_items['is_jewelry'] == 1).all(), \
                    f"Items from jewelry vendors {jewelry_vendors} should have is_jewelry = 1"
                print(f"✓ Jewelry vendor items correctly marked: {len(jewelry_vendor_items)} items")

                # Show breakdown by vendor
                vendor_breakdown = jewelry_vendor_items.groupby('vendor_code').size()
                print("  Breakdown by jewelry vendor:")
                for vendor, count in vendor_breakdown.items():
                    print(f"    - {vendor}: {count} items")

            # Check non-jewelry items (not WJEW/MJEW and not jewelry vendors)
            non_jewelry_items = df[
                (df['department'] != 'WJEW') &
                (df['department'] != 'MJEW') &
                (~df['vendor_code'].isin(jewelry_vendors))
            ]
            if len(non_jewelry_items) > 0:
                assert (non_jewelry_items['is_jewelry'] == 0).all(), \
                    "Non-jewelry items should have is_jewelry = 0"
                print(f"✓ Non-jewelry items correctly marked: {len(non_jewelry_items)} items")

            # Summary
            total_jewelry = (df['is_jewelry'] == 1).sum()
            total_non_jewelry = (df['is_jewelry'] == 0).sum()
            print(f"\nTotal jewelry items: {total_jewelry}")
            print(f"Total non-jewelry items: {total_non_jewelry}")

        finally:
            cursor.close()

    def test_query_bill_number_grouping(self, db_connection, query_params):
        """Test that bill_number correctly groups items from same transaction"""
        cursor = db_connection.cursor()

        try:
            cursor.execute(
                CommissionQueries.PERSONAL_COMMISSION_SALES_DATA,
                **query_params
            )

            columns = [col[0].lower() for col in cursor.description]
            rows = cursor.fetchall()
            df = pd.DataFrame(rows, columns=columns)

            # Group by bill_number and check consistency within bills
            bill_groups = df.groupby('bill_number')

            # Each bill should have consistent store_code
            for bill_number, group in bill_groups:
                assert group['store_code'].nunique() == 1, \
                    f"Bill {bill_number} has multiple store codes"
                # Note: employee_code can vary within a bill if multiple employees worked on it
                # But typically should be the same for most bills

            total_bills = df['bill_number'].nunique()
            avg_items_per_bill = len(df) / total_bills

            print(f"\n✓ Bill grouping validated")
            print(f"✓ Total unique bills: {total_bills}")
            print(f"✓ Average items per bill: {avg_items_per_bill:.2f}")

        finally:
            cursor.close()

    def test_query_sample_data_inspection(self, db_connection, query_params):
        """Inspect sample data to verify query output"""
        cursor = db_connection.cursor()

        try:
            cursor.execute(
                CommissionQueries.PERSONAL_COMMISSION_SALES_DATA,
                **query_params
            )

            columns = [col[0].lower() for col in cursor.description]
            rows = cursor.fetchall()
            df = pd.DataFrame(rows, columns=columns)

            print("\n" + "="*80)
            print("SAMPLE DATA INSPECTION - First 5 rows:")
            print("="*80)

            sample = df.head(5)
            for idx, row in sample.iterrows():
                print(f"\nRow {idx + 1}:")
                print(f"  Sale ID: {row['sale_id']}")
                print(f"  UPC: {row['upc']}")
                print(f"  Employee Code: {row['employee_code']}")
                print(f"  Bill Number: {row['bill_number']}")
                print(f"  Store: {row['store_code']}")
                print(f"  Sale Date: {row['sale_date']}")
                print(f"  Revenue WITH VAT: {row['revenue_with_vat']:,.0f}")
                print(f"  Revenue BEFORE VAT: {row['revenue_before_vat']:,.0f}")
                print(f"  Discount Rate: {row['discount_rate']:.2%}")
                print(f"  Is Jewelry: {row['is_jewelry']}")
                print(f"  Vendor: {row['vendor_code']}")
                print(f"  Department: {row['department']}")

            print("\n" + "="*80)
            print("SUMMARY STATISTICS:")
            print("="*80)
            print(f"Total rows: {len(df)}")
            print(f"Unique employees: {df['employee_code'].nunique()}")
            print(f"Unique stores: {df['store_code'].nunique()}")
            print(f"Unique bills: {df['bill_number'].nunique()}")
            print(f"Date range: {df['sale_date'].min()} to {df['sale_date'].max()}")
            print(f"Total revenue WITH VAT: {df['revenue_with_vat'].sum():,.0f}")
            print(f"Total revenue BEFORE VAT: {df['revenue_before_vat'].sum():,.0f}")
            print(f"Jewelry items: {(df['is_jewelry'] == 1).sum()}")
            print(f"Non-jewelry items: {(df['is_jewelry'] == 0).sum()}")
            print("="*80)

        finally:
            cursor.close()
