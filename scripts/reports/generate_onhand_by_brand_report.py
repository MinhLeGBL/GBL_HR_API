"""
Master-data Excel report: on-hand stock for one brand, by SKU and store.

One row per SKU that currently has stock. Quantities come from
INVN_SBS_ITEM_QTY, which holds a row per (item, store), so the store columns
are the real split rather than an allocation — a SKU with 3 units across two
stores shows exactly where they are.

Zero-stock SKUs are excluded by default: the question this answers is "what
can we sell today", and a catalogue of 77 SKUs where 34 have none buries the
43 that matter. `--include-zero` keeps them.

Columns:
  upc, product_code, product_name, season, item_size, category
  <STORE> ...        on-hand units at each store that holds any
  total_on_hand      sum across stores
  retail_price       INVN_SBS_PRICE.PRICE — the ticket price (VAT-inclusive)
  stock_value        total_on_hand x retail_price
  import_count       number of receiving-voucher LINES for this SKU
  import_dates       distinct dates it was received on — a SKU reordered three
                     times reads 3 here even if one delivery split into lines
  imported_qty       total units ever received
  last_import        most recent receipt date
  last_received      INVN_SBS_ITEM.first_rcvd_date
  units_sold_12m     sales in the last 12 months, for context on ageing

Output: document/reports/onhand_<brand>_<category>_<today>.xlsx
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_oracle_connection


QUERY = r"""
WITH sold AS (
    SELECT di.INVN_SBS_ITEM_SID AS item_sid,
           SUM(CASE WHEN di.ITEM_TYPE = 2 THEN -di.QTY ELSE di.QTY END) AS qty
    FROM RPS.DOCUMENT d
    JOIN RPS.DOCUMENT_ITEM di ON di.DOC_SID = d.SID
    WHERE d.STATUS = 4 AND d.RECEIPT_TYPE IN (0, 1) AND di.ITEM_TYPE IN (1, 2)
      AND d.INVC_POST_DATE >= ADD_MONTHS(TRUNC(SYSDATE), -12)
    GROUP BY di.INVN_SBS_ITEM_SID
),
-- "Import" = a posted receiving voucher line. Same predicate the SKU import
-- date report uses, so the two reports agree: vendor returns (vou_type=1) are
-- excluded, held and slip vouchers too.
imports AS (
    SELECT vi.item_sid                        AS item_sid,
           COUNT(*)                           AS import_events,
           COUNT(DISTINCT TRUNC(vh.post_date)) AS import_dates,
           SUM(vi.qty)                        AS imported_qty,
           MAX(TRUNC(vh.post_date))           AS last_import
    FROM RPS.VOUCHER vh
    JOIN RPS.VOU_ITEM vi ON vi.vou_sid = vh.SID
    WHERE vh.vou_class = 0 AND vh.slip_flag = 0 AND vh.held = 0
      AND vh.status = 4 AND vh.vou_type = 0 AND vh.post_date IS NOT NULL
    GROUP BY vi.item_sid
)
SELECT
    TO_CHAR(i.UPC)                                  AS upc,
    i.ALU                                           AS product_code,
    i.TEXT1                                         AS product_name,
    i.UDF5_STRING                                   AS season,
    i.ITEM_SIZE                                     AS item_size,
    SUBSTR(i.ALU, INSTR(i.ALU, '-', -1) + 1)        AS category,
    NVL(s.STORE_CODE, '(unassigned)')               AS store_code,
    NVL(q.QTY, 0)                                   AS on_hand,
    ROUND(pr.price)                                 AS retail_price,
    TO_CHAR(i.FIRST_RCVD_DATE, 'YYYY-MM-DD')        AS last_received,
    NVL(sold.qty, 0)                                AS units_sold_12m,
    NVL(imp.import_events, 0)                       AS import_count,
    NVL(imp.import_dates, 0)                        AS import_dates,
    NVL(imp.imported_qty, 0)                        AS imported_qty,
    TO_CHAR(imp.last_import, 'YYYY-MM-DD')          AS last_import
