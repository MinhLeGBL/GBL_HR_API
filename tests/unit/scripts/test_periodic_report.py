"""Unit tests for scripts/reports/generate_periodic_report.py.

Covers the pure logic only — period maths, department merging, metric
derivation and window aggregation. The Oracle queries are not exercised here;
they are validated by reconciling the generated workbook against a single-shot
query over the same range (see the script's module docstring).
"""
import importlib.util
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

# The report generators are standalone scripts, not an importable package, so
# load this one by path.
_SCRIPT = (Path(__file__).resolve().parents[3]
           / 'scripts' / 'reports' / 'generate_periodic_report.py')
_spec = importlib.util.spec_from_file_location('generate_periodic_report', _SCRIPT)
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)


class TestShiftYear:
    def test_ordinary_date_keeps_month_and_day(self):
        assert pr.shift_year(date(2026, 9, 9)) == date(2025, 9, 9)

    def test_leap_day_folds_back_to_feb_28(self):
        # 2024 is a leap year, 2023 is not.
        assert pr.shift_year(date(2024, 2, 29)) == date(2023, 2, 28)

    def test_leap_day_to_leap_year_is_exact(self):
        assert pr.shift_year(date(2024, 2, 29), years=4) == date(2020, 2, 29)


class TestPeriodWindows:
    def test_week_window_is_the_last_COMPLETE_week_not_week_to_date(self):
        # 2026-09-09 is a Wednesday in week 37. The report covers week 36:
        # Mon 08-31 to Sun 09-06. The partial current week is excluded.
        w = pr.period_windows(date(2026, 9, 9), 'monday')
        assert w['WOW'][0] == (date(2026, 8, 31), date(2026, 9, 6))

    def test_sunday_week_start_shifts_the_whole_window(self):
        # Sunday-based weeks: the last complete one ends Sat 09-05.
        w = pr.period_windows(date(2026, 9, 9), 'sunday')
        assert w['WOW'][0] == (date(2026, 8, 30), date(2026, 9, 5))

    def test_as_of_on_the_first_day_of_a_week_still_reports_the_week_before(self):
        w = pr.period_windows(date(2026, 9, 7), 'monday')   # a Monday
        assert w['WOW'][0] == (date(2026, 8, 31), date(2026, 9, 6))

    def test_as_of_on_the_last_day_of_a_week_excludes_that_week(self):
        # Sunday 2026-09-13 closes week 37, but the current week is always
        # excluded, so the report is still week 36.
        w = pr.period_windows(date(2026, 9, 13), 'monday')
        assert w['WOW'][0] == (date(2026, 8, 31), date(2026, 9, 6))

    @pytest.mark.parametrize('as_of', [
        date(2026, 9, 7), date(2026, 9, 9), date(2026, 9, 13),
        date(2026, 1, 1), date(2026, 12, 31), date(2024, 2, 29),
    ])
    def test_week_window_is_always_exactly_seven_days(self, as_of):
        (cf, ct), (pf, pt) = pr.period_windows(as_of)['WOW']
        assert (ct - cf).days == 6
        assert (pt - pf).days == 6

    @pytest.mark.parametrize('as_of', [
        date(2026, 9, 7), date(2026, 9, 9), date(2026, 9, 13), date(2026, 1, 1),
    ])
    def test_week_window_never_reaches_as_of(self, as_of):
        (_cf, ct), _prior = pr.period_windows(as_of)['WOW']
        assert ct < as_of

    def test_month_and_year_to_date(self):
        w = pr.period_windows(date(2026, 9, 9))
        assert w['MTD'][0] == (date(2026, 9, 1), date(2026, 9, 9))
        assert w['YTD'][0] == (date(2026, 1, 1), date(2026, 9, 9))

    def test_mtd_and_ytd_prior_windows_are_calendar_date_aligned(self):
        w = pr.period_windows(date(2026, 9, 9))
        assert w['MTD'][1] == (date(2025, 9, 1), date(2025, 9, 9))
        assert w['YTD'][1] == (date(2025, 1, 1), date(2025, 9, 9))

    def test_week_comparison_is_the_PREVIOUS_week_not_last_year(self):
        w = pr.period_windows(date(2026, 9, 9))
        # Current is 2026-W36 (Mon 08-31..Sun 09-06); the comparison is W35.
        assert w['WOW'][1] == (date(2026, 8, 24), date(2026, 8, 30))
        assert w['WOW'][1][0].year == 2026, 'must NOT reach into last year' 

    def test_prior_week_preserves_weekdays(self):
        (cur_from, cur_to), (pri_from, pri_to) = pr.period_windows(date(2026, 9, 9))['WOW']
        assert cur_from.weekday() == pri_from.weekday()
        assert cur_to.weekday() == pri_to.weekday()

    def test_prior_week_is_exactly_one_week_number_earlier(self):
        (cur_from, _), (pri_from, _) = pr.period_windows(date(2026, 9, 9))['WOW']
        assert pri_from.isocalendar().week == cur_from.isocalendar().week - 1

    def test_both_wtd_sides_span_the_same_number_of_days(self):
        (cf, ct), (pf, pt) = pr.period_windows(date(2026, 9, 9))['WOW']
        assert (ct - cf) == (pt - pf)


