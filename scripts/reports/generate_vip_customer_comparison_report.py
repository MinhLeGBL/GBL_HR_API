"""
Custom Excel report: VIP customer comparison across three periods.

Periods compared side by side:
  H1 2025  — 2025-01-01 .. 2025-06-30
  H1 2026  — 2026-01-01 .. 2026-06-30
  FY 2025  — 2025-01-01 .. 2025-12-31

VIP definition
  CUSTOMER.INFO1 (membership tier) IN ('GOLD', 'DIAMOND').
  INFO1 is the tier field POS writes; observed values are
  MEMBER / DIAMOND / GOLD / STAFF / NULL. It is a CURRENT snapshot —
  Oracle keeps no tier history, so a customer who was upgraded to Gold in
  2026 counts as VIP in the 2025 columns too. Flagged in Metadata.

Customer base (the denominator for "% of qty")
  distinct real customers with >= 1 sale receipt in the period
  + tourist DOCUMENT count (the Tourist / Tourist. POS buckets are single
    aggregation records, so their bill count stands in for headcount)

Revenue
  Net revenue BEFORE VAT, returns netted:
      signed_qty * (di.price - di.tax_amt) * (1 - d.DISC_PERC/100)
  di.price is already net of the LINE discount; the bill-level discount
  (d.DISC_PERC) still has to be applied on top.

FP / MD split
  combined discount = 1 - (1 - di.DISC_PERC/100) * (1 - d.DISC_PERC/100)
  FP = combined <= 30%, MD = combined > 30%  (same threshold commission uses)

New customer
  First-ever sale receipt (whole history, not just the window) falls inside
  the period. Tourist buckets excluded.

Filters: d.STATUS = 4, d.receipt_type IN (0, 1), di.ITEM_TYPE IN (1, 2).
Documents with NULL BT_CUID are excluded (2 such bills in the window).

Output: document/reports/vip_customer_comparison_<today>.xlsx
"""
import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

# Allow running this file directly from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_oracle_connection


VIP_TIERS = ('GOLD', 'DIAMOND')

# POS aggregation buckets / system accounts. Tourist rows are kept in the data
# (we need their document count and revenue) but are never treated as people.
TOURIST_NAMES = ('TOURIST', 'TOURIST.')
SYSTEM_NAMES = ('SYSADMIN',)

DISCOUNT_THRESHOLD_PCT = 30.0

PERIODS = [
    ('H1 2025', '2025-01-01', '2025-06-30'),
    ('H1 2026', '2026-01-01', '2026-06-30'),
    ('FY 2025', '2025-01-01', '2025-12-31'),
]

# Per-line net revenue before VAT, returns signed negative, bill discount applied.
_REV_LINE = (
    "(CASE WHEN di.ITEM_TYPE = 2 THEN di.qty * -1 ELSE di.qty END) "
    "* (di.price - NVL(di.tax_amt, 0)) "
    "* (1 - NVL(d.DISC_PERC, 0) / 100)"
)

# Combined line + bill discount percentage for the FP/MD classification.
_COMBINED_DISC = (
    "(1 - (1 - NVL(di.DISC_PERC, 0) / 100) * (1 - NVL(d.DISC_PERC, 0) / 100)) * 100"
)

