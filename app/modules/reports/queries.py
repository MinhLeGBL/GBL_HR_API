"""
Oracle SQL for the Reports module (CR #78 — live sale comparison).

Read-only aggregation over Retail Pro `DOCUMENT` / `DOCUMENT_ITEM`:
- one row per line item in `DOCUMENT_ITEM`, joined to its `DOCUMENT`;
- sale lines are `ITEM_TYPE = 1`, bounded to the period `[from, to]`;
- `total_revenue` also nets out return lines (`ITEM_TYPE = 2`) that post within
  a flat 30-day tail after the period — window `[from, to + 30 days]` (v1.4.0);
- net line revenue (ex-tax) = `(PRICE - TAX) × QTY × (1 - doc_disc)`.

Revenue formula — verified against live data (see `_NET_LINE`):
- `di.PRICE` is the actual UNIT selling price ALREADY net of the item-level
  discount: `ORIG_PRICE × (1 - di.DISC_PERC/100) == di.PRICE` holds on every
  line. Re-applying `di.DISC_PERC` therefore DOUBLE-discounts — the bug fixed
  after CR #81 surfaced Diamond (RWD) reading ~⅓ of actual.
- `di.TAX_AMT` is the item's REAL per-unit tax (data carries mixed 8% / 10%
  VAT); subtracted directly for ex-tax revenue. We deliberately do NOT use the
  fixed `price / 1.1` (10%) shortcut — that rate exists only for the employee
  commission calc and would over-strip tax on the 8%-VAT lines.
- the DOCUMENT-level discount `d.DISC_PERC` is NOT baked into `di.PRICE`
  (confirmed on doc-discount rows), so it is applied here.
- `di.QTY` matters: `PRICE` is a unit price, so the line total is `PRICE × QTY`.

NOTE: the CRM module's `monetary` uses the same (pre-fix) expression and has
the same double-discount bug — to be corrected separately on its own branch.

Column conventions: d = DOCUMENT, di = DOCUMENT_ITEM, c = CUSTOMER.

Customer buckets (see CRM `EXCLUDED_CUSTOMER_NAMES`):
- SYSADMIN            — system-internal account; NOT a real sale. Excluded
                        from every figure.
- TOURIST / TOURIST.  — POS aggregation bucket for walk-in tourist sales
                        (one synthetic record for many anonymous walk-ins).
                        Real revenue, so it stays in `total_revenue`, and NOT
                        an identifiable individual, so it is excluded from the
                        new/returning classification. CR #80 breaks it out as
                        its own `tourist_*` bucket (counted as one-bill-one-
                        tourist), removed from "returning".
- NULL BT_CUID        — anonymous sale, no customer record. Folded into the
                        tourist bucket (CR #80). None occur in current data.

Date binds are Python `date` objects. `to` is inclusive of the whole day, so
the service passes `to_exclusive = to + 1 day` and the range is
`invc_post_date >= :from_date AND invc_post_date < :to_exclusive`.
"""

# Net line revenue (ex-tax), shared by both queries.
# = (unit price ex-tax) × qty × (1 − document discount).
# di.PRICE is ALREADY net of the item discount, so di.DISC_PERC is deliberately
# NOT applied here (applying it double-discounts). See module docstring.
_NET_LINE = (
    "(di.PRICE - NVL(di.TAX_AMT, 0)) "
    "* NVL(di.QTY, 0) "
    "* (1 - NVL(d.DISC_PERC, 0) / 100)"
)

# CR #83: gross line revenue (ex-tax), pre-discount — the counterpart of
# `_NET_LINE` BEFORE both the item discount (baked into di.PRICE) and the
# document discount. di.ORIG_PRICE is the original UNIT list price (tax-incl,
# = commission's FULL_PRICE_VAT); di.ORIG_TAX_AMT is its tax, so
# (ORIG_PRICE − ORIG_TAX_AMT) is the ex-tax list price (commission's
# FULL_PRICE). ORIG_PRICE ≥ PRICE always, so gross ≥ net. The value-weighted
# average discount rate is 100 × (gross − net) / gross.
_GROSS_LINE = (
    "(di.ORIG_PRICE - NVL(di.ORIG_TAX_AMT, 0)) "
    "* NVL(di.QTY, 0)"
)

# CR #80: a bill belongs to the "tourist" bucket when its customer is the POS
# walk-in TOURIST record OR there is no customer at all (anonymous NULL). Both
# are one-time, no-identity walk-ins — the PO treats them as a distinct bucket,
# not "returning". (Anonymous NULL bills don't occur in current data — every
# walk-in is booked under TOURIST — so folding NULL here is future-proofing.)
_IS_TOURIST = (
    "(d.BT_CUID IS NULL OR d.BT_CUID IN ("
    "SELECT SID FROM CUSTOMER "
    "WHERE UPPER(TRIM(FIRST_NAME)) IN ('TOURIST', 'TOURIST.')))"
)


