---
name: checkcr
description: Check the frontend API change request against the current backend implementation
---

Read the frontend CR documentation and compare it against the current backend implementation.

## CR system overview

The two repos exchange change requests symmetrically. Each repo's `docs/cr/` holds
its **outgoing** asks; the fulfiller reads the requester's repo.

- **Incoming requests TO the backend** live in the frontend repo at
  `GBL_MASTER_FRONTEND/docs/cr/<feature>.md`. This skill reads from there.
- **Outgoing requests FROM the backend** live in this repo at
  `docs/cr/<feature>.md` (feature branches only — stripped at deploy).

Path resolution: the frontend repo is typically checked out as a sibling of this
repo (`../GBL_MASTER_FRONTEND/`). Adjust if your local layout differs.

## CR file locations (incoming)

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

## Branch-sensitive checking

1. Detect the current git branch name
2. Only read the CR file that matches the current feature branch (see mapping above)
3. If the branch doesn't match any known feature, fall back to reading all CR files

## CR file structure

Per CR #74, frontend CR files are migrating away from the `═══ Completed CRs (Archive) ═══`
split. Going forward, each file contains **only pending/active requests** — completed
CRs move to `docs/changes/<feature>.md` on the frontend side.

During the transition, files may still carry the archive section. Handle both:

- **If `═══` is present:** read only up to the separator.
- **If `═══` is absent:** read the entire file — it's all pending.

## What to check

1. Read the matching CR file (pending portion only — see above)
2. Look for any items marked with ⏳ (pending backend implementation)
3. For each pending item and endpoint documented in the pending section, verify against
   the actual backend routes and service code:
   - Does the endpoint exist?
   - Do request/response field names and types match?
   - Do validation rules match?
   - Are there behavioral differences (auth requirements, error shapes, edge cases)?
4. Check for any notes or instructions from the frontend team that need attention

## Output

Report back with a summary:
- **Pending items** (⏳) that need implementation
- **Mismatches** between spec and actual backend code
- **Notes** from the frontend team requiring attention
- **All clear** sections where everything matches
