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
    DIMENSIONS, MIN_FROM_DATE, PUSHDOWN_EXCLUDED, UNKNOWN, QueryError,
    _where_for, matches, parse_query, split_filters,
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
# The period: a floor date, no length cap (0.9.0)
# ----------------------------------------------------------------------
# Until 0.9.0 a period was capped at 366 days, or 1830 with a filter SQL
# could push and that excluded something. Now the only rule is that it
# starts on or after `MIN_FROM_DATE`. Kept in this file because the old
# cap's tests were here: the point of several below is that a filter no
# longer changes the answer.
BASE = {'subject': 'sales', 'groupBy': []}


def q(start, end, **over):
    body = dict(BASE, **{'from': start, 'to': end})
    body.update(over)
    return body


class TestDateFloor:
    def test_the_floor_is_the_start_of_the_data(self):
        assert MIN_FROM_DATE == dt.date(2024, 1, 1)

    def test_starting_on_the_floor_is_allowed(self):
        parse_query(q('2024-01-01', '2024-01-31'))

    def test_starting_the_day_before_is_refused(self):
        with pytest.raises(QueryError) as e:
            parse_query(q('2023-12-31', '2024-01-31'))
        assert e.value.code == 'rangeTooEarly'
        assert '2024-01-01' in str(e.value)

    def test_the_whole_history_unfiltered_is_allowed(self):
        """The change itself: no filter, longer than a year — and longer
        than the five years the old filtered cap allowed."""
        parse_query(q('2024-01-01', '2026-11-22'))
        parse_query(q('2024-01-01', '2030-12-31'))

    @pytest.mark.parametrize('filters', [
        {},
        {'brand': ['AKRIS']},
        {'fpMd': ['FP', 'MD']},          # selects everything: once refused
        {'size': ['M']},                 # Python-only: once refused
        {'brand': []},
    ])
    def test_filters_no_longer_decide_the_length(self, filters):
        parse_query(q('2024-01-01', '2026-11-22', filters=filters))

    @pytest.mark.parametrize('filters', [{}, {'brand': ['AKRIS']}])
    def test_a_filter_does_not_lift_the_floor(self, filters):
        with pytest.raises(QueryError) as e:
            parse_query(q('2023-06-01', '2023-06-30', filters=filters))
        assert e.value.code == 'rangeTooEarly'

    def test_the_filters_are_validated_before_the_floor(self):
        """A bad filter is still reported as a bad filter, not as a date."""
        with pytest.raises(QueryError, match='cannot be filtered'):
            parse_query(q('2023-01-01', '2023-01-31', filters={'alu': ['X']}))

    def test_an_inverted_range_is_still_refused_first(self):
        with pytest.raises(QueryError, match='to must not be before from'):
            parse_query(q('2023-02-01', '2023-01-01'))

    def test_a_filter_is_kept_and_pushed_on_a_long_period(self):
        query = parse_query(q('2024-01-01', '2026-11-22',
                              filters={'fpMd': ['FP', 'MD']}))
        assert query['filters'] == {'fpMd': ['FP', 'MD']}
        assert split_filters(query['filters'])[0] == {'fpMd': ['FP', 'MD']}

    def test_validation_touches_no_database(self, monkeypatch):
        """The old cap opened an Oracle connection to judge a filter; the
        floor is a date comparison and must stay one."""
        def boom():
            raise AssertionError('parse_query opened a connection')
        monkeypatch.setattr('app.modules.custom_reports.engine.get_oracle_connection', boom)
        parse_query(q('2024-01-01', '2026-11-22', filters={'brand': ['AKRIS']}))
