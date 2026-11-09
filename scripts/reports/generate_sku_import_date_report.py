"""
Master-data Excel report: SKU average import date & season.

One row per SKU (INVN_SBS_ITEM.SID). "Import" = a posted receiving
voucher line (vou_class=0, slip_flag=0, held=0, status=4, vou_type=0) —
the same receipt definition the custom_reports /sizes report uses.
Vendor returns (vou_type=1) are excluded so they cannot drag the average
date around.

A SKU received on several vouchers gets one averaged date. The average
is quantity-weighted: it is the date the *stock* landed on average, so a
SKU with 2 units in Jan and 200 in Nov averages to ~Nov rather than to
~Jun. Computed in Julian days and rounded back to a whole date.

Columns:
  upc                    — TO_CHAR(INVN_SBS_ITEM.UPC)
  product_code           — INVN_SBS_ITEM.ALU
  product_name           — INVN_SBS_ITEM.DESCRIPTION1
  brand_code             — VENDOR.VEND_CODE
  brand_name             — VENDOR.VEND_NAME
  department             — DCS.D_LONG_NAME
  category               — DCS.C_LONG_NAME
  item_size              — INVN_SBS_ITEM.ITEM_SIZE
  season                 — INVN_SBS_ITEM.UDF5_STRING
  import_events          — count of receipt voucher lines
  distinct_import_dates  — count of distinct receipt dates
  total_imported_qty     — SUM(VOU_ITEM.QTY) over those lines
  first_import_date      — MIN(TRUNC(voucher.post_date))
  last_import_date       — MAX(TRUNC(voucher.post_date))
  avg_import_date        — qty-weighted average (see above)
  import_span_days       — last_import_date - first_import_date

Output: document/reports/sku_import_date_<scope>_<today>.xlsx
"""
import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_oracle_connection


# `{item_filter}` / `{join_type}` are built by build_query() from validated
# arguments only — never from raw user text interpolated blind.
QUERY_TEMPLATE = r"""
WITH receipts AS (
    SELECT
        vi.item_sid                                     AS item_sid,
        TRUNC(vh.post_date)                             AS post_date,
        TO_NUMBER(TO_CHAR(TRUNC(vh.post_date), 'J'))    AS post_julian,
        vi.qty                                          AS qty
    FROM VOUCHER vh
    JOIN VOU_ITEM vi ON vi.vou_sid = vh.SID
    WHERE vh.vou_class = 0
      AND vh.slip_flag = 0
      AND vh.held      = 0
      AND vh.status    = 4
      AND vh.vou_type  = 0
      AND vh.post_date IS NOT NULL
      {date_filter}
),
per_item AS (
    SELECT
        item_sid,
        COUNT(*)                     AS import_events,
        COUNT(DISTINCT post_date)    AS distinct_import_dates,
        SUM(qty)                     AS total_imported_qty,
        MIN(post_date)               AS first_import_date,
        MAX(post_date)               AS last_import_date,
        -- qty-weighted mean date, in Julian days
        SUM(qty * post_julian) / NULLIF(SUM(qty), 0)  AS avg_julian_wtd
    FROM receipts
    GROUP BY item_sid
)
SELECT
    TO_CHAR(i.UPC)      AS upc,
    i.ALU               AS product_code,
    i.DESCRIPTION1      AS product_name,
    v.VEND_CODE         AS brand_code,
    v.VEND_NAME         AS brand_name,
    dep.D_LONG_NAME     AS department,
    dep.C_LONG_NAME     AS category,
    i.ITEM_SIZE         AS item_size,
    i.UDF5_STRING       AS season,
    NVL(pi.import_events, 0)         AS import_events,
    NVL(pi.distinct_import_dates, 0) AS distinct_import_dates,
    NVL(pi.total_imported_qty, 0)    AS total_imported_qty,
    pi.first_import_date             AS first_import_date,
    pi.last_import_date              AS last_import_date,
    TO_DATE(TO_CHAR(ROUND(pi.avg_julian_wtd)), 'J')    AS avg_import_date,
    (pi.last_import_date - pi.first_import_date)       AS import_span_days
FROM INVN_SBS_ITEM i
{join_type} per_item pi ON pi.item_sid = i.SID
LEFT JOIN VENDOR v ON v.SID = i.VEND_SID
LEFT JOIN DCS dep  ON dep.SID = i.DCS_SID
{item_filter}
ORDER BY brand_code, season, product_code, item_size
"""

COLUMNS = [
    'upc', 'product_code', 'product_name', 'brand_code', 'brand_name',
    'department', 'category', 'item_size', 'season',
    'import_events', 'distinct_import_dates', 'total_imported_qty',
    'first_import_date', 'last_import_date',
    'avg_import_date', 'import_span_days',
]
DATE_COLUMNS = {
    'first_import_date', 'last_import_date', 'avg_import_date',
}


def build_query(all_skus: bool, seasons, start_date, end_date):
    """Assemble the SQL and its bind parameters.

    all_skus  — False (default) keeps only SKUs with >=1 receipt (INNER
                JOIN); True keeps every catalog row, imported or not.
    seasons   — list of season codes, or None for no season filter.
    """
    params = {}

    date_filter = ''
    if start_date:
        date_filter += "\n      AND vh.post_date >= TO_DATE(:start_date, 'YYYY-MM-DD')"
        params['start_date'] = start_date
    if end_date:
        date_filter += "\n      AND TRUNC(vh.post_date) <= TO_DATE(:end_date, 'YYYY-MM-DD')"
        params['end_date'] = end_date

    item_filter = ''
    if seasons:
        binds = []
        for idx, season in enumerate(seasons):
            key = f'season{idx}'
            params[key] = season
            binds.append(f':{key}')
        item_filter = f"WHERE i.UDF5_STRING IN ({', '.join(binds)})"

    sql = QUERY_TEMPLATE.format(
        date_filter=date_filter,
        join_type='LEFT JOIN' if all_skus else 'JOIN',
        item_filter=item_filter,
    )
    return sql, params


