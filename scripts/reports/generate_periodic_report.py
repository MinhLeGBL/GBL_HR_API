"""
Periodic sales Excel report: week-over-week, plus MTD / YTD against last year.

Command-line front end for `app.modules.weekly_report`. The logic lives in the
module — this script parses arguments, runs one Oracle fetch and writes the
workbook to disk. The scheduled Monday job (`scripts/jobs/periodic_report_generate.py`)
goes through the same module, so the file emailed to recipients is the file this
command produces.

Periods (`--as-of` defaults to today)
-------------------------------------
  WOW  "Week by Week" — the LAST COMPLETE week against THE WEEK BEFORE IT.
       Run in week 38 and it reports week 37 vs week 36, Monday to Sunday
       (`--week-start sunday` shifts the week boundary). The partial current
       week is never included, so both sides are always whole weeks.
       This window does NOT compare against last year; MTD and YTD do.
  MTD  1st of month .. as-of (inclusive)
  YTD  1st of January .. as-of (inclusive)

`--through` ends the MTD and YTD windows on a day earlier than `--as-of`. The
scheduled run uses it to stop both on the Sunday that closes the reported week:
at 03:00 on Monday the current day carries almost no sales while the prior-year
side of the comparison carries a full trading day, which understates MTD and YTD
— severely so in the first days of a month.

Comparison windows differ by sheet:

- **WOW compares consecutive weeks** — a flat 7-day shift back, so the two sides
  are adjacent whole weeks on the same weekdays. No ISO-week or leap-year
  handling is needed. An earlier revision compared the same ISO week one year
  earlier; that was replaced because the week sheet is for reading momentum
  week to week, which a year-ago week cannot show.
- **MTD and YTD are calendar-date aligned against the PRIOR YEAR** — the
  identical month/day range one year earlier. Feb 29 in a leap-year `as-of`
  maps to Feb 28 of the prior (non-leap) year.

`--week-start sunday` shifts the week boundary, but week *numbering* in labels
stays ISO (Monday-based), so a Sunday-start week is labelled by the ISO week
containing its start date.

Departments, metrics, revenue formulas and the deliberate divergences from
`app/modules/reports/queries.py` are documented in the module:
`app/modules/weekly_report/{period,queries,aggregate}.py`.

Output: document/reports/periodic_report_<as_of>.xlsx
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# These imports are RE-EXPORTS as much as imports. `tests/unit/scripts/
# test_periodic_report.py` loads this file by path and reaches for
# `pr.shift_year`, `pr.aggregate`, `pr.METRIC_COLUMNS` and the rest, so the
# names must stay bound here even though main() uses only a few of them.
from app.modules.weekly_report.aggregate import (  # noqa: E402,F401
    LOWER_IS_BETTER, METRICS, ZERO, ZERO_METRICS, _COMPONENTS, aggregate,
    blank as _blank, delta as _delta, metrics as _metrics)
from app.modules.weekly_report.excel import (  # noqa: E402,F401
    BAD_COLOUR, COUNT_FORMAT, METRIC_COLUMNS, MONEY_1DP_FORMAT, MONEY_FORMAT,
    PCT_FORMAT, POINT_FORMAT, _UNIT_FORMAT, build_workbook)
from app.modules.weekly_report.period import (  # noqa: E402,F401
    DEPT_MERGE, UNKNOWN_DEPT, fetch_span, merge_department, period_windows,
    previous_week_window, shift_year, week_label)
from app.modules.weekly_report.repository import (  # noqa: E402,F401
    WeeklyReportRepository)


def fetch(from_date, to_date):
    """Pull both grains over [from_date, to_date] inclusive."""
    return WeeklyReportRepository().fetch_sales(from_date, to_date)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--as-of', default=None,
                        help='Period end date, YYYY-MM-DD (default: today)')
    parser.add_argument('--through', default=None,
                        help='Last day MTD/YTD include, YYYY-MM-DD '
                             '(default: --as-of). Use the last CLOSED day when '
                             'running early in the morning.')
    parser.add_argument('--week-start', choices=('monday', 'sunday'), default='monday',
                        help='First day of the week (default: monday)')
    parser.add_argument('--out-dir', default='document/reports',
                        help='Output directory (default: document/reports)')
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    through = date.fromisoformat(args.through) if args.through else None
    windows = period_windows(as_of, args.week_start, through)

    # One fetch covering every window -- see `fetch_span` for why the earliest
    # start is taken across all six windows rather than assumed to be YTD's.
    fetch_from, fetch_to = fetch_span(windows)
    print(f'as-of {as_of} (week starts {args.week_start})'
          + (f', MTD/YTD through {through}' if through else ''))
    for label in ('WOW', 'MTD', 'YTD'):
        (cf, ct), (pf, pt) = windows[label]
        suffix = (f'   [{week_label(cf)} vs {week_label(pf)}, week over week]'
                  if label == 'WOW' else '')
        print(f'  {label}: {cf} .. {ct}   vs   {pf} .. {pt}{suffix}')
    print(f'fetching {fetch_from} .. {fetch_to} ...')

    dept_rows, store_rows = fetch(fetch_from, fetch_to)
    print(f'  -> {len(dept_rows)} day x store x dept rows, '
          f'{len(store_rows)} day x store rows')

    out = Path(args.out_dir) / f'periodic_report_{as_of}.xlsx'
    build_workbook(out, as_of, windows, dept_rows, store_rows)
    print(f'wrote {out}')


if __name__ == '__main__':
    main()
