"""
Custom Excel report: Vhernier (VHN) import & sale statistics — BY SHIPMENT.

Grain: one row per receiving shipment (VOUCHER, vou_class=0 receipt), split by
season when a voucher spans seasons. Scope: brand VHN (VEND_CODE='VHN'), the
three 2026 seasons PS26 / PF26 / SS26 (INVN_SBS_ITEM.UDF5_STRING). All-time.

Attributing sales to a shipment: a SKU is received across several vouchers, so
sales are matched to shipments by **FIFO** — the branch's established rule
(see app/modules/custom_reports/service._fifo_match). Per SKU, outflows (sales
and vendor returns, in date order) consume the oldest received units first:
  - a SALE consuming a unit  → that unit's shipment gets +1 sold and +its ex-VAT
    revenue (unit revenue = di.price − di.tax_amt);
  - a VENDOR RETURN consuming a unit → that unit's shipment gets +1 returned;
  - whatever is left in a shipment's batch is still on hand.

Per-shipment columns:
  season, post_date, vou_no, po_no, shipment_no, skus,
  qty_received     units received on this voucher (VOU_ITEM.qty)
  import_cost      Σ(qty × VOU_ITEM.COST)  — raw PO/receiving cost
  qty_sold         FIFO-attributed units sold from this shipment
  revenue_before_vat  FIFO-attributed ex-VAT revenue of those sales
  roi_pct          (revenue_before_vat − import_cost) / import_cost × 100
                   → realized ROI on this shipment's FULL import spend
  sellthrough_pct  qty_sold / qty_received × 100
  qty_returned_vendor, qty_on_hand   (context)

Cost = VOU_ITEM.COST (raw PO/invoice cost; landed UDF1_STRING ~2% higher, not
used). Revenue ex-VAT via actual per-line tax (di.price − di.tax_amt); document
discount not applied (branch convention). Sales: DOCUMENT status=4,
receipt_type IN (0,1). Receipts: VOUCHER vou_class=0, status=4, vou_type=0
(receipt) / 1 (vendor return).

Output: document/reports/vhn_import_sale_by_shipment_<today>.xlsx
"""
import argparse
import sys
from collections import deque
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_oracle_connection

BRAND_CODE = 'VHN'
DEFAULT_SEASONS = ['PS26', 'PF26', 'SS26']


def _season_binds(seasons):
    names = [f's{i}' for i in range(len(seasons))]
    ph = ', '.join(f':{n}' for n in names)
    return ph, dict(zip(names, seasons))


def _receipts_sql(ph):
    # One row per receipt batch line (vou_type=0). qty>0.
    return f"""
    SELECT i.SID AS item_sid, i.UDF5_STRING AS season,
           vh.SID AS vou_sid, vh.VOU_NO AS vou_no, vh.PO_NO AS po_no,
           vh.SHIPMENT_NO AS shipment_no, TRUNC(vh.post_date) AS post_date,
           vi.qty AS qty, vi.COST AS unit_cost
    FROM INVN_SBS_ITEM i
    JOIN VENDOR v      ON v.SID = i.VEND_SID AND v.VEND_CODE = :brand
    JOIN VOU_ITEM vi   ON vi.item_sid = i.SID
    JOIN VOUCHER vh    ON vh.SID = vi.vou_sid
                      AND vh.vou_class = 0 AND vh.status = 4 AND vh.vou_type = 0
    WHERE i.UDF5_STRING IN ({ph}) AND vi.qty > 0
    """


def _vendor_returns_sql(ph):
    return f"""
    SELECT i.SID AS item_sid, TRUNC(vh.post_date) AS event_date, vi.qty AS qty
    FROM INVN_SBS_ITEM i
    JOIN VENDOR v    ON v.SID = i.VEND_SID AND v.VEND_CODE = :brand
    JOIN VOU_ITEM vi ON vi.item_sid = i.SID
    JOIN VOUCHER vh  ON vh.SID = vi.vou_sid
                    AND vh.vou_class = 0 AND vh.status = 4 AND vh.vou_type = 1
    WHERE i.UDF5_STRING IN ({ph}) AND vi.qty > 0
    """


def _sales_sql(ph):
    return f"""
    SELECT b.INVN_SBS_ITEM_SID AS item_sid,
           TRUNC(a.invc_post_date) AS event_date,
           b.qty AS qty,
           (b.price - NVL(b.tax_amt, 0)) AS unit_revenue
    FROM INVN_SBS_ITEM i
    JOIN VENDOR v         ON v.SID = i.VEND_SID AND v.VEND_CODE = :brand
    JOIN DOCUMENT_ITEM b  ON b.INVN_SBS_ITEM_SID = i.SID AND b.item_type = 1
    JOIN DOCUMENT a       ON a.SID = b.DOC_SID
                         AND a.status = 4 AND a.receipt_type IN (0, 1)
    WHERE i.UDF5_STRING IN ({ph}) AND b.qty > 0
    """


