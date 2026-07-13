"""Reports service — sale comparison (CR #78).

Validates the two independent date ranges, aggregates each period from Oracle,
and assembles the two-period comparison payload.
"""
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from .repository import ReportsRepository

# Inclusive-day cap. "range exceeds 366 days" → reject. Inclusive day count is
# (to - from).days + 1, so the cap on the raw delta is 365.
_MAX_RANGE_DAYS = 366


class ReportsService:
    def __init__(self):
        self.repo = ReportsRepository()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_sale_comparison(
        self, from_a: str, to_a: str, from_b: str, to_b: str,
    ) -> Dict[str, Any]:
        """Compare two independent date ranges.

        Returns `{success: True, period_a: {...}, period_b: {...}}` on success,
        or `{success: False, error, code: 'INVALID_INPUT' | 'SERVER_ERROR'}`.
        Periods A and B are independent — no ordering constraint.
        """
        parsed_a, err = self._validate_range(from_a, to_a, 'A')
        if err:
            return {'success': False, 'error': err, 'code': 'INVALID_INPUT'}
        parsed_b, err = self._validate_range(from_b, to_b, 'B')
        if err:
            return {'success': False, 'error': err, 'code': 'INVALID_INPUT'}

        try:
            period_a = self._build_period(*parsed_a)
            period_b = self._build_period(*parsed_b)
        except Exception as e:  # Oracle/connection failure — don't leak internals.
            print(f'[ERROR] Reports sale-comparison failed: {e}')
            return {
                'success': False,
                'error': 'Failed to compute sale comparison',
                'code': 'SERVER_ERROR',
            }

        return {'success': True, 'period_a': period_a, 'period_b': period_b}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_range(
        from_s: str, to_s: str, label: str,
    ) -> Tuple[Optional[Tuple[date, date]], Optional[str]]:
        """Parse + validate one range. Returns ((from_date, to_date), None) on
        success or (None, error_message)."""
        from_d = ReportsService._parse_date(from_s)
        to_d = ReportsService._parse_date(to_s)
        if from_d is None:
            return None, f'Period {label}: `from` is not a valid YYYY-MM-DD date'
        if to_d is None:
            return None, f'Period {label}: `to` is not a valid YYYY-MM-DD date'
        if from_d > to_d:
            return None, f'Period {label}: `from` ({from_s}) is after `to` ({to_s})'
        if (to_d - from_d).days + 1 > _MAX_RANGE_DAYS:
            return None, (
                f'Period {label}: range exceeds {_MAX_RANGE_DAYS} days'
            )
        return (from_d, to_d), None

    @staticmethod
    def _parse_date(s: Optional[str]) -> Optional[date]:
        if not s:
            return None
        try:
            return datetime.strptime(s.strip(), '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return None

    def _build_period(self, from_d: date, to_d: date) -> Dict[str, Any]:
        """Aggregate one period and shape it for the response."""
        to_exclusive = to_d + timedelta(days=1)
        m = self.repo.fetch_period_metrics(from_d, to_exclusive)

        total = m['total_revenue']
        bills = m['bill_count']
        avg_bill = round(total / bills) if bills else None
        # Walk-in / anonymous revenue has no identifiable customer, so it folds
        # into "returning" here — keeps new + returning == total_revenue exact.
        returning_revenue = total - m['new_customer_revenue']

        return {
            'from':                       from_d.isoformat(),
            'to':                         to_d.isoformat(),
            'total_revenue':              total,
            'bill_count':                 bills,
            'avg_bill':                   avg_bill,
            'new_customers':              m['new_customers'],
            'returning_customers':        m['returning_customers'],
            'new_customer_revenue':       m['new_customer_revenue'],
            'returning_customer_revenue': returning_revenue,
        }
