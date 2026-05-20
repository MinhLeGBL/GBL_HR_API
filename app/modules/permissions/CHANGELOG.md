# Changelog — permissions

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Baseline version — formal version tracking begins. Covers existing
functionality:

- Departments lookup (`GET /api/v1/departments`)
- Per-user permissions (`GET /api/v1/permissions/me`)
- Section-permission management (`GET /api/v1/sections/permissions`,
  `PUT /api/v1/sections/:code/access`) using the flat v2 model
  (`section_departments`, `section_roles`, `section_inclusions`,
  `section_exclusions_v2`)

Historical CRs for this module are tracked in
`GBL_HR_Frontend/docs/cr/permissions.md` (CR #1, #2, #44).