def _fetch(cur, sql, binds):
    cur.execute(sql, binds)
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_data(seasons):
    ph, sbinds = _season_binds(seasons)
    binds = {'brand': BRAND_CODE, **sbinds}
    conn = get_oracle_connection()
    if conn is None:
        raise RuntimeError('Could not open Oracle connection')
    try:
        cur = conn.cursor()
        try:
            receipts = _fetch(cur, _receipts_sql(ph), binds)
            vendor_returns = _fetch(cur, _vendor_returns_sql(ph), binds)
            sales = _fetch(cur, _sales_sql(ph), binds)
        finally:
            cur.close()
    finally:
        conn.close()
    return receipts, vendor_returns, sales


def _d(v):
    """Oracle DATE → date."""
    return v.date() if hasattr(v, 'date') else v


def fifo_attribute(receipts, vendor_returns, sales):
    """
    Per SKU, consume the oldest received units first with outflows (sales then
    vendor returns) ordered by date. Mutates a per-shipment accumulator keyed by
    vou_sid. Returns {vou_sid: {qty_sold, revenue, qty_returned_vendor}} and a
    reconciliation dict.
    """
    # Build FIFO receipt batches per item, oldest first.
    batches_by_item = {}
    for r in receipts:
        batches_by_item.setdefault(int(r['item_sid']), []).append({
            'date': _d(r['post_date']),
            'qty': int(r['qty']),
            'vou_sid': int(r['vou_sid']),
        })
    for item_sid, lst in batches_by_item.items():
        lst.sort(key=lambda b: b['date'])

    # Outflow events per item: sales (with unit revenue) + vendor returns.
    outflows_by_item = {}
    for s in sales:
        outflows_by_item.setdefault(int(s['item_sid']), []).append({
            'date': _d(s['event_date']), 'qty': int(s['qty']),
            'kind': 'sale', 'unit_revenue': float(s['unit_revenue'] or 0),
        })
    for vr in vendor_returns:
        outflows_by_item.setdefault(int(vr['item_sid']), []).append({
            'date': _d(vr['event_date']), 'qty': int(vr['qty']),
            'kind': 'return', 'unit_revenue': 0.0,
        })

    acc = {}  # vou_sid -> dict

    def _ship(vou_sid):
        return acc.setdefault(vou_sid, {
            'qty_sold': 0, 'revenue': 0.0, 'qty_returned_vendor': 0})

    recon = {'sold_matched': 0, 'sold_total': sum(int(s['qty']) for s in sales),
             'revenue_matched': 0.0,
             'revenue_total': sum(int(s['qty']) * float(s['unit_revenue'] or 0) for s in sales),
             'vr_matched': 0, 'vr_total': sum(int(v['qty']) for v in vendor_returns),
             'sold_unmatched': 0, 'vr_unmatched': 0}

    for item_sid, outflows in outflows_by_item.items():
        queue = deque(batches_by_item.get(item_sid, []))
        # Sales before vendor returns on the same day (arbitrary but stable);
        # primarily ordered by date.
        outflows.sort(key=lambda o: (o['date'], 0 if o['kind'] == 'sale' else 1))
        for o in outflows:
            remaining = o['qty']
            while remaining > 0 and queue:
                b = queue[0]
                take = remaining if remaining <= b['qty'] else b['qty']
                ship = _ship(b['vou_sid'])
                if o['kind'] == 'sale':
                    ship['qty_sold'] += take
                    ship['revenue'] += take * o['unit_revenue']
                    recon['sold_matched'] += take
                    recon['revenue_matched'] += take * o['unit_revenue']
                else:
                    ship['qty_returned_vendor'] += take
                    recon['vr_matched'] += take
                remaining -= take
                b['qty'] -= take
                if b['qty'] == 0:
                    queue.popleft()
            if remaining > 0:  # outflow with no receipt to draw from (anomaly)
                if o['kind'] == 'sale':
                    recon['sold_unmatched'] += remaining
                else:
                    recon['vr_unmatched'] += remaining

    return acc, recon


