"""
Store period performance Excel report — one workbook PER STORE.

Answers three questions for a short trading window, per store:
  1. Sales performance   — net sales, bills, units, avg bill, ASP, returns, daily curve
  2. FP vs MD split      — full-price vs markdown units/revenue + discount-band detail
  3. Customer mix        — new vs returning vs walk-in, counts and revenue

Definitions are taken from `app/modules/reports/queries.py` (CR #78/#83/#85),
so the figures here reconcile with the live sale-comparison dashboard rather
than inventing a second truth:

  net line revenue   = (di.PRICE - NVL(di.TAX_AMT,0)) * di.QTY
                       * (1 - NVL(d.DISC_PERC,0)/100)
                     di.PRICE is ALREADY net of the ITEM discount, so
                     di.DISC_PERC must NOT be re-applied (double-discount bug
                     fixed after CR #81).
  gross line revenue = (NVL(di.ORIG_PRICE,di.PRICE) - NVL(di.ORIG_TAX_AMT,0))
                       * di.QTY                     -- ex-tax list value
  Full Price         = di.PRICE >= 0.7 * NVL(di.ORIG_PRICE, di.PRICE)
                     i.e. ITEM discount <= 30%. Exactly 30% counts as FP.
                     Markdown is derived as the RESIDUAL (total - FP) so the
                     split reconciles to the unit/revenue totals exactly.

Returns: this report is for a short window, so the live module's flat 30-day
returns tail is deliberately NOT used — a 4-day period with a 30-day tail
would net returns of sales that mostly fall outside it. Returns are instead
reported as their own line, posted INSIDE the window (di.ITEM_TYPE = 2), and
`net_sales_after_returns` is shown alongside the sales-only `net_sales`.

Customer mix (identifiable customers only, per `queries.py` buckets):
  new        — first-ever sale (any store, all history) falls inside the period
  returning  — first-ever sale predates the period
  walk-in    — the POS TOURIST aggregate record, or a NULL customer. Counted as
               one customer per bill, since the single TOURIST record stands in
               for many anonymous people. Real revenue, no identity.
  excluded   — SYSADMIN, the system-internal account. Not a real sale.
The four buckets sum to the store's net sales exactly.

"new" is GLOBAL (first purchase anywhere), matching the live dashboard. Because
these are per-store workbooks, a second block also reports FIRST VISIT TO THIS
STORE, which is the store-scoped reading of the same question.

Output: document/reports/store_performance_<STORE>_<start>_<end>.xlsx
"""
import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_oracle_connection


# --- shared line expressions (see module docstring) -----------------------
_NET_LINE = (
    "(di.PRICE - NVL(di.TAX_AMT, 0)) "
    "* NVL(di.QTY, 0) "
    "* (1 - NVL(d.DISC_PERC, 0) / 100)"
)
_GROSS_LINE = (
    "(NVL(di.ORIG_PRICE, di.PRICE) - NVL(di.ORIG_TAX_AMT, 0)) "
    "* NVL(di.QTY, 0)"
)
_IS_FP = "di.PRICE >= 0.7 * NVL(di.ORIG_PRICE, di.PRICE)"

# Item-level discount rate, as a whole percent. Used only for the band detail.
_ITEM_DISC = (
    "100 * (NVL(di.ORIG_PRICE, di.PRICE) - di.PRICE) "
    "/ NULLIF(NVL(di.ORIG_PRICE, di.PRICE), 0)"
)

# Bill-level predicate shared by every scan. STATUS=4 is a finalised document;
# RECEIPT_TYPE 0/1 are the sale receipt types (per the monthly-sales report).
_DOC_WHERE = """
      d.STATUS = 4
      AND d.RECEIPT_TYPE IN (0, 1)
      AND d.STORE_CODE = :store_code
      AND d.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD')
      AND d.invc_post_date <  TO_DATE(:end_exclusive, 'YYYY-MM-DD')
"""

# Which customer record a bill belongs to. TOURIST / NULL = anonymous walk-in,
# SYSADMIN = not a real sale.
_IS_WALKIN = (
    "(d.BT_CUID IS NULL OR d.BT_CUID IN ("
    "SELECT SID FROM CUSTOMER "
    "WHERE UPPER(TRIM(FIRST_NAME)) IN ('TOURIST', 'TOURIST.')))"
)
_IS_SYSADMIN = (
    "(d.BT_CUID IS NOT NULL AND d.BT_CUID IN ("
    "SELECT SID FROM CUSTOMER "
    "WHERE UPPER(TRIM(FIRST_NAME)) = 'SYSADMIN'))"
)


# =========================================================================
# Queries
# =========================================================================

