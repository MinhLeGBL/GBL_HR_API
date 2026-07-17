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

# Postgres BIGINT range — pin `store_ids` are stored in a BIGINT[] column, so an
# out-of-range int must be rejected as 400 rather than blowing up at INSERT (500).
_BIGINT_MIN = -(2 ** 63)
_BIGINT_MAX = 2 ** 63 - 1


class ReportsService:
    def __init__(self):
        self.repo = ReportsRepository()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_sale_comparison(
        self, from_a: str, to_a: str, from_b: str, to_b: str,
        store_a: Optional[str] = None, store_b: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compare two independent date ranges.

        `store_a` / `store_b` are optional comma-separated `GET /stores` ids
        (CR #81) scoping each period to a union of stores. None / blank → all
        stores (backward-compatible with the dates-only contract).

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
            # Store resolution hits Postgres. A bad/unknown id is a normal
            # INVALID_INPUT return (short-circuits); only a DB fault raises here
            # and falls through to SERVER_ERROR below.
            sids_a, err = self._resolve_store_scope(store_a, 'A')
            if err:
                return {'success': False, 'error': err, 'code': 'INVALID_INPUT'}
            sids_b, err = self._resolve_store_scope(store_b, 'B')
            if err:
                return {'success': False, 'error': err, 'code': 'INVALID_INPUT'}

            period_a = self._build_period(*parsed_a, store_sids=sids_a)
            period_b = self._build_period(*parsed_b, store_sids=sids_b)
        except Exception as e:  # Oracle/Postgres failure — don't leak internals.
            print(f'[ERROR] Reports sale-comparison failed: {e}')
            return {
                'success': False,
                'error': 'Failed to compute sale comparison',
                'code': 'SERVER_ERROR',
            }

        return {'success': True, 'period_a': period_a, 'period_b': period_b}

    def _resolve_store_scope(
        self, raw: Optional[str], label: str,
    ) -> Tuple[Optional[list], Optional[str]]:
        """Parse a comma-separated store-id string and resolve it to Oracle
        STORE.SIDs. Returns (store_sids, None) — `None` store_sids means "all
        stores" (param omitted/blank) — or (None, error) for a bad token or an
        unknown/unmapped store id. May raise on a Postgres fault (→ 500)."""
        if raw is None:
            return None, None
        tokens = [t.strip() for t in raw.split(',') if t.strip() != '']
        if not tokens:  # present but blank → treat as all stores
            return None, None
        try:
            ids = [int(t) for t in tokens]
        except ValueError:
            return None, (
                f'Period {label}: `store_{label.lower()}` must be comma-separated '
                f'integer store ids'
            )

        mapping = self.repo.get_store_sids(ids)
        # An id absent from `mapping` does not exist; an id present with a None
        # value exists but has no Retail Pro link. Report each distinctly so a
        # store that's selectable in the UI but unmapped isn't called "unknown".
        unknown = [i for i in ids if i not in mapping]
        if unknown:
            return None, (
                f'Period {label}: unknown store id(s): '
                f'{", ".join(str(i) for i in unknown)}'
            )
        unmapped = [i for i in ids if mapping[i] is None]
        if unmapped:
            return None, (
                f'Period {label}: store id(s) not linked to a POS store '
                f'(no Retail Pro mapping): {", ".join(str(i) for i in unmapped)}'
            )

        # Preserve request order, drop duplicate SIDs (two ids → same store).
        sids, seen = [], set()
        for i in ids:
            sid = mapping[i]
            if sid not in seen:
                seen.add(sid)
                sids.append(sid)
        return sids, None

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
        # Non-string input (a JSON number/bool/list in a PUT body) is not a
        # valid date — treat as unparseable (→ 400) rather than letting
        # `.strip()` raise AttributeError and escape as a 500.
        if not s or not isinstance(s, str):
            return None
        try:
            return datetime.strptime(s.strip(), '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return None

    def _build_period(
        self, from_d: date, to_d: date, store_sids: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Aggregate one period and shape it for the response. `store_sids`
        (CR #81) scopes the period to a union of Oracle STORE.SIDs, or None for
        all stores."""
        to_exclusive = to_d + timedelta(days=1)
        m = self.repo.fetch_period_metrics(from_d, to_exclusive, store_sids)

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
        # CR #83: value-weighted average discount rate (percent) over the
        # period's SALE lines — 100 × (gross − net) / gross. Computed on sales
        # only (returns don't net in — a discount rate is a property of the sale
        # line), and store-scoped like everything else. null when no gross.
        gross_sales = m['gross_sales_revenue']
        net_sales = m['net_sales_revenue']
        avg_discount_rate = (
            round(100 * (gross_sales - net_sales) / gross_sales, 1)
            if gross_sales else None
        )

        return {
            'from':                       from_d.isoformat(),
            'to':                         to_d.isoformat(),
            'total_revenue':              total,
            'bill_count':                 bills,
            'avg_bill':                   avg_bill,
            'avg_discount_rate':          avg_discount_rate,
            'items_sold':                 m['items_sold'],   # CR #84

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
        nullable pin dates + CR #81 per-side store arrays). FK cascades on user
        delete. Idempotent — the `ADD COLUMN IF NOT EXISTS` upgrades tables
        created before CR #81 without a migration break (old rows read `NULL`,
        served back as `[]` = all stores)."""
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to database'}
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS live_comparison_pins (
                        user_id            BIGINT PRIMARY KEY
                                           REFERENCES users(sid) ON DELETE CASCADE,
                        period_a_from      DATE,
                        period_a_to        DATE,
                        period_a_store_ids BIGINT[],
                        period_b_from      DATE,
                        period_b_to        DATE,
                        period_b_store_ids BIGINT[],
                        updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                ''')
                # CR #81 upgrade path for tables created before the store arrays.
                cur.execute('''
                    ALTER TABLE live_comparison_pins
                        ADD COLUMN IF NOT EXISTS period_a_store_ids BIGINT[],
                        ADD COLUMN IF NOT EXISTS period_b_store_ids BIGINT[]
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
            'period_a': self._pin_dict(
                row['period_a_from'], row['period_a_to'], row['period_a_store_ids']),
            'period_b': self._pin_dict(
                row['period_b_from'], row['period_b_to'], row['period_b_store_ids']),
        }

    def set_pins(self, user_id: int, body: Any) -> Dict[str, Any]:
        """Replace the caller's pins. Body must carry both `period_a` and
        `period_b`; each is either `null` (unpin) or `{from, to, store_ids?}`
        (CR #81: `store_ids` is an optional int array, `[]`/omitted = all
        stores)."""
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
            self.repo.upsert_pins(user_id, a[0], a[1], a[2], b[0], b[1], b[2])
        except Exception as e:
            print(f'[ERROR] Reports set_pins failed: {e}')
            return {'success': False, 'error': 'Failed to save pins', 'code': 'SERVER_ERROR'}

        return {
            'success': True,
            'period_a': self._pin_dict(a[0], a[1], a[2]),
            'period_b': self._pin_dict(b[0], b[1], b[2]),
        }

    def _validate_pin(
        self, pin: Any, label: str,
    ) -> Tuple[Tuple[Optional[date], Optional[date], Optional[list]], Optional[str]]:
        """Validate one pin. Returns ((from_date, to_date, store_ids), None) —
        all None for an unpinned (null) side — or ((None, None, None), error).
        `store_ids` (CR #81) is a list of ints, or None for `[]`/omitted (all
        stores)."""
        if pin is None:
            return (None, None, None), None
        if not isinstance(pin, dict) or 'from' not in pin or 'to' not in pin:
            return (None, None, None), (
                f'Period {label}: must be null or an object with `from` and `to`'
            )
        parsed, err = self._validate_range(pin['from'], pin['to'], label)
        if err:
            return (None, None, None), err
        store_ids, err = self._parse_pin_store_ids(pin.get('store_ids'), label)
        if err:
            return (None, None, None), err
        return (parsed[0], parsed[1], store_ids), None

    @staticmethod
    def _parse_pin_store_ids(
        value: Any, label: str,
    ) -> Tuple[Optional[list], Optional[str]]:
        """Validate a pin's `store_ids` (CR #81). `None` / `[]` / omitted → None
        (all stores). Otherwise it must be an array of integer store ids. Unlike
        the sale-comparison scope, pin ids are persisted as-is (the saved
        selection) and are NOT existence-checked here — that check happens when
        the ids are later used on `GET /reports/sale-comparison`."""
        if value is None:
            return None, None
        if not isinstance(value, list):
            return None, (
                f'Period {label}: `store_ids` must be an array of integer store ids'
            )
        if not value:  # [] → all stores, stored as NULL
            return None, None
        ids = []
        for v in value:
            # bool is an int subclass — reject True/False explicitly.
            if isinstance(v, bool) or not isinstance(v, int):
                return None, (
                    f'Period {label}: `store_ids` must be an array of integer store ids'
                )
            # Guard the BIGINT[] column: an out-of-range int would raise at
            # INSERT and escape as a 500 — reject it as 400 here instead.
            if not (_BIGINT_MIN <= v <= _BIGINT_MAX):
                return None, (
                    f'Period {label}: `store_ids` contains an out-of-range store id'
                )
            ids.append(v)
        return ids, None

    @staticmethod
    def _pin_dict(
        from_d: Optional[date], to_d: Optional[date],
        store_ids: Optional[list] = None,
    ) -> Optional[Dict[str, Any]]:
        """A pin → `{from, to, store_ids}` (ISO dates; `store_ids` is `[]` when
        unset = all stores), or None when unpinned."""
        if from_d is None or to_d is None:
            return None
        return {
            'from': from_d.isoformat(),
            'to': to_d.isoformat(),
            'store_ids': list(store_ids) if store_ids else [],
        }