def build_shipment_rows(receipts, acc):
    """Aggregate receipt batches to (season, vou_sid) shipment rows and fold in
    the FIFO-attributed sold/revenue/returns."""
    ships = {}
    for r in receipts:
        key = (r['season'], int(r['vou_sid']))
        row = ships.setdefault(key, {
            'season': r['season'], 'vou_sid': int(r['vou_sid']),
            'vou_no': r['vou_no'], 'po_no': r['po_no'],
            'shipment_no': r['shipment_no'], 'post_date': _d(r['post_date']),
            'item_sids': set(), 'qty_received': 0, 'import_cost': 0.0,
        })
        row['item_sids'].add(int(r['item_sid']))
        row['qty_received'] += int(r['qty'])
        row['import_cost'] += int(r['qty']) * float(r['unit_cost'] or 0)
        row['post_date'] = min(row['post_date'], _d(r['post_date']))

    out = []
    for (season, vou_sid), row in ships.items():
        a = acc.get(vou_sid, {})
        qty_sold = int(a.get('qty_sold', 0))
        revenue = a.get('revenue', 0.0)
        qty_vr = int(a.get('qty_returned_vendor', 0))
        import_cost = round(row['import_cost'])
        qty_recv = row['qty_received']
        roi = round((revenue - import_cost) / import_cost * 100, 1) if import_cost else None
        st = round(qty_sold / qty_recv * 100, 1) if qty_recv else None
        out.append({
            'season': season, 'post_date': row['post_date'],
            'vou_no': row['vou_no'], 'po_no': row['po_no'],
            'shipment_no': row['shipment_no'], 'skus': len(row['item_sids']),
            'qty_received': qty_recv, 'import_cost': import_cost,
            'qty_sold': qty_sold, 'revenue_before_vat': round(revenue),
            'roi_pct': roi, 'sellthrough_pct': st,
            'qty_returned_vendor': qty_vr,
            'qty_on_hand': qty_recv - qty_sold - qty_vr,
        })
    out.sort(key=lambda r: (r['season'], r['post_date'], r['vou_no'] or ''))
    return out


HEADERS = ['season', 'post_date', 'vou_no', 'po_no', 'shipment_no', 'skus',
           'qty_received', 'import_cost', 'qty_sold', 'revenue_before_vat',
           'roi_pct', 'sellthrough_pct', 'qty_returned_vendor', 'qty_on_hand']