class TestWeekLabel:
    def test_week_label(self):
        assert pr.week_label(date(2026, 9, 7)) == 'Week 37 2026'

    def test_week_label_uses_the_iso_year_not_the_calendar_year(self):
        # 2025-12-29 is a Monday already inside ISO week 1 of 2026.
        assert pr.week_label(date(2025, 12, 29)) == 'Week 1 2026'


class TestPreviousWeekWindow:
    def test_shifts_back_exactly_seven_days(self):
        start, end = pr.previous_week_window(date(2026, 9, 7), date(2026, 9, 13))
        assert (start, end) == (date(2026, 8, 31), date(2026, 9, 6))

    def test_weekdays_are_preserved(self):
        start, end = pr.previous_week_window(date(2026, 9, 7), date(2026, 9, 13))
        assert start.weekday() == date(2026, 9, 7).weekday()
        assert end.weekday() == date(2026, 9, 13).weekday()

    def test_the_two_weeks_are_adjacent_and_do_not_overlap(self):
        cur = (date(2026, 9, 7), date(2026, 9, 13))
        start, end = pr.previous_week_window(*cur)
        assert end + timedelta(days=1) == cur[0]

    def test_crosses_a_year_boundary_without_special_handling(self):
        start, end = pr.previous_week_window(date(2026, 1, 5), date(2026, 1, 11))
        assert (start, end) == (date(2025, 12, 29), date(2026, 1, 4))


class TestMergeDepartment:
    @pytest.mark.parametrize('women,men,expected', [
        ('WRTW', 'MRTW', 'RTW'),
        ('WBAG', 'MBAG', 'BAG'),
        ('WSHO', 'MSHO', 'SHO'),
        ('WACC', 'MACC', 'ACC'),
        ('WJEW', 'MJEW', 'JEW'),
    ])
    def test_women_and_men_collapse_to_one_category(self, women, men, expected):
        assert pr.merge_department(women) == expected
        assert pr.merge_department(men) == expected

    @pytest.mark.parametrize('code', ['KACC', 'KRTW', 'COSM', 'HOME'])
    def test_other_departments_pass_through(self, code):
        assert pr.merge_department(code) == code

    def test_unmapped_code_starting_with_m_is_not_mangled(self):
        # The merge is an explicit table, not "strip a leading W/M".
        assert pr.merge_department('MISC') == 'MISC'
        assert pr.merge_department('WHSE') == 'WHSE'

    @pytest.mark.parametrize('raw', [None, '', '   '])
    def test_missing_department_buckets_as_unknown(self, raw):
        assert pr.merge_department(raw) == pr.UNKNOWN_DEPT

    def test_case_and_whitespace_are_normalised(self):
        assert pr.merge_department('  wrtw  ') == 'RTW'


