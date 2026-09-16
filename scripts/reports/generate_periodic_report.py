"""
Periodic sales Excel report: week-over-week, plus MTD / YTD against last year.

Intended to be run weekly. Each of the three windows shows a current period
against its comparison period, broken out by store and merchandise department,
plus a raw day-grain sheet the periods are derived from.

Periods (`--as-of` defaults to today)
-------------------------------------
  WOW  "Week by Week" — the LAST COMPLETE week against THE WEEK BEFORE IT.
       Run in week 38 and it reports week 37 vs week 36, Monday to Sunday
       (`--week-start sunday` shifts the week boundary). The partial current
       week is never included, so both sides are always whole weeks.
       This window does NOT compare against last year; MTD and YTD do.
  MTD  1st of month .. as-of (inclusive)
  YTD  1st of January .. as-of (inclusive)

Comparison windows differ by sheet:

- **WOW compares consecutive weeks** — a flat 7-day shift back, so the two sides
  are adjacent whole weeks on the same weekdays. No ISO-week or leap-year
  handling is needed. An earlier revision compared the same ISO week one year
  earlier; that was replaced because the week sheet is for reading momentum
  week to week, which a year-ago week cannot show.
- **MTD and YTD are calendar-date aligned against the PRIOR YEAR** — the
  identical month/day range one year earlier. Feb 29 in a leap-year `as-of`
  maps to Feb 28 of the prior (non-leap) year.

`--week-start sunday` shifts the week boundary, but week *numbering* in labels
stays ISO (Monday-based), so a Sunday-start week is labelled by the ISO week
containing its start date.

Departments
-----------
`DCS.D_LONG_NAME` carries gender-prefixed category codes (WRTW, MRTW, WBAG, …).
Women and Men are merged **per category** — the gender prefix is dropped and the
two fold into one row:

    WRTW + MRTW -> RTW      WACC + MACC -> ACC
    WBAG + MBAG -> BAG      WJEW + MJEW -> JEW
    WSHO + MSHO -> SHO

Every other code (KACC, KRTW, COSM, HOME) is passed through unchanged. A line
whose DCS_CODE does not resolve is bucketed as `UNKNOWN` rather than dropped, so
department rows always reconcile to the store total.

Metrics
-------
  Total sales            net ex-tax revenue, returns netted within the period
  Total quantity sold    Σ qty on sale lines (returns NOT deducted)
  Total bill quantity    distinct sale documents
  Average unit price     sale revenue / quantity sold
  Average transaction    sale revenue / bill quantity
  Total discount value   gross (list) − net, on sale lines
  Average discount %     100 × (gross − net) / gross, value-weighted

Every money column on the three period sheets is DISPLAYED in millions of VND
(format `#,##0,,`, rounded to the nearest million) and labelled "(VND m)". The
rounding is presentational only — the cell still holds the exact Decimal, so
sub-million remainders survive and any formula over those cells stays exact.
The `Raw Data` sheet deliberately stays in whole VND: its day x store x
department rows are often below a million, which would render as "0".

Each period sheet carries three blocks — every metric for the current period,
then the same metrics for the prior period, then the percentage change. The
absolute delta is not shown; for `Avg discount %` the change is a
percentage-POINT gap (`pp`) rather than a ratio of two ratios.

Revenue formulas are taken verbatim from `app/modules/reports/queries.py` (CR
#78-#85) so this report reconciles with the live-comparison feature:
  net line   = (di.PRICE − di.TAX_AMT) × di.QTY × (1 − d.DISC_PERC/100)
  gross line = (di.ORIG_PRICE − di.ORIG_TAX_AMT) × di.QTY
`di.PRICE` is ALREADY net of the item discount — re-applying `di.DISC_PERC`
double-discounts (the CR #81 bug). `di.TAX_AMT` is the real per-line tax (data
carries mixed 8%/10% VAT), so the `price / 1.1` shortcut used by commission is
deliberately not used here.

Deliberate divergences from `app/modules/reports/queries.py`
-----------------------------------------------------------
1. Returns are netted **within the period itself**, not over the module's flat
   30-day tail. A tail would be asymmetric here: the current window ends at or
   near today and so has little or no tail available, while the comparison
   window would get its full 30 days — biasing every delta.
2. `d.STATUS = 4 AND d.RECEIPT_TYPE IN (0, 1)` are applied, matching the sibling
   scripts in this directory. Verified 2026-09-09: 100% of 2026 YTD sale lines
   are already STATUS=4 / RECEIPT_TYPE=0, so this is currently a no-op and the
   two implementations agree on live data.

Caveat — department bill counts do not sum
------------------------------------------
One bill can contain lines from several departments, so a department's
"Total bill quantity" counts *bills containing that department*. Summing the
department rows therefore over-counts. Store and grand totals use a separate
document-grain count and are the authoritative bill figures. The same applies to
average transaction value on department rows.

Output: document/reports/periodic_report_<as_of>.xlsx
"""
import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import oracledb

