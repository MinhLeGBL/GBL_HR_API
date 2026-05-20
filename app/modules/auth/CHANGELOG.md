# Changelog — auth

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Baseline — formal `__version__` tracking begins. Reflects the cumulative
result of CRs #33, #34, plus the initial implementation (full specs in
`GBL_HR_Frontend/docs/cr/users.md`, where these are filed as CR #1 / #2).

### Endpoints
- `POST /api/v1/auth/login` — Public. Returns access + refresh tokens,
  user, and permissions array
- `POST /api/v1/auth/refresh` — Public. Refresh access token
- `GET /api/v1/auth/me` — Token. Current user
- `POST /api/v1/auth/change-password` — Token. Clears `must_change_password`
- `GET /api/v1/auth/roles` — Token. Role lookup
- `GET / POST / PUT / DELETE /api/v1/users` — user CRUD
- `GET /api/v1/users/:sid` — single user
- `POST /api/v1/users/:sid/reset-password` — Admin/Manager. Resets to
  default + forces password change on next login

### Pre-versioning history (chronological, newest first)

- **2026-03-18** — CR #34 (filed as #2 in users.md):
  `POST /users/:sid/reset-password` — admin/manager resets to default
  (`123456`) and sets `must_change_password = TRUE`.
- **2026-03-18** — CR #33 (filed as #1 in users.md): Force password change
  on first login. Added `must_change_password` BOOLEAN to `users`
  (default `TRUE`; existing users set to `FALSE`). Field returned in all
  user responses; cleared on successful `/auth/change-password`.
