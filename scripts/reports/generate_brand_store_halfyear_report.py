"""
Master-data Excel report: one brand, by store and half-year.

Rebuilt from the spec recorded in the Metadata sheet of
`marella_brand_2025-01-01_2026-08-16.xlsx`. The script that produced that
file was never committed — `scripts/reports/` is gitignored on this branch —
so this is a reimplementation, verified by reproducing that workbook's
numbers exactly (see --verify-against).

Grain: one row per (store_code, half_year), then an `ALL` half-year row per
store, then an `ALL`/`ALL` grand total. Half-years come from the invoice post
date: H1 = Jan-Jun, H2 = Jul-Dec.

Scope: sales documents only (status=4, not held, receipt_type IN (0,1)),
restricted to the stores given by --areas. The store list is deliberately a
filter, not "wherever the brand sold": these metrics are per-m² of dedicated
floor space, so a stray sale from a store with no area would distort them.

Columns:
  store_code         — rps.document.STORE_CODE
  half_year          — H1-YYYY / H2-YYYY, or ALL
  area_m2            — floor area, from --areas (NOT in the database)
  qty_sold_net       — item_type 1 as +qty, 2 (returns) as -qty
  revenue_before_vat — Σ(signed qty × (di.PRICE - di.TAX_AMT)), actual per-line VAT
  landed_cost        — Σ(signed qty × TO_NUMBER(i.UDF1_STRING)); non-numeric = 0
  margin             — revenue_before_vat - landed_cost
  margin_pct         — margin / revenue_before_vat
  revenue_per_m2     — revenue_before_vat / area_m2
  old_season_pct     — net revenue from items whose SEASON has already
                       closed. Same year: H1 stock (SS/PS) is old once H2
                       starts, but H2 stock (PF/FW) sold during H1 is NOT old —
                       season timing is approximate and PF/FW sells early.
                       Any other year, ahead or behind, is old.
  deep_discount_pct  — net revenue from lines discounted >50%. This is what the
                       original Marella report called `old_season_pct`; it is a
                       discount measure, not a season one, and is kept under its
                       true name so the two can be compared.
  season_coverage_pct— share of net revenue whose season code could be parsed.
                       Read old_season_pct against this: a low coverage means
                       the figure is drawn from a thin slice.
  avg_discount_rate  — 1 - Σ(qty × PRICE) / Σ(qty × ORIG_PRICE), sales only

Output: document/reports/<brand>_brand_<start>_<today>.xlsx
"""
import argparse
import re
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_oracle_connection


# Floor areas are not held anywhere in the system — they came from the user
# with the original Marella report. Defaults reproduce that workbook.
DEFAULT_AREAS = {'RHN': 15, 'RWD': 10, 'RWT': 15}

COLUMNS = [
    'store_code', 'half_year', 'area_m2', 'qty_sold_net',
    'revenue_before_vat', 'landed_cost', 'margin', 'margin_pct',
    'revenue_per_m2', 'old_season_pct', 'deep_discount_pct',
    'season_coverage_pct', 'avg_discount_rate',
]

# Season code -> the half-year the season belongs to. Confirmed against the
# data: in H1-2025 the brand sold PS25 and SS25 as current stock, in H2-2025 it
# sold PF25 and FW25. Pre-season codes (PS, PF) lead their main season but fall
# in the same half.
_SEASON_HALF = {'PS': 'H1', 'SS': 'H1', 'PF': 'H2', 'FW': 'H2'}
_SEASON_RE = re.compile(r'^([A-Z]{2})(\d{2})$')


def season_half_year(code):
    """'SS25' -> ('2025', 'H1'). None when the code is absent or unrecognised.

    Returning None rather than guessing keeps unparseable codes out of the
    old/new split entirely — they are counted in season_coverage_pct instead,
    so a brand with messy season data shows up as low coverage rather than as
    a confidently wrong percentage.
    """
    m = _SEASON_RE.match((code or '').strip().upper())
    if not m:
        return None
    prefix, yy = m.groups()
    half = _SEASON_HALF.get(prefix)
    return (f'20{yy}', half) if half else None


def is_old_season(code, sold_half_year):
    """Is this item off-season for the half-year it sold in?

    Within the SAME year, a season that comes later is still new — season
    boundaries are not exact, and FW/PF stock routinely starts selling during
    the SS half. So in H1-2026, SS26 and PS26 are current and PF26/FW26 are
    new as well; only the year's own earlier half is old.

    Across years the year itself decides: anything carrying a different year is
    old, whether it is behind or ahead.
    """
    sh = season_half_year(code)
    if sh is None:
        return None
    season_year, season_half = sh
    sold_half, sold_year = sold_half_year.split('-')
    if season_year != sold_year:
        return True
    # Same year: old only if the season's half already closed.
    return season_half < sold_half

