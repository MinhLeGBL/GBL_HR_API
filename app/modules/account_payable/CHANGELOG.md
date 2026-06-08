# Changelog — account_payable

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [2.4.0] — 2026-06-08

### Added — CR #70: remake / corrective payment tag

#### Motivation

Cashier-issued corrective payment docs ("remake" to fix an employee
mistake on the original sale) have no real outstanding bill behind
them — the original bill has been settled / voided / replaced. Today
these payments sit forever in the unmatched queue: the operator can't
reconcile (no bill to point at) and can't unmatch (already unmatched).
They distort the queue's `Unmatched` count and push the operator
toward false-positive reconciles against unrelated bills.

CR #70 adds a manual **Tag as remake** operator action: the payment
moves to a new `remake` state, any prior allocations are dropped, and
the payment is excluded from commission release. Reversible via
**Untag remake**.

#### Schema

History-preserving table — each tag inserts a new row; each untag
UPDATEs the active row to set `untagged_at`. Active tag = latest row
with `untagged_at IS NULL`.

```sql
CREATE TABLE payable_payment_remakes (
    id              BIGSERIAL    PRIMARY KEY,
    payment_doc_sid VARCHAR(40)  NOT NULL,
    tagged_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    tagged_by       VARCHAR(255) NOT NULL,
    untagged_at     TIMESTAMPTZ  NULL,
    untagged_by     VARCHAR(255) NULL
);
CREATE INDEX idx_payable_payment_remakes_active
    ON payable_payment_remakes(payment_doc_sid)
    WHERE untagged_at IS NULL;
```

The partial index speeds up the common active-tag lookup; full table
scans only happen for audit queries.

#### Endpoints — new

**`POST /payments/:payment_doc_sid/tag-remake`** (manager)
- 200 with updated `PendingPayment` (idempotent: re-tag returns current state)
- 400 if `match_source == 'ref_sale_sid'` (Oracle-auto-linked — fix at source)
- 400 if status is `matched_pending` or `released` (unmatch first)
- 404 if payment not in active Charge ledger
- Side effect: `DELETE FROM payable_reconciliations WHERE payment_doc_sid = X`
  (per spec: "drops any existing allocations"). The CR #69 CASCADE FK
  drops per-item rows automatically.

**`POST /payments/:payment_doc_sid/untag-remake`** (manager)
- 200 with updated `PendingPayment` (status returns to `unmatched`,
  `is_overdue` recomputed from payment date)
- 400 if payment is not currently tagged as remake
- Side effect: UPDATE active row, set `untagged_at` + `untagged_by`.

#### State model

`PaymentStatus` adds `'remake'`:

```
unmatched | matched_partially | matched_pending | released | remake
```

`PendingPayment` gains two nullable audit fields:
- `remake_tagged_at: string | null` — ISO YYYY-MM-DD when tagged
- `remake_tagged_by: string | null` — user email / display name

Both null when status ≠ `'remake'`. The remake override is applied at
the queue projection layer (`_build_pending_payment_rows`) by checking
`_load_active_remakes()` per request — the underlying status math
(`_payment_status_for`) is unchanged.

