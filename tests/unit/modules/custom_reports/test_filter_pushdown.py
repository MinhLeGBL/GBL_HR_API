"""
Tests for FILTER PUSHDOWN — the SQL half of `filters` (engine v0.8.0).

A filter is applied either in SQL (`_where_for`, inside the pull) or in
Python (`matches`, after it). Splitting one behaviour across two
implementations is the whole risk here, so the central assertion in this
file is EQUIVALENCE: for every pushable dimension, the generated
predicate must keep exactly the rows `matches()` keeps and drop exactly
the rows it drops.

The predicate is checked by executing it — against an in-memory SQLite
table standing in for the Oracle pull — rather than by string-matching
the SQL. Asserting on the text would pass a WHERE clause that is
well-formed and wrong; running it cannot.

⚠️ `item_size` / `customerType` / `customer` are NOT pushable and are
asserted to stay in Python. See `PUSHDOWN_EXCLUDED`.
"""
import datetime as dt
import sqlite3

import pytest

from app.modules.custom_reports.engine import (
    DIMENSIONS, MAX_RANGE_DAYS, MAX_RANGE_DAYS_FILTERED, PUSHDOWN_EXCLUDED,
    UNKNOWN, QueryError, _narrowing, _where_for, matches, parse_query,
    split_filters, universe_lookup,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
# The pushable columns, with a deliberate spread of awkward values: a
# NULL and an empty string (both read as UNKNOWN by `value_of`), and
# values differing only by case or padding.
ROWS = [
    # store  brand          season  gender  department  category  fp_md  customer_tier
    ('RWR', 'AKRIS',        'SS25', 'W',    'RTW',      'JACKET', 'FP',  'GOLD'),
    ('RWR', 'AKRIS PUNTO',  'SS25', 'W',    'RTW',      'DRESS',  'MD',  'SILVER'),
    ('RHN', 'THE ROW',      'FW25', 'M',    'SHO',      'BOOT',   'FP',  None),
    ('RHN', 'akris',        'FW25', 'W',    'RTW',      'JACKET', 'FP',  'GOLD'),
    ('RWT', None,           'SS25', 'W',    'BAG',      'TOTE',   'MD',  ''),
    ('RWT', 'NANIS',        None,   None,   None,       None,     'FP',  'GOLD'),
    ('HQ',  ' AKRIS',       'SS25', 'W',    'RTW',      'JACKET', 'MD',  'BRONZE'),
]
COLS = ('store', 'brand', 'season', 'gender', 'department', 'category',
        'fp_md', 'customer_tier')


@pytest.fixture
def db():
    """The pushed-down predicate, actually executed."""
    conn = sqlite3.connect(':memory:')
    conn.execute(f"CREATE TABLE lines ({', '.join(f'{c} TEXT' for c in COLS)})")
    conn.executemany(
        f"INSERT INTO lines VALUES ({', '.join('?' * len(COLS))})", ROWS)
    yield conn
    conn.close()


def sql_keeps(db, filters):
    """Row indexes the SQL predicate keeps."""
    where, binds = _where_for(filters)
    # `_where_for` emits Oracle-style `:name` binds; sqlite accepts the
    # same syntax for named parameters.
    sql = 'SELECT rowid FROM lines'
    if where:
        sql += f' WHERE {where}'
    return {r[0] - 1 for r in db.execute(sql, binds)}


def python_keeps(filters):
    """Row indexes `matches()` keeps, over the same rows."""
    out = set()
    for i, row in enumerate(ROWS):
        if matches(dict(zip(COLS, row)), filters):
            out.add(i)
    return out


# ----------------------------------------------------------------------
# The split
# ----------------------------------------------------------------------
class TestSplit:
    def test_pushable_and_python_halves_are_disjoint_and_complete(self):
        filters = {
            'brand': ['AKRIS'], 'department': ['RTW'], 'store': ['RWR'],
            'size': ['M'], 'customerType': ['new'], 'customer': ['Nguyen A'],
        }
        push, keep = split_filters(filters)
        assert set(push) | set(keep) == set(filters)
        assert not (set(push) & set(keep))

    @pytest.mark.parametrize('dim', ['size', 'customerType', 'customer'])
    def test_excluded_dimensions_stay_in_python(self, dim):
        """These three must never reach SQL — see PUSHDOWN_EXCLUDED."""
        push, keep = split_filters({dim: ['anything']})
        assert dim not in push
        assert dim in keep

    def test_customer_is_excluded_despite_having_a_column(self):
        """`_derive` rewrites it to None for walk-ins and TOURIST, so a
        SQL filter would keep rows `matches()` drops."""
        assert 'customer' in PUSHDOWN_EXCLUDED
        assert DIMENSIONS['customer']['field']    # it HAS a column

    @pytest.mark.parametrize('dim', [
        'store', 'brand', 'season', 'gender', 'department', 'category',
        'fpMd', 'customerTier',
    ])
    def test_the_eight_pushable_dimensions_are_pushed(self, dim):
        push, keep = split_filters({dim: ['x']})
        assert dim in push
        assert not keep

    def test_no_filters_means_no_predicate(self):
        where, binds = _where_for({})
        assert where == ''
        assert binds == {}


# ----------------------------------------------------------------------
# Equivalence — the reason this file exists
# ----------------------------------------------------------------------
class TestEquivalence:
    @pytest.mark.parametrize('filters', [
        {'brand': ['AKRIS']},
        {'brand': ['AKRIS', 'NANIS']},
        {'store': ['RWR', 'RHN']},
        {'department': ['RTW']},
        {'fpMd': ['FP']},
        {'customerTier': ['GOLD']},
        {'season': ['SS25'], 'gender': ['W']},
        {'brand': ['AKRIS'], 'department': ['RTW'], 'store': ['RWR']},
        {'category': ['JACKET', 'BOOT'], 'fpMd': ['FP']},
        # Nothing matches — the predicate must be empty-handed, not broken.
        {'brand': ['NO SUCH BRAND']},
    ])
    def test_sql_and_python_keep_the_same_rows(self, db, filters):
        assert sql_keeps(db, filters) == python_keeps(filters)

    def test_case_sensitive_like_python(self, db):
        """`value_of` returns `str(v)` and `matches` uses `in`, so
        matching is EXACT. 'akris' must not match 'AKRIS'."""
        assert sql_keeps(db, {'brand': ['AKRIS']}) == python_keeps({'brand': ['AKRIS']})
        assert 3 not in sql_keeps(db, {'brand': ['AKRIS']})   # 'akris'
        assert 6 not in sql_keeps(db, {'brand': ['AKRIS']})   # ' AKRIS'

    def test_padding_is_not_trimmed(self, db):
        """There is no TRIM on either side: ' AKRIS' is its own value."""
        assert sql_keeps(db, {'brand': [' AKRIS']}) == python_keeps({'brand': [' AKRIS']})
        assert sql_keeps(db, {'brand': [' AKRIS']}) == {6}

    @pytest.mark.parametrize('dim, col', [
        ('brand', 'brand'), ('season', 'season'), ('gender', 'gender'),
        ('department', 'department'), ('category', 'category'),
        ('customerTier', 'customer_tier'),
    ])
    def test_unknown_picks_up_null_and_empty(self, db, dim, col):
        """`value_of` maps BOTH NULL and '' to UNKNOWN, so the predicate
        must too — not a literal em-dash comparison."""
        assert sql_keeps(db, {dim: [UNKNOWN]}) == python_keeps({dim: [UNKNOWN]})

    def test_unknown_mixed_with_concrete_values(self, db):
        f = {'customerTier': ['GOLD', UNKNOWN]}
        assert sql_keeps(db, f) == python_keeps(f)
        # row 2 is NULL, row 4 is '' — both UNKNOWN — plus the GOLD rows.
        assert sql_keeps(db, f) == {0, 2, 3, 4, 5}

    def test_unknown_alone_matches_nothing_concrete(self, db):
        f = {'department': [UNKNOWN]}
        assert sql_keeps(db, f) == python_keeps(f) == {5}

    def test_binds_cannot_collide_with_the_date_binds(self):
        """The pull already binds `from_date` / `to_exclusive`."""
        _, binds = _where_for({'brand': ['A'], 'store': ['B'], 'season': ['C']})
        assert 'from_date' not in binds
        assert 'to_exclusive' not in binds

    def test_a_value_that_looks_like_sql_is_bound_not_spliced(self, db):
        """Values reach Oracle as binds, so quotes are data."""
        nasty = "AKRIS' OR 1=1 --"
        where, binds = _where_for({'brand': [nasty]})
        assert nasty not in where
        assert nasty in binds.values()
        assert sql_keeps(db, {'brand': [nasty]}) == set()


# ----------------------------------------------------------------------
# The conditional range cap
# ----------------------------------------------------------------------
BASE = {'subject': 'sales', 'groupBy': []}


def q(days, **over):
    start = dt.date(2022, 1, 1)
    body = dict(BASE, **{'from': start.isoformat(),
                         'to': (start + dt.timedelta(days=days - 1)).isoformat()})
    body.update(over)
    return body


class TestConditionalRangeCap:
    def test_unfiltered_still_capped_at_a_year(self):
        parse_query(q(MAX_RANGE_DAYS))
        with pytest.raises(QueryError, match='must not exceed'):
            parse_query(q(MAX_RANGE_DAYS + 1))

    def test_a_pushable_filter_unlocks_multi_year(self):
        """The whole point: 3 years by brand is 0.53s, cheaper than the
        one year unfiltered that was already allowed."""
        parse_query(q(1095, filters={'brand': ['AKRIS']}))

    def test_the_long_cap_is_still_a_cap(self):
        parse_query(q(MAX_RANGE_DAYS_FILTERED, filters={'brand': ['AKRIS']}))
        with pytest.raises(QueryError, match='must not exceed'):
            parse_query(q(MAX_RANGE_DAYS_FILTERED + 1,
                          filters={'brand': ['AKRIS']}))

    @pytest.mark.parametrize('dim, value', [
        ('size', 'M'),
        ('customerType', 'new'),
        ('customer', 'Nguyen A'),
    ])
    def test_a_python_only_filter_does_not_unlock_it(self, dim, value):
        """However selective it looks, it is applied AFTER the full pull —
        so it would license the long range without making it cheaper."""
        with pytest.raises(QueryError, match='must not exceed'):
            parse_query(q(1095, filters={dim: [value]}))

    def test_the_unfiltered_error_names_what_would_unlock_it(self):
        with pytest.raises(QueryError) as e:
            parse_query(q(1095))
        msg = str(e.value)
        assert 'brand' in msg and 'department' in msg
        # Not offered as a way out, because it is not one.
        assert 'customerType' not in msg

    def test_an_empty_filter_array_does_not_unlock_it(self):
        """`parse_query` drops empty lists, so this is unfiltered."""
        with pytest.raises(QueryError, match='must not exceed'):
            parse_query(q(1095, filters={'brand': []}))

    def test_a_python_only_filter_alongside_a_pushable_one_is_fine(self):
        parse_query(q(1095, filters={'brand': ['AKRIS'], 'size': ['M']}))

    def test_range_is_checked_after_the_filters_are_validated(self):
        """Reordered in v0.8.0 so the cap can depend on them — a bad
        filter must still be reported as a bad filter."""
        with pytest.raises(QueryError, match='cannot be filtered'):
            parse_query(q(1095, filters={'alu': ['X']}))
        with pytest.raises(QueryError, match='unknown dimension'):
            parse_query(q(1095, filters={'nope': ['X']}))


# ----------------------------------------------------------------------
# A filter that selects everything is not a filter
# ----------------------------------------------------------------------
# Measured option counts on production, 2026-11-11 — the reason this
# check exists. The small ones are selectable in full by hand:
#
#     fpMd 2 · gender 4 · customerTier 5 · store 6 · department 7
#     season 82 · category 125 · brand 381
#
# `parse_query` is handed a stub universe here. The real one talks to
# Oracle (`universe_lookup`); what needs testing is the DECISION, and a
# stub makes every case — including the ones a live DB cannot produce,
# like a lookup that fails — reachable and deterministic.
UNIVERSE = {
    'department': ['ACC', 'BAG', 'COSM', 'HOME', 'RTW', 'SHO', UNKNOWN],
    'fpMd': ['FP', 'MD'],
    'brand': ['AKRIS', 'NANIS', 'THE ROW'],
    'store': ['HQ', 'RHN', 'RWD', 'RWR', 'RWT', 'RHX'],
}


def stub_universe(mapping=None):
    src = UNIVERSE if mapping is None else mapping
    return lambda dim: src.get(dim)


class TestNarrowingPredicate:
    def test_a_subset_narrows(self):
        assert _narrowing('brand', ['AKRIS'], ['AKRIS', 'NANIS']) is True

    def test_every_option_does_not_narrow(self):
        assert _narrowing('brand', ['AKRIS', 'NANIS'], ['AKRIS', 'NANIS']) is False

    def test_order_does_not_matter(self):
        assert _narrowing('brand', ['NANIS', 'AKRIS'], ['AKRIS', 'NANIS']) is False

    def test_a_superset_does_not_narrow(self):
        """Compared as a superset, so a stale client value cannot defeat
        the check by merely making the two sets unequal."""
        assert _narrowing('brand', ['AKRIS', 'NANIS', 'GONE'],
                          ['AKRIS', 'NANIS']) is False

    def test_duplicates_do_not_defeat_it(self):
        assert _narrowing('brand', ['AKRIS', 'AKRIS'], ['AKRIS']) is False

    def test_the_unknown_bucket_counts_as_an_option(self):
        """UNKNOWN is a real selectable value, so covering it matters."""
        assert _narrowing('brand', ['AKRIS'], ['AKRIS', UNKNOWN]) is True
        assert _narrowing('brand', ['AKRIS', UNKNOWN], ['AKRIS', UNKNOWN]) is False

    def test_case_and_padding_are_not_folded(self):
        """`_where_for` and `matches` compare raw strings; this must too,
        or the three disagree about the same row."""
        assert _narrowing('brand', ['akris'], ['AKRIS']) is True
        assert _narrowing('brand', [' AKRIS'], ['AKRIS']) is True

    @pytest.mark.parametrize('universe', [None, []])
    def test_an_unanswerable_lookup_assumes_it_narrows(self, universe):
        """Fails OPEN. A cost gate is not an access gate: refusing a
        legitimate report because a 60ms lookup hiccuped is the worse
        failure, and the concurrency cap still bounds the damage."""
        assert _narrowing('brand', ['AKRIS'], universe) is True


class TestBroadFilterDoesNotUnlockTheLongRange:
    def test_every_department_is_refused(self):
        with pytest.raises(QueryError) as e:
            parse_query(q(1095, filters={'department': UNIVERSE['department']}),
                        stub_universe())
        assert e.value.code == 'rangeFilterTooBroad'

    def test_both_fp_and_md_is_refused(self):
        """Two checkboxes — the easiest way to buy five years for free."""
        with pytest.raises(QueryError) as e:
            parse_query(q(1095, filters={'fpMd': ['FP', 'MD']}), stub_universe())
        assert e.value.code == 'rangeFilterTooBroad'

    def test_one_of_two_still_unlocks_it(self):
        parse_query(q(1095, filters={'fpMd': ['FP']}), stub_universe())

    def test_the_error_names_the_broad_filter(self):
        with pytest.raises(QueryError) as e:
            parse_query(q(1095, filters={'fpMd': ['FP', 'MD']}), stub_universe())
        msg = str(e.value)
        assert 'fpMd' in msg and 'selects every option' in msg

    def test_a_narrowing_filter_rescues_a_broad_one(self):
        """All departments AND one brand is still cheap — the brand does
        the work. One narrowing filter is all the cap ever asked for."""
        parse_query(q(1095, filters={'department': UNIVERSE['department'],
                                     'brand': ['AKRIS']}), stub_universe())

    def test_all_broad_together_is_still_refused(self):
        with pytest.raises(QueryError) as e:
            parse_query(q(1095, filters={'fpMd': ['FP', 'MD'],
                                         'department': UNIVERSE['department']}),
                        stub_universe())
        assert e.value.code == 'rangeFilterTooBroad'
        # Plural phrasing, and both named: "the filterS ... EACH select".
        msg = str(e.value)
        assert 'the filters on department, fpMd each select' in msg

    def test_inside_the_plain_cap_a_broad_filter_is_fine(self):
        """Nothing that worked before fails. A year of everything was
        always allowed and still is — and the lookup is never consulted,
        which is why the common request pays nothing."""
        asked = []

        def spy(dim):
            asked.append(dim)
            return UNIVERSE.get(dim)

        parse_query(q(MAX_RANGE_DAYS, filters={'fpMd': ['FP', 'MD']}), spy)
        assert asked == []

    def test_the_lookup_is_only_asked_about_pushable_dims(self):
        asked = []

        def spy(dim):
            asked.append(dim)
            return UNIVERSE.get(dim)

        parse_query(q(1095, filters={'brand': ['AKRIS'], 'size': ['M']}), spy)
        assert asked == ['brand']          # not `size` — it never pushes

    def test_without_a_lookup_behaviour_is_unchanged(self):
        """`parse_query` stays pure when given no universe: the 46 tests
        above it, and every other caller, are unaffected."""
        parse_query(q(1095, filters={'fpMd': ['FP', 'MD']}))

    def test_an_unanswerable_lookup_lets_the_request_through(self):
        parse_query(q(1095, filters={'fpMd': ['FP', 'MD']}),
                    stub_universe({}))

    def test_a_broad_filter_is_still_applied_to_the_query(self):
        """Refused for the LONG range only. Inside a year the filter is
        kept and pushed — being unselective is not being invalid."""
        query = parse_query(q(MAX_RANGE_DAYS,
                              filters={'fpMd': ['FP', 'MD']}), stub_universe())
        assert query['filters'] == {'fpMd': ['FP', 'MD']}
        assert split_filters(query['filters'])[0] == {'fpMd': ['FP', 'MD']}


class TestUniverseLookup:
    def test_it_opens_no_connection_until_asked(self, monkeypatch):
        """The property that makes this affordable: an ordinary request
        validates without touching Oracle at all."""
        opened = []
        monkeypatch.setattr('app.modules.custom_reports.engine.get_oracle_connection',
                            lambda: opened.append(1))
        with universe_lookup():
            pass
        assert opened == []

    def test_a_fixed_dimension_needs_no_connection(self, monkeypatch):
        """`fpMd` is a constant in the module — the likeliest broad
        filter is also the one that costs nothing to catch."""
        opened = []
        monkeypatch.setattr('app.modules.custom_reports.engine.get_oracle_connection',
                            lambda: opened.append(1))
        with universe_lookup() as universe_of:
            assert universe_of('fpMd') == ['FP', 'MD']
        assert opened == []

    def test_no_connection_answers_unknown(self, monkeypatch):
        monkeypatch.setattr('app.modules.custom_reports.engine.get_oracle_connection',
                            lambda: None)
        with universe_lookup() as universe_of:
            assert universe_of('brand') is None

    def test_a_failing_query_answers_unknown_and_does_not_raise(self, monkeypatch):
        """A validation path must not turn an Oracle blip into a 500 on a
        request that would otherwise have been a clean 400."""
        monkeypatch.setattr('app.modules.custom_reports.engine.get_oracle_connection',
                            lambda: _FakeConn(fail=True))
        with universe_lookup() as universe_of:
            assert universe_of('brand') is None

    def test_each_dimension_is_queried_once(self, monkeypatch):
        conn = _FakeConn()
        monkeypatch.setattr('app.modules.custom_reports.engine.get_oracle_connection',
                            lambda: conn)
        with universe_lookup() as universe_of:
            universe_of('brand')
            universe_of('brand')
            universe_of('store')
        assert conn.cursors == 2        # one per DIMENSION, not per call

    def test_one_connection_is_shared_and_then_closed(self, monkeypatch):
        conn = _FakeConn()
        opened = []
        monkeypatch.setattr('app.modules.custom_reports.engine.get_oracle_connection',
                            lambda: (opened.append(1), conn)[1])
        with universe_lookup() as universe_of:
            universe_of('brand')
            universe_of('store')
            assert conn.closed is False
        assert opened == [1]
        assert conn.closed is True

    def test_the_connection_is_closed_even_if_the_caller_raises(self, monkeypatch):
        conn = _FakeConn()
        monkeypatch.setattr('app.modules.custom_reports.engine.get_oracle_connection',
                            lambda: conn)
        with pytest.raises(QueryError):
            with universe_lookup() as universe_of:
                universe_of('brand')
                raise QueryError('boom')
        assert conn.closed is True


class _FakeCursor:
    def __init__(self, fail):
        self.fail = fail
        self.closed = False

    def execute(self, sql, binds=None):
        if self.fail:
            raise RuntimeError('ORA-00942')

    def fetchall(self):
        return [('AKRIS',), ('NANIS',)]

    def close(self):
        self.closed = True


class _FakeConn:
    def __init__(self, fail=False):
        self.fail = fail
        self.closed = False
        self.cursors = 0
        self.handed = []

    def cursor(self):
        self.cursors += 1
        cur = _FakeCursor(self.fail)
        self.handed.append(cur)
        return cur

    def close(self):
        self.closed = True
