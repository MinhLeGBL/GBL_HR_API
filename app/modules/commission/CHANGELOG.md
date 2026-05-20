# Changelog — commission

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

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
