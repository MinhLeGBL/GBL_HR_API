"""
The custom-reports QUERY ENGINE.

Not a report. One engine over three subjects — sales, imports,
sell-through — driven by a date range, a filter set, and a user-chosen
group-by of up to three dimensions. Roughly a dozen of `scripts/reports/`'s
one-off generators are this same query with the filters and grouping
hardcoded.

⚠️ THE SPECIFICATION LIVES IN THE FRONTEND.
`GBL_MASTER_FRONTEND/src/features/custom-reports/mocks/engine.ts` is an
executable spec that 868 frontend tests run against, written BEFORE this
module at the user's instruction so the UI could be tried before the
contract was fixed. Where this file and that one disagree, THAT ONE IS
RIGHT. The structure below deliberately mirrors it function for function
(`facts_for` / `factsFor`, `value_of` / `valueOf`, `matches`, `accumulate`,
`measure_value`, `notes_for`) so a reader can hold them side by side.

The request contract is in
`GBL_MASTER_FRONTEND/docs/cr/custom_reports.md`.
"""
import datetime as dt
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core.database import get_oracle_connection
from .queries import EngineQueries
from .service import normalize_size


SUBJECTS = ('sales', 'imports', 'sellThrough')

# Shown in place of an empty source value. NEVER blank: a blank cell reads
# as a rendering fault, while "unknown" is a fact about the data. Sorts
# last in a picker list.
UNKNOWN = '—'   # em dash

# How many dimensions may be grouped at once. Mirrors the frontend's
# MAX_GROUP_BY — past three the row count approaches the line count and
# the result is detail wearing a summary's clothes.
MAX_GROUP_BY = 3

# The picker enforces these spans; enforced here too, because a client is
# not a validator.
#
# TWO caps, because the cost of a range depends almost entirely on how
# much of it a filter throws away. Measured on production 2026-11-09,
# COUNT(*) over the SALE_LINES joins:
#
#     1 year, unfiltered           6.34s    16,321 rows
#     1 year, brand = AKRIS        0.25s       192 rows
#     3 years, unfiltered         20.62s    48,097 rows
#     3 years, brand = AKRIS       0.53s       555 rows
#
# So a multi-year single-brand report is CHEAPER than the one-year
# unfiltered query the old single cap already allowed, while a multi-year
# unfiltered one is ~28s end to end and would hold a gunicorn worker (of
# three) for that long. The honest limit is therefore not "days" but "how
# many lines will this pull", and a pushable filter is the proxy for it.
#
# ⚠️ IT IS ONLY A PROXY, and a coarse one: the gate asks whether a
# pushable filter EXISTS, not how much it removes. A broad one barely
# helps — measured the same day, three years of `department = RTW` (about
# 79% of all lines) is 14.99s, against 2.13s for three years of one
# brand. Still inside the 120s worker timeout, and the route's
# concurrency cap is what keeps even that from starving the API. Judging
# selectivity properly would mean counting rows before deciding, and the
# COUNT is most of the query — so the cheap proxy is the right trade.
MAX_RANGE_DAYS = 366
MAX_RANGE_DAYS_FILTERED = 1830          # five years

# Filters that can be applied in SQL instead of in Python — see
# `_where_for`. A dimension qualifies only when the column and the value
# `value_of` produces agree EXACTLY, because `matches()` still handles the
# remainder and the two must never disagree about the same row.
#
# Three are held back:
#
#   · `size`         — `field: None`. Normalised per brand by
#                      `normalize_size()`, a Python table with no SQL
#                      equivalent.
#   · `customerType` — `field: None`. Derived, and RELATIVE TO THE
#                      PERIOD, so no column holds it.
#   · `customer`     — HAS a column, and is still excluded: `_derive`
#                      rewrites it to None for walk-ins and for the
#                      TOURIST aggregation record, so a SQL filter would
#                      keep rows `matches()` drops.
PUSHDOWN_EXCLUDED = ('size', 'customerType', 'customer')


# ----------------------------------------------------------------------
# Dimensions
# ----------------------------------------------------------------------
# `subjects` is NOT cosmetic and a dimension absent from one is REJECTED,
# not ignored. VOUCHER / VOU_ITEM carries no customer and no discount, so
# a customer or FP/MD filter on imports is a nonsense query rather than an
# empty one; and sell-through is sold / received, so filtering only the
# numerator returns a WRONG ratio rather than a narrower one. Silently
# dropping the clause would answer a different question under the label of
# the one that was asked.
#
# `field` is the column the pull returns (see queries.py). Two dimensions
# have no column because they cannot be expressed in SQL at all:
#   · `size`         — normalised per brand in Python
#   · `customerType` — new vs returning RELATIVE TO THE PERIOD
# Both are computed in `_derive`.
_ALL = ('sales', 'imports', 'sellThrough')
_PRODUCT = ('sales', 'imports', 'sellThrough')