from app.core.database import get_oracle_connection

ZERO = Decimal(0)


def _decimal_output_handler(cursor, name, default_type, size, precision, scale):
    """Return Oracle NUMBERs as Decimal instead of float.

    Money is summed across thousands of day x store x department rows here. In
    float64 that accumulates ~1e-12 relative drift, which showed up as a
    sub-VND disagreement with a single-shot query over the same range. Oracle's
    NUMBER is exact decimal, so keeping it exact all the way to the cell makes
    the report reconcile to the unit.
    """
    if default_type == oracledb.DB_TYPE_NUMBER:
        return cursor.var(Decimal, arraysize=cursor.arraysize)
    return None


# --- Shared line expressions (see module docstring) -------------------------
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
        -- range. Rounding happens once, at presentation, via number formats.
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


# --- Department merge -------------------------------------------------------
# Women/Men pairs collapse to their shared category; everything else passes
# through. Explicit rather than "strip a leading W/M" so a future code such as
# MISC or WHSE is never silently mangled.
DEPT_MERGE = {
    'WRTW': 'RTW', 'MRTW': 'RTW',
    'WBAG': 'BAG', 'MBAG': 'BAG',
    'WSHO': 'SHO', 'MSHO': 'SHO',
    'WACC': 'ACC', 'MACC': 'ACC',
    'WJEW': 'JEW', 'MJEW': 'JEW',
}

UNKNOWN_DEPT = 'UNKNOWN'


def merge_department(raw):
    """Map a raw DCS.D_LONG_NAME onto its reporting department."""
    if raw is None:
        return UNKNOWN_DEPT
    code = str(raw).strip().upper()
    if not code:
        return UNKNOWN_DEPT
    return DEPT_MERGE.get(code, code)


