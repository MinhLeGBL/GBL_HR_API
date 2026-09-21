"""Fold parsed facts into the category totals a statement is built from."""
from collections import defaultdict
from decimal import Decimal

from .period import month_range


def category_totals(facts, year: int, month, basis: str, entity=None):
    """{category: Decimal} for one year/period, for one store or the company.

    `entity=None` means the COMPANY: every store summed. That is the only way
    to get a company figure — the `Total` column the export supplies is never
    stored, because the July 2026 export's Total silently omits the RWC store.
    Summing what is actually there cannot omit a store that is present.

    Mirrors the workbook's SUMPRODUCT otherwise: match the year, and keep the
    months inside the basis window.
    """
    first, last = month_range(month, basis)
    out = defaultdict(Decimal)
    for f in facts:
        if f['year'] != year or not (first <= f['month'] <= last):
            continue
        if entity is not None and f['entity'] != entity:
            continue
        out[f['category']] += f['amount']
    return dict(out)