DIMENSIONS: Dict[str, Dict[str, Any]] = {
    # Detail-mode column only — never a filter or a grouping. Grouping by
    # a raw date is what `month` is for.
    'date':         {'field': 'line_date',     'subjects': _ALL,
                     'groupable': False, 'filterable': False},
    'month':        {'field': None,            'subjects': _ALL,
                     'groupable': True,  'filterable': False},
    'bill':         {'field': 'bill',          'subjects': ('sales',),
                     'groupable': True,  'filterable': False, 'kind': 'search'},
    'receiving':    {'field': 'receiving',
                     'subjects': ('imports', 'sellThrough'),
                     'groupable': True,  'filterable': False, 'kind': 'search'},
    'store':        {'field': 'store',         'subjects': ('sales', 'imports'),
                     'groupable': True,  'filterable': True},
    'alu':          {'field': 'alu',           'subjects': _PRODUCT,
                     'groupable': True,  'filterable': False, 'kind': 'search'},
    'brand':        {'field': 'brand',         'subjects': _ALL,
                     'groupable': True,  'filterable': True},
    'description':  {'field': 'description',   'subjects': _PRODUCT,
                     'groupable': True,  'filterable': False, 'kind': 'search'},
    'season':       {'field': 'season',        'subjects': _PRODUCT,
                     'groupable': True,  'filterable': True},
    'gender':       {'field': 'gender',        'subjects': _PRODUCT,
                     'groupable': True,  'filterable': True},
    'department':   {'field': 'department',    'subjects': _PRODUCT,
                     'groupable': True,  'filterable': True},
    'category':     {'field': 'category',      'subjects': _PRODUCT,
                     'groupable': True,  'filterable': True},
    'size':         {'field': None,            'subjects': _PRODUCT,
                     'groupable': True,  'filterable': True,
                     'caveat': 'caveatSizeNormalised'},
    'fpMd':         {'field': 'fp_md',         'subjects': ('sales',),
                     'groupable': True,  'filterable': True},
    'customer':     {'field': 'customer',      'subjects': ('sales',),
                     'groupable': True,  'filterable': True},
    'customerType': {'field': None,            'subjects': ('sales',),
                     'groupable': True,  'filterable': True,
                     'caveat': 'caveatCustomerType'},
    'customerTier': {'field': 'customer_tier', 'subjects': ('sales',),
                     'groupable': True,  'filterable': True,
                     'caveat': 'caveatTier'},
}

# Which dimensions a LINE-DETAIL row shows, per subject, in order.
# Mirrors `detailDimensions()` in the TS engine.
#
# The date leads where there is one date to show, then the document
# identity — what a reader scans for when checking one row against the POS
# or a supplier invoice — then the place, then the product.
_PRODUCT_DIMS = ('alu', 'brand', 'description', 'season', 'gender',
                 'department', 'category', 'size')

DETAIL_DIMENSIONS = {
    'sales': ('date', 'bill', 'store') + _PRODUCT_DIMS
             + ('fpMd', 'customer', 'customerTier'),
    'imports': ('date', 'receiving', 'store') + _PRODUCT_DIMS,
    # ⚠️ Sell-through carries NEITHER a date NOR a store, both on the
    # user's instruction (2026-11-05) and both for the same reason: a row
    # here spans two events, received then sold, so a single value could
    # only describe one of them and would be read as the other. The
    # receipt's store is HQ (1,030 of 1,041 receipts) while the sale
    # happened in an outlet.
    'sellThrough': ('receiving',) + _PRODUCT_DIMS,
}


# ----------------------------------------------------------------------
# Measures
# ----------------------------------------------------------------------
# Every figure the engine can produce. Placement — which report shows
# which, in which state of the Detail toggle — is the FRONTEND's business
# (`MEASURE_COLUMNS` in constants.ts); the query returns the full `detail`
# set for the subject every time, so the toggle is a projection over a
# result already in hand and nothing is re-queried.
MEASURES: Dict[str, str] = {
    'bills': 'int',           'avgBill': 'money',      'asp': 'money',
    'netSales': 'money',      'fpSalesShare': 'pct',   'fpSales': 'money',
    'mdSales': 'money',       'units': 'int',          'fpShare': 'pct',
    'fpUnits': 'int',         'mdUnits': 'int',        'receivedUnits': 'int',
    'receivedCost': 'money',  'soldUnits': 'int',      'sellThroughPct': 'pct',
    'onHandUnits': 'int',     'returnValue': 'money',  'returnedUnits': 'int',
}

# Must match `MEASURE_COLUMNS[subject].detail` in the frontend's
# constants.ts exactly, including ORDER — the array order IS the display
# order and the client does not sort it.
GROUPED_MEASURES = {
    'sales': ('bills', 'avgBill', 'asp', 'netSales', 'fpSalesShare',
              'fpSales', 'mdSales', 'returnValue',
              'units', 'fpShare', 'fpUnits', 'mdUnits', 'returnedUnits'),
    'imports': ('receivedUnits', 'receivedCost'),
    'sellThrough': ('sellThroughPct', 'receivedUnits', 'soldUnits',
                    'onHandUnits'),
}

# `MEASURE_COLUMNS[subject].lines` — the measures that mean something on
# ONE transaction line. A derived rate over one line is 100% or 0% and an
# average bill over one bill is just the bill, so those belong to grouped
# rows only — EXCEPT `sellThroughPct`, which does belong on a line here: a
# sell-through line IS a receipt line ("8 units landed on this receipt")
# and its rate is how many of those 8 sold.
LINE_MEASURES = {
    'sales': ('units', 'netSales', 'returnValue', 'returnedUnits'),
    'imports': ('receivedUnits', 'receivedCost'),
    'sellThrough': ('sellThroughPct', 'receivedUnits', 'soldUnits'),
}