# --- Period maths -----------------------------------------------------------
def shift_year(d, years=1):
    """Same calendar date `years` earlier, folding Feb 29 back to Feb 28."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:          # 29 Feb -> non-leap year
        return d.replace(year=d.year - years, month=2, day=28)


def week_label(start):
    """'Week 37 2026' for the ISO week containing `start`."""
    iso = start.isocalendar()
    return f'Week {iso.week} {iso.year}'


def previous_week_window(cur_start, cur_end):
    """The week immediately before [cur_start, cur_end] — a flat 7-day shift.

    The week sheet is week-over-week: the latest full week against the one
    before it. A plain 7-day shift needs no ISO-week or leap handling, always
    lands on the same weekdays, and never straddles a week boundary.
    """
    shift = timedelta(days=7)
    return cur_start - shift, cur_end - shift


def period_windows(as_of, week_start='monday'):
    """Return {label: ((cur_from, cur_to), (prior_from, prior_to))} inclusive.

    WOW compares the latest full week with the week before it; MTD and YTD
    compare against the prior YEAR, calendar-date aligned (see the docstring).
    """
    if week_start == 'sunday':
        # Monday=0 .. Sunday=6  ->  days since the most recent Sunday.
        back = (as_of.weekday() + 1) % 7
    else:
        back = as_of.weekday()

    # The week window is the LAST COMPLETE week, not the partial current one:
    # running in week 38 reports week 37. A part-week is not comparable against
    # a full prior-year week, and the report is produced weekly to review the
    # week that just closed. The current week is always excluded, so a run on
    # the final day of a week still reports the week before it.
    current_week_start = as_of - timedelta(days=back)
    week_end = current_week_start - timedelta(days=1)
    week_begin = week_end - timedelta(days=6)
    windows = {
        'WOW': ((week_begin, week_end), previous_week_window(week_begin, week_end)),
    }
    for label, start in (('MTD', as_of.replace(day=1)),
                         ('YTD', as_of.replace(month=1, day=1))):
        windows[label] = ((start, as_of), (shift_year(start), shift_year(as_of)))
    return windows


# --- Fetch ------------------------------------------------------------------
def fetch(from_date, to_date):
    """Pull both grains over [from_date, to_date] inclusive."""
    binds = {'from_date': from_date, 'to_exclusive': to_date + timedelta(days=1)}

    conn = get_oracle_connection()
    try:
        conn.outputtypehandler = _decimal_output_handler
        cur = conn.cursor()

        cur.execute(QUERY_BY_DEPT, binds)
        dept_rows = []
        for day, store_code, store_name, dept, s_net, r_net, s_gross, qty, qty_ret, bills in cur:
            dept_rows.append({
                'day': day.date() if hasattr(day, 'date') else day,
                'store_code': store_code,
                'store_name': store_name,
                'department': merge_department(dept),
                'sale_net': s_net or ZERO,
                'return_net': r_net or ZERO,
                'sale_gross': s_gross or ZERO,
                'qty_sold': qty or ZERO,
                'qty_returned': qty_ret or ZERO,
                'dept_bills': int(bills or 0),
            })

        cur.execute(QUERY_BY_STORE, binds)
        store_rows = []
        for day, store_code, bills in cur:
            store_rows.append({
                'day': day.date() if hasattr(day, 'date') else day,
                'store_code': store_code,
                'bills': int(bills or 0),
            })

        cur.close()
    finally:
        conn.close()

    return dept_rows, store_rows


# --- Aggregation ------------------------------------------------------------
_COMPONENTS = ('sale_net', 'return_net', 'sale_gross', 'qty_sold', 'qty_returned')


def _blank():
    d = {k: ZERO for k in _COMPONENTS}
    d['bills'] = 0
    return d


def _metrics(acc):
    """Derive the reported metrics from summed components."""
    sale_net = acc['sale_net']
    gross = acc['sale_gross']
    qty = acc['qty_sold']
    bills = acc['bills']
    discount = gross - sale_net
    # `total_discount_value` is no longer a displayed column (the rate says the
    # same thing in a comparable unit), but it is kept here because the rate is
    # derived from it and the raw sheet's gross/net reconcile against it.
    return {
        'total_sales': sale_net - acc['return_net'],
        'returns_value': acc['return_net'],
        'qty_sold': qty,
        'bills': bills,
        'avg_unit_price': (sale_net / qty) if qty else None,
        'avg_transaction_value': (sale_net / bills) if bills else None,
        'total_discount_value': discount,
        'avg_discount_pct': (100 * discount / gross) if gross else None,
    }


def aggregate(dept_rows, store_rows, window):
    """Aggregate one (from, to) window into store x department metrics.

    Returns (by_store_dept, by_store, grand) where keys are
    (store_code, department) and store_code.
    """
    start, end = window

    dept_acc = defaultdict(_blank)
    store_acc = defaultdict(_blank)
    grand = _blank()
    store_names = {}

    for r in dept_rows:
        if not (start <= r['day'] <= end):
            continue
        store_names[r['store_code']] = r['store_name']
        for target in (dept_acc[(r['store_code'], r['department'])],
                       store_acc[r['store_code']],
                       grand):
            for k in _COMPONENTS:
                target[k] += r[k]
        # Department-grain bills only; store/grand bills come from the
        # document-grain query below so they are not over-counted.
        dept_acc[(r['store_code'], r['department'])]['bills'] += r['dept_bills']

    for r in store_rows:
        if not (start <= r['day'] <= end):
            continue
        store_acc[r['store_code']]['bills'] += r['bills']
        grand['bills'] += r['bills']

    return (
        {k: _metrics(v) for k, v in dept_acc.items()},
        {k: _metrics(v) for k, v in store_acc.items()},
        _metrics(grand),
        store_names,
    )


# --- Excel ------------------------------------------------------------------
# Money is displayed in MILLIONS of VND. Each comma at the tail of an Excel
# number format divides the DISPLAYED value by 1000, so two commas render
# 159,745,427,865 as "159,745" -- rounded to the nearest million for display
# only. The cell still holds the exact Decimal, so sub-million remainders are
# preserved and any formula over these cells stays exact.
MONEY_FORMAT = '#,##0,,'        # whole millions -- the aggregate totals
MONEY_1DP_FORMAT = '#,##0.0,,'  # millions to 1dp -- the per-unit averages
COUNT_FORMAT = '#,##0'
PCT_FORMAT = '#,##0.0"%"'
POINT_FORMAT = '#,##0.0"pp"'

# (label, metric key, unit) -- unit drives both the number format and whether
# the delta column is a ratio or a percentage-point gap.
#
# The two per-unit averages use `money1` rather than `money`: they sit around
# 12-36 million, so rounding them to whole millions renders an average unit
# price of 12,286,107 as a bare "12" and makes a move from 12.2m to 12.4m
# invisible. One decimal keeps the same millions unit and restores that signal.
# The aggregate totals are in the tens of thousands of millions, where a decimal
# would be noise.
# Labels are kept short so the columns can be narrow enough to fit both week
# blocks on one screen (see _write_period_sheet). Money labels must keep the
# "(VND m)" suffix -- the delta block strips it, and the unit has to stay
# visible on the two value blocks.
METRIC_COLUMNS = [
    ('Sales (VND m)',      'total_sales',           'money'),
    ('Qty sold',           'qty_sold',              'count'),
    ('Bills',              'bills',                 'count'),
    ('Avg price (VND m)',  'avg_unit_price',        'money1'),
    ('Avg txn (VND m)',    'avg_transaction_value', 'money1'),
    ('Discount %',         'avg_discount_pct',      'pct'),
    ('Returns (VND m)',    'returns_value',         'money'),
]

# Metrics where a FALL is the good outcome. Everything else is "higher is
# better". Without this the delta block would paint a shrinking discount rate
# and shrinking returns red, which is exactly backwards -- both are wins.
LOWER_IS_BETTER = {'avg_discount_pct', 'returns_value'}

# Only unfavourable moves are coloured; favourable ones keep the default text
# colour, so the eye goes straight to the problems.
BAD_COLOUR = 'C00000'

_UNIT_FORMAT = {
    'money': MONEY_FORMAT,
    'money1': MONEY_1DP_FORMAT,
    'count': COUNT_FORMAT,
    'pct': PCT_FORMAT,
}


def _delta(cur, prior):
    if cur is None or prior is None:
        return None, None
    diff = cur - prior
    # Integer 100, not 100.0: these values are Decimal, and Decimal does not
    # support arithmetic with float.
    pct = (100 * diff / abs(prior)) if prior else None
    return diff, pct


# A store x department present in one year but not the other has genuinely zero
# activity on the missing side -- not unknown activity. Substituting this keeps
# the deltas meaningful (a department that opened this year shows its full value
# as the gain) while leaving the ratio metrics undefined, since they divide by a
# zero denominator.
ZERO_METRICS = _metrics(_blank())


def _write_period_sheet(ws, label, windows, cur_data, prior_data):
    from openpyxl.styles import Alignment, Font, PatternFill

    bold = Font(bold=True)
    head_fill = PatternFill('solid', fgColor='DDEBF7')
    total_fill = PatternFill('solid', fgColor='FFF2CC')
    grand_fill = PatternFill('solid', fgColor='E2EFDA')

    (c_from, c_to), (p_from, p_to) = windows

    cur_dept, cur_store, cur_grand, store_names = cur_data
    pri_dept, pri_store, pri_grand, pri_names = prior_data
    store_names = {**pri_names, **store_names}

    if label == 'WOW':
        title = 'Week by Week'
        headline = f'{week_label(c_from)}  vs  {week_label(p_from)}'
        alignment_note = ('Latest COMPLETE week against the week before it (the '
                          'current partial week is excluded). Both sides are whole '
                          'weeks on the same weekdays. This sheet is week-over-week, '
                          'NOT year-over-year — MTD and YTD compare against last year.')
    else:
        title = label
        headline = f'{c_from} to {c_to}  vs  {p_from} to {p_to}'
        alignment_note = 'Prior-year window is calendar-date aligned.'

    ws['A1'] = f'{title} — {headline}'
    ws['A1'].font = Font(bold=True, size=13)
    ws['A2'] = (f'{c_from} ({c_from:%a}) to {c_to} ({c_to:%a})   vs   '
                f'{p_from} ({p_from:%a}) to {p_to} ({p_to:%a})')
    ws['A2'].font = Font(size=10)
    legend = '   '.join(f'{code} = {store_names[code]}'
                        for code in sorted(store_names))
    ws['A4'] = f'Stores:   {legend}'
    ws['A4'].font = Font(italic=True, size=9)

    ws['A3'] = (f'{alignment_note} Money columns are shown in MILLIONS of VND '
                '(display rounding only — cells hold exact values). Department bill '
                'counts count bills CONTAINING that department and do not sum to the '
                'store total; store/grand totals are document-grain.')
    ws['A3'].font = Font(italic=True, size=9)

    # Two header rows. The sheet is laid out in four BLOCKS -- every metric for
    # the current period side by side, then the same metrics for the prior
    # period, then the deltas -- rather than interleaving the four views of each
    # metric. Reading one period straight across is the common case; comparing a
    # single metric across periods is the exception.
    top, sub = 5, 6
    ws.cell(top, 1, 'Store').font = bold
    ws.cell(top, 2, 'Department').font = bold
    ws.merge_cells(start_row=top, start_column=1, end_row=sub, end_column=1)
    ws.merge_cells(start_row=top, start_column=2, end_row=sub, end_column=2)

    if label == 'WOW':
        cur_block = f'Latest week — {week_label(c_from)}'
        pri_block = f'Prior week — {week_label(p_from)}'
    else:
        cur_block = f'Current — {c_from} to {c_to}'
        pri_block = f'Prior year — {p_from} to {p_to}'

    width = len(METRIC_COLUMNS)
    blocks = [
        (cur_block, PatternFill('solid', fgColor='DDEBF7')),
        (pri_block, PatternFill('solid', fgColor='EDEDED')),
        ('Δ %   (red = unfavourable)', PatternFill('solid', fgColor='FFF2CC')),
    ]

    for b, (block_label, fill) in enumerate(blocks):
        start = 3 + b * width
        ws.merge_cells(start_row=top, start_column=start,
                       end_row=top, end_column=start + width - 1)
        c = ws.cell(top, start, block_label)
        c.font = bold
        c.fill = fill
        c.alignment = Alignment(horizontal='center')
        is_delta_block = b == len(blocks) - 1
        for i, (metric_label, _key, unit) in enumerate(METRIC_COLUMNS):
            # The delta block is already all-percentages; repeating "(VND m)"
            # there would misdescribe it.
            text = metric_label.replace(' (VND m)', '') if is_delta_block else metric_label
            sc = ws.cell(sub, start + i, text)
            sc.font = bold
            sc.fill = fill
            sc.alignment = Alignment(horizontal='center', wrap_text=True)

    def emit(row, store_label, dept_label, cur_m, pri_m, fill=None):
        a = ws.cell(row, 1, store_label)
        b = ws.cell(row, 2, dept_label)
        if fill:
            a.fill = b.fill = fill
            a.font = b.font = bold
        cur_m = cur_m or ZERO_METRICS
        pri_m = pri_m or ZERO_METRICS
        for i, (_lbl, key, unit) in enumerate(METRIC_COLUMNS):
            cv = cur_m.get(key)
            pv = pri_m.get(key)
            diff, pct = _delta(cv, pv)
            fmt = _UNIT_FORMAT[unit]
            # For a percentage metric the delta is a percentage-POINT gap, not a
            # ratio of ratios -- a "26.4% vs 26.5%" change is +0.1pp, and
            # expressing that as +0.4% would invite the wrong reading.
            cells = (
                (cv, fmt),
                (pv, fmt),
                (diff, POINT_FORMAT) if unit == 'pct' else (pct, PCT_FORMAT),
            )
            for b, (val, vfmt) in enumerate(cells):
                cell = ws.cell(row, 3 + b * len(METRIC_COLUMNS) + i, val)
                cell.number_format = vfmt
                # Red only in the delta block, and only for a move in the
                # unfavourable direction for THAT metric.
                bad = False
                if b == len(blocks) - 1 and val is not None and val != 0:
                    bad = (val > 0) if key in LOWER_IS_BETTER else (val < 0)
                if fill:
                    cell.fill = fill
                    cell.font = Font(bold=True, color=BAD_COLOUR) if bad else bold
                elif bad:
                    cell.font = Font(color=BAD_COLOUR)
        return row + 1

    # Two-level Excel outline:
    #   level 1 (the "1" button, and how the file opens) -- store TOTAL rows
    #                                                       plus the grand total
    #   level 2 (the "2" button)                         -- every department row
    # Department rows sit ABOVE their store's TOTAL, so summaryBelow must stay
    # True (openpyxl's default) for the +/- control to attach to the TOTAL row.
    ws.sheet_properties.outlinePr.summaryBelow = True
    ws.sheet_properties.outlinePr.applyStyles = False

    row = sub + 1
    all_stores = sorted(set(cur_store) | set(pri_store))
    for store in all_stores:
        depts = sorted({d for (s, d) in cur_dept if s == store} |
                       {d for (s, d) in pri_dept if s == store})
        first_detail = row
        for dept in depts:
            row = emit(row, store, dept,
                       cur_dept.get((store, dept)), pri_dept.get((store, dept)))
        last_detail = row - 1
        total_row = row
        row = emit(row, store, 'TOTAL',
                   cur_store.get(store), pri_store.get(store), total_fill)

        # Group this store's department rows and start them collapsed, so the
        # sheet opens on the store view. `collapsed` belongs on the summary row
        # (the TOTAL), not on the hidden detail rows.
        for r in range(first_detail, last_detail + 1):
            ws.row_dimensions[r].outlineLevel = 1
            ws.row_dimensions[r].hidden = True
        if depts:
            ws.row_dimensions[total_row].collapsed = True

        row += 1   # spacer between stores, deliberately left at level 0

    emit(row, 'ALL STORES', 'TOTAL', cur_grand, pri_grand, grand_fill)

    from openpyxl.utils import get_column_letter

    # Sized so ALL THREE blocks (latest, prior, delta) fit on screen without
    # horizontal scrolling. Widths are per metric and measured from rendered
    # content rather than set uniformly: the widest value actually produced is
    # 7 chars for money, 6 for qty, 5 for bills, and 8 in the delta block
    # ("1,050.0%"). Each width carries ~2 chars of headroom on top of that so
    # larger figures in future periods do not clip.
    #   px = round(width * 7) + 5
    #   A(11) + B(10) + 2 value blocks(455px each) + delta block(476px)
    #   = 82 + 75 + 910 + 476 = 1543px at 100% zoom
    # Reference point: a 15" MacBook at default scaling gives ~1380px of grid,
    # which fits the two value blocks; the wider layouts (16" / "More Space")
    # give ~1700px+ and fit the delta block too.
    STORE_W, DEPT_W, ZOOM = 11, 10, 100
    VALUE_W = {
        'total_sales':           10,
        'qty_sold':               8,
        'bills':                  7,
        'avg_unit_price':         9,
        'avg_transaction_value':  9,
        'avg_discount_pct':       8,
        'returns_value':          9,
    }
    DELTA_W = 9   # the whole delta block is percentages -- one width suffices

    ws.freeze_panes = 'C7'
    ws.column_dimensions['A'].width = STORE_W
    ws.column_dimensions['B'].width = DEPT_W
    for b in range(len(blocks)):
        is_delta = b == len(blocks) - 1
        for i, (_lbl, key, _unit) in enumerate(METRIC_COLUMNS):
            col_letter = get_column_letter(3 + b * width + i)
            ws.column_dimensions[col_letter].width = (
                DELTA_W if is_delta else VALUE_W[key])
    # Narrow columns push the wrapped headers onto two or three lines.
    ws.row_dimensions[sub].height = 44
    ws.sheet_view.zoomScale = ZOOM


def _write_raw_sheet(ws, dept_rows):
    from openpyxl.styles import Font

    # Whole VND here, not millions: a single day x store x department row is
    # often under a million and would round to "0".
    headers = ['Day', 'Store code', 'Store name', 'Department',
               'Sale net (VND, ex-VAT)', 'Return net (VND, ex-VAT)',
               'Sale gross (VND, list, ex-VAT)',
               'Qty sold', 'Qty returned', 'Bills containing dept']
    for i, h in enumerate(headers, start=1):
        c = ws.cell(1, i, h)
        c.font = Font(bold=True)

    for r, row in enumerate(sorted(dept_rows,
                                   key=lambda x: (x['day'], x['store_code'], x['department'])),
                            start=2):
        ws.cell(r, 1, row['day']).number_format = 'yyyy-mm-dd'
        ws.cell(r, 2, row['store_code'])
        ws.cell(r, 3, row['store_name'])
        ws.cell(r, 4, row['department'])
        for i, key in enumerate(('sale_net', 'return_net', 'sale_gross'), start=5):
            ws.cell(r, i, row[key]).number_format = '#,##0'
        ws.cell(r, 8, row['qty_sold']).number_format = '#,##0'
        ws.cell(r, 9, row['qty_returned']).number_format = '#,##0'
        ws.cell(r, 10, row['dept_bills']).number_format = '#,##0'

    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions
    for col, width in zip('ABCDEFGHIJ', (12, 11, 26, 13, 20, 20, 24, 11, 13, 21)):
        ws.column_dimensions[col].width = width


def build_workbook(path, as_of, windows, dept_rows, store_rows):
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)

    # Internal keys stay short; the tab shows the human name.
    SHEET_NAMES = {'WOW': 'Week by Week', 'MTD': 'MTD', 'YTD': 'YTD'}
    for label in ('WOW', 'MTD', 'YTD'):
        cur_win, prior_win = windows[label]
        ws = wb.create_sheet(SHEET_NAMES[label])
        _write_period_sheet(
            ws, label, windows[label],
            aggregate(dept_rows, store_rows, cur_win),
            aggregate(dept_rows, store_rows, prior_win),
        )

    _write_raw_sheet(wb.create_sheet('Raw Data'), dept_rows)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


# --- Entry point ------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--as-of', default=None,
                        help='Period end date, YYYY-MM-DD (default: today)')
    parser.add_argument('--week-start', choices=('monday', 'sunday'), default='monday',
                        help='First day of the week (default: monday)')
    parser.add_argument('--out-dir', default='document/reports',
                        help='Output directory (default: document/reports)')
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    windows = period_windows(as_of, args.week_start)

    # One fetch covering every window. Usually that is prior-year Jan 1, but an
    # the WOW comparison week can start BEFORE it: run on 2026-01-05 and the
    # prior week begins 2025-12-22. Take the earliest start of any window
    # rather than assuming YTD is it, or those days silently read as zero.
    fetch_from = min(start for w in windows.values() for start, _end in w)
    fetch_to = as_of
    print(f'as-of {as_of} (week starts {args.week_start})')
    for label in ('WOW', 'MTD', 'YTD'):
        (cf, ct), (pf, pt) = windows[label]
        suffix = (f'   [{week_label(cf)} vs {week_label(pf)}, week over week]'
                  if label == 'WOW' else '')
        print(f'  {label}: {cf} .. {ct}   vs   {pf} .. {pt}{suffix}')
    print(f'fetching {fetch_from} .. {fetch_to} ...')

    dept_rows, store_rows = fetch(fetch_from, fetch_to)
    print(f'  -> {len(dept_rows)} day x store x dept rows, '
          f'{len(store_rows)} day x store rows')

    out = Path(args.out_dir) / f'periodic_report_{as_of}.xlsx'
    build_workbook(out, as_of, windows, dept_rows, store_rows)
    print(f'wrote {out}')


if __name__ == '__main__':
    main()