# Grain: one row per (customer, month). Monthly grain is safe to roll up into
# any of the three periods because a document belongs to exactly one month.
QUERY_MONTHLY = f"""
    SELECT
        d.BT_CUID                                          AS customer_sid,
        NVL(UPPER(TRIM(c.INFO1)), '(NONE)')                AS tier,
        UPPER(TRIM(c.FIRST_NAME))                          AS name_key,
        TO_CHAR(d.invc_post_date, 'YYYY-MM')               AS ym,
        COUNT(DISTINCT CASE WHEN d.receipt_type = 0
                            THEN d.SID END)                AS sale_doc_count,
        ROUND(SUM({_REV_LINE}), 0)                         AS revenue,
        ROUND(SUM(CASE WHEN {_COMBINED_DISC} <= {DISCOUNT_THRESHOLD_PCT}
                       THEN {_REV_LINE} ELSE 0 END), 0)    AS revenue_fp,
        ROUND(SUM(CASE WHEN {_COMBINED_DISC} > {DISCOUNT_THRESHOLD_PCT}
                       THEN {_REV_LINE} ELSE 0 END), 0)    AS revenue_md
    FROM DOCUMENT d
    JOIN DOCUMENT_ITEM di ON di.DOC_SID = d.SID
    JOIN CUSTOMER c       ON c.SID = d.BT_CUID
    WHERE d.STATUS = 4
      AND d.receipt_type IN (0, 1)
      AND di.ITEM_TYPE IN (1, 2)
      AND d.BT_CUID IS NOT NULL
      AND d.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD')
      AND TRUNC(d.invc_post_date) <= TO_DATE(:end_date, 'YYYY-MM-DD')
      AND UPPER(TRIM(c.FIRST_NAME)) NOT IN ('SYSADMIN')
    GROUP BY d.BT_CUID,
             NVL(UPPER(TRIM(c.INFO1)), '(NONE)'),
             UPPER(TRIM(c.FIRST_NAME)),
             TO_CHAR(d.invc_post_date, 'YYYY-MM')
"""

# First-ever sale receipt per customer, across all history (no date filter).
QUERY_FIRST_SALE = """
    SELECT
        d.BT_CUID                          AS customer_sid,
        TRUNC(MIN(d.invc_post_date))       AS first_sale_date
    FROM DOCUMENT d
    WHERE d.STATUS = 4
      AND d.receipt_type = 0
      AND d.BT_CUID IS NOT NULL
    GROUP BY d.BT_CUID
"""


def _fetch(query, params=None):
    conn = get_oracle_connection()
    if conn is None:
        raise RuntimeError('Could not open Oracle connection')
    try:
        cur = conn.cursor()
        try:
            cur.execute(query, params or {})
            cols = [d[0].lower() for d in cur.description]
            rows = cur.fetchall()
        finally:
            cur.close()
    finally:
        conn.close()
    return [dict(zip(cols, r)) for r in rows]


def fetch_monthly(start_date: str, end_date: str):
    return _fetch(QUERY_MONTHLY, {'start_date': start_date, 'end_date': end_date})


def fetch_first_sale():
    rows = _fetch(QUERY_FIRST_SALE)
    out = {}
    for r in rows:
        d = r['first_sale_date']
        out[r['customer_sid']] = d.date() if hasattr(d, 'date') else d
    return out


def _in_period(ym: str, start: str, end: str) -> bool:
    """Month 'YYYY-MM' overlaps the period. All periods are month-aligned."""
    return start[:7] <= ym <= end[:7]


def _pct(part, whole):
    return round(part / whole * 100, 2) if whole else 0.0


def _blank_bucket():
    return {'revenue': 0, 'fp': 0, 'md': 0}


def _add(bucket, row):
    bucket['revenue'] += row['revenue'] or 0
    bucket['fp'] += row['revenue_fp'] or 0
    bucket['md'] += row['revenue_md'] or 0


