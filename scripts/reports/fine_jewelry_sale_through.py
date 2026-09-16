"""
One-off: fine-jewelry sale-through report (brand x season x category).

Filters to the 10 fine-jewelry vendor codes used by the commission module:
    VHN, ROM, ATS, VIS, LUI, NAN, NAK, SPK, TED, BRT

Combines two Oracle queries:
  - Sales rollup     : DOCUMENT_ITEM   -> sold_qty, sold_revenue_before_vat
  - PO rollup        : PO_ITEM         -> imported_qty (ord), received_qty (rcvd),
                                         landed_cost_ordered, landed_cost_received
Merges them in Python (full outer join on brand x season x category) and writes
a CSV to document/fine_jewelry_sale_through_<YYYY-MM-DD>.csv.

Landed cost note: invn_sbs_item.UDF1_STRING stores landed cost as a per-unit
price on the item master. The two landed_cost_* columns are SUM(qty * unit cost)
across all SKUs in the bucket -- i.e. total landed value, not a per-unit average.
"""
import csv
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from app.core.database import get_oracle_connection

JEWELRY_VENDORS = ('VHN', 'ROM', 'ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'SPK', 'TED', 'BRT')
_VENDOR_LIST_SQL = ", ".join(f"'{v}'" for v in JEWELRY_VENDORS)

SALES_QUERY = f"""
    SELECT
        v.vend_code                                                       AS vend_code,
        v.vend_name                                                       AS brand,
        i.UDF5_STRING                                                     AS season,
        ie.udf8_string                                                    AS category,
        SUM(CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END)  AS sold_qty,
        SUM(ROUND(
            (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END)
            * (di.price - di.tax_amt), 0))                                AS sold_revenue_before_vat
    FROM rps.document d
    JOIN rps.document_item di    ON d.sid = di.doc_sid
    JOIN rps.invn_sbs_item i     ON i.sid = di.invn_sbs_item_sid
    LEFT JOIN rps.invn_sbs_extend ie ON ie.invn_sbs_item_sid = i.sid
    LEFT JOIN rps.vendor v       ON v.sid = i.vend_sid
    WHERE d.status = 4
      AND d.receipt_type IN (0, 1)
      AND di.item_type IN (1, 2)
      AND di.vend_code IN ({_VENDOR_LIST_SQL})
    GROUP BY v.vend_code, v.vend_name, i.UDF5_STRING, ie.udf8_string
"""

PO_QUERY = rf"""
    SELECT
        v.vend_code                                          AS vend_code,
        v.vend_name                                          AS brand,
        i.UDF5_STRING                                        AS season,
        ie.udf8_string                                       AS category,
        SUM(pi.ord_qty)                                      AS imported_qty,
        SUM(COALESCE(pi.rcvd_qty, 0))                        AS received_qty,
        SUM(pi.ord_qty
            * CASE
                WHEN REGEXP_LIKE(i.UDF1_STRING, '^[0-9]+(\.[0-9]+)?$')
                THEN TO_NUMBER(i.UDF1_STRING)
              END)                                           AS landed_cost_ordered,
        SUM(COALESCE(pi.rcvd_qty, 0)
            * CASE
                WHEN REGEXP_LIKE(i.UDF1_STRING, '^[0-9]+(\.[0-9]+)?$')
                THEN TO_NUMBER(i.UDF1_STRING)
              END)                                           AS landed_cost_received
    FROM rps.po po
    JOIN rps.po_item pi          ON pi.po_sid = po.sid
    JOIN rps.vendor v            ON po.vendor_sid = v.sid
    JOIN rps.invn_sbs_item i     ON pi.item_sid = i.sid
    LEFT JOIN rps.invn_sbs_extend ie ON ie.invn_sbs_item_sid = i.sid
    WHERE v.vend_code IN ({_VENDOR_LIST_SQL})
    GROUP BY v.vend_code, v.vend_name, i.UDF5_STRING, ie.udf8_string
"""


def _norm(value):
    """Treat NULL/empty season or category as the literal string '(unknown)' for grouping."""
    if value is None:
        return '(unknown)'
    s = str(value).strip()
    return s if s else '(unknown)'


def _fetch(conn, query):
    cur = conn.cursor()
    try:
        cur.execute(query)
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        cur.close()


def main():
    conn = get_oracle_connection()
    if conn is None:
        print('ERROR: failed to connect to Oracle', file=sys.stderr)
        sys.exit(1)

    try:
        print('Running sales query...')
        sales = _fetch(conn, SALES_QUERY)
        print(f'  {len(sales)} sale rows')

        print('Running PO query...')
        pos = _fetch(conn, PO_QUERY)
        print(f'  {len(pos)} PO rows')
    finally:
        conn.close()

    merged = {}

    def _key(r):
        return (r['vend_code'], _norm(r['brand']), _norm(r['season']), _norm(r['category']))

    for r in sales:
        k = _key(r)
        merged.setdefault(k, {
            'vend_code': r['vend_code'], 'brand': _norm(r['brand']),
            'season': _norm(r['season']), 'category': _norm(r['category']),
            'sold_qty': 0, 'sold_revenue_before_vat': 0,
            'imported_qty': 0, 'received_qty': 0,
            'landed_cost_ordered': 0, 'landed_cost_received': 0,
        })
        merged[k]['sold_qty'] = int(r['sold_qty'] or 0)
        merged[k]['sold_revenue_before_vat'] = int(r['sold_revenue_before_vat'] or 0)

    for r in pos:
        k = _key(r)
        merged.setdefault(k, {
            'vend_code': r['vend_code'], 'brand': _norm(r['brand']),
            'season': _norm(r['season']), 'category': _norm(r['category']),
            'sold_qty': 0, 'sold_revenue_before_vat': 0,
            'imported_qty': 0, 'received_qty': 0,
            'landed_cost_ordered': 0, 'landed_cost_received': 0,
        })
        merged[k]['imported_qty'] = int(r['imported_qty'] or 0)
        merged[k]['received_qty'] = int(r['received_qty'] or 0)
        merged[k]['landed_cost_ordered'] = (
            float(r['landed_cost_ordered']) if r['landed_cost_ordered'] is not None else 0
        )
        merged[k]['landed_cost_received'] = (
            float(r['landed_cost_received']) if r['landed_cost_received'] is not None else 0
        )

    rows = list(merged.values())
    for r in rows:
        imp = r['imported_qty'] or 0
        rcv = r['received_qty'] or 0
        sold = r['sold_qty'] or 0
        r['sell_through_pct_vs_received'] = round(sold / rcv * 100, 2) if rcv else None
        r['sell_through_pct_vs_imported'] = round(sold / imp * 100, 2) if imp else None
        r['fulfillment_pct'] = round(rcv / imp * 100, 2) if imp else None

    rows.sort(key=lambda r: (r['brand'], r['season'], r['category']))

    out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'document'))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'fine_jewelry_sale_through_{datetime.now():%Y-%m-%d}.csv')

    headers = [
        'vend_code', 'brand', 'season', 'category',
        'imported_qty', 'received_qty', 'fulfillment_pct',
        'sold_qty', 'sold_revenue_before_vat',
        'sell_through_pct_vs_received', 'sell_through_pct_vs_imported',
        'landed_cost_ordered', 'landed_cost_received',
    ]
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h) for h in headers})

    print(f'\nWrote {len(rows)} rows -> {out_path}')


if __name__ == '__main__':
    main()
