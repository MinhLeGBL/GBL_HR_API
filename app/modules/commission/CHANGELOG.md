# Changelog — commission

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [3.1.0] — 2026-06-23

### Changed — probation employees: full store commission, no personal commission

Corrected the probation rule. Previously a probation employee received **only the 30%
equal share** of store commission and still earned personal commission. The correct rule:

- **Store commission applies in full** to probation employees — individual (70%) + equal
  (30%) share, exactly like everyone else (`calculate_store_commission_v2` no longer caps
  probation to the equal share).
- **Personal commission does NOT apply** to probation employees — fashion, jewelry,
  suitcase, hand-carry, home-decor, and over-target are all excluded
  (`calculate_personal_commissions` returns an empty result for `is_probation`).

So a probation employee who sells at an eligible store receives their full store commission
but no personal commission. Verified live against May 2026: GL304/GL305 store commission now
matches the manual (was equal-share-only); their personal stays 0. (GL298 is a stale-flag
case — the manual treats it as non-probation; its contract should be updated if it has
graduated from probation.)

## [3.0.0] — 2026-06-22

### Changed — consolidate calculation to one DB-authoritative engine; fix store-pool dilution

**Bug.** `POST /commission/calculate` (the CR #29 frontend-payload path,
`calculate_commissions_v2`) built each store's roster from the request body. The
frontend assembles that body from the store-view revenue screen, which groups
contributors by **sale location** (`doc_store_code`). A cross-store seller (home
store ≠ sale store) therefore rode into the destination store's employee list,
inflating `total_employee_count`. Since the pool already excludes cross-store sales
(`store_code == X AND doc_store_code == X`), such a seller added **+1 to the 30%
equal-share divisor but 0 to the pool** — diluting every roster member's share.
Verified on RWT May 2026: shared share `402,832` (÷13) vs correct `436,402` (÷12).

**Fix / consolidation.** `/commission/calculate` is now fully DB-authoritative and is
the single calculate engine (`CommissionService.calculate_commissions`, formerly the
unused `calculate_commissions_for_period`):

- Each store's roster = commission-**active** employees whose resolved assigned store
  (override → status-history → default) is that store. `is_commission_active` is now
  surfaced by `_get_employees_with_username_for_period` and filtered in the per-store
  grouping. This sets pool membership, `total_employee_count`, and `total_working_days`.
- Cross-store sellers are rostered under their **home** store only. Their out-of-store
  sales reach the destination store's **total-revenue-for-eligibility** and their own
  **personal** commission, but never any store's pool.
- Revenue adjustments are loaded from the DB (`commission_revenue_adjustments`), not the
  request. Per-store adjustments now feed (a) store-revenue-for-eligibility — all
  adjustments booked at the location, and (b) the pool — only a roster employee's
  **in-store fashion** adjustments.

**API.** `POST /commission/calculate` now takes `{month, year}`. A legacy `stores`
array is accepted but **ignored** (adjustments/targets are read from the DB; save them
first via `PUT /commission/revenue/adjustments/<code>` and `PUT /commission/stores/<code>`).

### Removed

- `CommissionService.calculate_commissions_v2` (replaced by `calculate_commissions`).
- Routes `POST /commission/store/calculate-v2` and `POST /commission/personal/calculate`
  — no frontend or internal callers. The service methods `calculate_store_commission_v2`
  and `calculate_personal_commissions` remain as internal sub-engines.

## [2.2.0] — 2026-06-02

### Added — CR #66: populate `released_by_category` from Account Payable linkages

CR #59 Phase B declared `released`, `released_by_category`, and `payout`
on the calculate response but hardcoded them to `0` / `{}` / `employee_total
- withheld` because release tracking was deferred to Phase C. CR #60
(account_payable v1.0.0) + CR #62 (v1.1.0) shipped the linkage tables
this CR closes the loop on.

#### How it works

When `POST /commission/calculate` runs for month M, the response builder
now calls `AccountPayableService.compute_released_for_month(year, month)`
once per request and threads its return value into the per-employee
result builder.

