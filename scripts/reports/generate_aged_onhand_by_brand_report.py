"""
Excel report: brands still holding on-hand stock from season SS26 or older.

Grain: one row per brand (VENDOR) with any on-hand units in an eligible season.

Season = INVN_SBS_ITEM.UDF5_STRING, e.g. SS26. Seasons are ordered by year,
then by delivery within the year:
    PS (pre-spring) < SS / DS (spring) < PF (pre-fall) < FW / DF (fall)
So "SS26 and older" keeps PS26 and SS26 and drops PF26, FW26, PS27, SS27.

Columns (all on-hand UNITS, every store and warehouse combined):
  onhand_ss25_to_ss26   seasons from SS25 up to SS26, inclusive
                        (SS25, DS25, PF25, FW25, DF25, PS26, SS26)
  onhand_before_ss25    every season older than SS25 (PS25 and earlier)
  onhand_total          sum of the two
  <DEPARTMENT> ...      onhand_total split by DCS department (D_LONG_NAME)

Stock source: INVN_SBS_ITEM_QTY.QTY (live, one row per item x store).

Usage:
  FLASK_ENV=remote .venv/bin/python scripts/reports/generate_aged_onhand_by_brand_report.py
Output: document/reports/aged_onhand_by_brand_<cutoff>_<today>.xlsx
"""
import argparse
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_oracle_connection

SLOT = {'PS': 1, 'SS': 2, 'DS': 2, 'PF': 3, 'FW': 4, 'DF': 4}
SEASON_RE = re.compile(r'^([A-Z]{2})(\d{2})$')

QUERY = """
    SELECT v.VEND_CODE                 AS brand_code,
           v.VEND_NAME                 AS brand_name,
           UPPER(TRIM(i.UDF5_STRING))  AS season,
           NVL(dep.D_LONG_NAME, '(none)') AS department,
           NVL(s.STORE_CODE, '(none)') AS store_code,
           SUM(q.QTY)                  AS qty
    FROM RPS.INVN_SBS_ITEM i
    JOIN RPS.INVN_SBS_ITEM_QTY q ON q.INVN_SBS_ITEM_SID = i.SID
    LEFT JOIN RPS.VENDOR v       ON v.SID = i.VEND_SID
    LEFT JOIN RPS.DCS dep        ON dep.SID = i.DCS_SID
    LEFT JOIN RPS.STORE s        ON s.SID = q.STORE_SID
    WHERE q.QTY <> 0
      {exclusions}
    GROUP BY v.VEND_CODE, v.VEND_NAME, UPPER(TRIM(i.UDF5_STRING)),
             dep.D_LONG_NAME, s.STORE_CODE
"""

# Shops first (live-comparison page order), then the warehouses.
STORE_ORDER = ['RHN', 'RWP', 'RWR', 'RWT', 'RWD', 'HQ', 'LOA', 'RWL']

# Gender = first letter of the department code. HOME / COSM are not a gender.
GENDER = {'W': 'Women', 'M': 'Men', 'K': 'Kids'}
GENDER_ORDER = ['Women', 'Men', 'Kids', 'Other']


# Excluded from every sheet (requested 2026-11-05).
EXCLUDE_DEPTS = ['HOME']
# Matched on VENDOR.VEND_NAME. A name that is not a vendor is also matched
# against the item name (TEXT1): GIOIELLERIA PENNISI has no vendor of its own —
# its pieces are filed under ANN QUEEN with the name in TEXT1.
EXCLUDE_BRANDS = [
    '5 OCTOBRE', 'ARTUR SCHOLL', 'BE DOGON ART', 'BRONZ ALLURE', 'BURATO',
    'COLLEZIONE 925', 'CONFUORTO GIOIELLY ITALY', 'ETRUSCA', 'GEODE',
    'GIOIELLERIA PENNISI', 'GIORGIO VISCONTI', 'GIOVANNI RASPINI',
    'GOLDEN CORALS', 'LIZZIE FORTUNATO', 'MARIAE LUISA', 'MONICA CASTIGLIONI',
    'NAK ARMSTRONG', 'NANIS', 'NECKLACE YELLOW PEARL', 'ROSA MARIA', 'RUIFIER',
    'SERGE THORAVAL', 'SPINELLI KILCOLLIN', 'STEFANO FILOCAMO', 'TED MUEHLING',
    'VHERNIER',
]


