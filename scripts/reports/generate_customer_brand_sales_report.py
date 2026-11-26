"""
One-off Excel report: every sale and return line for ONE customer, ONE brand.

Grain: one row per posted DOCUMENT_ITEM line.

Scope:
  - customer   — matched on CUSTOMER first+last name (case-insensitive, exact)
  - brand      — matched on VENDOR.VEND_NAME or VEND_CODE (case-insensitive, exact),
                 via the item's VEND_SID
  - documents  — STATUS = 4 (posted), receipt_type IN (0, 1), item_type IN (1, 2).
                 Held / unposted documents (STATUS 2, 3) are excluded.

Money:
  - price is VAT-inclusive; DOCUMENT_ITEM.TAX_AMT is per unit
  - returns (item_type = 2) are shown with negative qty and amounts

Usage:
  FLASK_ENV=remote .venv/bin/python scripts/reports/generate_customer_brand_sales_report.py \
      --customer "To Mai Lien" --brand "TED MUEHLING"

Output: document/reports/customer_brand_sales_<customer>_<brand>_<today>.xlsx
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_oracle_connection


CUSTOMER_QUERY = """
    SELECT SID, TRIM(FIRST_NAME || ' ' || NVL(LAST_NAME, '')) AS name
    FROM CUSTOMER
    WHERE UPPER(TRIM(FIRST_NAME || ' ' || NVL(LAST_NAME, ''))) = UPPER(TRIM(:name))
"""

BRAND_QUERY = """
    SELECT SID, VEND_CODE, VEND_NAME
    FROM VENDOR
    WHERE UPPER(TRIM(VEND_NAME)) = UPPER(TRIM(:brand))
       OR UPPER(TRIM(VEND_CODE)) = UPPER(TRIM(:brand))
"""

LINES_QUERY = """
    SELECT
        d.invc_post_date                       AS post_date,
        d.STORE_CODE                           AS store_code,
        d.DOC_NO                               AS doc_no,
        CASE WHEN b.ITEM_TYPE = 2 THEN 'Return' ELSE 'Sale' END AS line_type,
        i.UPC                                  AS upc,
        i.ALU                                  AS alu,
        b.DESCRIPTION1                         AS description,
        b.DESCRIPTION2                         AS description2,
        b.ATTRIBUTE                            AS attribute,
        b.ITEM_SIZE                            AS item_size,
        CASE WHEN b.ITEM_TYPE = 2 THEN -b.QTY ELSE b.QTY END AS qty,
        b.ORIG_PRICE                           AS orig_price,
        b.PRICE                                AS unit_price,
        ROUND((1 - (1 - NVL(b.DISC_PERC,0)/100) * (1 - NVL(d.DISC_PERC,0)/100)) , 4)
                                               AS discount_rate,
        CASE WHEN b.ITEM_TYPE = 2 THEN -b.QTY ELSE b.QTY END * b.PRICE
                                               AS amount_incl_vat,
        CASE WHEN b.ITEM_TYPE = 2 THEN -b.QTY ELSE b.QTY END * (b.PRICE - b.TAX_AMT)
                                               AS amount_before_vat,
        d.EMPLOYEE1_FULL_NAME                  AS sales_associate
    FROM DOCUMENT d
    JOIN DOCUMENT_ITEM b  ON b.DOC_SID = d.SID
    JOIN INVN_SBS_ITEM i  ON i.SID = b.INVN_SBS_ITEM_SID
    WHERE d.BT_CUID = :customer_sid
      AND i.VEND_SID = :vend_sid
      AND d.STATUS = 4
      AND d.receipt_type IN (0, 1)
      AND b.ITEM_TYPE IN (1, 2)
    ORDER BY d.invc_post_date, d.DOC_NO, b.ITEM_POS
