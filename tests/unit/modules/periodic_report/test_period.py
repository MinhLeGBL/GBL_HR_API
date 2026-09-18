"""Unit tests for app.modules.periodic_report.period.

The Phase 1 period maths is already covered by
`tests/unit/scripts/test_periodic_report.py`, which loads the CLI script and so
exercises these same functions through it. What is tested here is what Phase 2
ADDED: the `through` clamp that ends MTD and YTD on the last closed day, and
`fetch_span`.
"""
from datetime import date

from app.modules.periodic_report.period import (fetch_span, last_complete_week,
                                                period_windows, shift_year)


class TestLastCompleteWeek:
    def test_excludes_the_week_containing_as_of(self):
        # Wed 2026-09-09 sits in week 37; the last COMPLETE week is week 36.
        assert last_complete_week(date(2026, 9, 9)) == (date(2026, 8, 31),
                                                        date(2026, 9, 6))

    def test_monday_reports_the_week_that_just_closed(self):
        # The scheduled run's case: Monday 2026-09-14 at 03:00 reports
        # Mon 09-07 .. Sun 09-13, the week that ended a few hours earlier.
        assert last_complete_week(date(2026, 9, 14)) == (date(2026, 9, 7),
                                                         date(2026, 9, 13))

    def test_sunday_still_reports_the_previous_week(self):
        # A run on the final day of a week must NOT report that week — it is
        # still in progress until midnight.
        assert last_complete_week(date(2026, 9, 13)) == (date(2026, 8, 31),
                                                         date(2026, 9, 6))

    def test_sunday_week_start_shifts_the_boundary(self):
        w = last_complete_week(date(2026, 9, 9), 'sunday')
        assert w == (date(2026, 8, 30), date(2026, 9, 5))
        assert w[0].weekday() == 6   # starts on a Sunday


class TestThroughClamp:
    """The Monday 03:00 run passes the closing Sunday as `through`.

    Without it, MTD and YTD would run to the Monday itself — a day with almost
    no sales at 03:00 — while the prior-year side carries a full trading day.
    """

    def test_default_through_is_as_of(self):
        w = period_windows(date(2026, 9, 14))
        assert w['MTD'][0] == (date(2026, 9, 1), date(2026, 9, 14))
        assert w['YTD'][0] == (date(2026, 1, 1), date(2026, 9, 14))

    def test_through_ends_mtd_and_ytd_on_the_closed_day(self):
        w = period_windows(date(2026, 9, 14), 'monday', date(2026, 9, 13))
        assert w['MTD'][0] == (date(2026, 9, 1), date(2026, 9, 13))
        assert w['YTD'][0] == (date(2026, 1, 1), date(2026, 9, 13))

    def test_through_does_not_touch_the_week_window(self):
        # The week sheet already uses the last complete week; the clamp is only
        # about MTD/YTD.
        plain = period_windows(date(2026, 9, 14))
        clamped = period_windows(date(2026, 9, 14), 'monday', date(2026, 9, 13))
        assert plain['WOW'] == clamped['WOW']

    def test_prior_year_side_is_clamped_too(self):
        # Both sides must end on the same calendar day, or the comparison
        # silently gains a day on one side.
        w = period_windows(date(2026, 9, 14), 'monday', date(2026, 9, 13))
        assert w['MTD'][1] == (date(2025, 9, 1), date(2025, 9, 13))
        assert w['YTD'][1] == (date(2025, 1, 1), date(2025, 9, 13))

    def test_month_is_taken_from_through_not_as_of(self):
        # Monday 1 Sep with through = Sunday 31 Aug. Taking the month from
        # `as_of` would give an INVERTED window (1 Sep .. 31 Aug); it must be
        # the whole of August instead.
        w = period_windows(date(2026, 9, 1), 'monday', date(2026, 8, 31))
        assert w['MTD'][0] == (date(2026, 8, 1), date(2026, 8, 31))
        assert w['MTD'][0][0] <= w['MTD'][0][1]

    def test_new_year_monday_reports_the_prior_year_in_full(self):
        # 2027-01-04 is the first Monday of 2027; through = Sun 2027-01-03
        # keeps YTD in 2027. But a Monday that IS 1 January clamps back into
        # the previous year, which is the sensible read for a report about a
        # week that ended in it.
        w = period_windows(date(2024, 1, 1), 'monday', date(2023, 12, 31))
        assert w['YTD'][0] == (date(2023, 1, 1), date(2023, 12, 31))
        assert w['YTD'][1] == (date(2022, 1, 1), date(2022, 12, 31))

    def test_leap_day_through_folds_on_the_prior_year_side(self):
        w = period_windows(date(2024, 3, 1), 'monday', date(2024, 2, 29))
        assert w['MTD'][0] == (date(2024, 2, 1), date(2024, 2, 29))
        assert w['MTD'][1] == (date(2023, 2, 1), date(2023, 2, 28))


