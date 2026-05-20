# Changelog — commission

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Baseline version — formal version tracking begins. Covers existing
functionality:

- Per-employee commission settings (`GET/PUT /api/v1/commission/employees`)
  with period-accurate `employee_status_history` lookups
- Per-store commission settings (`GET/PUT /api/v1/commission/stores`)
- Revenue endpoint (`GET /api/v1/commission/revenue`) with 8-category
  FP/MD split, location-based store totals, before-VAT base amounts
- Store View revenue (`GET /api/v1/commission/revenue/store-view`)
- Per-store revenue adjustments
  (`PUT /api/v1/commission/revenue/adjustments/:employee_code`)
- Full commission calculation (`POST /api/v1/commission/calculate`)
  with frontend-supplied revenue context, tier-based store pool,
  per-revenue-type personal commission

Historical CRs for this module are tracked in
`GBL_HR_Frontend/docs/cr/commission.md` (CRs #4 through #35).
