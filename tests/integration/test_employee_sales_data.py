import pytest
from app.database.connection import get_oracle_connection
from app.queries.commission_queries import CommissionQueries


class TestEmployeeSalesData:
    """Test employee sales data query with revenue breakdown"""

    def test_employee_sales_data_rwt_nov_2025(self):
        """Test retrieving employee sales data for RWT in November 2025"""
        conn = get_oracle_connection()
        assert conn is not None, "Failed to connect to Oracle database"

        start_date = '2025-11-01 00:00:00'
        end_date = '2025-11-30 23:59:59'
        store_code = 'RWT'

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    CommissionQueries.EMPLOYEE_SALES_DATA,
                    {
                        'start_date': start_date,
                        'end_date': end_date,
                        'store_code': store_code
                    }
                )

                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()

                assert rows is not None and len(rows) > 0, f"No employee sales data found for store {store_code}"

                # Print results
                print(f"\n{'='*100}")
                print(f"Employee Sales Data for {store_code} - November 2025")
                print(f"Period: {start_date} to {end_date}")
                print(f"{'='*100}")
                print(f"{'Employee Code':<15} {'Employee SID':<15} {'Employee Name':<30} {'Total Revenue':>15} {'FP Revenue':>15} {'Disc Revenue':>15}")
                print(f"{'-'*100}")

                total_revenue = 0
                total_fp_revenue = 0
                total_disc_revenue = 0

                for row in rows:
                    row_dict = dict(zip(columns, row))

                    emp_code = row_dict['EMPLOYEE_CODE'] or 'N/A'
                    emp_sid = str(row_dict['EMPLOYEE_SID']) if row_dict['EMPLOYEE_SID'] else 'N/A'
                    emp_name = row_dict['EMPLOYEE_FULL_NAME'] or 'UNKNOWN'
                    revenue = row_dict['EMPLOYEE_REVENUE'] or 0
                    fp_revenue = row_dict['EMPLOYEE_FP_REVENUE'] or 0
                    disc_revenue = row_dict['EMPLOYEE_DISCOUNTED_REVENUE'] or 0

                    print(f"{emp_code:<15} {emp_sid:<15} {emp_name:<30} {revenue:>15,.0f} {fp_revenue:>15,.0f} {disc_revenue:>15,.0f}")

                    total_revenue += revenue
                    total_fp_revenue += fp_revenue
                    total_disc_revenue += disc_revenue

                print(f"{'-'*100}")
                print(f"{'TOTAL':<15} {'':<15} {'':<30} {total_revenue:>15,.0f} {total_fp_revenue:>15,.0f} {total_disc_revenue:>15,.0f}")
                print(f"{'='*100}")
                print(f"\nTotal Employees: {len(rows)}")
                print(f"Sum of FP + Discounted: {total_fp_revenue + total_disc_revenue:>15,.0f}")
                print(f"Difference from Total:  {total_revenue - (total_fp_revenue + total_disc_revenue):>15,.0f}")
                print(f"{'='*100}\n")

        finally:
            conn.close()

    def test_employee_sales_data_returns_expected_columns(self):
        """Test that the query returns all expected columns"""
        conn = get_oracle_connection()
        assert conn is not None, "Failed to connect to Oracle database"

        start_date = '2025-11-01 00:00:00'
        end_date = '2025-11-30 23:59:59'
        store_code = 'RWT'

        expected_columns = [
            'EMPLOYEE_CODE',
            'EMPLOYEE_SID',
            'EMPLOYEE_STORE_CODE',
            'EMPLOYEE_FULL_NAME',
            'EMPLOYEE_REVENUE',
            'EMPLOYEE_FP_REVENUE',
            'EMPLOYEE_DISCOUNTED_REVENUE'
        ]

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    CommissionQueries.EMPLOYEE_SALES_DATA,
                    {
                        'start_date': start_date,
                        'end_date': end_date,
                        'store_code': store_code
                    }
                )

                columns = [col[0] for col in cursor.description]

                for expected_col in expected_columns:
                    assert expected_col in columns, f"Missing column: {expected_col}"

                print(f"\nAll expected columns present: {', '.join(columns)}")

        finally:
            conn.close()
