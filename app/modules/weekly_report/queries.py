"""Oracle queries for the weekly sales report.

Revenue formulas are taken verbatim from `app/modules/reports/queries.py`
(CR #78-#85) so this report reconciles with the live-comparison feature:
  net line   = (di.PRICE - di.TAX_AMT) * di.QTY * (1 - d.DISC_PERC/100)
  gross line = (di.ORIG_PRICE - di.ORIG_TAX_AMT) * di.QTY
`di.PRICE` is ALREADY net of the item discount — re-applying `di.DISC_PERC`
double-discounts (the CR #81 bug). `di.TAX_AMT` is the real per-line tax (data
carries mixed 8%/10% VAT), so the `price / 1.1` shortcut used by commission is
deliberately not used here.

Deliberate divergences from the `reports` module:

1. Returns are netted **within the period itself**, not over that module's flat
   30-day tail. A tail would be asymmetric here: the current window ends at or
   near today and so has little or no tail available, while the comparison
   window would get its full 30 days — biasing every delta.
2. `d.STATUS = 4 AND d.RECEIPT_TYPE IN (0, 1)` are applied, matching the sibling
   report scripts. Verified 2026-09-09: 100% of 2026 YTD sale lines are already
   STATUS=4 / RECEIPT_TYPE=0, so this is currently a no-op and the two
   implementations agree on live data.
"""

_NET_LINE = (
    "(di.PRICE - NVL(di.TAX_AMT, 0)) "
    "* NVL(di.QTY, 0) "
    "* (1 - NVL(d.DISC_PERC, 0) / 100)"
)

_GROSS_LINE = (
    "(NVL(di.ORIG_PRICE, di.PRICE) - NVL(di.ORIG_TAX_AMT, 0)) "
    "* NVL(di.QTY, 0)"
)

# Posted retail documents only, and never the system-internal account.
_DOC_FILTER = """
      AND d.STATUS = 4
      AND d.RECEIPT_TYPE IN (0, 1)
      AND (
          d.BT_CUID IS NULL
          OR d.BT_CUID NOT IN (
              SELECT SID FROM CUSTOMER
              WHERE UPPER(TRIM(FIRST_NAME)) = 'SYSADMIN'
          )
      )
"""

# Day x store x department grain. Sale and return components are kept apart so
# the caller can net returns over whatever window it wants.
QUERY_BY_DEPT = f"""
    SELECT
        TRUNC(CAST(d.invc_post_date AS DATE))                     AS day,
        s.STORE_CODE                                              AS store_code,
        s.STORE_NAME                                              AS store_name,
        dcs.D_LONG_NAME                                           AS department,

        -- Deliberately NOT rounded at this grain: the caller sums these day x
        -- store x dept rows into the period windows, and rounding per group
        -- would drift by a few units against a single-shot query over the same
        -- range. Rounding happens once, at presentation.
        NVL(SUM(CASE WHEN di.ITEM_TYPE = 1
                     THEN {_NET_LINE} ELSE 0 END), 0)             AS sale_net,
        NVL(SUM(CASE WHEN di.ITEM_TYPE = 2
                     THEN {_NET_LINE} ELSE 0 END), 0)             AS return_net,
        NVL(SUM(CASE WHEN di.ITEM_TYPE = 1
                     THEN {_GROSS_LINE} ELSE 0 END), 0)           AS sale_gross,

        NVL(SUM(CASE WHEN di.ITEM_TYPE = 1
                     THEN NVL(di.QTY, 0) ELSE 0 END), 0)          AS qty_sold,
        NVL(SUM(CASE WHEN di.ITEM_TYPE = 2
                     THEN NVL(di.QTY, 0) ELSE 0 END), 0)          AS qty_returned,

        COUNT(DISTINCT CASE WHEN di.ITEM_TYPE = 1 THEN d.SID END) AS dept_bills
    FROM DOCUMENT d
    JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
    JOIN STORE s          ON s.SID = d.STORE_SID
    LEFT JOIN DCS dcs     ON dcs.DCS_CODE = di.DCS_CODE
    WHERE di.ITEM_TYPE IN (1, 2)
      AND d.invc_post_date >= :from_date
      AND d.invc_post_date <  :to_exclusive
      {_DOC_FILTER}
    GROUP BY TRUNC(CAST(d.invc_post_date AS DATE)),
             s.STORE_CODE, s.STORE_NAME, dcs.D_LONG_NAME
"""

# Day x store grain, document-level. A bill belongs to exactly one (day, store),
# so these counts sum correctly across days -- unlike the per-department counts.
QUERY_BY_STORE = f"""
    SELECT
        TRUNC(CAST(d.invc_post_date AS DATE))                     AS day,
        s.STORE_CODE                                              AS store_code,
        COUNT(DISTINCT CASE WHEN di.ITEM_TYPE = 1 THEN d.SID END) AS bills
    FROM DOCUMENT d
    JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
    JOIN STORE s          ON s.SID = d.STORE_SID
    WHERE di.ITEM_TYPE IN (1, 2)
      AND d.invc_post_date >= :from_date
      AND d.invc_post_date <  :to_exclusive
      {_DOC_FILTER}
    GROUP BY TRUNC(CAST(d.invc_post_date AS DATE)), s.STORE_CODE
"""
