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
    # Uses SYSDATE for the daily recompute. For backfill, use
    # as_of_query() which replaces SYSDATE with a bind variable.
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
          AND d.invc_post_date <= SYSDATE
          AND di.ITEM_TYPE = 1
          AND c.ACTIVE = 1
          AND c.SID IS NOT NULL
          AND UPPER(TRIM(c.FIRST_NAME)) NOT IN ('SYSADMIN', 'TOURIST', 'TOURIST.')
        GROUP BY c.SID, c.FIRST_NAME, c.EMAIL
        HAVING COUNT(DISTINCT d.SID) >= 1
    """

    # -----------------------------------------------------------------
    # 2. Top brand and top category per customer (CR #42)
    # -----------------------------------------------------------------
    # Uses composite affinity score instead of pure monetary ranking:
    #   affinity = 0.6 × norm_frequency + 0.4 × norm_monetary
    # where norm_frequency = brand_purchases / total_purchases,
    #       norm_monetary  = brand_spend / total_spend.
    #
    # Category uses "D_NAME - UDF8_STRING" composite (e.g., "WOMEN - DRESS",
    # "MEN - TSHIRT") via INVN_SBS_EXTEND.UDF8_STRING (100% coverage).
    # category_breadth counts distinct composite category values.
    #
    # Returns one row per customer with top_brand, top_category, category_breadth.
    CUSTOMER_TOP_BRAND_CATEGORY = """
        WITH customer_sales AS (
            SELECT
                d.BT_CUID                                                  AS customer_sid,
                NVL(v.VEND_NAME, di.VEND_CODE)                             AS brand_name,
                NVL(dcs.C_NAME, '(Unknown)')
                  || ' - '
                  || NVL(ext.UDF8_STRING, '(Unknown)')                     AS category_name,
                d.SID                                                      AS doc_sid,
                (di.PRICE - NVL(di.TAX_AMT, 0))
                  * (1 - NVL(di.DISC_PERC, 0) / 100)
                  * (1 - NVL(d.DISC_PERC, 0) / 100)                        AS net_revenue
            FROM DOCUMENT d
            JOIN DOCUMENT_ITEM di       ON di.DOC_SID = d.SID
            LEFT JOIN VENDOR v          ON v.VEND_CODE = di.VEND_CODE
            LEFT JOIN DCS dcs           ON dcs.DCS_CODE = di.DCS_CODE
            LEFT JOIN INVN_SBS_ITEM isi ON isi.SID = di.INVN_SBS_ITEM_SID
            LEFT JOIN INVN_SBS_EXTEND ext ON ext.INVN_SBS_ITEM_SID = isi.SID
            WHERE d.invc_post_date >= ADD_MONTHS(SYSDATE, -24)
              AND d.invc_post_date <= SYSDATE
              AND di.ITEM_TYPE = 1
              AND d.BT_CUID IS NOT NULL
              AND d.BT_CUID NOT IN (
                  SELECT SID FROM CUSTOMER
                  WHERE UPPER(TRIM(FIRST_NAME)) IN ('SYSADMIN', 'TOURIST', 'TOURIST.')
              )
        ),
        customer_totals AS (
            SELECT customer_sid,
                   COUNT(DISTINCT doc_sid)   AS total_purchases,
                   SUM(net_revenue)          AS total_spend
            FROM customer_sales
            GROUP BY customer_sid
        ),
        brand_affinity AS (
            SELECT
                cs.customer_sid,
                cs.brand_name,
                0.6 * (COUNT(DISTINCT cs.doc_sid) / ct.total_purchases)
              + 0.4 * (SUM(cs.net_revenue) / NULLIF(ct.total_spend, 0))    AS affinity,
                ROW_NUMBER() OVER (PARTITION BY cs.customer_sid
                                   ORDER BY
                                     0.6 * (COUNT(DISTINCT cs.doc_sid) / ct.total_purchases)
                                   + 0.4 * (SUM(cs.net_revenue) / NULLIF(ct.total_spend, 0)) DESC,
                                   cs.brand_name)                          AS rn
            FROM customer_sales cs
            JOIN customer_totals ct ON ct.customer_sid = cs.customer_sid
            GROUP BY cs.customer_sid, cs.brand_name, ct.total_purchases, ct.total_spend
        ),
        category_affinity AS (
            SELECT
                cs.customer_sid,
                cs.category_name,
                0.6 * (COUNT(DISTINCT cs.doc_sid) / ct.total_purchases)
              + 0.4 * (SUM(cs.net_revenue) / NULLIF(ct.total_spend, 0))    AS affinity,
                ROW_NUMBER() OVER (PARTITION BY cs.customer_sid
                                   ORDER BY
                                     0.6 * (COUNT(DISTINCT cs.doc_sid) / ct.total_purchases)
                                   + 0.4 * (SUM(cs.net_revenue) / NULLIF(ct.total_spend, 0)) DESC,
                                   cs.category_name)                       AS rn
            FROM customer_sales cs
            JOIN customer_totals ct ON ct.customer_sid = cs.customer_sid
            GROUP BY cs.customer_sid, cs.category_name, ct.total_purchases, ct.total_spend
        ),
        top_brand AS (
            SELECT customer_sid, brand_name FROM brand_affinity WHERE rn = 1
        ),
        top_category AS (
            SELECT customer_sid, category_name FROM category_affinity WHERE rn = 1
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
              AND d.invc_post_date <= SYSDATE
              AND d.BT_CUID NOT IN (
                  SELECT SID FROM CUSTOMER
                  WHERE UPPER(TRIM(FIRST_NAME)) IN ('SYSADMIN', 'TOURIST', 'TOURIST.')
              )
        ) WHERE rn = 1
    """

    # -----------------------------------------------------------------
    # 4. Per-customer per-brand aggregates (CR #48 → revised by CR #49)
    # -----------------------------------------------------------------
    # One row per (customer, brand). Each row carries that customer's
    # **own** values for the brand; the pooler then averages across all
    # customers in a segment (nested per-customer averaging, CR #49).
    # Fields:
    #   items                 — total quantity of items of this brand the
    #                           customer bought across all their transactions
    #                           (SUM(QTY))
    #   revenue               — net revenue (after item + doc disc) across
    #                           those line items
    #   customer_recency_days — days since this customer's most recent
    #                           transaction containing the brand
    #                           (today − MAX(invc_post_date))
    PRODUCT_BRAND_AGGREGATES = """
        SELECT
            d.BT_CUID                                                      AS customer_sid,
            NVL(v.VEND_NAME, di.VEND_CODE)                                 AS group_name,
            SUM(NVL(di.QTY, 0))                                            AS items,
            SUM((di.PRICE - NVL(di.TAX_AMT, 0))
                * (1 - NVL(di.DISC_PERC, 0) / 100)
                * (1 - NVL(d.DISC_PERC, 0) / 100))                         AS revenue,
            TRUNC(SYSDATE)
              - MAX(TRUNC(CAST(d.invc_post_date AS DATE)))                 AS customer_recency_days
        FROM DOCUMENT d
        JOIN DOCUMENT_ITEM di       ON di.DOC_SID = d.SID
        LEFT JOIN VENDOR v          ON v.VEND_CODE = di.VEND_CODE
        WHERE d.invc_post_date >= ADD_MONTHS(SYSDATE, -24)
          AND d.invc_post_date <= SYSDATE
          AND di.ITEM_TYPE = 1
          AND d.BT_CUID IS NOT NULL
          AND d.BT_CUID NOT IN (
              SELECT SID FROM CUSTOMER
              WHERE UPPER(TRIM(FIRST_NAME)) IN ('SYSADMIN', 'TOURIST', 'TOURIST.')
          )
        GROUP BY d.BT_CUID, NVL(v.VEND_NAME, di.VEND_CODE)
    """

    # -----------------------------------------------------------------
    # 5. Per-customer per-category aggregates (CR #48 → revised by CR #49)
    # -----------------------------------------------------------------
    # Same shape as PRODUCT_BRAND_AGGREGATES but grouped on the C_NAME +
    # UDF8_STRING composite category used elsewhere in the CRM module
    # (CR #42). Categories with neither value are emitted as
    # "(Unknown) - (Unknown)" so they aggregate together.
    PRODUCT_CATEGORY_AGGREGATES = """
        SELECT
            d.BT_CUID                                                      AS customer_sid,
            NVL(dcs.C_NAME, '(Unknown)')
              || ' - '
              || NVL(ext.UDF8_STRING, '(Unknown)')                         AS group_name,
            SUM(NVL(di.QTY, 0))                                            AS items,
            SUM((di.PRICE - NVL(di.TAX_AMT, 0))
                * (1 - NVL(di.DISC_PERC, 0) / 100)
                * (1 - NVL(d.DISC_PERC, 0) / 100))                         AS revenue,
            TRUNC(SYSDATE)
              - MAX(TRUNC(CAST(d.invc_post_date AS DATE)))                 AS customer_recency_days
        FROM DOCUMENT d
        JOIN DOCUMENT_ITEM di       ON di.DOC_SID = d.SID
        LEFT JOIN DCS dcs           ON dcs.DCS_CODE = di.DCS_CODE
        LEFT JOIN INVN_SBS_ITEM isi ON isi.SID = di.INVN_SBS_ITEM_SID
        LEFT JOIN INVN_SBS_EXTEND ext ON ext.INVN_SBS_ITEM_SID = isi.SID
        WHERE d.invc_post_date >= ADD_MONTHS(SYSDATE, -24)
          AND d.invc_post_date <= SYSDATE
          AND di.ITEM_TYPE = 1
          AND d.BT_CUID IS NOT NULL
          AND d.BT_CUID NOT IN (
              SELECT SID FROM CUSTOMER
              WHERE UPPER(TRIM(FIRST_NAME)) IN ('SYSADMIN', 'TOURIST', 'TOURIST.')
          )
        GROUP BY d.BT_CUID,
                 NVL(dcs.C_NAME, '(Unknown)')
                   || ' - '
                   || NVL(ext.UDF8_STRING, '(Unknown)')
    """

    @classmethod
    def as_of_query(cls, query_name: str) -> str:
        """
        Return a query with SYSDATE replaced by :as_of_date bind variable.

        Used by the backfill script to compute RFM "as of" a historical date.
        The caller must pass {'as_of_date': datetime} when executing.

        Args:
            query_name: One of 'CUSTOMER_RFM_AGGREGATES',
                        'CUSTOMER_TOP_BRAND_CATEGORY', 'CUSTOMER_PHONE',
                        'PRODUCT_BRAND_AGGREGATES', 'PRODUCT_CATEGORY_AGGREGATES'.

        Returns:
            Modified SQL string with :as_of_date in place of SYSDATE.
        """
        sql = getattr(cls, query_name)
        return sql.replace('SYSDATE', ':as_of_date')