FROM RPS.INVN_SBS_ITEM i
JOIN RPS.VENDOR v           ON v.SID = i.VEND_SID
JOIN RPS.INVN_SBS_ITEM_QTY q ON q.INVN_SBS_ITEM_SID = i.SID
LEFT JOIN RPS.STORE s       ON s.SID = q.STORE_SID
LEFT JOIN sold              ON sold.item_sid = i.SID
LEFT JOIN imports imp       ON imp.item_sid = i.SID
-- Retail price lives in INVN_SBS_PRICE, not on the item: INVN_SBS_ITEM.FST_PRICE
-- is NULL throughout this dataset. MAX() because an item can carry several
-- price-level rows; the highest is the ticket price before any level discount.
LEFT JOIN (SELECT INVN_SBS_ITEM_SID sid, MAX(PRICE) price
           FROM RPS.INVN_SBS_PRICE GROUP BY INVN_SBS_ITEM_SID) pr
       ON pr.sid = i.SID
WHERE UPPER(TRIM(v.VEND_NAME)) = :brand
  {category_filter}
ORDER BY i.ALU, i.TEXT1
"""


def fetch(brand, category):
    cat_filter = ''
    params = {'brand': brand.upper()}
    if category:
        # The category is the last segment of the product code, e.g.
        # FAS-SS25-WACC-SUN -> SUN.
        cat_filter = "AND UPPER(SUBSTR(i.ALU, INSTR(i.ALU, '-', -1) + 1)) = :cat"
        params['cat'] = category.upper()

    conn = get_oracle_connection()
    if conn is None:
        raise RuntimeError('Could not open Oracle connection')
    try:
        cur = conn.cursor()
        try:
            cur.execute(QUERY.format(category_filter=cat_filter), params)
            cols = [d[0].lower() for d in cur.description]
            rows = cur.fetchall()
        finally:
            cur.close()
    finally:
        conn.close()
    return [dict(zip(cols, r)) for r in rows]


def pivot(raw, include_zero):
    """Collapse the per-store rows into one row per SKU with store columns."""
    stores = sorted({r['store_code'] for r in raw if float(r['on_hand'] or 0) != 0})
    by_sku = {}
    for r in raw:
        key = (r['upc'], r['product_code'], r['product_name'], r['item_size'])
        row = by_sku.setdefault(key, {
            'upc': r['upc'] or '', 'product_code': r['product_code'] or '',
            'product_name': r['product_name'] or '', 'season': r['season'] or '',
            'item_size': r['item_size'] or '', 'category': r['category'] or '',
            'retail_price': float(r['retail_price'] or 0),
            'last_received': r['last_received'] or '',
            'units_sold_12m': float(r['units_sold_12m'] or 0),
            'import_count': int(r['import_count'] or 0),
            'import_dates': int(r['import_dates'] or 0),
            'imported_qty': float(r['imported_qty'] or 0),
            'last_import': r['last_import'] or '',
            **{s: 0.0 for s in stores},
        })
        qty = float(r['on_hand'] or 0)
        if r['store_code'] in row:
            row[r['store_code']] += qty

    out = []
    for row in by_sku.values():
        row['total_on_hand'] = sum(row[s] for s in stores)
        row['stock_value'] = round(row['total_on_hand'] * row['retail_price'])
        if include_zero or row['total_on_hand'] > 0:
            out.append(row)
    out.sort(key=lambda r: (-r['total_on_hand'], r['product_code']))
    return out, stores


def write_xlsx(rows, stores, out_path, brand, category, as_of):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    cols = (['upc', 'product_code', 'product_name', 'season', 'item_size']
            + stores + ['total_on_hand', 'retail_price', 'stock_value',
                        'import_count', 'import_dates', 'imported_qty',
                        'last_import', 'units_sold_12m', 'last_received'])
    wb = Workbook()
    ws = wb.active
    ws.title = 'On hand'
    ws.append(cols)

    hf = Font(bold=True, color='FFFFFF')
    hfill = PatternFill('solid', start_color='305496', end_color='305496')
    for c in ws[1]:
        c.font = hf
        c.fill = hfill
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

    for r in rows:
        ws.append([r.get(c, '') for c in cols])

    # Flag SKUs sitting on stock with no sale in a year — the reason to read
    # this report at all is usually to find those.
    stale = PatternFill('solid', start_color='FCE4E4', end_color='FCE4E4')
    for idx, r in enumerate(rows, start=2):
        if r['total_on_hand'] > 0 and r['units_sold_12m'] == 0:
            for c in ws[idx]:
                c.fill = stale

    total_row = ['TOTAL', '', '', '', ''] + \
                [sum(r[s] for r in rows) for s in stores] + \
                [sum(r['total_on_hand'] for r in rows), '',
                 sum(r['stock_value'] for r in rows),
                 sum(r['import_count'] for r in rows), '',
                 sum(r['imported_qty'] for r in rows), '',
                 sum(r['units_sold_12m'] for r in rows), '']
    ws.append(total_row)
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)

    widths = ([14, 26, 42, 8, 9] + [7] * len(stores)
              + [9, 14, 16, 9, 9, 10, 13, 9, 14])
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    money_cols = [cols.index('retail_price') + 1, cols.index('stock_value') + 1]
    for r in range(2, ws.max_row + 1):
        for mc in money_cols:
            ws.cell(r, mc).number_format = '#,##0'
    ws.freeze_panes = 'C2'
    ws.auto_filter.ref = f'A1:{get_column_letter(len(cols))}{ws.max_row - 1}'

    meta = wb.create_sheet('Metadata')
    meta.append(['Parameter', 'Value'])
    for c in meta[1]:
        c.font = hf
        c.fill = hfill
    meta.append(['brand', brand])
    meta.append(['category', category or 'all'])
    meta.append(['as_of_date', as_of.isoformat()])
    meta.append(['stock source',
                 'INVN_SBS_ITEM_QTY.QTY — live on-hand, one row per item x store'])
    meta.append(['store columns', ', '.join(stores)])
    meta.append(['retail_price',
                 'INVN_SBS_PRICE.PRICE — the ticket price. NOTE: INVN_SBS_ITEM.'
                 'FST_PRICE is NULL throughout this database, so it cannot be used.'])
    meta.append(['units_sold_12m',
                 'net units sold in the last 12 months (returns subtract)'])
    meta.append(['import definition',
                 'posted receiving voucher: vou_class=0, slip_flag=0, held=0, '
                 'status=4, vou_type=0 (vendor returns excluded)'])
    meta.append(['import_count vs import_dates',
                 'count counts voucher LINES, dates counts distinct days — a '
                 'single delivery split across lines inflates the first, not '
                 'the second. Read import_dates as "how many times reordered".'])
    meta.append(['red rows', 'stock on hand but ZERO sales in 12 months'])
    meta.append(['SKUs with stock', len(rows)])
    meta.append(['total units', sum(r['total_on_hand'] for r in rows)])
    meta.append(['total stock value (VND)', sum(r['stock_value'] for r in rows)])
    meta.column_dimensions['A'].width = 26
    meta.column_dimensions['B'].width = 76

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--brand', required=True, help='VENDOR.VEND_NAME, exact')
    parser.add_argument('--category', default=None,
                        help="Product-code suffix, e.g. SUN, OPT, DRS. Default: all")
    parser.add_argument('--include-zero', action='store_true',
                        help='Keep SKUs with no stock (default: drop them)')
    parser.add_argument('--out-dir', default='document/reports')
    args = parser.parse_args()

    as_of = date.today()
    print(f'[1/3] Querying on-hand for {args.brand}'
          + (f' / {args.category}' if args.category else '') + ' ...')
    raw = fetch(args.brand, args.category)
    if not raw:
        print('No items found. Check the brand name against VENDOR.VEND_NAME.')
        return 1
    print(f'      -> {len(raw)} item x store rows')

    print('[2/3] Pivoting by store ...')
    rows, stores = pivot(raw, args.include_zero)
    print(f'      -> {len(rows)} SKUs across {", ".join(stores) or "no store"}')

    slug = args.brand.lower().replace(' ', '_').replace("'", '')
    tag = f'_{args.category.lower()}' if args.category else ''
    out_path = Path(args.out_dir) / f'onhand_{slug}{tag}_{as_of.isoformat()}.xlsx'
    print(f'[3/3] Writing {out_path} ...')
    write_xlsx(rows, stores, out_path, args.brand, args.category, as_of)

    total = sum(r['total_on_hand'] for r in rows)
    value = sum(r['stock_value'] for r in rows)
    dead = sum(1 for r in rows if r['units_sold_12m'] == 0)
    print(f'\nDone. Written to:\n  {out_path.resolve()}')
    print(f'  SKUs with stock:   {len(rows)}')
    print(f'  Units on hand:     {total:,.0f}')
    print(f'  Stock value:       {value:,.0f} VND')
    print(f'  No sale in 12m:    {dead} SKUs')
    return 0


if __name__ == '__main__':
    sys.exit(main())
