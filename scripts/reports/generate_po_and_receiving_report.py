"""
Master-data Excel report: Purchasing & Receiving Order report.

Cohort: purchase orders whose PO.CREATED_DATETIME falls in the period.
(PO.POST_DATE is a finalization timestamp — POs commonly get "posted" long
after they're created and even after receipts have been booked. CREATED_DATETIME
is the actual PO creation date and matches SHIPPING_DATE / first-receipt on
most rows.)
Grain:  one row per (PO, ITEM_SID) — i.e. one PO line.

Columns:
  po_no                            — PO.PO_NO
  po_date                          — TRUNC(PO.CREATED_DATETIME)
  supplier                         — VENDOR.VEND_NAME via PO.VENDOR_SID
                                     (who we bought FROM)
  vendor                           — VENDOR.VEND_NAME via INVN_SBS_ITEM.VEND_SID
                                     (the brand of the ordered item)
  season                           — INVN_SBS_ITEM.UDF5_STRING
  sku_upc                          — TO_CHAR(INVN_SBS_ITEM.UPC)
  ordered_qty                      — PO_ITEM.ORD_QTY
  received_qty                     — computed from vouchers linked to this
                                     PO/item (vou_class=0, vou_type=0, status=4).
                                     Excludes vendor returns (vou_type=1).
  first_received_date              — MIN(voucher.post_date) for that PO/item
  avg_received_days_from_first     — value-weighted average of
                                     (voucher.post_date - first_received_date)
                                     across all receipt lines for that PO/item.
                                     0 = all units received on the first day.

Output: document/reports/po_and_receiving_<start>_<today>.xlsx
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_oracle_connection


QUERY = r"""
WITH po_in_period AS (
    SELECT
        p.SID                     AS po_sid,
        p.PO_NO                   AS po_no,
        TRUNC(p.CREATED_DATETIME) AS po_date,
        p.VENDOR_SID              AS supplier_vend_sid
    FROM PO p
    WHERE p.CREATED_DATETIME >= TO_DATE(:start_date, 'YYYY-MM-DD')
      AND TRUNC(p.CREATED_DATETIME) <= TO_DATE(:end_date, 'YYYY-MM-DD')
),
po_receipts AS (
    -- One row per (po, item, voucher receipt line). Only positive receipts.
    SELECT
        vh.PO_SID    AS po_sid,
        vi.item_sid  AS item_sid,
        vh.post_date AS post_date,
        vi.qty       AS qty
    FROM VOUCHER vh
    JOIN VOU_ITEM vi ON vi.vou_sid = vh.SID
    WHERE vh.vou_class = 0
      AND vh.vou_type = 0
      AND vh.status = 4
      AND vh.PO_SID IS NOT NULL
),
first_receipt AS (
    SELECT po_sid, item_sid,
           MIN(post_date) AS first_date,
           SUM(qty)       AS total_rcvd
    FROM po_receipts
    GROUP BY po_sid, item_sid
),
avg_days AS (
    SELECT
        pr.po_sid, pr.item_sid,
        SUM(pr.qty * (pr.post_date - fr.first_date)) /
            NULLIF(SUM(pr.qty), 0) AS avg_days
    FROM po_receipts pr
    JOIN first_receipt fr
      ON fr.po_sid = pr.po_sid AND fr.item_sid = pr.item_sid
    GROUP BY pr.po_sid, pr.item_sid
)
SELECT
    pip.po_no                                    AS po_no,
    pip.po_date                                  AS po_date,
    v_po.VEND_NAME                               AS supplier,
    v_item.VEND_NAME                             AS vendor,
    i.UDF5_STRING                                AS season,
    TO_CHAR(i.UPC)                               AS sku_upc,
    pi.ORD_QTY                                   AS ordered_qty,
    NVL(fr.total_rcvd, 0)                        AS received_qty,
    fr.first_date                                AS first_received_date,
    ROUND(ad.avg_days, 2)                        AS avg_received_days_from_first
FROM po_in_period pip
JOIN PO_ITEM pi ON pi.po_sid = pip.po_sid
JOIN INVN_SBS_ITEM i ON i.SID = pi.ITEM_SID
LEFT JOIN VENDOR v_po   ON v_po.SID   = pip.supplier_vend_sid
LEFT JOIN VENDOR v_item ON v_item.SID = i.VEND_SID
LEFT JOIN first_receipt fr ON fr.po_sid = pip.po_sid AND fr.item_sid = pi.ITEM_SID
LEFT JOIN avg_days      ad ON ad.po_sid = pip.po_sid AND ad.item_sid = pi.ITEM_SID
ORDER BY pip.po_date, pip.po_no, pi.ITEM_POS
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
    def _date(v):
        if v is None: return None
        if hasattr(v, 'date'): return v.date()
        return v

    def _int(v):
        return 0 if v is None else int(v)

    def _float(v):
        return None if v is None else float(v)

    out = []
    for r in raw_rows:
        out.append({
            'po_no':                        r.get('po_no') or '',
            'po_date':                      _date(r.get('po_date')),
            'supplier':                     r.get('supplier') or '',
            'vendor':                       r.get('vendor') or '',
            'season':                       r.get('season') or '',
            'sku_upc':                      r.get('sku_upc') or '',
            'ordered_qty':                  _int(r.get('ordered_qty')),
            'received_qty':                 _int(r.get('received_qty')),
            'first_received_date':          _date(r.get('first_received_date')),
            'avg_received_days_from_first': _float(r.get('avg_received_days_from_first')),
        })
    return out


