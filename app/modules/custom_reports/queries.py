"""
Oracle SQL queries for the custom-reports module.

These queries are deliberately raw — they pull receipt/sale events at the
line-item grain so the service layer can do FIFO unit matching in pandas.
SQL alone is awkward for FIFO; pulling ~10k rows and matching in Python
is simpler and fast enough.
"""


class CustomReportsQueries:

    # -----------------------------------------------------------------
    # SS25 / SS26 (or any list of seasons) — item attributes
    # -----------------------------------------------------------------
    # One row per item. Brings brand (vendor), size, season, current
    # on-hand snapshot, and base cost. :seasons is a comma-separated
    # quoted list interpolated into the IN clause by the caller (no user
    # input — values are validated against `^[A-Z0-9]+$`).
    SIZE_REPORT_ITEMS = """
        SELECT
            i.sid AS item_sid,
            TO_CHAR(i.upc) AS upc,
            v.vend_name AS brand,
            i.item_size AS item_size,
            d.d_long_name AS department,
            i.UDF5_STRING AS season,
            NVL(iq.qty, 0) AS on_hand_qty,
            i.first_rcvd_date AS first_rcvd_date
        FROM rps.invn_sbs_item i
        LEFT JOIN rps.vendor v ON v.sid = i.vend_sid
        LEFT JOIN rps.dcs d ON d.sid = i.dcs_sid AND d.sbs_sid = i.sbs_sid
        LEFT JOIN (
            SELECT invn_sbs_item_sid AS item_sid, SUM(qty) AS qty
            FROM rps.invn_sbs_item_qty
            GROUP BY invn_sbs_item_sid
        ) iq ON iq.item_sid = i.sid
        WHERE i.UDF5_STRING IN ({seasons})
    """

    # -----------------------------------------------------------------
    # Receipt events (vou_type=0 only — positive receipts; vendor
    # returns excluded for cleaner FIFO)
    # -----------------------------------------------------------------
    # `post_date` is the date the voucher was posted to the system. Used
    # as the receipt date for FIFO purposes. Filtered to the same set of
    # items as SIZE_REPORT_ITEMS via inner join.
    SIZE_REPORT_RECEIPTS = """
        SELECT
            vi.item_sid AS item_sid,
            vh.post_date AS post_date,
            vi.qty AS qty
        FROM rps.voucher vh
        JOIN rps.vou_item vi ON vi.vou_sid = vh.sid
        JOIN rps.invn_sbs_item i ON i.sid = vi.item_sid
        WHERE vh.vou_class = 0
          AND vh.slip_flag = 0
          AND vh.held = 0
          AND vh.status = 4
          AND vh.vou_type = 0
          AND i.UDF5_STRING IN ({seasons})
        ORDER BY vi.item_sid, vh.post_date
    """

    # -----------------------------------------------------------------
    # Sale events (item_type=1 only — straight sales; customer returns
    # excluded for cleaner FIFO)
    # -----------------------------------------------------------------
    SIZE_REPORT_SALES = """
        SELECT
            b.invn_sbs_item_sid AS item_sid,
            a.created_datetime AS sale_date,
            b.qty AS qty
        FROM rps.document a
        JOIN rps.document_item b ON a.sid = b.doc_sid
        JOIN rps.invn_sbs_item i ON i.sid = b.invn_sbs_item_sid
        WHERE a.is_held = 0
          AND a.receipt_type IN (0, 1)
          AND a.status = 4
          AND b.item_type = 1
          AND i.UDF5_STRING IN ({seasons})
        ORDER BY b.invn_sbs_item_sid, a.created_datetime
    """


