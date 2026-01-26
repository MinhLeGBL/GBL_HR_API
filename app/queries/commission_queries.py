"""
SQL queries for store commission calculations
"""

class CommissionQueries:
    """Container for commission-related SQL queries"""

    DETAILED_SALES_REPORT = """
        SELECT
            -- ITEM INFO
            i.SID                                                                    as ITEM_SID,
            TO_CHAR(i.UPC)                                                           as UPC,
            --i.UDF5_STRING                                                          as SEASON,
            --di.VEND_CODE                                                           as VENDOR_CODE,
            --v.VEND_NAME                                                            as VENDOR_NAME,
            --dep.D_LONG_NAME                                                        as DEPARTMENT,
            --SUBSTR(i.DESCRIPTION2, INSTR(i.DESCRIPTION2, '-', -1) + 1)             as CATEGORY,
            --di.DESCRIPTION1                                                        as ITEM_NAME,
            --di.ATTRIBUTE                                                           as ATTRIBUTE,
            --di.ITEM_SIZE                                                           as ITEM_SIZE,
            ROUND(di.ORIG_PRICE, 0)                                                  as FULL_PRICE_VAT,
            ROUND(di.ORIG_PRICE - di.ORIG_TAX_AMT, 0)                                as FULL_PRICE,
            --i.TEXT1                                                                as DESCRIPTION,

            -- BILL INFO
            d.STORE_CODE || d.DOC_NO                                                 as STORE_BILL,
            d.CREATED_DATETIME                                                       as BILL_DATE,
            TO_CHAR(d.invc_post_date, 'YYYY')                                        as YEAR,
            TO_CHAR(d.invc_post_date, 'MM')                                          as MONTH,
            TO_CHAR(d.invc_post_date, 'WW')                                          as WEEK,
            d.STORE_CODE                                                             as STORE_CODE,
            d.DOC_NO                                                                 as BILL_NO,
            CASE d.RECEIPT_TYPE
                WHEN 0 THEN 'SALE'
                WHEN 1 THEN 'RETURN'
                ELSE 'DEPOSIT' END                                                   as RECEIPT_TYPE,
            di.sid                                                                   as DOC_ITEM_SID,
            di.RETURNED_ITEM_INVOICE_SID                                             as REF_ITEM_SID,
            ((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END))          AS QTY_SOLD,
            d.TENDER_NAME                                                            as PAYMENT_FORM,
            ROUND(((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) *
                   di.price))                                                        AS PAYMENT_VAT,
            ROUND(((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) *
                   (di.price - di.tax_amt)))                                         AS PAYMENT,
            d.DISC_PERC                                                              as BILL_DISC_PERC,
            di.DISC_PERC                                                             as ITEM_DISC_PERC,

            -- DISCOUNT CALCULATIONS
            ROUND((1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) * 100, 1) as TOTAL_DISC_PERC,
            ROUND((((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * (di.orig_price)) -
                   ((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * (di.price))),
                  0)                                                                 as DISC_AMT_VAT,
            ROUND(((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) *
                   (di.orig_price - di.ORIG_TAX_AMT)) *
                  (1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)))          as DISC_AMT,

            -- TAX CALCULATIONS
            ROUND(((CASE WHEN di.ITEM_TYPE = 2 THEN di.QTY * -1 ELSE di.QTY END) * (di.TAX_AMT)),
                  0)                                                                 as TAX_AMT,
            ROUND(((CASE WHEN di.ITEM_TYPE = 2 THEN di.QTY * -1 ELSE di.QTY END) * (di.ORIG_TAX_AMT)),
                  0)                                                                 as FULL_PRICE_TAX_AMT,

            -- EMPLOYEE INFO
            d.CASHIER_FULL_NAME                                                      as CASHIER,
            di.EMPLOYEE1_FULL_NAME                                                   as ASSOCIATE_1,
            di.EMPLOYEE2_FULL_NAME                                                   as ASSOCIATE_2,
            di.EMPLOYEE3_FULL_NAME                                                   as ASSOCIATE_3,
            di.EMPLOYEE4_FULL_NAME                                                   as ASSOCIATE_4,

            -- CUSTOMER INFO
            d.BT_CUID                                                                as CUSTOMER_SID,
            c.EMAIL                                                                  as CUSTOMER_EMAIL,
            c.INFO1                                                                  as CUSTOMER_TYPE,
            TRUNC(c.UDF1_DATE)                                                       as CUS_BIRTHDAY,
            c.FIRST_SALE_DATE                                                        as CUS_FIRST_SALE_DATE,
            c.LAST_SALE_DATE                                                         as CUS_LAST_SALE_DATE

        FROM DOCUMENT d
                 JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
                 JOIN SUBSIDIARY s ON d.SBS_NO = s.SBS_NO AND d.SBS_NO = s.SBS_NO
                 JOIN STORE st ON st.SID = d.STORE_SID
                 JOIN INVN_SBS_ITEM i ON i.SID = di.INVN_SBS_ITEM_SID
                 JOIN DCS dep on dep.SID = i.DCS_SID
                 JOIN CUSTOMER c ON c.SID = d.BT_CUID
                 JOIN VENDOR v ON v.VEND_CODE = di.VEND_CODE
        WHERE d.STATUS != 2
          AND d.receipt_type in (0, 1)
          AND di.ITEM_TYPE in (1, 2)
          AND d.invc_post_date >= :start_date
          AND d.invc_post_date <= :end_date
        #   -- Exclude returns that reference sales from outside the query period
        #   AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
        #       SELECT 1 FROM DOCUMENT d2
        #       JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
        #       WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
        #         AND d2.invc_post_date >= :start_date
        #         AND d2.invc_post_date <= :end_date
        #   )))
    """
    
    # Store sales summary query
    STORE_SALES_SUMMARY = """
        SELECT
            d.STORE_CODE,
            st.STORE_NAME,
            TO_CHAR(d.invc_post_date, 'YYYY-MM') as YEAR_MONTH,
            COUNT(DISTINCT d.DOC_NO) as TOTAL_TRANSACTIONS,
            COUNT(DISTINCT d.BT_CUID) as UNIQUE_CUSTOMERS,
            SUM(CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) as TOTAL_QTY,
            ROUND(SUM(CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END * di.price * (1 - d.DISC_PERC/100)), 0) as TOTAL_SALES_VAT,
            ROUND(SUM(CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END * (di.price * (1 - d.DISC_PERC/100)) - di.tax_amt), 0) as TOTAL_SALES

        FROM DOCUMENT d
                 JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
                 JOIN STORE st ON st.SID = d.STORE_SID
        WHERE d.STATUS = 4
          AND d.receipt_type in (0, 1)
          AND di.ITEM_TYPE in (1, 2)
          AND d.invc_post_date >= :start_date
          AND d.invc_post_date <= :end_date
          -- Exclude returns that reference sales from outside the query period
          AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
              SELECT 1 FROM DOCUMENT d2
              JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
              WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
                AND d2.invc_post_date >= :start_date
                AND d2.invc_post_date <= :end_date
          )))
        GROUP BY d.STORE_CODE, st.STORE_NAME, TO_CHAR(d.invc_post_date, 'YYYY-MM')
        ORDER BY d.STORE_CODE, YEAR_MONTH
    """

    # Get store sales data with revenue breakdown
    STORE_SALES_DATA = """
        SELECT
            d.STORE_CODE,
            st.STORE_NAME,
            -- Total revenue
            ROUND(SUM(
                CASE WHEN di.item_type = 2
                THEN di.qty * -1
                ELSE di.qty END * di.price
            ), 0) as ACTUAL_REVENUE,

            -- Full price revenue (items with less or equal than 30% discount)
            ROUND(SUM(
                CASE
                    WHEN (1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) <= 0.3
                    THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * di.price
                    ELSE 0
                END
            ), 0) as ACTUAL_FULL_PRICE_REVENUE,

            -- Discounted revenue (items with discount)
            ROUND(SUM(
                CASE
                    WHEN (1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) > 0.3
                    THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * di.price
                    ELSE 0
                END
            ), 0) as ACTUAL_DISCOUNTED_REVENUE

        FROM DOCUMENT d
        JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
        JOIN STORE st ON st.SID = d.STORE_SID
        WHERE d.STATUS = 4
          AND d.receipt_type in (0, 1)
          AND di.ITEM_TYPE in (1, 2)
          AND d.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
          AND d.invc_post_date <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
          AND d.STORE_CODE = :store_code
          -- Exclude returns that reference sales from outside the query period
          AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
              SELECT 1 FROM DOCUMENT d2
              JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
              WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
                AND d2.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
                AND d2.invc_post_date <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
          )))
        GROUP BY d.STORE_CODE, st.STORE_NAME
    """

    # Get employee sales data for a specific store
    EMPLOYEE_SALES_DATA = """
        SELECT
            e.UDF4_STRING as EMPLOYEE_CODE,
            e.SID as EMPLOYEE_SID,
            e.STORE_CODE as EMPLOYEE_STORE_CODE,
            e.FULL_NAME as EMPLOYEE_FULL_NAME,

            -- Total revenue by employee (excluding tax, after discount)
            ROUND(SUM(
                CASE WHEN di.item_type = 2
                THEN di.qty * -1
                ELSE di.qty END * (di.price * (1 - d.DISC_PERC/100)) - di.tax_amt
            ), 0) as EMPLOYEE_REVENUE,

            -- Full price revenue by employee (items with <= 30% total discount)
            ROUND(SUM(
                CASE
                    WHEN (1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) <= 0.3
                    THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * (di.price * (1 - d.DISC_PERC/100)) - di.tax_amt
                    ELSE 0
                END
            ), 0) as EMPLOYEE_FP_REVENUE,

            -- Discounted revenue by employee (items with > 30% total discount)
            ROUND(SUM(
                CASE
                    WHEN (1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) > 0.3
                    THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * (di.price * (1 - d.DISC_PERC/100)) - di.tax_amt
                    ELSE 0
                END
            ), 0) as EMPLOYEE_DISCOUNTED_REVENUE

        FROM DOCUMENT d
        JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
        JOIN EMPLOYEE_LIST_V e ON di.EMPLOYEE1_SID = e.SID
        WHERE d.STATUS = 4
          AND d.receipt_type in (0, 1)
          AND di.ITEM_TYPE in (1, 2)
          AND d.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
          AND d.invc_post_date <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
          AND e.STORE_CODE = :store_code
          -- Exclude returns that reference sales from outside the query period
          AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
              SELECT 1 FROM DOCUMENT d2
              JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
              WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
                AND d2.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
                AND d2.invc_post_date <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
          )))
        GROUP BY e.UDF4_STRING, e.SID, e.STORE_CODE, e.FULL_NAME
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

    # Personal commission sales data query
    # Returns sales data filtered for personal commission calculation
    PERSONAL_COMMISSION_SALES_DATA = """
        SELECT
            d.DOC_NO || '_' || di.SID                                             as sale_id,
            di.EMPLOYEE1_FULL_NAME                                                as employee_id,
            d.STORE_CODE                                                          as store_id,
            TRUNC(d.CREATED_DATETIME)                                             as sale_date,
            TO_CHAR(d.CREATED_DATETIME, 'HH24:MI:SS')                             as sale_time,
            d.CREATED_DATETIME                                                    as sale_datetime,
            ROUND((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) *
                  (di.price - di.tax_amt), 0)                                     as revenue_before_vat,
            ROUND((1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) * 100, 2) / 100
                                                                                  as discount_rate,
            dep.D_LONG_NAME                                                       as department
        FROM DOCUMENT d
        JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
        JOIN INVN_SBS_ITEM i ON i.SID = di.INVN_SBS_ITEM_SID
        JOIN DCS dep ON dep.SID = i.DCS_SID
        WHERE 1 = 1
          AND d.STATUS = 4
          AND di.ITEM_TYPE in (1, 2)
          AND di.EMPLOYEE1_FULL_NAME IS NOT NULL
          AND UPPER(dep.D_LONG_NAME) NOT LIKE '%JEWELRY%'
          AND UPPER(dep.D_LONG_NAME) NOT LIKE '%เครื่องประดับ%'
          AND TO_CHAR(d.CREATED_DATETIME, 'YYYY-MM') = :year_month
          -- Exclude returns that reference sales from outside the query period
          AND (di.ITEM_TYPE = 1 OR (di.ITEM_TYPE = 2 AND EXISTS (
              SELECT 1 FROM DOCUMENT d2
              JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
              WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
                AND TO_CHAR(d2.CREATED_DATETIME, 'YYYY-MM') = :year_month
          )))
        ORDER BY di.EMPLOYEE1_FULL_NAME, d.CREATED_DATETIME
    """
