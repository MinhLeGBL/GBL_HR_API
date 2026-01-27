"""
Test revenue reconciliation between store-level and employee-level data
"""
import pytest
from app.database.connection import get_oracle_connection


class TestRevenueReconciliation:
    """Test that store revenue matches sum of employee revenues"""

    @pytest.fixture
    def test_params(self):
        """Test parameters for November 2025 RHN store"""
        return {
            'store_code': 'RHN',
            'start_date': '2025-11-01 00:00:00',
            'end_date': '2025-11-30 23:59:59',
            'year_month': '2025-11'
        }

    def test_fp_revenue_reconciliation_excluding_jewelry(self, test_params):
        """Test that sum of employee FP revenue equals store FP revenue (both excluding WJEW/MJEW)"""

        conn = get_oracle_connection()
        assert conn is not None, "Failed to connect to database"

        try:
            cursor = conn.cursor()

            # Query 1: Get store FP revenue excluding WJEW and MJEW departments
            store_query = """
                SELECT
                    d.STORE_CODE,
                    -- Store FP revenue (excluding WJEW and MJEW, only same-store sales)
                    ROUND(SUM(
                        CASE
                            WHEN (1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) <= 0.3
                                 AND dep.D_LONG_NAME NOT IN ('WJEW', 'MJEW')
                                 AND d.STORE_CODE = :store_code
                            THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * (di.price * (1 - d.DISC_PERC/100)) - di.tax_amt
                            ELSE 0
                        END
                    ), 0) as STORE_FP_REVENUE
                FROM DOCUMENT d
                JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
                JOIN INVN_SBS_ITEM i ON i.SID = di.INVN_SBS_ITEM_SID
                JOIN DCS dep ON dep.SID = i.DCS_SID
                WHERE d.STATUS = 4
                  AND d.receipt_type in (0, 1)
                  AND di.ITEM_TYPE in (1, 2)
                  AND d.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
                  AND d.invc_post_date <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
                  AND d.STORE_CODE = :store_code
                  AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
                      SELECT 1 FROM DOCUMENT d2
                      JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
                      WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
                        AND TO_CHAR(d2.CREATED_DATETIME, 'YYYY-MM') = :year_month
                  )))
                GROUP BY d.STORE_CODE
            """

            cursor.execute(store_query, test_params)
            store_result = cursor.fetchone()

            if store_result:
                store_code = store_result[0]
                store_fp_revenue = store_result[1]
            else:
                store_fp_revenue = 0

            print("\n" + "="*120)
            print("REVENUE RECONCILIATION TEST - EXCLUDING WJEW/MJEW DEPARTMENTS")
            print("="*120)
            print(f"\n[STORE-LEVEL DATA]")
            print(f"  Store Code:                 {test_params['store_code']}")
            print(f"  Store FP Revenue:           {store_fp_revenue:>20,.0f} VND")
            print(f"  (Excluding WJEW/MJEW, same-store sales only)")

            # Query 2: Get sum of all employee FP revenues excluding WJEW and MJEW departments
            employee_query = """
                SELECT
                    COUNT(DISTINCT e.SID) as EMPLOYEE_COUNT,
                    -- Sum of all employee FP revenues (excluding WJEW and MJEW, RHN employees can count RHN and RWP sales)
                    ROUND(SUM(
                        CASE
                            WHEN (1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) <= 0.3
                                 AND dep.D_LONG_NAME NOT IN ('WJEW', 'MJEW')
                                 AND (
                                     (e.STORE_CODE IN ('RHN', 'RWP') AND d.STORE_CODE IN ('RHN', 'RWP'))
                                     OR
                                     (e.STORE_CODE NOT IN ('RHN', 'RWP') AND d.STORE_CODE = e.STORE_CODE)
                                 )
                            THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * (di.price * (1 - d.DISC_PERC/100)) - di.tax_amt
                            ELSE 0
                        END
                    ), 0) as TOTAL_EMPLOYEE_FP_REVENUE
                FROM DOCUMENT d
                JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
                JOIN INVN_SBS_ITEM i ON i.SID = di.INVN_SBS_ITEM_SID
                JOIN DCS dep ON dep.SID = i.DCS_SID
                JOIN EMPLOYEE_LIST_V e ON di.EMPLOYEE1_SID = e.SID
                WHERE d.STATUS = 4
                  AND d.receipt_type in (0, 1)
                  AND di.ITEM_TYPE in (1, 2)
                  AND d.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
                  AND d.invc_post_date <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
                  AND (
                      (e.STORE_CODE IN ('RHN', 'RWP') AND d.STORE_CODE IN ('RHN', 'RWP'))
                      OR
                      (e.STORE_CODE NOT IN ('RHN', 'RWP') AND d.STORE_CODE = e.STORE_CODE)
                  )
                  AND (
                      (:store_code IN ('RHN', 'RWP') AND e.STORE_CODE IN ('RHN', 'RWP'))
                      OR
                      (:store_code NOT IN ('RHN', 'RWP') AND e.STORE_CODE = :store_code)
                  )
                  AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
                      SELECT 1 FROM DOCUMENT d2
                      JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
                      WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
                        AND TO_CHAR(d2.CREATED_DATETIME, 'YYYY-MM') = :year_month
                  )))
            """

            cursor.execute(employee_query, test_params)
            employee_result = cursor.fetchone()

            if employee_result:
                employee_count = employee_result[0]
                total_employee_fp_revenue = employee_result[1]
            else:
                employee_count = 0
                total_employee_fp_revenue = 0

            print(f"\n[EMPLOYEE-LEVEL DATA]")
            print(f"  Employee Count:             {employee_count}")
            print(f"  Sum of Employee FP Rev:     {total_employee_fp_revenue:>20,.0f} VND")
            print(f"  (Excluding WJEW/MJEW, RHN/RWP employees can count both stores)")

            # Calculate difference
            difference = store_fp_revenue - total_employee_fp_revenue
            difference_pct = (difference / store_fp_revenue * 100) if store_fp_revenue > 0 else 0

            print(f"\n[RECONCILIATION]")
            print(f"  Difference:                 {difference:>20,.0f} VND")
            print(f"  Difference %:               {difference_pct:>20.2f}%")

            if abs(difference) < 1000:
                print(f"  Status:                     ✓ MATCHED (within 1,000 VND tolerance)")
            elif abs(difference_pct) < 0.1:
                print(f"  Status:                     ✓ MATCHED (within 0.1% tolerance)")
            else:
                print(f"  Status:                     ✗ MISMATCH")

            print("\n" + "="*120)
            print("END OF RECONCILIATION TEST")
            print("="*120 + "\n")

            cursor.close()

            # Assert that they match within a small tolerance (0.1% or 1000 VND)
            assert abs(difference) < 1000 or abs(difference_pct) < 0.1, \
                f"Revenue mismatch: Store FP Revenue ({store_fp_revenue:,.0f}) != Sum of Employee FP Revenue ({total_employee_fp_revenue:,.0f}), Difference: {difference:,.0f} VND ({difference_pct:.2f}%)"

        finally:
            conn.close()

    def test_total_revenue_reconciliation(self, test_params):
        """Test that sum of employee total revenue matches store total revenue"""

        conn = get_oracle_connection()
        assert conn is not None, "Failed to connect to database"

        try:
            cursor = conn.cursor()

            # Query 1: Get store total revenue (all departments, same-store sales only)
            store_query = """
                SELECT
                    d.STORE_CODE,
                    ROUND(SUM(
                        CASE WHEN di.item_type = 2
                        THEN di.qty * -1
                        ELSE di.qty END * (di.price * (1 - d.DISC_PERC/100)) - di.tax_amt
                    ), 0) as STORE_TOTAL_REVENUE
                FROM DOCUMENT d
                JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
                WHERE d.STATUS = 4
                  AND d.receipt_type in (0, 1)
                  AND di.ITEM_TYPE in (1, 2)
                  AND d.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
                  AND d.invc_post_date <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
                  AND d.STORE_CODE = :store_code
                  AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
                      SELECT 1 FROM DOCUMENT d2
                      JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
                      WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
                        AND TO_CHAR(d2.CREATED_DATETIME, 'YYYY-MM') = :year_month
                  )))
                GROUP BY d.STORE_CODE
            """

            cursor.execute(store_query, test_params)
            store_result = cursor.fetchone()

            if store_result:
                store_total_revenue = store_result[1]
            else:
                store_total_revenue = 0

            print("\n" + "="*120)
            print("TOTAL REVENUE RECONCILIATION TEST - ALL DEPARTMENTS")
            print("="*120)
            print(f"\n[STORE-LEVEL DATA]")
            print(f"  Store Code:                 {test_params['store_code']}")
            print(f"  Store Total Revenue:        {store_total_revenue:>20,.0f} VND")

            # Query 2: Get sum of all employee total revenues
            employee_query = """
                SELECT
                    COUNT(DISTINCT e.SID) as EMPLOYEE_COUNT,
                    ROUND(SUM(
                        CASE WHEN di.item_type = 2
                        THEN di.qty * -1
                        ELSE di.qty END * (di.price * (1 - d.DISC_PERC/100)) - di.tax_amt
                    ), 0) as TOTAL_EMPLOYEE_REVENUE
                FROM DOCUMENT d
                JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
                JOIN EMPLOYEE_LIST_V e ON di.EMPLOYEE1_SID = e.SID
                WHERE d.STATUS = 4
                  AND d.receipt_type in (0, 1)
                  AND di.ITEM_TYPE in (1, 2)
                  AND d.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
                  AND d.invc_post_date <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
                  AND (
                      (e.STORE_CODE IN ('RHN', 'RWP') AND d.STORE_CODE IN ('RHN', 'RWP'))
                      OR
                      (e.STORE_CODE NOT IN ('RHN', 'RWP') AND d.STORE_CODE = e.STORE_CODE)
                  )
                  AND (
                      (:store_code IN ('RHN', 'RWP') AND e.STORE_CODE IN ('RHN', 'RWP'))
                      OR
                      (:store_code NOT IN ('RHN', 'RWP') AND e.STORE_CODE = :store_code)
                  )
                  AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
                      SELECT 1 FROM DOCUMENT d2
                      JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
                      WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
                        AND TO_CHAR(d2.CREATED_DATETIME, 'YYYY-MM') = :year_month
                  )))
            """

            cursor.execute(employee_query, test_params)
            employee_result = cursor.fetchone()

            if employee_result:
                employee_count = employee_result[0]
                total_employee_revenue = employee_result[1]
            else:
                employee_count = 0
                total_employee_revenue = 0

            print(f"\n[EMPLOYEE-LEVEL DATA]")
            print(f"  Employee Count:             {employee_count}")
            print(f"  Sum of Employee Rev:        {total_employee_revenue:>20,.0f} VND")

            # Calculate difference
            difference = store_total_revenue - total_employee_revenue
            difference_pct = (difference / store_total_revenue * 100) if store_total_revenue > 0 else 0

            print(f"\n[RECONCILIATION]")
            print(f"  Difference:                 {difference:>20,.0f} VND")
            print(f"  Difference %:               {difference_pct:>20.2f}%")

            if abs(difference) < 1000:
                print(f"  Status:                     ✓ MATCHED (within 1,000 VND tolerance)")
            elif abs(difference_pct) < 0.1:
                print(f"  Status:                     ✓ MATCHED (within 0.1% tolerance)")
            else:
                print(f"  Status:                     ✗ MISMATCH")

            print("\n" + "="*120)
            print("END OF RECONCILIATION TEST")
            print("="*120 + "\n")

            cursor.close()

            # Assert that they match within a small tolerance (0.1% or 1000 VND)
            assert abs(difference) < 1000 or abs(difference_pct) < 0.1, \
                f"Revenue mismatch: Store Total Revenue ({store_total_revenue:,.0f}) != Sum of Employee Revenue ({total_employee_revenue:,.0f}), Difference: {difference:,.0f} VND ({difference_pct:.2f}%)"

        finally:
            conn.close()