class TestFetchSpan:
    def test_covers_every_window(self):
        w = period_windows(date(2026, 9, 14), 'monday', date(2026, 9, 13))
        start, end = fetch_span(w)
        for cur, prior in w.values():
            for win in (cur, prior):
                assert start <= win[0] and win[1] <= end

    def test_earliest_start_can_be_the_prior_WEEK_not_the_prior_year_ytd(self):
        # Early January: the YTD prior window starts 1 Jan of last year, but the
        # WOW comparison week reaches back further still. Assuming YTD bounds
        # the fetch would read those days as zero.
        w = period_windows(date(2025, 1, 6), 'monday', date(2025, 1, 5))
        start, _end = fetch_span(w)
        assert start == min(s for cur_pri in w.values() for s, _ in cur_pri)
        assert start <= w['WOW'][1][0]

    def test_end_is_the_latest_window_end(self):
        w = period_windows(date(2026, 9, 14), 'monday', date(2026, 9, 13))
        _start, end = fetch_span(w)
        assert end == date(2026, 9, 13)


class TestShiftYear:
    def test_leap_day_folds_back_to_feb_28(self):
        assert shift_year(date(2024, 2, 29)) == date(2023, 2, 28)


class TestMonthEndStraddle:
    """A week that spans a month boundary reports the month that ENDED in it.

    Reported from production on week 36 (31 Aug - 6 Sep), whose MTD sheet showed
    six days of September. Without this rule a complete month is never reported
    at all: the last week ending in August stops on the 30th and the next week
    jumps to September, so August is only ever seen a day short. That happens in
    every month whose last day is not a Sunday.
    """

    def _mtd(self, as_of):
        wk = last_complete_week(as_of)
        return period_windows(as_of, 'monday', wk[1])['MTD']

    def test_week_36_reports_the_whole_of_august(self):
        cur, prior = self._mtd(date(2026, 9, 7))       # week 31 Aug - 6 Sep
        assert cur == (date(2026, 8, 1), date(2026, 8, 31))
        assert prior == (date(2025, 8, 1), date(2025, 8, 31))

    def test_it_does_NOT_show_the_new_months_first_days(self):
        cur, _ = self._mtd(date(2026, 9, 7))
        assert cur[1].month == 8

    def test_a_week_inside_one_month_is_unchanged(self):
        cur, prior = self._mtd(date(2026, 9, 14))      # week 7-13 Sep
        assert cur == (date(2026, 9, 1), date(2026, 9, 13))
        assert prior == (date(2025, 9, 1), date(2025, 9, 13))

    def test_the_week_before_the_boundary_is_unchanged(self):
        cur, _ = self._mtd(date(2026, 8, 31))          # week 24-30 Aug
        assert cur == (date(2026, 8, 1), date(2026, 8, 30))

    def test_a_week_spanning_new_year_reports_the_whole_of_december(self):
        cur, prior = self._mtd(date(2026, 1, 5))       # week 29 Dec - 4 Jan
        assert cur == (date(2025, 12, 1), date(2025, 12, 31))
        assert prior == (date(2024, 12, 1), date(2024, 12, 31))

    def test_february_is_reported_to_its_real_last_day(self):
        # 2026: Feb ends on the 28th, inside the week 23 Feb - 1 Mar.
        cur, _ = self._mtd(date(2026, 3, 2))
        assert cur == (date(2026, 2, 1), date(2026, 2, 28))

    def test_a_leap_february_ends_on_the_29th(self):
        # 2028 is a leap year; Feb 29 falls in the week 28 Feb - 5 Mar.
        cur, prior = self._mtd(date(2028, 3, 6))
        assert cur == (date(2028, 2, 1), date(2028, 2, 29))
        # The prior-year side folds 29 Feb back to the 28th.
        assert prior == (date(2027, 2, 1), date(2027, 2, 28))

    def test_a_month_ending_on_a_sunday_needs_no_special_case(self):
        # 31 May 2026 is a Sunday, so that week already ends the month.
        cur, _ = self._mtd(date(2026, 6, 1))
        assert cur == (date(2026, 5, 1), date(2026, 5, 31))

    def test_YTD_is_untouched_by_the_straddle(self):
        w = period_windows(date(2026, 9, 7), 'monday', date(2026, 9, 6))
        assert w['YTD'][0] == (date(2026, 1, 1), date(2026, 9, 6))

    def test_the_week_window_is_untouched_by_the_straddle(self):
        w = period_windows(date(2026, 9, 7), 'monday', date(2026, 9, 6))
        assert w['WOW'][0] == (date(2026, 8, 31), date(2026, 9, 6))

    def test_fetch_span_still_covers_every_window(self):
        w = period_windows(date(2026, 9, 7), 'monday', date(2026, 9, 6))
        start, end = fetch_span(w)
        for cur, prior in w.values():
            for win in (cur, prior):
                assert start <= win[0] and win[1] <= end
