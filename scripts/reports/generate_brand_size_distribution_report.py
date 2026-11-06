"""
Master-data Excel report: how one brand's sales spread across sizes, per half-year.

Answers "which sizes are actually moving, and is that changing?" — a matrix of
size (rows) against half-year (columns), each period showing units sold and that
size's share of the period. Shares are within a period, so columns each sum to
100% and periods of different length stay comparable.

Sales predicate and store scoping match
`generate_brand_store_halfyear_report.py` — same brand report, different cut, so
the two must not disagree about what a sale is. Returns (item_type=2) subtract,
as there.

Sizes are grouped by SIZING SCALE, detected from the data rather than assumed.
The garment category comes from the last segment of the product code
(INVN_SBS_ITEM.ALU, e.g. `ULJ-PF24-WRTW-JNS` -> JNS); each category's observed
numeric range is then merged with any category whose range overlaps it. What
survives is one group per genuinely distinct scale, labelled by its range.

This matters because no fixed rule works across brands:
  ULLA JOHNSON (US)  apparel 2-10, JNS 24-28  -> two scales, correctly split
  MARELLA      (EU)  everything 36-46, JNS included -> ONE scale, not split
A numeric threshold would call every Marella size a waist size, and a rule that
always split jeans out would wrongly split Marella's.

Non-numeric labels (S, M, L, O, P) form their own `alpha` group. Read shares
WITHIN a group — different scales are not comparable.

Output: document/reports/<brand>_size_distribution_<start>_<today>.xlsx
"""
import argparse
import re
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_oracle_connection


DEFAULT_STORES = ('RHN', 'RWD', 'RWT')

# Conventional order, smallest first. Anything unrecognised sorts after these,
# alphabetically, so a new label shows up at the end rather than vanishing.
_ALPHA_ORDER = ['XXS', 'XS', 'S', 'M', 'L', 'XL', 'XXL', 'O', 'P']

QUERY = r"""
SELECT
    CASE WHEN TO_NUMBER(TO_CHAR(d.INVC_POST_DATE, 'MM')) <= 6
         THEN 'H1' ELSE 'H2' END
      || '-' || TO_CHAR(d.INVC_POST_DATE, 'YYYY')        AS half_year,
    NVL(di.ITEM_SIZE, '(none)')                          AS item_size,
    NVL(SUBSTR(i.ALU, INSTR(i.ALU, '-', -1) + 1), '(none)') AS category,
    SUM(CASE WHEN di.ITEM_TYPE = 2 THEN -di.QTY ELSE di.QTY END) AS qty,
    SUM(CASE WHEN di.ITEM_TYPE = 2 THEN -di.QTY ELSE di.QTY END
        * (di.PRICE - di.TAX_AMT))                       AS revenue
FROM RPS.DOCUMENT d
JOIN RPS.DOCUMENT_ITEM di ON di.DOC_SID = d.SID
JOIN RPS.INVN_SBS_ITEM i  ON i.SID = di.INVN_SBS_ITEM_SID
JOIN RPS.VENDOR v         ON v.SID = i.VEND_SID
WHERE v.VEND_NAME = :brand
  AND d.STATUS = 4
  AND d.RECEIPT_TYPE IN (0, 1)
  AND di.ITEM_TYPE IN (1, 2)
  AND d.STORE_CODE IN ({stores})
  AND d.INVC_POST_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
  AND TRUNC(d.INVC_POST_DATE) <= TO_DATE(:end_date, 'YYYY-MM-DD')
GROUP BY
    CASE WHEN TO_NUMBER(TO_CHAR(d.INVC_POST_DATE, 'MM')) <= 6
         THEN 'H1' ELSE 'H2' END
      || '-' || TO_CHAR(d.INVC_POST_DATE, 'YYYY'),
    NVL(di.ITEM_SIZE, '(none)'),
    NVL(SUBSTR(i.ALU, INSTR(i.ALU, '-', -1) + 1), '(none)')
"""


def _r(value, places=0):
    """Round half away from zero — Python rounds halves to even."""
    q = Decimal(1) if places == 0 else Decimal(1).scaleb(-places)
    d = Decimal(repr(float(value))).quantize(q, rounding=ROUND_HALF_UP)
    return int(d) if places == 0 else float(d)


def as_number(size):
    s = (size or '').strip().upper()
    return float(s) if re.fullmatch(r'\d+(\.\d+)?', s) else None


