"""Unit tests for app.modules.periodic_report.excel.

Focused on the sheet's column outline: the prior-period block is grouped and
opens collapsed. Everything here goes through a real save/load round trip
rather than inspecting the in-memory worksheet, because the outline only
matters if it survives serialisation into the file Excel opens.
"""
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from app.modules.periodic_report.excel import (METRIC_COLUMNS, SHEET_NAMES,
                                               build_workbook_bytes)
from app.modules.periodic_report.period import period_windows

WIDTH = len(METRIC_COLUMNS)
FIRST_CURRENT = 3
FIRST_PRIOR = FIRST_CURRENT + WIDTH
FIRST_DELTA = FIRST_CURRENT + 2 * WIDTH


def _dept_row(day, store, dept):
    return {'day': day, 'store_code': store, 'store_name': f'Store {store}',
            'department': dept, 'sale_net': Decimal('5000000'),
            'return_net': Decimal('100000'), 'sale_gross': Decimal('6000000'),
            'qty_sold': Decimal('3'), 'qty_returned': Decimal('1'),
            'dept_bills': 2}


@pytest.fixture(scope='module')
def workbook():
    """One workbook covering both the current and prior-year windows."""
    days = ([date(2026, 9, d) for d in range(1, 14)] +
            [date(2025, 9, d) for d in range(1, 14)])
    dept_rows = [_dept_row(d, s, dep)
                 for d in days for s in ('S1', 'S2') for dep in ('Alpha', 'Beta')]
    store_rows = [{'day': d, 'store_code': s, 'bills': 5}
                  for d in days for s in ('S1', 'S2')]
    windows = period_windows(date(2026, 9, 14), 'monday', date(2026, 9, 13))
    return load_workbook(BytesIO(
        build_workbook_bytes(windows, dept_rows, store_rows)))


def _cols(first):
    return [get_column_letter(first + i) for i in range(WIDTH)]


@pytest.fixture(params=sorted(SHEET_NAMES.values()))
def sheet(request, workbook):
    """Every period sheet, so a rule cannot hold on WOW alone."""
    return workbook[request.param]


class TestPriorBlockCollapsed:
    def test_prior_columns_are_grouped_at_outline_level_1(self, sheet):
        levels = {sheet.column_dimensions[c].outlineLevel for c in _cols(FIRST_PRIOR)}
        assert levels == {1}

    def test_prior_columns_open_hidden(self, sheet):
        # "Collapsed by default" means the file opens with them already hidden,
        # not merely groupable.
        assert all(sheet.column_dimensions[c].hidden for c in _cols(FIRST_PRIOR))

    def test_the_summary_column_carries_the_collapsed_flag(self, sheet):
        # Excel draws the +/- control on the column AFTER the group and marks
        # that one collapsed. Without this the columns are hidden but the
        # button does not render as expandable.
        summary = get_column_letter(FIRST_DELTA)
        assert sheet.column_dimensions[summary].collapsed is True

    def test_summary_is_to_the_right(self, sheet):
        # summaryRight is openpyxl's default, but the button landing on the
        # delta block is load-bearing rather than incidental.
        assert sheet.sheet_properties.outlinePr.summaryRight is True

    def test_current_and_delta_blocks_stay_visible(self, sheet):
        for first in (FIRST_CURRENT, FIRST_DELTA):
            assert not any(sheet.column_dimensions[c].hidden for c in _cols(first))

    def test_store_and_department_columns_stay_visible(self, sheet):
        assert not sheet.column_dimensions['A'].hidden
        assert not sheet.column_dimensions['B'].hidden


class TestPriorWidthsSurviveGrouping:
    """`column_dimensions.group()` would have destroyed these.

    It deletes the per-column dimensions across the range and leaves one
    spanning dimension keyed to the first column, so the rest fall back to
    Excel's default 13 — measured: group('J','P') over [10,8,7,9,9,8,9] gives
    [10,13,13,13,13,13,13]. The block would then be wider on expand than the
    layout was sized for. The outline attributes are therefore set per column.
    """

    def test_each_prior_column_keeps_its_own_width(self, sheet):
        prior = [sheet.column_dimensions[c].width for c in _cols(FIRST_PRIOR)]
        current = [sheet.column_dimensions[c].width for c in _cols(FIRST_CURRENT)]
        assert prior == current

    def test_widths_are_not_all_equal(self, sheet):
        # The regression this guards against collapses them to one value, so a
        # per-metric spread is the signal that it has not happened.
        assert len({sheet.column_dimensions[c].width
                    for c in _cols(FIRST_PRIOR)}) > 1

    def test_no_prior_column_fell_back_to_the_excel_default(self, sheet):
        assert all(sheet.column_dimensions[c].width != 13
                   for c in _cols(FIRST_PRIOR))


class TestRowOutlineStillWorks:
    """The column outline shares `outlinePr` with the existing row outline."""

    def test_summary_below_is_preserved(self, sheet):
        # Department rows sit ABOVE their store TOTAL, so this must stay True
        # or the row +/- control detaches from the TOTAL row.
        assert sheet.sheet_properties.outlinePr.summaryBelow is True

    def test_department_rows_are_still_grouped_and_hidden(self, sheet):
        grouped = [r for r, d in sheet.row_dimensions.items()
                   if d.outlineLevel == 1]
        assert grouped, 'department rows lost their outline level'
        assert all(sheet.row_dimensions[r].hidden for r in grouped)

    def test_some_total_row_is_marked_collapsed(self, sheet):
        assert any(d.collapsed for d in sheet.row_dimensions.values())


class TestRawDataSheetIsUntouched:
    def test_raw_sheet_has_no_hidden_columns(self, workbook):
        ws = workbook['Raw Data']
        assert not any(d.hidden for d in ws.column_dimensions.values())
