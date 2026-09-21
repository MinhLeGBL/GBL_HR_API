"""P&L orchestration: import a FAST export, then read statements back.

The shape of the feature
------------------------
Accounting closes a month, exports it from FAST, and uploads it. The report is
then whatever has been stored — there is no scheduled job and no snapshot,
because a closed month does not move. That is the opposite of the weekly sales
report, which freezes its figures precisely because Oracle is live underneath
it.

Two guarantees this module owes its readers:

1. NOTHING DERIVABLE IS COPIED. Company totals are summed from the stores and
   every subtotal is computed from the leaf lines. The July 2026 export proved
   why: its `Total` column silently omits a whole store, and one account is
   missing from FAST's own section subtotal.
2. AN IMPORT IS ALL OR NOTHING. A multi-month upload either lands completely or
   changes nothing, so the stored P&L is never a mixture of two files.
"""
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .aggregate import category_totals
from .importer import ImportProblem, parse
from .period import BASES, MONTHS, month_index, month_range, period_label
from .repository import PNLRepository
from .statement import STATEMENT_ORDER, build_statement

ZERO = Decimal(0)

# Shown as a share of net sales, matching the source workbook's `% sales`
# column. The sales lines are excluded: a percentage of itself says nothing,
# and the workbook leaves those cells blank too.
_NO_PCT = {'Gross sales', 'Store net sales', 'Other sales', 'Net sales'}

# Subtotal and total lines — the frontend emphasises these.
SUBTOTALS = {
    'Store net sales', 'Net sales', 'Standard profit', 'Other cost of sales',
    'Gross profit', 'Store payroll & commissions (total)', 'Occupancy',
    'Total Store Direct Operating Costs', 'Total Field & Marketing',
    'Store operating profit', 'General & Administrative', 'Operating income',
    'Operating income after tax',
}


def _pct(part, whole):
    if whole in (None, ZERO) or part is None:
        return None
    return float(part / whole)


def _variance(current, comparison):
    """(absolute, relative) change; relative is None when the base is zero.

    The source workbook clamps a swing beyond ±99% to "F"/"U". That is a
    display decision, so it stays in the frontend — the API returns the real
    ratio.
    """
    if current is None or comparison is None:
        return None, None
    diff = current - comparison
    if comparison == ZERO:
        return diff, None
    return diff, float(diff / abs(comparison))