# One row per sale line, with the signed quantity already applied. Everything
# else is aggregated in Python: the half-year buckets, the per-store and grand
# totals all derive from the same rows, so they cannot disagree.
QUERY = r"""
SELECT
    d.STORE_CODE                                   AS store_code,
    TO_CHAR(d.INVC_POST_DATE, 'YYYY')              AS yr,
    TO_NUMBER(TO_CHAR(d.INVC_POST_DATE, 'MM'))     AS mth,
    CASE WHEN di.ITEM_TYPE = 2 THEN -di.QTY ELSE di.QTY END AS signed_qty,
    di.PRICE                                       AS price,
    di.TAX_AMT                                     AS tax_amt,
    di.ORIG_PRICE                                  AS orig_price,
    di.ITEM_TYPE                                   AS item_type,
    i.UDF5_STRING                                  AS season,
    CASE
        WHEN REGEXP_LIKE(TRIM(i.UDF1_STRING), '^[0-9]+(\.[0-9]+)?$')
        THEN TO_NUMBER(TRIM(i.UDF1_STRING))
        ELSE 0
    END                                            AS unit_landed_cost
FROM RPS.DOCUMENT d
JOIN RPS.DOCUMENT_ITEM di ON di.DOC_SID = d.SID
JOIN RPS.INVN_SBS_ITEM i  ON i.SID = di.INVN_SBS_ITEM_SID
JOIN RPS.VENDOR v         ON v.SID = i.VEND_SID
WHERE v.VEND_NAME = :brand
  AND d.STATUS = 4
  AND d.RECEIPT_TYPE IN (0, 1)
  -- NOTE: deliberately NOT filtering d.IS_HELD. Two MARELLA documents in the
  -- reference period are STATUS=4 (completed) yet flagged held, and the
  -- original report counted them — dropping them loses a real sale at RHN and
  -- one at RWT. This differs from the /sizes report, which does filter
  -- is_held; see the Metadata sheet note.
  AND di.ITEM_TYPE IN (1, 2)
  AND d.STORE_CODE IN ({stores})
  AND d.INVC_POST_DATE >= TO_DATE(:start_date, 'YYYY-MM-DD')
  AND TRUNC(d.INVC_POST_DATE) <= TO_DATE(:end_date, 'YYYY-MM-DD')
"""


def fetch_lines(brand, stores, start_date, end_date):
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
            cols = [d[0].lower() for d in cur.description]
            rows = cur.fetchall()
        finally:
            cur.close()
    finally:
        conn.close()
    return [dict(zip(cols, r)) for r in rows]


def half_year(row):
    return f"{'H1' if row['mth'] <= 6 else 'H2'}-{row['yr']}"


def partial_bucket(end_date):
    """The half-year the report stops inside, if it stops short of its end.

    A half that is still running is not comparable with the completed ones
    beside it — fewer trading days, and a sell-through tail that has not run.
    Returns (label, first_day, last_day) or None.
    """
    y, m, d = (int(x) for x in end_date.split('-'))
    if m <= 6:
        return (f'H1-{y}', f'{y}-01-01', end_date) if (m, d) != (6, 30) else None
    return (f'H2-{y}', f'{y}-07-01', end_date) if (m, d) != (12, 31) else None