def detect_scales(rows):
    """Group numeric sizes into distinct sizing scales, from the data.

    Each product category's observed numeric range is merged with any other
    whose range overlaps or touches it. Categories that share a scale end up in
    one group; a category sized on its own scale (US denim beside US apparel)
    ends up in its own. Returns {size label -> group label} plus the categories
    behind each group, for the Metadata sheet.
    """
    by_cat = {}
    for r in rows:
        n = as_number(r['size'])
        if n is None:
            continue
        lo, hi, cats = by_cat.get(r['category'], (n, n, None))[0:2] + (None,)
        prev = by_cat.get(r['category'])
        by_cat[r['category']] = ((min(prev[0], n), max(prev[1], n))
                                 if prev else (n, n))

    # Merge overlapping or adjacent ranges into scale clusters.
    clusters = []
    for cat, (lo, hi) in sorted(by_cat.items(), key=lambda kv: kv[1]):
        placed = False
        for c in clusters:
            if lo <= c['hi'] and hi >= c['lo']:
                c['lo'], c['hi'] = min(c['lo'], lo), max(c['hi'], hi)
                c['cats'].add(cat)
                placed = True
                break
        if not placed:
            clusters.append({'lo': lo, 'hi': hi, 'cats': {cat}})

    # One merge pass is not enough when a wide range joins two earlier ones.
    merged = True
    while merged:
        merged = False
        for i, a in enumerate(clusters):
            for b in clusters[i + 1:]:
                if a['lo'] <= b['hi'] and a['hi'] >= b['lo']:
                    a['lo'], a['hi'] = min(a['lo'], b['lo']), max(a['hi'], b['hi'])
                    a['cats'] |= b['cats']
                    clusters.remove(b)
                    merged = True
                    break
            if merged:
                break

    clusters.sort(key=lambda c: c['lo'])
    label_of = {}
    for c in clusters:
        c['label'] = _scale_label(c, len(clusters))
    for r in rows:
        n = as_number(r['size'])
        if n is None:
            continue
        for c in clusters:
            if c['lo'] <= n <= c['hi']:
                label_of[r['size']] = c['label']
                break
    return label_of, clusters


# Garment categories that are worn on the lower body. Used only to NAME a
# scale, never to group by: trousers, skirts and shorts share the apparel scale
# here, so a scale is called "jeans/bottom" only when bottoms are all it holds.
_BOTTOM_CATS = {'JNS', 'TRS', 'SKT', 'SHR', 'PNT'}


def _scale_label(cluster, n_clusters):
    """Name a scale after what is actually in it.

    A scale holding nothing but bottoms is named for them; anything mixed stays
    "apparel", because this brand sizes blouses, dresses, skirts and trousers on
    one run and calling that "tops" would hide the bottoms sitting in it.
    """
    span = f"{cluster['lo']:g}-{cluster['hi']:g}"
    if n_clusters == 1:
        return 'apparel'
    if cluster['cats'] <= _BOTTOM_CATS:
        kind = 'jeans' if cluster['cats'] == {'JNS'} else 'bottom'
        return f'{kind}/bottom {span}' if kind == 'jeans' else f'bottom {span}'
    return f'apparel {span}'


def classify(size, scale_of):
    """(group, sort key) for a size label."""
    s = (size or '').strip().upper()
    n = as_number(s)
    if n is not None:
        return scale_of.get(size, 'numeric'), (0, n, '')
    if s in _ALPHA_ORDER:
        return 'alpha', (1, _ALPHA_ORDER.index(s), '')
    return 'other', (2, 0, s)


def fetch(brand, stores, start_date, end_date):
    binds = {f'st{i}': s for i, s in enumerate(stores)}
    sql = QUERY.format(stores=', '.join(f':{k}' for k in binds))
    params = {'brand': brand, 'start_date': start_date,
              'end_date': end_date, **binds}
    conn = get_oracle_connection()
    if conn is None:
        raise RuntimeError('Could not open Oracle connection')
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            rows = cur.fetchall()
        finally:
            cur.close()
    finally:
        conn.close()
    return [{'half_year': r[0], 'size': r[1], 'category': r[2],
             'qty': float(r[3] or 0), 'revenue': float(r[4] or 0)}
            for r in rows]