def write_xlsx(rows, out_path: Path, start_date: str, end_date: str, as_of: date):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'PO and Receiving'

    headers = [
        'po_no', 'po_date', 'supplier', 'vendor', 'season', 'sku_upc',
        'ordered_qty', 'received_qty',
        'first_received_date', 'avg_received_days_from_first',
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

    # Formatting
    date_cols = [headers.index(h) + 1 for h in ('po_date', 'first_received_date')]
    avg_col = headers.index('avg_received_days_from_first') + 1
    for row_idx in range(2, ws.max_row + 1):
        for c in date_cols:
            ws.cell(row=row_idx, column=c).number_format = 'YYYY-MM-DD'
        ws.cell(row=row_idx, column=avg_col).number_format = '0.00'

    for col_idx, header in enumerate(headers, start=1):
        cells = [ws.cell(row=r, column=col_idx).value
                 for r in range(2, ws.max_row + 1)] if ws.max_row > 1 else []
        max_len = max(len(str(header)),
                      *(len(str(v or '')) for v in cells)) if cells else len(header)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(max_len + 2, 12), 32)

    ws.freeze_panes = 'A2'

    # Metadata sheet
    meta = wb.create_sheet('Metadata')
    meta.append(['Parameter', 'Value'])
    for cell in meta[1]:
        cell.font = header_font
        cell.fill = header_fill
    meta.append(['start_date', start_date])
    meta.append(['end_date (inclusive)', end_date])
    meta.append(['as_of_date (today)', as_of.isoformat()])
    meta.append(['cohort', 'POs with CREATED_DATETIME in period (real PO creation date, not POST_DATE)'])
    meta.append(['received_qty basis',
                 'sum of VOU_ITEM.qty from vouchers linked to the PO (vou_class=0, vou_type=0, status=4)'])
    meta.append(['avg_received_days_from_first',
                 'weighted-average days from first receipt: Σ(qty × (date - first_date)) / Σ(qty)'])
    meta.append(['row count', len(rows)])
    meta.append(['distinct POs', len({r['po_no'] for r in rows if r['po_no']})])
    meta.append(['distinct SKUs', len({r['sku_upc'] for r in rows if r['sku_upc']})])
    meta.append(['total ordered qty', sum(r['ordered_qty'] for r in rows)])
    meta.append(['total received qty', sum(r['received_qty'] for r in rows)])
    meta.column_dimensions['A'].width = 34
    meta.column_dimensions['B'].width = 74

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--start-date', default='2025-01-01',
                        help='Inclusive lower bound on PO.CREATED_DATETIME (default: 2025-01-01)')
    parser.add_argument('--end-date', default=None,
                        help='Inclusive upper bound (default: today)')
    parser.add_argument('--out-dir', default='document/reports',
                        help='Output directory (default: document/reports)')
    args = parser.parse_args()

    as_of = date.today()
    end_date = args.end_date or as_of.isoformat()

    print(f'[1/2] Querying POs + receipts ({args.start_date} -> {end_date}) ...')
    raw = fetch_rows(args.start_date, end_date)
    rows = shape_rows(raw)
    print(f'      -> {len(rows)} PO lines')

    out_path = Path(args.out_dir) / f'po_and_receiving_{args.start_date}_{as_of.isoformat()}.xlsx'
    print(f'[2/2] Writing {out_path} ...')
    write_xlsx(rows, out_path, args.start_date, end_date, as_of)

    tot_ord = sum(r['ordered_qty'] for r in rows)
    tot_rcvd = sum(r['received_qty'] for r in rows)
    print(f'\nDone. Written to:')
    print(f'  {out_path.resolve()}')
    print(f'  PO lines:         {len(rows):,}')
    print(f'  Distinct POs:     {len({r["po_no"] for r in rows if r["po_no"]}):,}')
    print(f'  Distinct SKUs:    {len({r["sku_upc"] for r in rows if r["sku_upc"]}):,}')
    print(f'  Total ordered:    {tot_ord:,} units')
    print(f'  Total received:   {tot_rcvd:,} units')
    print(f'  Fill rate:        {(tot_rcvd / tot_ord * 100 if tot_ord else 0):.1f}%')


if __name__ == '__main__':
    main()
