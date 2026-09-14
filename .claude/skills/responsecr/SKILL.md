---
name: responsecr
description: Update the API change request file to reflect the current backend implementation status
---

Update the frontend CR documentation to reflect the current backend implementation status.

## CR system overview

The two repos exchange change requests symmetrically. The frontend's CR file is
where the backend writes its **response** to a frontend-raised request — inline,
in the same file.

- **Frontend's incoming requests:** `GBL_MASTER_FRONTEND/docs/cr/<feature>.md`.
  This skill writes responses there.
- **Backend's own outgoing requests** live in this repo at `docs/cr/<feature>.md`
  (feature branches only — stripped at deploy).

Path resolution: the frontend repo is typically checked out as a sibling of this
repo (`../GBL_MASTER_FRONTEND/`). Adjust if your local layout differs.

## CR file locations

- Per-feature CRs: `../GBL_MASTER_FRONTEND/docs/cr/*.md`
- Index (legacy): `../GBL_MASTER_FRONTEND/docs/API_REFERENCE.md`

### Feature-to-file mapping

| Branch pattern | CR file |
|----------------|---------|
| `feature/commission*` | `cr/commission.md` |
| `feature/employee*` | `cr/employees.md` |
| `feature/permission*` | `cr/permissions.md` |
| `feature/user*` or `feature/auth*` | `cr/users.md` |
| `feature/bathroom*` | `cr/bathroom-price-check.md` |
| `feature/crm*` | `cr/crm.md` |
| `feature/handcarry*` | `cr/handcarry.md` |
| `feature/account-payable*` | `cr/account-payable.md` |

## Branch-sensitive updating

1. Detect the current git branch name
2. Only update the CR file that matches the current feature branch (see mapping above)
3. If the branch doesn't match any known feature, ask the user which CR file to update

## CR file structure

Per CR #74, frontend CR files are migrating away from the `═══ Completed CRs (Archive) ═══`
split. Going forward, each file contains **only pending/active requests** — completed
CRs move to `docs/changes/<feature>.md` on the frontend side.

During the transition, files may still carry the archive section. Always write
responses **in the pending portion** (above any `═══` separator if present).

## What to update

**Respond directly in the pending section** of the matching file:

1. Update endpoint documentation with the actual response shape if it differs from
   what was requested
2. Add implementation notes, constraints, or edge cases the frontend should know about
3. Mark completed items: change ⏳ to Done in the pending section
4. Never remove frontend-written request sections — only annotate with actual details

**Do NOT move completed CRs to the archive section** (when one exists). The frontend
team handles confirmation and archival.

**In `docs/API_REFERENCE.md`** (legacy index, still used during transition):
- Add new endpoints to the appropriate endpoint table if not already there
- Update change log rows from ⏳ to Done for completed items

## Rules

- Be explicit about any deviations from what was requested (different field names,
  extra fields, changed auth requirements)
- Keep response shape examples accurate — the frontend codes directly against these specs
- New features get new `docs/cr/<feature>.md` files — follow the same pattern as existing ones
- If a new CR file is created, add a reference row to the API_REFERENCE.md per-feature table
