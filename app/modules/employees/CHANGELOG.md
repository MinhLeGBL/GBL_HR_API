# Changelog — employees

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Baseline — formal `__version__` tracking begins. Reflects the cumulative
result of CRs #2, #5, #16 (full specs in
`GBL_HR_Frontend/docs/cr/employees.md`).

### Endpoints
- `GET / POST / PUT / DELETE /api/v1/employees` — CRUD with department /
  role filters; `is_manager` returned from `employee_status_history`
- `GET /api/v1/employees/:sid` — single employee with period snapshot
- `POST /api/v1/employees/:sid/sync-retailpro` — sync Oracle RetailPro
  username/SID
- `POST /api/v1/employees/:sid/manager-status` — record is_manager change
- `GET /api/v1/employees/types`, `/contracts` — lookup endpoints

### Pre-versioning history (chronological, newest first)

- **2026-03-04** — CR #16: Unified `employee_status_history` snapshot
  table replaces `employee_manager_history` + `employee_store_history`.
  Period-based (month / year) UPSERT, tracks 5 fields. Commission query
  simplified from 3 LATERAL JOINs to 1. Old tables kept with dual-write.
- **2026-02-20** — CR #5: Time-sensitive history tables —
  `employee_manager_history` + `employee_store_history` with
  `effective_from` dates. `POST /employees/:sid/manager-status` endpoint;
  auto-tracking store transfers on PUT.
- **2026-02-13** — CR #2: RetailPro sync endpoint —
  `POST /employees/:sid/sync-retailpro` queries Oracle, updates
  `retailpro_username` and `retailpro_sid`.