# =====================================================================
# The query engine (CR #1) — line-grain pulls
# =====================================================================
# Everything below serves `POST /custom-reports/query`. It is separate
# from the SIZE_REPORT_* queries above, which keep serving `/sizes`.
#
# ONE PULL PER SUBJECT, AT LINE GRAIN, then grouped in pandas. SQL does
# the date bound and the joins; it does NOT do the GROUP BY. Three
# reasons, in order of weight:
#
#   1. Two dimensions are not SQL-expressible. `size` is normalised
#      through `normalize_size()` (a per-brand Python table) and
#      `customerType` is new-vs-returning RELATIVE TO THE PERIOD. Both
#      would have to come back raw and be re-grouped anyway, so a SQL
#      GROUP BY would only ever cover part of the vocabulary — and a
#      half-SQL half-pandas grouping is two code paths that must agree.
#   2. Line detail (`groupBy: []`) needs the lines regardless, so a
#      grouping engine in SQL would be a SECOND implementation of the
#      same measures. `mocks/engine.ts` — the frontend's executable
#      spec — has exactly one path; matching it statement for statement
#      is how this stays verifiable against it.
#   3. The volume does not justify anything cleverer. Measured on
#      production 2026-11-05: a FULL YEAR of sale lines is ~12.8k rows
#      (2024: 16,557 · 2025: 16,125 · 2026-to-date: 12,804), and all
#      1,041 posted receipts ever are 33,182 lines. The one-year cap
#      the picker enforces therefore bounds the pull to ~17k rows.
#
# ---------------------------------------------------------------------
# DOCUMENT SCOPE — and where it differs from the `reports` module
# ---------------------------------------------------------------------
# Scope here: `is_held = 0 AND receipt_type IN (0, 1) AND status = 4`,
# matching `/sizes` and `app/modules/commission`.
#
# `app/modules/reports` (live comparison) filters NONE of those three.
# That sounds like a large divergence and is not, because of a fact
# worth recording: `INVC_POST_DATE IS NULL on every status-2 and
# status-3 document` — all 41,480 such lines (production, 2026-11-05).
# Any query bounded on `invc_post_date` has therefore already dropped
# the unposted ones, so `status = 4` is implied by having a date at all.
#
# What is left is `is_held = 1 AND status = 4` (50 lines in all history)
# plus `receipt_type NOT IN (0, 1)`. Measured per year against the
# unfiltered scope:
#
#     2023   1,662 → 1,662    (0.00%)
#     2024  16,557 → 16,554   (0.02%)
#     2025  16,125 → 16,106   (0.12%)
#     2026  12,804 → 12,776   (0.22%)
#
# and 0.21% of 2026 net revenue. Small, but NOT zero — so a figure here
# can sit a fraction below the live-comparison dashboard, and that is
# this scope being stricter, not a defect in either.
# ---------------------------------------------------------------------

# Net line revenue (ex-tax). IDENTICAL to `reports.queries._NET_LINE` —
# copied rather than imported to keep this module's SQL readable in one
# place, and asserted equal in tests/test_custom_reports_engine.py so
# the two cannot drift.
#
# ⚠️ `di.PRICE` is ALREADY net of the ITEM discount. Re-applying
# `di.DISC_PERC` double-discounts; that bug shipped twice (reports
# v1.3.1 / CR #81 Diamond, and crm v1.2.0).
_NET_LINE = (
    "(di.PRICE - NVL(di.TAX_AMT, 0)) "
    "* NVL(di.QTY, 0) "
    "* (1 - NVL(d.DISC_PERC, 0) / 100)"
)

# Full Price when the ITEM discount is <= 30%. Same as `reports._IS_FP`
# (CR #85), NOT the combined item x doc rate the CRM module uses.
_IS_FP = "di.PRICE >= 0.7 * NVL(di.ORIG_PRICE, di.PRICE)"

# Product attributes, shared by the sales and imports pulls.
#
# Each mapping was verified against production on 2026-11-04/05; the
# traps are recorded where they bite:
#
#   · `description` is TEXT1. DESCRIPTION1 holds a supplier style
#     reference ('612110') despite the name, and TEXT2 is the
#     Vietnamese customs wording. Both are on 100% of 2026 sold units,
#     so this is a choice of language, not of coverage.
#   · `category` is the THIRD '-'-separated segment of DESCRIPTION2,
#     not DCS.C_LONG_NAME (empty in all 14 rows).
#     ⚠️ Taking the LAST segment instead looks better on coverage and
#     is worse: DESCRIPTION2 is sometimes two segments ('OWH-TSH',
#     'ULJ-DRS'), whose tail is an ABBREVIATION. Last-segment would file
#     those as 'TSH' and 'DRS' beside 'TSHIRT' and 'DRESS' — the same
#     category in two buckets. Measured over 2026 sold units: occurrence
#     3 is NULL on 66 of 15,478 units (0.43% → the '—' bucket), while
#     last-segment is NULL on 4 but splits 63 units off into synonym
#     buckets. An honest unknown beats a plausible duplicate.
#   · gender is the first character of DCS.D_LONG_NAME, EXCEPT for COSM
#     and HOME, which are departments with no gender at all. Taking
#     SUBSTR(...,1,1) blindly would invent a gender 'C' and a gender
#     'H', and leave departments 'OSM' and 'OME'.
#   · `size` comes back RAW and is normalised in Python — see
#     `normalize_size()` and the `caveatSizeNormalised` note.
_PRODUCT_COLS = """
            i.sid                                        AS item_sid,
            i.alu                                        AS alu,
            NVL(v.vend_name, ' ')                        AS brand,
            i.text1                                      AS description,
            i.udf5_string                                AS season,
            CASE WHEN dcs.d_long_name IN ('COSM', 'HOME') THEN NULL
                 ELSE SUBSTR(dcs.d_long_name, 1, 1) END  AS gender,
            CASE WHEN dcs.d_long_name IN ('COSM', 'HOME') THEN dcs.d_long_name
                 ELSE SUBSTR(dcs.d_long_name, 2) END     AS department,
            REGEXP_SUBSTR(i.description2, '[^-]+', 1, 3) AS category,
            i.item_size                                  AS item_size,
            -- The RAW DCS code, carried alongside the split gender and
            -- department purely for `normalize_size()`, which keys its
            -- shoe pass-through on 'WSHO' / 'MSHO'. Handing it the split
            -- department ('SHO') silently translated shoe sizes as
            -- clothing — a size-38 shoe came back 'M'.
            dcs.d_long_name                              AS dcs_code
"""