class TestMetrics:
    def test_derived_metrics(self):
        acc = {'sale_net': D(1000), 'return_net': D(100), 'sale_gross': D(1250),
               'qty_sold': D(10), 'qty_returned': D(1), 'bills': 4}
        m = pr._metrics(acc)
        assert m['total_sales'] == 900.0          # returns netted
        assert m['qty_sold'] == 10.0              # returns NOT deducted
        assert m['avg_unit_price'] == 100.0       # sale revenue / qty
        assert m['avg_transaction_value'] == 250.0
        assert m['total_discount_value'] == 250.0
        assert m['avg_discount_pct'] == pytest.approx(20.0)
        assert m['returns_value'] == 100.0

    def test_zero_denominators_yield_none_not_zero(self):
        m = pr._metrics(pr._blank())
        assert m['avg_unit_price'] is None
        assert m['avg_transaction_value'] is None
        assert m['avg_discount_pct'] is None
        # Additive metrics are a real zero.
        assert m['total_sales'] == 0
        assert m['qty_sold'] == 0
        assert m['bills'] == 0

    def test_zero_metrics_constant_matches_a_blank_accumulator(self):
        assert pr.ZERO_METRICS == pr._metrics(pr._blank())


def _dept_row(day, store, dept, **kw):
    """A fetched row. Money and quantities arrive from Oracle as Decimal."""
    row = {'day': day, 'store_code': store, 'store_name': f'{store} NAME',
           'department': dept, 'sale_net': D(0), 'return_net': D(0),
           'sale_gross': D(0), 'qty_sold': D(0), 'qty_returned': D(0),
           'dept_bills': 0}
    row.update({k: D(v) if k in pr._COMPONENTS else v for k, v in kw.items()})
    return row


class TestAggregate:
    def _rows(self):
        return [
            _dept_row(date(2026, 9, 7), 'RWT', 'RTW',
                      sale_net=100.0, sale_gross=125.0, qty_sold=2.0, dept_bills=1),
            _dept_row(date(2026, 9, 8), 'RWT', 'BAG',
                      sale_net=50.0, sale_gross=50.0, qty_sold=1.0, dept_bills=1),
            # Outside the window under test.
            _dept_row(date(2026, 9, 20), 'RWT', 'RTW',
                      sale_net=999.0, sale_gross=999.0, qty_sold=9.0, dept_bills=1),
        ]

    def test_window_bounds_are_inclusive_and_exclude_outside_rows(self):
        dept, store, grand, _ = pr.aggregate(
            self._rows(), [], (date(2026, 9, 7), date(2026, 9, 8)))
        assert grand['total_sales'] == 150.0
        assert set(dept) == {('RWT', 'RTW'), ('RWT', 'BAG')}
        assert store['RWT']['total_sales'] == 150.0

    def test_department_rows_sum_to_the_store_total(self):
        dept, store, _, _ = pr.aggregate(
            self._rows(), [], (date(2026, 9, 7), date(2026, 9, 8)))
        summed = sum(v['total_sales'] for (s, _d), v in dept.items() if s == 'RWT')
        assert summed == store['RWT']['total_sales']

    def test_store_bills_come_from_the_document_grain_not_the_department_grain(self):
        # Two departments each report 1 bill, but they are the SAME bill: the
        # document-grain row is authoritative for the store total.
        store_rows = [{'day': date(2026, 9, 7), 'store_code': 'RWT', 'bills': 1},
                      {'day': date(2026, 9, 8), 'store_code': 'RWT', 'bills': 1}]
        dept, store, grand, _ = pr.aggregate(
            self._rows(), store_rows, (date(2026, 9, 7), date(2026, 9, 8)))
        assert sum(v['bills'] for (s, _d), v in dept.items() if s == 'RWT') == 2
        assert store['RWT']['bills'] == 2
        assert grand['bills'] == 2

    def test_store_rows_outside_the_window_are_ignored(self):
        store_rows = [{'day': date(2026, 9, 20), 'store_code': 'RWT', 'bills': 7}]
        _dept, store, grand, _ = pr.aggregate(
            self._rows(), store_rows, (date(2026, 9, 7), date(2026, 9, 8)))
        assert store['RWT']['bills'] == 0
        assert grand['bills'] == 0

    def test_empty_window_produces_empty_aggregates(self):
        dept, store, grand, _ = pr.aggregate(
            self._rows(), [], (date(2026, 1, 1), date(2026, 1, 2)))
        assert dept == {} and store == {}
        assert grand['total_sales'] == 0

    def test_store_names_are_captured(self):
        _d, _s, _g, names = pr.aggregate(
            self._rows(), [], (date(2026, 9, 7), date(2026, 9, 8)))
        assert names == {'RWT': 'RWT NAME'}


