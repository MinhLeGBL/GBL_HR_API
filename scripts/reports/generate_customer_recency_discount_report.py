"""
One-off Excel report: per-customer recency + discount-frequency for store RHN
since 2024-01-01.

For each customer with at least one sale line at RHN in the window:
  - last_purchase_date and days_since_last_purchase (vs today)
  - total_items                — count of sale lines (item_type = 1)
  - discount_item_count        — sale lines where combined line+bill discount
                                 > 30% (same formula used by commission)
  - discount_item_pct          — discount_item_count / total_items × 100

Exclusions: walk-ins (BT_CUID NULL) and SYSADMIN / Tourist accounts.

Output: document/reports/customer_recency_discount_<STORE>_<YYYY-MM-DD>.xlsx
"""
import argparse
import sys
from datetime import date
from pathlib import Path

# Allow running this file directly from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_oracle_connection


QUERY = """
    SELECT
        d.BT_CUID                                          AS customer_sid,
        TRIM(c.FIRST_NAME)                                 AS customer_name,
        TRUNC(MAX(d.invc_post_date))                       AS last_purchase_date,
        COUNT(*)                                           AS total_items,
        SUM(
            CASE
                WHEN ROUND(
                    (1 - (1 - di.DISC_PERC/100) * (1 - d.DISC_PERC/100)) * 100,
                    2
                ) / 100 > 0.30
                THEN 1 ELSE 0
            END
        )                                                  AS discount_item_count
    FROM DOCUMENT d
    JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
    LEFT JOIN CUSTOMER c  ON c.SID = d.BT_CUID
    WHERE d.STORE_CODE = :store_code
      AND d.invc_post_date >= TO_DATE(:start_date, 'YYYY-MM-DD')
      AND d.STATUS = 4
      AND d.receipt_type IN (0, 1)
      AND di.ITEM_TYPE = 1
      AND d.BT_CUID IS NOT NULL
      AND d.BT_CUID NOT IN (
          SELECT SID FROM CUSTOMER
          WHERE UPPER(TRIM(FIRST_NAME)) IN ('SYSADMIN', 'TOURIST', 'TOURIST.')
      )
    GROUP BY d.BT_CUID, TRIM(c.FIRST_NAME)
    ORDER BY MAX(d.invc_post_date) DESC
"""


def fetch_rows(store_code: str, start_date: str):
    conn = get_oracle_connection()
    if conn is None:
        raise RuntimeError('Could not open Oracle connection')
    try:
        cur = conn.cursor()
        try:
            cur.execute(QUERY, {'store_code': store_code, 'start_date': start_date})
            cols = [d[0].lower() for d in cur.description]
            rows = cur.fetchall()
        finally:
            cur.close()
    finally:
        conn.close()
    return cols, rows


def shape_rows(cols, raw_rows, as_of: date):
    out = []
    for raw in raw_rows:
        rec = dict(zip(cols, raw))
        last = rec.get('last_purchase_date')
        if hasattr(last, 'date'):
            last = last.date()
        total = int(rec.get('total_items') or 0)
        disc = int(rec.get('discount_item_count') or 0)
        out.append({
            'customer_sid': int(rec['customer_sid']),
            'customer_name': rec.get('customer_name') or '',
            'last_purchase_date': last,
            'days_since_last_purchase': (as_of - last).days if last else None,
            'total_items': total,
            'discount_item_count': disc,
            'discount_item_pct': round(disc / total * 100, 2) if total > 0 else None,
        })
    return out


def write_xlsx(rows, out_path: Path, store_code: str, start_date: str, as_of: date):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()

    # ---- Report sheet ----
    ws = wb.active
    ws.title = 'Customer Recency'

    headers = [
        'customer_sid', 'customer_name',
        'last_purchase_date', 'days_since_last_purchase',
        'total_items', 'discount_item_count', 'discount_item_pct',
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

    # Format date and percent columns
    for row_idx in range(2, ws.max_row + 1):
        date_cell = ws.cell(row=row_idx, column=headers.index('last_purchase_date') + 1)
        date_cell.number_format = 'YYYY-MM-DD'
        pct_cell = ws.cell(row=row_idx, column=headers.index('discount_item_pct') + 1)
        pct_cell.number_format = '0.00'

    # Auto-size columns (header-width-based, capped)
    for col_idx, header in enumerate(headers, start=1):
        max_len = max(len(str(header)),
                      *(len(str(ws.cell(row=r, column=col_idx).value or ''))
                        for r in range(2, ws.max_row + 1))) if ws.max_row > 1 else len(header)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(max_len + 2, 12), 40)

    ws.freeze_panes = 'A2'

    # ---- Metadata sheet ----
    meta = wb.create_sheet('Metadata')
    meta.append(['Parameter', 'Value'])
    for cell in meta[1]:
        cell.font = header_font
        cell.fill = header_fill
    meta.append(['store_code', store_code])
    meta.append(['start_date', start_date])
    meta.append(['as_of_date (today)', as_of.isoformat()])
    meta.append(['discount_threshold', '> 30% combined line+bill discount'])
    meta.append(['customer_count', len(rows)])
    meta.append(['total_items_all_customers', sum(r['total_items'] for r in rows)])
    meta.append(['discount_items_all_customers', sum(r['discount_item_count'] for r in rows)])
    meta.append(['excluded', 'walk-ins (BT_CUID NULL), SYSADMIN, Tourist'])
    meta.column_dimensions['A'].width = 32
    meta.column_dimensions['B'].width = 50

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--store-code', default='RHN', help='Store code (default: RHN)')
    parser.add_argument('--start-date', default='2024-01-01',
                        help='Inclusive lower bound on invc_post_date, ISO YYYY-MM-DD (default: 2024-01-01)')
    parser.add_argument('--out-dir', default='document/reports',
                        help='Output directory (default: document/reports)')
    args = parser.parse_args()

    store_code = args.store_code.upper()
    start_date = args.start_date
    as_of = date.today()

    print(f'[1/3] Querying Oracle: store={store_code} since {start_date} ...')
    cols, raw = fetch_rows(store_code, start_date)
    print(f'      → {len(raw)} customer rows')

    print('[2/3] Shaping rows + computing days-since / discount-pct ...')
    rows = shape_rows(cols, raw, as_of)

    out_path = Path(args.out_dir) / f'customer_recency_discount_{store_code}_{as_of.isoformat()}.xlsx'
    print(f'[3/3] Writing {out_path} ...')
    write_xlsx(rows, out_path, store_code, start_date, as_of)

    print(f'\nDone. {len(rows)} customers written to:')
    print(f'  {out_path.resolve()}')


if __name__ == '__main__':
    main()