# 1. Headline sale totals (sale lines only) + returns, in one scan.
Q_TOTALS = f"""
SELECT
    COUNT(DISTINCT CASE WHEN di.ITEM_TYPE = 1 THEN d.SID END)   AS bills,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 1 THEN NVL(di.QTY, 0) END), 0)
                                                                AS units,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 1 THEN {_NET_LINE} END), 0)
                                                                AS net_sales,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 1 THEN {_GROSS_LINE} END), 0)
                                                                AS gross_sales,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 1 AND {_IS_FP}
                 THEN NVL(di.QTY, 0) END), 0)                   AS fp_units,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 1 AND {_IS_FP}
                 THEN {_NET_LINE} END), 0)                      AS fp_net,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 1 AND {_IS_FP}
                 THEN {_GROSS_LINE} END), 0)                    AS fp_gross,
    COUNT(DISTINCT CASE WHEN di.ITEM_TYPE = 2 THEN d.SID END)   AS return_bills,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 2 THEN NVL(di.QTY, 0) END), 0)
                                                                AS return_units,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 2 THEN {_NET_LINE} END), 0)
                                                                AS return_value
FROM DOCUMENT d
JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
WHERE di.ITEM_TYPE IN (1, 2)
  AND NOT {_IS_SYSADMIN}
  AND {_DOC_WHERE.strip()}
"""

# 2. Daily curve.
Q_DAILY = f"""
SELECT
    TRUNC(CAST(d.invc_post_date AS DATE))                       AS sale_day,
    COUNT(DISTINCT CASE WHEN di.ITEM_TYPE = 1 THEN d.SID END)   AS bills,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 1 THEN NVL(di.QTY, 0) END), 0)
                                                                AS units,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 1 THEN {_NET_LINE} END), 0)
                                                                AS net_sales,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 1 AND {_IS_FP}
                 THEN NVL(di.QTY, 0) END), 0)                   AS fp_units,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 1 AND {_IS_FP}
                 THEN {_NET_LINE} END), 0)                      AS fp_net,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 2 THEN NVL(di.QTY, 0) END), 0)
                                                                AS return_units,
    NVL(SUM(CASE WHEN di.ITEM_TYPE = 2 THEN {_NET_LINE} END), 0)
                                                                AS return_value
FROM DOCUMENT d
JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
WHERE di.ITEM_TYPE IN (1, 2)
  AND NOT {_IS_SYSADMIN}
  AND {_DOC_WHERE.strip()}
GROUP BY TRUNC(CAST(d.invc_post_date AS DATE))
ORDER BY sale_day
"""

# 3. Discount-band detail (sale lines only). Bands are cut on the ITEM
#    discount rate — the same quantity the FP/MD rule tests — so the 0-30
#    bands sum to FP and the rest sum to MD.
Q_BANDS = f"""
SELECT
    CASE
        WHEN {_ITEM_DISC} IS NULL   THEN 'unknown'
        WHEN {_ITEM_DISC} <= 0      THEN '0% (no discount)'
        WHEN {_ITEM_DISC} < 10      THEN '1-9%'
        WHEN {_ITEM_DISC} < 20      THEN '10-19%'
        WHEN {_ITEM_DISC} <= 30     THEN '20-30%'
        WHEN {_ITEM_DISC} < 40      THEN '31-39%'
        WHEN {_ITEM_DISC} < 50      THEN '40-49%'
        WHEN {_ITEM_DISC} < 60      THEN '50-59%'
        WHEN {_ITEM_DISC} < 70      THEN '60-69%'
        WHEN {_ITEM_DISC} < 80      THEN '70-79%'
        ELSE '80%+'
    END                                             AS band,
    MIN(ROUND({_ITEM_DISC}, 1))                     AS min_rate,
    MAX(ROUND({_ITEM_DISC}, 1))                     AS max_rate,
    COUNT(*)                                        AS lines,
    NVL(SUM(NVL(di.QTY, 0)), 0)                     AS units,
    NVL(SUM({_NET_LINE}), 0)                        AS net_sales,
    NVL(SUM({_GROSS_LINE}), 0)                      AS gross_sales
FROM DOCUMENT d
JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
WHERE di.ITEM_TYPE = 1
  AND NOT {_IS_SYSADMIN}
  AND {_DOC_WHERE.strip()}
GROUP BY
    CASE
        WHEN {_ITEM_DISC} IS NULL   THEN 'unknown'
        WHEN {_ITEM_DISC} <= 0      THEN '0% (no discount)'
        WHEN {_ITEM_DISC} < 10      THEN '1-9%'
        WHEN {_ITEM_DISC} < 20      THEN '10-19%'
        WHEN {_ITEM_DISC} <= 30     THEN '20-30%'
        WHEN {_ITEM_DISC} < 40      THEN '31-39%'
        WHEN {_ITEM_DISC} < 50      THEN '40-49%'
        WHEN {_ITEM_DISC} < 60      THEN '50-59%'
        WHEN {_ITEM_DISC} < 70      THEN '60-69%'
        WHEN {_ITEM_DISC} < 80      THEN '70-79%'
        ELSE '80%+'
    END
"""

BAND_ORDER = [
    '0% (no discount)', '1-9%', '10-19%', '20-30%',
    '31-39%', '40-49%', '50-59%', '60-69%', '70-79%', '80%+', 'unknown',
]
# Bands that land on the Full-Price side of the 30% line.
BAND_IS_FP = {'0% (no discount)', '1-9%', '10-19%', '20-30%'}

