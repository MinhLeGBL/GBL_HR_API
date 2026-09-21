"""Metric derivation and window aggregation for the weekly sales report.

Pure functions over the day x store x department rows the repository fetches.
All money and quantities stay `Decimal` end to end: float64 accumulates ~1e-12
relative drift across the ~6,200 rows a year-long window carries, which showed
up as a sub-VND disagreement against a single-shot query on a 159bn total.

Caveat — department bill counts do not sum
------------------------------------------
One bill can contain lines from several departments, so a department's bill
count means "bills containing this department". Summing department rows
over-counts. Store and grand totals use a separate document-grain count and are
the authoritative bill figures. The same applies to average transaction value on
department rows.
"""
from collections import defaultdict
from decimal import Decimal

ZERO = Decimal(0)

_COMPONENTS = ('sale_net', 'return_net', 'sale_gross', 'qty_sold', 'qty_returned')

# (metric key, unit). `unit` drives presentation and, for the delta, whether the
# change is a ratio or a percentage-POINT gap.
METRICS = (
    ('total_sales',           'money'),
    ('qty_sold',              'count'),
    ('bills',                 'count'),
    ('avg_unit_price',        'money1'),
    ('avg_transaction_value', 'money1'),
    ('avg_discount_pct',      'pct'),
    ('returns_value',         'money'),
)

# Metrics where a FALL is the good outcome. Everything else is "higher is
# better". Without this, a shrinking discount rate and shrinking returns would
# read as losses, which is exactly backwards — both are wins.
LOWER_IS_BETTER = {'avg_discount_pct', 'returns_value'}


def blank():
    d = {k: ZERO for k in _COMPONENTS}
    d['bills'] = 0
    return d


def metrics(acc):
    """Derive the reported metrics from summed components."""
    sale_net = acc['sale_net']
    gross = acc['sale_gross']
    qty = acc['qty_sold']
    bills = acc['bills']
    discount = gross - sale_net
    # `total_discount_value` is not a displayed column (the rate says the same
    # thing in a comparable unit), but it is kept because the rate derives from
    # it and the raw gross/net reconcile against it.
    return {
        'total_sales': sale_net - acc['return_net'],
        'returns_value': acc['return_net'],
        'qty_sold': qty,
        'bills': bills,
        'avg_unit_price': (sale_net / qty) if qty else None,
        'avg_transaction_value': (sale_net / bills) if bills else None,
        'total_discount_value': discount,
        'avg_discount_pct': (100 * discount / gross) if gross else None,
    }


# A store x department present in one period but not the other has genuinely
# zero activity on the missing side — not unknown activity. Substituting this
# keeps the deltas meaningful (a department that opened this period shows its
# full value as the gain) while leaving the ratio metrics undefined, since they
# divide by a zero denominator.
ZERO_METRICS = metrics(blank())


def aggregate(dept_rows, store_rows, window):
    """Aggregate one (from, to) window into store x department metrics.

    Returns (by_store_dept, by_store, grand, store_names) where keys are
    (store_code, department) and store_code.
    """
    start, end = window

    dept_acc = defaultdict(blank)
    store_acc = defaultdict(blank)
    grand = blank()
    store_names = {}

    for r in dept_rows:
        if not (start <= r['day'] <= end):
            continue
        store_names[r['store_code']] = r['store_name']
        for target in (dept_acc[(r['store_code'], r['department'])],
                       store_acc[r['store_code']],
                       grand):
            for k in _COMPONENTS:
                target[k] += r[k]
        # Department-grain bills only; store/grand bills come from the
        # document-grain query below so they are not over-counted.
        dept_acc[(r['store_code'], r['department'])]['bills'] += r['dept_bills']

    for r in store_rows:
        if not (start <= r['day'] <= end):
            continue
        store_acc[r['store_code']]['bills'] += r['bills']
        grand['bills'] += r['bills']

    return (
        {k: metrics(v) for k, v in dept_acc.items()},
        {k: metrics(v) for k, v in store_acc.items()},
        metrics(grand),
        store_names,
    )


def delta(cur, prior):
    """(absolute change, percentage change) between two metric values."""
    if cur is None or prior is None:
        return None, None
    diff = cur - prior
    # Integer 100, not 100.0: these values are Decimal, and Decimal does not
    # support arithmetic with float.
    pct = (100 * diff / abs(prior)) if prior else None
    return diff, pct


def delta_for(key, unit, cur, prior):
    """The change to report for one metric.

    A rate metric's change is a percentage-POINT gap, not a ratio of two ratios:
    26.5% against 26.4% is +0.1pp, and calling that "+0.4%" invites the reader to
    compare it against the sales change, which means something different.

    Returns (value, kind) where kind is 'pp' or 'pct'.
    """
    diff, pct = delta(cur, prior)
    return (diff, 'pp') if unit == 'pct' else (pct, 'pct')


def is_unfavourable(key, change):
    """Whether a change is the bad direction for this metric.

    Polarity is per metric, not per sign — see LOWER_IS_BETTER. Zero change is
    never flagged.
    """
    if change is None or change == 0:
        return False
    return change > 0 if key in LOWER_IS_BETTER else change < 0
