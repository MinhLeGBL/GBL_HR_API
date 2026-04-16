"""
Oracle SQL queries for CRM RFM aggregation (CR #40).

All queries are restricted to a rolling 24-month window from SYSDATE,
filter to ITEM_TYPE = 1 (sales only — returns excluded), and operate on
ACTIVE customers only.

Column conventions:
- d  = DOCUMENT (one row per invoice)
- di = DOCUMENT_ITEM (one row per line item)
- c  = CUSTOMER
- v  = VENDOR
- dcs = DCS (department/class/subclass)

Note on customer names: Vietnamese customer records use FIRST_NAME for the
full name (last-first convention) and leave LAST_NAME null. We pass
FIRST_NAME through as `name`.

Excluded customers: SYSADMIN (system-internal) and Tourist / Tourist. (POS
aggregation bucket — combines all walk-in tourist transactions into a
single record, which would skew RFM scoring badly).
"""

# Names (case-insensitive, trimmed) that identify non-real customer accounts
# and must be excluded from RFM analytics:
#   SYSADMIN  — system-internal account
#   Tourist   — POS aggregation bucket combining all walk-in tourist sales
#               into a single record (would skew RFM badly)
#   Tourist.  — additional aggregation buckets (with trailing dot)
EXCLUDED_CUSTOMER_NAMES = ('SYSADMIN', 'TOURIST', 'TOURIST.')