class QueryError(ValueError):
    """A bad request. Carries the message the route returns as a 400.

    `code` is optional and exists for the few errors the FRONTEND needs to
    render in the user's own language. The message stays English prose and
    stays the contract — the code is an additional hint, so an unknown or
    absent one simply falls back to showing the message. Most validation
    errors name a dimension or a field and read fine untranslated; these
    two are the ones a user meets while learning the feature rather than
    while misusing the API.
    """

    def __init__(self, message: str, code: str = None):
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------------
# Request validation
# ----------------------------------------------------------------------
def parse_query(body: Any) -> Dict[str, Any]:
    """Validate a request body into a normalised query dict, or raise.

    Everything is checked here rather than trusted from the client — the
    picker enforces the one-year cap and the three-dimension limit, but a
    client is not a validator.
    """
    if not isinstance(body, dict):
        raise QueryError('request body must be an object')

    subject = body.get('subject')
    if subject not in SUBJECTS:
        raise QueryError(
            f"subject must be one of {', '.join(SUBJECTS)}")

    def _date(key):
        raw = body.get(key)
        if not isinstance(raw, str):
            raise QueryError(f'{key} is required (yyyy-mm-dd)')
        try:
            return dt.date.fromisoformat(raw)
        except ValueError:
            raise QueryError(f'{key} must be an ISO date (yyyy-mm-dd)')

    date_from, date_to = _date('from'), _date('to')
    if date_to < date_from:
        raise QueryError('to must not be before from')

    group_by = body.get('groupBy') or []
    if not isinstance(group_by, list):
        raise QueryError('groupBy must be an array')
    if len(group_by) > MAX_GROUP_BY:
        raise QueryError(f'groupBy accepts at most {MAX_GROUP_BY} dimensions')
    if len(set(group_by)) != len(group_by):
        raise QueryError('groupBy must not repeat a dimension')
    for dim in group_by:
        meta = DIMENSIONS.get(dim)
        if meta is None:
            raise QueryError(f'unknown dimension: {dim}')
        if not meta['groupable']:
            raise QueryError(f'{dim} cannot be grouped')
        # Rejected, not ignored — see the DIMENSIONS note.
        if subject not in meta['subjects']:
            raise QueryError(f'{dim} does not apply to {subject}')

    filters_raw = body.get('filters') or {}
    if not isinstance(filters_raw, dict):
        raise QueryError('filters must be an object')
    filters: Dict[str, List[str]] = {}
    for dim, picked in filters_raw.items():
        if not isinstance(picked, list):
            raise QueryError(f'filters.{dim} must be an array of strings')
        if not picked:
            continue        # empty array = no filter, same as absent
        meta = DIMENSIONS.get(dim)
        if meta is None:
            raise QueryError(f'unknown dimension: {dim}')
        if not meta.get('filterable'):
            raise QueryError(f'{dim} cannot be filtered')
        if subject not in meta['subjects']:
            raise QueryError(f'{dim} does not apply to {subject}')
        filters[dim] = [str(v) for v in picked]

    # ⚠️ CHECKED AFTER THE FILTERS, because which cap applies depends on
    # them. A range beyond a year needs at least one PUSHABLE filter —
    # one SQL can actually use to narrow the scan. `size` and
    # `customerType` do not count however selective they look: they are
    # applied in Python, after the full pull, so they would license the
    # long range without making it any cheaper. `customer` is excluded
    # for the same reason (see `PUSHDOWN_EXCLUDED`).
    span = (date_to - date_from).days + 1
    pushable, _ = split_filters(filters)
    cap = MAX_RANGE_DAYS_FILTERED if pushable else MAX_RANGE_DAYS
    if span > cap:
        if pushable:
            raise QueryError(
                f'date range must not exceed {cap} days', 'rangeTooLong')
        raise QueryError(
            f'date range must not exceed {cap} days without a filter on '
            f'{", ".join(_pushable_dims(subject))} — '
            f'a longer range is allowed once one of those narrows it',
            'rangeNeedsFilter')

    return {
        'subject': subject,
        'from': date_from,
        'to': date_to,
        'filters': filters,
        'groupBy': list(group_by),
    }


def _pushable_dims(subject: str) -> List[str]:
    """The filters that unlock the long range, for this subject."""
    return sorted(
        dim for dim, meta in DIMENSIONS.items()
        if meta.get('filterable') and meta['field']
        and dim not in PUSHDOWN_EXCLUDED and subject in meta['subjects']
    )


# ----------------------------------------------------------------------
# Facts
# ----------------------------------------------------------------------
# A "fact" is one normalised row the grouping machinery works over,
# whichever subject produced it — mirroring the `Fact` interface in the TS
# engine. Receipts and sales are separate events with separate dates, so
# each subject flattens its own source into this shape and everything
# downstream stays single-pathed. A branch per subject through the
# grouping code is how the two header-less Imports columns got shipped on
# the frontend.
_MEASURE_FIELDS = ('units', 'netSales', 'receivedUnits', 'receivedCost',
                   'soldUnits', 'returnedUnits', 'returnValue')


def _blank_fact() -> Dict[str, Any]:
    f = {k: 0 for k in _MEASURE_FIELDS}
    f['billId'] = None
    return f


def _derive(row: Dict[str, Any], query: Dict[str, Any],
            first_buy: Optional[Dict[Any, str]] = None) -> None:
    """Fill the two dimensions SQL cannot express. Mutates `row`.

    `first_buy` maps customer sid -> ISO date of their first purchase
    ever. It is None whenever `customerType` was not asked for, in which
    case every identified customer falls to 'returning' — a value no
    column shows and no filter reads, because asking for either is what
    makes the lookup run. See `facts_for`.
    """
    # Normalised size. `normalize_size` needs the brand's sizing system
    # and the department, and falls through to the raw label so an
    # unmapped size surfaces as its own bucket rather than disappearing
    # into a wrong one.
    #
    # ⚠️ It is given `dcs_code`, the RAW DCS name, NOT the split
    # `department`. Its shoe pass-through keys on 'WSHO' / 'MSHO', so the
    # gender-stripped 'SHO' misses it and a size-38 shoe is translated as
    # clothing: normalize_size('38', 'AKRIS', 'SHO') == 'M', against
    # normalize_size('38', 'AKRIS', 'WSHO') == '38'.
    #
    # A one-size label ('OS', 'U', 'TU', 'ONE SIZE', …) returns '' and so
    # lands in the UNKNOWN bucket, which is the honest answer: a scarf has
    # no size. `/sizes` drops those rows entirely; this engine keeps them,
    # because Sales grouped by size must still account for every unit.
    raw_size = row.get('item_size')
    brand = row.get('brand') or ''
    dcs = row.get('dcs_code') or row.get('department') or ''
    row['size'] = normalize_size(raw_size, brand.strip(), dcs) if raw_size else ''

    # New vs returning vs walk-in, RELATIVE TO THE PERIOD: a customer is
    # "new" when their first purchase EVER falls inside it. Widening the
    # period reclassifies people, which is what `caveatCustomerType`
    # discloses.
    #
    # A sale with no customer record is a walk-in, and so is the POS
    # TOURIST aggregation record — one synthetic customer standing for
    # many anonymous walk-ins, which is why it must not be counted as a
    # returning individual. Same treatment as `reports`' tourist bucket.
    name = (row.get('customer') or '').strip().upper()
    if not row.get('cust_sid') or name in ('TOURIST', 'TOURIST.', ''):
        row['customerType'] = 'walkIn'
        row['customer'] = None
    else:
        first = (first_buy or {}).get(row.get('cust_sid'))
        iso_from = query['from'].isoformat()
        iso_to = query['to'].isoformat()
        row['customerType'] = (
            'new' if first and iso_from <= first <= iso_to else 'returning')


