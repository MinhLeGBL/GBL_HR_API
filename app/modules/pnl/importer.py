"""Parse a FAST P&L export into facts.

No database, no Flask — a file in, rows and problems out, so the parsing rules
can be tested against real exports without touching anything.

NOTHING DERIVABLE IS READ FROM THE FILE
---------------------------------------
The export supplies a `Total` column and its own subtotal rows (`3. Doanh thu
thuần`, `5. Lợi nhuận gộp`, …). Both are ignored as *inputs*: the company total
is summed from the stores, and every subtotal is computed by `statement.py`
from the leaf lines. They are kept only as a CROSS-CHECK, reported alongside
the facts so a divergence is visible rather than silently inherited.

That is not a stylistic choice. The July 2026 export's `Total` column excludes
the RWC store entirely — exactly, in all 80 rows — so a report that copied
`Total` would omit a trading store and never say so.

LAYOUTS
-------
All supported shapes put one account per row and one (month, store) per column.
They differ only in how many header rows sit above the account grid, so one
parser reads them all: the header row is the one whose first cell is
`Chỉ tiêu`, and whatever lies above it is read upwards.

`LAYOUT_MONTHLY` — what FAST emits, one month per sheet:

        A                      B       C       D     ...
    1                                  RW000   RW001      <- store codes
    2   Chỉ tiêu               Total   HQ      RHN        <- store names
    3+  <account label>        <amounts>

  The period is not in the grid; it comes from the sheet name, `T07.2026`.

`LAYOUT_BULK` — the same thing with every month side by side, for the one-off
historical load:

        A                      B       C       D     ...
    1                          2025    2025    2025       <- year
    2                          January January January    <- month
    3                          RW000   RW001   RW002      <- store codes
    4   Chỉ tiêu               HQ      RHN     RWR        <- store names
    5+  <account label>        <amounts>

  The period comes from the grid, so the sheet may be named anything.

`LAYOUT_HISTORY` — the `Inputs Fast` sheet of the original workbook, kept
because that workbook is the accuracy cross-check and the only existing source
of 2025 history. Account labels sit in column B there, not A, and column A
carries a mapping that is READ BUT NOT TRUSTED — the mapping lives in
`fast_accounts.py`.

Layout is detected, never guessed from the caller.
"""
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .fast_accounts import FAST_ACCOUNTS, is_section_heading, normalise
from .period import MONTHS, month_index

LAYOUT_MONTHLY = 'monthly'
LAYOUT_BULK = 'bulk'
LAYOUT_HISTORY = 'history'

# Where the header row's first cell says the account column begins.
_HEADER_MARKER = 'Chỉ tiêu'
# How far down to look for it before giving up.
_MAX_HEADER_ROW = 10

# The company total is this sum, never a column read from the file.
TOTAL_ENTITY = 'Total'

# `T07.2026`, `T7.2026`, and the tolerable `T07-2026`.
_SHEET_PERIOD = re.compile(r'^\s*T\s*(\d{1,2})\s*[.\-_/]\s*(\d{4})\s*$', re.I)