_PRODUCT_JOINS = """
        LEFT JOIN rps.vendor v   ON v.sid = i.vend_sid
        LEFT JOIN rps.dcs dcs    ON dcs.sid = i.dcs_sid
                                AND dcs.sbs_sid = i.sbs_sid
"""


class EngineQueries:
    """Line-grain pulls for `POST /custom-reports/query`."""

    # -----------------------------------------------------------------
    # Sale and return lines
    # -----------------------------------------------------------------
    # BOTH item types come back in one pull, tagged by `item_type`, and
    # the service splits them. They are NOT netted here — see the
    # returns note in service/engine: a return has no discount rate, so
    # it has no FP or MD side, and folding it into the net would break
    # `fpSales + mdSales == netSales`.
    #
    # ⚠️ Returns carry POSITIVE qty. There is no negative quantity
    # anywhere in DOCUMENT_ITEM, so a return cannot be found by sign —
    # only by `item_type = 2` (1,109 lines, 1,107 of them pointing at
    # the invoice they reverse).
    #
    # `bill` is PREFIXED WITH THE STORE CODE and `receiving` below is
    # not. The asymmetry is load-bearing: sales run six per-store DOC_NO
    # sequences, so the bare number merges 13,620 of 21,375 bills
    # (63.7%), while receiving is centralised and its bare VOU_NO merges
    # 0.6%.
    #
    # SYSADMIN is excluded, matching `reports` and `crm`: it is a
    # system-internal account, not a sale. TOURIST stays in — real
    # revenue — and is surfaced as its own `customerType` bucket rather
    # than being mixed into "returning".
    SALE_LINES = f"""
        SELECT
            TO_CHAR(d.invc_post_date, 'YYYY-MM-DD')      AS line_date,
            d.sid                                        AS bill_id,
            st.store_code || '-' || d.doc_no             AS bill,
            st.store_code                                AS store,
            di.item_type                                 AS item_type,
            NVL(di.qty, 0)                               AS qty,
            {_NET_LINE}                                  AS net_sales,
            CASE WHEN {_IS_FP} THEN 'FP' ELSE 'MD' END   AS fp_md,
            d.bt_cuid                                    AS cust_sid,
            TRIM(NVL(cu.first_name, '') || ' ' || NVL(cu.last_name, ''))
                                                         AS customer,
            cu.info1                                     AS customer_tier,
{_PRODUCT_COLS}
        FROM rps.document d
        JOIN rps.document_item di ON di.doc_sid = d.sid
        JOIN rps.invn_sbs_item i  ON i.sid = di.invn_sbs_item_sid
        LEFT JOIN rps.store st    ON st.sid = d.store_sid
        LEFT JOIN rps.customer cu ON cu.sid = d.bt_cuid
{_PRODUCT_JOINS}
        WHERE di.item_type IN (1, 2)
          AND d.is_held = 0
          AND d.receipt_type IN (0, 1)
          AND d.status = 4
          AND d.invc_post_date >= :from_date
          AND d.invc_post_date <  :to_exclusive
          AND (
              d.bt_cuid IS NULL
              OR d.bt_cuid NOT IN (
                  SELECT sid FROM rps.customer
                  WHERE UPPER(TRIM(first_name)) = 'SYSADMIN'
              )
          )
    """

    # -----------------------------------------------------------------
    # First-ever purchase per customer
    # -----------------------------------------------------------------
    # Feeds `customerType` (new vs returning), and is fetched SEPARATELY
    # and joined in Python rather than LEFT JOINed into SALE_LINES.
    #
    # ⚠️ THAT IS A PERFORMANCE FIX, NOT A STYLE CHOICE. Measured on
    # production, a year of sale lines (12,802 rows):
    #
    #     SALE_LINES with the join     35.10s
    #     SALE_LINES without it         8.93s
    #     this query on its own          1.75s
    #
    # The optimiser re-runs the whole-history aggregate per driving row
    # instead of materialising it once, so folding it in cost 26 seconds
    # to produce 5,142 rows that take under two to fetch alone. The
    # service skips this query entirely unless `customerType` is actually
    # grouped or filtered.
    #
    # Unbounded by the period ON PURPOSE: "new" must mean first purchase
    # EVER, not first purchase in the window. Same definition as reports'
    # `first_buy` scan (CR #81 response) — widening the period therefore
    # reclassifies people, which `caveatCustomerType` discloses.
    CUSTOMER_FIRST_BUY = """
        SELECT
            d.bt_cuid                                    AS cust_sid,
            TO_CHAR(MIN(d.invc_post_date), 'YYYY-MM-DD') AS first_buy
        FROM rps.document d
        JOIN rps.document_item di ON di.doc_sid = d.sid
        WHERE di.item_type = 1
          AND d.is_held = 0
          AND d.receipt_type IN (0, 1)
          AND d.status = 4
          AND d.bt_cuid IS NOT NULL
        GROUP BY d.bt_cuid
    """

    # -----------------------------------------------------------------
    # Receipt lines
    # -----------------------------------------------------------------
    # `vou_type = 0` excludes VENDOR RETURNS, matching the FIFO pull
    # `/sizes` already uses. `slip_flag = 0` excludes TRANSFER SLIPS,
    # which is the filter whose absence produced the retracted "39% of
    # receiving numbers collide" figure — with slips counted, 73.4% of
    # 13,916 rows merge; receipts alone, 0.6% of 1,041.
    #
    # `vi.cost` is a UNIT cost (verified against INVN_SBS_ITEM.COST on
    # qty>3 lines), so the line cost is cost x qty. Summing `cost` alone
    # understates by 21% (293.4B vs 372.6B over all receipts).
    #
    # `post_date` is the receipt date. NOT `arrived_date`, which is NULL
    # on all 1,041 posted receipts.
    RECEIPT_LINES = f"""
        SELECT
            TO_CHAR(vh.post_date, 'YYYY-MM-DD')          AS line_date,
            -- TO_CHAR because VOU_NO is NUMERIC. Every dimension value
            -- crosses the wire as a string (the frontend's filters are
            -- `string[]`), so casting here keeps the group key, the
            -- filter value and the picker option the same type.
            TO_CHAR(vh.vou_no)                           AS receiving,
            st.store_code                                AS store,
            NVL(vi.qty, 0)                               AS received_units,
            NVL(vi.cost, 0) * NVL(vi.qty, 0)             AS received_cost,
{_PRODUCT_COLS}
        FROM rps.voucher vh
        JOIN rps.vou_item vi      ON vi.vou_sid = vh.sid
        JOIN rps.invn_sbs_item i  ON i.sid = vi.item_sid
        LEFT JOIN rps.store st    ON st.sid = vh.store_sid
{_PRODUCT_JOINS}
        WHERE vh.vou_class = 0
          AND vh.slip_flag = 0
          AND vh.held = 0
          AND vh.status = 4
          AND vh.vou_type = 0
          AND vh.post_date >= :from_date
          AND vh.post_date <  :to_exclusive
    """

    # -----------------------------------------------------------------
    # Sell-through — the sold side
    # -----------------------------------------------------------------
    # Sale UNITS per (item_sid, date), for FIFO attribution back to the
    # receipt lines of the same item. Bounded to the SAME period as the
    # cohort, which is the user's rule: the period picks the receipts
    # AND bounds their sales.
    #
    # `item_type = 1` only. A returned unit was not sold through, so
    # returns are skipped outright rather than netted — the same choice
    # `/sizes` already ships.
    SELLTHROUGH_SOLD = """
        SELECT
            di.invn_sbs_item_sid                         AS item_sid,
            TO_CHAR(d.invc_post_date, 'YYYY-MM-DD')      AS sale_date,
            NVL(di.qty, 0)                               AS qty
        FROM rps.document d
        JOIN rps.document_item di ON di.doc_sid = d.sid
        WHERE di.item_type = 1
          AND d.is_held = 0
          AND d.receipt_type IN (0, 1)
          AND d.status = 4
          AND d.invc_post_date >= :from_date
          AND d.invc_post_date <  :to_exclusive
        ORDER BY di.invn_sbs_item_sid, d.invc_post_date
    """
