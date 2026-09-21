"""The P&L statement: its lines, and how each one is produced.

Transcribed from the `P&L overview` sheet's own formulas in
`Runway_Company Overview H1 2026 vs 25 NEW v2.xlsx`, which are authoritative.
The workbook's `Mapping` sheet carries a few rows the statement never reads
(`Other cost of sales`, `Store payroll & commissions (total)`,
`General & Administrative` are computed subtotals there AND category rows here);
those vestigial rows are deliberately not reproduced.

Two levels of naming, both kept
-------------------------------
    FAST account  --fast_accounts.py-->  category  --LINES-->  statement line

e.g. five FAST wage accounts collapse into the `O/W Wages` category, which is
the sole source of the `Base wages & benefits` line. The middle level is kept
rather than flattened because the budget file is keyed by category, not by
statement line.

Sign convention
---------------
Cost categories arrive as POSITIVE magnitudes, as they do in FAST and in the
budget sheet, and the derived lines subtract them. The one exception is
`Others fees/incomes`, which stays net-signed — the workbook does the same.
"""
from decimal import Decimal

ZERO = Decimal(0)

# Statement line -> the categories summed into it. An empty tuple is a line the
# current FAST export has no accounts for; it reports zero rather than being
# dropped, because the statement's shape should not change with the data.
LEAF_LINES = {
    'Gross sales':                          ('Gross sales',),
    'Discounts':                            ('Discounts',),
    'Other sales':                          (),
    'Cost of sales':                        ('Cost of sales',),
    'Obsolescence':                         ('Obsolescence',),
    'Logistics & shipping':                 ('Logistics', 'Shipping'),
    'After sales':                          ('After sales',),
    'Base wages & benefits':                ('O/W Wages',),
    'Sales commissions':                    ('O/W Commissions',),
    'Fixed rent':                           ('Fixed rent',),
    'Variable Occupancy':                   ('Variable occupancy',),
    'Depreciation & amortization - store':  ('Store D&A',),
    'Store operating & other costs':        ('Other selling',),
    'Field team payroll':                   ('Field - Payroll',),
    'Field travel & training':              ('Field - Training and travel',),
    'Marketing and training fees':          ('Marketing and training fees',),
    'Marketing payroll':                    ('Marketing payroll',),
    'G&A expenses':                         ('G&A expenses',),
    'G&A payroll':                          ('G&A payroll',),
    'Others fees/incomes':                  ('Others fees/incomes',),
    'Tax':                                  ('Tax',),
}

# `Financial profit/loss` is the one netted line: income less cost, so a
# financing cost shows as negative. Kept out of LEAF_LINES because it is a
# difference, not a sum.
NETTED_LINES = {
    'Financial profit/loss': ('Financial profit', 'Financial loss'),
}

# Derived lines, in statement order, each as (line, [(sign, source), ...]).
# Order matters: every source must already have been computed.
DERIVED_LINES = (
    ('Store net sales', (('+', 'Gross sales'), ('-', 'Discounts'))),
    ('Net sales', (('+', 'Store net sales'), ('+', 'Other sales'))),
    ('Standard profit', (('+', 'Net sales'), ('-', 'Cost of sales'))),
    ('Other cost of sales', (('+', 'Obsolescence'),
                             ('+', 'Logistics & shipping'),
                             ('+', 'After sales'))),
    ('Gross profit', (('+', 'Standard profit'), ('-', 'Other cost of sales'))),
    ('Store payroll & commissions (total)',
     (('+', 'Base wages & benefits'), ('+', 'Sales commissions'))),
    ('Occupancy', (('+', 'Fixed rent'), ('+', 'Variable Occupancy'))),
    ('Total Store Direct Operating Costs',
     (('+', 'Store payroll & commissions (total)'),
      ('+', 'Occupancy'),
      ('+', 'Depreciation & amortization - store'),
      ('+', 'Store operating & other costs'))),
    ('Total Field & Marketing',
     (('+', 'Field team payroll'), ('+', 'Field travel & training'),
      ('+', 'Marketing and training fees'), ('+', 'Marketing payroll'))),
    ('Store operating profit',
     (('+', 'Gross profit'), ('-', 'Total Store Direct Operating Costs'),
      ('-', 'Total Field & Marketing'))),
    ('General & Administrative',
     (('+', 'G&A expenses'), ('+', 'G&A payroll'))),
    ('Operating income',
     (('+', 'Store operating profit'), ('-', 'General & Administrative'))),
    # Tax is SUBTRACTED here while the other three are added — the workbook's
    # `=C37+C38+C39-C40`. Tax arrives as a positive magnitude like every other
    # cost, so the sign belongs in the formula, not in the data.
    ('Operating income after tax',
     (('+', 'Operating income'), ('+', 'Financial profit/loss'),
      ('+', 'Others fees/incomes'), ('-', 'Tax'))),
)

# Presentation order of the whole statement, matching the `P&L overview` sheet
# top to bottom. Indentation is the display hint, not structure.
STATEMENT_ORDER = (
    'Gross sales', 'Discounts', 'Store net sales', 'Other sales', 'Net sales',
    'Cost of sales', 'Standard profit',
    'Obsolescence', 'Logistics & shipping', 'After sales',
    'Other cost of sales', 'Gross profit',
    'Store payroll & commissions (total)', 'Base wages & benefits',
    'Sales commissions', 'Occupancy', 'Fixed rent', 'Variable Occupancy',
    'Depreciation & amortization - store', 'Store operating & other costs',
    'Total Store Direct Operating Costs',
    'Field team payroll', 'Field travel & training',
    'Marketing and training fees', 'Marketing payroll',
    'Total Field & Marketing', 'Store operating profit',
    'G&A expenses', 'G&A payroll', 'General & Administrative',
    'Operating income', 'Financial profit/loss', 'Others fees/incomes', 'Tax',
    'Operating income after tax',
)


def build_statement(category_totals) -> dict:
    """Turn {category: Decimal} into {statement line: Decimal}.

    Categories absent from the input count as zero: a month with no entry for
    `Variable occupancy` should report 0, not omit the line or raise.
    """
    def total(category):
        return category_totals.get(category) or ZERO

    out = {line: sum((total(c) for c in cats), ZERO)
           for line, cats in LEAF_LINES.items()}

    for line, (income, cost) in NETTED_LINES.items():
        out[line] = total(income) - total(cost)

    for line, parts in DERIVED_LINES:
        out[line] = sum(
            (out[src] if sign == '+' else -out[src]) for sign, src in parts)

    return out
