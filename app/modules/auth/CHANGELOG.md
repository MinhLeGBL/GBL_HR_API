# Changelog — auth

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Baseline version — formal version tracking begins. Covers existing
functionality:

- JWT login with access + refresh tokens (`POST /api/v1/auth/login`)
- Token refresh (`POST /api/v1/auth/refresh`)
- Current user info (`GET /api/v1/auth/me`)
- Password change (`POST /api/v1/auth/change-password`)
- Roles lookup (`GET /api/v1/auth/roles`)
- Full user CRUD on `/api/v1/users` including `must_change_password` flag
  and admin-driven password reset

Historical CRs for this module are tracked in
`GBL_HR_Frontend/docs/cr/users.md` (force-password-change, password reset).