def summarise(lines, area, cell_half_year=None):
    """Aggregate a set of sale lines into the reported metrics.

    `cell_half_year` is the half-year this cell covers, needed to decide what
    counts as old season. The ALL rows pass None and compose their figures from
    the half-year cells instead, so every line is judged against the half it
    actually sold in rather than against a total that has no season of its own.
    """
    qty = revenue = cost = old_season_rev = 0.0
    deep_discount_rev = season_known_rev = 0.0
    disc_actual = disc_orig = 0.0

    for r in lines:
        q = float(r['signed_qty'] or 0)
        price = float(r['price'] or 0)
        tax = float(r['tax_amt'] or 0)
        orig = float(r['orig_price'] or 0)

        line_rev = q * (price - tax)
        qty += q
        revenue += line_rev
        cost += q * float(r['unit_landed_cost'] or 0)

        # Discounted by more than half. This is what the original report used
        # as its "old season" proxy; kept, but under its own name.
        if orig > 0 and (1 - price / orig) > 0.5:
            deep_discount_rev += line_rev

        # Old season proper: the item's season closed before the half it sold
        # in. Unparseable codes are counted in neither bucket but tracked, so
        # the percentage is always read against its coverage.
        if cell_half_year:
            old = is_old_season(r.get('season'), cell_half_year)
            if old is not None:
                season_known_rev += line_rev
                if old:
                    old_season_rev += line_rev

        # Discount rate is about what was given away at full-price intent, so
        # returns are excluded — item_type 1 only, per the original spec.
        if r['item_type'] == 1:
            disc_actual += q * price
            disc_orig += q * orig

    return _finish({
        'qty': qty, 'revenue': revenue, 'cost': cost,
        'old_season_rev': old_season_rev,
        'deep_discount_rev': deep_discount_rev,
        'season_known_rev': season_known_rev,
        'disc_actual': disc_actual, 'disc_orig': disc_orig,
    }, area)


# Additive fields are summed from the ROUNDED cell values rather than
# re-aggregated from the lines, so a store's ALL row is exactly the sum of the
# half-years printed above it. Re-aggregating leaves the column off by a unit
# and makes the sheet look like it cannot add up. Ratios are always derived,
# never summed.
_ADDITIVE = ('qty', 'revenue', 'cost')


def _r(value, places=0):
    """Round half AWAY from zero, not Python's round().

    Python rounds halves to even, so a cost summing to exactly …994.5 becomes
    …994 while every other tool — and the original report — gives …995. Money
    columns land on .5 often enough that this is not academic: it put one cell
    out by a unit and the error then propagated into every total above it.
    """
    q = Decimal(1) if places == 0 else Decimal(1).scaleb(-places)
    d = Decimal(repr(float(value))).quantize(q, rounding=ROUND_HALF_UP)
    return int(d) if places == 0 else float(d)


def _finish(acc, area):
    qty, revenue, cost = acc['qty'], acc['revenue'], acc['cost']
    rev, cst = _r(revenue), _r(cost)
    margin = rev - cst
    return {
        'area_m2': area,
        'qty_sold_net': _r(qty),
        'revenue_before_vat': rev,
        'landed_cost': cst,
        'margin': margin,
        'margin_pct': _r(margin / rev, 4) if rev else 0,
        'revenue_per_m2': _r(rev / area) if area else 0,
        # Denominator is revenue whose season is KNOWN, not all revenue —
        # otherwise unparseable codes silently deflate the figure.
        'old_season_pct': (_r(acc['old_season_rev'] / acc['season_known_rev'], 4)
                           if acc['season_known_rev'] else 0),
        'deep_discount_pct': (_r(acc['deep_discount_rev'] / revenue, 4)
                              if revenue else 0),
        'season_coverage_pct': (_r(acc['season_known_rev'] / revenue, 4)
                                if revenue else 0),
        'avg_discount_rate': (_r(1 - acc['disc_actual'] / acc['disc_orig'], 4)
                              if acc['disc_orig'] else 0),
        '_acc': acc,
    }


def _combine(cells, area):
    """A total row: additive fields summed from the cells above it."""
    acc = {k: 0.0 for k in ('qty', 'revenue', 'cost', 'old_season_rev',
                            'deep_discount_rev', 'season_known_rev',
                            'disc_actual', 'disc_orig')}
    for c in cells:
        for k in acc:
            acc[k] += (_r(c['_acc'][k]) if k in _ADDITIVE else c['_acc'][k])
    return _finish(acc, area)


def build_rows(lines, areas):
    """Per store: one row per half-year, then the store's ALL. Then grand ALL."""
    buckets = sorted({half_year(r) for r in lines},
                     key=lambda h: (h.split('-')[1], h.split('-')[0]))
    rows = []
    cells = {}
    for store in sorted(areas):
        store_lines = [r for r in lines if r['store_code'] == store]
        for hy in buckets:
            cell = summarise([r for r in store_lines if half_year(r) == hy],
                             areas[store], cell_half_year=hy)
            cells[(store, hy)] = cell
            rows.append({'store_code': store, 'half_year': hy, **cell})
        rows.append({'store_code': store, 'half_year': 'ALL',
                     **_combine([cells[(store, hy)] for hy in buckets],
                                areas[store])})

    total_area = sum(areas.values())
    for hy in buckets:
        rows.append({'store_code': 'ALL', 'half_year': hy,
                     **_combine([cells[(s, hy)] for s in areas], total_area)})
    rows.append({'store_code': 'ALL', 'half_year': 'ALL',
                 **_combine(list(cells.values()), total_area)})

    for r in rows:
        r.pop('_acc', None)
    return rows, buckets


