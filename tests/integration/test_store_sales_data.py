import pytest
from app.database.connection import get_oracle_connection
from app.queries.commission_queries import CommissionQueries


class TestStoreSalesData:
    """Test store sales data query with revenue breakdown"""

    def test_store_sales_data_rwt_nov_2025(self):
        """Test retrieving store sales data for RWT in November 2025"""
        conn = get_oracle_connection()
        assert conn is not None, "Failed to connect to Oracle database"

        start_date = '2025-11-01 00:00:00'
        end_date = '2025-11-30 23:59:59'
        store_code = 'RWT'

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    CommissionQueries.STORE_SALES_DATA,
                    {
                        'start_date': start_date,
                        'end_date': end_date,
                        'store_code': store_code
                    }
                )

                columns = [col[0] for col in cursor.description]
                row = cursor.fetchone()

                assert row is not None, f"No sales data found for store {store_code}"

                row_dict = dict(zip(columns, row))

                # Print results
                print(f"\n{'='*60}")
                print(f"Store Sales Data for {store_code} - November 2025")
                print(f"Period: {start_date} to {end_date}")
                print(f"{'='*60}")
                print(f"Store Code:              {row_dict['STORE_CODE']}")
                print(f"Store Name:              {row_dict['STORE_NAME']}")
                print(f"{'='*60}")
                print(f"Actual Revenue:          {row_dict['ACTUAL_REVENUE']:>20,.0f}")
                print(f"Full Price Revenue:      {row_dict['ACTUAL_FULL_PRICE_REVENUE']:>20,.0f}")
                print(f"Discounted Revenue:      {row_dict['ACTUAL_DISCOUNTED_REVENUE']:>20,.0f}")
                print(f"{'='*60}")

                # Verify revenue breakdown adds up
                total_check = row_dict['ACTUAL_FULL_PRICE_REVENUE'] + row_dict['ACTUAL_DISCOUNTED_REVENUE']
                print(f"Sum of FP + Discounted:  {total_check:>20,.0f}")
                print(f"Difference:              {row_dict['ACTUAL_REVENUE'] - total_check:>20,.0f}")

        finally:
            conn.close()

    def test_store_sales_data_returns_expected_columns(self):
        """Test that the query returns all expected columns"""
        conn = get_oracle_connection()
        assert conn is not None, "Failed to connect to Oracle database"

        start_date = '2025-11-01 00:00:00'
        end_date = '2025-11-30 23:59:59'
        store_code = 'RWT'

        expected_columns = [
            'STORE_CODE',
            'STORE_NAME',
            'ACTUAL_REVENUE',
            'ACTUAL_FULL_PRICE_REVENUE',
            'ACTUAL_DISCOUNTED_REVENUE'
        ]

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    CommissionQueries.STORE_SALES_DATA,
                    {
                        'start_date': start_date,
                        'end_date': end_date,
                        'store_code': store_code
                    }
                )

                columns = [col[0] for col in cursor.description]

                for expected_col in expected_columns:
                    assert expected_col in columns, f"Missing column: {expected_col}"

        finally:
            conn.close()