def _sale_fact(row: Dict[str, Any]) -> Dict[str, Any]:
    """A sale line, or a return line.

    ⚠️ A RETURN CONTRIBUTES NOTHING TO `units` / `netSales`. It is carried
    in `returnedUnits` / `returnValue` instead, as POSITIVE values, and
    the presentation decides whether to subtract them.

    This is the `weekly_report` shape, on the user's call. Four
    conventions exist in this backend — `reports` nets returns into
    revenue but NOT units, `commission` and `account_payable` net both,
    `crm` and `custom_reports/sizes` exclude them outright — so there is
    no single "production behaviour" to match. Carrying both sides lets
    one result reconcile against whichever screen it is read beside.

    Keeping returns OUT of `units` / `netSales` is also what preserves the
    reconciliation the shares depend on: `fpSales + mdSales == netSales`
    and `fpUnits + mdUnits == units` hold only over the sale-only
    population, because a return has no discount rate and so no FP/MD
    side. Measured on the mock's full range, netting returns into
    `netSales` while leaving the components sale-only puts
    `fpSales + mdSales` at up to 107.1% of the total, on 13 of 14 brands.
    """
    f = _blank_fact()
    f.update(row)
    f['billId'] = row.get('bill_id')
    qty = int(row.get('qty') or 0)
    net = float(row.get('net_sales') or 0.0)
    if int(row.get('item_type') or 1) == 2:
        f['returnedUnits'] = qty
        f['returnValue'] = net
    else:
        f['units'] = qty
        f['netSales'] = net
    return f


def _receipt_fact(row: Dict[str, Any]) -> Dict[str, Any]:
    f = _blank_fact()
    f.update(row)
    f['receivedUnits'] = int(row.get('received_units') or 0)
    f['receivedCost'] = float(row.get('received_cost') or 0.0)
    return f


# Rows fetched per round trip. The default (100 in python-oracledb) made
# a 12,802-row pull 8.93s; at 5,000 it is 5.12s, against 4.91s for a bare
# COUNT(*) over the same joins — so the transfer is now ~0.2s and what is
# left is the query itself. Measured from this Mac THROUGH THE SSH TUNNEL;
# on the server the round trips are cheaper, so this is an upper bound.
_ARRAYSIZE = 5000


def _fetch(cur, sql: str, binds: Dict[str, Any],
           where: str = '', where_binds: Dict[str, Any] = None
           ) -> List[Dict[str, Any]]:
    """Run one pull, optionally narrowed by a pushed-down filter.

    The predicate wraps the query as a subquery rather than being spliced
    into it. That is deliberate: the inner SQL carries a lot of
    load-bearing scope (`is_held`, `receipt_type`, `status`, the season
    list) documented at length in `queries.py`, and editing it to thread
    a WHERE through would put all of that at risk for no gain. Oracle
    pushes the outer predicate down through the view anyway — the 25x
    measured in `MAX_RANGE_DAYS` was measured in exactly this form.
    """
    if where:
        sql = f'SELECT * FROM ({sql}) WHERE {where}'
        binds = {**binds, **(where_binds or {})}
    cur.arraysize = _ARRAYSIZE
    cur.execute(sql, binds)
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _first_buy(cur, query: Dict[str, Any]) -> Optional[Dict[Any, str]]:
    """Each customer's first purchase ever — but ONLY when the query
    actually needs it.

    ⚠️ This lookup is the difference between a 10-second query and a
    35-second one (see `CUSTOMER_FIRST_BUY` for the measurements), and
    most queries never touch `customerType`. Skipping it when unasked is
    therefore the single largest saving in the engine, and it is safe
    because the value it feeds is unreachable without asking: a dimension
    that is neither grouped nor filtered appears in no column, in no
    group key, and in no filter test. Line detail is the one case that
    shows a dimension without grouping by it — and `customerType` is not
    among the Sales detail columns.
    """
    needed = ('customerType' in query['groupBy']
              or 'customerType' in query['filters'])
    if not needed:
        return None
    return {r['cust_sid']: r['first_buy']
            for r in _fetch(cur, EngineQueries.CUSTOMER_FIRST_BUY, {})}


