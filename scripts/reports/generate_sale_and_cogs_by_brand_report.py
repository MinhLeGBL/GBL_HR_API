"""
Master-data Excel report: Sale and COGS by brand — scoped to newly-imported SKUs.

Grain: one row per (brand_code, brand_name, department, category).

**Scope rule (new):** the report includes ONLY items that were received
via a voucher (net) in the period. Sales of items imported before the
period are NOT counted here. This keeps the sale/COGS metrics aligned
with the "in-period import" cohort.

Columns:
  net_import_qty      — voucher receipts (vou_class=0), sign from vou_type
                        (0 = receipt +, 1 = vendor return -)
  net_sold_qty        — documents (STATUS=4, receipt_type IN (0,1)),
                        item_type=1 as +qty, item_type=2 as -qty,
                        RESTRICTED to items in the import cohort
  net_revenue_before_vat — sum(signed qty × (di.price − di.tax_amt)),
                        RESTRICTED to the import cohort
  net_landed_cost     — sum(signed qty × TO_NUMBER(i.UDF1_STRING)),
                        RESTRICTED to the import cohort
                        (items with NULL/non-numeric UDF1 contribute 0)
  landed_cost_coverage_pct — fraction of sold units with numeric UDF1_STRING
  avg_discount_rate   — sales only, line-level, value-weighted:
                        1 - Σ(qty × di.PRICE) / Σ(qty × di.ORIG_PRICE),
                        RESTRICTED to the import cohort

Notes:
- Opening inventory reconstruction removed (per user: not needed).
- Adjustments and vou_class=2 transfers are ignored; SKU cohort is
  strictly what voucher receipts brought in during the period.

Output: document/reports/sale_and_cogs_by_brand_<start>_<today>.xlsx
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_oracle_connection


QUERY = r"""
WITH period_received AS (
    SELECT
        vi.item_sid AS item_sid,
        SUM(CASE WHEN vh.vou_type = 1 THEN -vi.qty ELSE vi.qty END) AS qty
    FROM VOUCHER vh
    JOIN VOU_ITEM vi ON vi.vou_sid = vh.SID
    WHERE vh.vou_class = 0
      AND vh.status = 4
      AND vh.vou_type IN (0, 1)
      AND vh.post_date >= TO_DATE(:start_date, 'YYYY-MM-DD')
      AND TRUNC(vh.post_date) <= TO_DATE(:end_date, 'YYYY-MM-DD')
    GROUP BY vi.item_sid
),
period_sold AS (
    SELECT
        b.INVN_SBS_ITEM_SID AS item_sid,
        SUM(CASE WHEN b.ITEM_TYPE = 2 THEN -b.qty ELSE b.qty END) AS qty,
        SUM(CASE WHEN b.ITEM_TYPE = 2 THEN -b.qty ELSE b.qty END
            * (b.price - b.tax_amt)) AS revenue,
        SUM(CASE WHEN b.ITEM_TYPE = 1 THEN b.qty * b.PRICE ELSE 0 END)
            AS sale_price_qty,
        SUM(CASE WHEN b.ITEM_TYPE = 1 THEN b.qty * b.ORIG_PRICE ELSE 0 END)
            AS sale_origprice_qty
    FROM DOCUMENT a
    JOIN DOCUMENT_ITEM b ON a.SID = b.DOC_SID
    WHERE a.STATUS = 4
      AND a.receipt_type IN (0, 1)
      AND b.ITEM_TYPE IN (1, 2)
      AND a.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD')
      AND TRUNC(a.invc_post_date) <= TO_DATE(:end_date, 'YYYY-MM-DD')
      AND b.INVN_SBS_ITEM_SID IN (SELECT item_sid FROM period_received)
    GROUP BY b.INVN_SBS_ITEM_SID
),
per_item AS (
    SELECT
        i.SID           AS item_sid,
        i.VEND_SID      AS vend_sid,
        i.DCS_SID       AS dcs_sid,
        pr.qty          AS received_qty,
        NVL(ps.qty, 0)  AS sold_qty,
        NVL(ps.revenue, 0)             AS revenue,
        NVL(ps.sale_price_qty, 0)      AS sale_price_qty,
        NVL(ps.sale_origprice_qty, 0)  AS sale_origprice_qty,
        CASE WHEN REGEXP_LIKE(i.UDF1_STRING, '^[0-9]+(\.[0-9]+)?$')
             THEN TO_NUMBER(i.UDF1_STRING) ELSE 0 END AS landed_cost
    FROM INVN_SBS_ITEM i
    JOIN period_received pr ON pr.item_sid = i.SID
    LEFT JOIN period_sold ps ON ps.item_sid = i.SID
)
SELECT
    v.VEND_CODE                       AS brand_code,
    v.VEND_NAME                       AS brand_name,
    dep.D_LONG_NAME                   AS department,
    dep.C_LONG_NAME                   AS category,
    SUM(pi.received_qty)              AS net_import_qty,
    SUM(pi.sold_qty)                  AS net_sold_qty,
    ROUND(SUM(pi.revenue), 0)         AS net_revenue_before_vat,
    ROUND(SUM(pi.sold_qty * pi.landed_cost), 0)  AS net_landed_cost,
    ROUND(
        SUM(CASE WHEN pi.landed_cost > 0 THEN pi.sold_qty ELSE 0 END) /
        NULLIF(SUM(pi.sold_qty), 0),
        4
    )                                 AS landed_cost_coverage_pct,
    ROUND(
        1 - SUM(pi.sale_price_qty) / NULLIF(SUM(pi.sale_origprice_qty), 0),
        4
    )                                 AS avg_discount_rate