def fetch_rows(sql: str, params: dict):
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


def shape_rows(raw_rows):
    out = []
    for r in raw_rows:
        row = {}
        for key in COLUMNS:
            val = r.get(key)
            if key in DATE_COLUMNS:
                # Oracle DATE -> python date; blank for never-imported SKUs
                row[key] = val.date() if isinstance(val, datetime) else (val or '')
            elif key == 'import_span_days':
                row[key] = int(val) if val is not None else ''
            elif key in ('import_events', 'distinct_import_dates'):
                row[key] = int(val or 0)
            elif key == 'total_imported_qty':
                row[key] = float(val or 0)
            else:
                row[key] = val or ''
        out.append(row)
    return out


def write_xlsx(rows, out_path: Path, as_of: date, scope_notes: list):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'SKU Import Dates'
    ws.append(COLUMNS)

    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='305496', end_color='305496', fill_type='solid')
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')

    for r in rows:
        ws.append([r[h] for h in COLUMNS])

    # Real date cells, not strings, so Excel can sort/filter them
    for col_idx, header in enumerate(COLUMNS, start=1):
        if header not in DATE_COLUMNS:
            continue
        letter = get_column_letter(col_idx)
        for row_idx in range(2, ws.max_row + 1):
            ws[f'{letter}{row_idx}'].number_format = 'YYYY-MM-DD'

    for col_idx, header in enumerate(COLUMNS, start=1):
        cells = [ws.cell(row=r, column=col_idx).value
                 for r in range(2, ws.max_row + 1)] if ws.max_row > 1 else []
        max_len = max(len(str(header)),
                      *(len(str(v or '')) for v in cells)) if cells else len(header)
        cap = 40 if header == 'product_name' else 24
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(max_len + 2, 10), cap)

    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions

    meta = wb.create_sheet('Metadata')
    meta.append(['Parameter', 'Value'])
    for cell in meta[1]:
        cell.font = header_font
        cell.fill = header_fill
    meta.append(['as_of_date (today)', as_of.isoformat()])
    for label, value in scope_notes:
        meta.append([label, value])
    meta.append(['import definition',
                 'voucher vou_class=0, slip_flag=0, held=0, status=4, vou_type=0 '
                 '(vendor returns excluded)'])
    meta.append(['avg_import_date', 'quantity-weighted mean of receipt dates'])
    meta.append(['SKU rows', len(rows)])
    meta.append(['SKUs with >1 import date',
                 sum(1 for r in rows if r['distinct_import_dates'] > 1)])
    meta.append(['SKUs never imported',
                 sum(1 for r in rows if r['import_events'] == 0)])
    meta.append(['distinct seasons', len({r['season'] for r in rows if r['season']})])
    meta.column_dimensions['A'].width = 32
    meta.column_dimensions['B'].width = 78

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--seasons', default=None,
                        help='Comma-separated season codes (default: all seasons)')
    parser.add_argument('--start-date', default=None,
                        help='Only count imports on/after this date (default: no bound)')
    parser.add_argument('--end-date', default=None,
                        help='Only count imports on/before this date (default: no bound)')
    parser.add_argument('--all-skus', action='store_true',
                        help='Include SKUs that were never imported (blank dates). '
                             'Default: only SKUs with at least one receipt.')
    parser.add_argument('--out-dir', default='document/reports',
                        help='Output directory (default: document/reports)')
    args = parser.parse_args()

    seasons = ([s.strip() for s in args.seasons.split(',') if s.strip()]
               if args.seasons else None)

    as_of = date.today()
    sql, params = build_query(args.all_skus, seasons, args.start_date, args.end_date)

    scope = '_'.join(seasons) if seasons else 'all'
    if args.all_skus:
        scope += '_allskus'

    print(f'[1/2] Querying SKU import dates (scope: {scope}) ...')
    rows = shape_rows(fetch_rows(sql, params))
    print(f'      -> {len(rows)} SKU rows')

    scope_notes = [
        ('seasons', ','.join(seasons) if seasons else 'all'),
        ('import window start', args.start_date or 'unbounded'),
        ('import window end', args.end_date or 'unbounded'),
        ('SKU cohort',
         'every catalog SKU' if args.all_skus else 'SKUs with >=1 receipt'),
    ]

    out_path = Path(args.out_dir) / f'sku_import_date_{scope}_{as_of.isoformat()}.xlsx'
    print(f'[2/2] Writing {out_path} ...')
    write_xlsx(rows, out_path, as_of, scope_notes)

    multi = sum(1 for r in rows if r['distinct_import_dates'] > 1)
    print(f'\nDone. Written to:')
    print(f'  {out_path.resolve()}')
    print(f'  SKU rows:                 {len(rows):,}')
    print(f'  SKUs imported >1 date:    {multi:,}')
    print(f'  SKUs never imported:      {sum(1 for r in rows if r["import_events"] == 0):,}')
    print(f'  Distinct seasons:         {len({r["season"] for r in rows if r["season"]}):,}')


if __name__ == '__main__':
    main()
