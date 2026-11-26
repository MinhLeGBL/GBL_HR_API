"""The reconciliation: actual values, with the tracking system's differences
added (custom_reports 0.9.0).

`engine.reconcile` is the arithmetic. The route decides who gets it, and is
tested in test_custom_reports_routes.py.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.modules.custom_reports import engine
from app.modules.custom_reports.engine import measure_value, reconcile

from .test_engine import bucket_of, receipt, sale


def deltas(out=None, in_=None):
    return {'out': out or {}, 'in': in_ or {}}


class TestSales:
    def test_a_tracked_line_gains_its_difference(self):
        f = sale(line_id=11, net_sales=1_000_000.0)
        reconcile([f], 'sales', deltas(out={11: 250_000.0}))
        assert f['netSales'] == 1_250_000.0

    def test_an_untracked_line_is_untouched(self):
        f = sale(line_id=12, net_sales=1_000_000.0)
        reconcile([f], 'sales', deltas(out={11: 250_000.0}))
        assert f['netSales'] == 1_000_000.0

    def test_a_negative_difference_lowers_the_figure(self):
        f = sale(line_id=11, net_sales=1_000_000.0)
        reconcile([f], 'sales', deltas(out={11: -400_000.0}))
        assert f['netSales'] == 600_000.0

    def test_a_return_is_the_mirror(self):
        """The tracker stores a return negated: Oracle -1,166,851.85 entered
        as -900,000 is +266,851.85. Here a return's value is positive, so
        the actual refund, 900,000, is the value minus the difference."""
        f = sale(line_id=13, item_type=2, net_sales=1_166_851.85)
        reconcile([f], 'sales', deltas(out={13: -900_000.0 - -1_166_851.85}))
        assert f['returnValue'] == pytest.approx(900_000.0)
        assert f['netSales'] == 0          # a return never touches net sales

    def test_fp_md_average_bill_and_asp_follow(self):
        fp = sale(line_id=1, bill_id=1, net_sales=1_000_000.0, fp_md='FP', qty=2)
        md = sale(line_id=2, bill_id=2, net_sales=500_000.0, fp_md='MD', qty=1)
        reconcile([fp, md], 'sales', deltas(out={1: 200_000.0}))
        b = bucket_of(fp, md)
        assert measure_value('netSales', b) == 1_700_000
        assert measure_value('fpSales', b) == 1_200_000
        assert measure_value('mdSales', b) == 500_000
        assert measure_value('avgBill', b) == 850_000
        assert measure_value('asp', b) == round(1_700_000 / 3)

    def test_imports_differences_do_not_touch_sales(self):
        f = sale(line_id=11, item_sid=7)
        reconcile([f], 'sales', deltas(in_={(7, None): 99.0}))
        assert f['netSales'] == 1_000_000.0


class TestImports:
    def test_cost_gains_the_unit_difference_times_units(self):
        f = receipt(item_sid=7, received_units=10, received_cost=5_000_000.0)
        reconcile([f], 'imports', deltas(in_={(7, None): 30_000.0}))
        assert f['receivedCost'] == 5_300_000.0

    def test_a_year_row_applies_to_that_years_receipts(self):
        f25 = receipt(item_sid=7, line_date='2025-06-01', received_units=2, received_cost=0.0)
        f26 = receipt(item_sid=7, line_date='2026-02-01', received_units=3, received_cost=0.0)
        reconcile([f25, f26], 'imports', deltas(in_={(7, 2025): 100.0}))
        assert f25['receivedCost'] == 200.0
        assert f26['receivedCost'] == 0.0          # no row for 2026, none for all years

    def test_the_year_row_wins_over_the_all_years_row(self):
        """Tracked both ways, an item is counted once per receipt, not twice."""
        f25 = receipt(item_sid=7, line_date='2025-06-01', received_units=2, received_cost=0.0)
        f26 = receipt(item_sid=7, line_date='2026-02-01', received_units=3, received_cost=0.0)
        reconcile([f25, f26], 'imports',
                  deltas(in_={(7, 2025): 100.0, (7, None): 10.0}))
        assert f25['receivedCost'] == 200.0        # the 2025 row
        assert f26['receivedCost'] == 30.0         # the all-years row

    def test_an_untracked_item_is_untouched(self):
        f = receipt(item_sid=8, received_cost=5_000_000.0)
        reconcile([f], 'imports', deltas(in_={(7, None): 30_000.0}))
        assert f['receivedCost'] == 5_000_000.0

    def test_units_are_never_changed(self):
        f = receipt(item_sid=7, received_units=10)
        reconcile([f], 'imports', deltas(in_={(7, None): 30_000.0}))
        assert f['receivedUnits'] == 10


class TestSellThrough:
    def test_nothing_changes(self):
        f = receipt(item_sid=7, received_units=10, received_cost=5_000_000.0)
        before = dict(f)
        reconcile([f], 'sellThrough', deltas(out={1: 1.0}, in_={(7, None): 1.0}))
        assert f == before


class TestRunQuery:
    """End to end through `run_query`, with the Oracle pull replaced."""

    def _run(self, facts, reconcile_with=None):
        query = engine.parse_query({'subject': 'sales', 'from': '2026-01-01',
                                    'to': '2026-03-31', 'groupBy': ['store']})
        if reconcile_with is not None:
            query['reconcile'] = reconcile_with
        with patch.object(engine, 'get_oracle_connection', return_value=MagicMock()), \
             patch.object(engine, 'facts_for', return_value=facts):
            return engine.run_query(query, lambda d: d, lambda m: m)['result']

    def test_totals_and_rows_carry_the_actual_values(self):
        res = self._run([sale(line_id=1, net_sales=1_000_000.0)],
                        deltas(out={1: 500_000.0}))
        net = [c['id'] for c in res['columns']].index('netSales')
        assert res['rows'][0][net] == 1_500_000
        assert res['totals'][net] == 1_500_000
        assert res['reconciled'] is True

    def test_an_ordinary_result_has_no_flag(self):
        res = self._run([sale(line_id=1)])
        assert 'reconciled' not in res

    def test_the_flag_is_set_even_when_nothing_matched(self):
        """The figures are then the actual ones too: nothing was tracked."""
        res = self._run([sale(line_id=1)], deltas())
        assert res['reconciled'] is True


def test_sale_lines_carry_their_line_id():
    """What the Out rows are keyed by. Without it nothing would ever match."""
    from app.modules.custom_reports.queries import EngineQueries
    assert 'di.sid                                       AS line_id' in EngineQueries.SALE_LINES