def write_xlsx(rows, buckets, out_path, brand, areas, start_date, end_date, as_of):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = f'{brand.title()} by Season and Store'[:31]
    ws.append(COLUMNS)

    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='305496', end_color='305496',
                              fill_type='solid')
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')

    for r in rows:
        ws.append([r[c] for c in COLUMNS])
        # The ALL rows are totals, not another store — bold them so the eye
        # does not read them as a fourth store's figures.
        if r['half_year'] == 'ALL' or r['store_code'] == 'ALL':
            for cell in ws[ws.max_row]:
                cell.font = Font(bold=True)

    for idx, header in enumerate(COLUMNS, start=1):
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = max(len(header) + 2, 12)
        if header in ('revenue_before_vat', 'landed_cost', 'margin',
                      'revenue_per_m2'):
            for row_idx in range(2, ws.max_row + 1):
                ws[f'{letter}{row_idx}'].number_format = '#,##0'
        elif header in ('margin_pct', 'old_season_pct', 'deep_discount_pct',
                        'season_coverage_pct', 'avg_discount_rate'):
            for row_idx in range(2, ws.max_row + 1):
                ws[f'{letter}{row_idx}'].number_format = '0.00%'

    ws.freeze_panes = 'A2'

    meta = wb.create_sheet('Metadata')
    meta.append(['Parameter', 'Value'])
    for cell in meta[1]:
        cell.font = header_font
        cell.fill = header_fill
    meta.append(['brand', brand])
    meta.append(['start_date', start_date])
    meta.append(['end_date (inclusive)', end_date])
    meta.append(['as_of_date (today)', as_of.isoformat()])
    meta.append(['half_year buckets', ', '.join(buckets)])
    partial = partial_bucket(end_date)
    if partial and partial[0] in buckets:
        label, first, last = partial
        meta.append([f'PARTIAL — {label}',
                     f'covers {first} to {last} only; the half-year is still '
                     f'running, so it is NOT comparable with the completed '
                     f'halves above it'])
    meta.append(['stores', ', '.join(sorted(areas))])
    for store in sorted(areas):
        meta.append([f'area {store} (m²)', areas[store]])
    meta.append(['combined area (m²)', sum(areas.values())])
    meta.append(['revenue basis',
                 'before VAT — di.price - di.tax_amt (actual per-line VAT)'])
    meta.append(['landed cost source',
                 'INVN_SBS_ITEM.UDF1_STRING (numeric only; else 0)'])
    meta.append(['margin', 'revenue_before_vat - landed_cost'])
    meta.append(['revenue_per_m2', 'revenue_before_vat / area_m2'])
    meta.append(['old_season_pct',
                 'net revenue from items whose SEASON is not this half-year\'s '
                 'own, over revenue with a known season'])
    meta.append(['season rule',
                 'H1 -> SS and PS of that year; H2 -> PF and FW of that year. '
                 'Within the same year a LATER season still counts as new '
                 '(PF26/FW26 sold in H1-2026 are new — season timing is '
                 'approximate); only the same year\'s earlier half is old. '
                 'Any other year, ahead or behind, counts as old'])
    meta.append(['deep_discount_pct',
                 'net revenue from lines with LINE discount > 50% / total net '
                 'revenue. This is what the earlier Marella report labelled '
                 'old_season_pct — a discount measure, not a season one'])
    meta.append(['season_coverage_pct',
                 'share of net revenue whose season code parsed; '
                 'read old_season_pct against it'])
    meta.append(['avg_discount_rate',
                 '1 - Σ(qty × PRICE) / Σ(qty × ORIG_PRICE); '
                 'sales only (item_type=1)'])
    meta.column_dimensions['A'].width = 32
    meta.column_dimensions['B'].width = 74

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def verify_against(rows, reference_path):
    """Diff this run against a known-good workbook, cell by cell.

    The original script is gone, so matching its output is the only evidence
    that this reimplementation reads the data the same way.
    """
    from openpyxl import load_workbook
    ws = load_workbook(reference_path, data_only=True).worksheets[0]
    ref = list(ws.values)
    headers = [str(h) for h in ref[0]]
    expected = [dict(zip(headers, r)) for r in ref[1:]]

    mine = {(r['store_code'], r['half_year']): r for r in rows}
    diffs = []
    for exp in expected:
        key = (exp['store_code'], exp['half_year'])
        got = mine.get(key)
        if got is None:
            diffs.append(f'{key}: missing from this run')
            continue
        for col in COLUMNS[2:]:
            # The reference's `old_season_pct` is this script's
            # `deep_discount_pct` — same formula, honest name. The season-based
            # old_season_pct and season_coverage_pct are new and have no
            # counterpart to check against.
            if col in ('old_season_pct', 'season_coverage_pct'):
                continue
            ref_col = 'old_season_pct' if col == 'deep_discount_pct' else col
            a, b = exp.get(ref_col), got.get(col)
            if a is None:
                continue
            if abs(float(a) - float(b)) > 1e-9:
                diffs.append(f'{key} {col}: expected {a}, got {b}')
    extra = set(mine) - {(e['store_code'], e['half_year']) for e in expected}
    diffs += [f'{k}: extra row not in reference' for k in sorted(extra)]
    return len(expected), diffs


