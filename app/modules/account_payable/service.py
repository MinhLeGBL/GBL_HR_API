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
        - `payable_reconciliation_items` — per-item priority assignment (CR #69).
        - `payable_payment_remakes` — remake/corrective payment tags (CR #70).
        - `payable_item_custom_rates` — user-set per-item release-rate overrides.
        - `payable_bill_voids` — bill write-offs (CR #68; goodwill / discount).

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
                # CR #72 — migrate pre-existing tables: add tender_category
                # column (defaults to 'cash_card' for legacy rows) and swap
                # the (payment, bill) UNIQUE for the new (payment, bill,
                # tender_category) UNIQUE so the same bill can be targeted
                # from both cash_card and gift_certificate panels.
                cur.execute(AccountPayableQueries.ADD_RECONCILIATIONS_TENDER_CATEGORY_COLUMN)
                cur.execute(AccountPayableQueries.DROP_RECONCILIATIONS_OLD_UNIQUE)
                cur.execute(AccountPayableQueries.ADD_RECONCILIATIONS_TENDER_UNIQUE)
                cur.execute(AccountPayableQueries.CREATE_RECONCILIATION_ITEMS_TABLE)
                cur.execute(AccountPayableQueries.CREATE_RECONCILIATION_ITEMS_UPC_INDEX)
                cur.execute(AccountPayableQueries.CREATE_PAYMENT_REMAKES_TABLE)
                cur.execute(AccountPayableQueries.CREATE_PAYMENT_REMAKES_ACTIVE_INDEX)
                cur.execute(AccountPayableQueries.CREATE_CUSTOM_RATES_TABLE)
                cur.execute(AccountPayableQueries.CREATE_BILL_VOIDS_TABLE)
                cur.execute(AccountPayableQueries.CREATE_BILL_VOIDS_BILL_INDEX)
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
    # Release accumulator (CR #66) — read by commission's calculate
    # ------------------------------------------------------------------
    # Constants mirror commission's classification — kept local to avoid
    # importing from commission and creating a circular module dep.
    _SUITCASE_VENDORS = frozenset(['TVL', 'TIT'])
    _SUITCASE_FLAT_AMOUNT = 500_000
    _VAT_DIVISOR = 1.1  # CR #20 flat-10% VAT convention

    def compute_released_for_month(
        self, year: int, month: int,
    ) -> Dict[str, Dict[str, int]]:
        """CR #66: accumulate released commission per (employee_code, category)
        for bills paid (in part or full) during the given month.

        Algorithm — mirrors the spec in
        `docs/cr/commission.md → CR #66 — Spec`:

        1. Load all bills via `_load_bills` (REF_SALE_SID → manual replay,
           CR #62 unified — v2.0.0 removed FIFO). Reuses the per-request
           replay output — commission's calculate triggers this once per
           `POST /calculate`.
        2. For each bill, find payments whose `payment_date` falls in the
           target month. A bill is "in scope" for month M if it has any
           such payment. Allocation factor = `sum(amount_applied_in_month)
           / original_charge`.
        3. Pull line items for in-scope bills via Oracle
           `ORACLE_BILL_ITEMS_BY_SID`. Each item contributes
              `revenue_basis × effective_rate × allocation_factor`
           to its employee's `revenue_type[_fp_or_md]` bucket.
        4. `effective_rate = custom_release_rate ?? auto_release_rate ?? 0`.
           Custom rates live in `payable_item_custom_rates` (CR #60); auto
           rates come from commission's `payable_bill_rates` snapshot (CR #61).
        5. `revenue_basis = revenue_with_vat / 1.1` for every category
           except `hand_carry` (with-VAT) and `suitcase` (flat amount, no
           rate — `_SUITCASE_FLAT_AMOUNT × qty`).

        Items missing both an auto-rate snapshot AND a custom override
        contribute 0 — typically legacy bills predating CR #61. A warning
        is logged when this happens so users can investigate; never errors.

        Returns:
            {employee_code: {category_key: int_amount}}
            where category_key is 'fashion_fp' | 'fashion_md' | 'jewelry'
            | 'vhernier' | 'rosa_maria' | 'hand_carry' | 'suitcase'
            | 'home_decor' | 'other'. Zero-value entries are dropped.
            Note: there is no `over_target` bucket here — over-target
            bonus is baked into `payable_bill_rates` as a fashion+fp row
            at the inflated rate, so it accumulates under `fashion_fp`.
            This differs from `withheld_by_category` (which splits OT out)
            and is per CR #66 spec.

        Empty dict on Oracle/Postgres connection failure.
        """
        target_month_str = f'{year:04d}-{month:02d}'

        bills = self._load_bills()
        if not bills:
            return {}

        # 1. Find bills with payments in the target month — track each
        #    payment separately (CR #69: per-payment per-item factors).
        # CR #72: ONLY cash_card chronology entries release commission.
        # Gift Certificate entries clear the bill (count toward total_paid)
        # but contribute 0 to release — the boss-approved discount semantics.
        payments_in_month_by_bill: Dict[str, List[Dict[str, Any]]] = {}
        for bill_sid, bill in bills.items():
            for p in bill.get('payments', []):
                if p.get('payment_date', '')[:7] != target_month_str:
                    continue
                if (p.get('tender_category') or 'cash_card') != self._TENDER_CATEGORY_CASH_CARD:
                    continue
                payments_in_month_by_bill.setdefault(bill_sid, []).append(p)

        if not payments_in_month_by_bill:
            return {}

        # 2. Fetch line items + rate lookups for in-scope bills.
        scope_sids = list(payments_in_month_by_bill.keys())
        items = self.repository.get_bill_items(scope_sids)
        auto_rates = self._load_auto_rates(scope_sids)
        custom_rates = self._load_custom_rates(scope_sids)

        # CR #69: per-item priority assignments per (payment, bill).
        # Empty dict when nothing is in priority mode — the per-item
        # factor falls through to the proportional path automatically.
        scope_payment_sids = sorted({
            p['payment_doc_sid']
            for plist in payments_in_month_by_bill.values()
            for p in plist
        })
        per_item_assignments = self._load_reconciliation_items_for_payments(
            scope_payment_sids
        )
        # CR #70: defensive — payments tagged as remake should have NO
        # reconciliation rows by construction (tag_remake DELETEs them),
        # so they wouldn't appear in scope_payment_sids in normal flow.
        # We still skip them explicitly here in case a race condition
        # (mid-tag reconcile) leaves stray rows behind.
        active_remakes = self._load_active_remakes()
        remake_pay_sids = set(active_remakes.keys())

        def _factor_for_item(
            bill_sid: str, item_upc: str,
            item_revenue_with_vat: float, original_charge: float,
        ) -> float:
            """Sum per-payment factors for this (bill, item) across all
            in-month payments. Proportional payments contribute the same
            paid-ratio to every item; priority payments contribute
            `amount_assigned / item.revenue_with_vat` ONLY to listed
            items (0 to everything else)."""
            total = 0.0
            for p in payments_in_month_by_bill.get(bill_sid, []):
                pay_sid = p['payment_doc_sid']
                # CR #70: skip remake-tagged payments — they're explicitly
                # excluded from release per spec.
                if pay_sid in remake_pay_sids:
                    continue
                # CR #72: only cash_card items count for release. The
                # surrounding loop already filtered the chrono entries to
                # cash_card, so this lookup is for the matching parent row.
                per_item_rows = per_item_assignments.get(
                    (pay_sid, bill_sid, self._TENDER_CATEGORY_CASH_CARD)
                )
                if per_item_rows is None:
                    # Proportional payment — every item shares the same factor.
                    if original_charge > 0:
                        total += float(p['amount_applied']) / float(original_charge)
                else:
                    # Priority payment — find this item's assignment (or 0).
                    assigned = next(
                        (row['amount_assigned'] for row in per_item_rows
                         if row['upc'] == item_upc),
                        0,
                    )
                    if assigned > 0 and item_revenue_with_vat > 0:
                        total += float(assigned) / float(item_revenue_with_vat)
            return total

        # 3. Accumulate per (employee_code, category_key).
        released_by_emp_by_cat: Dict[str, Dict[str, float]] = {}
        missing_rate_count = 0
        for item in items:
            emp_code = item.get('employee_code')
            if not emp_code:
                continue
            bill_sid = item['bill_sid']
            original_charge = bills[bill_sid]['original_charge']
            if original_charge <= 0:
                continue
            revenue_with_vat = float(item.get('revenue_with_vat') or 0)
            allocation_factor = _factor_for_item(
                bill_sid=bill_sid, item_upc=item.get('upc') or '',
                item_revenue_with_vat=revenue_with_vat,
                original_charge=original_charge,
            )
            if allocation_factor == 0:
                # CR #69: priority mode silently excludes unlisted items —
                # not a missing-snapshot warning. Skip without counting.
                continue
            upc = item.get('upc')
            qty = int(item.get('qty') or 0)
            # revenue_with_vat already pulled above for the factor lookup.
            vendor = item.get('vendor_code')

            # Suitcase: flat per qty × allocation, no rate snapshot.
            if vendor in self._SUITCASE_VENDORS:
                contribution = (
                    self._SUITCASE_FLAT_AMOUNT * qty * allocation_factor
                )
                category_key = 'suitcase'
            else:
                auto = auto_rates.get((bill_sid, upc)) if upc else None
                custom = custom_rates.get((bill_sid, upc)) if upc else None
                if not auto and custom is None:
                    missing_rate_count += 1
                    continue
                if custom is not None:
                    effective_rate = custom
                else:
                    effective_rate = auto['effective_rate'] or 0.0
                if not auto:
                    # Custom override without an auto row — uncommon, but
                    # we lack a revenue_type so we cannot bucket it.
                    missing_rate_count += 1
                    continue
                revenue_type = auto['revenue_type']
                fp_or_md = auto['fp_or_md']

                if revenue_type == 'hand_carry':
                    revenue_basis = revenue_with_vat
                else:
                    revenue_basis = revenue_with_vat / self._VAT_DIVISOR

                contribution = (
                    revenue_basis * float(effective_rate) * allocation_factor
                )
                category_key = (
                    f'{revenue_type}_{fp_or_md}' if fp_or_md else revenue_type
                )

            if contribution == 0:
                continue
            by_cat = released_by_emp_by_cat.setdefault(emp_code, {})
            by_cat[category_key] = by_cat.get(category_key, 0.0) + contribution

        if missing_rate_count:
            print(
                f'[WARN] CR #66: {missing_rate_count} line item(s) for '
                f'{target_month_str} had no rate snapshot — contributed 0 '
                'to released. Likely legacy bills from before CR #61.'
            )

        # 4. Round per-bucket; drop zeros after rounding.
        return {
            code: {
                k: int(round(v))
                for k, v in by_cat.items() if int(round(v)) != 0
            }
            for code, by_cat in released_by_emp_by_cat.items()
            if any(int(round(v)) != 0 for v in by_cat.values())
        }

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
        # CR #72: surface tender_category so the BillDetailDialog can show
        # a 💵/🎁 badge per payment ref.
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
                'tender_category': p.get('tender_category') or 'cash_card',
            })
        return {
            'bill_sid':          bill_sid,
            'doc_no':            bill['doc_no'],
            'doc_store_code':    bill['doc_store_code'],
            'customer_sid':      bill['customer_sid'],
            'customer_name':     bill['customer_name'],
            'original_charge':   bill['original_charge'],
            'total_paid':        bill['total_paid'],
            # CR #68: always present; 0 when no voids on file.
            'total_voided':      bill.get('total_voided', 0),
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
        bills = self._load_bills()
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
        and the customer-scoped allocation state. A per-bill query is
        possible but we already cache the replay output below, and
        consistency with get_bills' chronology matters more than the
        avoided round-trip.

        Returns ServiceResult<BillDetail | None>. `None` → 404 at the route layer.
        """
        bills = self._load_bills()
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
        # CR #68: BillDetail surfaces the full void chronology (newest-first
        # via `_load_voids_by_bill_sid`'s ORDER BY clause).
        row['voids'] = b.get('voids_internal', [])
        return {'success': True, 'data': row}

    def get_employees(self) -> Dict[str, Any]:
        """List employees with non-zero open payable.

        For each employee, sums their item-share of every open/partial bill:
            employee_share_per_item = item.revenue × bill.unpaid_ratio
        where unpaid_ratio = remaining_unpaid / sale_total_amt.

        Returns ServiceResult<PayableEmployee[]>.
        """
        bills = self._load_bills()
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
        bills = self._load_bills()
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

    def _load_bills(self) -> Dict[str, Dict[str, Any]]:
        """Single-shot bill loader (CR #62; CR #68 layers voids).

        Loads the manual `payable_reconciliations` rows once per request
        and threads them through the Oracle ledger replay so EVERY read
        path (bill view, queue, employees, employee-bills) sees the
        identical replay state. Without this unifier the queue could
        report `unmatched` for the same payment the bill view shows
        `matched (manual)`.

        CR #68: after the replay, `payable_bill_voids` rows are applied
        on top — each bill gains `total_voided` and `voids_internal`, and
        `remaining_unpaid` + `status` are recomputed via the new formula
        `remaining = original_charge - total_paid - total_voided`. Voids
        do NOT enter `amount_applied`, so commission release (CR #66)
        correctly excludes them via the existing paid-ratio math.
        """
        manual = self._load_manual_allocations()
        bills = self.repository.get_all_bills(manual_allocations_by_payment=manual)
        self._apply_voids_to_bills(bills)
        return bills

    def _apply_voids_to_bills(self, bills: Dict[str, Dict[str, Any]]) -> None:
        """CR #68: layer Postgres `payable_bill_voids` rows on top of the
        Oracle replay output. Mutates each bill in place to add:

        - `total_voided` (int)
        - `voids_internal` (list of {void_id, amount, reason, voided_at,
           voided_by}, newest-first) — used by `get_bill_detail`.

        Then recomputes `remaining_unpaid` and `status` using the void-
        aware formula so every downstream consumer (queue suggested_bills,
        employees, reconcile cap, etc.) sees the post-void state.
        """
        voids_by_bill = self._load_voids_by_bill_sid()
        for sid, bill in bills.items():
            voids = voids_by_bill.get(sid, [])
            total_voided = sum(v['amount'] for v in voids)
            bill['total_voided'] = total_voided
            bill['voids_internal'] = voids
            # Void-aware remaining + status.
            original = int(bill['original_charge'])
            paid = int(bill['total_paid'])
            new_remaining = max(0, original - paid - total_voided)
            bill['remaining_unpaid'] = new_remaining
            if new_remaining == 0:
                bill['status'] = 'fully_paid'
            elif paid == 0 and total_voided == 0:
                bill['status'] = 'open'
            else:
                bill['status'] = 'partial'

    def _load_voids_by_bill_sid(self) -> Dict[str, List[Dict[str, Any]]]:
        """CR #68: read all `payable_bill_voids` rows once per request,
        grouped by bill_sid (newest-first). Empty dict on connection
        failure — voids cleanly fall back to "no voids on file" which
        keeps the bill view working with stale data."""
        conn = get_postgres_connection()
        if conn is None:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT void_id, bill_sid, amount, reason,
                           TO_CHAR(voided_at, 'YYYY-MM-DD') AS voided_at_str,
                           voided_by
                    FROM payable_bill_voids
                    ORDER BY voided_at DESC, void_id DESC
                ''')
                rows = cur.fetchall()
        except Exception as e:
            print(f'[WARN] AP _load_voids_by_bill_sid failed: {e}')
            return {}
        finally:
            try:
                conn.close()
            except Exception:
                pass
        out: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r[1], []).append({
                'void_id':   str(r[0]),
                'amount':    int(r[2]),
                'reason':    r[3],
                'voided_at': r[4],
                'voided_by': r[5],
            })
        return out

    # ------------------------------------------------------------------
    # CR #70 — remake / corrective payment tag
    # ------------------------------------------------------------------

    def _load_active_remakes(self) -> Dict[str, Dict[str, Any]]:
        """CR #70: map `payment_doc_sid` → {tagged_at, tagged_by} for every
        payment currently tagged as a remake (active row in
        `payable_payment_remakes` where `untagged_at IS NULL`).

        Used by `_build_pending_payment_rows` to override the computed
        status to `'remake'` and surface the audit fields on the
        PendingPayment row. Empty dict on connection failure — payments
        cleanly fall back to their computed status.
        """
        conn = get_postgres_connection()
        if conn is None:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT payment_doc_sid,
                           TO_CHAR(tagged_at, 'YYYY-MM-DD') AS tagged_at_str,
                           tagged_by
                    FROM payable_payment_remakes
                    WHERE untagged_at IS NULL
                ''')
                rows = cur.fetchall()
        except Exception as e:
            print(f'[WARN] AP _load_active_remakes failed: {e}')
            return {}
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return {
            r[0]: {'tagged_at': r[1], 'tagged_by': r[2]}
            for r in rows
        }

    # ------------------------------------------------------------------
    # CR #69 — per-item allocation (proportional vs priority)
    # ------------------------------------------------------------------

    @staticmethod
    def _assign_per_item(
        items_in_order: List[str],
        item_revenue_by_upc: Dict[str, int],
        amount: int,
    ) -> List[Tuple[str, int]]:
        """Priority-fill algorithm: walk `items_in_order`, take
        `min(item.revenue_with_vat, remaining)` from each, stop when
        amount is exhausted. Returns [(upc, amount_assigned), ...] in
        order_index sequence.

        Raises ValueError if the items' combined revenue can't absorb
        the full amount — that should never reach this helper because
        validation catches it earlier, but a defensive check keeps the
        write transactional.
        """
        remaining = amount
        out: List[Tuple[str, int]] = []
        for upc in items_in_order:
            rev = int(item_revenue_by_upc.get(upc) or 0)
            take = min(rev, remaining)
            out.append((upc, take))
            remaining -= take
            if remaining == 0:
                break
        if remaining > 0:
            raise ValueError(
                f'amount {amount:,} exceeds combined revenue of selected items'
            )
        return out

    def _load_reconciliation_items_for_payments(
        self, payment_doc_sids: List[str],
    ) -> Dict[Tuple[str, str, str], List[Dict[str, Any]]]:
        """CR #69: load per-item assignment rows joined to their parent
        reconciliation, keyed by (payment_doc_sid, bill_sid, tender_category).

        Each value is a list of `{upc, order_index, amount_assigned}` in
        order_index sequence. Empty dict on connection failure — callers
        treat missing keys as proportional mode (existing behaviour).

        CR #72: keyed by tender_category as well — a split-tender payment
        can have separate priority lists for its cash_card and
        gift_certificate slices of the same bill. Commission release in
        `compute_released_for_month` consults only the cash_card slice;
        the queue display surfaces each allocation row's own items list.
        """
        if not payment_doc_sids:
            return {}
        conn = get_postgres_connection()
        if conn is None:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT r.payment_doc_sid, r.bill_sid, r.tender_category,
                           i.upc, i.order_index, i.amount_assigned
                    FROM payable_reconciliations r
                    JOIN payable_reconciliation_items i
                         ON i.reconciliation_id = r.id
                    WHERE r.payment_doc_sid = ANY(%s)
                    ORDER BY r.payment_doc_sid, r.bill_sid,
                             r.tender_category, i.order_index
                ''', (list(payment_doc_sids),))
                rows = cur.fetchall()
        except Exception as e:
            print(f'[WARN] AP _load_reconciliation_items_for_payments failed: {e}')
            return {}
        finally:
            try:
                conn.close()
            except Exception:
                pass
        out: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        for r in rows:
            key = (r[0], r[1], r[2] or 'cash_card')
            out.setdefault(key, []).append({
                'upc':             r[3],
                'order_index':     int(r[4]),
                'amount_assigned': int(r[5]),
            })
        return out

    def _load_manual_allocations(self) -> Dict[str, List[Dict[str, Any]]]:
        """Map payment_doc_sid → list of its linkage rows from
        `payable_reconciliations`. Shape matches what the replay's Pass
        1.5 consumes. Empty dict on connection failure.

        This output is also the canonical input to `get_all_bills` so the
        bill view and queue endpoint see identical replay state.

        CR #72: each row carries `tender_category` ('cash_card' or
        'gift_certificate'). The replay treats both categories as
        bill-clearing (both reduce `remaining_unpaid`); only commission
        release filters out 'gift_certificate' downstream.
        """
        conn = get_postgres_connection()
        if conn is None:
            return {}
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT payment_doc_sid, bill_sid, amount_applied,
                           tender_category
                    FROM payable_reconciliations
                ''')
                rows = cur.fetchall()
        except Exception as e:
            print(f'[WARN] AP _load_manual_allocations failed: {e}')
            return {}
        finally:
            try:
                conn.close()
            except Exception:
                pass
        out: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r[0], []).append({
                'bill_sid':        r[1],
                'amount_applied':  int(r[2]),
                'tender_category': r[3] or 'cash_card',
            })
        return out

    @staticmethod
    def _transpose_to_payment_allocations(
        bills: Dict[str, Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Transpose bills' `payments_chrono` into a per-payment view.

        Each output entry maps `payment_doc_sid` → list of dicts:
            {bill_sid, doc_no, amount_applied, source, payment_date,
             tender_category}
        sorted by `payment_date` (stable across the input bills).

        Used by `get_pending_payments` so its status logic consumes the
        SAME replay output the bill view shows (CR #62). `tender_category`
        is plumbed through for CR #72 — a single (payment, bill) can have
        BOTH cash_card and gift_certificate entries on a split-tender doc.
        """
        out: Dict[str, List[Dict[str, Any]]] = {}
        for bill_sid, b in bills.items():
            for entry in b.get('payments', []):
                out.setdefault(entry['payment_doc_sid'], []).append({
                    'bill_sid':        bill_sid,
                    'doc_no':          b['doc_no'],
                    'amount_applied':  entry['amount_applied'],
                    'source':          entry['source'],
                    'payment_date':    entry['payment_date'],
                    'tender_category': entry.get('tender_category') or 'cash_card',
                })
        for entries in out.values():
            entries.sort(key=lambda e: (e['payment_date'], e['bill_sid']))
        return out

    # Source-of-truth priority order for queue match_source (CR #62):
    # if a payment's allocations span both sources, the row reports the
    # highest-priority source. ref_sale_sid > manual. v2.0.0 removed FIFO
    # so only these two sources exist.
    _MATCH_SOURCE_PRIORITY = {'ref_sale_sid': 0, 'manual': 1}

    # ------------------------------------------------------------------
    # CR #72 — tender-category classification
    # ------------------------------------------------------------------
    # Backend-side allowlist of tender names that DON'T release commission.
    # The frontend (per CR #72 Q1) defers to backend on this taxonomy.
    # v1 covers only Gift Certificate. Other non-releasing tenders
    # (deposits, store credit) are out-of-scope per the CR spec.
    _NON_RELEASING_TENDER_NAMES = frozenset({'Gift Certificate'})

    _TENDER_CATEGORY_CASH_CARD = 'cash_card'
    _TENDER_CATEGORY_GIFT_CERT = 'gift_certificate'
    _ALLOWED_TENDER_CATEGORIES = (
        _TENDER_CATEGORY_CASH_CARD, _TENDER_CATEGORY_GIFT_CERT,
    )

    @classmethod
    def _is_commission_releasing_tender(cls, tender_name: Optional[str]) -> bool:
        """A money-in tender leg releases commission unless it's in the
        non-releasing allowlist. `Charge` (the AR-reduction offset) is never
        passed to this — it's filtered before the call."""
        return (tender_name or '') not in cls._NON_RELEASING_TENDER_NAMES

    @classmethod
    def _summarize_tender_breakdown(
        cls, legs: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """CR #72 / CR #76: project raw `rps.tender` rows into the wire
        shape and compute the per-category split of the doc's AR reduction.

        Returns `(tender_breakdown, commission_releasing_amount,
        gift_certificate_amount)`:
        - `tender_breakdown`: full leg list, each enriched with
          `is_commission_releasing`. Includes BOTH positive money-in
          legs AND the offsetting negative Charge legs (frontend wants
          the raw rps.tender view per the spec).
        - `commission_releasing_amount`: the real-money (cash/card) slice
          of the doc's AR reduction.
        - `gift_certificate_amount`: the gift-certificate slice of the
          doc's AR reduction.

        The two totals ALWAYS sum to the doc's AR reduction — the magnitude
        of its net (negative) `Charge`, which equals `payment.amount` in the
        replay. That is the Retail Pro tender-balance invariant.

        CR #76 — sale-with-paydown docs: a doc can BOTH sell goods and pay
        down AR (e.g. a customer settles an old bill with a gift certificate
        while buying fresh goods paid by bank transfer; the GC overpays and
        the excess posts as a negative `Charge` that clears the older bill).
        There the raw money-in (bank transfer + GC) OVERSTATES the AR
        reduction by `sale_total`. Summing all positive legs (the pre-CR#76
        behavior) therefore broke the invariant and could mis-tag a fresh
        sale's real-money tender as commission-releasing AR payment. We now
        cap the totals to the actual AR reduction and attribute it
        GIFT-FIRST: real money is presumed to fund the fresh goods, the
        gift/overpayment funds the paydown. For a pure payment doc
        (`sale_total = 0`) the money-in exactly funds the Charge, so this
        reduces to the identity split. Empty input → ([], 0, 0).
        """
        wire_legs: List[Dict[str, Any]] = []
        raw_cash = 0
        raw_gift = 0
        charge_sum = 0
        for leg in legs:
            name = leg.get('tender_name')
            amt = int(leg.get('amount') or 0)
            # `is_commission_releasing` applies to money-in tender legs only
            # (MC / Cash / Gift Certificate / etc.). The `Charge` legs are
            # AR-reduction offsets — not customer-paid tenders — so they
            # carry False here. Frontend can iterate the full leg list
            # without special-casing Charge.
            if (name or '') == 'Charge':
                is_releasing = False
            else:
                is_releasing = cls._is_commission_releasing_tender(name)
            wire_legs.append({
                'tender_sid':              leg.get('tender_sid'),
                'tender_name':             name,
                'amount':                  amt,
                'is_commission_releasing': is_releasing,
            })
            # Accumulate the net Charge separately — its magnitude is the
            # doc's true AR reduction and the cap for the two totals below.
            if (name or '') == 'Charge':
                charge_sum += amt
                continue
            # Raw money-in by category, BEFORE the sale-portion cap.
            if amt <= 0:
                continue
            if is_releasing:
                raw_cash += amt
            else:
                raw_gift += amt
        # Cap the category totals to the actual AR reduction (magnitude of
        # the net negative Charge), attributing it gift-first. On a pure
        # payment doc `raw_cash + raw_gift` already equals this, so the caps
        # are no-ops; on a sale-with-paydown doc they strip the sale portion
        # (which the money-in also funded) out of the AR totals. A non-
        # negative net Charge (a bill doc, no AR reduction) yields (0, 0).
        ar_reduction = max(0, -charge_sum)
        gift_cert = min(raw_gift, ar_reduction)
        cash_card = ar_reduction - gift_cert
        return wire_legs, cash_card, gift_cert

    # CR #67 state model — uniform across queue + reconcile responses.
    # CR #70 adds `'remake'` — applied as an OVERRIDE in
    # `_build_pending_payment_rows` (not via `_payment_status_for` since
    # remake state lives in `payable_payment_remakes`, not in the
    # payment/applied math). `is_overdue` continues to gate ONLY on
    # `(unmatched AND past_month)` — remake is never overdue.
    _ALLOWED_PAYMENT_STATUSES = (
        'unmatched', 'matched_partially', 'matched_pending', 'released', 'remake',
    )

    @staticmethod
    def _payment_status_for(
        amount: int, applied: int, pay_month: str, current_month: str,
    ) -> Tuple[str, bool]:
        """CR #67 transition rule. Returns (status, is_overdue).

            sum == 0                              → unmatched
            0 < sum < payment.amount              → matched_partially
            sum == payment.amount   AND   past    → released
            sum == payment.amount   AND   current → matched_pending
        """
        is_past = pay_month < current_month
        if applied <= 0:
            return ('unmatched', is_past)
        if applied < amount:
            return ('matched_partially', False)
        # applied >= amount; defensively the reconcile validator caps at amount.
        if is_past:
            return ('released', False)
        return ('matched_pending', False)

    def get_pending_payments(self, status: Optional[str] = None) -> Dict[str, Any]:
        """Payments queue — surfaces every Charge payment with its CR #67
        state. Released past-month payments stay in the queue so finance
        can edit allocations after release.

        Status logic — uniform rule, see `_payment_status_for`:
        - applied = 0                          → `unmatched` (overdue if past)
        - 0 < applied < amount                 → `matched_partially`
        - applied = amount AND past month      → `released`
        - applied = amount AND current month   → `matched_pending`

        Each row carries `suggested_bills` = the customer's open + partial
        bill list (oldest-first). Walk-in payments get `[]`.

        Returns ServiceResult<PendingPayment[]>, sorted by payment_date desc.
        """
        if status and status not in self._ALLOWED_PAYMENT_STATUSES:
            allowed = ', '.join(repr(s) for s in self._ALLOWED_PAYMENT_STATUSES)
            return {
                'success': False,
                'error': f"status must be one of {allowed} (got {status!r})",
            }

        payments = self.repository.get_charge_payments()
        if not payments:
            return {'success': True, 'data': []}

        # Single replay drives BOTH the bill view's chronology and this queue,
        # so they cannot disagree.
        bills = self._load_bills()
        payment_allocations = self._transpose_to_payment_allocations(bills)

        return {
            'success': True,
            'data': self._build_pending_payment_rows(
                payments_meta=payments,
                bills=bills,
                payment_allocations=payment_allocations,
                status_filter=status,
            ),
        }

    def _build_pending_payment_rows(
        self,
        payments_meta: List[Dict[str, Any]],
        bills: Dict[str, Dict[str, Any]],
        payment_allocations: Dict[str, List[Dict[str, Any]]],
        status_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Project `(payments, bills, allocations)` into PendingPayment rows.

        Factored out of `get_pending_payments` so `reconcile` can build
        the same shape for its embedded `ReconcileResult.payment` (CR #67).
        Always sorted by payment_date descending.

        CR #69: each `allocations[i]` is enriched with `items: string[] | null`
        — the priority order stored at write time. Proportional rows
        get `null`.
        """
        # Suggested_bills: precompute customer → open+partial-bill list.
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
        for sugg in open_bills_by_customer.values():
            sugg.sort(key=lambda b: b['created_date'])

        # CR #69: load per-item priority lists for all payments in scope,
        # keyed by (payment_doc_sid, bill_sid). Empty when no priority rows
        # exist — every allocation defaults to items=null (proportional).
        per_item_for_pay_bill = self._load_reconciliation_items_for_payments(
            [p['payment_doc_sid'] for p in payments_meta]
        )
        # CR #70: active remake tags override the computed status.
        active_remakes = self._load_active_remakes()
        # CR #72: per-payment tender breakdown (MC / Cash / Gift Certificate
        # legs). One Oracle round-trip for all in-scope payments.
        tender_legs_by_payment = self.repository.get_tender_breakdowns(
            [p['payment_doc_sid'] for p in payments_meta]
        )

        current_month = _current_month_str(today)
        out: List[Dict[str, Any]] = []
        for p in payments_meta:
            pay_month = p['payment_date'][:7]
            allocs = payment_allocations.get(p['payment_doc_sid'], [])
            applied = sum(a['amount_applied'] for a in allocs)

            remake_info = active_remakes.get(p['payment_doc_sid'])
            if remake_info is not None:
                # CR #70: remake override — payment is excluded from queue
                # action ("settled"); allocations dropped from display;
                # is_overdue cleared; surface tagged_at + tagged_by.
                payment_status = 'remake'
                is_overdue = False
                allocs_for_display: List[Dict[str, Any]] = []
                match_source = None
                remake_tagged_at = remake_info['tagged_at']
                remake_tagged_by = remake_info['tagged_by']
            else:
                payment_status, is_overdue = self._payment_status_for(
                    amount=p['amount'], applied=applied,
                    pay_month=pay_month, current_month=current_month,
                )
                allocs_for_display = allocs
                match_source = None  # filled below if non-empty
                remake_tagged_at = None
                remake_tagged_by = None

            if status_filter and status_filter != payment_status:
                continue

            if allocs_for_display:
                # Highest-priority source across the allocations wins.
                match_source = min(
                    (a['source'] for a in allocs_for_display),
                    key=lambda s: self._MATCH_SOURCE_PRIORITY.get(s, 99),
                )
                allocations_out = []
                # CR #69 priority items keyed by (payment, bill, tender_cat) —
                # split-tender allocations on the same bill may carry their
                # own per-tender items lists (or both None for proportional).
                for a in allocs_for_display:
                    alloc_tender_cat = a.get('tender_category') or 'cash_card'
                    item_rows = per_item_for_pay_bill.get(
                        (p['payment_doc_sid'], a['bill_sid'], alloc_tender_cat)
                    )
                    items_field: Optional[List[str]] = (
                        [row['upc'] for row in item_rows] if item_rows else None
                    )
                    allocations_out.append({
                        'bill_sid':        a['bill_sid'],
                        'doc_no':          a['doc_no'],
                        'amount_applied':  a['amount_applied'],
                        'items':           items_field,
                        'tender_category': alloc_tender_cat,
                    })
            else:
                allocations_out = []

            # CR #72: tender breakdown + per-category amounts.
            raw_legs = tender_legs_by_payment.get(p['payment_doc_sid'], [])
            tender_breakdown, releasing_amt, gift_amt = (
                self._summarize_tender_breakdown(raw_legs)
            )

            out.append({
                'payment_doc_sid':              p['payment_doc_sid'],
                'payment_doc_no':               p['payment_doc_no'],
                'payment_date':                 p['payment_date'],
                'doc_store_code':               p['doc_store_code'],
                'customer_sid':                 p['customer_sid'],
                'customer_name':                p['customer_name'],
                'amount':                       p['amount'],
                'notes_lostdoc':                p['notes_lostdoc'],
                'status':                       payment_status,
                'match_source':                 match_source,
                'is_overdue':                   is_overdue,
                'allocations':                  allocations_out,
                'suggested_bills':  (
                    open_bills_by_customer.get(p['customer_sid'], [])
                    if (p['customer_sid'] and payment_status != 'remake') else []
                ),
                # CR #70: audit fields. Both null when status ≠ 'remake'.
                'remake_tagged_at':             remake_tagged_at,
                'remake_tagged_by':             remake_tagged_by,
                # CR #72: split-tender breakdown for the dual-panel UX.
                'tender_breakdown':             tender_breakdown,
                'commission_releasing_amount':  releasing_amt,
                'gift_certificate_amount':      gift_amt,
            })
        out.sort(key=lambda r: r['payment_date'], reverse=True)
        return out

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
        - allocations is a list (may be empty — CR #67: clears all linkages).
        - Each allocation: bill_sid (digit) + amount (positive int).
        - `SUM(allocations.amount) <= payment.amount` (CR #67: partial
          and empty allocations supported; only overflow rejected).
        - For each allocation: existing reconciliations' sum + new amount
          ≤ bill.original_charge - total_voided (cumulative cap; CR #68).
        - CR #69: optional `items: string[]` per allocation. When provided:
          UPCs must belong to the bill, no duplicates, and the sum of those
          items' revenue must be ≥ the allocation amount. Empty list /
          missing / null all normalize to proportional mode (no per-item
          rows written).

        Behavior:
        - First reconcile for the payment → INSERT rows.
        - Existing rows with identical shape (incl. items lists) → no-op
          (200, idempotent).
        - Existing rows with different shape → edit (DELETE + INSERT in
          one tx). The CASCADE FK on `payable_reconciliation_items`
          drops the old per-item rows automatically.
        - Empty allocations + existing rows → clear all linkages (DELETE).
        - Concurrent edit (row count changed mid-tx) → 409.

        Returns ServiceResult<ReconcileResult>:
        - outcome: one of `unmatched | matched_partially | matched_pending |
          released` (CR #67) — computed per the same uniform rule the
          queue endpoint uses.
        - affected_months: distinct YYYY-MM of touched payments (one in v1).
        - For `released` and past-month `matched_partially` outcomes:
          affected_employee_count + total_release_amount so the frontend
          toast can summarize.
        - payment: full PendingPayment shape post-write (CR #67); items[]
          per allocation reflects what was just written (CR #69).
        """
        # ── 1. Validate inputs (CR #69 items[]; CR #72 tender_category) ──
        if not str(payment_doc_sid).isdigit():
            return {'success': False, 'error': 'payment_doc_sid must be a numeric string'}
        if not isinstance(allocations, list):
            return {'success': False, 'error': 'allocations must be a list'}
        # Each entry: (bill_sid_str, amount_int, items_list_or_None, tender_category).
        # `items_list_or_None` is None for proportional, list[str] (≥1 UPC) for priority.
        # `tender_category` is 'cash_card' or 'gift_certificate' (defaults
        # to 'cash_card' when the frontend omits the field — pre-CR #72
        # clients still work).
        validated: List[Tuple[str, int, Optional[List[str]], str]] = []
        for a in allocations:
            b_sid = str(a.get('bill_sid') or '')
            if not b_sid.isdigit():
                return {'success': False, 'error': f'allocation.bill_sid must be numeric; got {b_sid!r}'}
            amt = a.get('amount')
            if not isinstance(amt, int) or amt <= 0:
                return {'success': False, 'error': f'allocation.amount must be a positive int; got {amt!r}'}
            # CR #69: optional items[]. Empty list / missing / null →
            # proportional mode (stored as None internally).
            raw_items = a.get('items')
            if raw_items in (None, []):
                items_list = None
            elif isinstance(raw_items, list):
                if not all(isinstance(u, str) and u.strip() for u in raw_items):
                    return {'success': False, 'error': 'allocation.items must be a list of non-empty UPC strings'}
                # Dedup check — per CR #69 spec, same UPC twice is a 400.
                seen = set()
                for u in raw_items:
                    if u in seen:
                        return {
                            'success': False,
                            'error': f'allocation.items contains duplicate UPC {u!r}',
                        }
                    seen.add(u)
                items_list = list(raw_items)   # preserve order — that's the priority sequence
            else:
                return {'success': False, 'error': 'allocation.items must be a list or null'}
            # CR #72: tender_category — required for GC, defaults to
            # cash_card otherwise (back-compat for pre-1.7.0 frontend).
            tender_cat = a.get('tender_category') or self._TENDER_CATEGORY_CASH_CARD
            if tender_cat not in self._ALLOWED_TENDER_CATEGORIES:
                return {
                    'success': False,
                    'error': (
                        f'allocation.tender_category must be one of '
                        f'{self._ALLOWED_TENDER_CATEGORIES}; got {tender_cat!r}'
                    ),
                }
            validated.append((b_sid, amt, items_list, tender_cat))
        total_alloc = sum(amt for _, amt, _, _ in validated)

        # ── 2. Fetch payment from Oracle ──────────────────────────────
        payment = self.repository.get_payment_meta(payment_doc_sid)
        if not payment:
            return {
                'success': False,
                'error': f'Payment {payment_doc_sid} not found or not a Charge receipt',
                'not_found': True,
            }
        if total_alloc > payment['amount']:
            return {
                'success': False,
                'error': (
                    f'allocations sum ({total_alloc:,}) must not exceed '
                    f'payment amount ({payment["amount"]:,})'
                ),
            }

        # ── 2b. CR #72: per-category caps from the payment's tender breakdown ──
        # Skip the Oracle round-trip if every allocation is cash_card AND
        # the total already fits payment.amount (the legacy invariant) —
        # cash_card-only requests don't need the breakdown.
        if any(cat == self._TENDER_CATEGORY_GIFT_CERT for _, _, _, cat in validated):
            tender_legs = self.repository.get_tender_breakdowns([payment_doc_sid]).get(
                payment_doc_sid, [],
            )
            _legs, releasing_cap, gift_cap = self._summarize_tender_breakdown(tender_legs)
            cash_total = sum(
                amt for _, amt, _, cat in validated
                if cat == self._TENDER_CATEGORY_CASH_CARD
            )
            gift_total = sum(
                amt for _, amt, _, cat in validated
                if cat == self._TENDER_CATEGORY_GIFT_CERT
            )
            if cash_total > releasing_cap:
                return {
                    'success': False,
                    'error': (
                        f'cash/card allocations sum ({cash_total:,}) must not '
                        f'exceed the payment\'s commission-releasing amount '
                        f'({releasing_cap:,})'
                    ),
                }
            if gift_total > gift_cap:
                return {
                    'success': False,
                    'error': (
                        f'gift-certificate allocations sum ({gift_total:,}) '
                        f'must not exceed the payment\'s gift-certificate '
                        f'amount ({gift_cap:,})'
                    ),
                }

        # ── 3. Bill lookups (single ledger replay) ────────────────────
        bills = self._load_bills()
        for b_sid, amt, _items, _tc in validated:
            if b_sid not in bills:
                return {
                    'success': False,
                    'error': f'Bill {b_sid} not found in active AP ledger',
                    'not_found': True,
                }

        # ── 3b. CR #69: validate priority items against the bill ──────
        # Per-bill item-revenue map for any allocation that has items[].
        priority_bill_sids = [b for b, _, items, _ in validated if items]
        item_rev_by_bill: Dict[str, Dict[str, int]] = {}
        if priority_bill_sids:
            bill_item_rows = self.repository.get_bill_items(priority_bill_sids)
            for r in bill_item_rows:
                item_rev_by_bill.setdefault(r['bill_sid'], {})[r['upc']] = int(r['revenue_with_vat'] or 0)
            for b_sid, amt, items, _tc in validated:
                if not items:
                    continue
                bill_items = item_rev_by_bill.get(b_sid, {})
                for upc in items:
                    if upc not in bill_items:
                        return {
                            'success': False,
                            'error': (
                                f'UPC {upc!r} is not part of bill '
                                f'#{bills[b_sid]["doc_no"]}'
                            ),
                        }
                sum_rev = sum(bill_items[upc] for upc in items)
                if amt > sum_rev:
                    return {
                        'success': False,
                        'error': (
                            f'allocation for bill #{bills[b_sid]["doc_no"]}: '
                            f'amount ({amt:,}) exceeds combined revenue '
                            f'({sum_rev:,}) of selected items'
                        ),
                    }

        # ── 4. Transaction: read existing, enforce cap, write ────────
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}
        try:
            with conn.cursor() as cur:
                # Existing parent linkages for THIS payment.
                # CR #72: a single (payment, bill) can now have TWO rows
                # (one per tender_category). Idempotency compares the full
                # (bill, amount, items, tender_category) shape.
                cur.execute('''
                    SELECT bill_sid, amount_applied, tender_category
                    FROM payable_reconciliations
                    WHERE payment_doc_sid = %s
                    ORDER BY bill_sid, tender_category
                ''', (str(payment_doc_sid),))
                existing = [
                    (r[0], int(r[1]), r[2] or 'cash_card') for r in cur.fetchall()
                ]

                # CR #69: existing per-item rows. Keyed by (bill, tender_category)
                # since CR #72 splits the parent into two rows per (pay, bill).
                cur.execute('''
                    SELECT r.bill_sid, r.tender_category, i.upc, i.order_index
                    FROM payable_reconciliations r
                    JOIN payable_reconciliation_items i ON i.reconciliation_id = r.id
                    WHERE r.payment_doc_sid = %s
                    ORDER BY r.bill_sid, r.tender_category, i.order_index
                ''', (str(payment_doc_sid),))
                existing_items_by_key: Dict[Tuple[str, str], List[str]] = {}
                for r in cur.fetchall():
                    existing_items_by_key.setdefault(
                        (r[0], r[1] or 'cash_card'), [],
                    ).append(r[2])

                # Recast existing rows into the same shape for comparison.
                existing_full = sorted(
                    (b, amt, tuple(existing_items_by_key.get((b, tc), [])), tc)
                    for b, amt, tc in existing
                )
                requested_full = sorted(
                    (b, amt, tuple(items or []), tc)
                    for b, amt, items, tc in validated
                )
                if existing_full == requested_full:
                    conn.commit()
                    return self._reconcile_response(
                        payment=payment, allocations=validated, bills=bills,
                    )

                # Cumulative cap (CR #68 void-aware). Cap applies to TOTAL
                # allocations against the bill regardless of category — a
                # bill's cap is a property of the bill, not the tender mix.
                own_by_bill: Dict[str, int] = {}
                for b_sid, amt, _tc in existing:
                    own_by_bill[b_sid] = own_by_bill.get(b_sid, 0) + amt
                # Pre-sum the requested allocations per bill to enforce the
                # cap across BOTH categories on a split-tender row.
                new_by_bill: Dict[str, int] = {}
                for b_sid, new_amt, _items, _tc in validated:
                    new_by_bill[b_sid] = new_by_bill.get(b_sid, 0) + new_amt
                for b_sid, bill_new_total in new_by_bill.items():
                    cur.execute('''
                        SELECT COALESCE(SUM(amount_applied), 0)
                        FROM payable_reconciliations
                        WHERE bill_sid = %s
                    ''', (b_sid,))
                    other_total = int(cur.fetchone()[0]) - own_by_bill.get(b_sid, 0)
                    bill = bills[b_sid]
                    cap = int(bill['original_charge']) - int(bill.get('total_voided', 0))
                    if other_total + bill_new_total > cap:
                        conn.rollback()
                        overflow = other_total + bill_new_total - cap
                        return {
                            'success': False,
                            'error': (
                                f'Bill #{bill["doc_no"]} would exceed its '
                                f'available cap by {overflow:,} VND'
                            ),
                        }

                # Replace: DELETE parent rows (CASCADE drops item rows),
                # then INSERT new parents, then SELECT IDs, then INSERT items.
                if existing:
                    cur.execute(
                        'DELETE FROM payable_reconciliations WHERE payment_doc_sid = %s',
                        (str(payment_doc_sid),),
                    )
                    if cur.rowcount != len(existing):
                        conn.rollback()
                        return {
                            'success': False,
                            'error': 'Payment was reconciled by another user — refresh required',
                            'conflict': True,
                        }
                if validated:
                    cur.executemany('''
                        INSERT INTO payable_reconciliations
                            (payment_doc_sid, bill_sid, amount_applied,
                             tender_category, created_by)
                        VALUES (%s, %s, %s, %s, %s)
                    ''', [
                        (str(payment_doc_sid), b_sid, amt, tc, created_by_user_sid)
                        for b_sid, amt, _items, tc in validated
                    ])

                    # CR #69: write per-item rows for priority allocations.
                    # CR #72: id lookup keyed by (bill, tender_category)
                    # so the split-tender case writes items under the
                    # correct parent row.
                    if any(items for _, _, items, _ in validated):
                        cur.execute('''
                            SELECT id, bill_sid, tender_category
                            FROM payable_reconciliations
                            WHERE payment_doc_sid = %s
                        ''', (str(payment_doc_sid),))
                        id_by_key = {
                            (r[1], r[2] or 'cash_card'): r[0] for r in cur.fetchall()
                        }

                        item_rows: List[Tuple[int, str, int, int]] = []
                        for b_sid, amt, items, tc in validated:
                            if not items:
                                continue
                            assignments = self._assign_per_item(
                                items_in_order=items,
                                item_revenue_by_upc=item_rev_by_bill[b_sid],
                                amount=amt,
                            )
                            recon_id = id_by_key[(b_sid, tc)]
                            for order_index, (upc, assigned) in enumerate(assignments):
                                item_rows.append(
                                    (recon_id, upc, order_index, assigned)
                                )
                        if item_rows:
                            cur.executemany('''
                                INSERT INTO payable_reconciliation_items
                                    (reconciliation_id, upc, order_index, amount_assigned)
                                VALUES (%s, %s, %s, %s)
                            ''', item_rows)
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
        self, *, payment: Dict[str, Any],
        allocations: List[Tuple[str, int, Optional[List[str]], str]],
        bills: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build the ReconcileResult (CR #67; CR #69 priority-aware):
        - `outcome` ∈ {unmatched, matched_partially, matched_pending, released}
          computed via the uniform `_payment_status_for` rule.
        - `affected_months`: distinct YYYY-MM of touched payments (one in v1).
        - `affected_employee_count` + `total_release_amount`: computed for
          past-month outcomes. CR #69: priority allocations release per
          item's `amount_assigned`; proportional uses the per-bill share
          ratio (existing math).
        - `payment`: full PendingPayment shape post-write (CR #67); items[]
          per allocation reflects what was just stored.
        """
        today = _today()
        pay_month = payment['payment_date'][:7]
        cur_month = _current_month_str(today)
        total_alloc = sum(amt for _, amt, _, _ in allocations)
        outcome, _is_overdue = self._payment_status_for(
            amount=payment['amount'], applied=total_alloc,
            pay_month=pay_month, current_month=cur_month,
        )

        result: Dict[str, Any] = {
            'outcome':         outcome,
            'affected_months': [pay_month],
        }

        # Release totals: computed for past-month allocations with money on them.
        # CR #72: only cash_card allocations release commission. GC
        # allocations clear the bill but contribute 0 to the toast.
        cash_card_allocs = [
            (b, amt, items)
            for b, amt, items, tc in allocations
            if tc == self._TENDER_CATEGORY_CASH_CARD
        ]
        cash_card_total = sum(amt for _, amt, _ in cash_card_allocs)
        is_past = pay_month < cur_month
        if is_past and cash_card_total > 0:
            alloc_bill_sids = [b_sid for b_sid, _, _ in cash_card_allocs]
            items = self.repository.get_bill_items(alloc_bill_sids)
            auto = self._load_auto_rates(alloc_bill_sids)
            custom = self._load_custom_rates(alloc_bill_sids)
            by_emp: Dict[str, int] = {}
            total_release = 0
            for b_sid, amt, priority_items in cash_card_allocs:
                bill = bills[b_sid]
                if bill['original_charge'] <= 0:
                    continue
                # CR #69: per-item factor map for this (bill, allocation).
                # Proportional → all items share `amt / original_charge`.
                # Priority    → each listed item gets `amt_assigned / item.revenue`;
                #               others get 0 (excluded from release).
                if priority_items:
                    # Recompute the priority-fill assignments locally — the
                    # transaction already committed; we need the same numbers
                    # for the toast. Cheaper than re-querying the join table.
                    rev_by_upc = {
                        it['upc']: int(it['revenue_with_vat'] or 0)
                        for it in items if it['bill_sid'] == b_sid
                    }
                    try:
                        assignments = self._assign_per_item(
                            items_in_order=priority_items,
                            item_revenue_by_upc=rev_by_upc,
                            amount=amt,
                        )
                    except ValueError:
                        # Should never happen — validation rejects this earlier.
                        assignments = []
                    assigned_by_upc = {upc: assigned for upc, assigned in assignments}
                for it in items:
                    if it['bill_sid'] != b_sid:
                        continue
                    a = auto.get((b_sid, it['upc']))
                    c = custom.get((b_sid, it['upc']))
                    effective = c if c is not None else (
                        a['effective_rate'] if (a and a['effective_rate'] is not None) else 0.0
                    )
                    rev_with_vat = int(it['revenue_with_vat'] or 0)
                    if priority_items:
                        # Priority mode — only listed items contribute.
                        amt_for_item = assigned_by_upc.get(it['upc'], 0)
                        if amt_for_item == 0 or rev_with_vat == 0:
                            continue
                        share_ratio = amt_for_item / rev_with_vat
                        released = int(round(rev_with_vat * effective * share_ratio))
                    else:
                        share_ratio = amt / bill['original_charge']
                        released = int(round(rev_with_vat * effective * share_ratio))
                    if released == 0:
                        continue
                    code = it.get('employee_code') or '__unknown__'
                    by_emp[code] = by_emp.get(code, 0) + released
                    total_release += released
            result['affected_employee_count'] = len(by_emp)
            result['total_release_amount'] = total_release
        else:
            result['affected_employee_count'] = 0
            result['total_release_amount'] = 0

        # CR #67: embed the post-write PendingPayment. Re-run the replay
        # so the response reflects the just-committed state. Cost is one
        # extra Oracle round-trip per reconcile; commission's own queue
        # uses the same path on every read.
        result['payment'] = self._pending_payment_for(payment_doc_sid=str(payment['doc_sid']))
        return {'success': True, 'data': result}

    def _pending_payment_for(
        self, payment_doc_sid: str,
    ) -> Optional[Dict[str, Any]]:
        """Reload + project a single payment as a PendingPayment row.

        Used by `reconcile` to embed the post-write payment in its
        response (CR #67). Returns `None` if the payment isn't in the
        active Charge window.
        """
        all_meta = self.repository.get_charge_payments()
        match = next(
            (p for p in all_meta if p['payment_doc_sid'] == payment_doc_sid),
            None,
        )
        if match is None:
            return None
        bills = self._load_bills()
        payment_allocations = self._transpose_to_payment_allocations(bills)
        rows = self._build_pending_payment_rows(
            payments_meta=[match],
            bills=bills,
            payment_allocations=payment_allocations,
        )
        return rows[0] if rows else None

    def unmatch(self, payment_doc_sid: str) -> Dict[str, Any]:
        """Remove every Postgres `payable_reconciliations` row for the payment.

        CR #62: REF_SALE_SID-sourced matches cannot be unmatched here —
        they come from Oracle's `DOCUMENT.REF_SALE_SID` column. If no manual
        row exists, return 400 with guidance to override the REF link by
        inserting a manual reconciliation against the desired bill(s).
        Frontend chose option A — reject — over option B (insert a
        suppression flag).

        Returns `{success, data: {payment_doc_sid, deleted_count}}` on success.
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
                if deleted == 0:
                    conn.rollback()
                    return {
                        'success': False,
                        'error': (
                            'Payment has no manual linkage to remove. '
                            'If it was matched via REF_SALE_SID, reconcile '
                            'manually against the desired bill(s) to override '
                            'the auto-link.'
                        ),
                        'not_found': True,
                    }
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

    def void_remaining(
        self,
        bill_sid: str,
        amount: int,
        reason: Optional[str],
        voided_by: str,
    ) -> Dict[str, Any]:
        """CR #68: write off part (or all) of a bill's remaining balance.

        Terminal — voids in v1 cannot be reversed (operator creates a
        manual payment / credit adjustment instead). The void amount is
        excluded from commission release: it never enters `amount_applied`,
        and the release math in `compute_released_for_month` (CR #66) uses
        `amount_applied / original_charge`, so 0 release on voided VND
        falls out automatically.

        Validations:
        - `bill_sid` is a digit string; the bill exists in the active
          AP ledger.
        - `amount` is a positive int and `<= bill.remaining_unpaid` (the
          post-void formula, so prior voids are already subtracted).
        - `reason` is an optional string (nullable).
        - `voided_by` is the authenticated user's identifier (typically
          email) — resolved at the route layer from Flask `g`.

        Returns ServiceResult<{ bill: BillDetail, void_id: str }>. On the
        same request after writing the void, re-loads the bill via
        `get_bill_detail` so the response reflects the new
        `total_voided` / `remaining_unpaid` / `status`.
        """
        # ── 1. Validate inputs ────────────────────────────────────────
        if not str(bill_sid).isdigit():
            return {'success': False, 'error': 'bill_sid must be a numeric string'}
        if not isinstance(amount, int) or amount <= 0:
            return {'success': False, 'error': 'amount must be a positive int'}
        if reason is not None and not isinstance(reason, str):
            return {'success': False, 'error': 'reason must be a string or null'}
        if not isinstance(voided_by, str) or not voided_by.strip():
            return {'success': False, 'error': 'voided_by must be a non-empty string'}

        # ── 2. Load bills, find target, check the new amount fits ────
        bills = self._load_bills()
        bill = bills.get(str(bill_sid))
        if bill is None:
            return {
                'success': False,
                'error': f'Bill {bill_sid} not found in active AP ledger',
                'not_found': True,
            }
        # `remaining_unpaid` already factors in any prior voids via the
        # `_apply_voids_to_bills` pass.
        remaining = int(bill['remaining_unpaid'])
        if remaining <= 0:
            return {
                'success': False,
                'error': (
                    f'Bill #{bill["doc_no"]} has no remaining balance to void '
                    f'(already fully settled by payments or prior voids)'
                ),
            }
        if amount > remaining:
            return {
                'success': False,
                'error': (
                    f'amount ({amount:,}) exceeds bill remaining '
                    f'({remaining:,})'
                ),
            }

        # ── 3. INSERT void row ───────────────────────────────────────
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO payable_bill_voids
                        (bill_sid, amount, reason, voided_by)
                    VALUES (%s, %s, %s, %s)
                    RETURNING void_id
                ''', (str(bill_sid), amount, reason, voided_by))
                void_id = cur.fetchone()[0]
                conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return {'success': False, 'error': f'void failed: {e}'}
        finally:
            try:
                conn.close()
            except Exception:
                pass

        # ── 4. Build response: refetched BillDetail + new void_id ────
        detail = self.get_bill_detail(str(bill_sid))
        if not detail.get('success'):
            # Highly unlikely (the bill was found above), but surface a
            # clean error rather than dropping the just-inserted void.
            return detail
        return {
            'success': True,
            'data': {
                'bill':    detail['data'],
                'void_id': str(void_id),
            },
        }

    # ------------------------------------------------------------------
    # CR #70 — tag / untag remake (corrective payment)
    # ------------------------------------------------------------------

    def tag_remake(
        self, payment_doc_sid: str, tagged_by: str,
    ) -> Dict[str, Any]:
        """CR #70: tag an unmatched / matched_partially payment as a
        remake (corrective doc — no real outstanding bill behind it).

        Validations:
        - `payment_doc_sid` is a digit string.
        - Payment exists in the active Charge window (else 404).
        - Current status is `unmatched` or `matched_partially`; reject
          `matched_pending` / `released` (operator must unmatch first).
        - No allocation source is `ref_sale_sid` — Oracle-auto-linked
          payments must be fixed at the source bill in Oracle.
        - If already tagged → 200 no-op (idempotent per spec).
        - `tagged_by` is non-empty (typically user email from g.email).

        Side effects (when not a no-op):
        - DELETE any rows in `payable_reconciliations` for this payment
          (per spec: "drops any existing allocations"). CASCADE FK drops
          the per-item rows automatically.
        - INSERT a new active row into `payable_payment_remakes`.

        Returns ServiceResult<PendingPayment> with the updated state
        (status='remake', allocations=[], remake_tagged_at + _by populated).
        """
        if not str(payment_doc_sid).isdigit():
            return {'success': False, 'error': 'payment_doc_sid must be a numeric string'}
        if not isinstance(tagged_by, str) or not tagged_by.strip():
            return {'success': False, 'error': 'tagged_by must be a non-empty string'}

        # Quick existence check via the queue's projection — gives us both
        # the existence guarantee and the computed status in one call.
        existing = self._pending_payment_for(str(payment_doc_sid))
        if existing is None:
            return {
                'success': False,
                'error': f'Payment {payment_doc_sid} not found in active Charge ledger',
                'not_found': True,
            }

        current_status = existing['status']
        # Idempotent: already tagged → return current state.
        if current_status == 'remake':
            return {'success': True, 'data': existing}

        # Reject if the payment is bound to a real bill linkage.
        if current_status in ('matched_pending', 'released'):
            return {
                'success': False,
                'error': (
                    f'Payment {payment_doc_sid} is in state {current_status!r}; '
                    'unmatch first before tagging as remake'
                ),
            }
        if existing.get('match_source') == 'ref_sale_sid':
            return {
                'success': False,
                'error': (
                    f'Payment {payment_doc_sid} is auto-linked via '
                    'REF_SALE_SID; remake tag must be made at the source '
                    'bill in Oracle'
                ),
            }

        # Write: drop any manual reconciliations + INSERT remake row.
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    'DELETE FROM payable_reconciliations WHERE payment_doc_sid = %s',
                    (str(payment_doc_sid),),
                )
                cur.execute('''
                    INSERT INTO payable_payment_remakes
                        (payment_doc_sid, tagged_by)
                    VALUES (%s, %s)
                ''', (str(payment_doc_sid), tagged_by))
                conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return {'success': False, 'error': f'tag-remake failed: {e}'}
        finally:
            try:
                conn.close()
            except Exception:
                pass

        # Re-project the payment so the response reflects the post-tag
        # state (status='remake', allocations=[], audit fields set).
        updated = self._pending_payment_for(str(payment_doc_sid))
        return {'success': True, 'data': updated}

    def untag_remake(
        self, payment_doc_sid: str, untagged_by: str,
    ) -> Dict[str, Any]:
        """CR #70: reverse a remake tag — the payment returns to the
        unmatched queue (with `is_overdue` recomputed from its date).

        Validations:
        - `payment_doc_sid` is a digit string.
        - Payment is currently tagged as remake (else 400).
        - `untagged_by` is non-empty.

        Side effect: UPDATE the active remake row, setting `untagged_at`
        + `untagged_by`. Keeps a history row for finance audit.
        """
        if not str(payment_doc_sid).isdigit():
            return {'success': False, 'error': 'payment_doc_sid must be a numeric string'}
        if not isinstance(untagged_by, str) or not untagged_by.strip():
            return {'success': False, 'error': 'untagged_by must be a non-empty string'}

        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to PostgreSQL'}
        try:
            with conn.cursor() as cur:
                # UPDATE the active row (untagged_at IS NULL). If no
                # active row exists, the rowcount tells us this payment
                # isn't tagged → reject.
                cur.execute('''
                    UPDATE payable_payment_remakes
                    SET untagged_at = NOW(), untagged_by = %s
                    WHERE payment_doc_sid = %s
                      AND untagged_at IS NULL
                ''', (untagged_by, str(payment_doc_sid)))
                if cur.rowcount == 0:
                    conn.rollback()
                    return {
                        'success': False,
                        'error': (
                            f'Payment {payment_doc_sid} is not currently '
                            'tagged as remake'
                        ),
                    }
                conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return {'success': False, 'error': f'untag-remake failed: {e}'}
        finally:
            try:
                conn.close()
            except Exception:
                pass

        updated = self._pending_payment_for(str(payment_doc_sid))
        return {'success': True, 'data': updated}

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
        bills = self._load_bills()
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