# 4. Per-customer in-period revenue (identifiable customers only), with each
#    customer's first-ever sale date GLOBALLY and at THIS store. One row per
#    customer so counts, revenue and bills are all derivable in Python.
Q_CUSTOMERS = f"""
WITH in_range AS (
    SELECT d.BT_CUID                        AS cust,
           COUNT(DISTINCT d.SID)            AS bills,
           SUM({_NET_LINE})                 AS rev,
           SUM(NVL(di.QTY, 0))              AS units
    FROM DOCUMENT d
    JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
    WHERE di.ITEM_TYPE = 1
      AND d.BT_CUID IS NOT NULL
      AND NOT {_IS_WALKIN}
      AND NOT {_IS_SYSADMIN}
      AND {_DOC_WHERE.strip()}
    GROUP BY d.BT_CUID
),
first_buy AS (
    SELECT d.BT_CUID                                           AS cust,
           MIN(TRUNC(CAST(d.invc_post_date AS DATE)))          AS first_any,
           MIN(CASE WHEN d.STORE_CODE = :store_code
                    THEN TRUNC(CAST(d.invc_post_date AS DATE)) END)
                                                               AS first_here
    FROM DOCUMENT d
    JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
    WHERE di.ITEM_TYPE = 1
      AND d.STATUS = 4
      AND d.RECEIPT_TYPE IN (0, 1)
      AND d.BT_CUID IN (SELECT cust FROM in_range)
    GROUP BY d.BT_CUID
)
SELECT ir.cust                      AS cust,
       ir.bills                     AS bills,
       ir.rev                       AS rev,
       ir.units                     AS units,
       fb.first_any                 AS first_any,
       fb.first_here                AS first_here
FROM in_range ir
JOIN first_buy fb ON fb.cust = ir.cust
"""

# 4b. Biggest sale lines + every return line. A short window at this price
#     point is easily dominated by one piece — RWP 29 Oct-1 Nov 2026 was 61%
#     one necklace — so the percentages above are unreadable without it.
Q_TOP_LINES = f"""
SELECT * FROM (
    SELECT
        TRUNC(CAST(d.invc_post_date AS DATE))       AS sale_day,
        CASE di.ITEM_TYPE WHEN 1 THEN 'sale' ELSE 'return' END  AS line_type,
        v.VEND_NAME                                 AS brand,
        di.ALU                                      AS product_code,
        di.DESCRIPTION1                             AS description,
        NVL(di.QTY, 0)                              AS qty,
        {_NET_LINE}                                 AS net_sales,
        ROUND({_ITEM_DISC}, 1)                      AS item_discount_pct
    FROM DOCUMENT d
    JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
    LEFT JOIN INVN_SBS_ITEM i ON i.SID = di.INVN_SBS_ITEM_SID
    LEFT JOIN VENDOR v        ON v.SID = i.VEND_SID
    WHERE di.ITEM_TYPE IN (1, 2)
      AND NOT {_IS_SYSADMIN}
      AND {_DOC_WHERE.strip()}
    ORDER BY ABS({_NET_LINE}) DESC
) WHERE ROWNUM <= 15
"""

# 5. Walk-in (TOURIST / anonymous) and SYSADMIN slices. One bill = one walk-in.
Q_WALKIN = f"""
SELECT
    COUNT(DISTINCT CASE WHEN {_IS_WALKIN} THEN d.SID END)       AS walkin_bills,
    NVL(SUM(CASE WHEN {_IS_WALKIN} THEN NVL(di.QTY, 0) END), 0) AS walkin_units,
    NVL(SUM(CASE WHEN {_IS_WALKIN} THEN {_NET_LINE} END), 0)
                                                                AS walkin_rev,
    COUNT(DISTINCT CASE WHEN {_IS_SYSADMIN} THEN d.SID END)     AS sysadmin_bills,
    NVL(SUM(CASE WHEN {_IS_SYSADMIN} THEN {_NET_LINE} END), 0)
                                                                AS sysadmin_rev
FROM DOCUMENT d
JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
WHERE di.ITEM_TYPE = 1
  AND {_DOC_WHERE.strip()}
"""


# =========================================================================
# Helpers
# =========================================================================

def _r(value, places=0):
    """Half-up rounding. Oracle rounds half-up; Python's round() is banker's,
    which has already produced real off-by-one bugs in these reports."""
    if value is None:
        return 0
    q = Decimal(1).scaleb(-places)
    d = Decimal(repr(float(value))).quantize(q, rounding=ROUND_HALF_UP)
    return int(d) if places == 0 else float(d)


def _pct(part, whole, places=1):
    """part/whole as a whole-number percentage, 0 when whole is 0."""
    return _r(100 * part / whole, places) if whole else 0


def _as_date(value):
    return value.date() if isinstance(value, datetime) else value


def fetch(sql, params):
    conn = get_oracle_connection()
    if conn is None:
        raise RuntimeError('Could not open Oracle connection')
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            cols = [d[0].lower() for d in cur.description]
            rows = cur.fetchall()
        finally:
            cur.close()
    finally:
        conn.close()
    return [dict(zip(cols, r)) for r in rows]


def resolve_stores(codes):
    """Map each requested STORE_CODE to its name; fail loudly on a typo."""
    rows = fetch('SELECT STORE_CODE, STORE_NAME FROM STORE', {})
    known = {r['store_code']: (r['store_name'] or '') for r in rows}
    missing = [c for c in codes if c not in known]
    if missing:
        raise SystemExit(
            f'Unknown store code(s): {", ".join(missing)}. '
            f'Known: {", ".join(sorted(known))}'
        )
    return {c: known[c] for c in codes}


