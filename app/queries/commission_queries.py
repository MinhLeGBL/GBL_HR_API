"""
SQL queries for store commission calculations
"""

class CommissionQueries:
    """Container for commission-related SQL queries"""

    # Get store sales data with revenue breakdown
    STORE_SALES_DATA = """
        SELECT
            d.STORE_CODE,
            st.STORE_NAME,
            -- Total revenue (excluding tax)
            ROUND(SUM(
                CASE WHEN di.item_type = 2
                THEN di.qty * -1
                ELSE di.qty END * (di.price - di.tax_amt)
            ), 0) as ACTUAL_REVENUE,

            -- Full price revenue (items with 0% discount)
            ROUND(SUM(
                CASE
                    WHEN di.DISC_PERC = 0 AND d.DISC_PERC = 0
                    THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * (di.price - di.tax_amt)
                    ELSE 0
                END
            ), 0) as ACTUAL_FULL_PRICE_REVENUE,

            -- Discounted revenue (items with discount)
            ROUND(SUM(
                CASE
                    WHEN di.DISC_PERC > 0 OR d.DISC_PERC > 0
                    THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * (di.price - di.tax_amt)
                    ELSE 0
                END
            ), 0) as ACTUAL_DISCOUNTED_REVENUE

        FROM DOCUMENT d
        JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
        JOIN STORE st ON st.SID = d.STORE_SID
        WHERE d.STATUS = 4
          AND di.ITEM_TYPE in (1, 2)
          AND d.CREATED_DATETIME >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
          AND d.CREATED_DATETIME <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
          AND d.STORE_CODE = :store_code
        GROUP BY d.STORE_CODE, st.STORE_NAME
    """

    # Get employee sales data for a specific store
    EMPLOYEE_SALES_DATA = """
        SELECT
            -- Employee information
            COALESCE(di.EMPLOYEE1_FULL_NAME, 'UNKNOWN') as EMPLOYEE_NAME,
            di.EMPLOYEE1_FULL_NAME as EMPLOYEE_FULL_NAME,

            -- Total revenue by employee (excluding tax)
            ROUND(SUM(
                CASE WHEN di.item_type = 2
                THEN di.qty * -1
                ELSE di.qty END * (di.price - di.tax_amt)
            ), 0) as EMPLOYEE_REVENUE,

            -- Full price revenue by employee
            ROUND(SUM(
                CASE
                    WHEN di.DISC_PERC = 0 AND d.DISC_PERC = 0
                    THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * (di.price - di.tax_amt)
                    ELSE 0
                END
            ), 0) as EMPLOYEE_FP_REVENUE,

            -- Discounted revenue by employee
            ROUND(SUM(
                CASE
                    WHEN di.DISC_PERC > 0 OR d.DISC_PERC > 0
                    THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * (di.price - di.tax_amt)
                    ELSE 0
                END
            ), 0) as EMPLOYEE_DISCOUNTED_REVENUE

        FROM DOCUMENT d
        JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
        WHERE d.STATUS = 4
          AND di.ITEM_TYPE in (1, 2)
          AND d.CREATED_DATETIME >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
          AND d.CREATED_DATETIME <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
          AND d.STORE_CODE = :store_code
          AND di.EMPLOYEE1_FULL_NAME IS NOT NULL
        GROUP BY di.EMPLOYEE1_FULL_NAME
        ORDER BY EMPLOYEE_REVENUE DESC
    """

    # Get employee information including tenure
    EMPLOYEE_INFO = """
        SELECT
            EMPLOYEE_CODE,
            FULL_NAME,
            POSITION,
            STORE_CODE,
            HIRE_DATE,
            -- Calculate tenure in months
            ROUND(MONTHS_BETWEEN(SYSDATE, HIRE_DATE), 0) as TENURE_MONTHS
        FROM EMPLOYEE
        WHERE STORE_CODE = :store_code
          AND STATUS = 'ACTIVE'
        ORDER BY FULL_NAME
    """
