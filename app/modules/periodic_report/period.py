"""Period maths and department merging for the periodic sales report.

Pure functions, no I/O — the same logic the standalone generator
(`scripts/reports/generate_periodic_report.py`) has used since Phase 1. The
script now imports these rather than carrying its own copy, so the figures the
API serves and the figures in the .xlsx cannot drift apart.

Window rules
------------
  WOW  the LAST COMPLETE week vs THE WEEK BEFORE IT (a flat 7-day shift).
       The partial current week is never included.
  MTD  1st of month .. `through` (inclusive), vs the same range a year earlier.
  YTD  1st of January .. `through` (inclusive), vs the same range a year earlier.

`through` defaults to `as_of`. The scheduled Monday run passes the last closed
day instead — see `period_windows`.
"""
from datetime import timedelta

# Women/Men pairs collapse to their shared category; everything else passes
# through. Explicit rather than "strip a leading W/M" so a future code such as
# MISC or WHSE is never silently mangled.
DEPT_MERGE = {
    'WRTW': 'RTW', 'MRTW': 'RTW',
    'WBAG': 'BAG', 'MBAG': 'BAG',
    'WSHO': 'SHO', 'MSHO': 'SHO',
    'WACC': 'ACC', 'MACC': 'ACC',
    'WJEW': 'JEW', 'MJEW': 'JEW',
}

UNKNOWN_DEPT = 'UNKNOWN'

PERIOD_LABELS = ('WOW', 'MTD', 'YTD')


def merge_department(raw):
    """Map a raw DCS.D_LONG_NAME onto its reporting department."""
    if raw is None:
        return UNKNOWN_DEPT
    code = str(raw).strip().upper()
    if not code:
        return UNKNOWN_DEPT
    return DEPT_MERGE.get(code, code)


def shift_year(d, years=1):
    """Same calendar date `years` earlier, folding Feb 29 back to Feb 28."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:          # 29 Feb -> non-leap year
        return d.replace(year=d.year - years, month=2, day=28)


def week_label(start):
    """'Week 37 2026' for the ISO week containing `start`."""
    iso = start.isocalendar()
    return f'Week {iso.week} {iso.year}'


def previous_week_window(cur_start, cur_end):
    """The week immediately before [cur_start, cur_end] — a flat 7-day shift.

    The week sheet is week-over-week: the latest full week against the one
    before it. A plain 7-day shift needs no ISO-week or leap handling, always
    lands on the same weekdays, and never straddles a week boundary.
    """
    shift = timedelta(days=7)
    return cur_start - shift, cur_end - shift


def last_complete_week(as_of, week_start='monday'):
    """The last complete week on or before `as_of`, as (begin, end) inclusive.

    The week CONTAINING `as_of` is treated as in-progress and excluded, so a run
    on the final day of a week still reports the week before it. Run on any day
    of week 38 and this returns week 37.
    """
    if week_start == 'sunday':
        # Monday=0 .. Sunday=6  ->  days since the most recent Sunday.
        back = (as_of.weekday() + 1) % 7
    else:
        back = as_of.weekday()

    current_week_start = as_of - timedelta(days=back)
    end = current_week_start - timedelta(days=1)
    return end - timedelta(days=6), end


def period_windows(as_of, week_start='monday', through=None):
    """Return {label: ((cur_from, cur_to), (prior_from, prior_to))} inclusive.

    WOW compares the last complete week with the week before it. MTD and YTD
    compare against the prior YEAR, calendar-date aligned (Feb 29 folds to
    Feb 28).

    `through` is the last day MTD and YTD include; it defaults to `as_of`, which
    is what an ad-hoc CLI run wants. The scheduled Monday 03:00 run passes the
    last CLOSED day (the Sunday that ends the reported week) instead, because at
    03:00 the current Monday has all but no sales on it while the prior-year
    side of the comparison carries a full trading day — which would understate
    MTD and YTD, severely so in the first days of a month.

    Note that the month and year are taken from `through`, not `as_of`: on
    Monday 1 September with `through` = Sunday 31 August, MTD is the whole of
    August, not an inverted 1 Sep .. 31 Aug range. The same rule makes a run on
    1 January report the prior year in full.
    """
    week_begin, week_end = last_complete_week(as_of, week_start)
    windows = {
        'WOW': ((week_begin, week_end), previous_week_window(week_begin, week_end)),
    }

    period_end = as_of if through is None else through
    for label, start in (('MTD', period_end.replace(day=1)),
                         ('YTD', period_end.replace(month=1, day=1))):
        windows[label] = ((start, period_end),
                          (shift_year(start), shift_year(period_end)))
    return windows


def fetch_span(windows):
    """The single (from, to) range covering every window in `windows`.

    Usually the earliest start is the prior-year 1 January, but it is not safe
    to assume so: the WOW comparison week can begin earlier than the YTD prior
    window (a run on 2026-01-05 needs data from 2024-12-22 once the prior-year
    YTD window is short). Taking the extremes of all six windows is correct in
    the general case; assuming YTD bounds it silently reads those days as zero.
    """
    starts = [start for w in windows.values() for start, _end in w]
    ends = [end for w in windows.values() for _start, end in w]
    return min(starts), max(ends)