When `remake` override fires: `is_overdue = false`, `allocations = []`,
`match_source = null`, `suggested_bills = []` (the payment is "settled"
from the queue's perspective).

#### Queue filter

`?status=remake` joins the existing four-value enum. Omitting `status`
returns all five states.

#### Commission release exclusion

`compute_released_for_month` (CR #66) reads `payable_reconciliations`
to drive release math. Remake payments have no rows there by
construction (tag_remake DELETEs them). Belt-and-suspenders: the
release helper now also reads `_load_active_remakes()` and explicitly
skips payments in that set inside `_factor_for_item` — guards against
race conditions between tag-remake and a concurrent reconcile insert.

#### Tests
- 20 new in `test_payment_remakes.py`:
  - `TestLoadActiveRemakes` (2): query + filter, connection failure
  - `TestTagRemakeValidation` (7): non-digit sid, empty tagged_by,
    unknown payment, matched_pending, released, ref_sale_sid, already
    tagged (idempotent no-op)
  - `TestTagRemakeHappyPath` (2): DELETE + INSERT path for
    matched_partially; INSERT-only path for unmatched
  - `TestUntagRemake` (4): validation + not-tagged 400 + active row UPDATE
  - `TestQueueRemakeOverride` (3): allocations dropped + audit fields,
    null fields on non-remake, status-filter works for `'remake'`
  - `TestReleaseSkipsRemake` (2): baseline + defensive skip
- 1 existing init_database call_count bumped (was 8, now 10).
- 149 → 169 AP tests; 736 across the full suite.

#### Open questions — backend responses
1. **Audit history**: separate history table per recommendation.
   `payable_payment_remakes` rows are append-only; untag UPDATEs the
   row in place (sets `untagged_at`), preserving the original
   `tagged_at` / `tagged_by` for audit. Each re-tag inserts a new row.
2. **Permission**: `@manager_required` (admin + manager). Matches
   reconcile / void / unmatch.
3. **Auto-tag heuristic**: out of scope per spec.
4. **Untag → reconcile new bill**: no special handling — existing
   untag → reconcile flow covers it.

#### Bumped MINOR
Additive — new endpoints, new optional fields on PendingPayment, new
status value. Existing callers that don't know about remake see
`status` they recognize (untagged payments fall through to the
4-state model) and ignore the new nullable fields.

## [2.3.0] — 2026-06-04

### Added — CR #69: per-item allocation (proportional vs priority)

#### Motivation

`reconcile`'s allocation was bill-level — the backend split the paid
amount across the bill's items by revenue weight (proportional). That
works for the common case but misses two patterns:

1. **Customer pays for specific item(s) only**: bill with a 200M dress
   (employee A) and a 100M jewelry (employee B). Customer pays 200M
   intending to clear the dress. Proportional: each item releases 67%;
   employee A under-paid, employee B over-paid. Priority `["DRESS"]`:
   employee A releases on full 200M, employee B gets 0.
2. **Operator picks priority order**: multi-item bill, partial payment,
   operator wants specific items cleared first.

CR #69 adds an optional `items: string[]` per allocation. Null/absent →
proportional (existing behavior, no schema change). One+ UPCs → priority
order: fill each in turn up to `item.revenue_with_vat`, overflow rejected.

#### Schema

New Postgres table `payable_reconciliation_items` (idempotent in
`init_database`):

```sql
CREATE TABLE payable_reconciliation_items (
    reconciliation_id BIGINT NOT NULL
        REFERENCES payable_reconciliations(id) ON DELETE CASCADE,
    upc               VARCHAR(50)  NOT NULL,
    order_index       INTEGER      NOT NULL,
    amount_assigned   BIGINT       NOT NULL CHECK (amount_assigned >= 0),
    PRIMARY KEY (reconciliation_id, upc)
);
CREATE INDEX idx_payable_reco_items_upc ON payable_reconciliation_items(upc);
```

`ON DELETE CASCADE` on the FK: when reconcile's "edit" path drops the
parent rows, item rows go with them automatically. No orphan cleanup
needed.

`amount_assigned` is the result of the priority-fill stored at write
time so commission release doesn't recompute (and so the queue can
display the historical priority order verbatim).

#### Request shape — additive

```json
POST /reconcile
{
  "payment_doc_sid": "...",
  "allocations": [
    { "bill_sid": "...", "amount": N, "items": ["UPC-A", "UPC-B"] },
    { "bill_sid": "...", "amount": M, "items": null }
  ]
}
```

- `items` is `string[] | null`. **Empty list normalizes to null** (proportional).
- Order matters — array order is the priority sequence.
- Mixed mode in one payment is **allowed**: each allocation independently
  picks its mode (per-allocation independence — most callers won't mix
  but the data model supports it for the degenerate single-item case).

#### Validation — three new 400 cases

1. `items` contains a UPC that doesn't belong to `bill_sid`.
2. Same UPC listed twice in the same allocation's `items[]`.
3. `amount` exceeds the combined `revenue_with_vat` of the listed items.

Error messages include the bill `doc_no` and concrete amounts so the
frontend can surface them without guesswork (CR #69 open Q #4).

#### Idempotency check — items-aware

Comparing existing rows to requested rows now incorporates the items
list per allocation. An edit that adds/removes/reorders items writes
new state even when (bill_sid, amount) is unchanged.

#### Commission release — per-item factor

`compute_released_for_month` (CR #66) used a uniform per-bill paid
ratio: `amount_applied / original_charge`. Now computes a per-item
factor:

- For each in-month payment touching this (bill, item):
  - Proportional payment → adds `pay_amount / original_charge` (same for every item)
  - Priority payment → adds `amount_assigned / item.revenue_with_vat` (0 if item not in priority list)
- Item's total factor = sum across all in-month payments.

Backward-compatible: when no payment is in priority mode, every item
gets the same factor as before. Verified against the existing
`test_released_accumulator` suite (no changes needed there).

#### Queue round-trip

`PendingPayment.allocations[i]` now includes `items: string[] | null` —
priority UPCs in `order_index` sequence, or null when the allocation
is proportional. Frontend round-trips this to restore the dialog state
when re-editing a stored allocation.

#### Tests
- 15 new in `test_reconcile_per_item.py`:
  - `TestAssignPerItem` (6): priority-fill algorithm — single full, single
    partial, multi-item overflow, short-circuit, order-sensitivity,
    overflow ValueError.
  - `TestReconcileItemsValidation` (6): non-list rejected, empty → proportional,
    dup UPC, UPC not on bill, amount > combined revenue, non-string UPC.
  - `TestReconcileItemsHappyPath` (3): priority writes per-item rows
    + assignments; proportional doesn't; release math in toast.
- 4 existing tests updated for new fetchall pattern (two SELECTs per reconcile)
  + 1 init_database call_count bump (was 6, now 8) + 2 queue tests
  for the new `items: null` field on allocations.
- 127 → 142 AP tests; 709 across the full suite.

#### Open questions — backend responses

1. **Storage** (join table vs JSONB): chose the join table per CR
   recommendation. Audit-friendly (one row per assignment), UPC index
   enables "show all linkages that ever touched item X".
2. **Mixed mode within one payment**: supported. Per-allocation
   independence is a side effect of the data shape; no extra
   validation cost. Matches the degenerate single-item-bill case.
3. **Order persistence**: confirmed. `_load_reconciliation_items_for_payments`
   reads rows `ORDER BY order_index`; queue round-trip preserves order.
4. **Validation message format**: bill_doc_no + concrete numbers
   included in the three new 400 messages.

#### Bumped MINOR
Additive — new optional field, new schema (empty for existing data),
new validation paths only fire when `items` is provided. Existing
proportional callers behave identically.

## [2.2.0] — 2026-06-04

### Added — CR #68: manual void of remaining outstanding (gift / discount)

#### Motivation

Real-world AR cleanup: customer paid 80M of a 100M bill, company writes
off the remaining 20M as a goodwill gesture / VIP discount. Without a
void path the bill sits as `partial` forever, distorting the open-payables
view and inviting future payments to silently clear stale debt.

#### Schema

New Postgres table — history-preserving, terminal in v1 (no UPDATE / DELETE):

```sql
CREATE TABLE payable_bill_voids (
    void_id     BIGSERIAL    PRIMARY KEY,
    bill_sid    VARCHAR(40)  NOT NULL,
    amount      BIGINT       NOT NULL CHECK (amount > 0),
    reason      TEXT,
    voided_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    voided_by   VARCHAR(255) NOT NULL
);
CREATE INDEX idx_payable_bill_voids_bill_sid ON payable_bill_voids(bill_sid);
```

`voided_by` stores the user's email (resolved at write time from
`g.email`); reads don't need to join `users`. Migration runs idempotently
via `init_database()`.

#### Endpoint — new

**`POST /api/v1/account-payable/bills/:bill_sid/void`** — manager-required

Request: `{ amount: int, reason?: string|null }`
- `amount` required, > 0, `<= bill.remaining_unpaid` (post-prior-voids)
- `reason` optional

Response 200: `{ bill: BillDetail, void_id: string }`
- 400 on invalid amount / non-string reason / amount > remaining
- 404 if bill not in active AP ledger

#### Endpoint — shape updates

- **`GET /bills`**: each `BillRow` gains `total_voided: int` (always
  present; 0 when no voids on file).
- **`GET /bills/:bill_sid`**: `BillDetail` adds `total_voided` AND
  `voids: BillVoidRef[]` (newest-first; `{void_id, amount, reason,
  voided_at, voided_by}`).
- **`GET /employees`** + **`GET /employees/:code/bills`**: no shape
  change — outstanding totals automatically exclude voided portions
  via the new `remaining_unpaid` formula.

#### Derived state — the new invariant

`_apply_voids_to_bills` runs at the end of `_load_bills` and mutates
every bill in place:

```
total_voided     = SUM(payable_bill_voids.amount WHERE bill_sid = X)
remaining_unpaid = max(0, original_charge - total_paid - total_voided)
status           = fully_paid  if remaining_unpaid == 0
                 | open        if total_paid == 0 AND total_voided == 0
                 | partial     otherwise
```

Every downstream consumer (`get_bills`, `get_bill_detail`, `get_employees`,
`get_employee_bills`, `get_pending_payments.suggested_bills`,
`reconcile.cumulative_cap`) sees the void-adjusted state by construction.

#### Reconcile cumulative cap — void-aware

`reconcile`'s cap check now uses `original_charge - total_voided` as the
effective ceiling, so post-void allocations can never exceed what's left:

```
SUM(amount_applied for bill X across all payments) + new_amount
   <= original_charge - total_voided
```

This honors the open-question #4 in the spec: post-mutation cap is
`paid + voided <= original_charge`.

#### Commission integration — no code change

CR #66's `compute_released_for_month` computes release as
`item.revenue × effective_rate × (amount_applied / original_charge)`.
Voids never enter `amount_applied`, so the released portion correctly
excludes voided VND with zero new logic:

- 100M / paid 80M / voided 20M → release × `(80M / 100M)` = 80% ✓
- 100M / paid 100M / voided 0M → release × `1.0` = full release ✓
- 100M / paid 0M / voided 100M → release × `0` = 0 commission ✓

#### Bill flow examples

| Original | Paid | Voided | remaining_unpaid | status |
|---:|---:|---:|---:|---|
| 100M | 60M | 0  | 40M | partial |
| 100M | 60M | 40M | 0   | fully_paid |
| 100M | 60M | 10M | 30M | partial |
| 50M  | 0   | 20M | 30M | partial (open → partial) |
| 50M  | 0   | 50M | 0   | fully_paid (write-off) |

#### Tests
- 17 new unit tests in `test_bill_voids.py`:
  - `TestApplyVoids` (6): no-voids passthrough, partial keeps status,
    full void → fully_paid, open→partial on partial void, full write-off
    of open bill, multiple voids sum.
  - `TestVoidRemainingValidation` (7): non-digit bill_sid, non-positive
    amount, non-string reason, empty voided_by, unknown bill, amount
    > remaining, void on fully_paid bill.
  - `TestVoidRemainingHappyPath` (1): INSERT + RETURNING + post-void
    BillDetail in response.
  - `TestBillShape` (2): `total_voided` default on row, `voids` array
    on detail.
  - `TestReconcileCapWithVoids` (1): cap check subtracts voids.
- 110 → 127 AP tests pass. Updated 2 existing tests for the new shape:
  `test_success_creates_all_tables` (init_database now executes 6
  statements) and `test_empty_when_no_active_bills` (derives status
  from numbers instead of overriding it directly).
- 694 across the full suite.

#### Open questions — backend responses

1. **Reversibility**: v1 terminal as recommended. No UPDATE / DELETE
   on `payable_bill_voids` rows.
2. **Audit feed**: out of scope per spec.
3. **Permission**: `@manager_required` (admin + manager). Matches
   reconcile/unmatch.
4. **Cumulative cap on partial voids**: confirmed. Cap is
   `paid + voided <= original_charge`; see void-aware cap block in
   `reconcile()`.

#### Bumped MINOR
Additive — new endpoint, additive response fields (`total_voided`,
`voids`). Existing callers that don't know about voids continue to
see `total_voided = 0` and a `voids: []` array; the `remaining_unpaid`
field still has the same meaning (just now subtracts voids too).

## [2.1.0] — 2026-06-04

### Added — CR #67: released payments stay editable; `matched_partially` state

#### State model

Uniform rule across queue + reconcile, computed from `(amount, applied,
month, current_month)`:

```
sum == 0                           → unmatched (overdue if past)
0 < sum < payment.amount           → matched_partially  (NEW)
sum == payment.amount AND past     → released           (now in queue)
sum == payment.amount AND current  → matched_pending
```

`is_overdue` continues to fire ONLY on `unmatched AND past`. Partial
payments are never overdue regardless of their month.

#### `GET /account-payable/payments`

- `?status=` now accepts `unmatched | matched_partially | matched_pending
  | released` (was `unmatched | matched_pending`). Omitting returns all
  four.
- **Released past-month payments stay in queue** (was: dropped). Lets
  finance edit allocations after release — see CR §"Released-state
  mutation semantics".
- Response shape unchanged. `match_source` continues to report the
  highest-priority source across the allocations.

#### `POST /account-payable/reconcile`

- Allocation sum may now be `0`, `< payment.amount`, or `==
  payment.amount`. Only `sum > payment.amount` (overflow) is rejected.
- `outcome` enum widens to all four states. Computed via the same
  uniform rule the queue uses.
- `total_release_amount` and `affected_employee_count` are now computed
  for **any past-month outcome with non-zero allocations** — covers both
  `released` (full payment) and past-month `matched_partially` (the
  partial portion). Current-month outcomes return 0 (release fires at
  month-close).
- `payment` field is now always populated when the payment is in the
  active 24-month Charge window — full PendingPayment shape. Returns
  `null` only when the payment falls outside the window (rare).
- Empty `allocations` with existing rows fires DELETE — the "clear
  linkage" path. Frontend's confirm-before-clear dialog covers UX.

#### Released-state mutation — falls out for free

CR #66's `compute_released_for_month` already re-reads `payable_bill_rates`
and `payable_item_custom_rates` on every call. When a released linkage
is amended (downward or empty), the next `POST /commission/calculate`
for the affected month picks up the new allocation set and adjusts
`released_by_category` accordingly. No cache invalidation needed.

Bills similarly reopen on the next `_load_bills` call — the replay
re-runs from scratch including the manual reconciliation pass.

#### `POST /account-payable/payments/:sid/unmatch`

Already works for released-with-manual payments — the existing DELETE
doesn't gate on status. The CR #67 spec language ("was 400 in v1.0.0")
referenced the fact that released payments didn't appear in the queue
pre-CR #67 so users couldn't reach the unmatch button; now they can.

#### Tests
- 110 AP unit tests pass (was 100). 8 new tests:
  - `test_status_filter_released`, `test_status_filter_matched_partially`
    (queue, CR #67 filter values).
  - `test_partial_application_in_current_month_is_matched_partially`,
    `test_partial_application_in_past_month_is_matched_partially`
    (queue, new state classifications).
  - `test_empty_allocations_succeeds_as_unmatched`,
    `test_empty_allocations_with_existing_runs_delete`,
    `test_partial_allocation_in_past_month_is_matched_partially_with_release`,
    `test_partial_allocation_in_current_month_no_release`
    (reconcile, the new partial / empty paths).
  - `test_embedded_payment_returned_when_in_active_window`,
    `test_embedded_payment_is_none_when_not_in_active_window`
    (reconcile, PendingPayment embedding contract).
- 677 across the whole codebase.

#### Bumped MINOR
Additive — new state values, new filter values, additional fields
populated. Existing callers handling only the 2-state model continue to
work. `payment` field's type was already `PendingPayment | null` so
callers tolerating null are safe; callers can now also see real values.

## [2.0.0] — 2026-06-04

### BREAKING — FIFO auto-matching removed from the ledger replay

#### Motivation

A live audit of payment-note text vs FIFO allocations found multiple
cases where the auto-FIFO pass was misallocating payments to the wrong
bills. Examples spanning 2024-2025:

| Customer | Note (translated intent) | FIFO matched | Verdict |
|---|---|---|---|
| To Thi Hanh | "khách thanh toán nợ **bill 5180**" | doc_no **1778** | WRONG |
| Nguyen Thi Linh San | "Bill **3593**, khách tt công nợ" | doc_no 324, 538, 656, 753, 1168 | WRONG, fragmented across 5 oldest bills |
| Nguyen Thi Linh San | "Bill **5172**, khách tt acc" | doc_no 1168 | WRONG |

FIFO over-applies when employees write notes (or forget to). The
silent default — "match to oldest open" — turns out wrong far more
often than the original CR #59 Phase A assumed. Manual reconciliation
becomes the only safe path; FIFO offered the appearance of
auto-matching while quietly mis-attributing commission release to the
wrong employees.

#### Behaviour change

`_replay_charge_ledger_with_chronology` now runs:
1. **Pass 1 — REF_SALE_SID** (Oracle's targeted auto-link). Unchanged.
2. **Pass 2 — manual `payable_reconciliations`** rows. Unchanged.
3. *(removed)* ~~Pass 3 — FIFO over remaining open bills.~~

Payments without either REF_SALE_SID or a manual reconciliation row
**stay unmatched**. The queue's `unmatched` / `is_overdue` bucket is
now the default destination for any unreferenced payment — finance
must manually allocate via `POST /account-payable/payments/:sid/reconcile`.

#### Response shape

- Bill `payments[*].source` enum narrows from
  `'ref_sale_sid' | 'manual' | 'fifo'` to `'ref_sale_sid' | 'manual'`.
  Frontend code that branches on `source === 'fifo'` will no longer hit
  that branch (defensive: keeping the union type with `'fifo'` is fine
  for backward compat with any stored snapshots).
- `match_source` on queue rows similarly narrows.
- All other field shapes unchanged.

#### Operational impact (live snapshot at branch-cut time)

Re-replaying the past 24 months of charge-tender activity with FIFO
disabled:

| Today (FIFO on) | After v2.0.0 (FIFO off) |
|---|---|
| 116 bills `fully_paid`, 3 partial, 8 open | 2 fully_paid, 3 partial, **122 open** |
| 89 payments allocated | 8 allocated (REF only), **81 unmatched** |
| — | **~15 billion VND** of allocations need manual reconciliation |

Finance team must process the unmatched-queue backlog before commission
release for future months resumes its CR #66 behaviour.

#### Commission release (CR #66) downstream

`compute_released_for_month` will return substantially less per month
until backfill is complete — only bills with explicit REF_SALE_SID or
manual reconciliation contribute to released. This is the desired
correctness behaviour going forward: the previous numbers were
inflated by incorrect FIFO inferences.

#### Tests
- Rewrote 5 repository tests + 1 smoke test to assert the no-FIFO
  behaviour:
  - `test_unreferenced_payments_stay_unmatched` (was `test_fifo_pays_oldest_first`)
  - `test_ref_payment_applies_unreferenced_stays_unmatched`
  - `test_orphan_ref_sale_sid_stays_unmatched`
  - `test_manual_pass_skips_unknown_bill` — updated expectations
  - `test_chronology_sorted_by_date_within_bill` — uses ref + manual instead of ref + fifo
  - `TestGetAllBillsSmoke::test_classifies_status_correctly` — payment without REF stays unmatched
- 100 AP unit tests pass. 667 across the whole codebase.

#### Bumped MAJOR
Match semantics fundamentally change. Existing data analysis that
relies on past FIFO allocations is no longer reproducible from the
replay alone; existing reconciled state can only be reproduced by
inserting equivalent `payable_reconciliations` rows.

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
