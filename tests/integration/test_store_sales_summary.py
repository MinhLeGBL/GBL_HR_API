import pytest
from datetime import datetime
from app.database.connection import get_oracle_connection
from app.queries.commission_queries import CommissionQueries


class TestStoreSalesSummary:
    """Test store sales summary for November 2025"""

    def test_store_sales_summary_nov_2025(self):
        """Test retrieving store sales summary for November 2025"""
        conn = get_oracle_connection()
        assert conn is not None, "Failed to connect to Oracle database"

        start_date = datetime(2025, 11, 1, 0, 0, 0)
        end_date = datetime(2025, 11, 30, 23, 59, 59)

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    CommissionQueries.STORE_SALES_SUMMARY,
                    {'start_date': start_date, 'end_date': end_date}
                )

                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()

                # Print results
                print(f"\n{'='*80}")
                print(f"Store Sales Summary for November 2025")
                print(f"Period: {start_date.date()} to {end_date.date()}")
                print(f"{'='*80}")
                print(f"{'STORE':<8} {'STORE NAME':<25} {'MONTH':<10} {'TRANS':>8} {'CUSTOMERS':>10} {'QTY':>8} {'SALES (VAT)':>15} {'SALES':>15}")
                print(f"{'-'*80}")

                for row in rows:
                    row_dict = dict(zip(columns, row))
                    print(f"{row_dict['STORE_CODE']:<8} "
                          f"{(row_dict['STORE_NAME'] or '')[:25]:<25} "
                          f"{(row_dict['YEAR_MONTH'] or 'N/A'):<10} "
                          f"{row_dict['TOTAL_TRANSACTIONS']:>8} "
                          f"{row_dict['UNIQUE_CUSTOMERS']:>10} "
                          f"{row_dict['TOTAL_QTY']:>8} "
                          f"{row_dict['TOTAL_SALES_VAT']:>15,.0f} "
                          f"{row_dict['TOTAL_SALES']:>15,.0f}")

                print(f"{'='*80}")
                print(f"Total stores: {len(rows)}")

                # Assert we got results
                assert len(rows) > 0, "No sales data found for November 2025"

        finally:
            conn.close()

    def test_store_sales_summary_returns_expected_columns(self):
        """Test that the query returns all expected columns"""
        conn = get_oracle_connection()
        assert conn is not None, "Failed to connect to Oracle database"

        start_date = datetime(2025, 11, 1, 0, 0, 0)
        end_date = datetime(2025, 11, 30, 23, 59, 59)

        expected_columns = [
            'STORE_CODE',
            'STORE_NAME',
            'YEAR_MONTH',
            'TOTAL_TRANSACTIONS',
            'UNIQUE_CUSTOMERS',
            'TOTAL_QTY',
            'TOTAL_SALES_VAT',
            'TOTAL_SALES'
        ]

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    CommissionQueries.STORE_SALES_SUMMARY,
                    {'start_date': start_date, 'end_date': end_date}
                )

                columns = [col[0] for col in cursor.description]

                for expected_col in expected_columns:
                    assert expected_col in columns, f"Missing column: {expected_col}"

        finally:
            conn.close()
