"""
Oracle SQL for the Reports module (CR #78 — live sale comparison).

Read-only aggregation over Retail Pro `DOCUMENT` / `DOCUMENT_ITEM`. Mirrors
the CRM revenue conventions so numbers reconcile across features:
- one row per line item in `DOCUMENT_ITEM`, joined to its `DOCUMENT`;
- `ITEM_TYPE = 1` (sales only — returns/exchanges excluded);
- net line revenue = `(PRICE - TAX) × (1 - item_disc) × (1 - doc_disc)`.

Column conventions: d = DOCUMENT, di = DOCUMENT_ITEM, c = CUSTOMER.

Customer buckets (see CRM `EXCLUDED_CUSTOMER_NAMES`):
- SYSADMIN            — system-internal account; NOT a real sale. Excluded
                        from every figure.
- TOURIST / TOURIST.  — POS aggregation bucket for walk-in tourist sales
                        (one synthetic record for thousands of anonymous
                        walk-ins). Real revenue, so it stays in
                        `total_revenue`, but it is NOT an identifiable
                        individual, so it is excluded from the new/returning
                        customer classification (its revenue folds into the
                        "returning" bucket via `returning = total - new`).
- NULL BT_CUID        — anonymous sale, no customer record. Same treatment as
                        TOURIST: counted in revenue, not in customer counts.

Date binds are Python `date` objects. `to` is inclusive of the whole day, so
the service passes `to_exclusive = to + 1 day` and the range is
`invc_post_date >= :from_date AND invc_post_date < :to_exclusive`.
"""

# Net line revenue expression (shared by both queries).
_NET_LINE = (
    "(di.PRICE - NVL(di.TAX_AMT, 0)) "
    "* (1 - NVL(di.DISC_PERC, 0) / 100) "
    "* (1 - NVL(d.DISC_PERC, 0) / 100)"
)


class ReportsQueries:
    """Oracle queries for the sale-comparison report."""

    # -----------------------------------------------------------------
    # 1. Period totals: revenue + distinct bill count.
    # -----------------------------------------------------------------
    # All in-range sales EXCEPT the SYSADMIN system account. Walk-in
    # (TOURIST) and anonymous (NULL BT_CUID) sales ARE included — they are
    # real revenue.
    PERIOD_TOTALS = f"""
        SELECT
            ROUND(NVL(SUM({_NET_LINE}), 0), 0)   AS total_revenue,
            COUNT(DISTINCT d.SID)                AS bill_count
        FROM DOCUMENT d
        JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
        WHERE di.ITEM_TYPE = 1
          AND d.invc_post_date >= :from_date
          AND d.invc_post_date <  :to_exclusive
          AND (
              d.BT_CUID IS NULL
              OR d.BT_CUID NOT IN (
                  SELECT SID FROM CUSTOMER
                  WHERE UPPER(TRIM(FIRST_NAME)) = 'SYSADMIN'
              )
          )
    """

    # -----------------------------------------------------------------
    # 2. New vs returning split (identifiable customers only).
    # -----------------------------------------------------------------
    # `in_range`  — per identifiable customer, their net revenue in [from, to].
    # `first_buy` — each of those customers' FIRST-EVER sale date across all
    #               history (ITEM_TYPE = 1). A customer is "new" iff that first
    #               date falls in the period; since every in-range customer has
    #               a purchase <= to, first_dt <= to always holds, so the test
    #               reduces to `first_dt >= from` (new) vs `< from` (returning).
    # `new_customer_revenue` is returned; the service derives
    # `returning_customer_revenue = total_revenue - new_customer_revenue`, which
    # folds walk-in / anonymous revenue into "returning" and keeps the invariant
    # `new + returning == total_revenue` exact.
    NEW_VS_RETURNING = f"""
        WITH in_range AS (
            SELECT d.BT_CUID AS cust,
                   SUM({_NET_LINE}) AS rev
            FROM DOCUMENT d
            JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
            WHERE di.ITEM_TYPE = 1
              AND d.invc_post_date >= :from_date
              AND d.invc_post_date <  :to_exclusive
              AND d.BT_CUID IS NOT NULL
              AND d.BT_CUID NOT IN (
                  SELECT SID FROM CUSTOMER
                  WHERE UPPER(TRIM(FIRST_NAME)) IN ('SYSADMIN', 'TOURIST', 'TOURIST.')
              )
            GROUP BY d.BT_CUID
        ),
        first_buy AS (
            SELECT d.BT_CUID AS cust,
                   MIN(TRUNC(CAST(d.invc_post_date AS DATE))) AS first_dt
            FROM DOCUMENT d
            JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
            WHERE di.ITEM_TYPE = 1
              AND d.BT_CUID IN (SELECT cust FROM in_range)
            GROUP BY d.BT_CUID
        )
        SELECT
            NVL(SUM(CASE WHEN fb.first_dt >= :from_date THEN 1 ELSE 0 END), 0)
                AS new_customers,
            NVL(SUM(CASE WHEN fb.first_dt <  :from_date THEN 1 ELSE 0 END), 0)
                AS returning_customers,
            ROUND(NVL(SUM(CASE WHEN fb.first_dt >= :from_date THEN ir.rev ELSE 0 END), 0), 0)
                AS new_customer_revenue
        FROM in_range ir
        JOIN first_buy fb ON fb.cust = ir.cust
    """
