"""
Tests for the custom-reports QUERY ENGINE (`POST /custom-reports/query`).

These cover the pure layer — validation, grouping, measures, the returns
split, the sell-through FIFO attribution — with hand-built fact rows, so
no database is touched. The SQL itself is exercised against production
read-only during development; what is asserted here is the logic the
frontend's `mocks/engine.ts` specifies.
"""
import datetime as dt

import pytest

from app.modules.custom_reports import engine
from app.modules.custom_reports.engine import (
    UNKNOWN, QueryError, accumulate, matches, measure_value, notes_for,
    parse_query, value_of,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def sale(**over):
    """A sale fact with sane defaults, shaped like `_sale_fact` output."""
    row = {
        'line_date': '2026-03-15', 'bill_id': 1, 'bill': 'RWR-1001',
        'store': 'RWR', 'item_type': 1, 'qty': 1, 'net_sales': 1_000_000.0,
        'fp_md': 'FP', 'cust_sid': 99, 'customer': 'Nguyen A',
        'customer_tier': 'GOLD', 'first_buy': '2024-01-01',
        'item_sid': 7, 'alu': 'KIT-SS25-WRTW-JKT', 'brand': 'KITON',
        'description': "Kiton Women's Jacket", 'season': 'SS25',
        'gender': 'W', 'department': 'RTW', 'category': 'JACKET',
        'item_size': '38', 'dcs_code': 'WRTW',
    }
    row.update(over)
    q = {'from': dt.date(2026, 1, 1), 'to': dt.date(2026, 12, 31)}
    # `first_buy` is a separate lookup in the engine (it is 26 of the 35
    # seconds a joined version cost), so the helper builds the one-entry
    # map the real `facts_for` would pass in.
    engine._derive(row, q, {row['cust_sid']: row.pop('first_buy', None)})
    return engine._sale_fact(row)


def receipt(**over):
    row = {
        'line_date': '2026-02-01', 'receiving': '1700', 'store': 'HQ',
        'received_units': 10, 'received_cost': 5_000_000.0,
        'item_sid': 7, 'alu': 'KIT-SS25-WRTW-JKT', 'brand': 'KITON',
        'description': "Kiton Women's Jacket", 'season': 'SS25',
        'gender': 'W', 'department': 'RTW', 'category': 'JACKET',
        'item_size': '38', 'dcs_code': 'WRTW',
    }
    row.update(over)
    q = {'from': dt.date(2026, 1, 1), 'to': dt.date(2026, 12, 31)}
    engine._derive(row, q)
    return engine._receipt_fact(row)


def bucket_of(*facts):
    b = engine._empty_bucket(())
    for f in facts:
        accumulate(b, f)
    return b


BASE = {'subject': 'sales', 'from': '2026-01-01', 'to': '2026-03-31'}


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------
class TestParseQuery:
    def test_minimal_query_is_accepted(self):
        q = parse_query(dict(BASE, groupBy=['brand']))
        assert q['subject'] == 'sales'
        assert q['from'] == dt.date(2026, 1, 1)
        assert q['groupBy'] == ['brand']
        assert q['filters'] == {}

    @pytest.mark.parametrize('body, fragment', [
        ({}, 'subject must be one of'),
        (dict(BASE, subject='stock'), 'subject must be one of'),
        ({'subject': 'sales', 'to': '2026-01-01'}, 'from is required'),
        (dict(BASE, **{'from': '15/03/2026'}), 'must be an ISO date'),
        ({'subject': 'sales', 'from': '2026-03-31', 'to': '2026-01-01'},
         'to must not be before from'),
        (dict(BASE, groupBy=['brand', 'store', 'season', 'category']),
         'at most 3'),
        (dict(BASE, groupBy=['brand', 'brand']), 'must not repeat'),
        (dict(BASE, groupBy=['colour']), 'unknown dimension'),
        (dict(BASE, groupBy=['date']), 'cannot be grouped'),
        (dict(BASE, filters={'bill': ['x']}), 'cannot be filtered'),
        (dict(BASE, filters={'brand': 'KITON'}), 'must be an array'),
    ])
    def test_rejections(self, body, fragment):
        with pytest.raises(QueryError) as e:
            parse_query(body)
        assert fragment in str(e.value)

    def test_one_year_cap_is_enforced_server_side(self):
        """The picker enforces it, but a client is not a validator."""
        parse_query({'subject': 'sales', 'from': '2026-01-01',
                     'to': '2026-12-31', 'groupBy': []})          # 365 days, ok
        with pytest.raises(QueryError, match='must not exceed'):
            parse_query({'subject': 'sales', 'from': '2025-01-01',
                         'to': '2026-12-31', 'groupBy': []})

    def test_empty_filter_array_is_not_a_filter(self):
        q = parse_query(dict(BASE, filters={'brand': []}, groupBy=[]))
        assert q['filters'] == {}

    @pytest.mark.parametrize('subject, dim', [
        ('imports', 'customer'),      # VOU_ITEM carries no customer
        ('imports', 'fpMd'),          # ...and no discount
        ('imports', 'customerTier'),
        ('sellThrough', 'store'),     # receipts land at HQ, sales elsewhere
        ('sellThrough', 'customer'),
        ('sellThrough', 'fpMd'),
    ])
    def test_inapplicable_dimension_is_rejected_not_ignored(self, subject, dim):
        """Silently dropping the clause would answer a DIFFERENT question
        under the label of the one that was asked — and for sell-through
        specifically it returns a wrong RATIO, not a narrower one."""
        with pytest.raises(QueryError, match='does not apply'):
            parse_query({'subject': subject, 'from': '2026-01-01',
                         'to': '2026-03-31', 'groupBy': [dim]})
        if DIMENSION_IS_FILTERABLE := engine.DIMENSIONS[dim].get('filterable'):
            with pytest.raises(QueryError, match='does not apply'):
                parse_query({'subject': subject, 'from': '2026-01-01',
                             'to': '2026-03-31', 'filters': {dim: ['x']}})
        assert DIMENSION_IS_FILTERABLE in (True, None)

    def test_bill_applies_to_sales_only(self):
        parse_query(dict(BASE, groupBy=['bill']))
        with pytest.raises(QueryError, match='does not apply'):
            parse_query({'subject': 'imports', 'from': '2026-01-01',
                         'to': '2026-03-31', 'groupBy': ['bill']})

    def test_receiving_applies_to_imports_and_sellthrough(self):
        for subject in ('imports', 'sellThrough'):
            parse_query({'subject': subject, 'from': '2026-01-01',
                         'to': '2026-03-31', 'groupBy': ['receiving']})
        with pytest.raises(QueryError, match='does not apply'):
            parse_query(dict(BASE, groupBy=['receiving']))


# ----------------------------------------------------------------------
# Dimension values
# ----------------------------------------------------------------------
class TestValueOf:
    def test_month_is_derived_from_the_rows_own_date(self):
        assert value_of(sale(line_date='2026-03-15'), 'month') == '2026-03'
        assert value_of(receipt(line_date='2026-02-01'), 'month') == '2026-02'

    def test_empty_becomes_the_unknown_bucket_never_blank(self):
        """A blank cell reads as a rendering fault; "unknown" is a fact
        about the data."""
        assert value_of(sale(season=None), 'season') == UNKNOWN
        assert value_of(sale(category=''), 'category') == UNKNOWN

    def test_size_is_normalised_through_the_brands_sizing_system(self):
        """AKRIS 36 is FR 36 = S; MARNI 36 is IT 36 = XS. One physical
        label means two different sizes, which is why grouping the raw
        label scatters a size across rows."""
        assert value_of(sale(brand='AKRIS', item_size='36'), 'size') == 'S'
        assert value_of(sale(brand='MARNI INTERNATIONAL S.A',
                             item_size='36'), 'size') == 'XS'

    def test_a_shoe_size_is_not_translated(self):
        """A 38 shoe is not a medium. `normalize_size` keys this on the
        RAW DCS code, so handing it the gender-stripped department
        ('SHO' instead of 'WSHO') translates shoes as clothing."""
        assert value_of(sale(brand='AKRIS', dcs_code='WSHO',
                             department='SHO', item_size='38'), 'size') == '38'
        assert value_of(sale(brand='AKRIS', dcs_code='WRTW',
                             department='RTW', item_size='38'), 'size') == 'M'

    def test_a_one_size_label_lands_in_the_unknown_bucket(self):
        """A scarf has no size, and saying so beats inventing one. Note
        this differs from `/sizes`, which DROPS such rows — an engine that
        groups Sales by size must still account for every unit."""
        for label in ('TU', 'OS', 'U', 'ONE SIZE'):
            assert value_of(sale(item_size=label), 'size') == UNKNOWN, label

    def test_an_unmapped_numeric_passes_through_as_its_raw_label(self):
        """Its own bucket, rather than disappearing into a wrong one.
        This is what `caveatSizeNormalised` tells the reader about."""
        assert value_of(sale(brand='AKRIS', item_size='99'), 'size') == '99'

    def test_bill_carries_the_store_prefix(self):
        """Sales run six per-store DOC_NO sequences; the bare number
        merges 63.7% of all bills."""
        assert value_of(sale(bill='RWD-3381'), 'bill') == 'RWD-3381'


class TestCustomerType:
    def test_new_when_first_ever_purchase_falls_inside_the_period(self):
        f = sale(first_buy='2026-02-02')
        assert value_of(f, 'customerType') == 'new'

    def test_returning_when_they_bought_before_the_period(self):
        assert value_of(sale(first_buy='2023-05-01'), 'customerType') == 'returning'

    def test_widening_the_period_reclassifies_a_customer(self):
        """What `caveatCustomerType` discloses — asserted so the caveat
        cannot quietly stop being true."""
        row = dict(cust_sid=1, customer='X', brand='KITON',
                   department='RTW', item_size='38')
        lookup = {1: '2025-06-01'}
        narrow = dict(row)
        engine._derive(narrow, {'from': dt.date(2026, 1, 1),
                                'to': dt.date(2026, 12, 31)}, lookup)
        wide = dict(row)
        engine._derive(wide, {'from': dt.date(2025, 1, 1),
                              'to': dt.date(2025, 12, 31)}, lookup)
        assert narrow['customerType'] == 'returning'
        assert wide['customerType'] == 'new'

    def test_an_unasked_customer_type_falls_to_returning(self):
        """When `customerType` is neither grouped nor filtered the engine
        skips the first-buy lookup entirely — 26 of the 35 seconds a
        joined version cost. The value is then unreachable: no column, no
        group key, no filter test reads it."""
        row = dict(cust_sid=1, customer='X', brand='KITON',
                   department='RTW', item_size='38')
        engine._derive(row, {'from': dt.date(2026, 1, 1),
                             'to': dt.date(2026, 12, 31)}, None)
        assert row['customerType'] == 'returning'

    def test_the_lookup_runs_only_when_the_dimension_is_asked_for(self):
        calls = []

        class FakeCursor:
            def execute(self, sql, binds):
                calls.append(sql)
                self.description = [('CUST_SID',), ('FIRST_BUY',)]

            def fetchall(self):
                return [(1, '2020-01-01')]

        for body, expected in (
            (dict(BASE, groupBy=['brand']), 0),
            (dict(BASE, groupBy=['customerType']), 1),
            (dict(BASE, groupBy=[], filters={'customerType': ['new']}), 1),
        ):
            calls.clear()
            engine._first_buy(FakeCursor(), parse_query(body))
            assert len(calls) == expected, body

    def test_walk_in_covers_both_no_customer_and_the_tourist_record(self):
        """TOURIST is one synthetic POS record standing for many anonymous
        walk-ins, so counting it as a returning individual would be wrong."""
        assert value_of(sale(cust_sid=None, customer=None),
                        'customerType') == 'walkIn'
        assert value_of(sale(customer='TOURIST'), 'customerType') == 'walkIn'
        assert value_of(sale(customer='TOURIST.'), 'customerType') == 'walkIn'

    def test_walk_in_has_no_customer_name(self):
        assert value_of(sale(customer='TOURIST'), 'customer') == UNKNOWN


# ----------------------------------------------------------------------
# Filters
# ----------------------------------------------------------------------
class TestMatches:
    def test_multi_filter_is_an_exact_match_on_any_picked_value(self):
        f = sale(brand='KITON')
        assert matches(f, {'brand': ['KITON', 'ERES']})
        assert not matches(f, {'brand': ['ERES']})

    def test_search_filter_is_a_case_insensitive_substring(self):
        """Exact-matching a 22,000-value description field would mean
        typing it perfectly to get one row back."""
        f = sale(description="Kiton Women's Jacket")
        assert matches(f, {'description': ['jacket']})
        assert matches(f, {'description': ['WOMEN']})
        assert not matches(f, {'description': ['trouser']})

    def test_filters_combine_as_AND(self):
        f = sale(brand='KITON', season='SS25')
        assert matches(f, {'brand': ['KITON'], 'season': ['SS25']})
        assert not matches(f, {'brand': ['KITON'], 'season': ['FW25']})

    def test_unknown_bucket_is_filterable_like_any_other_value(self):
        assert matches(sale(season=None), {'season': [UNKNOWN]})


# ----------------------------------------------------------------------
# Measures
# ----------------------------------------------------------------------
class TestMeasures:
    def test_zero_denominator_is_none_not_zero(self):
        """"No bills, so no average bill" and "an average bill of zero"
        are different facts, and the second one would be a finding."""
        empty = engine._empty_bucket(())
        for mid in ('avgBill', 'asp', 'fpShare', 'fpSalesShare',
                    'sellThroughPct'):
            assert measure_value(mid, empty) is None, mid

    def test_bills_is_a_distinct_count_not_a_line_count(self):
        b = bucket_of(sale(bill_id=1), sale(bill_id=1), sale(bill_id=2))
        assert measure_value('bills', b) == 3 - 1

    def test_avg_bill_divides_by_distinct_bills(self):
        b = bucket_of(sale(bill_id=1, net_sales=600.0),
                      sale(bill_id=1, net_sales=400.0))
        assert measure_value('netSales', b) == 1000
        assert measure_value('bills', b) == 1
        assert measure_value('avgBill', b) == 1000

    def test_derived_measures_come_from_summed_components(self):
        """A total computed by summing per-row averages is wrong, and
        that is the easy mistake here."""
        b = bucket_of(sale(bill_id=1, qty=1, net_sales=1000.0),
                      sale(bill_id=2, qty=3, net_sales=3000.0))
        assert measure_value('asp', b) == 1000            # 4000 / 4, not avg(1000, 1000)
        assert measure_value('avgBill', b) == 2000        # 4000 / 2

    def test_on_hand_floors_at_zero(self):
        """FIFO can attribute more sold units than a single receipt line
        carries when a SKU was received more than once."""
        b = engine._empty_bucket(())
        b['receivedUnits'], b['soldUnits'] = 5, 8
        assert measure_value('onHandUnits', b) == 0


class TestFpMdSplit:
    def test_fp_and_md_sum_back_to_the_whole_exactly(self):
        """The invariant the shares are read against. A split that does
        not reconcile is worse than no split."""
        b = bucket_of(sale(fp_md='FP', qty=2, net_sales=2000.0),
                      sale(fp_md='MD', qty=3, net_sales=1500.0),
                      sale(fp_md='FP', qty=1, net_sales=900.0))
        assert measure_value('fpUnits', b) + measure_value('mdUnits', b) \
            == measure_value('units', b)
        assert measure_value('fpSales', b) + measure_value('mdSales', b) \
            == measure_value('netSales', b)

    def test_a_return_does_not_land_on_either_side(self):
        """A return has no discount rate, so it has no FP or MD side —
        which is why it must stay out of `netSales`."""
        b = bucket_of(sale(fp_md='FP', qty=2, net_sales=2000.0),
                      sale(item_type=2, fp_md='FP', qty=1, net_sales=800.0))
        assert measure_value('units', b) == 2
        assert measure_value('netSales', b) == 2000
        assert measure_value('returnedUnits', b) == 1
        assert measure_value('returnValue', b) == 800
        assert measure_value('fpUnits', b) + measure_value('mdUnits', b) == 2


class TestReturns:
    def test_returns_are_positive_and_carried_separately(self):
        """The `weekly_report` shape. There is no negative quantity
        anywhere in DOCUMENT_ITEM, so a return cannot be found by sign."""
        f = sale(item_type=2, qty=2, net_sales=500_000.0)
        assert f['returnedUnits'] == 2
        assert f['returnValue'] == 500_000.0
        assert f['units'] == 0
        assert f['netSales'] == 0

    def test_a_returns_only_document_counts_as_zero_bills(self):
        """Surfaced on purpose: it has a bill number so it gets a row,
        but it is not a SALE bill. `bills` counts sale bills, matching
        `reports`. Hiding the return would be worse."""
        b = bucket_of(sale(bill_id=42, item_type=2, qty=1, net_sales=900.0))
        assert measure_value('bills', b) == 0
        assert measure_value('returnedUnits', b) == 1

    def test_note_is_raised_only_when_the_period_had_returns(self):
        """A standing warning about returns on a period that had none is
        noise."""
        q = parse_query(dict(BASE, groupBy=['brand']))
        assert 'noteReturnsNotNetted' in notes_for(q, has_returns=True)
        assert 'noteReturnsNotNetted' not in notes_for(q, has_returns=False)


# ----------------------------------------------------------------------
# Sell-through
# ----------------------------------------------------------------------
class TestSellThroughCohort:
    def test_sold_units_attach_to_the_receipt_that_preceded_them(self):
        receipts = [receipt(line_date='2026-02-01', received_units=10)]
        sold = [{'item_sid': 7, 'sale_date': '2026-03-01', 'qty': 4}]
        facts = engine._sell_through_facts(receipts, sold)
        assert [f['soldUnits'] for f in facts] == [4]
        b = bucket_of(*facts)
        assert measure_value('sellThroughPct', b) == pytest.approx(0.4)

    def test_a_sale_before_the_receipt_is_not_attributed_to_it(self):
        """Without the date guard, a sale early in the period would be
        absorbed by stock that landed in December."""
        receipts = [receipt(line_date='2026-06-01', received_units=10)]
        sold = [{'item_sid': 7, 'sale_date': '2026-01-05', 'qty': 4}]
        facts = engine._sell_through_facts(receipts, sold)
        assert [f['soldUnits'] for f in facts] == [0]

    def test_sales_of_another_item_are_not_attributed(self):
        receipts = [receipt(item_sid=7, received_units=5)]
        sold = [{'item_sid': 999, 'sale_date': '2026-03-01', 'qty': 5}]
        assert [f['soldUnits'] for f in engine._sell_through_facts(receipts, sold)] == [0]

    def test_fifo_charges_the_oldest_open_receipt_first(self):
        receipts = [receipt(line_date='2026-01-10', receiving='1', received_units=3),
                    receipt(line_date='2026-02-10', receiving='2', received_units=3)]
        sold = [{'item_sid': 7, 'sale_date': '2026-03-01', 'qty': 5}]
        facts = engine._sell_through_facts(receipts, sold)
        assert [f['soldUnits'] for f in facts] == [3, 2]

    def test_a_receipt_can_never_be_oversold(self):
        receipts = [receipt(received_units=2)]
        sold = [{'item_sid': 7, 'sale_date': '2026-03-01', 'qty': 9}]
        facts = engine._sell_through_facts(receipts, sold)
        assert facts[0]['soldUnits'] == 2
        assert measure_value('sellThroughPct', bucket_of(*facts)) == 1.0

    def test_a_received_but_never_sold_line_is_a_row_at_zero(self):
        """Not an absence — the report exists to surface exactly these."""
        facts = engine._sell_through_facts([receipt(received_units=6)], [])
        assert len(facts) == 1
        assert measure_value('sellThroughPct', bucket_of(*facts)) == 0.0

    def test_excess_sales_are_dropped_rather_than_inflating_the_rate(self):
        """Residue is a sale of stock received BEFORE the period; that
        receipt is not in this result to carry it, so the unit vanishes
        rather than pushing the rate over 100%."""
        receipts = [receipt(received_units=2)]
        sold = [{'item_sid': 7, 'sale_date': '2026-03-01', 'qty': 5}]
        b = bucket_of(*engine._sell_through_facts(receipts, sold))
        assert measure_value('sellThroughPct', b) <= 1.0

    def test_cohort_note_is_always_raised(self):
        q = parse_query({'subject': 'sellThrough', 'from': '2026-01-01',
                         'to': '2026-03-31', 'groupBy': ['brand']})
        assert 'noteSellThroughCohort' in notes_for(q, has_returns=False)


# ----------------------------------------------------------------------
# Notes
# ----------------------------------------------------------------------
class TestNotes:
    def test_caveats_are_derived_from_the_dimension_registry(self):
        """Not a hardcoded list — so a new caveat is one field, and
        cannot be added without firing."""
        q = parse_query(dict(BASE, groupBy=['size']))
        assert 'caveatSizeNormalised' in notes_for(q, False)
        q = parse_query(dict(BASE, filters={'customerTier': ['GOLD']},
                             groupBy=[]))
        assert 'caveatTier' in notes_for(q, False)

    def test_a_caveat_fires_for_a_filter_as_well_as_a_grouping(self):
        q = parse_query(dict(BASE, filters={'size': ['M']}, groupBy=['brand']))
        assert 'caveatSizeNormalised' in notes_for(q, False)

    def test_detail_mode_is_announced(self):
        q = parse_query(dict(BASE, groupBy=[]))
        assert 'noteDetailMode' in notes_for(q, False)
        q = parse_query(dict(BASE, groupBy=['brand']))
        assert 'noteDetailMode' not in notes_for(q, False)

    def test_no_caveat_is_repeated(self):
        q = parse_query(dict(BASE, groupBy=['size'], filters={'size': ['M']}))
        notes = notes_for(q, False)
        assert notes.count('caveatSizeNormalised') == 1


# ----------------------------------------------------------------------
# Contract — the frontend is the specification
# ----------------------------------------------------------------------
class TestContract:
    def test_net_line_matches_the_reports_module_exactly(self):
        """Copied rather than imported for readability; asserted equal so
        the two cannot drift. Re-applying `di.DISC_PERC` on top of
        `di.PRICE` is the double-discount bug that shipped twice."""
        from app.modules.reports import queries as reports_queries
        from app.modules.custom_reports import queries as cr_queries
        assert cr_queries._NET_LINE == reports_queries._NET_LINE
        assert cr_queries._IS_FP == reports_queries._IS_FP

    def test_every_listed_measure_has_a_format(self):
        for subject in engine.SUBJECTS:
            for mid in (engine.GROUPED_MEASURES[subject]
                        + engine.LINE_MEASURES[subject]):
                assert mid in engine.MEASURES, mid

    def test_every_listed_measure_is_computable(self):
        """A typo in a measure list would otherwise reach the table as a
        blank column over real numbers."""
        b = bucket_of(sale(), receipt())
        for mid in engine.MEASURES:
            measure_value(mid, b)       # must not raise

    def test_line_measures_are_a_subset_of_what_the_subject_computes(self):
        for subject in engine.SUBJECTS:
            assert set(engine.LINE_MEASURES[subject]) <= set(engine.MEASURES)

    def test_detail_dimensions_apply_to_their_subject(self):
        for subject, dims in engine.DETAIL_DIMENSIONS.items():
            for dim in dims:
                assert subject in engine.DIMENSIONS[dim]['subjects'], \
                    f'{dim} is not available on {subject}'

    def test_sell_through_detail_carries_neither_date_nor_store(self):
        """Both on the user's instruction, and both for the same reason: a
        row spans two events, so one value could only describe one of
        them and would be read as the other."""
        dims = engine.DETAIL_DIMENSIONS['sellThrough']
        assert 'date' not in dims
        assert 'store' not in dims
        assert 'receiving' in dims

    def test_returns_are_absent_from_imports_and_sellthrough(self):
        for subject in ('imports', 'sellThrough'):
            measures = set(engine.GROUPED_MEASURES[subject])
            assert not measures & {'returnValue', 'returnedUnits'}


class TestSortedValues:
    def test_unknown_sorts_last(self):
        """It is the one entry a reader scans past, not toward."""
        out = engine._sorted_values({'ZARA', None, 'AKRIS', ''})
        assert out[-1] == UNKNOWN
        assert out[:2] == ['AKRIS', 'ZARA']

    def test_values_are_distinct_after_the_unknown_collapse(self):
        out = engine._sorted_values({None, '', '  '})
        assert out == [UNKNOWN]


class TestStorePicker:
    """`GET /dimensions/store` offers only stores with activity.

    The user's call (2026-11-05). `rps.store` has nine rows, but `LOA`,
    `RWL` and `DEMO` have zero sale lines and zero receipt lines in ALL
    history, so offering them is a filter value that can only ever return
    an empty result — and `DEMO` is not a shop.
    """

    def test_the_query_constrains_on_activity_not_mere_existence(self):
        sql = engine._PICKER_SQL['store']
        assert 'rps.document' in sql and 'rps.voucher' in sql, \
            'a plain SELECT from rps.store would offer the dead stores'

    def test_both_document_scopes_count_as_activity(self):
        """A store that only ever RECEIVED must still be offered — HQ
        takes essentially all receiving (33,170 lines) against 609 sale
        lines, so a sales-only definition would nearly drop it."""
        sql = engine._PICKER_SQL['store']
        assert 'receipt_type IN (0, 1)' in sql      # the sale side
        assert 'vou_type = 0' in sql                # the receipt side

    def test_the_picker_is_not_bounded_by_any_date(self):
        """Like every other picker: a store that sold nothing this quarter
        must still be offered, or the filter cannot reach the rows you are
        about to widen the range to find."""
        sql = engine._PICKER_SQL['store']
        assert ':from_date' not in sql and ':to_exclusive' not in sql

    def test_store_is_still_absent_from_sell_through(self):
        """Restricting the picker does not make the dimension applicable —
        receipts land at HQ while the sale happened in an outlet."""
        assert 'sellThrough' not in engine.DIMENSIONS['store']['subjects']