def build(rows, scale_of, clusters):
    """Matrix of size x half-year, with each size's share of its period."""
    buckets = sorted({r['half_year'] for r in rows},
                     key=lambda h: (h.split('-')[1], h.split('-')[0]))
    order = {c['label']: i for i, c in enumerate(clusters)}
    order['alpha'] = len(order)
    order['other'] = len(order) + 1

    sizes = sorted({r['size'] for r in rows},
                   key=lambda s: (order.get(classify(s, scale_of)[0], 99),
                                  classify(s, scale_of)[1]))

    qty, rev = {}, {}
    for r in rows:
        k = (r['half_year'], r['size'])
        qty[k] = qty.get(k, 0.0) + r['qty']
        rev[k] = rev.get(k, 0.0) + r['revenue']

    # Period totals are the denominator for every share in that column.
    totals = {hy: sum(v for (h, _), v in qty.items() if h == hy)
              for hy in buckets}
    grand = sum(totals.values())

    out = []
    for size in sizes:
        row = {'size_group': classify(size, scale_of)[0], 'size': size}
        for hy in buckets:
            q = qty.get((hy, size), 0.0)
            row[f'{hy} qty'] = _r(q)
            row[f'{hy} %'] = _r(q / totals[hy], 4) if totals[hy] else 0
        total_q = sum(qty.get((hy, size), 0.0) for hy in buckets)
        row['ALL qty'] = _r(total_q)
        row['ALL %'] = _r(total_q / grand, 4) if grand else 0
        row['ALL revenue'] = _r(sum(rev.get((hy, size), 0.0) for hy in buckets))
        out.append(row)

    total_row = {'size_group': '', 'size': 'TOTAL'}
    for hy in buckets:
        total_row[f'{hy} qty'] = _r(totals[hy])
        total_row[f'{hy} %'] = 1 if totals[hy] else 0
    total_row['ALL qty'] = _r(grand)
    total_row['ALL %'] = 1 if grand else 0
    total_row['ALL revenue'] = _r(sum(rev.values()))
    out.append(total_row)

    columns = ['size_group', 'size']
    for hy in buckets:
        columns += [f'{hy} qty', f'{hy} %']
    columns += ['ALL qty', 'ALL %', 'ALL revenue']
    return out, columns, buckets, totals


def main_group(rows):
    """The scale carrying the most units — the brand's principal size run."""
    totals = {}
    for r in rows:
        if r['size'] == 'TOTAL':
            continue
        totals[r['size_group']] = totals.get(r['size_group'], 0) + r['ALL qty']
    return max(totals, key=totals.get) if totals else ''


def concentration(rows, columns, buckets, group):
    """How much of each period rides on its top sizes — the headline of a
    distribution. Computed on ONE scale; mixing scales would make the ranking
    meaningless."""
    out = []
    body = [r for r in rows if r['size'] != 'TOTAL' and r['size_group'] == group]
    for hy in buckets:
        ranked = sorted(body, key=lambda r: r[f'{hy} qty'], reverse=True)
        ranked = [r for r in ranked if r[f'{hy} qty'] > 0]
        top2 = sum(r[f'{hy} %'] for r in ranked[:2])
        out.append({
            'half_year': hy,
            'sizes_sold': len(ranked),
            'top_size': ranked[0]['size'] if ranked else '',
            'top_size_pct': ranked[0][f'{hy} %'] if ranked else 0,
            'top_2_pct': _r(top2, 4),
        })
    return out