FROM per_item pi
LEFT JOIN VENDOR v ON v.SID = pi.vend_sid
LEFT JOIN DCS dep  ON dep.SID = pi.dcs_sid
GROUP BY v.VEND_CODE, v.VEND_NAME, dep.D_LONG_NAME, dep.C_LONG_NAME
ORDER BY brand_code, department, category
"""


def fetch_rows(start_date: str, end_date: str):
    conn = get_oracle_connection()
    if conn is None:
        raise RuntimeError('Could not open Oracle connection')
    try:
        cur = conn.cursor()
        try:
            cur.execute(QUERY, {'start_date': start_date, 'end_date': end_date})
            cols = [d[0].lower() for d in cur.description]
            rows = cur.fetchall()
        finally:
            cur.close()
    finally:
        conn.close()
    return [dict(zip(cols, r)) for r in rows]


def shape_rows(raw_rows):
    def _num(v):
        if v is None: return 0
        return float(v)
    def _int(v):
        return int(_num(v))

    out = []
    for r in raw_rows:
        out.append({
            'brand_code':               r.get('brand_code') or '',
            'brand_name':               r.get('brand_name') or '',
            'department':               r.get('department') or '',
            'category':                 r.get('category') or '',
            'net_import_qty':           _int(r.get('net_import_qty')),
            'net_sold_qty':             _int(r.get('net_sold_qty')),
            'net_revenue_before_vat':   _int(r.get('net_revenue_before_vat')),
            'net_landed_cost':          _int(r.get('net_landed_cost')),
            'landed_cost_coverage_pct': float(r.get('landed_cost_coverage_pct') or 0),
            'avg_discount_rate':        float(r.get('avg_discount_rate') or 0),
        })
    return out


def write_xlsx(rows, out_path: Path, start_date: str, end_date: str, as_of: date):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'Sale and COGS by Brand'

    headers = [
        'brand_code', 'brand_name', 'department', 'category',
        'net_import_qty', 'net_sold_qty',
        'net_revenue_before_vat', 'net_landed_cost',
        'landed_cost_coverage_pct', 'avg_discount_rate',
    ]
    ws.append(headers)

    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='305496', end_color='305496', fill_type='solid')
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')

    for r in rows:
        ws.append([r[h] for h in headers])

    money_cols = [headers.index(h) + 1 for h in
                  ('net_revenue_before_vat', 'net_landed_cost')]
    pct_cols = [headers.index(h) + 1 for h in
                ('landed_cost_coverage_pct', 'avg_discount_rate')]
    for row_idx in range(2, ws.max_row + 1):
        for c in money_cols:
            ws.cell(row=row_idx, column=c).number_format = '#,##0'
        for c in pct_cols:
            ws.cell(row=row_idx, column=c).number_format = '0.00%'

    for col_idx, header in enumerate(headers, start=1):
        cells = [ws.cell(row=r, column=col_idx).value
                 for r in range(2, ws.max_row + 1)] if ws.max_row > 1 else []
        max_len = max(len(str(header)),
                      *(len(f'{v:,}') if isinstance(v, int) else len(str(v or ''))
                        for v in cells)) if cells else len(header)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(max_len + 2, 12), 34)

    ws.freeze_panes = 'A2'

    meta = wb.create_sheet('Metadata')
    meta.append(['Parameter', 'Value'])
    for cell in meta[1]:
        cell.font = header_font
        cell.fill = header_fill
    meta.append(['start_date', start_date])
    meta.append(['end_date (inclusive)', end_date])
    meta.append(['as_of_date (today)', as_of.isoformat()])
    meta.append(['SKU cohort',
                 'ONLY items received via voucher (vou_class=0) in the period'])
    meta.append(['revenue basis',
                 'before VAT — di.price - di.tax_amt (actual per-line VAT)'])
    meta.append(['landed cost source',
                 'INVN_SBS_ITEM.UDF1_STRING (numeric only; else 0)'])
    meta.append(['avg discount rule',
                 'sales only, line-level, value-weighted by di.ORIG_PRICE'])
    meta.append(['ignored channels',
                 'rps.adjustment; vou_class=2 transfers; items not received in period'])
    meta.append(['row count', len(rows)])
    meta.append(['total net import qty',
                 sum(r['net_import_qty'] for r in rows)])
    meta.append(['total net sold qty (in-cohort)',
                 sum(r['net_sold_qty'] for r in rows)])
    meta.append(['total net revenue (in-cohort)',
                 sum(r['net_revenue_before_vat'] for r in rows)])
    meta.append(['total net landed cost (in-cohort)',
                 sum(r['net_landed_cost'] for r in rows)])
    meta.column_dimensions['A'].width = 34
    meta.column_dimensions['B'].width = 78

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--start-date', default='2025-01-01',
                        help='Inclusive lower bound (default: 2025-01-01)')
    parser.add_argument('--end-date', default=None,
                        help='Inclusive upper bound (default: today)')
    parser.add_argument('--out-dir', default='document/reports',
                        help='Output directory (default: document/reports)')
    args = parser.parse_args()

    as_of = date.today()
    end_date = args.end_date or as_of.isoformat()

    print(f'[1/2] Running Oracle aggregation ({args.start_date} -> {end_date}) ...')
    raw = fetch_rows(args.start_date, end_date)
    rows = shape_rows(raw)
    print(f'      -> {len(rows)} (brand x dept x category) groups')

    out_path = Path(args.out_dir) / f'sale_and_cogs_by_brand_{args.start_date}_{as_of.isoformat()}.xlsx'
    print(f'[2/2] Writing {out_path} ...')
    write_xlsx(rows, out_path, args.start_date, end_date, as_of)

    total_import = sum(r['net_import_qty'] for r in rows)
    total_sold = sum(r['net_sold_qty'] for r in rows)
    total_rev = sum(r['net_revenue_before_vat'] for r in rows)
    total_cogs = sum(r['net_landed_cost'] for r in rows)
    print(f'\nDone. Written to:')
    print(f'  {out_path.resolve()}')
    print(f'  Rows:               {len(rows)}')
    print(f'  Total import qty:   {total_import:,} units')
    print(f'  Total sold qty:     {total_sold:,} units (in-cohort)')
    print(f'  Sell-through:       {(total_sold / total_import * 100 if total_import else 0):.1f}%')
    print(f'  Total revenue:      {total_rev:,} VND (in-cohort)')
    print(f'  Total landed cost:  {total_cogs:,} VND (in-cohort)')
    print(f'  Distinct brands:    {len({r["brand_code"] for r in rows})}')


if __name__ == '__main__':
    main()
