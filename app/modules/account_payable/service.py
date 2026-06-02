"""
Account Payable Service (CR #60).

Service for the manual-reconciliation tool that closes CR #59 Phase C.
Identifies open AR-payable bills, surfaces them per-employee and per-bill,
and lets finance link customer payments to specific bills. The linkages
drive commission release at month-close via the existing commission
calculate pipeline.

Implementation notes:
- The Oracle ledger replay runs PER REQUEST. ~127 active bills today and
  growing; sub-second response on dev DB. Caching is a future optimization
  if latency becomes a concern.
- `payable_bill_rates` (commission CR #61) is the source of truth for
  `auto_release_rate`. `payable_item_custom_rates` (this CR) holds user
  overrides. Effective rate = custom_release_rate ?? auto_release_rate ?? 0.
- Release status of an in-bill payment chronology entry is derived at
  query time from the payment_date's calendar month vs current month.
"""
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from app.core.database import get_postgres_connection
from .repository import AccountPayableRepository


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _today() -> date:
    """Indirection so tests can monkey-patch the clock if needed."""
    return date.today()


def _current_month_str(today: Optional[date] = None) -> str:
    t = today or _today()
    return f'{t.year:04d}-{t.month:02d}'


def _age_days(created_iso: Optional[str], today: Optional[date] = None) -> int:
    """Whole calendar days between created_date and today (>= 0)."""
    if not created_iso:
        return 0
    t = today or _today()
    try:
        created = date.fromisoformat(created_iso)
    except ValueError:
        return 0
    delta = (t - created).days
    return max(0, delta)


def _release_status_for_payment(
    payment_date_iso: str, today: Optional[date] = None,
) -> Tuple[str, str]:
    """Return (release_status, release_month) for one BillPaymentRef.

    Released when the payment's calendar month is < current month;
    pending when it's the current month or future. See CR §"Lifecycle model".
    """
    t = today or _today()
    current_month = f'{t.year:04d}-{t.month:02d}'
    pay_month = payment_date_iso[:7]
    release_status = 'released' if pay_month < current_month else 'pending'
    return release_status, pay_month