# =========================================================================
# Per-store assembly
# =========================================================================

def build_store(store_code, start_date, end_date):
    """Collect every figure for one store into a plain dict."""
    end_exclusive = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
    params = {
        'store_code': store_code,
        'start_date': start_date,
        'end_exclusive': end_exclusive,
    }

    totals = fetch(Q_TOTALS, params)[0]
    daily = fetch(Q_DAILY, params)
    bands = fetch(Q_BANDS, params)
    customers = fetch(Q_CUSTOMERS, params)
    walkin = fetch(Q_WALKIN, params)[0]
    top_lines = fetch(Q_TOP_LINES, params)

    # --- sales performance -------------------------------------------
    bills = int(totals['bills'] or 0)
    units = _r(totals['units'])
    net = _r(totals['net_sales'])
    gross = _r(totals['gross_sales'])
    fp_units = _r(totals['fp_units'])
    fp_net = _r(totals['fp_net'])
    fp_gross = _r(totals['fp_gross'])
    ret_value = _r(totals['return_value'])
    ret_units = _r(totals['return_units'])

    # Markdown as the residual, so fp + md == total exactly.
    md_units, md_net, md_gross = units - fp_units, net - fp_net, gross - fp_gross

    perf = {
        'bills': bills,
        'units_sold': units,
        'net_sales': net,
        'gross_list_value': gross,
        'discount_value': gross - net,
        'avg_discount_rate_pct': _pct(gross - net, gross),
        'avg_bill_value': _r(net / bills) if bills else 0,
        'units_per_bill': _r(units / bills, 2) if bills else 0,
        'avg_selling_price': _r(net / units) if units else 0,
        'return_bills': int(totals['return_bills'] or 0),
        'return_units': ret_units,
        'return_value': ret_value,
        'net_sales_after_returns': net - ret_value,
        'trading_days': len(daily),
        'net_sales_per_day': _r(net / len(daily)) if daily else 0,
    }

    # Concentration: how much of the store's net sales is one piece. At this
    # price point a single sale can carry the whole period, which makes the
    # FP/MD and customer percentages a statement about one item rather than
    # about the store's trading.
    sale_lines = sorted((l for l in top_lines if l['line_type'] == 'sale'),
                        key=lambda l: -float(l['net_sales'] or 0))
    perf['top_line_net_sales'] = _r(sale_lines[0]['net_sales']) if sale_lines else 0
    perf['top_line_pct_of_net'] = _pct(perf['top_line_net_sales'], net)
    top5 = sum(_r(l['net_sales']) for l in sale_lines[:5])
    perf['top5_lines_pct_of_net'] = _pct(top5, net)

    # --- FP vs MD ----------------------------------------------------
    fpmd = [
        {
            'bucket': 'Full price (item discount <= 30%)',
            'units': fp_units, 'units_pct': _pct(fp_units, units),
            'net_sales': fp_net, 'net_sales_pct': _pct(fp_net, net),
            'gross_list_value': fp_gross,
            'avg_discount_rate_pct': _pct(fp_gross - fp_net, fp_gross),
            'avg_selling_price': _r(fp_net / fp_units) if fp_units else 0,
        },
        {
            'bucket': 'Markdown (item discount > 30%)',
            'units': md_units, 'units_pct': _pct(md_units, units),
            'net_sales': md_net, 'net_sales_pct': _pct(md_net, net),
            'gross_list_value': md_gross,
            'avg_discount_rate_pct': _pct(md_gross - md_net, md_gross),
            'avg_selling_price': _r(md_net / md_units) if md_units else 0,
        },
        {
            'bucket': 'TOTAL',
            'units': units, 'units_pct': _pct(units, units),
            'net_sales': net, 'net_sales_pct': _pct(net, net),
            'gross_list_value': gross,
            'avg_discount_rate_pct': _pct(gross - net, gross),
            'avg_selling_price': _r(net / units) if units else 0,
        },
    ]

    by_band = {r['band']: r for r in bands}
    band_rows = []
    for name in BAND_ORDER:
        r = by_band.get(name)
        if not r:
            continue
        b_net, b_gross, b_units = _r(r['net_sales']), _r(r['gross_sales']), _r(r['units'])
        band_rows.append({
            'discount_band': name,
            'side': 'FP' if name in BAND_IS_FP else ('?' if name == 'unknown' else 'MD'),
            'lines': int(r['lines'] or 0),
            'units': b_units,
            'units_pct': _pct(b_units, units),
            'net_sales': b_net,
            'net_sales_pct': _pct(b_net, net),
            'gross_list_value': b_gross,
            'avg_selling_price': _r(b_net / b_units) if b_units else 0,
        })

    # --- customer mix ------------------------------------------------
    period_start = date.fromisoformat(start_date)
    mix = defaultdict(lambda: {'customers': 0, 'bills': 0, 'units': 0, 'rev': 0})
    here_mix = defaultdict(lambda: {'customers': 0, 'bills': 0, 'units': 0, 'rev': 0})
    for c in customers:
        rev, bl, un = _r(c['rev']), int(c['bills'] or 0), _r(c['units'])

        key = 'New' if _as_date(c['first_any']) >= period_start else 'Returning'
        acc = mix[key]
        acc['customers'] += 1
        acc['bills'] += bl
        acc['units'] += un
        acc['rev'] += rev

        # Store-scoped reading: first_here is never NULL (they bought here in
        # the period), so this is simply "was that first visit inside the
        # window". Someone can be Returning globally and New to this store.
        here_key = ('First visit to this store'
                    if _as_date(c['first_here']) >= period_start
                    else 'Been to this store before')
        acc = here_mix[here_key]
        acc['customers'] += 1
        acc['bills'] += bl
        acc['units'] += un
        acc['rev'] += rev

    walkin_bills = int(walkin['walkin_bills'] or 0)
    walkin_rev = _r(walkin['walkin_rev'])
    sysadmin_bills = int(walkin['sysadmin_bills'] or 0)
    sysadmin_rev = _r(walkin['sysadmin_rev'])

    def cust_row(label, d, note=''):
        return {
            'segment': label,
            'customers': d['customers'],
            'bills': d['bills'],
            'units': d['units'],
            'net_sales': d['rev'],
            'net_sales_pct': _pct(d['rev'], net),
            'avg_spend_per_customer': (_r(d['rev'] / d['customers'])
                                       if d['customers'] else 0),
            'avg_bill_value': _r(d['rev'] / d['bills']) if d['bills'] else 0,
            'note': note,
        }

    cust_rows = [
        cust_row('New (first purchase ever, any store)', mix['New']),
        cust_row('Returning (bought before the period)', mix['Returning']),
        cust_row('Walk-in / tourist (no identity)',
                 {'customers': walkin_bills, 'bills': walkin_bills,
                  'units': _r(walkin['walkin_units']), 'rev': walkin_rev},
                 'one bill counted as one walk-in'),
    ]
    if sysadmin_bills or sysadmin_rev:
        cust_rows.append(cust_row(
            'SYSADMIN (excluded from every other figure)',
            {'customers': sysadmin_bills, 'bills': sysadmin_bills,
             'units': 0, 'rev': sysadmin_rev},
            'system account — not a real sale'))

    identified_rev = mix['New']['rev'] + mix['Returning']['rev']
    cust_rows.append({
        'segment': 'TOTAL (excl. SYSADMIN)',
        'customers': (mix['New']['customers'] + mix['Returning']['customers']
                      + walkin_bills),
        'bills': mix['New']['bills'] + mix['Returning']['bills'] + walkin_bills,
        'units': (mix['New']['units'] + mix['Returning']['units']
                  + _r(walkin['walkin_units'])),
        'net_sales': identified_rev + walkin_rev,
        'net_sales_pct': _pct(identified_rev + walkin_rev, net),
        'avg_spend_per_customer': 0,
        'avg_bill_value': 0,
        'note': 'reconciles to net_sales on the Summary sheet',
    })

    here_rows = [
        cust_row('First visit to this store',
                 here_mix['First visit to this store']),
        cust_row('Been to this store before',
                 here_mix['Been to this store before']),
    ]

    # --- daily -------------------------------------------------------
    daily_rows = []
    for r in daily:
        d_net, d_units = _r(r['net_sales']), _r(r['units'])
        d_fp_net, d_fp_units = _r(r['fp_net']), _r(r['fp_units'])
        d_bills = int(r['bills'] or 0)
        day = _as_date(r['sale_day'])
        daily_rows.append({
            'date': day,
            'weekday': day.strftime('%a'),
            'bills': d_bills,
            'units_sold': d_units,
            'net_sales': d_net,
            'avg_bill_value': _r(d_net / d_bills) if d_bills else 0,
            'fp_units': d_fp_units,
            'md_units': d_units - d_fp_units,
            'fp_net_sales': d_fp_net,
            'md_net_sales': d_net - d_fp_net,
            'md_share_of_net_pct': _pct(d_net - d_fp_net, d_net),
            'return_units': _r(r['return_units']),
            'return_value': _r(r['return_value']),
        })

    # Reconciliation guard: every decomposition must re-add to the headline, or
    # the workbook is worse than no workbook. Checked on the RAW (unrounded)
    # sums — each sheet rounds its own cells to whole VND, so a sheet column can
    # sit 1-2 VND off the headline purely from that. On a billion-VND figure
    # that is display rounding, not a data error; a real break is orders of
    # magnitude larger. FP + MD is exact by construction (MD is the residual).
    raw_net = float(totals['net_sales'] or 0)
    slack = 2.0
    checks = [
        ('daily', sum(float(d['net_sales'] or 0) for d in daily)),
        ('discount bands', sum(float(b['net_sales'] or 0) for b in bands)),
        ('customer buckets',
         sum(float(c['rev'] or 0) for c in customers) + float(walkin['walkin_rev'] or 0)),
    ]
    for name, value in checks:
        assert abs(value - raw_net) <= slack, (
            f'{store_code}: {name} sums to {value:,.0f} but net sales is '
            f'{raw_net:,.0f}'
        )
    assert fp_net + md_net == net, 'FP + MD != total net'

    line_rows = [{
        'date': _as_date(l['sale_day']),
        'line_type': l['line_type'],
        'brand': l['brand'] or '',
        'product_code': l['product_code'] or '',
        'description': l['description'] or '',
        'qty': _r(l['qty'], 2),
        'net_sales': _r(l['net_sales']),
        'pct_of_net_sales': _pct(_r(l['net_sales']), net),
        'item_discount_pct': _r(l['item_discount_pct'], 1),
    } for l in top_lines]

    return {
        'store_code': store_code,
        'perf': perf,
        'fpmd': fpmd,
        'bands': band_rows,
        'customers': cust_rows,
        'customers_here': here_rows,
        'daily': daily_rows,
        'top_lines': line_rows,
    }


