# Changelog — employees

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-06-24

### Added — master `is_manager` + contract/is_manager change-timestamps (CR #75)

Supports the commission point-in-time resolution of `contract` / `is_manager` (master vs
period roster snapshot by recency). Three columns added to `employees` (idempotent
`ADD COLUMN IF NOT EXISTS` in `HREmployeeService.init_database`):

- `is_manager BOOLEAN NOT NULL DEFAULT FALSE` — master mirror of manager status (previously
  only in `employee_status_history` / `employee_manager_history`).
- `contract_changed_at TIMESTAMPTZ`, `is_manager_changed_at TIMESTAMPTZ` — bump **only when
  that field actually changes**, so unrelated master edits (name/email/store) don't make the
  master win in the resolver.

**Backfill** (idempotent — the nullable `*_changed_at` columns are the "already backfilled"
markers): `is_manager` ← latest history value (else FALSE); each `*_changed_at` ←
`GREATEST(created_at, latest history period whose value matches the master)`, so the first
post-deploy master edit (`NOW()`) correctly overrides.

**Writers (conditional bump):**
- `update_employee()` — adds `contract_changed_at = NOW()` to the UPDATE only when
  `contract_type_id` actually changes.
- `set_manager_status()` — keeps the existing history writes and now also mirrors the value
  to `employees.is_manager`, bumping `is_manager_changed_at` only on an actual change.

Read-side resolution lives in the commission module (no change here beyond the schema +
writers). First tracked version for this module.
