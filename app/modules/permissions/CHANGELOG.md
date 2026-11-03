# Changelog — permissions

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [2.0.0] — 2026-11-03

### BREAKING — resolution rule 5 is the department alone

`get_user_permissions_v2` no longer consults `section_roles`. Rule 5 was
**department AND role**; it is now **department**.

**Why it had to change.** The access page no longer has a role field — role
decided nothing useful there, being half of an AND that per-user inclusions
routinely bypassed. With the field gone the stored roles would have gone empty,
and `department AND role` against an empty role set matches nobody: every
department grant would have silently granted no one. Dropping the role half was
the only way for a department grant to keep meaning anything.

**This WIDENS rule 5 where grants are left as they are.** `USER_MANAGEMENT`
granted `IT, HR` + role `ADMIN` admitted only admins in those departments;
department-only admits every IT and HR user. So the switch is not safe on
existing data by itself — see the migration below, which is mandatory, not
optional.

`section_roles` is left in place and is dead to resolution. It is not dropped
because it is the only remaining record of what the old rules granted, which the
migration reads. `PUT /sections/<code>/access` still accepts `allowed_roles` for
compatibility and ignores it.

### Added — `scripts/database/migrate_section_access_to_departments.py`

Makes the switch access-neutral. For each section it computes who has access
TODAY under the old rules, then rewrites the grants so the new rules resolve to
exactly the same people:

- a department is kept/added when every member of it who is not excluded
  already has access today — preferred wherever it fits, because the grant is
  standing and so keeps covering people hired later;
- anyone left over becomes an explicit `section_inclusions` row;
- existing exclusions are preserved as they are.

The admin is ignored throughout: admin bypasses every check, so it is never part
of what a grant has to reproduce.

Read-only by default, printing a per-section table.

> **The delta column in that table proves nothing.** `propose` derives its
> grants from `effective_old` and the delta re-derives the result from the
> proposal, so it is algebraically always zero — it was described as a safety
> gate in an earlier revision and was never one. It is kept only as a
> consistency check on the proposal code.
>
> **The real check runs on `--apply`:** the SHIPPING resolver is run over the
> committed database and compared against what the OLD rules granted. Anything
> different and the grants are restored exactly as they were, nothing is
> marked applied, and the script exits non-zero. So the only thing taken on
> trust is `effective_old`; the apply and the new rule are both verified.
>
> Note the comparison is against the OLD rules, **not** against what the
> resolver said beforehand. Deployed code is already department-only, so on
> un-migrated data the resolver reports the widened set — the very widening
> this script undoes. An earlier revision compared against that and reported
> the IT and HR managers "losing" `USER_MANAGEMENT`, which they only had
> because of the widening.

**It is NOT idempotent, and refuses to run twice.** Its first run moves access
out of per-user inclusions and into department grants; a second run reads
`section_roles` as the baseline, which no longer describes reality, concludes
the department grant is unjustified, and deletes it. Observed on dev: a second
run proposed removing `LIVE_COMPARISON` from both BOD managers. A marker table
(`section_access_migration`) is written **after** verification passes, so a
rolled-back run does not block the retry. `--force` overrides.

Writes run in one transaction (`conn.autocommit` is honoured and restored), so
a failure part-way cannot leave a section holding no grants at all.

A department that currently has no members — or whose every member is excluded
— cannot be derived from today's access, but if it is granted now that grant is
a deliberate pre-authorisation the access page allows. It is carried over
rather than deleted.

It also closes a gap recorded in 1.1.0: `LIVE_COMPARISON` had department `BOD`
and no roles, so rule 5 granted nobody and its two BOD managers were held up by
hand-added inclusion rows. It now carries a standing `BOD` grant, so a future
BOD hire inherits it.

> **Deployment order matters.** Deploy, then `python scripts/database/init_db.py`,
> then this migration. Running the new code against un-migrated data is the
> widening described above, and that window is unavoidable with this design.

### Changed — the seed no longer re-grants over a migrated database

`_seed_v2_permissions` is additive, which was harmless when rule 5 needed a
role match and is not now: a re-inserted department grant admits the whole
department, so running `init_db.py` after the migration would restore grants an
administrator had narrowed through the access page. It now seeds only when
`section_departments` is empty, and says so when it skips.

## [1.1.0] — 2026-11-03

### Changed — the access page is reachable by every role in the IT department

`ACCESS_MANAGEMENT` now grants `STAFF` as well as `ADMIN` and `MANAGER`
(`_seed_v2_permissions`), so "admin and members of the IT department" holds for
all three roles.

**Why it did not already.** Resolution rule 5 is department **AND** role. The
section granted department `IT` and roles `ADMIN, MANAGER`, so an IT staff user
matched the department, missed the role, and was denied — which does not match
the intent.

Verified on the local stack: before, an IT staff user resolved to `['DASHBOARD']`
only; after, to `['ACCESS_MANAGEMENT', 'DASHBOARD']`. An ACC staff user is
unaffected (`['DASHBOARD', 'HAND_CARRY']`) — the department half still binds.

> **Requires `python scripts/database/init_db.py` on the server after deploy.**
> Schema and seed changes do **not** run on deploy (see CLAUDE.md → Testing →
> Schema migrations; this is what broke CR #82). `_seed_v2_permissions` is
> additive and idempotent (`ON CONFLICT DO NOTHING`), so it is safe to re-run.
>
> Note the seed is additive in both directions: re-running it restores any
> *seeded* grant an administrator has since removed through the UI. That is
> pre-existing behaviour, not introduced here, but it is worth knowing before
> running it.

### Unchanged, and worth recording

- **Modifying `ACCESS_MANAGEMENT`'s own access is admin-only** and already was,
  in `routes.py` (`update_section_access`). Likewise reading the permission
  matrix and editing any other section require admin or `ACCESS_MANAGEMENT`.
  These checks live in the handlers rather than in a decorator, so the route
  carrying only `@token_required` does *not* mean it is ungated.
- **`LIVE_COMPARISON` has no role grants at all** — department `BOD`, roles
  none. Rule 5 therefore cannot grant it to anybody; the two BOD managers who
  have it hold per-user inclusions added by hand, so a newly hired BOD manager
  would **not** inherit it. Left as-is: it is production permission data and
  changing it is the product owner's call, not a code fix.

## [1.0.0] — baseline

The state of this module before it carried a version: departments CRUD, the v2
flat section model (`sections`, `section_departments`, `section_roles`,
`section_inclusions`, `section_exclusions_v2`), `GET /permissions/me`,
`GET /sections/permissions` and `PUT /sections/<code>/access`, and
`get_user_permissions_v2`'s six-step resolution. Recorded retrospectively so
1.1.0 has something to be an increment of.
