"""Unit tests for the P&L importer.

Built on synthetic worksheets rather than the real exports: the real files live
outside the repo and are not on CI, and a test that silently skips when a file
is missing is worse than no test. The reconciliation AGAINST those real files
is a separate exercise (see the module CHANGELOG) — what is pinned here is the
parsing behaviour that reconciliation depends on.
"""
from decimal import Decimal

import pytest
from openpyxl import Workbook

from app.modules.pnl.importer import (LAYOUT_BULK, LAYOUT_HISTORY,
                                      LAYOUT_MONTHLY, ImportProblem,
                                      detect_layout, parse,
                                      period_from_sheet_name)

SELLING = '8. Chi phí bán hàng'
GA = '9. Chi phí quản lý doanh nghiệp'
STORES = ('Total', 'HQ', 'RHN')


def monthly_sheet(title='T07.2026', rows=None, codes=True):
    """A FAST monthly export: codes on row 1, `Chỉ tiêu` at A2, data from B."""
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.cell(2, 1, 'Chỉ tiêu')
    code_n = 0
    for i, store in enumerate(STORES):
        col = 2 + i
        if store != 'Total':
            if codes:
                ws.cell(1, col, f'RW00{code_n}')
            code_n += 1
        ws.cell(2, col, store)
    # Statement order matters: a numbered row is itself a section heading, so
    # the revenue lines come first and `8. Chi phí bán hàng` opens the block
    # its detail rows belong to — exactly as FAST emits it.
    rows = rows if rows is not None else [
        ('1. Doanh thu bán hàng và cung cấp dịch vụ', (300, 100, 200)),
        (SELLING, None),
        ('- Lương cơ bản', (30, 10, 20)),
    ]
    r = 3
    for label, values in rows:
        ws.cell(r, 1, label)
        if values:
            for i, v in enumerate(values):
                ws.cell(r, 2 + i, v)
        r += 1
    return ws


def bulk_sheet(months=((2026, 'June'), (2026, 'July'))):
    """Year/Month/Code/Name stacked above `Chỉ tiêu` at A4."""
    wb = Workbook()
    ws = wb.active
    ws.cell(4, 1, 'Chỉ tiêu')
    col = 2
    for year, month in months:
        for i, store in enumerate(STORES):
            ws.cell(1, col, year)
            ws.cell(2, col, month)
            if store != 'Total':
                ws.cell(3, col, f'RW00{i}')
            ws.cell(4, col, store)
            col += 1
    ws.cell(5, 1, '1. Doanh thu bán hàng và cung cấp dịch vụ')
    for c in range(2, col):
        ws.cell(5, c, 100)
    return ws


class TestLayoutDetection:
    def test_monthly_is_recognised(self):
        layout, header, label_col, year_row, month_row = detect_layout(monthly_sheet())
        assert layout == LAYOUT_MONTHLY
        assert (header, label_col) == (2, 1)
        assert year_row is None and month_row is None

    def test_bulk_is_recognised(self):
        layout, header, label_col, year_row, month_row = detect_layout(bulk_sheet())
        assert layout == LAYOUT_BULK
        assert (header, label_col) == (4, 1)
        # Identified by CONTENT: the year row is the one full of 4-digit
        # numbers, not simply "the row two above the header".
        assert (year_row, month_row) == (1, 2)

    def test_history_layout_still_reads(self):
        # The original `Inputs Fast` workbook is the accuracy cross-check and
        # the only source of 2025 history, so it must keep parsing.
        wb = Workbook()
        ws = wb.active
        ws.cell(3, 2, 'Chỉ tiêu')
        for i, store in enumerate(STORES):
            ws.cell(1, 3 + i, 2026)
            ws.cell(2, 3 + i, 'June')
            ws.cell(3, 3 + i, store)
        layout, header, label_col, _y, _m = detect_layout(ws)
        assert layout == LAYOUT_HISTORY
        assert (header, label_col) == (3, 2)

    def test_an_unrecognisable_sheet_is_reported_not_guessed(self):
        # Guessing would read account labels as amounts.
        wb = Workbook()
        with pytest.raises(ImportProblem) as e:
            detect_layout(wb.active)
        assert 'Chỉ tiêu' in e.value.problems[0]

    def test_bulk_without_a_store_code_row_still_finds_year_and_month(self):
        # Position-based parsing would read the month row as store codes here.
        wb = Workbook()
        ws = wb.active
        ws.cell(3, 1, 'Chỉ tiêu')
        for i, store in enumerate(STORES):
            ws.cell(1, 2 + i, 2026)
            ws.cell(2, 2 + i, 'June')
            ws.cell(3, 2 + i, store)
        layout, _h, _lc, year_row, month_row = detect_layout(ws)
        assert layout == LAYOUT_BULK
        assert (year_row, month_row) == (1, 2)


