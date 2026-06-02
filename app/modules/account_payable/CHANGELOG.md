# Changelog — account_payable

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.1.1] — 2026-06-02

### Fixed — CR #63: item description sourced from wrong Oracle column

`GET /account-payable/bills/:bill_sid` returned `items[i].description`
as an internal SKU code (e.g. `25HACU00670AD00042`) because the Phase B
SELECT used `INVN_SBS_ITEM.DESCRIPTION1`. Switched to `INVN_SBS_ITEM.TEXT1`
which holds the vendor-prefixed human-readable name
(e.g. `JQM-LA CASQUETTE GADJO SIZE58`).

Affects every endpoint that surfaces `description` (the bill-detail
items table + `EmployeeBillSlice.items[*]` in the employee drilldown)
since they share the same Oracle JOIN.

Oracle column survey (4 candidates inspected against live data):
- `DESCRIPTION1` → SKU/style code (the buggy source)
- `DESCRIPTION2` → vendor-dept-category triple (used by commission)
- `TEXT1`        → vendor-prefixed product name ✓ (the fix)

Live-verified the CR's sample: UPC `218103` now returns
`'JQM-LA CASQUETTE GADJO SIZE58'` instead of `'25HACU00670AD00042'`.
Hand-carry items spot-checked (CGC, ARP, AQU vendors) all surface
human-readable TEXT1 names of the same shape.

### Bumped PATCH — backend bug fix, no shape change

`PayableBillItem.description: string | null` shape unchanged. Frontend
sees strictly better data with no code or type change.

## [1.1.0] — 2026-06-02

### Fixed — CR #62: payments queue must mirror the ledger-replay match state

Backend v1.0.0 reported `unmatched + is_overdue` for every payment that
the bill-view ledger replay had already FIFO-applied to an open AR
bill. Frontend testing surfaced this on live data: **all 72** queue
rows were false positives. After the fix the queue correctly shows 0
rows (all 72 past-month payments are released via FIFO).

#### What changed

- `AccountPayableRepository._replay_charge_ledger_with_chronology` now
  accepts a `manual_allocations_by_payment` arg and runs a Pass 1.5
  between the REF_SALE_SID pass and the FIFO pass. Manual rows
  displace what FIFO would otherwise infer (priority tier 2 > 3).
  Each per-bill chronology entry carries `source ∈ {ref_sale_sid,
  manual, fifo}`.
- `AccountPayableRepository.get_all_bills` gained an optional
  `manual_allocations_by_payment` parameter and threads it through to
  the replay.
- New `AccountPayableService._load_bills()` helper loads manual rows
  once per request and feeds them to `get_all_bills`. Every read
  endpoint (bills, bill-detail, employees, employee-bills, payments)
  now goes through this helper, so the bill view and the payments
  queue are guaranteed to use the same replay output. Single source
  of truth.
- `get_pending_payments` rewritten per CR §"Behaviour matrix":
  - Status derives from `applied = sum(allocations.amount_applied)`
    where allocations are the transpose of the bill chronology.
  - Past-month + `applied > 0` → released → drops from queue
    (including partial FIFO application — applied portion is
    released; remainder is implicit deposit-on-account).
  - Current-month + `applied > 0` → `matched_pending`; `match_source`
    is the highest-priority source across the allocations
    (ref_sale_sid > manual > fifo).
  - `applied == 0` → `unmatched`; `is_overdue` only when past month.
- `unmatch` now rejects with 404 when no manual row exists — FIFO
  inferences are derived, not stored, so they cannot be unmatched
  directly. Error message guides user to reconcile manually instead
  (which displaces FIFO via the priority order on next read).
- `match_source` enum widened: `'ref_sale_sid' | 'manual' | 'fifo' | null`.

#### Tests

- 76 AP unit tests pass (10 queue rewrites covering the full behaviour
  matrix + 4 new replay tests for Pass 1.5: manual-displaces-FIFO,
  manual-runs-after-ref, unknown-bill-falls-to-FIFO, amount-caps-at-bill).
- Existing reconcile / unmatch / PATCH-rate tests updated to stub the
  new `_load_manual_allocations` helper.

#### Live-verified against shared DB

- Queue size: 72 → 0 (all 72 false positives now correctly classified
  as released).
- Bill view unchanged — doc 2797 (Le Thi Van partial, 38.4M with FIFO
  payment 2978 on 2026-05-29) still shows the same chronology.
- 76 AP unit tests pass; smoke-verified bill view + queue against
  live data.

### Behaviour change to surface to frontend

