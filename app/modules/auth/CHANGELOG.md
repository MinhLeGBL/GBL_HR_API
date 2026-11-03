# Changelog — auth

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.1.0] — 2026-11-03

### Security — the admin role is no longer assignable through the API

**There was a privilege-escalation path.** `PUT /users/<sid>` is gated
`@admin_or_it_manager_required` and accepted any `role` value, so **a manager in
the IT department could promote themselves — or anyone — to admin.** Admin
bypasses every permission check (`PermissionService.get_user_permissions_v2`
resolution rule 1), so that was a route from "manager in IT" to "access to
everything". `POST /users` was narrower ("only an admin may create an admin")
but had no uniqueness check, so an admin could still mint a second one.

Both are closed. `_reject_admin_role` refuses `role: admin` on create and
update, for **every** caller including the admin — a second admin is not a thing
this system has, so there is nobody for whom granting it is correct. Matching is
case-insensitive and trimmed (`Admin`, `ADMIN`, `" admin "` are all refused).

### Added — the admin row is protected from being stripped of its powers

With the role ungrantable, anything that leaves zero admins is unrecoverable
without database access. `_protect_admin_row` therefore refuses, through this
API:

| Operation on the admin row | Result |
|---|---|
| change `role` | 403 |
| set `is_active = false` | 403 |
| `DELETE` | 403 |
| change `department_id` | allowed |
| set `is_active = true` | allowed |

`DELETE` previously relied on "users cannot delete themselves", which stops the
admin deleting *itself* and never covered anyone else doing it.

**Transferring admin is a deliberate database operation, by decision** — there
is no endpoint for it:

```sql
BEGIN;
UPDATE users SET role_id = (SELECT id FROM roles WHERE code = 'MANAGER')
 WHERE sid = <current admin sid>;
UPDATE users SET role_id = (SELECT id FROM roles WHERE code = 'ADMIN')
 WHERE sid = <new admin sid>;
COMMIT;
```

### Changed — creating a user now needs the same authority as changing one

`POST /users` moves from `@token_required` plus an in-handler "admin or manager"
check to **`@admin_or_it_manager_required`**, matching `PUT /users/<sid>`.
Creating a user assigns a role, so it is role assignment and belongs behind the
same gate. **This is a reduction in who can act:** a manager in ACC, HR, BOD or
any non-IT department could create users before and now cannot.

### Note on how `_target_is_admin` reads the target's role

It runs its own query rather than calling `auth_service.get_user_by_sid`. This
module shares that service instance with the auth middleware, so a lookup of the
TARGET and the middleware's lookup of the CALLER are the same method on the same
object — one test stub answers both, every target looks like whoever is
authenticated, and the guard fires on innocent updates. Three pre-existing tests
failed exactly this way before the lookup was separated. It returns `False` when
the role cannot be read, so a database blip cannot start rejecting ordinary user
edits; the escalation itself is already closed by `_reject_admin_role`.

## [1.0.0] — baseline

The state of this module before it carried a version: login, token refresh,
`/auth/me`, password change and reset, user CRUD under `/api/v1/users`, and the
local-only dev sign-in panel (`dev_auth.py`). Recorded retrospectively so 1.1.0
has something to be an increment of.
