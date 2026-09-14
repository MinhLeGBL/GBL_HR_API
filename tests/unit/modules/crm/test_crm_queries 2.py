"""
Guards the v1.2.0 revenue fix in app.modules.crm.queries.

`di.PRICE` is already net of the item discount and is a UNIT price, so every
revenue SUM must multiply by `di.QTY` and must NOT re-apply `di.DISC_PERC`
(doing so double-discounts — the same bug fixed in the reports module). The
Full-Price/Markdown split may still use `di.DISC_PERC`, but only inside the
combined-discount *classification* rate, never as a revenue multiplier.

Oracle math can't be unit-tested (no DB), so we pin the SQL shape.
"""
from app.modules.crm.queries import CRMQueries, _NET_REVENUE, _COMBINED_DISC

# Every uppercase class attribute that is a SQL string.
_QUERIES = {
    n: getattr(CRMQueries, n)
    for n in dir(CRMQueries)
    if n.isupper() and isinstance(getattr(CRMQueries, n), str)
}

# Queries that carry no revenue at all (phone lookup) — exempt from the
# revenue-shape assertions.
_NO_REVENUE = {'CUSTOMER_PHONE'}


class TestRevenueFormulaShape:

    def test_net_revenue_fragment_is_qty_based_ex_tax(self):
        assert 'NVL(di.QTY, 0)' in _NET_REVENUE, 'PRICE is a unit price — need × QTY'
        assert 'di.DISC_PERC' not in _NET_REVENUE, (
            'PRICE is already net of the item discount — must not re-apply it')
        assert 'NVL(di.TAX_AMT, 0)' in _NET_REVENUE and 'd.DISC_PERC' in _NET_REVENUE

    def test_no_query_reapplies_item_discount_to_revenue(self):
        # The buggy multiplier `* (1 - NVL(di.DISC_PERC ...))` must appear in no
        # query. Legit di.DISC_PERC use (classification) has no leading `* `.
        for name, sql in _QUERIES.items():
            assert '* (1 - NVL(di.DISC_PERC' not in sql, (
                f'{name} still multiplies revenue by the item discount')

    def test_every_revenue_query_multiplies_by_qty(self):
        for name, sql in _QUERIES.items():
            if name in _NO_REVENUE:
                continue
            assert _NET_REVENUE in sql, (
                f'{name} does not use the shared QTY-based net-revenue fragment')

    def test_item_discount_only_in_classification(self):
        # di.DISC_PERC may appear ONLY as part of the combined-discount rate.
        for name, sql in _QUERIES.items():
            if 'di.DISC_PERC' in sql:
                assert _COMBINED_DISC in sql, (
                    f'{name} uses di.DISC_PERC outside the FP/MD classification')

    def test_fp_md_split_queries_still_classify(self):
        # The two split queries must keep the combined-discount classification.
        for name in ('CUSTOMER_RFM_AGGREGATES', 'CUSTOMER_DRILLDOWN_TOTALS'):
            assert _COMBINED_DISC in _QUERIES[name]
            assert '<= 0.30' in _QUERIES[name] and '> 0.30' in _QUERIES[name]