- `MatchSource` TypeScript type needs widening to include `'fifo'`.
- Queue status badge should render a new `'fifo'` chip.
- `ReconcileDialog` opened on a FIFO row needs the "auto inference"
  banner the CR describes.
- `POST /payments/:sid/unmatch` on a FIFO row now returns 404 — the
  frontend should disable the Unmatch button for `match_source==='fifo'`
  rows (per CR §"Edge cases #3 — option A").

### Bumped MINOR — additive shape, behaviour fix for queue

`PaymentsQueue` response shape stays compatible (enum widened, not
narrowed). Bill view chronology gains a new `'manual'` source value
when a Postgres reconciliation row exists; existing clients ignoring
unknown sources keep working.

## [1.0.0] — 2026-06-02

First real release of the Account Payable backend. All 8 endpoints
documented in CR #60 are live, depend only on data already present in
Oracle (Charge tender ledger) and Postgres (the two new AP tables), and
are gated by section permissions via the `ACCOUNT_PAYABLE` section seed.

### Added — read endpoints

- `GET /api/v1/account-payable/bills?search=&status=` — full bill list
  with payment chronology. Search is case-insensitive substring on
  `doc_no` or `customer_name`; status filter accepts
  `open|partial|fully_paid`. Each `payments[]` entry is decorated with
  `release_status` (released when payment month is past, pending when
  current) and `release_month`. Sorted newest-first.
- `GET /api/v1/account-payable/bills/:bill_sid` — full bill detail with
  `items[]`. Each item carries `auto_release_rate` (from commission's
  `payable_bill_rates` snapshot by `(bill_sid, upc)`), `custom_release_rate`
  (from `payable_item_custom_rates`), and the snapshot's `revenue_type` /
  `fp_or_md` when present. Legacy items (no snapshot row) come back with
  null rates so the frontend can flag them. `sale_total_amt` added for
  unpaid-ratio context.
- `GET /api/v1/account-payable/employees` — per-employee running open
  payable. Aggregates each employee's item-share of every open + partial
  bill: `share = item.revenue × bill.remaining_unpaid / bill.sale_total_amt`.
  Fully-paid bills excluded. Returns `PayableEmployee[]` sorted by
  `total_payable` desc.
- `GET /api/v1/account-payable/employees/:employee_code/bills` — one
  employee's open/partial bills with items scoped to that employee and
  `employee_share` precomputed.
- `GET /api/v1/account-payable/payments?status=` — payments queue.
  Source: receipt_type=0 negative-Charge tender events from the past
  24 months (excludes returns + deposit-on-account). Multi-tender-row
  docs aggregate to one queue row. Statuses per the CR's lifecycle rule:
  - `unmatched` — no REF_SALE_SID matching a known bill AND no Postgres
    linkage; `is_overdue=true` when payment month is past.
  - `matched_pending (ref_sale_sid)` — REF_SALE_SID hits a real bill +
    current month. Read-only (kept for future-proofing; 0 hits today).
  - `matched_pending (manual)` — Postgres linkage exists + current month.
  - Released (matched + past month) drops out of the queue.
  Each row carries `suggested_bills` — the customer's open + partial
  bills sorted oldest-first.

### Added — write endpoints

- `POST /api/v1/account-payable/reconcile` — save a manual linkage
  (or replace an existing one).
  - Validates `SUM(allocations.amount) == payment.amount` (no partial
    confirms in v1).
  - Enforces cumulative cap per allocation:
    `SUM(other payments' amount_applied for bill) + new ≤ bill.original_charge`
    — error message names the bill's `doc_no` and the exact overflow.
  - Same shape as existing → idempotent no-op. Different shape → DELETE+
    INSERT in one transaction. Concurrent change detected via DELETE
    rowcount mismatch → 409 `Conflict` (frontend interprets as graceful
    refresh).
  - Returns `ReconcileResult` with `outcome` ('released' for past-month
    payments, 'matched_pending' for current), `affected_months`, and for
    released outcomes `affected_employee_count` + `total_release_amount`
    (computed as `Σ revenue × effective_rate × amount/original_charge`
    over allocated bills, joined to `payable_bill_rates` and custom
    rates). `payment` is null in v1 — full `PendingPayment` shape is
    deferred; frontend should refetch the queue after reconcile.
- `POST /api/v1/account-payable/payments/:payment_doc_sid/unmatch` —
  DELETE every linkage for the payment. Idempotent — returns
  `deleted_count`.
