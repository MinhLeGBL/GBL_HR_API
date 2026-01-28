"""
SQL queries for retail sales reports and analytics
"""

class RetailSalesQueries:
    """Container for retail sales-related SQL queries"""
    
    # Complex retail sales transaction report
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
        WHERE 1 = 1
          --AND d.RECEIPT_TYPE in (0,1)
          AND di.ITEM_TYPE in (1, 2)
          AND d.STATUS = 4
          AND d.CREATED_DATETIME >= :start_date
          AND d.CREATED_DATETIME <= :end_date
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
            ROUND(SUM(CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END * di.price), 0) as TOTAL_SALES_VAT,
            ROUND(SUM(CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END * (di.price - di.tax_amt)), 0) as TOTAL_SALES
        FROM DOCUMENT d
                 JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
                 JOIN STORE st ON st.SID = d.STORE_SID
        WHERE d.STATUS = 4
          AND di.ITEM_TYPE in (1, 2)
          AND d.CREATED_DATETIME >= :start_date
          AND d.CREATED_DATETIME <= :end_date
        GROUP BY d.STORE_CODE, st.STORE_NAME, TO_CHAR(d.invc_post_date, 'YYYY-MM')
        ORDER BY d.STORE_CODE, YEAR_MONTH
    """
    
    # Customer analysis query
    CUSTOMER_ANALYSIS = """
        SELECT 
            c.SID as CUSTOMER_SID,
            c.EMAIL as CUSTOMER_EMAIL,
            c.INFO1 as CUSTOMER_TYPE,
            COUNT(DISTINCT d.DOC_NO) as TOTAL_PURCHASES,
            SUM(CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) as TOTAL_ITEMS,
            ROUND(SUM(CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END * (di.price - di.tax_amt)), 0) as TOTAL_SPENT,
            ROUND(AVG(CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END * (di.price - di.tax_amt)), 0) as AVG_TRANSACTION,
            MIN(d.CREATED_DATETIME) as FIRST_PURCHASE,
            MAX(d.CREATED_DATETIME) as LAST_PURCHASE
        FROM DOCUMENT d
                 JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
                 JOIN CUSTOMER c ON c.SID = d.BT_CUID
        WHERE d.STATUS = 4
          AND di.ITEM_TYPE in (1, 2)
          AND d.CREATED_DATETIME >= :start_date
          AND d.CREATED_DATETIME <= :end_date
        GROUP BY c.SID, c.EMAIL, c.INFO1
        HAVING COUNT(DISTINCT d.DOC_NO) > 0
        ORDER BY TOTAL_SPENT DESC
    """