class TestPeriodFromSheetName:
    @pytest.mark.parametrize('name,expected', [
        ('T07.2026', (2026, 7)), ('T7.2026', (2026, 7)),
        ('t12.2025', (2025, 12)), ('T07-2026', (2026, 7)),
    ])
    def test_recognised_forms(self, name, expected):
        assert period_from_sheet_name(name) == expected

    @pytest.mark.parametrize('name', ['Sheet1', 'T13.2026', 'July 2026', '', None])
    def test_rejected_forms(self, name):
        assert period_from_sheet_name(name) is None


class TestParsing:
    def test_monthly_takes_its_period_from_the_sheet_name(self):
        result = parse(monthly_sheet('T07.2026'))
        assert result['months'] == [(2026, 7)]

    def test_an_unnamed_monthly_sheet_is_refused_with_a_way_out(self):
        with pytest.raises(ImportProblem) as e:
            parse(monthly_sheet('Sheet1'))
        assert 'T07.2026' in e.value.problems[0]

    def test_an_explicit_period_overrides_the_sheet_name(self):
        result = parse(monthly_sheet('Sheet1'), year=2026, month='August')
        assert result['months'] == [(2026, 8)]

    def test_bulk_reads_every_month_in_the_grid(self):
        result = parse(bulk_sheet())
        assert result['months'] == [(2026, 6), (2026, 7)]

    def test_store_codes_are_captured(self):
        assert parse(monthly_sheet())['store_codes'] == {'HQ': 'RW000', 'RHN': 'RW001'}

    def test_an_unknown_account_is_rejected_by_name(self):
        ws = monthly_sheet(rows=[(SELLING, None), ('- Chi phí tưởng tượng', (1, 1, 0))])
        with pytest.raises(ImportProblem) as e:
            parse(ws)
        assert 'Chi phí tưởng tượng' in e.value.problems[0]
        assert 'fast_accounts' in e.value.problems[0]

    def test_every_problem_is_reported_at_once(self):
        # One round trip should tell the uploader everything to fix.
        ws = monthly_sheet(rows=[(SELLING, None),
                                 ('- Sai một', (1, 1, 0)),
                                 ('- Sai hai', (1, 1, 0))])
        with pytest.raises(ImportProblem) as e:
            parse(ws)
        assert len(e.value.problems) == 2


class TestNothingDerivableIsCopied:
    """The rule the July 2026 export proved necessary."""

    def test_the_total_column_never_becomes_a_fact(self):
        result = parse(monthly_sheet())
        assert 'Total' not in {f['entity'] for f in result['facts']}
        assert 'Total' not in result['entities']

    def test_fast_subtotal_rows_are_not_posted(self):
        # '3. Doanh thu thuần' is FAST's own subtotal; posting it would
        # double-count revenue.
        ws = monthly_sheet(rows=[
            ('1. Doanh thu bán hàng và cung cấp dịch vụ', (300, 100, 200)),
            ('2. Các khoản giảm trừ doanh thu', (30, 10, 20)),
            ('3. Doanh thu thuần về bán hàng và cung cấp dịch vụ (10=01- 02)',
             (270, 90, 180)),
        ])
        categories = {f['category'] for f in parse(ws)['facts']}
        assert categories == {'Gross sales', 'Discounts'}

    def test_a_store_missing_from_total_is_still_counted(self):
        # Exactly the July 2026 case: Total omits a trading store. Summing the
        # stores cannot lose one; copying Total does.
        ws = monthly_sheet(rows=[
            ('1. Doanh thu bán hàng và cung cấp dịch vụ', (100, 100, 40)),
        ])
        result = parse(ws)
        posted = sum(f['amount'] for f in result['facts'])
        assert posted == Decimal(140)
        # and the divergence is REPORTED rather than silently inherited
        assert result['total_check']
        assert result['total_check'][0]['difference'] == Decimal(40)

    def test_an_agreeing_total_produces_no_cross_check_noise(self):
        ws = monthly_sheet(rows=[
            ('1. Doanh thu bán hàng và cung cấp dịch vụ', (140, 100, 40)),
        ])
        assert parse(ws)['total_check'] == []


class TestSectionKeying:
    """Twelve FAST labels appear under two sections and mean different things."""

    def test_the_same_label_maps_by_its_section(self):
        ws = monthly_sheet(rows=[
            (SELLING, None),
            ('- Lương cơ bản', (10, 10, 0)),
            (GA, None),
            ('- Lương cơ bản', (7, 7, 0)),
        ])
        by_category = {}
        for f in parse(ws)['facts']:
            by_category[f['category']] = by_category.get(f['category'], 0) + f['amount']
        # Store payroll and G&A payroll land in different statement blocks; a
        # label-only key would merge them and move cost between subtotals.
        assert by_category == {'O/W Wages': Decimal(10), 'G&A payroll': Decimal(7)}


class TestBlockEnd:
    def test_rows_below_the_first_blank_are_ignored(self):
        # The history sheet parks period helper rows under the statement.
        ws = monthly_sheet(rows=[
            (SELLING, None),
            ('- Lương cơ bản', (10, 10, 0)),
            (None, None),
            ('Basis', None),
        ])
        # a None label writes nothing, leaving the row blank
        assert {f['category'] for f in parse(ws)['facts']} == {'O/W Wages'}