class PNLService:

    def __init__(self):
        self.repo = PNLRepository()

    # ==================================================================
    # Schema
    # ==================================================================
    def init_database(self) -> Dict[str, Any]:
        """Create the three `pnl_*` tables. Idempotent."""
        return self.repo.init_tables()

    # ==================================================================
    # Import
    # ==================================================================
    def import_worksheet(self, worksheet, *, filename: str,
                         sheet_name: Optional[str] = None,
                         uploaded_by: Optional[str] = None,
                         year: Optional[int] = None,
                         month: Optional[Any] = None,
                         allow_locked: bool = False) -> Dict[str, Any]:
        """Parse an upload and store it, replacing the months it covers.

        `allow_locked` is the deliberate override for correcting a closed
        month. It is NOT the default: a locked month is one somebody has
        already reported on, and restating it silently is how two people end up
        quoting different numbers for the same period.
        """
        try:
            parsed = parse(worksheet, sheet_name=sheet_name,
                           year=year, month=month)
        except ImportProblem as e:
            return {'success': False, 'code': 'INVALID_FILE',
                    'error': 'The file could not be imported.',
                    'problems': e.problems}

        months = parsed['months']
        if not months:
            return {'success': False, 'code': 'INVALID_FILE',
                    'error': 'The file contains no months.'}

        if not allow_locked:
            locked = self.repo.locked_months()
            clash = [m for m in months if tuple(m) in locked]
            if clash:
                names = ', '.join(f'{MONTHS[m - 1]} {y}' for y, m in clash)
                return {
                    'success': False, 'code': 'MONTH_LOCKED',
                    'error': f'These months are locked: {names}. Unlock them '
                             'first if the figures really need restating.',
                    'locked_months': [{'year': y, 'month': m} for y, m in clash],
                }

        stored = self.repo.replace_months(
            filename=filename, sheet_name=sheet_name or getattr(worksheet, 'title', None),
            layout=parsed['layout'], uploaded_by=uploaded_by,
            months=months, entities=parsed['entities'],
            total_check=parsed['total_check'],
            facts=self._with_codes(parsed))
        if not stored.get('success'):
            return {'success': False, 'code': 'SERVER_ERROR',
                    'error': stored.get('error', 'The import could not be stored.')}

        return {
            'success': True,
            'import_id': stored['import_id'],
            'layout': parsed['layout'],
            'months': [{'year': y, 'month': m, 'month_name': MONTHS[m - 1]}
                       for y, m in months],
            'entities': parsed['entities'],
            'store_codes': parsed['store_codes'],
            'fact_count': len(parsed['facts']),
            'replaced': stored['replaced'],
            # Not an error: it is what the SOURCE file's own Total column
            # disagreed with. Surfaced so nobody has to discover it later.
            'total_check': [
                {**row, 'computed': float(row['computed']),
                 'reported': float(row['reported']),
                 'difference': float(row['difference'])}
                for row in parsed['total_check']],
        }

    @staticmethod
    def _with_codes(parsed) -> List[Dict[str, Any]]:
        """Attach each store's FAST code to its facts, where the file gave one."""
        codes = parsed['store_codes']
        return [{**f, 'entity_code': codes.get(f['entity'])}
                for f in parsed['facts']]

    # ==================================================================
    # Read
    # ==================================================================
    def statement(self, year: int, month, basis: str,
                  entity: Optional[str] = None) -> Dict[str, Any]:
        """The statement for one period, against the same period a year back."""
        if basis not in BASES:
            return {'success': False, 'code': 'INVALID_INPUT',
                    'error': f'Unknown view {basis!r}. Expected one of '
                             f'{", ".join(BASES)}.'}
        try:
            month_no = month_index(month)
        except ValueError as e:
            return {'success': False, 'code': 'INVALID_INPUT', 'error': str(e)}

        first, last = month_range(month_no, basis)
        facts = self.repo.fetch_facts([year, year - 1], first, last, entity)
        if not facts:
            return {'success': False, 'code': 'NO_DATA',
                    'error': f'No P&L data is stored for '
                             f'{period_label(year, month_no, basis)}. '
                             'Import the FAST export for those months first.'}

        current = build_statement(category_totals(facts, year, month_no, basis, entity))
        prior = build_statement(category_totals(facts, year - 1, month_no, basis, entity))
        have = {(f['year'], f['month']) for f in facts}

        return {
            'success': True,
            'period': {
                'year': year, 'month': month_no,
                'month_name': MONTHS[month_no - 1], 'basis': basis,
                'label': period_label(year, month_no, basis),
                'first_month': first, 'last_month': last,
            },
            'entity': entity,
            'coverage': {
                'current_months': sorted(m for (y, m) in have if y == year),
                'prior_months': sorted(m for (y, m) in have if y == year - 1),
                'expected_months': list(range(first, last + 1)),
            },
            'lines': self._lines(current, prior),
        }

    def _lines(self, current, prior) -> List[Dict[str, Any]]:
        cur_net = current.get('Net sales') or ZERO
        pri_net = prior.get('Net sales') or ZERO
        out = []
        for name in STATEMENT_ORDER:
            c, p = current.get(name, ZERO), prior.get(name, ZERO)
            diff, rel = _variance(c, p)
            out.append({
                'line': name,
                'is_subtotal': name in SUBTOTALS,
                'current': float(c),
                'current_pct': None if name in _NO_PCT else _pct(c, cur_net),
                'prior': float(p),
                'prior_pct': None if name in _NO_PCT else _pct(p, pri_net),
                'variance': None if diff is None else float(diff),
                'variance_pct': rel,
            })
        return out

    def months(self) -> Dict[str, Any]:
        """Which months are stored, how complete they are, and which are locked.

        Also returns every store seen, so the page can offer a picker without a
        hard-coded list — RWC only appeared partway through 2026.
        """
        rows = self.repo.stored_months()
        return {'success': True,
                'entities': self.repo.stored_entities(),
                'months': [{**r, 'month_name': MONTHS[r['month'] - 1]}
                           for r in rows]}

    def imports(self, limit: int = 50) -> Dict[str, Any]:
        return {'success': True, 'imports': self.repo.list_imports(limit)}

    # ==================================================================
    # Locks
    # ==================================================================
    def lock_month(self, year: int, month, user: Optional[str] = None) -> Dict[str, Any]:
        return self._set_lock(year, month, user, locked=True)

    def unlock_month(self, year: int, month) -> Dict[str, Any]:
        return self._set_lock(year, month, None, locked=False)

    def _set_lock(self, year, month, user, *, locked) -> Dict[str, Any]:
        try:
            month_no = month_index(month)
        except ValueError as e:
            return {'success': False, 'code': 'INVALID_INPUT', 'error': str(e)}
        ok = (self.repo.set_lock(year, month_no, user) if locked
              else self.repo.clear_lock(year, month_no))
        if not ok:
            return {'success': False, 'code': 'SERVER_ERROR',
                    'error': 'The lock could not be changed.'}
        return {'success': True, 'year': year, 'month': month_no,
                'month_name': MONTHS[month_no - 1], 'locked': locked}
