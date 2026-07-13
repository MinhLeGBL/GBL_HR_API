"""Reports service — sale comparison (CR #78).

Validates the two independent date ranges, aggregates each period from Oracle,
and assembles the two-period comparison payload.
"""
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from app.core.database import get_postgres_connection
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
        # CR #80: revenue splits three ways — new (identifiable first-timers),
        # tourist (walk-in / anonymous bills), and returning (everything else,
        # i.e. identifiable repeat customers). "returning" is the residual so
        # new + returning + tourist == total_revenue stays exact.
        returning_revenue = (
            total - m['new_customer_revenue'] - m['tourist_customer_revenue']
        )

        return {
            'from':                       from_d.isoformat(),
            'to':                         to_d.isoformat(),
            'total_revenue':              total,
            'bill_count':                 bills,
            'avg_bill':                   avg_bill,
            'new_customers':              m['new_customers'],
            'returning_customers':        m['returning_customers'],
            'tourist_customers':          m['tourist_customers'],
            'new_customer_revenue':       m['new_customer_revenue'],
            'returning_customer_revenue': returning_revenue,
            'tourist_customer_revenue':   m['tourist_customer_revenue'],
        }

    # ==================================================================
    # CR #79 — per-user pinned periods
    # ==================================================================
    def init_database(self) -> Dict[str, Any]:
        """Create the `live_comparison_pins` table (one row per user, four
        nullable pin dates). FK cascades on user delete. Idempotent."""
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to database'}
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS live_comparison_pins (
                        user_id       BIGINT PRIMARY KEY
                                      REFERENCES users(sid) ON DELETE CASCADE,
                        period_a_from DATE,
                        period_a_to   DATE,
                        period_b_from DATE,
                        period_b_to   DATE,
                        updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                ''')
            conn.commit()
            return {'success': True}
        finally:
            conn.close()

    def get_pins(self, user_id: int) -> Dict[str, Any]:
        """Return the caller's pinned periods. Unset → nulls (never 404)."""
        try:
            row = self.repo.get_pins(user_id)
        except Exception as e:
            print(f'[ERROR] Reports get_pins failed: {e}')
            return {'success': False, 'error': 'Failed to load pins', 'code': 'SERVER_ERROR'}

        if row is None:
            return {'success': True, 'period_a': None, 'period_b': None}
        return {
            'success': True,
            'period_a': self._pin_dict(row['period_a_from'], row['period_a_to']),
            'period_b': self._pin_dict(row['period_b_from'], row['period_b_to']),
        }

    def set_pins(self, user_id: int, body: Any) -> Dict[str, Any]:
        """Replace the caller's pins. Body must carry both `period_a` and
        `period_b`; each is either `null` (unpin) or `{from, to}`."""
        if not isinstance(body, dict) or 'period_a' not in body or 'period_b' not in body:
            return {
                'success': False,
                'error': 'Body must include both `period_a` and `period_b` (use null to unpin a side)',
                'code': 'INVALID_INPUT',
            }
        a, err = self._validate_pin(body['period_a'], 'A')
        if err:
            return {'success': False, 'error': err, 'code': 'INVALID_INPUT'}
        b, err = self._validate_pin(body['period_b'], 'B')
        if err:
            return {'success': False, 'error': err, 'code': 'INVALID_INPUT'}

        try:
            self.repo.upsert_pins(user_id, a[0], a[1], b[0], b[1])
        except Exception as e:
            print(f'[ERROR] Reports set_pins failed: {e}')
            return {'success': False, 'error': 'Failed to save pins', 'code': 'SERVER_ERROR'}

        return {
            'success': True,
            'period_a': self._pin_dict(a[0], a[1]),
            'period_b': self._pin_dict(b[0], b[1]),
        }

    def _validate_pin(
        self, pin: Any, label: str,
    ) -> Tuple[Tuple[Optional[date], Optional[date]], Optional[str]]:
        """Validate one pin. Returns ((from_date, to_date), None) — both None
        for an unpinned (null) side — or ((None, None), error_message)."""
        if pin is None:
            return (None, None), None
        if not isinstance(pin, dict) or 'from' not in pin or 'to' not in pin:
            return (None, None), (
                f'Period {label}: must be null or an object with `from` and `to`'
            )
        parsed, err = self._validate_range(pin['from'], pin['to'], label)
        if err:
            return (None, None), err
        return parsed, None

    @staticmethod
    def _pin_dict(from_d: Optional[date], to_d: Optional[date]) -> Optional[Dict[str, str]]:
        """A pin → `{from, to}` (ISO), or None when unpinned."""
        if from_d is None or to_d is None:
            return None
        return {'from': from_d.isoformat(), 'to': to_d.isoformat()}