def write_xlsx(rows, out_path, seasons, as_of, recon):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='305496', end_color='305496', fill_type='solid')
    bold = Font(bold=True)

    def _style_header(ws):
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center')

    def _autosize(ws):
        for ci in range(1, ws.max_column + 1):
            vals = [ws.cell(row=r, column=ci).value for r in range(1, ws.max_row + 1)]
            w = max((len(str(v)) for v in vals if v is not None), default=10)
            ws.column_dimensions[get_column_letter(ci)].width = min(max(w + 2, 11), 40)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Shipments'
    ws.append(HEADERS)
    _style_header(ws)
    for r in rows:
        ws.append([
            r['season'], r['post_date'].isoformat() if r['post_date'] else '',
            r['vou_no'], r['po_no'], r['shipment_no'], r['skus'],
            r['qty_received'], r['import_cost'], r['qty_sold'],
            r['revenue_before_vat'], r['roi_pct'], r['sellthrough_pct'],
            r['qty_returned_vendor'], r['qty_on_hand'],
        ])

    money = [HEADERS.index(h) + 1 for h in ('import_cost', 'revenue_before_vat')]
    pct = [HEADERS.index(h) + 1 for h in ('roi_pct', 'sellthrough_pct')]
    for ri in range(2, ws.max_row + 1):
        for c in money:
            ws.cell(row=ri, column=c).number_format = '#,##0'
        for c in pct:
            ws.cell(row=ri, column=c).number_format = '0.0"%"'
    ws.freeze_panes = 'A2'
    _autosize(ws)

    # Season summary sheet (rolled from shipment rows).
    ws2 = wb.create_sheet('Season Summary')
    sh = ['season', 'shipments', 'qty_received', 'import_cost', 'qty_sold',
          'revenue_before_vat', 'roi_pct', 'sellthrough_pct',
          'qty_returned_vendor', 'qty_on_hand']
    ws2.append(sh)
    _style_header(ws2)
    by_season = {}
    for r in rows:
        s = by_season.setdefault(r['season'], {'shipments': 0, 'qty_received': 0,
            'import_cost': 0, 'qty_sold': 0, 'revenue_before_vat': 0,
            'qty_returned_vendor': 0, 'qty_on_hand': 0})
        s['shipments'] += 1
        for k in ('qty_received', 'import_cost', 'qty_sold', 'revenue_before_vat',
                  'qty_returned_vendor', 'qty_on_hand'):
            s[k] += r[k]
    for season in sorted(by_season):
        s = by_season[season]
        roi = round((s['revenue_before_vat'] - s['import_cost']) / s['import_cost'] * 100, 1) if s['import_cost'] else None
        st = round(s['qty_sold'] / s['qty_received'] * 100, 1) if s['qty_received'] else None
        ws2.append([season, s['shipments'], s['qty_received'], s['import_cost'],
                    s['qty_sold'], s['revenue_before_vat'], roi, st,
                    s['qty_returned_vendor'], s['qty_on_hand']])
    for ri in range(2, ws2.max_row + 1):
        for h in ('import_cost', 'revenue_before_vat'):
            ws2.cell(row=ri, column=sh.index(h) + 1).number_format = '#,##0'
        for h in ('roi_pct', 'sellthrough_pct'):
            ws2.cell(row=ri, column=sh.index(h) + 1).number_format = '0.0"%"'
    ws2.freeze_panes = 'A2'
    _autosize(ws2)

    # Metadata
    meta = wb.create_sheet('Metadata')
    meta.append(['Parameter', 'Value'])
    _style_header(meta)
    for k, v in [
        ('brand', f'{BRAND_CODE} (VHERNIER)'),
        ('seasons', ', '.join(seasons)),
        ('as_of_date', as_of.isoformat()),
        ('grain', 'one row per receiving shipment (VOUCHER vou_class=0, vou_type=0), split by season'),
        ('sale attribution', 'FIFO — sales & vendor returns consume oldest received units first, per SKU'),
        ('import_cost', 'Σ(qty × VOU_ITEM.COST) — raw PO/invoice cost (landed UDF1 ~2% higher, not used)'),
        ('revenue_before_vat', 'FIFO-attributed Σ(unit × (di.price − di.tax_amt)); doc discount not applied'),
        ('roi_pct', '(revenue_before_vat − import_cost) / import_cost × 100 — realized ROI on this shipment’s full import spend'),
        ('sellthrough_pct', 'qty_sold / qty_received × 100'),
        ('customer returns', 'none in this cohort (item_type=2 count = 0)'),
        ('recon: sold matched/total', f"{recon['sold_matched']} / {recon['sold_total']} (unmatched {recon['sold_unmatched']})"),
        ('recon: revenue matched/total', f"{round(recon['revenue_matched']):,} / {round(recon['revenue_total']):,}"),
        ('recon: vendor-ret matched/total', f"{recon['vr_matched']} / {recon['vr_total']} (unmatched {recon['vr_unmatched']})"),
        ('shipment rows', len(rows)),
    ]:
        meta.append([k, v])
    meta.column_dimensions['A'].width = 28
    meta.column_dimensions['B'].width = 98

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main():
    ap = argparse.ArgumentParser(description='VHN import & sale by shipment (FIFO)')
    ap.add_argument('--seasons', nargs='+', default=DEFAULT_SEASONS)
    ap.add_argument('--out-dir', default='document/reports')
    args = ap.parse_args()

    as_of = date.today()
    print(f'[1/3] Querying VHN receipts / vendor-returns / sales for {args.seasons} ...')
    receipts, vendor_returns, sales = fetch_data(args.seasons)
    print(f'      -> {len(receipts)} receipt lines, {len(vendor_returns)} vendor-returns, {len(sales)} sale lines')

    print('[2/3] FIFO-attributing sales to shipments ...')
    acc, recon = fifo_attribute(receipts, vendor_returns, sales)
    rows = build_shipment_rows(receipts, acc)
    print(f"      -> {len(rows)} shipment rows; sold matched {recon['sold_matched']}/{recon['sold_total']}, "
          f"vendor-ret matched {recon['vr_matched']}/{recon['vr_total']}")

    out_path = Path(args.out_dir) / f'vhn_import_sale_by_shipment_{as_of.isoformat()}.xlsx'
    print(f'[3/3] Writing {out_path} ...')
    write_xlsx(rows, out_path, args.seasons, as_of, recon)

    print('\nDone. Written to:')
    print(f'  {out_path.resolve()}')
    tot_recv = sum(r['qty_received'] for r in rows)
    tot_sold = sum(r['qty_sold'] for r in rows)
    tot_imp = sum(r['import_cost'] for r in rows)
    tot_rev = sum(r['revenue_before_vat'] for r in rows)
    print(f'  Shipments:        {len(rows)}')
    print(f'  Units received:   {tot_recv:,}   sold(FIFO): {tot_sold:,}   '
          f'sell-through: {(tot_sold/tot_recv*100 if tot_recv else 0):.1f}%')
    print(f'  Import cost:      {tot_imp:,} VND')
    print(f'  Revenue (ex-VAT): {tot_rev:,} VND   '
          f'ROI: {((tot_rev-tot_imp)/tot_imp*100 if tot_imp else 0):.1f}%')


if __name__ == '__main__':
    main()