def facts_for(cur, query: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The rows one subject answers over, with the DATE RANGE already
    applied — because WHICH date it applies to is the whole difference
    between them. Sales: the sale date. Imports: the receipt date.
    """
    binds = {
        'from_date': query['from'],
        # `to` is inclusive of the whole day, so the SQL bound is
        # exclusive of the day after.
        'to_exclusive': query['to'] + dt.timedelta(days=1),
    }
    subject = query['subject']

    # The SQL half of the filters. `parse_query` has already rejected any
    # dimension that does not apply to this subject, so every pushable
    # field is guaranteed to exist in the query being narrowed:
    # `fpMd` / `customerTier` are sales-only, `store` is absent from
    # sellThrough, and the rest come from the shared `_PRODUCT_COLS`.
    where, where_binds = _where_for(split_filters(query['filters'])[0])

    if subject == 'sales':
        rows = _fetch(cur, EngineQueries.SALE_LINES, binds, where, where_binds)
        first_buy = _first_buy(cur, query)
        for r in rows:
            _derive(r, query, first_buy)
        return [_sale_fact(r) for r in rows]

    receipts = _fetch(cur, EngineQueries.RECEIPT_LINES, binds,
                      where, where_binds)
    for r in receipts:
        _derive(r, query)

    if subject == 'imports':
        # Imports carry no returns — `vou_type = 0` already excludes
        # vendor returns, which is what `/sizes` does for clean FIFO.
        return [_receipt_fact(r) for r in receipts]

    # ⚠️ The SOLD side is NOT narrowed, and must not be: SELLTHROUGH_SOLD
    # selects only `item_sid`, `sale_date` and `qty` — it carries no
    # product columns to filter on. Narrowing the receipts alone is still
    # correct, because the cohort rule already makes receipts the only
    # source of rows: `_sell_through_facts` attributes sales by
    # `item_sid` and emits one row per RECEIPT, so a sold line whose item
    # is not among the filtered receipts finds no bucket and contributes
    # nothing. It costs a wider sold pull than strictly needed — bounded
    # by the same date range, and the receipts side is where the joins
    # are — in exchange for not splitting the FIFO model in two.
    sold = _fetch(cur, EngineQueries.SELLTHROUGH_SOLD, binds)
    return _sell_through_facts(receipts, sold)


def _sell_through_facts(receipts: List[Dict[str, Any]],
                        sold: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attribute in-period sold units back to in-period receipt lines.

    ⚠️ BOTH SIDES ARE BOUNDED BY THE PERIOD (the user's rule, revised the
    same day it was set). The period picks the RECEIPTS, and counts only
    those receipts' own sales that also fall inside the period. Two
    constraints, both load-bearing:

      · the sale must be attributable to an in-period receipt — a sale of
        stock received earlier belongs to a different cohort;
      · the sale must fall INSIDE the period.

    The second was briefly the other way, counting a cohort's sales
    whenever they happened. The user rejected it for consistency: *"it
    would be confusing to have the sale report within the period but sale
    through is from sale between the start of the period until current"*.
    One period meaning two different things across two tabs is a worse
    problem than an imperfect ratio.

    ⚠️ THE COST, which `noteSellThroughCohort` states plainly: stock
    landing late in the period has had almost no time to sell inside it,
    so a SHORT PERIOD READS LOW BY CONSTRUCTION. That is a property of the
    question, not a defect — but it is only safe because it is disclosed.

    ---------------------------------------------------------------
    WHY FIFO, AND WHAT IT COSTS
    ---------------------------------------------------------------
    Retail Pro carries NO link from a sale line to the receipt that
    brought the unit in. (The frontend mock has one only because the mock
    generates both sides.) So the attribution has to be constructed, and
    FIFO per `item_sid` — oldest open receipt line first — is the same
    rule `/sizes` already uses for days-to-sell. It is a MODEL, not a
    fact: two receipts of one SKU in the same period cannot be told
    apart, and the first one absorbs the sales.

    Where it is exact: at the grain the report is read at. Every grouping
    this engine offers is coarser than `item_sid` except `receiving`
    itself, so a per-brand or per-category rate is unaffected by which of
    a SKU's receipt lines a unit was charged to. Grouping BY `receiving`,
    or reading line detail, is where the model shows — and there it is the
    best available answer rather than a true one.
    """
    # Open receipt quantities per item, oldest first.
    by_item: Dict[Any, List[Dict[str, Any]]] = {}
    for r in sorted(receipts, key=lambda r: (r.get('line_date') or '')):
        by_item.setdefault(r['item_sid'], []).append(r)

    remaining = {id(r): int(r.get('received_units') or 0) for r in receipts}
    attributed: Dict[int, int] = {id(r): 0 for r in receipts}

    for s in sorted(sold, key=lambda s: (s.get('sale_date') or '')):
        qty = int(s.get('qty') or 0)
        for r in by_item.get(s['item_sid'], ()):
            if qty <= 0:
                break
            # A unit can only be charged to a receipt that preceded it.
            # Without this, a sale early in the period would be absorbed
            # by stock that landed in December.
            if (r.get('line_date') or '') > (s.get('sale_date') or ''):
                continue
            take = min(qty, remaining[id(r)])
            if take <= 0:
                continue
            remaining[id(r)] -= take
            attributed[id(r)] += take
            qty -= take
        # Any residue is a sale of stock received BEFORE the period. It is
        # dropped, which is the cohort rule: that receipt is not in this
        # result to carry it.

    # Only in-period receipts become rows. THIS is what excludes a sale of
    # stock received before the period.
    out = []
    for r in receipts:
        f = _receipt_fact(r)
        f['soldUnits'] = attributed[id(r)]
        out.append(f)
    return out


# ----------------------------------------------------------------------
# Grouping
# ----------------------------------------------------------------------
def value_of(fact: Dict[str, Any], dim: str) -> str:
    """The group key / cell value a fact takes for a dimension."""
    if dim == 'month':
        return (fact.get('line_date') or '')[:7] or UNKNOWN
    meta = DIMENSIONS[dim]
    field = meta['field'] or dim
    v = fact.get(field)
    if v is None or v == '':
        return UNKNOWN
    return str(v)


def split_filters(filters: Dict[str, List[str]]
                  ) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Split filters into the SQL half and the Python half.

    Returns `(pushable, python_only)`. Deterministic and side-effect
    free, so `facts_for` and `run_query` can each call it rather than
    thread the result between them.
    """
    push: Dict[str, List[str]] = {}
    keep: Dict[str, List[str]] = {}
    for dim, picked in filters.items():
        meta = DIMENSIONS[dim]
        if dim in PUSHDOWN_EXCLUDED or not meta['field']:
            keep[dim] = picked
        else:
            push[dim] = picked
    return push, keep


def _where_for(push: Dict[str, List[str]]) -> Tuple[str, Dict[str, Any]]:
    """A WHERE fragment equivalent to `matches()` for these filters.

    ⚠️ This must agree with `matches()` row for row, so it mirrors
    `value_of`'s two rules rather than inventing its own:

      · a value is compared as the plain string the column holds, which
        is what `str(v)` yields for every pushable field (all of them are
        character columns);
      · NULL and '' both read as `UNKNOWN`, so a picked UNKNOWN becomes
        an IS NULL test, NOT a literal em-dash comparison. Oracle treats
        '' as NULL so the `= ''` half can never fire; it is written out
        anyway because it is the rule `value_of` states, and a reader
        comparing the two functions should not have to know that quirk
        to see that they match.

    Binds are numbered rather than named after the dimension so a value
    can never collide with `from_date` / `to_exclusive`.
    """
    clauses: List[str] = []
    binds: Dict[str, Any] = {}
    for n, (dim, picked) in enumerate(sorted(push.items())):
        field = DIMENSIONS[dim]['field']
        parts: List[str] = []
        concrete = [v for v in picked if v != UNKNOWN]
        if concrete:
            names = []
            for j, v in enumerate(concrete):
                key = f'f{n}_{j}'
                binds[key] = v
                names.append(f':{key}')
            parts.append(f'{field} IN ({", ".join(names)})')
        if len(concrete) != len(picked):
            parts.append(f"({field} IS NULL OR {field} = '')")
        # `parse_query` drops empty lists, so `parts` is never empty.
        clauses.append('(' + ' OR '.join(parts) + ')')
    return ' AND '.join(clauses), binds


def matches(fact: Dict[str, Any], filters: Dict[str, List[str]]) -> bool:
    """Dimension filters only — the date range is applied by `facts_for`,
    on whichever date that subject is keyed to.

    Called with the PYTHON HALF of the filters (see `split_filters`); the
    rest never reaches here because the SQL already dropped those rows.
    Passing the full set still works and is what the tests do — it is
    just redundant work.
    """
    for dim, picked in filters.items():
        value = value_of(fact, dim)
        # A `search` dimension holds ONE free-text query and matches on
        # substring, case-insensitively. Exact-matching a 22,000-value
        # description field would mean typing it perfectly for one row.
        if DIMENSIONS[dim].get('kind') == 'search':
            needle = (picked[0] if picked else '').strip().lower()
            if needle and needle not in value.lower():
                return False
            continue
        if value not in picked:
            return False
    return True


def _empty_bucket(key: Tuple[str, ...]) -> Dict[str, Any]:
    b = {k: 0 for k in ('units', 'netSales', 'fpUnits', 'mdUnits', 'fpSales',
                        'mdSales', 'receivedUnits', 'receivedCost',
                        'soldUnits', 'returnedUnits', 'returnValue')}
    b['key'] = key
    b['bills'] = set()
    return b


def accumulate(b: Dict[str, Any], f: Dict[str, Any]) -> None:
    b['units'] += f['units']
    b['netSales'] += f['netSales']
    b['returnedUnits'] += f['returnedUnits']
    b['returnValue'] += f['returnValue']
    # Only a sale has a bill. Counting receipt rows would make `bills` 1
    # on every Imports bucket. A returns-only document is not a bill
    # either: `bills` counts SALE bills, matching `reports`.
    #
    # ⚠️ CONSEQUENCE, surfaced on purpose: grouping Sales by bill shows a
    # returns-only document as a row with `bills = 0`. It has a bill
    # number so it gets a row, but it is not a SALE bill. Hiding a return
    # would be worse.
    if f.get('billId') and f['units']:
        b['bills'].add(f['billId'])
    # Every SALE line is one side or the other, so the two halves add back
    # to the whole exactly. `f['units']` is already 0 on a return, so a
    # return adds nothing to either side and `fp + md == units` stays
    # exact — which is the invariant the shares are read against.
    if f.get('fp_md') == 'FP':
        b['fpUnits'] += f['units']
        b['fpSales'] += f['netSales']
    else:
        b['mdUnits'] += f['units']
        b['mdSales'] += f['netSales']
    b['receivedUnits'] += f['receivedUnits']
    b['receivedCost'] += f['receivedCost']
    # Sold units of the COHORT, already attributed by `facts_for` — not
    # `f['units']`, which is a sale row's own quantity and is 0 here.
    b['soldUnits'] += f['soldUnits']


def measure_value(mid: str, b: Dict[str, Any]) -> Optional[float]:
    """Derived measures live in ONE place so a column and the totals row
    cannot disagree. A total computed by summing per-row averages is
    wrong, and that is the easy mistake here.

    ⚠️ `None`, NEVER 0, on a zero denominator. "No bills, so no average
    bill" and "an average bill of zero" are different facts, and the
    second one would be a finding. The client renders None as a dash.
    """
    bills = len(b['bills'])
    if mid == 'units':          return b['units']
    if mid == 'netSales':       return round(b['netSales'])
    if mid == 'bills':          return bills
    if mid == 'avgBill':        return round(b['netSales'] / bills) if bills else None
    if mid == 'asp':            return round(b['netSales'] / b['units']) if b['units'] else None
    if mid == 'fpUnits':        return b['fpUnits']
    if mid == 'mdUnits':        return b['mdUnits']
    if mid == 'fpShare':        return b['fpUnits'] / b['units'] if b['units'] else None
    if mid == 'fpSales':        return round(b['fpSales'])
    if mid == 'mdSales':        return round(b['mdSales'])
    if mid == 'fpSalesShare':   return b['fpSales'] / b['netSales'] if b['netSales'] else None
    if mid == 'receivedUnits':  return b['receivedUnits']
    if mid == 'receivedCost':   return round(b['receivedCost'])
    if mid == 'soldUnits':      return b['soldUnits']
    if mid == 'sellThroughPct':
        return b['soldUnits'] / b['receivedUnits'] if b['receivedUnits'] else None
    if mid == 'onHandUnits':    return max(0, b['receivedUnits'] - b['soldUnits'])
    if mid == 'returnedUnits':  return b['returnedUnits']
    if mid == 'returnValue':    return round(b['returnValue'])
    return None


def notes_for(query: Dict[str, Any], has_returns: bool) -> List[str]:
    """i18n KEYS, not sentences — the client holds the EN/VI text.

    Caveats that depend on what was actually asked, not on the page as a
    whole. An unknown key renders as nothing on the client, so a new one
    needs a CR before it is raised here.
    """
    notes: List[str] = []
    # Always, on this subject. Two things a reader cannot infer from the
    # screen: that a sale of stock received before the period is excluded,
    # and that goods landing late in the period have had almost no time to
    # sell inside it.
    if query['subject'] == 'sellThrough':
        notes.append('noteSellThroughCohort')
    # Said only when TRUE OF THIS RESULT: a standing warning about returns
    # on a period that had none is noise.
    if query['subject'] == 'sales' and has_returns:
        notes.append('noteReturnsNotNetted')
    # Derived from the dimension definitions rather than a hardcoded list,
    # so a new caveat is one field — and cannot be added without firing.
    used = list(query['groupBy']) + list(query['filters'].keys())
    for dim in used:
        key = DIMENSIONS.get(dim, {}).get('caveat')
        if key and key not in notes:
            notes.append(key)
    if not query['groupBy']:
        notes.append('noteDetailMode')
    return notes


# ----------------------------------------------------------------------
# The entrypoint
# ----------------------------------------------------------------------
def run_query(query: Dict[str, Any],
              dim_label, measure_label) -> Dict[str, Any]:
    """Run a validated query. `query` comes from `parse_query`.

    `dim_label` / `measure_label` turn an id into the header text. The
    route passes the id straight through — the frontend holds the
    translations and relabels every column itself — but they are
    parameters rather than hardcoded so a CSV export or a scheduled job
    can supply real labels.
    """
    t0 = time.perf_counter()
    conn = get_oracle_connection()
    if conn is None:
        return {'success': False, 'error': 'database unavailable'}
    try:
        cur = conn.cursor()
        try:
            facts = facts_for(cur, query)
        finally:
            cur.close()
    finally:
        conn.close()

    subject = query['subject']
    # Only the half SQL could not do. The pushed-down half was applied by
    # `facts_for`, and re-testing it here would be pure waste on the
    # hottest loop in the engine.
    rows = [f for f in facts if matches(f, split_filters(query['filters'])[1])]

    # Totals ALWAYS span the whole filtered result. The engine holds every
    # matching row and the client pages in the browser, so paging never
    # changes this number — which is the point of querying once. A
    # page-only total would be a different number wearing the same label.
    grand = _empty_bucket(())
    for f in rows:
        accumulate(grand, f)

    if not query['groupBy']:
        dims = DETAIL_DIMENSIONS[subject]
        shown = LINE_MEASURES[subject]
        columns = (
            [{'id': d, 'label': dim_label(d), 'kind': 'dimension',
              'role': 'attribute'} for d in dims]
            + [{'id': m, 'label': measure_label(m), 'kind': 'measure',
                'format': MEASURES[m]} for m in shown]
        )
        out = []
        for f in rows:
            b = _empty_bucket(())
            accumulate(b, f)
            out.append([value_of(f, d) for d in dims]
                       + [measure_value(m, b) for m in shown])
    else:
        buckets: Dict[Tuple[str, ...], Dict[str, Any]] = {}
        for f in rows:
            key = tuple(value_of(f, d) for d in query['groupBy'])
            b = buckets.get(key)
            if b is None:
                b = buckets[key] = _empty_bucket(key)
            accumulate(b, f)
        measures = GROUPED_MEASURES[subject]
        columns = (
            # A grouped dimension is the row's IDENTITY, so it is tagged
            # `key` and the Detail toggle must never hide it. Hiding a key
            # leaves rows that cannot be told apart.
            [{'id': d, 'label': dim_label(d), 'kind': 'dimension',
              'role': 'key'} for d in query['groupBy']]
            + [{'id': m, 'label': measure_label(m), 'kind': 'measure',
                'format': MEASURES[m]} for m in measures]
        )
        ordered = sorted(buckets.values(), key=_sort_key(subject), reverse=True)
        out = [list(b['key']) + [measure_value(m, b) for m in measures]
               for b in ordered]

    totals = [measure_value(c['id'], grand) if c['kind'] == 'measure' else None
              for c in columns]

    return {
        'success': True,
        'result': {
            # Echoed back so the result can be read without the page's
            # state, which may have moved on from the query that produced
            # these rows.
            'subject': subject,
            'groupBy': query['groupBy'],
            'columns': columns,
            'rows': out,
            'totals': totals,
            'rowCount': len(out),
            'queryMs': int((time.perf_counter() - t0) * 1000),
            'notes': notes_for(query, grand['returnedUnits'] > 0),
        },
    }


def _sort_key(subject: str):
    """Biggest first: with up to three grouping levels the interesting
    rows must not be on page 7.

    ⚠️ The frontend sorts grouped rows by `netSales`, which is ALWAYS 0 on
    imports and sell-through — so that sort is a no-op there and the mock
    returns insertion order. Rather than reproduce a no-op, each subject
    sorts by its own headline measure. This is CR #1 open question 1 and
    the frontend has been asked to match.
    """
    if subject == 'imports':
        return lambda b: b['receivedCost']
    if subject == 'sellThrough':
        return lambda b: b['receivedUnits']
    return lambda b: b['netSales']


def dimension_values(dim: str) -> Dict[str, Any]:
    """Allowed values for one dimension's picker.

    Drawn from BOTH fact tables, not just sales: a brand that was
    received but never sold must still be offered, or the filter cannot
    reach exactly the rows sell-through exists to surface.

    Unbounded by date on purpose — a picker that only offered what the
    current range happens to contain would hide the value you are about
    to widen the range to look for.
    """
    meta = DIMENSIONS.get(dim)
    if meta is None:
        raise QueryError(f'unknown dimension: {dim}')
    if not meta.get('filterable'):
        raise QueryError(f'{dim} is not a filter')

    conn = get_oracle_connection()
    if conn is None:
        return {'success': False, 'error': 'database unavailable'}
    try:
        cur = conn.cursor()
        try:
            values = _distinct_values(cur, dim)
        finally:
            cur.close()
    finally:
        conn.close()
    return {'success': True, 'values': values}


# Pickers are a flat distinct list, so they do NOT need the line-grain
# pull — one lightweight scan per dimension is enough. `size` is the
# exception: it must be normalised in Python, so its raw labels come back
# with the brand and department needed to translate them.
_PICKER_SQL = {
    # ⚠️ STORES WITH ACTIVITY ONLY (user's call, 2026-11-05). `rps.store`
    # has nine rows; `LOA`, `RWL` and `DEMO` have **zero** sale lines and
    # **zero** receipt lines in ALL history, so offering them is a filter
    # value that can only ever return an empty result — the "control no
    # data could ever trigger" shape this project has been bitten by
    # before (`draft_stale`), and `DEMO` is not a shop at all.
    #
    # "Activity" is defined as the scope THIS ENGINE REPORTS ON, not as
    # mere existence in a table: a store is offered iff one of the two
    # pulls could return a row for it. The two EXISTS clauses below are
    # therefore the WHERE of SALE_LINES and RECEIPT_LINES with the date
    # bound dropped.
    #
    # Unbounded by date, like every other picker: a store that sold
    # nothing this quarter must still be offered, or the filter cannot
    # reach the rows you are about to widen the range to find. Measured
    # over all history (production, 2026-11-05):
    #
    #     HQ     609 sale lines   33,170 receipt lines
    #     RHN  14,606                  0
    #     RWT  14,557                  7   (Dec 2023 setup)
    #     RWD   9,068                  5   (Dec 2023 setup)
    #     RWR   7,030                  0
    #     RWP   2,150                  0
    #     LOA / RWL / DEMO    0        0   ← excluded
    #
    # HQ stays: 609 sale lines is small but real (the TRAVELITE bulk
    # sales), and it is where essentially all receiving lands.
    # A UNION of the two document scopes rather than two correlated
    # EXISTS clauses. All three formulations tried returned the SAME six
    # stores; this one has the cheapest plan — 126ms against 202ms on a
    # warm cursor, 1.88s against 2.22s end to end through a fresh
    # connection. The DOCUMENT_ITEM / VOU_ITEM joins are dropped because
    # they cost nothing to the answer (a document in scope has lines) and
    # only made the plan worse.
    #
    # ⚠️ It is still ~1.5s dearer than a plain `SELECT DISTINCT
    # store_code FROM rps.store` (0.4s), because proving activity means
    # touching DOCUMENT and VOUCHER. That is the price of not offering
    # three options that can only ever return nothing, paid once when the
    # filter panel opens.
    'store':        """
        SELECT st.store_code
        FROM rps.store st
        WHERE st.sid IN (
            SELECT d.store_sid FROM rps.document d
            WHERE d.is_held = 0
              AND d.receipt_type IN (0, 1)
              AND d.status = 4
            UNION
            SELECT vh.store_sid FROM rps.voucher vh
            WHERE vh.vou_class = 0
              AND vh.slip_flag = 0
              AND vh.held = 0
              AND vh.status = 4
              AND vh.vou_type = 0
        )
    """,
    'brand':        "SELECT DISTINCT vend_name FROM rps.vendor",
    'season':       "SELECT DISTINCT udf5_string FROM rps.invn_sbs_item",
    'gender':       ("SELECT DISTINCT CASE WHEN d_long_name IN ('COSM','HOME') "
                     "THEN NULL ELSE SUBSTR(d_long_name, 1, 1) END FROM rps.dcs"),
    'department':   ("SELECT DISTINCT CASE WHEN d_long_name IN ('COSM','HOME') "
                     "THEN d_long_name ELSE SUBSTR(d_long_name, 2) END FROM rps.dcs"),
    'category':     ("SELECT DISTINCT REGEXP_SUBSTR(description2, '[^-]+', 1, 3) "
                     "FROM rps.invn_sbs_item"),
    'customer':     ("SELECT DISTINCT TRIM(NVL(first_name,'') || ' ' || "
                     "NVL(last_name,'')) FROM rps.customer WHERE active = 1"),
    'customerTier': "SELECT DISTINCT info1 FROM rps.customer",
}

# Fixed vocabularies — no scan needed, and listing them here means the
# picker offers every bucket the engine can PRODUCE rather than only the
# ones that happen to occur.
_PICKER_FIXED = {
    'fpMd': ['FP', 'MD'],
    'customerType': ['new', 'returning', 'walkIn'],
}


def _distinct_values(cur, dim: str) -> List[str]:
    if dim in _PICKER_FIXED:
        return list(_PICKER_FIXED[dim])

    if dim == 'size':
        # Normalised in Python, so the raw labels have to come back with
        # what `normalize_size` needs to translate them.
        cur.execute("""
            SELECT DISTINCT i.item_size,
                   NVL(v.vend_name, ' '),
                   CASE WHEN dcs.d_long_name IN ('COSM', 'HOME')
                        THEN dcs.d_long_name
                        ELSE SUBSTR(dcs.d_long_name, 2) END
            FROM rps.invn_sbs_item i
            LEFT JOIN rps.vendor v ON v.sid = i.vend_sid
            LEFT JOIN rps.dcs dcs  ON dcs.sid = i.dcs_sid
                                  AND dcs.sbs_sid = i.sbs_sid
            WHERE i.item_size IS NOT NULL
        """)
        seen = set()
        for raw, brand, dept in cur.fetchall():
            seen.add(normalize_size(raw, (brand or '').strip(), dept or ''))
        return _sorted_values(seen)

    cur.execute(_PICKER_SQL[dim])
    return _sorted_values({r[0] for r in cur.fetchall()})


def _sorted_values(values) -> List[str]:
    """Sorted, with the unknown bucket LAST — it is the one entry a reader
    scans past, not toward."""
    out = sorted(UNKNOWN if v is None or str(v).strip() == '' else str(v)
                 for v in values)
    if UNKNOWN in out:
        out = [v for v in out if v != UNKNOWN] + [UNKNOWN]
    return out