def compute_period(rows, first_sale, label, start, end):
    """Roll monthly rows into the metric set for one period."""
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)

    total = _blank_bucket()
    vip = _blank_bucket()
    new = _blank_bucket()
    by_tier = defaultdict(_blank_bucket)          # GOLD / DIAMOND detail
    tourist = _blank_bucket()

    real_customers = set()
    vip_customers = set()
    tier_customers = defaultdict(set)
    new_customers = set()
    tourist_doc_count = 0

    for r in rows:
        if not _in_period(r['ym'], start, end):
            continue

        sid = r['customer_sid']
        is_tourist = r['name_key'] in TOURIST_NAMES
        is_vip = r['tier'] in VIP_TIERS and not is_tourist

        _add(total, r)

        if is_tourist:
            tourist_doc_count += r['sale_doc_count'] or 0
            _add(tourist, r)
            continue

        # A customer "counts" for the period only with a real sale receipt;
        # a return-only month must not create headcount.
        if r['sale_doc_count']:
            real_customers.add(sid)

        if is_vip:
            _add(vip, r)
            _add(by_tier[r['tier']], r)
            if r['sale_doc_count']:
                vip_customers.add(sid)
                tier_customers[r['tier']].add(sid)

        fs = first_sale.get(sid)
        if fs and start_d <= fs <= end_d:
            _add(new, r)
            if r['sale_doc_count']:
                new_customers.add(sid)

    customer_base = len(real_customers) + tourist_doc_count

    def tier_block(tier):
        b = by_tier[tier]
        return {
            f'{tier.lower()}_customers': len(tier_customers[tier]),
            f'{tier.lower()}_revenue': b['revenue'],
        }

    result = {
        'label': label,
        'start': start,
        'end': end,

        'real_customers': len(real_customers),
        'tourist_documents': tourist_doc_count,
        'customer_base': customer_base,

        'vip_customers': len(vip_customers),
        'vip_customer_pct': _pct(len(vip_customers), customer_base),

        'total_revenue': total['revenue'],
        'vip_revenue': vip['revenue'],
        'vip_revenue_pct': _pct(vip['revenue'], total['revenue']),
        'vip_fp_revenue': vip['fp'],
        'vip_md_revenue': vip['md'],
        'vip_fp_pct': _pct(vip['fp'], vip['revenue']),
        'vip_md_pct': _pct(vip['md'], vip['revenue']),

        'new_customers': len(new_customers),
        'new_customer_pct': _pct(len(new_customers), customer_base),
        'new_revenue': new['revenue'],
        'new_revenue_pct': _pct(new['revenue'], total['revenue']),
        'new_fp_revenue': new['fp'],
        'new_md_revenue': new['md'],
        'new_fp_pct': _pct(new['fp'], new['revenue']),
        'new_md_pct': _pct(new['md'], new['revenue']),

        'tourist_revenue': tourist['revenue'],
    }
    result.update(tier_block('GOLD'))
    result.update(tier_block('DIAMOND'))
    return result


# (section, metric label, result key, number format)
INT = '#,##0'
PCT = '0.00"%"'
SECTION = None

REPORT_ROWS = [
    ('CUSTOMER BASE', None, None),
    ('Real customers (distinct, with a sale)', 'real_customers', INT),
    ('Tourist documents (qty proxy)', 'tourist_documents', INT),
    ('Total customer base', 'customer_base', INT),

    ('VIP — QTY', None, None),
    ('VIP customers (Gold + Diamond)', 'vip_customers', INT),
    ('VIP % of customer base', 'vip_customer_pct', PCT),
    ('  of which Gold', 'gold_customers', INT),
    ('  of which Diamond', 'diamond_customers', INT),

    ('VIP — REVENUE', None, None),
    ('Total revenue (before VAT)', 'total_revenue', INT),
    ('VIP revenue', 'vip_revenue', INT),
    ('VIP % of total revenue', 'vip_revenue_pct', PCT),
    ('  Gold revenue', 'gold_revenue', INT),
    ('  Diamond revenue', 'diamond_revenue', INT),

    ('VIP — FP / MD SPLIT', None, None),
    ('VIP FP revenue', 'vip_fp_revenue', INT),
    ('VIP MD revenue', 'vip_md_revenue', INT),
    ('VIP FP % of VIP revenue', 'vip_fp_pct', PCT),
    ('VIP MD % of VIP revenue', 'vip_md_pct', PCT),

    ('NEW CUSTOMERS', None, None),
    ('New customers (1st-time buyer in period)', 'new_customers', INT),
    ('New % of customer base', 'new_customer_pct', PCT),
    ('New customer revenue', 'new_revenue', INT),
    ('New % of total revenue', 'new_revenue_pct', PCT),
    ('New FP revenue', 'new_fp_revenue', INT),
    ('New MD revenue', 'new_md_revenue', INT),
    ('New FP % of new revenue', 'new_fp_pct', PCT),
    ('New MD % of new revenue', 'new_md_pct', PCT),

    ('REFERENCE', None, None),
    ('Tourist revenue', 'tourist_revenue', INT),
]