def write_xlsx(rows, columns, conc, out_path, brand, stores,
               start_date, end_date, as_of, clusters, group):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'Size Distribution'
    ws.append(columns)

    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='305496', end_color='305496',
                              fill_type='solid')
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center',
                                   wrap_text=True)

    for r in rows:
        ws.append([r.get(c, '') for c in columns])
        if r['size'] == 'TOTAL':
            for cell in ws[ws.max_row]:
                cell.font = Font(bold=True)

    for idx, header in enumerate(columns, start=1):
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = 13 if idx > 2 else 12
        fmt = ('0.0%' if header.endswith(' %')
               else '#,##0' if header.endswith(('qty', 'revenue')) else None)
        if fmt:
            for row_idx in range(2, ws.max_row + 1):
                ws[f'{letter}{row_idx}'].number_format = fmt

    ws.freeze_panes = 'C2'

    cs = wb.create_sheet('Concentration')
    cs.append(['half_year', 'sizes_sold', 'top_size', 'top_size_pct', 'top_2_pct'])
    for cell in cs[1]:
        cell.font = header_font
        cell.fill = header_fill
    for c in conc:
        cs.append([c['half_year'], c['sizes_sold'], c['top_size'],
                   c['top_size_pct'], c['top_2_pct']])
    for col in ('D', 'E'):
        for row_idx in range(2, cs.max_row + 1):
            cs[f'{col}{row_idx}'].number_format = '0.0%'
    for col, w in (('A', 12), ('B', 12), ('C', 11), ('D', 14), ('E', 12)):
        cs.column_dimensions[col].width = w

    meta = wb.create_sheet('Metadata')
    meta.append(['Parameter', 'Value'])
    for cell in meta[1]:
        cell.font = header_font
        cell.fill = header_fill
    meta.append(['brand', brand])
    meta.append(['start_date', start_date])
    meta.append(['end_date (inclusive)', end_date])
    meta.append(['as_of_date (today)', as_of.isoformat()])
    meta.append(['stores', ', '.join(stores)])
    meta.append(['qty basis', 'net units — returns (item_type=2) subtract'])
    meta.append(['% basis',
                 "share of that half-year's total net units; each period "
                 'column sums to 100%'])
    meta.append(['size_group',
                 'sizing scale, DETECTED from the data: each product-code '
                 'category\'s numeric range is merged with any overlapping '
                 'one, so genuinely separate scales split and shared ones do '
                 'not. Named for what the scale holds — note the apparel scale '
                 'carries skirts, trousers and shorts as well as tops and '
                 'dresses, so it is NOT a "tops" group'])
    for c in clusters:
        meta.append([f"scale {c['label']}",
                     'categories: ' + ', '.join(sorted(c['cats']))])
    meta.append(['comparing groups',
                 'read shares WITHIN a group — different scales are not '
                 'comparable; the % columns are still shares of the whole '
                 'period, so they sum to 100% across all groups'])
    meta.append(['Concentration sheet',
                 f'computed on "{group}" — the scale carrying the most units'])
    meta.column_dimensions['A'].width = 26
    meta.column_dimensions['B'].width = 80

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--brand', required=True, help='VENDOR.VEND_NAME, exact')
    parser.add_argument('--start-date', default='2025-01-01')
    parser.add_argument('--end-date', default=None, help='Default: today')
    parser.add_argument('--stores', default=','.join(DEFAULT_STORES))
    parser.add_argument('--out-dir', default='document/reports')
    args = parser.parse_args()

    stores = [s.strip().upper() for s in args.stores.split(',') if s.strip()]
    as_of = date.today()
    end_date = args.end_date or as_of.isoformat()

    print(f'[1/3] Querying {args.brand} sales by size '
          f'({args.start_date} -> {end_date}) in {", ".join(stores)} ...')
    raw = fetch(args.brand, stores, args.start_date, end_date)
    if not raw:
        print('No sales found. Check the brand name against VENDOR.VEND_NAME.')
        return 1
    print(f'      -> {len(raw)} size x period cells')

    print('[2/3] Building the distribution ...')
    scale_of, clusters = detect_scales(raw)
    rows, columns, buckets, totals = build(raw, scale_of, clusters)
    group = main_group(rows)
    conc = concentration(rows, columns, buckets, group)
    print(f'      -> scales detected: '
          + '; '.join(f"{c['label']} ({', '.join(sorted(c['cats']))})"
                      for c in clusters))
    print(f'      -> {len(rows) - 1} sizes across {", ".join(buckets)}')

    slug = args.brand.lower().replace(' ', '_')
    out_path = (Path(args.out_dir) /
                f'{slug}_size_distribution_{args.start_date}_{as_of.isoformat()}.xlsx')
    print(f'[3/3] Writing {out_path} ...')
    write_xlsx(rows, columns, conc, out_path, args.brand, stores,
               args.start_date, end_date, as_of, clusters, group)

    print(f'\nDone. Written to:\n  {out_path.resolve()}')
    for c in conc:
        print(f'  {c["half_year"]}: {c["sizes_sold"]} numeric sizes sold, '
              f'top = {c["top_size"]} at {c["top_size_pct"]:.1%}, '
              f'top 2 = {c["top_2_pct"]:.1%}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
