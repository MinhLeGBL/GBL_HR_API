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

    def _load_reconciliations(self) -> Dict[str, List[Dict[str, Any]]]:
        """Map payment_doc_sid → list of its linkage rows from
        `payable_reconciliations`. Empty dict on connection failure."""
        conn = get_postgres_connection()
        if conn is None:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT payment_doc_sid, bill_sid, amount_applied
                    FROM payable_reconciliations
                ''')
                rows = cur.fetchall()
        except Exception as e:
            print(f'[WARN] AP _load_reconciliations failed: {e}')
            return {}
        finally:
            try:
                conn.close()
            except Exception:
                pass
        out: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r[0], []).append({
                'bill_sid':       r[1],
                'amount_applied': int(r[2]),
            })
        return out

    def get_pending_payments(self, status: Optional[str] = None) -> Dict[str, Any]:
        """Payments queue.

        Source: every Charge-tender payment receipt (negative-Charge on
        receipt_type=0 docs) in the past 24 months. Each receipt is
        bucketed by the CR §"Payment queue filter rules":

        - `unmatched`: no REF_SALE_SID matching a known bill AND no
          Postgres `payable_reconciliations` row. `is_overdue=true` when
          the payment's calendar month is past.
        - `matched_pending (ref_sale_sid)`: REF_SALE_SID points at a real
          bill AND payment_date's month == current month. (Read-only —
          0 hits in current data, kept as future-proofing if Retail Pro
          starts setting REF_SALE_SID on payment receipts.)
        - `matched_pending (manual)`: has a Postgres linkage AND
          payment_date's month == current month.

        Released payments (payment month closed AND any linkage exists)
        do NOT appear in the queue per the CR's lifecycle rule.

        `status` filter optionally narrows to one bucket.

        Each row carries `suggested_bills` = the customer's open + partial
        bill list. Walk-in payments (`customer_sid=null`) get `[]`.

        Returns ServiceResult<PendingPayment[]>, sorted by payment_date desc.
        """
        if status and status not in ('unmatched', 'matched_pending'):
            return {
                'success': False,
                'error': f"status must be 'unmatched' or 'matched_pending' (got {status!r})",
            }

        payments = self.repository.get_charge_payments()
        if not payments:
            return {'success': True, 'data': []}

        linkages = self._load_reconciliations()
        bills = self.repository.get_all_bills()

        # Suggested_bills: precompute the customer → open+partial-bill list.
        open_bills_by_customer: Dict[str, List[Dict[str, Any]]] = {}
        today = _today()
        for b_sid, b in bills.items():
            if b['status'] not in ('open', 'partial'):
                continue
            if not b['customer_sid']:
                continue
            open_bills_by_customer.setdefault(b['customer_sid'], []).append({
                'bill_sid':         b_sid,
                'doc_no':           b['doc_no'],
                'created_date':     b['created_date'],
                'age_days':         _age_days(b['created_date'], today),
                'original_charge':  b['original_charge'],
                'remaining_unpaid': b['remaining_unpaid'],
            })
        # Sort each customer's suggestions oldest-first (FIFO bias).
        for sugg in open_bills_by_customer.values():
            sugg.sort(key=lambda b: b['created_date'])

        current_month = _current_month_str(today)
        out: List[Dict[str, Any]] = []
        for p in payments:
            pay_month = p['payment_date'][:7]
            is_past_month = pay_month < current_month

            ref_sid = p.get('ref_sale_sid')
            existing = linkages.get(p['payment_doc_sid'])

            if ref_sid and ref_sid in bills:
                match_source = 'ref_sale_sid'
                ref_bill = bills[ref_sid]
                allocations = [{
                    'bill_sid':       ref_sid,
                    'doc_no':         ref_bill['doc_no'],
                    'amount_applied': p['amount'],
                }]
                payment_status = 'matched_pending'   # released ones are filtered out below
            elif existing:
                match_source = 'manual'
                allocations = [{
                    'bill_sid':       link['bill_sid'],
                    'doc_no':         bills.get(link['bill_sid'], {}).get('doc_no'),
                    'amount_applied': link['amount_applied'],
                } for link in existing]
                payment_status = 'matched_pending'
            else:
                match_source = None
                allocations = []
                payment_status = 'unmatched'

            # Released = matched + payment month past → drop from queue.
            if payment_status == 'matched_pending' and is_past_month:
                continue

            if status and status != payment_status:
                continue

            out.append({
                'payment_doc_sid': p['payment_doc_sid'],
                'payment_doc_no':  p['payment_doc_no'],
                'payment_date':    p['payment_date'],
                'doc_store_code':  p['doc_store_code'],
                'customer_sid':    p['customer_sid'],
                'customer_name':   p['customer_name'],
                'amount':          p['amount'],
                'notes_lostdoc':   p['notes_lostdoc'],
                'status':          payment_status,
                'match_source':    match_source,
                'is_overdue':      payment_status == 'unmatched' and is_past_month,
                'allocations':     allocations,
                'suggested_bills': (
                    open_bills_by_customer.get(p['customer_sid'], [])
                    if p['customer_sid'] else []
                ),
            })
        out.sort(key=lambda r: r['payment_date'], reverse=True)
        return {'success': True, 'data': out}

    # ------------------------------------------------------------------
    # Classifier — bucket-only port of CommissionService._classify_item_rate
    # ------------------------------------------------------------------
    # Used by `set_item_custom_rate` to derive (revenue_type, fp_or_md) for
    # a legacy item so propagation can scope to the same category. Mirrors
    # the priority chain in commission's classifier; rate computation
    # omitted (AP only needs the bucket).
    #
    # If commission's bucket logic ever changes, this needs to follow.
    # Kept inline (rather than importing private helper) to honor CLAUDE.md's
    # "no cross-module internals" rule — the two implementations are
    # cross-checked via the AP integration test in tests/integration.

    _HC_VENDORS_1PCT = frozenset(['ATQ', 'AQU', 'ERE', 'GEO', 'GDC', 'BDA', 'SKY', 'CHI', 'MNC', 'DAP'])
    _HC_VENDORS_2PCT = frozenset(['CGI', 'ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'ROM', 'SPK', 'TED', 'BRT'])
    _SUITCASE_VENDORS = frozenset(['TVL', 'TIT'])
    _DISCOUNT_THRESHOLD = 0.30  # >0.30 → MD; ≤0.30 → FP
    _HEA_AS_FASHION_FROM_MONTH = '2026-04'

    @classmethod
    def _classify_item_bucket(
        cls,
        item: Dict[str, Any],
        hand_carry_upcs: set,
        creation_month: str,   # 'YYYY-MM' — bill's invc_post_date month
    ) -> Optional[tuple]:
        """Return (revenue_type, fp_or_md) for an item, or None when the
        category has no rate concept (suitcase).

        `creation_month` controls the HEA-as-fashion cutoff (CR #61).
        """
        vendor = item.get('vendor_code')
        upc = item.get('upc')
        department = item.get('department')
        is_jewelry = item.get('is_jewelry', 0)
        category = item.get('category', '') or ''
        discount_rate = item.get('discount_rate', 0) or 0

        if upc in hand_carry_upcs:
            return ('hand_carry', None)
        if vendor in cls._SUITCASE_VENDORS:
            return None    # flat rate — no propagation/category
        if department == 'HOME':
            return ('home_decor', None)
        hea_as_fashion = creation_month >= cls._HEA_AS_FASHION_FROM_MONTH
        if department == 'COSM' and vendor == 'HEA' and not hea_as_fashion:
            return ('other', None)
        if is_jewelry == 1:
            if vendor == 'VHN':
                return ('vhernier', None)
            if vendor == 'ROM' and category == 'EARRINGS':
                return ('rosa_maria', None)
            return ('jewelry', None)
        is_fp = discount_rate <= cls._DISCOUNT_THRESHOLD
        return ('fashion', 'fp' if is_fp else 'md')

    # ------------------------------------------------------------------
    # Write endpoints — Phase D
    # ------------------------------------------------------------------
    def reconcile(
        self,
        payment_doc_sid: str,
        allocations: List[Dict[str, Any]],
        created_by_user_sid: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Save (or replace) a payment→bill linkage.

        Validations:
        - payment_doc_sid is a digit string; the payment exists in Oracle.
        - allocations is non-empty; each has bill_sid (digit) + amount (positive int).
        - `SUM(allocations.amount) == payment.amount` (no partial confirms in v1).
        - For each allocation: existing reconciliations' sum + new amount
          ≤ bill.original_charge (cumulative cap).

        Behavior:
        - First reconcile for the payment → INSERT rows.
        - Existing rows with identical shape → no-op (200, idempotent).
        - Existing rows with different shape → edit (DELETE + INSERT in one tx).
        - Concurrent edit (row count changed mid-tx) → 409.

        Returns ServiceResult<ReconcileResult>:
        - outcome: 'released' when the payment's calendar month is past,
          'matched_pending' when it's the current month.
        - affected_months: the distinct YYYY-MM of all touched payments
          (just one in v1 — single payment per request).
        - For released outcome, also computes affected_employee_count +
          total_release_amount so the frontend toast can summarize.
        - payment: null in v1 (full PendingPayment shape lands with Phase C-5).
        """
        # ── 1. Validate inputs ────────────────────────────────────────
        if not str(payment_doc_sid).isdigit():
            return {'success': False, 'error': 'payment_doc_sid must be a numeric string'}
        if not allocations:
            return {'success': False, 'error': 'allocations must be non-empty'}
        validated: List[tuple] = []  # (bill_sid_str, amount_int)
        for a in allocations:
            b_sid = str(a.get('bill_sid') or '')
            if not b_sid.isdigit():
                return {'success': False, 'error': f'allocation.bill_sid must be numeric; got {b_sid!r}'}
            amt = a.get('amount')
            if not isinstance(amt, int) or amt <= 0:
                return {'success': False, 'error': f'allocation.amount must be a positive int; got {amt!r}'}
            validated.append((b_sid, amt))
        total_alloc = sum(a for _, a in validated)

        # ── 2. Fetch payment from Oracle ──────────────────────────────
        payment = self.repository.get_payment_meta(payment_doc_sid)
        if not payment:
            return {
                'success': False,
                'error': f'Payment {payment_doc_sid} not found or not a Charge receipt',
                'not_found': True,
            }
        if total_alloc != payment['amount']:
            return {
                'success': False,
                'error': (
                    f'allocations sum ({total_alloc:,}) must equal '
                    f'payment amount ({payment["amount"]:,}) — partial confirms unsupported'
                ),
            }

        # ── 3. Bill lookups (single ledger replay) ────────────────────
        bills = self.repository.get_all_bills()
        for b_sid, amt in validated:
            if b_sid not in bills:
                return {
                    'success': False,
                    'error': f'Bill {b_sid} not found in active AP ledger',
                    'not_found': True,
                }

        # ── 4. Transaction: read existing, enforce cap, write ────────
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}
        try:
            with conn.cursor() as cur:
                # Existing linkages for THIS payment.
                cur.execute('''
                    SELECT bill_sid, amount_applied
                    FROM payable_reconciliations
                    WHERE payment_doc_sid = %s
                    ORDER BY bill_sid
                ''', (str(payment_doc_sid),))
                existing = [(r[0], int(r[1])) for r in cur.fetchall()]
                existing_sorted = sorted(existing)
                requested_sorted = sorted(validated)
                if existing_sorted == requested_sorted:
                    # Idempotent — nothing to write.
                    conn.commit()
                    return self._reconcile_response(
                        payment=payment, allocations=validated, bills=bills,
                    )

                # Cumulative cap: SUM(existing amount for bill X across ALL payments)
                # excluding our own existing rows + new amount ≤ original_charge.
                # If we're editing, our own rows are about to be deleted, so
                # subtract them from the cap baseline.
                own_by_bill: Dict[str, int] = {}
                for b_sid, amt in existing:
                    own_by_bill[b_sid] = own_by_bill.get(b_sid, 0) + amt
                for b_sid, new_amt in validated:
                    cur.execute('''
                        SELECT COALESCE(SUM(amount_applied), 0)
                        FROM payable_reconciliations
                        WHERE bill_sid = %s
                    ''', (b_sid,))
                    other_total = int(cur.fetchone()[0]) - own_by_bill.get(b_sid, 0)
                    bill = bills[b_sid]
                    cap = bill['original_charge']
                    if other_total + new_amt > cap:
                        conn.rollback()
                        overflow = other_total + new_amt - cap
                        return {
                            'success': False,
                            'error': (
                                f'Bill #{bill["doc_no"]} would exceed its original '
                                f'charge by {overflow:,} VND'
                            ),
                        }

                # Replace: DELETE then INSERT in the same tx.
                if existing:
                    cur.execute(
                        'DELETE FROM payable_reconciliations WHERE payment_doc_sid = %s',
                        (str(payment_doc_sid),),
                    )
                    if cur.rowcount != len(existing):
                        # Another session changed the row count between our
                        # SELECT and our DELETE → 409 + rollback.
                        conn.rollback()
                        return {
                            'success': False,
                            'error': 'Payment was reconciled by another user — refresh required',
                            'conflict': True,
                        }
                cur.executemany('''
                    INSERT INTO payable_reconciliations
                        (payment_doc_sid, bill_sid, amount_applied, created_by)
                    VALUES (%s, %s, %s, %s)
                ''', [
                    (str(payment_doc_sid), b_sid, amt, created_by_user_sid)
                    for b_sid, amt in validated
                ])
                conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return {'success': False, 'error': f'reconcile failed: {e}'}
        finally:
            try:
                conn.close()
            except Exception:
                pass

        return self._reconcile_response(
            payment=payment, allocations=validated, bills=bills,
        )

    def _reconcile_response(
        self, *, payment: Dict[str, Any], allocations: List[tuple],
        bills: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build the ReconcileResult: outcome + affected_months + (for released)
        the employee/release totals."""
        today = _today()
        pay_month = payment['payment_date'][:7]
        cur_month = _current_month_str(today)
        outcome = 'released' if pay_month < cur_month else 'matched_pending'

        result: Dict[str, Any] = {
            'outcome':         outcome,
            # `payment` shape is the queue PendingPayment (Phase C-5).
            # Until that lands, return null and let the frontend refetch.
            'payment':         None,
            'affected_months': [pay_month],
        }
        if outcome == 'released':
            # Compute release totals: revenue × effective_rate × (amount / original_charge)
            # per item on each allocated bill, then aggregate by employee.
            alloc_bill_sids = [b_sid for b_sid, _ in allocations]
            items = self.repository.get_bill_items(alloc_bill_sids)
            auto = self._load_auto_rates(alloc_bill_sids)
            custom = self._load_custom_rates(alloc_bill_sids)
            by_emp: Dict[str, int] = {}
            total_release = 0
            for b_sid, amt in allocations:
                bill = bills[b_sid]
                if bill['original_charge'] <= 0:
                    continue
                share_ratio = amt / bill['original_charge']
                for it in items:
                    if it['bill_sid'] != b_sid:
                        continue
                    a = auto.get((b_sid, it['upc']))
                    c = custom.get((b_sid, it['upc']))
                    effective = c if c is not None else (
                        a['effective_rate'] if (a and a['effective_rate'] is not None) else 0.0
                    )
                    released = int(round((it['revenue_with_vat'] or 0) * effective * share_ratio))
                    if released == 0:
                        continue
                    code = it.get('employee_code') or '__unknown__'
                    by_emp[code] = by_emp.get(code, 0) + released
                    total_release += released
            result['affected_employee_count'] = len(by_emp)
            result['total_release_amount'] = total_release
        return {'success': True, 'data': result}

    def unmatch(self, payment_doc_sid: str) -> Dict[str, Any]:
        """Remove every linkage for `payment_doc_sid`.

        Returns `{success, data: {payment_doc_sid, deleted_count}}`. The full
        PendingPayment shape comes back via Phase C-5; the frontend refetches
        the queue after a successful unmatch.
        """
        if not str(payment_doc_sid).isdigit():
            return {'success': False, 'error': 'payment_doc_sid must be a numeric string'}
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    'DELETE FROM payable_reconciliations WHERE payment_doc_sid = %s',
                    (str(payment_doc_sid),),
                )
                deleted = cur.rowcount
                conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return {'success': False, 'error': f'unmatch failed: {e}'}
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return {
            'success': True,
            'data': {
                'payment_doc_sid': str(payment_doc_sid),
                'deleted_count':   deleted,
            },
        }

    def set_item_custom_rate(
        self,
        bill_sid: str,
        upc: str,
        custom_release_rate: Optional[float],
        set_by_user_sid: Optional[int] = None,
    ) -> Dict[str, Any]:
        """UPSERT or DELETE a per-item rate override, with legacy propagation.

        Per CR §"Custom rate legacy-propagation rule":
        - Clearing (null) → DELETE the targeted row only. No propagation.
        - Setting (non-null) on a non-legacy item → UPSERT targeted row only.
        - Setting (non-null) on a LEGACY item (no `payable_bill_rates` row or
          rate IS NULL) → UPSERT targeted row AND propagate the same rate to
          every other legacy item with the same `(revenue_type, fp_or_md)`
          across every open/partial bill in the same creation month.

        Returns ServiceResult<UpdateItemRateResult>: `bill` (refreshed
        BillDetail), `propagated_to_bill_count`, `propagated_item_count`.
        """
        # ── Validate ──────────────────────────────────────────────────
        if not str(bill_sid).isdigit():
            return {'success': False, 'error': 'bill_sid must be a numeric string'}
        upc = str(upc).strip() if upc is not None else None
        if not upc:
            return {'success': False, 'error': 'upc is required'}
        if custom_release_rate is not None:
            if not isinstance(custom_release_rate, (int, float)) or custom_release_rate < 0:
                return {'success': False, 'error': 'custom_release_rate must be a non-negative number or null'}
            if custom_release_rate > 9.99999:   # NUMERIC(6,5) safety
                return {'success': False, 'error': 'custom_release_rate exceeds maximum (9.99999)'}

        # Targeted bill must exist (we'll need it for the response payload).
        bills = self.repository.get_all_bills()
        targeted_bill = bills.get(bill_sid)
        if not targeted_bill:
            return {
                'success': False,
                'error': f'Bill {bill_sid} not found in active AP ledger',
                'not_found': True,
            }

        # ── Clear path: DELETE only, no propagation ───────────────────
        if custom_release_rate is None:
            conn = get_postgres_connection()
            if conn is None:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}
            try:
                with conn.cursor() as cur:
                    cur.execute('''
                        DELETE FROM payable_item_custom_rates
                        WHERE bill_sid = %s AND upc = %s
                    ''', (bill_sid, upc))
                    conn.commit()
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                return {'success': False, 'error': f'clear custom rate failed: {e}'}
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            # Refresh bill detail for the response.
            return self._wrap_with_bill_detail(bill_sid, 0, 0)

        # ── Set path: UPSERT targeted + maybe propagate ──────────────
        # 1) Look up the targeted item from Oracle (needed for classification).
        targeted_items = self.repository.get_bill_items([bill_sid])
        targeted_item = next((it for it in targeted_items if it['upc'] == upc), None)
        if not targeted_item:
            return {
                'success': False,
                'error': f'Item with upc {upc} not found on bill {bill_sid}',
                'not_found': True,
            }

        # 2) Determine if targeted is legacy.
        auto_rates = self._load_auto_rates([bill_sid])
        auto_row = auto_rates.get((bill_sid, upc))
        is_legacy = auto_row is None or auto_row.get('effective_rate') is None

        # 3) UPSERT targeted row.
        propagated_to_bill_count = 0
        propagated_item_count = 0
        propagation_targets: List[tuple] = []   # (bill_sid, upc) pairs

        if is_legacy:
            propagation_targets = self._find_legacy_propagation_targets(
                targeted_bill=targeted_bill,
                targeted_item=targeted_item,
                bills=bills,
                exclude_key=(bill_sid, upc),
            )

        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}
        try:
            with conn.cursor() as cur:
                # 3a) UPSERT the targeted row.
                cur.execute('''
                    INSERT INTO payable_item_custom_rates
                        (bill_sid, upc, custom_release_rate, set_by)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (bill_sid, upc) DO UPDATE SET
                        custom_release_rate = EXCLUDED.custom_release_rate,
                        set_by              = EXCLUDED.set_by,
                        set_at              = NOW()
                ''', (bill_sid, upc, custom_release_rate, set_by_user_sid))

                # 3b) UPSERT propagation targets.
                if propagation_targets:
                    cur.executemany('''
                        INSERT INTO payable_item_custom_rates
                            (bill_sid, upc, custom_release_rate, set_by)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (bill_sid, upc) DO UPDATE SET
                            custom_release_rate = EXCLUDED.custom_release_rate,
                            set_by              = EXCLUDED.set_by,
                            set_at              = NOW()
                    ''', [
                        (b_sid, u, custom_release_rate, set_by_user_sid)
                        for b_sid, u in propagation_targets
                    ])
                    propagated_to_bill_count = len({b for b, _ in propagation_targets})
                    propagated_item_count = len(propagation_targets)
                conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return {'success': False, 'error': f'set custom rate failed: {e}'}
        finally:
            try:
                conn.close()
            except Exception:
                pass

        return self._wrap_with_bill_detail(
            bill_sid, propagated_to_bill_count, propagated_item_count,
        )

    def _load_hand_carry_upcs(self) -> set:
        """Load hand-carry UPC set via commission's repository.

        Lazy-imported to honor CLAUDE.md's circular-import guidance for
        cross-module dependencies. Returns an empty set on failure rather
        than aborting propagation — worst case, HC items just don't
        propagate (HC has a single rate regardless of category anyway).
        """
        try:
            from app.modules.commission.repository import CommissionRepository
            return set(CommissionRepository().get_hand_carry_upcs() or [])
        except Exception as e:
            print(f'[WARN] AP hand_carry_upcs load failed; treating no items as HC: {e}')
            return set()

    def _find_legacy_propagation_targets(
        self,
        *,
        targeted_bill: Dict[str, Any],
        targeted_item: Dict[str, Any],
        bills: Dict[str, Dict[str, Any]],
        exclude_key: tuple,
    ) -> List[tuple]:
        """Find (bill_sid, upc) pairs eligible for legacy propagation.

        Eligibility:
        - Bill is open or partial.
        - Bill's invc_post_date month == targeted bill's month.
        - Item has NO `payable_bill_rates` row (or row with NULL rate) — i.e. legacy.
        - Item classifies to the same (revenue_type, fp_or_md) as the targeted item.
        """
        hc_upcs = self._load_hand_carry_upcs()

        # 1. Classify the targeted item.
        targeted_month = targeted_bill['post_month']   # 'YYYY-MM'
        targeted_bucket = self._classify_item_bucket(
            targeted_item, hand_carry_upcs=hc_upcs, creation_month=targeted_month,
        )
        if targeted_bucket is None:
            # Suitcase or skipped — no propagation.
            return []

        # 2. Candidate bills: open/partial AND same post_month.
        candidate_bill_sids = [
            b_sid for b_sid, b in bills.items()
            if b['status'] in ('open', 'partial') and b['post_month'] == targeted_month
        ]
        if not candidate_bill_sids:
            return []

        # 3. Items + auto-rate lookup for legacy determination.
        candidate_items = self.repository.get_bill_items(candidate_bill_sids)
        auto = self._load_auto_rates(candidate_bill_sids)

        targets: List[tuple] = []
        for it in candidate_items:
            key = (it['bill_sid'], it['upc'])
            if key == exclude_key:
                continue
            a = auto.get(key)
            if a is not None and a.get('effective_rate') is not None:
                continue
            bucket = self._classify_item_bucket(
                it, hand_carry_upcs=hc_upcs,
                creation_month=bills[it['bill_sid']]['post_month'],
            )
            if bucket != targeted_bucket:
                continue
            targets.append(key)
        return targets

    def _wrap_with_bill_detail(
        self, bill_sid: str, propagated_to_bill_count: int, propagated_item_count: int,
    ) -> Dict[str, Any]:
        """Return UpdateItemRateResult shape: refreshed bill + propagation counts."""
        detail = self.get_bill_detail(bill_sid)
        if not detail.get('success'):
            # Treat detail-lookup failure as a partial success — the write
            # already happened. Frontend should refetch.
            return {
                'success': True,
                'data': {
                    'bill': None,
                    'propagated_to_bill_count': propagated_to_bill_count,
                    'propagated_item_count':    propagated_item_count,
                },
            }
        return {
            'success': True,
            'data': {
                'bill': detail['data'],
                'propagated_to_bill_count': propagated_to_bill_count,
                'propagated_item_count':    propagated_item_count,
            },
        }