class ReportsQueries:
    """Oracle queries for the sale-comparison report.

    CR #81: each period may be scoped to one or more stores. The scope is a
    union — `d.STORE_SID IN (:store_0, :store_1, …)` — injected via
    `store_filter()` and applied to the *in-range* bill scans only. The
    first-ever-purchase scan (`first_buy`) stays global so "new" keeps its
    documented meaning (first purchase ever, anywhere), rather than "first
    purchase at this store" — see CHANGELOG / CR #81 response.
    """

    # -----------------------------------------------------------------
    # CR #81 — optional per-period store scope.
    # -----------------------------------------------------------------
    @staticmethod
    def store_filter(store_sids):
        """Build the `AND d.STORE_SID IN (...)` fragment + its Oracle binds.

        Args:
            store_sids: list of Oracle STORE.SID ints, or None/empty for no
                scope (all stores).

        Returns (sql_fragment, binds) — `('', {})` when unscoped.
        """
        if not store_sids:
            return '', {}
        names = [f'store_{i}' for i in range(len(store_sids))]
        placeholders = ', '.join(f':{n}' for n in names)
        binds = {n: sid for n, sid in zip(names, store_sids)}
        return f'AND d.STORE_SID IN ({placeholders})', binds

    # -----------------------------------------------------------------
    # 1. Period totals: net revenue + distinct sale-bill count.
    # -----------------------------------------------------------------
    # All in-range sales EXCEPT the SYSADMIN system account. Walk-in
    # (TOURIST) and anonymous (NULL BT_CUID) sales ARE included — they are
    # real revenue.
    # CR #80: `tourist_customers` counts DISTINCT tourist bills (one bill = one
    # tourist — the TOURIST record aggregates many physical walk-ins), and
    # `tourist_customer_revenue` is their net revenue. The service subtracts the
    # tourist slice out of "returning".
    # CR #81: `store_filter` scopes this whole scan to the selected store(s).
    #
    # RETURNS NETTING (v1.4.0): `total_revenue` nets out returns (ITEM_TYPE = 2)
    # that post within a flat 30-day tail after the period — window
    # [from, to + 30 days], carried by :returns_cutoff = to_exclusive + 30d.
    # A return is subtracted regardless of whether its original sale fell in the
    # period ("easy calculation" — no linking to RETURNED_ITEM_INVOICE_SID).
    # SALES (ITEM_TYPE = 1) are still bounded to the period itself
    # (< :to_exclusive), so a sale in the tail is NOT counted. Per product
    # decision, ONLY `total_revenue` nets returns; `bill_count`,
    # `tourist_customers`, and `tourist_customer_revenue` remain sales-only
    # (< :to_exclusive), and the service's residual `returning_customer_revenue`
    # absorbs the return adjustment.
    @staticmethod
    def period_totals(store_filter=''):
        # Sale lines counted only inside the period; return lines counted
        # (negated) across the whole [from, returns_cutoff) window.
        sale_in_period = "di.ITEM_TYPE = 1 AND d.invc_post_date < :to_exclusive"
        return f"""
        SELECT
            ROUND(NVL(SUM(
                CASE
                    WHEN {sale_in_period}   THEN {_NET_LINE}
                    WHEN di.ITEM_TYPE = 2   THEN -1 * ({_NET_LINE})
                    ELSE 0
                END
            ), 0), 0)                            AS total_revenue,
            COUNT(DISTINCT CASE WHEN {sale_in_period} THEN d.SID END)
                                                 AS bill_count,
            COUNT(DISTINCT CASE WHEN {sale_in_period} AND {_IS_TOURIST} THEN d.SID END)
                                                 AS tourist_customers,
            ROUND(NVL(SUM(
                CASE WHEN {sale_in_period} AND {_IS_TOURIST} THEN {_NET_LINE} ELSE 0 END
            ), 0), 0)                            AS tourist_customer_revenue,
            -- CR #83: sale-only (in-period) gross + net, for avg_discount_rate.
            -- Both exclude returns (a discount rate is a property of sale lines),
            -- so net_sales_revenue is the pre-returns-netting sale total.
            ROUND(NVL(SUM(
                CASE WHEN {sale_in_period} THEN {_GROSS_LINE} ELSE 0 END
            ), 0), 0)                            AS gross_sales_revenue,
            ROUND(NVL(SUM(
                CASE WHEN {sale_in_period} THEN {_NET_LINE} ELSE 0 END
            ), 0), 0)                            AS net_sales_revenue
        FROM DOCUMENT d
        JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
        WHERE di.ITEM_TYPE IN (1, 2)
          AND d.invc_post_date >= :from_date
          AND d.invc_post_date <  :returns_cutoff
          {store_filter}
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
    # CR #81: `store_filter` scopes the in-range membership to the selected
    # store(s); `first_buy` stays global (first purchase EVER, any store).
    @staticmethod
    def new_vs_returning(store_filter=''):
        return f"""
        WITH in_range AS (
            SELECT d.BT_CUID AS cust,
                   SUM({_NET_LINE}) AS rev
            FROM DOCUMENT d
            JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
            WHERE di.ITEM_TYPE = 1
              AND d.invc_post_date >= :from_date
              AND d.invc_post_date <  :to_exclusive
              {store_filter}
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