# =========================================================================
# Excel
# =========================================================================

HEADER_FILL = '305496'
SUBHEAD_FILL = 'D9E1F2'

PERF_LABELS = [
    ('net_sales', 'Net sales (ex-VAT, before returns)', '#,##0'),
    ('return_value', 'Returns posted in period', '#,##0'),
    ('net_sales_after_returns', 'Net sales after returns', '#,##0'),
    (None, None, None),
    ('bills', 'Bills (sale transactions)', '#,##0'),
    ('units_sold', 'Units sold', '#,##0'),
    ('return_units', 'Units returned', '#,##0'),
    ('return_bills', 'Bills containing a return', '#,##0'),
    (None, None, None),
    ('avg_bill_value', 'Average bill value', '#,##0'),
    ('units_per_bill', 'Units per bill', '#,##0.00'),
    ('avg_selling_price', 'Average selling price per unit', '#,##0'),
    (None, None, None),
    ('gross_list_value', 'Gross list value (ex-VAT, pre-discount)', '#,##0'),
    ('discount_value', 'Discount given away', '#,##0'),
    ('avg_discount_rate_pct', 'Average discount rate %', '#,##0.0'),
    (None, None, None),
    ('trading_days', 'Trading days with sales', '#,##0'),
    ('net_sales_per_day', 'Net sales per trading day', '#,##0'),
    (None, None, None),
    ('top_line_net_sales', 'Largest single line (net)', '#,##0'),
    ('top_line_pct_of_net', '  — its share of net sales %', '#,##0.0'),
    ('top5_lines_pct_of_net', 'Top 5 lines share of net sales %', '#,##0.0'),
]