`compute_released_for_month` reuses the same ledger-replay output the
bill view and queue endpoints consume (`AccountPayableService._load_bills`,
CR #62 unifier — no duplicate replay). For every bill with at least one
payment in month M:

1. `allocation_factor = sum(amount_applied in M) / original_charge`.
2. For each line item on the bill, look up
   `effective_rate = custom_release_rate ?? auto_release_rate ?? 0`
   where the auto rate comes from the CR #61 `payable_bill_rates`
   snapshot and the custom rate from the CR #60 `payable_item_custom_rates`
   override.
3. `revenue_basis = revenue_with_vat / 1.1` for every category except
   `hand_carry` (with-VAT, per the existing commission rate convention)
   and `suitcase` (flat 500K × qty — deliberately absent from the rate
   snapshot per CR #61).
4. Bucket: `released[employee_code][f'{revenue_type}_{fp_or_md}'] +=
   revenue_basis × effective_rate × allocation_factor`.

The result key shape mirrors `withheld_by_category` so the response's
existing serialization round-trips through unchanged. One semantic
difference vs withheld: over-target bonus is *not* split into its own
`over_target` bucket here — `payable_bill_rates` stores the OT-blended
rate as a fashion+fp row, so OT released contribution accumulates under
`fashion_fp`. This matches the CR §66 spec and what the frontend
already renders.

#### Sanity warnings, not errors

Bills paid in M whose source months never ran `POST /commission/calculate`
have no rate snapshot — these items contribute 0 to released and log a
single-line WARNING (one count per request). Frontend continues to
render zeros for those employees, same as Phase B.

#### Response shape — unchanged

The `released`, `released_by_category`, and `payout` keys have been
declared since Phase B; they just stop returning 0. No frontend update
required (Step 3 already renders these columns).

#### Tests
- 11 new unit tests (`test_released_accumulator.py`) covering: empty
  bills, payment-in-month filter, fashion FP with auto rate, custom rate
  override, partial-payment proration, hand-carry with-VAT basis,
  suitcase flat-per-qty, multi-category aggregation, missing-rate
  fallback, cross-month payment isolation, missing-employee skip.
- All 637 unit tests pass.

#### Live verify (2026-04)
- 2 employees have non-zero release on April commission.
- GL005: `fashion_fp = 80,116`. The other 17 items in scope lack a
  source-month rate snapshot — flagged via the WARN line so finance
  knows to run calculate on the bills' creation months to backfill.

#### Bumped MINOR — backward-compatible behaviour change
The response shape is identical to v2.1.0; previously-zero fields now
return real numbers when the data permits. Per Keep-a-Changelog: a
documented behaviour change that adds value without breaking callers is
MINOR.

## [2.1.0] — 2026-06-01

### Added — CR #61: per-item release rate snapshot + HEA-as-fashion cutoff

- New Postgres table `payable_bill_rates` keyed on `(bill_sid, upc)` with
  `revenue_type`, `fp_or_md`, `effective_rate`, `source_month`, `updated_at`.
  Created in `CommissionRevenueService.init_database()` alongside the
  existing adjustment table.
- `POST /commission/calculate` and `POST /personal/calculate` now batch
  UPSERT one row per line item that an employee sold in the target month.
  The rate is the exact rate the existing pipeline would apply if the item
  were paid in full — tier rate by category, plus the over-target bonus for
  FP fashion items above the 100% threshold (a single bill-crossing item
  gets a proportional blended rate). Items with no commission concept (e.g.
  suitcase, which is a flat per-item amount) are skipped.
- Account Payable (CR #60) reads from `payable_bill_rates` directly to
  compute release commission — no new commission endpoint needed.
- Snapshot is best-effort: a failure logs a warning but does NOT abort
  the calculate response.

### Changed — HEA-as-fashion reclassification (effective April 2026)

- New module constant `HEA_AS_FASHION_FROM_MONTH = '2026-04'` + helper
  `hea_is_fashion(year, month)`.
- For target months **>= 2026-04**, COSM+HEA products are now classified
  as fashion (same tier rate + over-target bonus). Pre-cutoff months
  preserve the legacy SYSADMIN routing so historical commission output
  remains stable.
- Touchpoints made conditional on the cutoff:
  - `CommissionRepository.get_all_sales_data` — skips the SYSADMIN
    rewrite post-cutoff.
  - `_compute_revenue_by_type` — accepts `year` / `month` kwargs; routes
    HEA to fashion post-cutoff. All 7 internal callers updated.
  - `calculate_personal_commissions` — main path filter, exception path
    filter, and both over-target qualification filters all gated.

### Behavior change warning — replaying older months is NOT idempotent across versions

The HEA reclassification is **target-month keyed**: re-running `calculate` for
**April 2026 or later** with this version produces commission numbers that
include HEA in fashion, whereas the same call on **v2.0.0** excluded HEA via
SYSADMIN. Pre-cutoff months (March 2026 and earlier) reproduce v2.0.0 numbers
exactly.

If you regenerate snapshots for a previously-paid period >= 2026-04 with this
version, employees who sold HEA items will see different commission than the
v2.0.0 payout they received. Treat v2.1.0 calculate output for periods >=
2026-04 as authoritative going forward, but don't use it to retroactively
re-pay older periods.

### Bumped MINOR (additive response shape, behavior change for HEA periods)

`POST /commission/calculate` response **shape** is unchanged. The new
`payable_bill_rates` rows are a side effect, not a response field. The MINOR
bump is justified by shape compatibility; the HEA cutoff is a behavior change
for periods >= 2026-04 (see warning above).

### Verified

- 210 unit tests pass (14 new — 5 original CR #61 + 9 follow-up covering
  hand-carry / suitcase / home / jewelry / sub-50% / blended OT classifier
  branches + end-to-end snapshot integration; 196 prior).
- Live April 2026 calculate populated 116 snapshot rows for
  GL005/GH100/GH083 with the expected tier rate distribution
  (tier-1 0.25% FP, tier-1 0.125% MD, 1% hand carry, 2% jewelry-other).
- GL005/GH100/GH083 commission payable + withheld numbers unchanged
  from v2.0.0 reference data (none of their items are COSM+HEA).
- Snapshot's over-target maps are populated INLINE by the main pipeline
  (no logic duplication) — the snapshot rate for an over-target item is
  guaranteed to match the rate that paid that item.

## [2.0.0] — 2026-05-29

MAJOR bump: introduces the `withheld` / `payout` concept on every commission
result. Previously the only "amount the employee receives" was `employee_total`;
that is now the **gross** value, and the new `payout` field is the actual
take-home after AR-payable withholding. Existing clients that read only
`employee_total` keep working but lose the new accounting nuance — hence the
MAJOR bump even though the response is technically additive.

### Added — CR #59 Phase B: AR payable detection + commission withholding

- `CommissionRepository.get_unpaid_bill_amounts(year, month)` — replays the
  per-customer Charge tender ledger (two-tier matching: `REF_SALE_SID` →
  FIFO) and returns bills created in the target month that remain unpaid
  at end-of-month. Each entry carries `original_charge`, `remaining_unpaid`,
  `sale_total_amt`, and an `unpaid_ratio` for proportional withholding.
- `ORACLE_UNPAID_BILLS_BY_MONTH` SQL query in `CommissionQueries`. Pulls
  the full Charge ledger up to end-of-month for any customer with at least
  one in-month Charge bill.
- `bill_sid` column added to `ALL_SALES_DATA` so the service can look up
  each line item's bill in the unpaid map. SID is stringified at the
  repository layer to dodge float64 precision loss (same approach as
  v1.0.2).
- `CommissionService._calculate_withheld_by_category` — computes per-
  category withholding for one employee. Withholding uses the same rate
  the category's normal commission line uses, applied to the
  `unpaid_ratio`-weighted revenue.
- New per-employee response fields on `POST /commission/calculate`:
  `withheld`, `withheld_by_category`, `released`, `released_by_category`,
  `payout`. `released = 0` and `released_by_category = {}` are placeholders
  shipped now per the CR spec; Phase C will populate them.
- New per-category field `payable_amount` on each `RevenueEntry` returned
  by `GET /commission/revenue` and `GET /commission/revenue/store-view`,
  plus top-level per-employee `payable_amount`.

### Behavior

- Bills with `REF_SALE_SID` set are matched to that specific bill first.
  Bills without are FIFO-matched against the customer's oldest open bills.
  `NOTES_LOSTDOC` parsing is intentionally not used (free-text Vietnamese
  with inconsistent formatting; see CR #59 Phase A research).
- Period scoping: only bills created in the target month appear in that
  month's payable display. Carry-over bills from prior months are not
  re-withheld (already withheld in their creation month).
- `unpaid_ratio = remaining_unpaid / sale_total_amt` — applied uniformly
  across line items on the same bill. For bills with mixed tenders (only
  partial AR on Charge), this naturally scales withholding down.

### Verified against user-supplied reference data

- GL005 payable: 56,982,600 ✓
- GH100 payable: 55,836,000 ✓
- GH083 payable: 27,019,800 ✓
- Withheld commission for GL005/GH083 matches `payable_revenue / 1.1 ×
  tier_rate` exactly. GH100 withheld = 0 because their achievement rate
  is below 50% (no fashion commission to withhold).

## [1.0.2] — 2026-05-25

### Fixed
- Preserve Oracle SID precision in `get_all_sales_data` by stringifying
  SIDs at the row-dict level **before** they enter pandas. Oracle SIDs
  are 18-digit integers; the moment a single LEFT JOIN walk-in produces
  a NULL in `sale_id` / `employee_sid` / `customer_sid`, pandas promotes
  the whole column to `float64` and every SID in it gets silently rounded
  (off by up to ~100). The corrupted SID then stops matching exact
  hardcoded values — notably the `EMPLOYEE_COMMISSION_EXCEPTIONS` dict,
  which is the only known production-visible failure: GL018 fell through
  to the standard achievement-tier path and earned 0 fashion FP
  commission instead of their flat-rate 0.7% on qualifying-customer
  sales (~2.1M VND/month).
- `EMPLOYEE_COMMISSION_EXCEPTIONS` keys and `qualifying_customer_sid`
  are now stored as strings to match the stringified SID column.
- Stringifying *before* the DataFrame constructor (rather than casting
  after) is what actually fixes the bug — casting after pandas has
  already promoted to float64 cannot recover the lost digits.

## [1.0.1] — 2026-05-20

### Verified (no code change)
- **CR #56** — Exclude hand carry from store commission pool.
  Investigation confirmed the exclusion is already in place at
  `service.py:1097` (filters `hc_upc_set` out of `emp_df` before
  per-employee `fp_revenue`/`disc_revenue` are computed, which then feed
  `store_pool`). Numerical verification against RHN/March-2026 data:
  every one of the 11 employees' `fp_revenue + disc_revenue` matched the
  no-hand-carry total exactly. `actual_revenue` (used for eligibility /
  achievement / FP-ratio / tier) continues to include hand carry as
  required. Per-employee `hand_carry` personal commission and manager
  bonus paths are unaffected (separate code paths). No source change
  needed — bumped to PATCH to record the verification.

## [1.0.0] — 2026-05-20

Baseline — formal `__version__` tracking begins. Reflects the cumulative
result of CRs #4 through #35 (full specs in
`GBL_HR_Frontend/docs/cr/commission.md`).

### Endpoints
- `GET/PUT /api/v1/commission/employees` — per-employee commission settings
  with period-accurate `is_manager` / `contract` / store grouping
- `GET/PUT /api/v1/commission/stores` — per-store settings
- `GET /api/v1/commission/revenue` — live Oracle revenue with 8-category
  FP/MD split, location-based store totals, before-VAT base amounts
- `GET /api/v1/commission/revenue/store-view` — revenue grouped by
  transaction location, cross-store contributors, SYSADMIN bucket
- `PUT /api/v1/commission/revenue/adjustments/:employee_code` — per-store
  revenue adjustments (16 type+tier codes)
- `POST /api/v1/commission/calculate` — full pipeline with
  frontend-supplied revenue context, tier-based store pool, 9 revenue
  types per employee

### Pre-versioning history (chronological, newest first)

- **2026-03-19** — CR #35: Store eligibility simplified to 2 conditions
  (achievement ≥ 70% + FP ratio compensation). Removed incorrect
  "FP revenue floor" rule.
- **2026-03-13** — CR #31: 3 over-target bugs fixed — proportional split
  on target-crossing item, all items in running total, FP-first ascending
  sort.
- **2026-03-12** — CR #30: Bug fix `ON CONFLICT` missing `store_code`
  (already resolved as part of CR #28).
- **2026-03-12** — CR #29: `POST /calculate` accepts frontend-supplied
  revenue context (per-category base + adjustments, achievement,
  eligibility flags).
- **2026-03-10** — CR #28: Per-store revenue adjustments — `store_code`
  added to `commission_revenue_adjustments`, unique constraint extended.
- **2026-03-10** — CR #27: 9 flat revenue types → 8 categories with FP/MD
  split (≤30% = FP, >30% = MD). `fashion` replaces `full_price` +
  `markdown`. Per-type-per-tier adjustments (16 codes).
- **2026-03-10** — CR #26: `GET /revenue/store-view` endpoint —
  location-based grouping, cross-store flags, SYSADMIN bucket.
- **2026-03-10** — CR #25: COSM+HEA items re-attributed to SYSADMIN; HOME
  items classified as `home_decor` (1% rate). 9 revenue types.
- **2026-03-10** — CR #24: Resolved by CR #25 (COSM/HOME misclassification
  caused store-level totals mismatch).
- **2026-03-09** — CR #23: `store_total_vat` filtered to 7 classified
  revenue types only (excludes unclassified items).
- **2026-03-09** — CR #22: Investigation — store totals query is correct;
  discrepancies explained by non-classified items and manual sample edits.
- **2026-03-06** — CR #21: Location-based store totals (cross-store sales,
  SYSADMIN, returns). With-VAT `base_amount` for all types. Removed
  `personal_eligible`, `store_eligible`, `store_achievement`,
  `store_fp_ratio` from response.
- **2026-03-06** — CR #20: Flat 10% VAT (`di.price / 1.1`) for
  `revenue_before_vat` — **temporary**, revertible by searching
  `CR #20` comments in `queries.py`.
- **2026-03-06** — CR #19: Investigation — `store_code_override` bleed is
  a frontend bug, not backend.
- **2026-03-06** — CR #18: Fix `is_commission_active` default to
  `COALESCE(cs.is_commission_active, esh.is_active, TRUE)`.
- **2026-03-06** — CR #17: Extended `PUT /employees/:employee_code` to
  accept `is_manager` + `contract` for `employee_status_history` upsert.
- **2026-03-04** — CR #15: `store_code_override` per-period reassignment
  in `commission_settings`. Inactive employees with settings included in
  roster.
- **2026-03-03** — CR #14: `is_commission_active` added to
  `commission_settings` table and endpoints.
- **2026-02-24** — CR #13: `store_fp_total_vat` added to revenue response.
- **2026-02-24** — CR #12: Fix `personal_total_vat` / `store_total_vat`
  with-VAT amounts.
- **2026-02-24** — CR #11: Revenue endpoint returns `personal_total_vat`
  and `store_total_vat`.
- **2026-02-23** — CR #10: `is_manager` changed to runtime-only toggle.
- **2026-02-23** — CR #9: Added `fp_below_target` field to calculate
  response (later renamed `fashion_fp` in CR #27).
- **2026-02-22** — CR #8: Calculate response with per-employee breakdown.
- **2026-02-21** — CR #7: Revenue adjustments, calculation endpoint,
  initial 7 revenue types.
- **2026-02-20** — CR #6: Commission store settings endpoints (GET + PUT).
- **2026-02-20** — CR #5: Period-accurate `is_manager` and store grouping
  via history tables.
- **2026-02-20** — CR #4: Commission employee list endpoints (GET + PUT).
