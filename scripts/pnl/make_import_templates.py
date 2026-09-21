"""
P&L — generate blank import templates.

Writes the two .xlsx files accounting fills in:

  * MONTHLY  one month, uploaded after each month closes
  * BULK     many months side by side, for the one-off historical load

Both use the same grid, so the importer reads them through one code path and
there is one format to explain. They differ only in how many month blocks the
sheet carries.

Regenerate these whenever the chart of accounts or the store list changes —
that is the point of having this as a script rather than a file someone edited
by hand once. The account list is read from `--accounts-from`, a real FAST
export, so the template always matches what FAST actually emits rather than
what it emitted the day someone last touched it.

Usage:
    PYTHONPATH=. python scripts/pnl/make_import_templates.py \\
        --accounts-from "document/PL thang 7.2026.xlsx" \\
        --out-dir document/pnl-templates \\
        --from 2025-01 --to 2026-09

Exit codes:
    0 — both templates written and re-read successfully
    1 — the source export could not be read, or a written template failed to
        parse (which would mean shipping a template the importer rejects)
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from openpyxl import Workbook, load_workbook              # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.utils import get_column_letter              # noqa: E402

from app.modules.pnl.fast_accounts import (is_section_heading,  # noqa: E402
                                           normalise)
from app.modules.pnl.importer import parse                # noqa: E402
from app.modules.pnl.period import MONTHS                 # noqa: E402

# Column order for the store block. `Total` is included because FAST emits it
# and leaving it out would make the template look unlike the real export — but
# the importer ignores it and sums the stores instead. See importer.py.
STORES = ('Total', 'HQ', 'RHN', 'RWR', 'RWT', 'RWD', 'RWL', 'RWP', 'RWC')

# FAST's own store codes, as they appear on row 1 of an export. `Total` has
# none. Keep in step with STORES.
STORE_CODES = {'HQ': 'RW000', 'RHN': 'RW001', 'RWR': 'RW002', 'RWT': 'RW003',
               'RWD': 'RW004', 'RWL': 'RW007', 'RWP': 'RW009', 'RWC': 'RW011'}

HEADER_FILL_A = 'DDEBF7'
HEADER_FILL_B = 'BDD7EE'     # alternates per month block so blocks are legible
SECTION_FILL = 'F2F2F2'


def read_accounts(path):
    """The account labels, in statement order, from a real FAST export."""
    wb = load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    # The header row is the one whose first cell is `Chỉ tiêu`; accounts start
    # below it. Mirrors the importer rather than assuming a fixed row.
    header = next((r for r in range(1, 11)
                   if normalise(ws.cell(r, 1).value or '') == 'Chỉ tiêu'), None)
    if header is None:
        raise SystemExit(f'{path}: no "Chỉ tiêu" header cell found in column A.')
    out = []
    for r in range(header + 1, ws.max_row + 1):
        v = ws.cell(r, 1).value
        if v is None or str(v).strip() == '':
            if out:
                break
            continue
        out.append(normalise(v))
    return out


def months_between(start, end):
    """[(year, month)] inclusive, from two 'YYYY-MM' strings."""
    (y0, m0), (y1, m1) = start, end
    out = []
    while (y0, m0) <= (y1, m1):
        out.append((y0, m0))
        y0, m0 = (y0 + 1, 1) if m0 == 12 else (y0, m0 + 1)
    return out


def build(path, accounts, months, notes):
    wb = Workbook()
    ws = wb.active
    ws.title = 'P&L data'
    bold = Font(bold=True)
    fills = (PatternFill('solid', fgColor=HEADER_FILL_A),
             PatternFill('solid', fgColor=HEADER_FILL_B))
    section_fill = PatternFill('solid', fgColor=SECTION_FILL)

    marker = ws.cell(4, 1, 'Chỉ tiêu')
    marker.font = bold
    marker.fill = fills[0]

    col = 2
    for i, (year, month) in enumerate(months):
        fill = fills[i % 2]
        for store in STORES:
            # Year and month are REPEATED on every column, never merged: a
            # merged range holds its value only in the top-left cell, so the
            # other eight columns would arrive with no period and be rejected.
            for row, value in ((1, year), (2, MONTHS[month - 1]),
                               (3, STORE_CODES.get(store)), (4, store)):
                c = ws.cell(row, col, value)
                c.font = bold
                c.fill = fill
                c.alignment = Alignment(horizontal='center')
            col += 1

    for i, label in enumerate(accounts):
        row = 5 + i
        c = ws.cell(row, 1, label)
        if is_section_heading(label):
            c.font = bold
            for cc in range(1, col):
                ws.cell(row, cc).fill = section_fill
        for cc in range(2, col):
            cell = ws.cell(row, cc, 0)
            cell.number_format = '#,##0'

    ws.freeze_panes = 'B5'
    ws.column_dimensions['A'].width = 62
    for i in range(2, col):
        ws.column_dimensions[get_column_letter(i)].width = 16

    guide = wb.create_sheet('How to fill this in')
    for i, (text, size, is_bold) in enumerate(notes, start=1):
        guide.cell(i, 1, text).font = Font(bold=is_bold, size=size)
    guide.column_dimensions['A'].width = 96

    wb.save(path)
    return col - 2


SHARED_RULES = [
    ('Layout — do not change the shape:', 11, True),
    ('  Row 1   Year         repeated on every column of the month block', 10, False),
    ('  Row 2   Month        full English month name (January … December)', 10, False),
    ('  Row 3   Store code   RW000 … RW011; blank for the Total column', 10, False),
    ('  Row 4   Store name   "Chỉ tiêu" in column A, store names beside it', 10, False),
    ('  Row 5+  Accounts     exactly as FAST exports them, in statement order', 10, False),
    ('', 10, False),
    ('Rules that matter:', 11, True),
    ('  • Repeat Year and Month on EVERY column. Do NOT merge those cells —', 10, False),
    ('    a merged cell keeps its value only in the top-left, so the other', 10, False),
    ('    columns arrive with no period and the whole import is rejected.', 10, False),
    ('  • Keep the numbered section headings (8. Chi phí bán hàng,', 10, False),
    ('    9. Chi phí quản lý doanh nghiệp) and the account order. The same', 10, False),
    ('    account name appears under both sections, and the heading above it', 10, False),
    ('    is the only thing distinguishing store payroll from G&A payroll.', 10, False),
    ('  • The Total column is optional and is NOT read. The tool sums the', 10, False),
    ('    stores itself, so a store left out of Total is still counted.', 10, False),
    ('  • Blank or 0 where there is no value.', 10, False),
    ('  • A new or reworded account will be REJECTED by name until it is', 10, False),
    ('    added to the tool. That is deliberate — it is how a cost stops', 10, False),
    ('    going missing silently. Send the rejection message on.', 10, False),
]


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument('--accounts-from', required=True,
                   help='A real FAST export to take the account list from')
    p.add_argument('--out-dir', default='.')
    p.add_argument('--from', dest='start', default='2025-01',
                   help='First month of the BULK template, YYYY-MM')
    p.add_argument('--to', dest='end', default=None,
                   help='Last month of the BULK template, YYYY-MM '
                        '(default: last closed month)')
    args = p.parse_args()

    accounts = read_accounts(args.accounts_from)

    start = tuple(int(x) for x in args.start.split('-'))
    if args.end:
        end = tuple(int(x) for x in args.end.split('-'))
    else:
        today = date.today()
        end = (today.year, today.month - 1) if today.month > 1 \
            else (today.year - 1, 12)
    months = months_between(start, end)

    os.makedirs(args.out_dir, exist_ok=True)

    monthly_notes = (
        [('GBL Master — P&L MONTHLY import template', 14, True), ('', 10, False),
         ('One month per file, uploaded once accounting closes the month.', 10, False),
         ('', 10, False)]
        + SHARED_RULES
        + [('', 10, False),
           ('The raw FAST export is also accepted', 11, True),
           ('  If it is easier to send the FAST file unchanged, that works:', 10, False),
           ('  it has no Year/Month rows, so name the sheet T07.2026 (month', 10, False),
           ('  then year) or set the period when uploading. This template is', 10, False),
           ('  the sturdier option because the period travels IN the file.', 10, False)])

    bulk_notes = (
        [('GBL Master — P&L BULK import template', 14, True), ('', 10, False),
         (f'The one-off historical load: {months[0][0]}-{months[0][1]:02d} to '
          f'{months[-1][0]}-{months[-1][1]:02d}, every month side by side.', 10, False),
         ('After this, use the MONTHLY template once a month.', 10, False),
         ('', 10, False)]
        + SHARED_RULES
        + [('', 10, False),
           ('All or nothing', 11, True),
           ('  If any month fails validation the entire upload is rejected and', 10, False),
           ('  nothing is stored, so you are never left guessing which months', 10, False),
           ('  landed. Every problem is listed at once, per month.', 10, False)])

    monthly_path = os.path.join(args.out_dir, 'PNL_import_template_MONTHLY.xlsx')
    bulk_path = os.path.join(args.out_dir, 'PNL_import_template_BULK.xlsx')

    n_monthly = build(monthly_path, accounts, [months[-1]], monthly_notes)
    n_bulk = build(bulk_path, accounts, months, bulk_notes)

    print(f'{len(accounts)} accounts from {args.accounts_from}')
    print(f'  MONTHLY  {monthly_path}  ({n_monthly} data columns, '
          f'{months[-1][0]}-{months[-1][1]:02d})')
    print(f'  BULK     {bulk_path}  ({n_bulk} data columns, {len(months)} months)')

    # A template the importer would reject is worse than no template, so both
    # are read back through the real parser before this exits 0.
    for path in (monthly_path, bulk_path):
        try:
            result = parse(load_workbook(path, data_only=True)['P&L data'])
        except Exception as e:                                # noqa: BLE001
            print(f'FAILED: {path} does not parse: {e}', file=sys.stderr)
            sys.exit(1)
        print(f'  verified {os.path.basename(path)}: layout={result["layout"]}, '
              f'{len(result["months"])} month(s), {len(result["entities"])} stores')


if __name__ == '__main__':
    main()