def _style_header(ws, row=1, fill=HEADER_FILL, font_color='FFFFFF'):
    from openpyxl.styles import Alignment, Font, PatternFill
    for cell in ws[row]:
        if cell.value is None:
            continue
        cell.font = Font(bold=True, color=font_color)
        cell.fill = PatternFill(start_color=fill, end_color=fill, fill_type='solid')
        cell.alignment = Alignment(horizontal='center', vertical='center')


def _autosize(ws, cap=30, floor=12):
    from openpyxl.utils import get_column_letter
    for col in ws.columns:
        lengths = [len(f'{c.value:,.0f}') if isinstance(c.value, (int, float))
                   else len(str(c.value or '')) for c in col]
        width = min(max(max(lengths) + 2, floor), cap)
        ws.column_dimensions[get_column_letter(col[0].column)].width = width


def _table(ws, headers, rows, money_cols=(), pct_cols=(), start_row=None):
    """Write a header row + data rows, returning the next free row."""
    from openpyxl.styles import Font
    r0 = start_row or (ws.max_row + 1 if ws.max_row > 1 else 1)
    ws.cell(row=r0, column=1)
    for i, h in enumerate(headers, start=1):
        ws.cell(row=r0, column=i, value=h)
    _style_header(ws, r0)
    for j, row in enumerate(rows, start=1):
        for i, h in enumerate(headers, start=1):
            cell = ws.cell(row=r0 + j, column=i, value=row.get(h))
            if h in money_cols:
                cell.number_format = '#,##0'
            elif h in pct_cols:
                cell.number_format = '#,##0.0'
        if str(row.get(headers[0], '')).startswith('TOTAL'):
            for i in range(1, len(headers) + 1):
                ws.cell(row=r0 + j, column=i).font = Font(bold=True)
    return r0 + len(rows) + 2