"""


def _one(cur, sql, params, what):
    cur.execute(sql, params)
    rows = cur.fetchall()
    if len(rows) != 1:
        raise SystemExit(f'Expected exactly one {what} for {params}, found {len(rows)}: {rows}')
    return rows[0]


def fetch(customer: str, brand: str):
    conn = get_oracle_connection()
    if conn is None:
        raise RuntimeError('Could not open Oracle connection')
    try:
        cur = conn.cursor()
        try:
            cust_sid, cust_name = _one(cur, CUSTOMER_QUERY, {'name': customer}, 'customer')
            vend_sid, vend_code, vend_name = _one(cur, BRAND_QUERY, {'brand': brand}, 'brand')
            cur.execute(LINES_QUERY, {'customer_sid': cust_sid, 'vend_sid': vend_sid})
            cols = [d[0].lower() for d in cur.description]
            lines = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()
    finally:
        conn.close()
    return cust_name, vend_code, vend_name, lines


def shape(lines):
    for r in lines:
        if hasattr(r['post_date'], 'date'):
            r['post_date'] = r['post_date'].date()
        r['qty'] = int(r['qty'] or 0)
        for k in ('orig_price', 'unit_price', 'amount_incl_vat', 'amount_before_vat'):
            r[k] = round(float(r[k] or 0))
        r['discount_rate'] = float(r['discount_rate'] or 0)
        r['upc'] = str(r['upc'] or '')
    return lines


HEADERS = [
    'post_date', 'store_code', 'doc_no', 'line_type', 'upc', 'alu',
    'description', 'description2', 'attribute', 'item_size', 'qty',
    'orig_price', 'unit_price', 'discount_rate',
    'amount_incl_vat', 'amount_before_vat', 'sales_associate',
]


def write_xlsx(lines, out_path, cust_name, vend_code, vend_name, as_of):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='305496', end_color='305496', fill_type='solid')

    def style_header(ws):
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center')

    wb = Workbook()

    # ---- Lines ----
    ws = wb.active
    ws.title = 'Lines'
    ws.append(HEADERS)
    style_header(ws)
    for r in lines:
        ws.append([r[h] for h in HEADERS])
    total_row = ws.max_row + 1
    ws.cell(row=total_row, column=1, value='TOTAL (net)').font = Font(bold=True)
    for h in ('qty', 'amount_incl_vat', 'amount_before_vat'):
        c = HEADERS.index(h) + 1
        cell = ws.cell(row=total_row, column=c, value=sum(r[h] for r in lines))
        cell.font = Font(bold=True)

    money = [HEADERS.index(h) + 1 for h in
             ('orig_price', 'unit_price', 'amount_incl_vat', 'amount_before_vat')]
    for row in range(2, ws.max_row + 1):
        ws.cell(row=row, column=1).number_format = 'YYYY-MM-DD'
        for c in money:
            ws.cell(row=row, column=c).number_format = '#,##0'
        ws.cell(row=row, column=HEADERS.index('discount_rate') + 1).number_format = '0.00%'
    for i, h in enumerate(HEADERS, start=1):
        vals = [ws.cell(row=r, column=i).value for r in range(1, ws.max_row + 1)]
        width = max(len(f'{v:,}') if isinstance(v, (int, float)) else len(str(v or '')) for v in vals)
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 36)
    ws.freeze_panes = 'A2'

    # ---- Summary ----
    sales = [r for r in lines if r['line_type'] == 'Sale']
    rets = [r for r in lines if r['line_type'] == 'Return']
    sm = wb.create_sheet('Summary')
    sm.append(['Metric', 'Value'])
    style_header(sm)
    rows = [
        ('Customer', cust_name),
        ('Brand', f'{vend_name} ({vend_code})'),
        ('First purchase', min((r['post_date'] for r in sales), default=None)),
        ('Last purchase', max((r['post_date'] for r in sales), default=None)),
        ('Sale receipts', len({r['doc_no'] for r in sales})),
        ('Return receipts', len({r['doc_no'] for r in rets})),
        ('Units sold', sum(r['qty'] for r in sales)),
        ('Units returned', -sum(r['qty'] for r in rets)),
        ('Net units', sum(r['qty'] for r in lines)),
        ('Gross sales incl. VAT', sum(r['amount_incl_vat'] for r in sales)),
        ('Returns incl. VAT', sum(r['amount_incl_vat'] for r in rets)),
        ('Net sales incl. VAT', sum(r['amount_incl_vat'] for r in lines)),
        ('Net sales before VAT', sum(r['amount_before_vat'] for r in lines)),
    ]
    for k, v in rows:
        sm.append([k, v])
    for row in range(2, sm.max_row + 1):
        cell = sm.cell(row=row, column=2)
        if isinstance(cell.value, date):
            cell.number_format = 'YYYY-MM-DD'
        elif isinstance(cell.value, (int, float)):
            cell.number_format = '#,##0'
    sm.column_dimensions['A'].width = 26
    sm.column_dimensions['B'].width = 34

    # ---- Metadata ----
    meta = wb.create_sheet('Metadata')
    meta.append(['Parameter', 'Value'])
    style_header(meta)
    meta.append(['as_of_date', as_of.isoformat()])
    meta.append(['date range', 'all history'])
    meta.append(['documents', 'STATUS = 4 (posted) only; held/unposted excluded'])
    meta.append(['brand match', 'INVN_SBS_ITEM.VEND_SID'])
    meta.append(['price basis', 'unit_price is VAT-inclusive; before VAT = price - tax_amt'])
    meta.append(['returns', 'item_type = 2, shown as negative qty / amounts'])
    meta.column_dimensions['A'].width = 20
    meta.column_dimensions['B'].width = 60

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def _slug(s):
    return re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_')


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument('--customer', required=True, help='Customer full name, e.g. "To Mai Lien"')
    p.add_argument('--brand', required=True, help='Brand name or vendor code, e.g. "TED MUEHLING"')
    p.add_argument('--out-dir', default='document/reports')
    args = p.parse_args()

    as_of = date.today()
    cust_name, vend_code, vend_name, lines = fetch(args.customer, args.brand)
    lines = shape(lines)
    out = Path(args.out_dir) / (
        f'customer_brand_sales_{_slug(cust_name)}_{_slug(vend_name)}_{as_of.isoformat()}.xlsx')
    write_xlsx(lines, out, cust_name, vend_code, vend_name, as_of)

    print(f'Customer: {cust_name} | Brand: {vend_name} ({vend_code})')
    print(f'Lines: {len(lines)}  Net units: {sum(r["qty"] for r in lines)}')
    print(f'Net sales incl. VAT:   {sum(r["amount_incl_vat"] for r in lines):,}')
    print(f'Net sales before VAT:  {sum(r["amount_before_vat"] for r in lines):,}')
    print(f'Written to: {out.resolve()}')


if __name__ == '__main__':
    main()
