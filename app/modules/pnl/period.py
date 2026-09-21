"""Period maths for the P&L: which months a report covers.

Transcribed from the workbook's `Period` sheet, cells B19-B21:

    B19  selected month index         =MATCH(B8, {January..December}, 0)
    B20  first month of the basis     Month -> B19
                                      Quarter -> B19 - MOD(B19-1, 3)
                                      Half -> 1 if B19<=6 else 7
                                      Year -> 1
    B21  last month                   =B19

Both the actual and prior-year columns use the SAME month range, one year
apart — the comparison is calendar-aligned, so June Half-to-date is Jan-Jun
2026 against Jan-Jun 2025. This is unlike the weekly report, whose MTD/YTD
windows end on a closing Sunday; a P&L period is always whole months.
"""
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July',
          'August', 'September', 'October', 'November', 'December')

MONTH_INDEX = {name: i for i, name in enumerate(MONTHS, start=1)}

# The workbook's `View` dropdown.
BASES = ('Month-to-date', 'Quarter-to-date', 'Half-to-date', 'Year-to-date')


def month_index(month) -> int:
    """Accept either a month name ('June') or an index (6)."""
    if isinstance(month, int):
        if not 1 <= month <= 12:
            raise ValueError(f'month index out of range: {month}')
        return month
    try:
        return MONTH_INDEX[str(month).strip().capitalize()]
    except KeyError:
        raise ValueError(f'unknown month: {month!r}') from None


def month_range(month, basis: str) -> tuple:
    """(first month, last month) inclusive, both 1-12.

    The last month is always the selected one — a period runs TO the month
    being reported, never past it.
    """
    m = month_index(month)
    if basis == 'Month-to-date':
        first = m
    elif basis == 'Quarter-to-date':
        first = m - ((m - 1) % 3)
    elif basis == 'Half-to-date':
        first = 1 if m <= 6 else 7
    elif basis == 'Year-to-date':
        first = 1
    else:
        raise ValueError(f'unknown basis: {basis!r} (expected one of {BASES})')
    return first, m


def period_label(year: int, month, basis: str) -> str:
    """e.g. 'June-2026/Half-to-date' — the workbook's own heading format."""
    return f'{MONTHS[month_index(month) - 1]}-{year}/{basis}'
