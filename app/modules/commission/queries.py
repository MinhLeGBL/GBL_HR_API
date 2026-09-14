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
          -- All sales and returns within the query period count
          AND d.invc_post_date >= :start_date
          AND d.invc_post_date <= :end_date
          -- FUTURE: To also include next-month returns for same-period sales, replace
          -- the date filter above with:
          -- AND (
          --     (d.invc_post_date >= :start_date AND d.invc_post_date <= :end_date)
          --     OR
          --     (di.ITEM_TYPE = 2
          --      AND d.invc_post_date > :end_date
          --      AND d.invc_post_date <= :next_month_end
          --      AND EXISTS (
          --          SELECT 1 FROM DOCUMENT d2
          --          JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
          --          WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
          --            AND d2.invc_post_date >= :start_date
          --            AND d2.invc_post_date <= :end_date
          --     ))
          -- )
        GROUP BY d.STORE_CODE, st.STORE_NAME, TO_CHAR(d.invc_post_date, 'YYYY-MM')
        ORDER BY d.STORE_CODE, YEAR_MONTH
    """

    # Get store sales data with revenue breakdown (location-based, CR #21)
    #
    # Purpose: Sum classified sales at a physical store register for store achievement/eligibility.
    # Unlike ALL_SALES_DATA (which returns row-level), this pre-aggregates into 4 vendor-based buckets.
    #
    # Design decisions:
    # - No INVN_SBS_ITEM/DCS JOINs: sysadmin and non-standard entries may lack inventory
    #   records. INNER JOIN would silently drop them. We don't need department info here.
    # - Revenue is broken into 4 vendor-based buckets: full_price, markdown, jewelry, suitcase.
    #   ACTUAL_REVENUE is computed in Python as the sum of these 4 (7-type filtered).
    #   Items with NULL VEND_CODE are excluded (Oracle NOT IN returns NULL for NULLs).
    # - ACTUAL_FULL_PRICE_REVENUE: non-jewelry, non-suitcase, discount <= 30%.
    #   Uses di.VEND_CODE (on DOCUMENT_ITEM) for vendor classification — no JOIN needed.
    # - Hand carry items (UPC-based at employee level) are counted by their vendor code
    #   at store level — they're already included in one of the vendor categories.
    #
    # NOTE: Kept for backward compatibility. New code should use ALL_SALES_DATA + service-layer
    # classification via _compute_revenue_by_type() for consistent 7-type breakdown.
    STORE_SALES_DATA = """
        SELECT
            d.STORE_CODE,
            st.STORE_NAME,

            -- Full price revenue: non-jewelry, non-suitcase items with discount <= 30%
            ROUND(SUM(
                CASE
                    WHEN (1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) <= 0.3
                         AND di.VEND_CODE NOT IN ('VHN', 'ROM', 'ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'SPK', 'TED', 'BRT')
                         AND di.VEND_CODE NOT IN ('TVL', 'TIT')
                    THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * di.price
                    ELSE 0
                END
            ), 0) as ACTUAL_FULL_PRICE_REVENUE,

            -- Discounted revenue: non-jewelry, non-suitcase items with discount > 30%
            ROUND(SUM(
                CASE
                    WHEN (1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) > 0.3
                         AND di.VEND_CODE NOT IN ('VHN', 'ROM', 'ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'SPK', 'TED', 'BRT')
                         AND di.VEND_CODE NOT IN ('TVL', 'TIT')
                    THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * di.price
                    ELSE 0
                END
            ), 0) as ACTUAL_DISCOUNTED_REVENUE,

            -- Jewelry revenue: all jewelry vendor items (VHN, ROM, ATS, VIS, LUI, NAN, NAK, SPK, TED, BRT)
            ROUND(SUM(
                CASE
                    WHEN di.VEND_CODE IN ('VHN', 'ROM', 'ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'SPK', 'TED', 'BRT')
                    THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * di.price
                    ELSE 0
                END
            ), 0) as ACTUAL_JEWELRY_REVENUE,

            -- Suitcase revenue: suitcase vendor items (TVL, TIT)
            ROUND(SUM(
                CASE
                    WHEN di.VEND_CODE IN ('TVL', 'TIT')
                    THEN (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) * di.price
                    ELSE 0
                END
            ), 0) as ACTUAL_SUITCASE_REVENUE

        FROM DOCUMENT d
        JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
        JOIN STORE st ON st.SID = d.STORE_SID
        WHERE d.STATUS = 4
          AND d.receipt_type in (0, 1)
          AND di.ITEM_TYPE in (1, 2)
          AND d.STORE_CODE = :store_code
          -- All sales and returns within the query period count
          AND d.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
          AND d.invc_post_date <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
          -- FUTURE: To also include next-month returns for same-period sales, replace
          -- the date filter above with:
          -- AND (
          --     (d.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
          --      AND d.invc_post_date <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS'))
          --     OR
          --     (di.ITEM_TYPE = 2
          --      AND d.invc_post_date > TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
          --      AND d.invc_post_date <= TO_DATE(:next_month_end, 'YYYY-MM-DD HH24:MI:SS')
          --      AND EXISTS (
          --          SELECT 1 FROM DOCUMENT d2
          --          JOIN DOCUMENT_ITEM di2 ON d2.SID = di2.DOC_SID
          --          WHERE di2.SID = di.RETURNED_ITEM_INVOICE_SID
          --            AND d2.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
          --            AND d2.invc_post_date <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
          --     ))
          -- )
        GROUP BY d.STORE_CODE, st.STORE_NAME
    """

    # Unified sales data query — single source of truth for ALL transaction line items
    # in a period. Returns every row with both location data and employee data (when available).
    #
    # Replaces STORE_SALES_DETAIL, EMPLOYEE_SALES_DATA, and PERSONAL_COMMISSION_SALES_DATA.
    #
    # The service layer filters this single DataFrame for different views:
    #   - Store view:    filter by doc_store_code → _compute_revenue_by_type()
    #   - Employee view: filter by employee_username + store logic → FP/discounted/7-type
    #   - Personal commission: filter by employee_username
    #   - Hand carry:    filter by UPC
    #
    # JOINs:
    #   - LEFT JOIN EMPLOYEE: keeps non-employee transactions (SYSADMIN, walk-ins)
    #   - LEFT JOIN STORE:    keeps employees with NULL BASE_STORE_SID (SYSADMIN)
    #   - LEFT JOIN INVN_SBS_ITEM: keeps items without inventory records
    #   - LEFT JOIN DCS:      keeps items without department classification
    ALL_SALES_DATA = """
        SELECT
            di.SID                                                                as sale_id,
            di.SCAN_UPC                                                           as upc,
            d.DOC_NO                                                              as bill_number,
            d.SID                                                                 as bill_sid,
            d.STORE_CODE                                                          as doc_store_code,
            TRUNC(d.invc_post_date)                                               as sale_date,
            TO_CHAR(d.CREATED_DATETIME, 'HH24:MI:SS')                             as sale_time,
            d.BT_CUID                                                             as customer_sid,
            emp.SID                                                               as employee_sid,
            emp.USER_NAME                                                         as employee_username,
            s.STORE_CODE                                                          as store_code,
            di.VEND_CODE                                                          as vendor_code,
            CASE
                WHEN di.VEND_CODE IN ('VHN', 'ROM', 'ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'SPK', 'TED', 'BRT')
                THEN 1
                ELSE 0
            END                                                                   as is_jewelry,
            SUBSTR(i.DESCRIPTION2, INSTR(i.DESCRIPTION2, '-', -1) + 1)            as category,
            dep.D_LONG_NAME                                                       as department,
            ROUND((1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) * 100, 2) / 100
                                                                                  as discount_rate,
            ROUND((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) *
                  di.price, 0)                                                    as revenue_with_vat,
            -- CR #20: flat 10% VAT (di.price / 1.1) instead of actual rate (di.price - di.tax_amt)
            ROUND((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) *
                  di.price / 1.1, 0)                                              as revenue_before_vat,
            -- Net units sold (returns negative). Needed for per-piece commissions
            -- like suitcase/Travelite (500k/cái) where a line may carry qty > 1.
            (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END)          as qty_sold
        FROM DOCUMENT d
        JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
        LEFT JOIN EMPLOYEE emp ON di.EMPLOYEE1_SID = emp.SID
        LEFT JOIN STORE s ON emp.BASE_STORE_SID = s.SID
        LEFT JOIN INVN_SBS_ITEM i ON i.SID = di.INVN_SBS_ITEM_SID
        LEFT JOIN DCS dep ON dep.SID = i.DCS_SID
        WHERE d.STATUS = 4
          AND d.receipt_type IN (0, 1)
          AND di.ITEM_TYPE IN (1, 2)
          AND d.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD HH24:MI:SS')
          AND d.invc_post_date <= TO_DATE(:end_date, 'YYYY-MM-DD HH24:MI:SS')
    """

    # Customer Charge tender ledger for AR payable detection (CR #59).
    # Pulls every Charge tender event (positive sales + negative payments)
    # for any customer who has at least one Charge bill in the target month,
    # ordered chronologically for FIFO replay.
    #
    # Replay rule (REF_SALE_SID → FIFO) is in CommissionRepository — see
    # `get_unpaid_bill_amounts`. NOTES_LOSTDOC parsing intentionally not used
    # (free-text, unreliable — see CR #59 Phase A research).
    ORACLE_UNPAID_BILLS_BY_MONTH = """
        WITH target_month_customers AS (
            SELECT DISTINCT d.bt_cuid AS customer_sid
            FROM rps.document d
            JOIN rps.tender t ON t.doc_sid = d.sid
                              AND t.tender_name = 'Charge'
                              AND t.amount > 0
            WHERE d.status = 4
              AND TO_CHAR(d.invc_post_date, 'YYYY-MM') = :target_month
        )
        SELECT
            d.bt_cuid                                AS customer_sid,
            d.sid                                    AS doc_sid,
            d.doc_no                                 AS doc_no,
            t.amount                                 AS charge_amount,
            d.invc_post_date                         AS post_date,
            d.ref_sale_sid                           AS ref_sale_sid,
            d.sale_total_amt                         AS sale_total_amt,
            TO_CHAR(d.invc_post_date, 'YYYY-MM')     AS post_month
        FROM rps.document d
        JOIN rps.tender t ON t.doc_sid = d.sid
                          AND t.tender_name = 'Charge'
        JOIN target_month_customers c ON c.customer_sid = d.bt_cuid
        WHERE d.status = 4
          AND d.invc_post_date < TO_DATE(:period_end_exclusive, 'YYYY-MM-DD HH24:MI:SS')
        ORDER BY d.bt_cuid, d.invc_post_date, d.doc_no
    """

    # Get employee information including tenure
    # UPDATED: Now uses EMPLOYEE table with CUSTOMER and STORE joins instead of EMPLOYEE_LIST_V
    EMPLOYEE_INFO = """
        SELECT
            cust.UDF4_STRING as EMPLOYEE_CODE,
            cust.FIRST_NAME as FULL_NAME,
            s.STORE_CODE
        FROM EMPLOYEE emp
        JOIN CUSTOMER cust ON emp.CUST_SID = cust.SID
        JOIN STORE s ON emp.BASE_STORE_SID = s.SID
        WHERE s.STORE_CODE = :store_code
          AND emp.USER_NAME IS NOT NULL
        ORDER BY cust.UDF4_STRING
    """