def write_store_xlsx(data, out_path, store_name, start_date, end_date, as_of):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    code = data['store_code']
    wb = Workbook()

    # --- Summary -----------------------------------------------------
    ws = wb.active
    ws.title = 'Summary'
    ws['A1'] = f'{code} — {store_name}'
    ws['A1'].font = Font(bold=True, size=14)
    ws['A2'] = f'{start_date} to {end_date} (inclusive)'
    ws['A3'] = 'Net sales are ex-VAT. See the Metadata sheet for every definition.'
    ws['A3'].font = Font(italic=True, size=9)

    row = 5
    ws.cell(row=row, column=1, value='Metric')
    ws.cell(row=row, column=2, value='Value')
    _style_header(ws, row)
    row += 1
    for key, label, fmt in PERF_LABELS:
        if key is None:
            row += 1
            continue
        ws.cell(row=row, column=1, value=label)
        cell = ws.cell(row=row, column=2, value=data['perf'][key])
        cell.number_format = fmt
        row += 1

    row += 1
    ws.cell(row=row, column=1, value='Full price vs markdown').font = Font(bold=True)
    row += 1
    row = _table(
        ws,
        ['bucket', 'units', 'units_pct', 'net_sales', 'net_sales_pct',
         'avg_selling_price', 'avg_discount_rate_pct'],
        data['fpmd'],
        money_cols={'units', 'net_sales', 'avg_selling_price'},
        pct_cols={'units_pct', 'net_sales_pct', 'avg_discount_rate_pct'},
        start_row=row,
    )

    ws.cell(row=row, column=1, value='Customer mix').font = Font(bold=True)
    row += 1
    _table(
        ws,
        ['segment', 'customers', 'bills', 'units', 'net_sales', 'net_sales_pct',
         'avg_spend_per_customer', 'avg_bill_value', 'note'],
        data['customers'],
        money_cols={'units', 'net_sales', 'avg_spend_per_customer', 'avg_bill_value'},
        pct_cols={'net_sales_pct'},
        start_row=row,
    )
    _autosize(ws, cap=44, floor=14)
    ws.column_dimensions['A'].width = 42

    # --- Daily -------------------------------------------------------
    ws = wb.create_sheet('Daily')
    headers = ['date', 'weekday', 'bills', 'units_sold', 'net_sales',
               'avg_bill_value', 'fp_units', 'md_units', 'fp_net_sales',
               'md_net_sales', 'md_share_of_net_pct', 'return_units',
               'return_value']
    rows = list(data['daily'])
    total = {
        'date': 'TOTAL', 'weekday': '',
        **{k: sum(r[k] for r in rows) for k in
           ('bills', 'units_sold', 'net_sales', 'fp_units', 'md_units',
            'fp_net_sales', 'md_net_sales', 'return_units', 'return_value')},
    }
    total['avg_bill_value'] = (_r(total['net_sales'] / total['bills'])
                               if total['bills'] else 0)
    total['md_share_of_net_pct'] = _pct(total['md_net_sales'], total['net_sales'])
    rows.append(total)
    _table(ws, headers, rows,
           money_cols={'net_sales', 'avg_bill_value', 'fp_net_sales',
                       'md_net_sales', 'return_value'},
           pct_cols={'md_share_of_net_pct'},
           start_row=1)
    for r in range(2, ws.max_row + 1):
        if isinstance(ws.cell(row=r, column=1).value, date):
            ws.cell(row=r, column=1).number_format = 'YYYY-MM-DD'
    ws.freeze_panes = 'A2'
    _autosize(ws)

    # --- FP vs MD ----------------------------------------------------
    ws = wb.create_sheet('FP vs MD')
    row = _table(
        ws,
        ['bucket', 'units', 'units_pct', 'net_sales', 'net_sales_pct',
         'gross_list_value', 'avg_selling_price', 'avg_discount_rate_pct'],
        data['fpmd'],
        money_cols={'units', 'net_sales', 'gross_list_value', 'avg_selling_price'},
        pct_cols={'units_pct', 'net_sales_pct', 'avg_discount_rate_pct'},
        start_row=1,
    )
    ws.cell(row=row, column=1,
            value='Discount-band detail (item discount rate)').font = Font(bold=True)
    row += 1
    _table(
        ws,
        ['discount_band', 'side', 'lines', 'units', 'units_pct', 'net_sales',
         'net_sales_pct', 'gross_list_value', 'avg_selling_price'],
        data['bands'],
        money_cols={'units', 'net_sales', 'gross_list_value', 'avg_selling_price'},
        pct_cols={'units_pct', 'net_sales_pct'},
        start_row=row,
    )
    _autosize(ws, cap=38)

    # --- Customers ---------------------------------------------------
    ws = wb.create_sheet('Customers')
    headers = ['segment', 'customers', 'bills', 'units', 'net_sales',
               'net_sales_pct', 'avg_spend_per_customer', 'avg_bill_value', 'note']
    money = {'units', 'net_sales', 'avg_spend_per_customer', 'avg_bill_value'}
    row = _table(ws, headers, data['customers'], money_cols=money,
                 pct_cols={'net_sales_pct'}, start_row=1)
    ws.cell(row=row, column=1,
            value='Same customers, scored against THIS store only').font = Font(bold=True)
    row += 1
    ws.cell(row=row, column=1,
            value='(identifiable customers only — walk-ins have no history to score)')
    ws.cell(row=row, column=1).font = Font(italic=True, size=9)
    row += 1
    _table(ws, headers, data['customers_here'], money_cols=money,
           pct_cols={'net_sales_pct'}, start_row=row)
    _autosize(ws, cap=44)
    ws.column_dimensions['A'].width = 42

    # --- Top lines ---------------------------------------------------
    ws = wb.create_sheet('Top lines')
    ws['A1'] = ('The 15 largest lines by absolute net value, sales and returns '
                'together — so a period carried by one piece is visible.')
    ws['A1'].font = Font(italic=True, size=9)
    _table(
        ws,
        ['date', 'line_type', 'brand', 'product_code', 'description', 'qty',
         'net_sales', 'pct_of_net_sales', 'item_discount_pct'],
        data['top_lines'],
        money_cols={'net_sales'},
        pct_cols={'pct_of_net_sales', 'item_discount_pct'},
        start_row=3,
    )
    for r in range(4, ws.max_row + 1):
        if isinstance(ws.cell(row=r, column=1).value, date):
            ws.cell(row=r, column=1).number_format = 'YYYY-MM-DD'
    _autosize(ws, cap=34)

    # --- Metadata ----------------------------------------------------
    meta = wb.create_sheet('Metadata')
    meta.append(['Parameter', 'Value'])
    _style_header(meta)
    for label, value in [
        ('store_code', code),
        ('store_name', store_name),
        ('start_date', start_date),
        ('end_date (inclusive)', end_date),
        ('as_of_date (report run)', as_of.isoformat()),
        ('bill filter', 'd.STATUS = 4 AND d.RECEIPT_TYPE IN (0, 1)'),
        ('sale lines', 'di.ITEM_TYPE = 1'),
        ('return lines', 'di.ITEM_TYPE = 2, posted inside the period'),
        ('net sales basis',
         'ex-VAT: (di.PRICE - di.TAX_AMT) * di.QTY * (1 - d.DISC_PERC/100). '
         'di.PRICE is already net of the item discount, so di.DISC_PERC is '
         'NOT re-applied (would double-discount).'),
        ('gross list value',
         'ex-VAT pre-discount: (di.ORIG_PRICE - di.ORIG_TAX_AMT) * di.QTY'),
        ('VAT treatment',
         'actual per-line tax (data carries mixed 8% / 10% VAT) — NOT the flat '
         '10% the commission calc uses'),
        ('FP / MD rule',
         'Full Price when the ITEM discount is <= 30% '
         '(di.PRICE >= 0.7 * di.ORIG_PRICE); exactly 30% counts as FP. '
         'Markdown is the residual, so FP + MD == total exactly.'),
        ('returns treatment',
         'reported as their own line, not netted into net_sales. The live '
         "module's 30-day returns tail is not used — the period is too short "
         'for it to mean anything.'),
        ('new customer',
         'first-ever sale line (any store, all history) falls inside the period'),
        ('returning customer', 'first-ever sale line predates the period'),
        ('walk-in / tourist',
         'the POS TOURIST aggregate record or a NULL customer. One bill = one '
         'walk-in, since one TOURIST record stands in for many people. Real '
         'revenue, no identity, so excluded from new/returning.'),
        ('excluded', 'SYSADMIN system account — not a real sale'),
        ('first visit to this store',
         "second customer block only: the customer's first-ever sale AT THIS "
         'STORE falls inside the period. A customer can be Returning globally '
         'and a first-time visitor here.'),
        ('concentration figures',
         'largest single line / top 5 lines as a share of net sales. A 4-day '
         'window at this price point can be carried by one piece, which makes '
         'the FP/MD and customer percentages a statement about that piece '
         'rather than about the store. See the Top lines sheet.'),
        ('rounding', 'half-up (matching Oracle), not Python banker\'s rounding'),
        ('reconciliation',
         'asserted at build time against the unrounded totals: daily, '
         'discount bands and customer buckets each re-add to net sales. '
         'FP + MD is exact by construction. Individual cells are rounded to '
         'whole VND, so a column can read 1-2 VND off the headline.'),
    ]:
        meta.append([label, value])
    meta.column_dimensions['A'].width = 32
    meta.column_dimensions['B'].width = 96
    for r in range(2, meta.max_row + 1):
        meta.cell(row=r, column=2).alignment = Alignment(wrap_text=True,
                                                         vertical='top')

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