def exclusion_sql(depts, brands):
    """WHERE fragment + binds for the excluded departments and brands."""
    if not depts and not brands:
        return '', {}
    binds, parts = {}, []
    if depts:
        names = []
        for n, d in enumerate(depts):
            binds[f'xd{n}'] = d.upper()
            names.append(f':xd{n}')
        parts.append(f"AND NVL(dep.D_LONG_NAME, '-') NOT IN ({', '.join(names)})")
    for n, b in enumerate(brands):
        binds[f'xb{n}'] = b.upper()
        parts.append(f"AND UPPER(TRIM(NVL(v.VEND_NAME, '-'))) <> :xb{n}")
        parts.append(f"AND INSTR(UPPER(NVL(i.TEXT1, '-')), :xb{n}) = 0")
    return '\n      '.join(parts), binds


def gender_of(department):
    return GENDER.get((department or '')[:1].upper(), 'Other')

# Women first, then men, kids, then the rest — the order buyers read them in.
DEPT_ORDER = ['WRTW', 'WSHO', 'WBAG', 'WACC', 'WJEW',
              'MRTW', 'MSHO', 'MBAG', 'MACC', 'MJEW',
              'KRTW', 'KACC', 'HOME', 'COSM']


def season_key(season):
    """(year, slot) or None if the code is not a recognised season."""
    m = SEASON_RE.match(season or '')
    if not m or m.group(1) not in SLOT:
        return None
    return (int(m.group(2)), SLOT[m.group(1)])