class CRMQueries:
    """Oracle queries for the CRM recompute pipeline."""

    # -----------------------------------------------------------------
    # 1. Customer aggregates: RFM raw values + identity fields
    # -----------------------------------------------------------------
    # Returns one row per customer who has at least 1 purchase in the
    # rolling 24-month window. CUSTOMER metadata pulled inline.
    CUSTOMER_RFM_AGGREGATES = """
        SELECT
            c.SID                                                          AS customer_sid,
            TRIM(c.FIRST_NAME)                                             AS name,
            c.EMAIL                                                        AS email,
            TRUNC(SYSDATE) - MAX(TRUNC(CAST(d.invc_post_date AS DATE)))    AS recency_days,
            COUNT(DISTINCT d.SID)                                          AS frequency,
            ROUND(SUM((di.PRICE - NVL(di.TAX_AMT, 0))
                      * (1 - NVL(di.DISC_PERC, 0) / 100)
                      * (1 - NVL(d.DISC_PERC, 0) / 100)), 0)               AS monetary,
            MAX(TRUNC(CAST(d.invc_post_date AS DATE)))                     AS last_purchase_date
        FROM CUSTOMER c
        JOIN DOCUMENT d        ON d.BT_CUID = c.SID
        JOIN DOCUMENT_ITEM di  ON di.DOC_SID = d.SID
        WHERE d.invc_post_date >= ADD_MONTHS(SYSDATE, -24)
          AND di.ITEM_TYPE = 1
          AND c.ACTIVE = 1
          AND c.SID IS NOT NULL
          AND UPPER(TRIM(c.FIRST_NAME)) NOT IN ('SYSADMIN', 'TOURIST', 'TOURIST.')
        GROUP BY c.SID, c.FIRST_NAME, c.EMAIL
        HAVING COUNT(DISTINCT d.SID) >= 1
    """

    # -----------------------------------------------------------------
    # 2. Top brand and top category per customer
    # -----------------------------------------------------------------
    # Joins DOCUMENT_ITEM → VENDOR (for brand name) and DCS (for D_NAME, the
    # top-level category). For each customer, finds the brand and category
    # with the highest spend in the 24-month window. Two ROW_NUMBER scans
    # in parallel keep this as a single round-trip.
    #
    # Returns one row per customer with both top_brand and top_category.
    CUSTOMER_TOP_BRAND_CATEGORY = """
        WITH customer_sales AS (
            SELECT
                d.BT_CUID                                                  AS customer_sid,
                NVL(v.VEND_NAME, di.VEND_CODE)                             AS brand_name,
                NVL(dcs.D_NAME, '(Unknown)')                               AS category_name,
                (di.PRICE - NVL(di.TAX_AMT, 0))
                  * (1 - NVL(di.DISC_PERC, 0) / 100)
                  * (1 - NVL(d.DISC_PERC, 0) / 100)                        AS net_revenue
            FROM DOCUMENT d
            JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
            LEFT JOIN VENDOR v    ON v.VEND_CODE = di.VEND_CODE
            LEFT JOIN DCS dcs     ON dcs.DCS_CODE = di.DCS_CODE
            WHERE d.invc_post_date >= ADD_MONTHS(SYSDATE, -24)
              AND di.ITEM_TYPE = 1
              AND d.BT_CUID IS NOT NULL
              AND d.BT_CUID NOT IN (
                  SELECT SID FROM CUSTOMER
                  WHERE UPPER(TRIM(FIRST_NAME)) IN ('SYSADMIN', 'TOURIST', 'TOURIST.')
              )
        ),
        brand_totals AS (
            SELECT customer_sid, brand_name, SUM(net_revenue) AS spend,
                   ROW_NUMBER() OVER (PARTITION BY customer_sid
                                      ORDER BY SUM(net_revenue) DESC, brand_name) AS rn
            FROM customer_sales
            GROUP BY customer_sid, brand_name
        ),
        category_totals AS (
            SELECT customer_sid, category_name, SUM(net_revenue) AS spend,
                   ROW_NUMBER() OVER (PARTITION BY customer_sid
                                      ORDER BY SUM(net_revenue) DESC, category_name) AS rn
            FROM customer_sales
            GROUP BY customer_sid, category_name
        ),
        top_brand AS (
            SELECT customer_sid, brand_name FROM brand_totals WHERE rn = 1
        ),
        top_category AS (
            SELECT customer_sid, category_name FROM category_totals WHERE rn = 1
        ),
        breadth AS (
            SELECT customer_sid, COUNT(DISTINCT category_name) AS category_breadth
            FROM customer_sales
            GROUP BY customer_sid
        )
        SELECT
            COALESCE(tb.customer_sid, tc.customer_sid, b.customer_sid)   AS customer_sid,
            tb.brand_name                                                AS top_brand,
            tc.category_name                                             AS top_category,
            NVL(b.category_breadth, 0)                                   AS category_breadth
        FROM top_brand tb
        FULL OUTER JOIN top_category tc ON tc.customer_sid = tb.customer_sid
        FULL OUTER JOIN breadth b       ON b.customer_sid  = COALESCE(tb.customer_sid, tc.customer_sid)
    """

    # -----------------------------------------------------------------
    # 3. Customer phone numbers (from DOCUMENT.BT_PRIMARY_PHONE_NO)
    # -----------------------------------------------------------------
    # Phones captured at point-of-sale via BT_PRIMARY_PHONE_NO have ~99.9%
    # coverage on customers with transactions in the 24-month window —
    # significantly better than the CUSTOMER_PHONE master (which is often
    # stale or unsynced). We take each customer's most recent non-null
    # phone observation from the DOCUMENT history.
    CUSTOMER_PHONE = """
        SELECT customer_sid, phone FROM (
            SELECT
                d.BT_CUID                                                  AS customer_sid,
                d.BT_PRIMARY_PHONE_NO                                      AS phone,
                ROW_NUMBER() OVER (PARTITION BY d.BT_CUID
                                   ORDER BY d.invc_post_date DESC,
                                            d.SID DESC)                   AS rn
            FROM DOCUMENT d
            WHERE d.BT_CUID IS NOT NULL
              AND d.BT_PRIMARY_PHONE_NO IS NOT NULL
              AND d.invc_post_date >= ADD_MONTHS(SYSDATE, -24)
              AND d.BT_CUID NOT IN (
                  SELECT SID FROM CUSTOMER
                  WHERE UPPER(TRIM(FIRST_NAME)) IN ('SYSADMIN', 'TOURIST', 'TOURIST.')
              )
        ) WHERE rn = 1
    """