class TestDelta:
    def test_plain_difference_and_percentage(self):
        assert pr._delta(D(150), D(100)) == (D(50), pytest.approx(50))

    def test_zero_prior_leaves_the_percentage_undefined(self):
        diff, pct = pr._delta(D(150), D(0))
        assert diff == D(150)
        assert pct is None

    def test_none_on_either_side_propagates(self):
        assert pr._delta(None, D(100)) == (None, None)
        assert pr._delta(D(100), None) == (None, None)

    def test_negative_prior_uses_magnitude_so_the_sign_tracks_the_change(self):
        diff, pct = pr._delta(D(-50), D(-100))
        assert diff == D(50)
        assert pct == pytest.approx(50)

    def test_decimal_inputs_never_touch_float_arithmetic(self):
        # Decimal * float raises TypeError; the percentage must stay exact.
        diff, pct = pr._delta(D('159745427865.4321'), D('145327154784.1234'))
        assert isinstance(diff, D) and isinstance(pct, D)


class TestFetchWindowCoverage:
    """The single fetch must span every window, including the prior WTD.

    Regression guard: the fetch start was originally taken from the YTD prior
    window, but an ISO-week-aligned prior WTD can begin earlier -- run on
    1 January and prior week 1 starts in the previous December. Those days would
    have read as zero instead of being fetched.
    """

    @pytest.mark.parametrize('as_of', [
        date(2026, 1, 1), date(2026, 1, 2), date(2027, 1, 1),
        date(2026, 9, 9), date(2026, 6, 30), date(2026, 12, 31),
    ])
    def test_earliest_window_start_is_covered(self, as_of):
        windows = pr.period_windows(as_of)
        fetch_from = min(start for w in windows.values() for start, _end in w)
        for label, w in windows.items():
            for start, end in w:
                assert start >= fetch_from, f'{label} starts before the fetch window'
                assert end <= as_of, f'{label} ends after as-of'

    def test_early_january_still_reaches_back_to_the_prior_year(self):
        # The earliest start is NOT reliably YTD's prior-year window. In early
        # January, WTD's prior window — the same ISO week a year back — begins
        # in the December before it, earlier than 1 January. Assuming YTD
        # bounds the fetch would read those days as zero.
        windows = pr.period_windows(date(2026, 1, 1))
        fetch_from = min(start for w in windows.values() for start, _end in w)
        assert fetch_from == windows['WTD'][1][0]
        assert fetch_from < windows['YTD'][1][0]

    def test_the_fetch_span_covers_every_window(self):
        # The property that actually matters, whichever window happens to be
        # the extreme.
        windows = pr.period_windows(date(2026, 1, 1))
        start, end = pr.fetch_span(windows)
        for cur, prior in windows.values():
            for win in (cur, prior):
                assert start <= win[0] and win[1] <= end


