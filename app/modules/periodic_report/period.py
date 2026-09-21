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
from calendar import monthrange
from datetime import date, timedelta

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

# Presentation order. WTD, MTD and YTD all compare against a year earlier;
# WOW is the odd one out (week against the previous week) and sits last.
PERIOD_LABELS = ('WTD', 'MTD', 'YTD', 'WOW')

# The periods the written analysis covers. WTD is deliberately absent: it was
# added as a figures-only view, and feeding it to the model would spend tokens
# narrating a comparison nobody asked to have narrated.
COMMENTARY_LABELS = ('WOW', 'MTD', 'YTD')

# Display names for every period, used by the payload and so by the page's
# tabs. Wider than `excel.SHEET_NAMES`, which covers only the workbook's three
# sheets — WTD is an app-only view.
PERIOD_TITLES = {'WTD': 'WTD', 'MTD': 'MTD', 'YTD': 'YTD',
                 'WOW': 'Week by Week'}


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


def month_end(d):
    """The last day of `d`'s month."""
    return d.replace(day=monthrange(d.year, d.month)[1])


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


def prior_iso_week(week_begin, week_end):
    """The same ISO WEEK NUMBER one year earlier, as (from, to).

    Not `shift_year`. MTD and YTD align on the calendar date, which is right
    for a month or a year — but a week aligned by date drifts across weekdays,
    so "the same week last year" would compare a Mon-Sun run against a Sun-Sat
    one and carry a different number of Saturdays. Retail weeks are only
    comparable weekday-for-weekday.

    Week 38 of 2026 (14-20 Sep) therefore pairs with week 38 of 2025
    (15-21 Sep), not with 14-20 Sep 2025.

    The ISO week is taken from a day three into the week rather than from
    `week_begin`, so this holds for a Sunday-start week too: the anchor stays
    inside the ISO week that contains most of it, and the prior-year anchor is
    placed on the same ISO weekday, preserving the offset.
    """
    anchor = week_begin + timedelta(days=3)
    iso_year, iso_week, iso_weekday = anchor.isocalendar()
    try:
        prior_anchor = date.fromisocalendar(iso_year - 1, iso_week, iso_weekday)
    except ValueError:
        # The prior ISO year has no week 53 — most years have 52. Compare
        # against its last week rather than failing or silently skipping.
        prior_anchor = date.fromisocalendar(iso_year - 1, 52, iso_weekday)
    prior_begin = prior_anchor - timedelta(days=3)
    return prior_begin, prior_begin + (week_end - week_begin)


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

    **When the reported week straddles a month boundary, MTD reports the month
    that ENDED inside it, in full.** Week 36 of 2026 runs 31 Aug - 6 Sep; MTD
    there is the whole of August, not the first six days of September. Without
    this, a complete month is never reported at all: the last week ending in
    August stops on the 30th, and the next week jumps to September — so August
    is only ever seen a day short. That happens in every month whose last day is
    not a Sunday. Six days of a new month is also a poor comparison against six
    days of the prior year, while a complete month is the natural unit.

    **YTD ends wherever MTD ends.** Whenever the reported week closed a month,
    year-to-date runs to that month end as well, so the two columns always
    describe the same span and YTD is the sum of the months reported. Week 36
    of 2026 gives MTD = the whole of August and YTD = 1 January to 31 August.

    Across a year boundary this falls out of the same rule: the week spanning
    New Year closes December, so YTD reports the whole of the year that just
    ended rather than the two or three days of the new one — which would
    otherwise sit next to a complete December and say far less. A complete year
    is likewise never reported otherwise, since 31 December is rarely a Sunday.
    """
    week_begin, week_end = last_complete_week(as_of, week_start)
    windows = {
        'WOW': ((week_begin, week_end), previous_week_window(week_begin, week_end)),
        # Same week, same weekday span, one year back — see prior_iso_week.
        'WTD': ((week_begin, week_end), prior_iso_week(week_begin, week_end)),
    }

    period_end = as_of if through is None else through

    if week_begin.month != week_end.month and period_end <= week_end:
        # A month ended inside the reported week, and the report is anchored to
        # that week. Report the month whole.
        #
        # The `period_end <= week_end` guard matters: an ad-hoc run that asks
        # for a LATER cut-off ("--as-of 9 Sep", no --through) is asking for
        # month-to-date on the 9th, and should get it, even though the week it
        # reports on happens to straddle. Production always anchors to the week,
        # so the scheduled run always takes the branch above.
        mtd_from = week_begin.replace(day=1)
        mtd_to = month_end(week_begin)
    else:
        mtd_from = period_end.replace(day=1)
        mtd_to = period_end
    windows['MTD'] = ((mtd_from, mtd_to),
                      (shift_year(mtd_from), shift_year(mtd_to)))

    if week_begin.month != week_end.month and period_end <= week_end:
        # YTD ENDS WHERE MTD ENDS. The same condition as the month above, not a
        # narrower year-only one: whenever the report is anchored to a week that
        # closed a month, year-to-date runs to that month end too.
        #
        # Getting this wrong is subtle, because the figure still looks
        # plausible. Week 36 of 2026 (31 Aug - 6 Sep) reported MTD as the whole
        # of August while YTD ran to 6 September — so YTD carried six days the
        # MTD beside it excluded, and was not the sum of the months shown. The
        # two columns silently described different periods.
        #
        # This also SUBSUMES the year-boundary case rather than special-casing
        # it: the week spanning New Year straddles December into January, so
        # `month_end(week_begin)` is 31 December and the year is reported whole
        # exactly as before. One rule, two effects.
        ytd_from = date(week_begin.year, 1, 1)
        ytd_to = month_end(week_begin)
    else:
        ytd_from = period_end.replace(month=1, day=1)
        ytd_to = period_end
    windows['YTD'] = ((ytd_from, ytd_to),
                      (shift_year(ytd_from), shift_year(ytd_to)))
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