class ImportProblem(Exception):
    """A file that cannot be trusted. Carries every problem, not just the
    first — one round trip should tell the uploader everything to fix."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__(f'{len(self.problems)} problem(s): '
                         + '; '.join(self.problems[:5]))


# Kept so existing callers and tests importing the old name still work.
ImportError_ = ImportProblem


def _decimal(value):
    """Money as Decimal. Blank is zero; anything non-numeric returns None."""
    if value is None or value == '':
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def find_header(ws):
    """(header_row, label_col) — the row whose first cell is `Chỉ tiêu`.

    That row carries the store names; the account labels run down the column
    it sits in. Searching for it rather than assuming a fixed row is what lets
    the monthly and bulk layouts share one parser: they differ only in how many
    header rows sit above this one.
    """
    for col in (1, 2):
        for row in range(1, _MAX_HEADER_ROW + 1):
            if normalise(ws.cell(row, col).value or '') == _HEADER_MARKER:
                return row, col
    raise ImportProblem([
        f"Unrecognised sheet layout: no {_HEADER_MARKER!r} cell found in "
        f'column A or B within the first {_MAX_HEADER_ROW} rows. That cell '
        'marks the header row, with store names beside it and account labels '
        'below it.'])


def _classify_header_rows(ws, header_row, first_data_col, last_col):
    """Work out which rows above the header hold the year and the month.

    Identified by CONTENT, not position: a bulk sheet may or may not carry the
    store-code row, and assuming a fixed offset would silently read months as
    codes. A row of four-digit numbers is the year; a row of month names is the
    month; anything else above the header is treated as store codes.
    """
    year_row = month_row = None
    for row in range(1, header_row):
        values = [ws.cell(row, c).value for c in range(first_data_col, last_col + 1)]
        values = [v for v in values if v is not None and str(v).strip() != '']
        if not values:
            continue
        if year_row is None and all(
                isinstance(v, int) or str(v).strip().isdigit() for v in values):
            if all(1900 <= int(str(v).strip()) <= 2999 for v in values):
                year_row = row
                continue
        if month_row is None:
            try:
                for v in values:
                    month_index(normalise(v))
            except ValueError:
                continue
            month_row = row
    return year_row, month_row


def detect_layout(ws):
    """(layout, header_row, label_col, year_row, month_row)."""
    header_row, label_col = find_header(ws)
    if label_col == 2:
        # The original `Inputs Fast` workbook: mapping in A, labels in B,
        # data from C, with year/month/entity stacked in rows 1-3.
        return LAYOUT_HISTORY, header_row, label_col, 1, 2
    first_data_col = label_col + 1
    year_row, month_row = _classify_header_rows(
        ws, header_row, first_data_col, ws.max_column)
    layout = LAYOUT_BULK if (year_row and month_row) else LAYOUT_MONTHLY
    return layout, header_row, label_col, year_row, month_row


def period_from_sheet_name(name):
    """(year, month) from a sheet named like `T07.2026`, else None."""
    m = _SHEET_PERIOD.match(str(name or ''))
    if not m:
        return None
    month, year = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return None
    return year, month


def _read_columns(ws, header_row, label_col, year_row, month_row,
                  sheet_name, year=None, month=None):
    """[(col, year, month, entity, code)] for every data column.

    `year_row`/`month_row` are None on a monthly sheet, where the period comes
    from the sheet name (or from an explicit override supplied with the
    upload). Store codes are whatever other row sits above the header.
    """
    columns, problems = [], []
    fallback = None
    if year_row is None or month_row is None:
        fallback = (year, month) if (year and month) else \
            period_from_sheet_name(sheet_name)
        if fallback is None:
            problems.append(
                f'Cannot tell which month {sheet_name!r} covers. Name the '
                "sheet like 'T07.2026', add Year and Period header rows, or "
                'supply the year and month with the upload.')
            return [], problems
        if month is not None:
            try:
                fallback = (fallback[0], month_index(month))
            except ValueError as e:
                return [], [str(e)]

    code_rows = [r for r in range(1, header_row)
                 if r not in (year_row, month_row)]

    for c in range(label_col + 1, ws.max_column + 1):
        entity = ws.cell(header_row, c).value
        if entity is None or str(entity).strip() == '':
            continue
        if fallback is not None:
            y, m = fallback
        else:
            raw_y, raw_m = ws.cell(year_row, c).value, ws.cell(month_row, c).value
            if raw_y is None or raw_m is None:
                problems.append(
                    f'column {c}: incomplete header '
                    f'(year={raw_y!r}, period={raw_m!r}, store={entity!r})')
                continue
            try:
                y, m = int(raw_y), month_index(normalise(raw_m))
            except (TypeError, ValueError) as e:
                problems.append(f'column {c}: {e}')
                continue
        code = None
        for r in code_rows:
            v = ws.cell(r, c).value
            if v is not None and str(v).strip():
                code = normalise(v)
                break
        columns.append((c, y, m, normalise(entity), code))

    if not columns and not problems:
        problems.append(
            f'No store columns found on row {header_row}.')
    return columns, problems


def _accounts(ws, first_row, label_col):
    """[(row, section, label, category)] for every mapped account row.

    `section` is the most recent numbered heading at or above the row — the
    same label means different things under different sections, so the mapping
    is keyed on both. Excluded accounts (FAST's own subtotals) are returned
    with a category of None so the caller can use them for cross-checking.
    """
    accounts, problems = [], []
    section = None
    for r in range(first_row, ws.max_row + 1):
        raw = ws.cell(r, label_col).value
        if raw is None or str(raw).strip() == '':
            # The statement block is contiguous, so the first blank account
            # ends it. Scanning past the blank would pick up whatever sits
            # below — the history sheet parks period helper rows there.
            if accounts:
                break
            continue
        label = normalise(raw)
        if is_section_heading(label):
            section = label
        key = (section, label)
        if key not in FAST_ACCOUNTS:
            problems.append(
                f'row {r}: unknown FAST account {label!r} under section '
                f'{section!r} — add it to fast_accounts.py (as a category, or '
                'as None to exclude it deliberately)')
            continue
        accounts.append((r, section, label, FAST_ACCOUNTS[key]))
    if not accounts and not problems:
        problems.append('No account rows found.')
    return accounts, problems


def parse(ws, sheet_name=None, year=None, month=None) -> dict:
    """Parse a worksheet into facts.

    Returns::

        {'layout', 'facts', 'months', 'entities', 'store_codes',
         'reported_totals', 'total_check'}

    `facts` excludes the `Total` column and every FAST subtotal row: both are
    derivable, and derivable things are computed, not copied. `total_check`
    reports what the file's own `Total` column said versus the sum of the
    stores, per month and category, so a divergence is visible.
    """
    layout, header_row, label_col, year_row, month_row = detect_layout(ws)
    sheet_name = sheet_name if sheet_name is not None else ws.title

    columns, problems = _read_columns(
        ws, header_row, label_col, year_row, month_row, sheet_name, year, month)
    accounts, account_problems = _accounts(
        ws, first_row=header_row + 1, label_col=label_col)

    problems = list(problems) + account_problems
    if problems:
        raise ImportProblem(problems)

    facts = []
    store_totals = defaultdict(Decimal)     # (y, m, category) -> summed stores
    reported = defaultdict(Decimal)         # (y, m, category) -> file's Total
    codes = {}

    for row, _section, label, category in accounts:
        for col, y, m, entity, code in columns:
            amount = _decimal(ws.cell(row, col).value)
            if amount is None:
                problems.append(
                    f'row {row}, column {col}: not a number: '
                    f'{ws.cell(row, col).value!r} ({label})')
                continue
            if category is None:            # a FAST subtotal — never a fact
                continue
            if entity == TOTAL_ENTITY:
                reported[(y, m, category)] += amount
                continue                    # derivable: recomputed below
            if code:
                codes[entity] = code
            if amount == 0:
                continue
            facts.append({'year': y, 'month': m, 'entity': entity,
                          'category': category, 'fast_account': label,
                          'amount': amount})
            store_totals[(y, m, category)] += amount

    if problems:
        raise ImportProblem(problems)

    entities = sorted({e for _c, _y, _m, e, _k in columns} - {TOTAL_ENTITY})
    return {
        'layout': layout,
        'facts': facts,
        'months': sorted({(y, m) for _c, y, m, _e, _k in columns}),
        'entities': entities,
        'store_codes': codes,
        'total_check': _total_check(store_totals, reported),
    }


def _total_check(store_totals, reported):
    """Compare the file's own `Total` column with the sum of its stores.

    Reported, never enforced. The July 2026 export's `Total` omits the RWC
    store, so requiring agreement would reject a valid file; copying `Total`
    would drop a store. Summing and reporting the gap does neither.
    """
    rows = []
    for key in sorted(set(store_totals) | set(reported)):
        y, m, category = key
        computed = store_totals.get(key, Decimal(0))
        supplied = reported.get(key, Decimal(0))
        if computed != supplied:
            rows.append({'year': y, 'month': m, 'month_name': MONTHS[m - 1],
                         'category': category,
                         'computed': computed, 'reported': supplied,
                         'difference': computed - supplied})
    return rows