def write_xlsx(results, out_path: Path, as_of: date):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='305496', end_color='305496', fill_type='solid')
    section_font = Font(bold=True, color='1F3864')
    section_fill = PatternFill(start_color='D9E1F2', end_color='D9E1F2', fill_type='solid')

    wb = Workbook()
    ws = wb.active
    ws.title = 'VIP Comparison'

    ws.append(['Metric'] + [r['label'] for r in results])
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')

    for label, key, fmt in REPORT_ROWS:
        if key is None:
            ws.append([label] + [''] * len(results))
            for cell in ws[ws.max_row]:
                cell.font = section_font
                cell.fill = section_fill
            continue
        ws.append([label] + [r[key] for r in results])
        for col_idx in range(2, len(results) + 2):
            ws.cell(row=ws.max_row, column=col_idx).number_format = fmt

    ws.freeze_panes = 'B2'
    ws.column_dimensions['A'].width = 42
    for i in range(len(results)):
        ws.column_dimensions[get_column_letter(i + 2)].width = 18

    # ---- Metadata ----
    meta = wb.create_sheet('Metadata')
    meta.append(['Parameter', 'Value'])
    for cell in meta[1]:
        cell.font = header_font
        cell.fill = header_fill
    for r in results:
        meta.append([f'period: {r["label"]}', f'{r["start"]} .. {r["end"]}'])
    meta.append(['as_of_date', as_of.isoformat()])
    meta.append(['VIP definition', "CUSTOMER.INFO1 IN ('GOLD', 'DIAMOND')"])
    meta.append(['tier snapshot caveat',
                 'INFO1 is the CURRENT tier — Oracle keeps no tier history, so 2025 '
                 'columns use 2026 tier membership. Treat cross-period VIP counts as '
                 'directional, not audited.'])
    meta.append(['customer base',
                 'distinct real customers with >=1 sale receipt + Tourist document count'])
    meta.append(['tourist handling',
                 'Tourist / Tourist. are POS aggregation buckets — counted by document '
                 'count, never as headcount; their revenue is in Total revenue'])
    meta.append(['revenue',
                 'before VAT, returns netted: signed_qty * (di.price - di.tax_amt) '
                 '* (1 - d.DISC_PERC/100)'])
    meta.append(['FP / MD',
                 'combined = 1 - (1 - di.DISC_PERC/100) * (1 - d.DISC_PERC/100); '
                 f'FP <= {DISCOUNT_THRESHOLD_PCT:.0f}%, MD > {DISCOUNT_THRESHOLD_PCT:.0f}%'])
    meta.append(['new customer',
                 'first-ever sale receipt (all history, receipt_type=0) falls in the period'])
    meta.append(['filters', 'd.STATUS=4, d.receipt_type IN (0,1), di.ITEM_TYPE IN (1,2)'])
    meta.append(['excluded', 'SYSADMIN; documents with NULL BT_CUID'])
    meta.column_dimensions['A'].width = 30
    meta.column_dimensions['B'].width = 95
    for row in meta.iter_rows(min_row=2, min_col=2, max_col=2):
        row[0].alignment = Alignment(wrap_text=True, vertical='top')

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--out-dir', default='document/reports',
                        help='Output directory (default: document/reports)')
    args = parser.parse_args()

    as_of = date.today()
    window_start = min(p[1] for p in PERIODS)
    window_end = max(p[2] for p in PERIODS)

    print(f'[1/3] Querying monthly customer aggregates ({window_start} .. {window_end}) ...')
    rows = fetch_monthly(window_start, window_end)
    print(f'      -> {len(rows)} (customer, month) rows')

    print('[2/3] Querying first-ever sale date per customer ...')
    first_sale = fetch_first_sale()
    print(f'      -> {len(first_sale)} customers')

    results = [compute_period(rows, first_sale, *p) for p in PERIODS]

    out_path = Path(args.out_dir) / f'vip_customer_comparison_{as_of.isoformat()}.xlsx'
    print(f'[3/3] Writing {out_path} ...')
    write_xlsx(results, out_path, as_of)

    print('\nDone. Written to:')
    print(f'  {out_path.resolve()}\n')
    for r in results:
        print(f'  {r["label"]}: base={r["customer_base"]:,} '
              f'VIP={r["vip_customers"]:,} ({r["vip_customer_pct"]}%) '
              f'VIP rev%={r["vip_revenue_pct"]}% '
              f'new={r["new_customers"]:,} ({r["new_revenue_pct"]}% of rev)')


if __name__ == '__main__':
    main()