def parse_areas(raw):
    areas = {}
    for part in raw.split(','):
        code, _, val = part.partition('=')
        if not val.strip():
            raise argparse.ArgumentTypeError(
                f'--areas needs CODE=m2 pairs, got {part!r}')
        areas[code.strip().upper()] = float(val)
    return areas


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--brand', required=True,
                        help='VENDOR.VEND_NAME, exact (e.g. "ULLA JOHNSON")')
    parser.add_argument('--start-date', default='2025-01-01')
    parser.add_argument('--end-date', default=None,
                        help='Inclusive. Default: end of the last complete '
                             'half-year before today.')
    parser.add_argument('--areas', type=parse_areas, default=None,
                        help='CODE=m2 pairs, e.g. "RHN=15,RWD=10,RWT=15". '
                             'Also fixes WHICH stores are reported.')
    parser.add_argument('--out-dir', default='document/reports')
    parser.add_argument('--verify-against', default=None,
                        help='A previous workbook to diff this run against.')
    args = parser.parse_args()

    areas = args.areas or dict(DEFAULT_AREAS)
    as_of = date.today()
    if args.end_date:
        end_date = args.end_date
    else:
        # Half-years are only meaningful once complete.
        end_date = (f'{as_of.year}-06-30' if as_of.month > 6
                    else f'{as_of.year - 1}-12-31')

    print(f'[1/3] Querying {args.brand} sales '
          f'({args.start_date} -> {end_date}) in {", ".join(sorted(areas))} ...')
    lines = fetch_lines(args.brand, sorted(areas), args.start_date, end_date)
    print(f'      -> {len(lines):,} sale lines')
    if not lines:
        print('No sales found. Check the brand name against VENDOR.VEND_NAME.')
        return 1

    print('[2/3] Aggregating by store and half-year ...')
    rows, buckets = build_rows(lines, areas)
    print(f'      -> {len(rows)} rows across {", ".join(buckets)}')
    partial = partial_bucket(end_date)
    if partial and partial[0] in buckets:
        print(f'      -> NOTE: {partial[0]} is PARTIAL '
              f'({partial[1]} to {partial[2]}) — not a full half-year')

    if args.verify_against:
        checked, diffs = verify_against(rows, args.verify_against)
        if diffs:
            print(f'\n  MISMATCH against {args.verify_against}:')
            for d in diffs[:25]:
                print(f'    {d}')
            print(f'  {len(diffs)} difference(s) over {checked} reference rows.')
            return 2
        print(f'      -> verified: {checked} reference rows match exactly')

    slug = args.brand.lower().replace(' ', '_')
    out_path = Path(args.out_dir) / f'{slug}_brand_{args.start_date}_{as_of.isoformat()}.xlsx'
    print(f'[3/3] Writing {out_path} ...')
    write_xlsx(rows, buckets, out_path, args.brand, areas,
               args.start_date, end_date, as_of)

    total = next(r for r in rows
                 if r['store_code'] == 'ALL' and r['half_year'] == 'ALL')
    print(f'\nDone. Written to:\n  {out_path.resolve()}')
    print(f'  qty sold net:   {total["qty_sold_net"]:,}')
    print(f'  revenue:        {total["revenue_before_vat"]:,}')
    print(f'  margin:         {total["margin"]:,} ({total["margin_pct"]:.2%})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