class AccountPayableService:
    """Service for the account-payable manual-reconciliation tool."""

    def __init__(self, repository: Optional[AccountPayableRepository] = None):
        self.repository = repository or AccountPayableRepository()

    # ------------------------------------------------------------------
    # Schema init — idempotent
    # ------------------------------------------------------------------
    def init_database(self) -> Dict[str, Any]:
        """Create the AP tables + indexes. Idempotent.

        Tables:
        - `payable_reconciliations` — manual payment→bill linkages.
        - `payable_item_custom_rates` — user-set per-item release-rate overrides.

        Called from `scripts/database/init_db.py`. Safe to call repeatedly;
        `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` make
        re-runs no-ops.
        """
        from .queries import AccountPayableQueries

        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}
        try:
            with conn.cursor() as cur:
                cur.execute(AccountPayableQueries.CREATE_RECONCILIATIONS_TABLE)
                cur.execute(AccountPayableQueries.CREATE_RECONCILIATIONS_BILL_INDEX)
                cur.execute(AccountPayableQueries.CREATE_RECONCILIATIONS_PAYMENT_INDEX)
                cur.execute(AccountPayableQueries.CREATE_CUSTOM_RATES_TABLE)
                conn.commit()
            return {'success': True}
        except Exception as e:
            conn.rollback()
            return {'success': False, 'error': f'init failed: {e}'}
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Postgres reads — rate lookups
    # ------------------------------------------------------------------
    def _load_auto_rates(self, bill_sids: List[str]) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Map (bill_sid, upc) → {revenue_type, fp_or_md, effective_rate}
        from commission's `payable_bill_rates` snapshot.

        Missing entries mean the bill's creation month was never calculated
        for that item — treated as legacy (auto_release_rate = None).
        """
        if not bill_sids:
            return {}
        conn = get_postgres_connection()
        if conn is None:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT bill_sid, upc, revenue_type, fp_or_md, effective_rate
                    FROM payable_bill_rates
                    WHERE bill_sid = ANY(%s)
                ''', (list(bill_sids),))
                rows = cur.fetchall()
        except Exception as e:
            print(f'[WARN] AP _load_auto_rates failed: {e}')
            return {}
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return {
            (r[0], r[1]): {
                'revenue_type':   r[2],
                'fp_or_md':       r[3],
                'effective_rate': float(r[4]) if r[4] is not None else None,
            }
            for r in rows
        }

    def _load_custom_rates(
        self, bill_sids: List[str],
    ) -> Dict[Tuple[str, str], float]:
        """Map (bill_sid, upc) → custom_release_rate from the override table."""
        if not bill_sids:
            return {}
        conn = get_postgres_connection()
        if conn is None:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT bill_sid, upc, custom_release_rate
                    FROM payable_item_custom_rates
                    WHERE bill_sid = ANY(%s)
                ''', (list(bill_sids),))
                rows = cur.fetchall()
        except Exception as e:
            print(f'[WARN] AP _load_custom_rates failed: {e}')
            return {}
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return {(r[0], r[1]): float(r[2]) for r in rows}

    # ------------------------------------------------------------------
    # Shape helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _bill_to_row(
        bill_sid: str, bill: Dict[str, Any], employee_codes: List[str],
        today: date,
    ) -> Dict[str, Any]:
        """Project a repository bill record + employee_codes into the
        wire-shape BillRow defined by the frontend types.ts."""
        # Decorate each payment chronology entry with release_status / release_month.
        payments_out: List[Dict[str, Any]] = []
        for p in bill['payments']:
            status, month = _release_status_for_payment(p['payment_date'], today)
            payments_out.append({
                'payment_doc_sid': p['payment_doc_sid'],
                'payment_doc_no':  p['payment_doc_no'],
                'payment_date':    p['payment_date'],
                'amount_applied':  p['amount_applied'],
                'source':          p['source'],
                'release_status':  status,
                'release_month':   month,
            })
        return {
            'bill_sid':          bill_sid,
            'doc_no':            bill['doc_no'],
            'doc_store_code':    bill['doc_store_code'],
            'customer_sid':      bill['customer_sid'],
            'customer_name':     bill['customer_name'],
            'original_charge':   bill['original_charge'],
            'total_paid':        bill['total_paid'],
            'remaining_unpaid':  bill['remaining_unpaid'],
            'status':            bill['status'],
            'created_date':      bill['created_date'],
            'last_payment_date': bill['last_payment_date'],
            'age_days':          _age_days(bill['created_date'], today),
            'employee_codes':    employee_codes,
            'payments':          payments_out,
        }

    @staticmethod
    def _item_to_wire(
        item_row: Dict[str, Any],
        auto: Optional[Dict[str, Any]],
        custom: Optional[float],
    ) -> Dict[str, Any]:
        """Project a repository item + its rate lookups into PayableBillItem shape.

        - `revenue_type` and `fp_or_md` come from the `payable_bill_rates`
          snapshot. Legacy items (no row) have null for both — the frontend
          surfaces them as unclassified.
        - `auto_release_rate` is the snapshot's effective_rate; `custom_release_rate`
          is the override (if set).
        """
        return {
            'upc':                 item_row['upc'],
            'description':         item_row.get('description'),
            'revenue_type':        auto['revenue_type'] if auto else None,
            'fp_or_md':            auto['fp_or_md'] if auto else None,
            'qty':                 int(item_row['qty'] or 0),
            'revenue':             int(item_row['revenue_with_vat'] or 0),
            'employee_code':       item_row.get('employee_code'),
            'employee_name':       item_row.get('employee_name'),
            'auto_release_rate':   auto['effective_rate'] if auto else None,
            'custom_release_rate': custom,
        }

    # ------------------------------------------------------------------
    # Read endpoints
    # ------------------------------------------------------------------
    def get_bills(
        self, search: Optional[str] = None, status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List all bills with payment chronology. Filters:
          - `search`: case-insensitive substring of doc_no or customer_name.
          - `status`: 'open' | 'partial' | 'fully_paid'.
        Returns ServiceResult<BillRow[]>.
        """
        bills = self.repository.get_all_bills()
        if not bills:
            return {'success': True, 'data': []}

        # Per-bill employee_codes via the items table (one Oracle round-trip).
        all_sids = list(bills.keys())
        items = self.repository.get_bill_items(all_sids)
        employee_codes_by_bill: Dict[str, List[str]] = {}
        for it in items:
            code = it.get('employee_code')
            if not code:
                continue
            employee_codes_by_bill.setdefault(it['bill_sid'], [])
            if code not in employee_codes_by_bill[it['bill_sid']]:
                employee_codes_by_bill[it['bill_sid']].append(code)

        today = _today()
        out: List[Dict[str, Any]] = []
        for bill_sid, b in bills.items():
            if status and b['status'] != status:
                continue
            row = self._bill_to_row(
                bill_sid, b, employee_codes_by_bill.get(bill_sid, []), today,
            )
            if search:
                needle = search.lower()
                hay = f'{row["doc_no"]} {row["customer_name"] or ""}'.lower()
                if needle not in hay:
                    continue
            out.append(row)
        # Most-recent first by creation date (callers can re-sort).
        out.sort(key=lambda r: r['created_date'], reverse=True)
        return {'success': True, 'data': out}

    def get_bill_detail(self, bill_sid: str) -> Dict[str, Any]:
        """Full bill detail with items[] and rate lookups.

        Runs the full ledger replay since we need this bill's chronology
        and customer-scoped FIFO state. A per-bill query is possible but
        we already cache the replay output below, and consistency with
        get_bills' chronology matters more than the avoided round-trip.

        Returns ServiceResult<BillDetail | None>. `None` → 404 at the route layer.
        """
        bills = self.repository.get_all_bills()
        b = bills.get(bill_sid)
        if not b:
            return {'success': False, 'error': f'Bill {bill_sid} not found', 'not_found': True}

        items_rows = self.repository.get_bill_items([bill_sid])
        auto_rates = self._load_auto_rates([bill_sid])
        custom_rates = self._load_custom_rates([bill_sid])

        items_wire = [
            self._item_to_wire(
                it,
                auto_rates.get((bill_sid, it['upc'])),
                custom_rates.get((bill_sid, it['upc'])),
            )
            for it in items_rows
        ]
        employee_codes = sorted({
            it.get('employee_code') for it in items_rows if it.get('employee_code')
        })

        today = _today()
        row = self._bill_to_row(bill_sid, b, employee_codes, today)
        row['items'] = items_wire
        row['sale_total_amt'] = b['sale_total_amt']
        return {'success': True, 'data': row}

    def get_employees(self) -> Dict[str, Any]:
        """List employees with non-zero open payable.

        For each employee, sums their item-share of every open/partial bill:
            employee_share_per_item = item.revenue × bill.unpaid_ratio
        where unpaid_ratio = remaining_unpaid / sale_total_amt.

        Returns ServiceResult<PayableEmployee[]>.
        """
        bills = self.repository.get_all_bills()
        if not bills:
            return {'success': True, 'data': []}

        # Only open + partial bills contribute to "currently payable".
        active_sids = [
            s for s, b in bills.items() if b['status'] in ('open', 'partial')
        ]
        if not active_sids:
            return {'success': True, 'data': []}

        items = self.repository.get_bill_items(active_sids)

        today = _today()
        # Aggregate per employee.
        by_emp: Dict[str, Dict[str, Any]] = {}
        for it in items:
            code = it.get('employee_code')
            if not code:
                continue
            bill = bills[it['bill_sid']]
            sale_total = bill['sale_total_amt']
            if sale_total <= 0:
                # Malformed bill — no proportional share possible.
                continue
            unpaid_ratio = bill['remaining_unpaid'] / sale_total
            share = int(round((it['revenue_with_vat'] or 0) * unpaid_ratio))
            slot = by_emp.setdefault(code, {
                'employee_code':   code,
                'full_name':       it.get('employee_name') or '',
                'store_code':      it.get('employee_store_code') or '',
                'total_payable':   0,
                'bill_sids':       set(),
                'oldest_age_days': 0,
            })
            slot['total_payable'] += share
            slot['bill_sids'].add(it['bill_sid'])
            slot['oldest_age_days'] = max(
                slot['oldest_age_days'], _age_days(bill['created_date'], today),
            )

        out = [
            {
                'employee_code':    slot['employee_code'],
                'full_name':        slot['full_name'],
                'store_code':       slot['store_code'],
                'total_payable':    slot['total_payable'],
                'open_bills_count': len(slot['bill_sids']),
                'oldest_age_days':  slot['oldest_age_days'],
            }
            for slot in by_emp.values()
            if slot['total_payable'] > 0
        ]
        out.sort(key=lambda e: e['total_payable'], reverse=True)
        return {'success': True, 'data': out}

    def get_employee_bills(self, employee_code: str) -> Dict[str, Any]:
        """One employee's open/partial bills, items scoped to their share.

        Returns ServiceResult<EmployeeBillSlice[]>.
        """
        bills = self.repository.get_all_bills()
        active_sids = [
            s for s, b in bills.items() if b['status'] in ('open', 'partial')
        ]
        if not active_sids:
            return {'success': True, 'data': []}

        all_items = self.repository.get_bill_items(active_sids)
        # Filter to this employee's items only.
        emp_items = [it for it in all_items if it.get('employee_code') == employee_code]
        if not emp_items:
            return {'success': True, 'data': []}

        # Rate joins so the slice items carry the same rate context as the bill detail.
        emp_bill_sids = sorted({it['bill_sid'] for it in emp_items})
        auto_rates = self._load_auto_rates(emp_bill_sids)
        custom_rates = self._load_custom_rates(emp_bill_sids)

        # Group items by bill.
        items_by_bill: Dict[str, List[Dict[str, Any]]] = {}
        for it in emp_items:
            items_by_bill.setdefault(it['bill_sid'], []).append(it)

        today = _today()
        out: List[Dict[str, Any]] = []
        for b_sid, b_items in items_by_bill.items():
            bill = bills[b_sid]
            sale_total = bill['sale_total_amt']
            unpaid_ratio = (
                bill['remaining_unpaid'] / sale_total if sale_total > 0 else 0.0
            )
            employee_share = int(round(
                sum((it['revenue_with_vat'] or 0) for it in b_items) * unpaid_ratio,
            ))
            items_wire = [
                self._item_to_wire(
                    it,
                    auto_rates.get((b_sid, it['upc'])),
                    custom_rates.get((b_sid, it['upc'])),
                )
                for it in b_items
            ]
            out.append({
                'bill_sid':             b_sid,
                'doc_no':               bill['doc_no'],
                'customer_name':        bill['customer_name'],
                'created_date':         bill['created_date'],
                'age_days':             _age_days(bill['created_date'], today),
                'bill_original_charge': bill['original_charge'],
                'employee_share':       employee_share,
                'items':                items_wire,
            })
        out.sort(key=lambda r: r['age_days'], reverse=True)
        return {'success': True, 'data': out}

    def get_pending_payments(self, status: Optional[str] = None) -> Dict[str, Any]:
        """Payments queue. status=unmatched|matched_pending."""
        raise NotImplementedError('CR #60 endpoint not yet implemented')

    # ------------------------------------------------------------------
    # Write endpoints — Phase D
    # ------------------------------------------------------------------
    def reconcile(
        self,
        payment_doc_sid: str,
        allocations: List[Dict[str, Any]],
        created_by_user_sid: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Save a manual linkage (or update an existing one).

        Enforces cumulative cap: per allocation,
            SUM(amount_applied for bill_sid) + new_amount ≤ original_charge.
        Also: SUM(allocations.amount) == payment.amount (no partial confirms in v1).
        """
        raise NotImplementedError('CR #60 endpoint not yet implemented')

    def unmatch(self, payment_doc_sid: str) -> Dict[str, Any]:
        """Remove an existing manual linkage for a payment."""
        raise NotImplementedError('CR #60 endpoint not yet implemented')
