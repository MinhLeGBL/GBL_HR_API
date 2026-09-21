"""Unit tests for the P&L period maths and statement construction.

These are the sums the whole feature rests on: every figure the page shows is
either a category total or arithmetic over them.
"""
from decimal import Decimal

import pytest

from app.modules.pnl.aggregate import category_totals
from app.modules.pnl.period import (BASES, month_index, month_range,
                                    period_label)
from app.modules.pnl.statement import (LEAF_LINES, STATEMENT_ORDER,
                                       build_statement)


def fact(year, month, entity, category, amount):
    return {'year': year, 'month': month, 'entity': entity,
            'category': category, 'fast_account': 'x',
            'amount': Decimal(str(amount))}


class TestMonthRange:
    """Transcribed from the source workbook's Period!B19:B21."""

    @pytest.mark.parametrize('month,basis,expected', [
        (6, 'Month-to-date', (6, 6)),
        (6, 'Quarter-to-date', (4, 6)),      # Q2 starts in April
        (6, 'Half-to-date', (1, 6)),
        (6, 'Year-to-date', (1, 6)),
        (7, 'Half-to-date', (7, 7)),         # second half starts again at July
        (12, 'Quarter-to-date', (10, 12)),
        (1, 'Quarter-to-date', (1, 1)),
    ])
    def test_windows(self, month, basis, expected):
        assert month_range(month, basis) == expected

    def test_the_period_always_ends_on_the_selected_month(self):
        for basis in BASES:
            assert month_range(8, basis)[1] == 8

    def test_month_names_and_indexes_are_interchangeable(self):
        assert month_range('June', 'Half-to-date') == month_range(6, 'Half-to-date')

    def test_an_unknown_basis_is_refused(self):
        with pytest.raises(ValueError, match='unknown basis'):
            month_range(6, 'Decade-to-date')

    @pytest.mark.parametrize('bad', ['Jun', 'Juny', 0, 13, None])
    def test_a_bad_month_is_refused(self, bad):
        with pytest.raises(ValueError):
            month_index(bad)

    def test_label_matches_the_workbook_heading(self):
        assert period_label(2026, 6, 'Half-to-date') == 'June-2026/Half-to-date'


class TestCategoryTotals:
    FACTS = [
        fact(2026, 5, 'RHN', 'Gross sales', 100),
        fact(2026, 6, 'RHN', 'Gross sales', 200),
        fact(2026, 6, 'RWR', 'Gross sales', 50),
        fact(2026, 7, 'RHN', 'Gross sales', 999),   # outside a June window
        fact(2025, 6, 'RHN', 'Gross sales', 70),    # prior year
    ]

    def test_company_sums_every_store(self):
        # There is no stored `Total`; the company figure IS this sum, which is
        # why a store missing from the source's Total column still counts.
        totals = category_totals(self.FACTS, 2026, 6, 'Half-to-date')
        assert totals['Gross sales'] == Decimal(350)

    def test_one_store_can_be_isolated(self):
        totals = category_totals(self.FACTS, 2026, 6, 'Half-to-date', entity='RHN')
        assert totals['Gross sales'] == Decimal(300)

    def test_months_outside_the_window_are_excluded(self):
        totals = category_totals(self.FACTS, 2026, 6, 'Month-to-date')
        assert totals['Gross sales'] == Decimal(250)

    def test_the_year_is_respected(self):
        totals = category_totals(self.FACTS, 2025, 6, 'Half-to-date')
        assert totals['Gross sales'] == Decimal(70)

    def test_an_absent_category_is_simply_absent(self):
        assert 'Tax' not in category_totals(self.FACTS, 2026, 6, 'Year-to-date')


class TestBuildStatement:
    def test_every_line_is_produced(self):
        result = build_statement({})
        assert set(result) == set(STATEMENT_ORDER)

    def test_missing_categories_count_as_zero(self):
        # A month with no `Variable occupancy` reports 0 — it must not vanish
        # from the statement or raise.
        assert build_statement({})['Variable Occupancy'] == Decimal(0)

    def test_the_revenue_chain(self):
        s = build_statement({'Gross sales': Decimal(1000),
                             'Discounts': Decimal(250),
                             'Cost of sales': Decimal(300)})
        assert s['Store net sales'] == Decimal(750)
        assert s['Net sales'] == Decimal(750)
        assert s['Standard profit'] == Decimal(450)

    def test_other_cost_of_sales_is_a_sum_of_three(self):
        s = build_statement({'Obsolescence': Decimal(1),
                             'Logistics': Decimal(2), 'Shipping': Decimal(3),
                             'After sales': Decimal(4)})
        assert s['Logistics & shipping'] == Decimal(5)
        assert s['Other cost of sales'] == Decimal(10)

    def test_financial_profit_loss_is_netted(self):
        # The one line that is a difference, not a sum: a financing cost
        # exceeding income shows as negative.
        s = build_statement({'Financial profit': Decimal(10),
                             'Financial loss': Decimal(30)})
        assert s['Financial profit/loss'] == Decimal(-20)

    def test_tax_is_subtracted_while_the_rest_are_added(self):
        # Tax arrives as a positive magnitude like every other cost, so the
        # sign lives in the formula — the workbook's =C37+C38+C39-C40.
        s = build_statement({'Tax': Decimal(100)})
        assert s['Operating income after tax'] == Decimal(-100)

    def test_the_full_chain_down_to_operating_income(self):
        s = build_statement({
            'Gross sales': Decimal(1000), 'Discounts': Decimal(200),
            'Cost of sales': Decimal(300), 'O/W Wages': Decimal(50),
            'O/W Commissions': Decimal(10), 'Fixed rent': Decimal(100),
            'Store D&A': Decimal(20), 'Other selling': Decimal(5),
            'G&A expenses': Decimal(30), 'G&A payroll': Decimal(40),
        })
        assert s['Gross profit'] == Decimal(500)
        assert s['Store payroll & commissions (total)'] == Decimal(60)
        assert s['Occupancy'] == Decimal(100)
        assert s['Total Store Direct Operating Costs'] == Decimal(185)
        assert s['Store operating profit'] == Decimal(315)
        assert s['General & Administrative'] == Decimal(70)
        assert s['Operating income'] == Decimal(245)

    def test_every_leaf_category_is_known_to_the_mapping(self):
        # A statement line sourcing a category no FAST account produces would
        # silently always be zero.
        from app.modules.pnl.fast_accounts import CATEGORIES
        sourced = {c for cats in LEAF_LINES.values() for c in cats}
        # These three have no account in either chart and always report zero;
        # they are listed so the set cannot grow unnoticed. `Marketing payroll`
        # is deliberately NOT here: the history sheet carries a row for it
        # (under section 17, value 0 throughout), so it IS mapped.
        expected_empty = {'Shipping', 'Variable occupancy', 'Field - Payroll'}
        assert sourced - CATEGORIES == expected_empty

    def test_statement_order_covers_the_computed_lines(self):
        assert len(STATEMENT_ORDER) == len(set(STATEMENT_ORDER))
        assert set(build_statement({})) == set(STATEMENT_ORDER)
