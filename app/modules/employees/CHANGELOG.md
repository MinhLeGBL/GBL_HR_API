# Changelog — employees

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Baseline version — formal version tracking begins. Covers existing
functionality:

- Employee CRUD with department/role filters (`/api/v1/employees`)
- Period-accurate `is_manager` and `contract` from
  `employee_status_history` (CR #16 unified snapshot table)
- Manager-status audit endpoint
  (`POST /api/v1/employees/:sid/manager-status`)
- Auto-tracking of store transfers on PUT
- RetailPro Oracle sync (`POST /api/v1/employees/:sid/sync-retailpro`)
- Employee types and contract types lookup endpoints

Historical CRs for this module are tracked in
`GBL_HR_Frontend/docs/cr/employees.md` (CR #2, #5, #16).