- `PATCH /api/v1/account-payable/bills/:bill_sid/items/:upc/rate` — set
  or clear a per-item custom rate override.
  - `custom_release_rate=null` → DELETE only. No propagation.
  - Set on a non-legacy item (has `payable_bill_rates` row) → UPSERT
    only.
  - Set on a legacy item → UPSERT targeted row + propagate the same
    rate to every other legacy item that classifies to the same
    `(revenue_type, fp_or_md)` across every open/partial bill in the
    SAME creation month. Classification uses a private `_classify_item_bucket`
    helper that mirrors commission's CR #61 priority chain.
  - Returns `UpdateItemRateResult` with refreshed `bill` + `propagated_to_bill_count`
    + `propagated_item_count`.

### Added — schema

- `payable_reconciliations` Postgres table (manual payment→bill linkages).
- `payable_item_custom_rates` Postgres table (per-item release-rate
  overrides). Both idempotently created in `AccountPayableService.init_database`
  and wired into `scripts/database/init_db.py`.

### Added — permissions seed

- `ACCOUNT_PAYABLE` section added to `permissions/service.py`.
  Default departments: HR + ACC. Default roles: ADMIN + MANAGER.
  Section + dept/role mappings seeded by `init_permission_tables` and
  live in shared Postgres.

### Behavior — depends on commission v2.1.0

- Reads `payable_bill_rates` (commission CR #61) for `auto_release_rate`.
  Items on bills whose creation month was never `calculate`-d in v2.1.0+
  show as legacy (null rate). User can override via PATCH /items/.../rate.
- The HEA-as-fashion cutoff (`HEA_AS_FASHION_FROM_MONTH = '2026-04'`)
  is honored by AP's classifier so propagation matches commission's
  bucket logic for any given month.

### Out of scope (per CR §"Out of scope")

- Bulk reconcile (single-payment-per-request in v1).
- Write-off / uncollectable state.
- Audit-trail UI (`created_by`/`set_by` columns populated for v1.1).
- Commission release integration (`released_by_category` in
  `commission/calculate`) — separate follow-up PR on the
  `feature/commission` branch.

### Verified

- 71 unit tests pass (3 schema + 12 repository + 17 reads + 30 writes +
  9 queue).
- Live-verified against shared Oracle + Postgres:
  - 127 bills in the active 24-month window (8 open, 3 partial,
    116 fully_paid). 290M VND outstanding AR.
  - 8 employees with non-zero open payable; reference values cross-check
    against CR #59 memory (GL005=56.98M, GH100=55.84M exact match).
  - 72 unique payment receipts surfaced in the queue, totalling
    ~3.4B VND of unmatched AR going back to 2024 — the backlog the
    frontend will help finance work through.
  - Write paths verified end-to-end: set + read-back + clear of a custom
    rate on a real legacy item (bill 781753658000125384 UPC 218103) with
    self-cleanup so DB returns to its pre-test state.

### Open items for follow-up

- Commission release integration: extend `calculate_personal_commissions`
  to read `payable_reconciliations` and populate `released_by_category`.
  Lives on `feature/commission`, will be commission v2.2.0 (MINOR —
  additive shape, `released_by_category` previously always empty).
- Full `PendingPayment` shape on reconcile response (currently null).

## [0.1.0] — 2026-05-29

Initial scaffold for CR #60. Pre-1.0 — endpoints exist but return 501.

### Added
- Module skeleton: `__init__.py` / `routes.py` / `service.py` /
  `queries.py`.
- 7 endpoint stubs under `/api/v1/account-payable/*`:
  - `GET  /employees`
  - `GET  /employees/:employee_code/bills`
  - `GET  /bills?search=&status=`
  - `GET  /bills/:bill_sid`
  - `GET  /payments?status=`
  - `POST /reconcile`
  - `POST /payments/:payment_doc_sid/unmatch`
- `AccountPayableService.init_database()` creates the
  `payable_reconciliations` Postgres table + indexes idempotently.
- Blueprint registered in `app/main.py`.

### Status
All read/write methods currently raise `NotImplementedError` and the
routes return 501. The frontend on `feature/account-payable` ships with
mocks (`VITE_PAYABLE_MOCK=true`) so UX can be reviewed before backend
implementation begins.

### Related
- CR #60 — full spec in
  `GBL_HR_Frontend/docs/cr/account-payable.md`.
- Contract types — `GBL_HR_Frontend/src/features/account-payable/types.ts`
  is the source of truth for request/response shapes.
- Builds on CR #59 Phase B (commission v2.0.0) — reuses the
  Charge-tender ledger replay from
  `CommissionRepository.get_unpaid_bill_amounts`. Once the linkage table
  is populated, commission's `released_by_category` will be wired up at
  query time.
