"""
Test to verify RHN and RWP employees are both counted for RHN store
"""
import pytest
from app.database.connection import get_oracle_connection


class TestRHNRWPEmployeeCheck:
    """Test that both RHN and RWP employees are counted for revenue reconciliation"""

    @pytest.fixture
    def test_params(self):
        """Test parameters for November 2025"""
        return {
            'start_date': '2025-11-01 00:00:00',
            'end_date': '2025-11-30 23:59:59',
            'year_month': '2025-11'
        }

    def test_rhn_rwp_employee_count(self, test_params):
        """Check employee counts for RHN and RWP stores"""

        conn = get_oracle_connection()
        assert conn is not None, "Failed to connect to database"

        try:
            cursor = conn.cursor()

            print("\n" + "="*120)
            print("RHN/RWP EMPLOYEE COUNT AND REVENUE CHECK")
            print("="*120)

            # Query 1: Get RHN employees only
            rhn_only_query = """
                SELECT
                    'RHN' as QUERY_TYPE,
                    COUNT(DISTINCT e.SID) as EMPLOYEE_COUNT,
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
                    ), 0) as TOTAL_FP_REVENUE
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
                  AND e.STORE_CODE = 'RHN'
                  AND (
                      (e.STORE_CODE IN ('RHN', 'RWP') AND d.STORE_CODE IN ('RHN', 'RWP'))
                      OR
                      (e.STORE_CODE NOT IN ('RHN', 'RWP') AND d.STORE_CODE = e.STORE_CODE)
                  )
                  AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
                      SELECT 1 FROM DOCUMENT d2
                      JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
                      WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
                        AND TO_CHAR(d2.CREATED_DATETIME, 'YYYY-MM') = :year_month
                  )))
            """

            cursor.execute(rhn_only_query, test_params)
            rhn_result = cursor.fetchone()

            # Query 2: Get RWP employees only
            rwp_only_query = """
                SELECT
                    'RWP' as QUERY_TYPE,
                    COUNT(DISTINCT e.SID) as EMPLOYEE_COUNT,
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
                    ), 0) as TOTAL_FP_REVENUE
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
                  AND e.STORE_CODE = 'RWP'
                  AND (
                      (e.STORE_CODE IN ('RHN', 'RWP') AND d.STORE_CODE IN ('RHN', 'RWP'))
                      OR
                      (e.STORE_CODE NOT IN ('RHN', 'RWP') AND d.STORE_CODE = e.STORE_CODE)
                  )
                  AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
                      SELECT 1 FROM DOCUMENT d2
                      JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
                      WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
                        AND TO_CHAR(d2.CREATED_DATETIME, 'YYYY-MM') = :year_month
                  )))
            """

            cursor.execute(rwp_only_query, test_params)
            rwp_result = cursor.fetchone()

            # Query 3: Get BOTH RHN and RWP employees combined
            combined_query = """
                SELECT
                    'RHN+RWP' as QUERY_TYPE,
                    COUNT(DISTINCT e.SID) as EMPLOYEE_COUNT,
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
                    ), 0) as TOTAL_FP_REVENUE
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
                  AND e.STORE_CODE IN ('RHN', 'RWP')
                  AND (
                      (e.STORE_CODE IN ('RHN', 'RWP') AND d.STORE_CODE IN ('RHN', 'RWP'))
                      OR
                      (e.STORE_CODE NOT IN ('RHN', 'RWP') AND d.STORE_CODE = e.STORE_CODE)
                  )
                  AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
                      SELECT 1 FROM DOCUMENT d2
                      JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
                      WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
                        AND TO_CHAR(d2.CREATED_DATETIME, 'YYYY-MM') = :year_month
                  )))
            """

            cursor.execute(combined_query, test_params)
            combined_result = cursor.fetchone()

            print(f"\n[EMPLOYEE COUNTS WITH FP REVENUE (Excluding WJEW/MJEW)]")
            print(f"{'Store':<15} {'Employee Count':>20} {'FP Revenue':>25}")
            print("─"*120)

            if rhn_result:
                print(f"{'RHN Only':<15} {rhn_result[1]:>20} {rhn_result[2]:>25,.0f} VND")

            if rwp_result:
                print(f"{'RWP Only':<15} {rwp_result[1]:>20} {rwp_result[2]:>25,.0f} VND")

            if combined_result:
                print(f"{'RHN + RWP':<15} {combined_result[1]:>20} {combined_result[2]:>25,.0f} VND")

            # Query 4: Get store-level FP revenue for RHN only
            store_query = """
                SELECT
                    ROUND(SUM(
                        CASE
                            WHEN (1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) <= 0.3
                                 AND dep.D_LONG_NAME NOT IN ('WJEW', 'MJEW')
                                 AND d.STORE_CODE = 'RHN'
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
                  AND d.STORE_CODE = 'RHN'
                  AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
                      SELECT 1 FROM DOCUMENT d2
                      JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
                      WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
                        AND TO_CHAR(d2.CREATED_DATETIME, 'YYYY-MM') = :year_month
                  )))
            """

            cursor.execute(store_query, test_params)
            store_result = cursor.fetchone()

            print(f"\n[STORE-LEVEL FP REVENUE (Excluding WJEW/MJEW)]")
            if store_result:
                store_fp_revenue = store_result[0]
                print(f"  RHN Store FP Revenue:       {store_fp_revenue:>25,.0f} VND")

                # Compare with combined employee revenue
                if combined_result:
                    combined_emp_revenue = combined_result[2]
                    difference = store_fp_revenue - combined_emp_revenue
                    difference_pct = (difference / store_fp_revenue * 100) if store_fp_revenue > 0 else 0

                    print(f"\n[RECONCILIATION: RHN Store vs RHN+RWP Employees]")
                    print(f"  Store FP Revenue:           {store_fp_revenue:>25,.0f} VND")
                    print(f"  RHN+RWP Employee FP Rev:    {combined_emp_revenue:>25,.0f} VND")
                    print(f"  Difference:                 {difference:>25,.0f} VND ({difference_pct:.2f}%)")

            print("\n" + "="*120)
            print("END OF RHN/RWP EMPLOYEE CHECK")
            print("="*120 + "\n")

            cursor.close()

            # Assert that using both RHN and RWP employees gives better reconciliation
            assert combined_result[1] > rhn_result[1], \
                "Combined RHN+RWP should have more employees than RHN only"

        finally:
            conn.close()