def fetch(depts, brands):
    excl, binds = exclusion_sql(depts, brands)
    conn = get_oracle_connection()
    if conn is None:
        raise RuntimeError('Could not open Oracle connection')
    try:
        cur = conn.cursor()
        try:
            cur.execute(QUERY.format(exclusions=excl), binds)
            cols = [d[0].lower() for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()
    finally:
        conn.close()
    return rows


def build(raw, lower, upper):
    lo, hi = season_key(lower), season_key(upper)
    brands = {}
    skipped = defaultdict(float)   # unrecognised season codes -> units
    depts = set()
    older = defaultdict(lambda: defaultdict(float))  # (brand, gender) -> store -> qty
    stores = set()
    for r in raw:
        qty = float(r['qty'] or 0)
        k = season_key(r['season'])
        if k is None:
            skipped[r['season'] or '(blank)'] += qty
            continue
        if k > hi:
            continue
        key = (r['brand_code'] or '', r['brand_name'] or '(no brand)')
        b = brands.setdefault(key, {'recent': 0.0, 'older': 0.0,
                                    'dept': defaultdict(float)})
        b['recent' if k >= lo else 'older'] += qty
        b['dept'][r['department']] += qty
        depts.add(r['department'])
        if k < lo:
            older[(key, gender_of(r['department']))][r['store_code']] += qty
            stores.add(r['store_code'])

    dept_cols = [d for d in DEPT_ORDER if d in depts] + \
                sorted(d for d in depts if d not in DEPT_ORDER)
    rows = []
    for (code, name), b in brands.items():
        total = b['recent'] + b['older']
        if total <= 0:
            continue
        rows.append({'brand_code': code, 'brand_name': name,
                     'recent': int(b['recent']), 'older': int(b['older']),
                     'total': int(total),
                     **{d: int(b['dept'].get(d, 0)) for d in dept_cols}})
    rows.sort(key=lambda r: (-r['total'], r['brand_name']))

    # Older-than-lower stock, one row per brand x gender, a column per store.
    store_cols = [s for s in STORE_ORDER if s in stores] + \
                 sorted(s for s in stores if s not in STORE_ORDER)
    brand_rank = {(r['brand_code'], r['brand_name']): i for i, r in enumerate(rows)}
    gender_rows = []
    for (key, gender), by_store in older.items():
        total = sum(by_store.values())
        if total <= 0 or key not in brand_rank:
            continue
        gender_rows.append({'brand_code': key[0], 'brand_name': key[1],
                            'gender': gender, 'total': int(total),
                            **{s: int(by_store.get(s, 0)) for s in store_cols}})
    gender_rows.sort(key=lambda r: (brand_rank[(r['brand_code'], r['brand_name'])],
                                    GENDER_ORDER.index(r['gender'])))
    return rows, dept_cols, dict(skipped), gender_rows, store_cols


def write_xlsx(rows, dept_cols, skipped, gender_rows, store_cols,
               out_path, lower, upper, as_of, excluded=([], [])):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    hf = Font(bold=True, color='FFFFFF')
    hfill = PatternFill('solid', start_color='305496', end_color='305496')
    dfill = PatternFill('solid', start_color='5B7DB8', end_color='5B7DB8')

    labels = ['brand_code', 'brand_name',
              f'onhand_{lower}_to_{upper}'.lower(), f'onhand_before_{lower}'.lower(),
              'onhand_total'] + dept_cols
    keys = ['brand_code', 'brand_name', 'recent', 'older', 'total'] + dept_cols

    wb = Workbook()
    ws = wb.active
    ws.title = 'Brands'
    ws.append(labels)
    for i, c in enumerate(ws[1]):
        c.font = hf
        c.fill = dfill if i >= 5 else hfill
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for r in rows:
        ws.append([r[k] or (None if k in dept_cols else 0) for k in keys])
    ws.append(['TOTAL', f'{len(rows)} brands'] +
              [sum(r[k] for r in rows) for k in keys[2:]])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)

    for r in range(2, ws.max_row + 1):
        for c in range(3, len(keys) + 1):
            ws.cell(r, c).number_format = '#,##0'
    widths = [12, 30, 14, 14, 12] + [9] * len(dept_cols)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = 'C2'
    ws.auto_filter.ref = f'A1:{get_column_letter(len(keys))}{ws.max_row - 1}'

    # ---- Older stock by gender x store ----
    # One row per brand. Two header rows: gender on top (merged), and under
    # each gender a column per store plus that gender's total.
    from openpyxl.styles import Border, Side
    gs = wb.create_sheet(f'Before {lower} by gender')
    genders = [g for g in GENDER_ORDER if any(r['gender'] == g for r in gender_rows)]
    sub_cols = store_cols + ['Total']
    gfill = {'Women': 'C0504D', 'Men': '4F81BD', 'Kids': '9BBB59', 'Other': '8064A2'}
    edge = Side(style='medium', color='808080')

    # brand -> gender -> {store: qty, 'Total': qty}
    pivot = {}
    for r in gender_rows:
        b = pivot.setdefault((r['brand_code'], r['brand_name']), {})
        b[r['gender']] = {**{s: r[s] for s in store_cols}, 'Total': r['total']}

    fixed = ['brand_code', 'brand_name', 'onhand_total', f'onhand_before_{lower}'.lower()]
    # onhand_total = all stock up to `upper`, same figure as the Brands sheet.
    brand_total = {(r['brand_code'], r['brand_name']): r['total'] for r in rows}
    top = fixed[:]
    second = [''] * len(fixed)
    for g in genders:
        top += [g] + [''] * (len(sub_cols) - 1)
        second += sub_cols
    gs.append(top)
    gs.append(second)
    for i in range(len(fixed)):                       # fixed headers span both rows
        gs.merge_cells(start_row=1, start_column=i + 1, end_row=2, end_column=i + 1)
    # Gender label sits above that gender's Total column, not merged across the
    # block: the store columns start collapsed, and a merged label anchored on
    # a hidden column would vanish. The colour band still marks the block.
    starts = []
    for gi, g in enumerate(genders):
        start = len(fixed) + gi * len(sub_cols) + 1
        starts.append(start)
        gs.cell(1, start).value = None
        gs.cell(1, start + len(sub_cols) - 1).value = g
    for row in (1, 2):
        for c in gs[row]:
            c.font = hf
            c.fill = hfill
            c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for g, start in zip(genders, starts):
        fill = PatternFill('solid', start_color=gfill[g], end_color=gfill[g])
        for col in range(start, start + len(sub_cols)):
            gs.cell(1, col).fill = fill
            gs.cell(2, col).fill = fill

    def body(vals_for):
        out = []
        for g in genders:
            out += [vals_for(g, s) for s in sub_cols]
        return out

    order = [(r['brand_code'], r['brand_name']) for r in rows if (r['brand_code'], r['brand_name']) in pivot]
    for key in order:
        b = pivot[key]
        total = sum(v['Total'] for v in b.values())
        gs.append([key[0], key[1], brand_total[key], total] +
                  body(lambda g, s: (b.get(g, {}).get(s) or None)))
    gs.append(['TOTAL', f'{len(order)} brands',
               sum(brand_total[k] for k in order),
               sum(r['total'] for r in gender_rows)] +
              body(lambda g, s: sum(pivot[k].get(g, {}).get(s, 0) for k in order)))
    for c in gs[gs.max_row]:
        c.font = Font(bold=True)

    last_col = len(fixed) + len(genders) * len(sub_cols)
    bold_cols = {3, 4} | {s + len(sub_cols) - 1 for s in starts}   # the total columns
    for r in range(3, gs.max_row + 1):
        for c in range(3, last_col + 1):
            cell = gs.cell(r, c)
            cell.number_format = '#,##0'
            if c in bold_cols:
                cell.font = Font(bold=True)
    # Divider on the right of each gender's Total — it stays visible when the
    # store columns are collapsed.
    for r in range(1, gs.max_row + 1):
        for s in starts:
            gs.cell(r, s + len(sub_cols) - 1).border = Border(right=edge)
    for i, w in enumerate([12, 30, 12, 14] + [7] * (last_col - 4), start=1):
        gs.column_dimensions[get_column_letter(i)].width = w
    # Outline: each gender's store columns fold into its Total column, and
    # start collapsed so only the totals show. The +/- sits above Total.
    gs.sheet_properties.outlinePr.summaryRight = True
    # Excel shows no +/- buttons unless the sheet declares its outline depth.
    # openpyxl writes that from column_dimensions.max_outline, but only computes
    # max_outline AFTER writing it (3.1.x), so set it up front.
    gs.column_dimensions.max_outline = 1
    for s in starts:
        total_col = get_column_letter(s + len(sub_cols) - 1)
        gs.column_dimensions.group(get_column_letter(s),
                                   get_column_letter(s + len(store_cols) - 1),
                                   outline_level=1, hidden=True)
        gs.column_dimensions[total_col].width = 10
        gs.column_dimensions[total_col].collapsed = True
    gs.freeze_panes = 'E3'
    gs.auto_filter.ref = f'A2:{get_column_letter(last_col)}{gs.max_row - 1}'

    meta = wb.create_sheet('Metadata')
    meta.append(['Parameter', 'Value'])
    for c in meta[1]:
        c.font = hf
        c.fill = hfill
    meta.append(['as_of_date', as_of.isoformat()])
    meta.append(['scope', f'on-hand stock in seasons {upper} and older'])
    meta.append(['season field', 'INVN_SBS_ITEM.UDF5_STRING'])
    meta.append(['season order', 'by year, then PS < SS/DS < PF < FW/DF'])
    meta.append([f'onhand_{lower}_to_{upper}'.lower(),
                 f'{lower} up to {upper} inclusive (incl. PF/FW of {lower[2:]}, PS{upper[2:]})'])
    meta.append([f'onhand_before_{lower}'.lower(), f'all seasons older than {lower}'])
    meta.append(['unit of measure', 'on-hand UNITS (not SKU count)'])
    meta.append(['stores', 'all stores and warehouses combined (INVN_SBS_ITEM_QTY)'])
    meta.append(['department', 'DCS.D_LONG_NAME'])
    meta.append(['excluded departments', ', '.join(excluded[0]) or 'none'])
    meta.append(['excluded brands',
                 (', '.join(excluded[1]) + ' — matched on vendor name, or on the '
                  'item name for brands filed under another vendor '
                  '(GIOIELLERIA PENNISI sits under ANN QUEEN)') if excluded[1] else 'none'])
    meta.append(['gender', 'first letter of department: W=Women, M=Men, K=Kids; '
                           'anything else (HOME, COSM) = Other'])
    meta.append([f'Before {lower} by gender',
                 f'stock older than {lower}, one row per brand; a header block per '
                 'gender with a column per store (warehouses LOA, RWL last) and '
                 'a Total column for that gender'])
    meta.append(['excluded seasons',
                 ', '.join(f'{k}: {v:,.0f}' for k, v in sorted(skipped.items()))
                 or 'none — every season code was recognised'])
    meta.column_dimensions['A'].width = 24
    meta.column_dimensions['B'].width = 70

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument('--upper', default='SS26', help='Newest season included (default SS26)')
    p.add_argument('--lower', default='SS25',
                   help='Start of the "recent" bucket (default SS25)')
    p.add_argument('--no-exclusions', action='store_true',
                   help='Keep HOME and the excluded brand list')
    p.add_argument('--out-dir', default='document/reports')
    a = p.parse_args()
    lower, upper = a.lower.upper(), a.upper.upper()
    if not (season_key(lower) and season_key(upper)):
        raise SystemExit('--lower/--upper must look like SS25, FW24, ...')

    as_of = date.today()
    excluded = ([], []) if a.no_exclusions else (EXCLUDE_DEPTS, EXCLUDE_BRANDS)
    rows, dept_cols, skipped, gender_rows, store_cols = build(fetch(*excluded), lower, upper)
    out = Path(a.out_dir) / f'aged_onhand_by_brand_{upper.lower()}_{as_of.isoformat()}.xlsx'
    write_xlsx(rows, dept_cols, skipped, gender_rows, store_cols,
               out, lower, upper, as_of, excluded)

    print(f'Brands: {len(rows)}')
    print(f'{lower}..{upper}: {sum(r["recent"] for r in rows):,}  '
          f'before {lower}: {sum(r["older"] for r in rows):,}  '
          f'total: {sum(r["total"] for r in rows):,}')
    print('Departments:', ', '.join(f'{d}={sum(r[d] for r in rows):,}' for d in dept_cols))
    for g in GENDER_ORDER:
        sub = [r for r in gender_rows if r['gender'] == g]
        if sub:
            print(f'Before {lower} {g}: {sum(r["total"] for r in sub):,}  ' +
                  ', '.join(f'{s}={sum(r[s] for r in sub):,}' for s in store_cols))
    print('Unrecognised seasons skipped:', skipped or 'none')
    print(f'Written to: {out.resolve()}')


if __name__ == '__main__':
    main()