class TestMetricColumns:
    """Display contract: money in millions, counts plain, percent as percent."""

    def test_money_columns_use_a_millions_format(self):
        money = [(lbl, key, unit) for lbl, key, unit in pr.METRIC_COLUMNS
                 if unit in ('money', 'money1')]
        assert {key for _l, key, _u in money} == {
            'total_sales', 'avg_unit_price', 'avg_transaction_value',
            'returns_value',
        }
        for lbl, _key, unit in money:
            assert lbl.endswith('(VND m)'), f'{lbl} must declare its unit'
            assert pr._UNIT_FORMAT[unit].endswith(',,')

    def test_per_unit_averages_keep_one_decimal(self):
        # Whole millions renders a 12,286,107 average unit price as "12" and
        # hides a move to 12.4m; these two columns need the extra digit.
        by_key = {key: unit for _l, key, unit in pr.METRIC_COLUMNS}
        assert by_key['avg_unit_price'] == 'money1'
        assert by_key['avg_transaction_value'] == 'money1'
        assert pr.MONEY_1DP_FORMAT == '#,##0.0,,'

    def test_aggregate_totals_stay_whole_millions(self):
        by_key = {key: unit for _l, key, unit in pr.METRIC_COLUMNS}
        for key in ('total_sales', 'returns_value'):
            assert by_key[key] == 'money'

    def test_discount_amount_is_computed_but_not_displayed(self):
        # The rate carries the same information in a comparable unit, so the
        # amount is dropped from the sheet -- but it must stay in the metric
        # set, since avg_discount_pct is derived from it.
        assert 'total_discount_value' not in {k for _l, k, _u in pr.METRIC_COLUMNS}
        assert 'total_discount_value' in pr.ZERO_METRICS
        assert 'avg_discount_pct' in {k for _l, k, _u in pr.METRIC_COLUMNS}

    def test_eight_metrics_per_block(self):
        # Seven until 0.15.0, when qty_returned joined returns_value: the value
        # of what came back and how many items did are different problems.
        assert len(pr.METRIC_COLUMNS) == 8

    def test_millions_format_scales_by_two_thousands(self):
        # Two trailing commas in an Excel format divide the DISPLAYED value by
        # 1e6; the stored value is untouched.
        assert pr.MONEY_FORMAT.endswith(',,')
        assert pr.MONEY_FORMAT == '#,##0,,'

    def test_counts_are_not_scaled(self):
        counts = [key for _l, key, unit in pr.METRIC_COLUMNS if unit == 'count']
        assert counts == ['qty_sold', 'bills', 'qty_returned']
        assert ',,' not in pr.COUNT_FORMAT

    def test_percentage_metric_is_the_only_pct_unit(self):
        pct = [key for _l, key, unit in pr.METRIC_COLUMNS if unit == 'pct']
        assert pct == ['avg_discount_pct']

    def test_every_metric_key_is_produced_by_metrics(self):
        produced = set(pr.ZERO_METRICS)
        for _lbl, key, _unit in pr.METRIC_COLUMNS:
            assert key in produced, f'{key} is not a metric _metrics() returns'

    def test_every_unit_has_a_format(self):
        for _lbl, _key, unit in pr.METRIC_COLUMNS:
            assert unit in pr._UNIT_FORMAT


class TestDeltaPolarity:
    """Which direction is 'bad' per metric, for the red colouring.

    Two metrics are inverted: a FALLING discount rate and FALLING returns are
    wins. Colouring purely on the sign of the delta would paint both of those
    red, which is the opposite of the truth.
    """

    def test_only_discount_and_returns_are_inverted(self):
        # Both return metrics invert: more returned stock is a loss whether it
        # is measured in đồng or in items.
        assert pr.LOWER_IS_BETTER == {'avg_discount_pct', 'returns_value',
                                      'qty_returned'}

    def test_every_inverted_metric_is_actually_displayed(self):
        shown = {k for _l, k, _u in pr.METRIC_COLUMNS}
        assert pr.LOWER_IS_BETTER <= shown

    @pytest.mark.parametrize('key,delta,expect_bad', [
        # Higher is better -> a fall is bad.
        ('total_sales',           -1, True),
        ('total_sales',           +1, False),
        ('qty_sold',              -1, True),
        ('bills',                 -1, True),
        ('avg_unit_price',        -1, True),
        ('avg_transaction_value', -1, True),
        # Inverted -> a RISE is bad.
        ('avg_discount_pct',      +1, True),
        ('avg_discount_pct',      -1, False),
        ('returns_value',         +1, True),
        ('returns_value',         -1, False),
    ])
    def test_bad_direction_per_metric(self, key, delta, expect_bad):
        bad = (delta > 0) if key in pr.LOWER_IS_BETTER else (delta < 0)
        assert bad is expect_bad

    def test_no_change_is_never_flagged(self):
        for key in {k for _l, k, _u in pr.METRIC_COLUMNS}:
            delta = 0
            bad = delta != 0 and (
                (delta > 0) if key in pr.LOWER_IS_BETTER else (delta < 0))
            assert bad is False