# =========================================================================

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--start-date', required=True, help='YYYY-MM-DD (inclusive)')
    parser.add_argument('--end-date', required=True, help='YYYY-MM-DD (inclusive)')
    parser.add_argument('--stores', default='RWR,RWP',
                        help='Comma-separated STORE_CODEs; one workbook each '
                             '(default: RWR,RWP)')
    parser.add_argument('--out-dir', default='document/reports')
    args = parser.parse_args()

    codes = [s.strip().upper() for s in args.stores.split(',') if s.strip()]
    if not codes:
        raise SystemExit('No store codes given')
    # Fail on a bad date before touching the heavy queries.
    date.fromisoformat(args.start_date)
    date.fromisoformat(args.end_date)
    if args.end_date < args.start_date:
        raise SystemExit('--end-date is before --start-date')

    as_of = date.today()
    names = resolve_stores(codes)

    written = []
    for n, code in enumerate(codes, start=1):
        print(f'[{n}/{len(codes)}] {code} — {names[code]} ...')
        data = build_store(code, args.start_date, args.end_date)
        p = data['perf']
        print(f'      bills {p["bills"]:,}  units {p["units_sold"]:,}  '
              f'net {p["net_sales"]:,} VND')
        out_path = (Path(args.out_dir)
                    / f'store_performance_{code}_{args.start_date}_{args.end_date}.xlsx')
        write_store_xlsx(data, out_path, names[code],
                         args.start_date, args.end_date, as_of)
        written.append((code, out_path, data))

    print('\nDone. Written:')
    for code, path, data in written:
        print(f'  {code}: {path.resolve()}')


if __name__ == '__main__':
    main()
