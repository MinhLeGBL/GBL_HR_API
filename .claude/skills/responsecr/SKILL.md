---
name: responsecr
description: Update the API change request file to reflect the current backend implementation status
---

Update the frontend CR documentation to reflect the current backend implementation status.

## CR file locations

- Index: /Users/minhle/Desktop/GBL_HR_Frontend/docs/API_REFERENCE.md
- Per-feature CRs: /Users/minhle/Desktop/GBL_HR_Frontend/docs/cr/*.md

### Feature-to-file mapping

| Branch pattern | CR file |
|----------------|---------|
| `feature/commission*` | `cr/commission.md` |
| `feature/employee*` | `cr/employees.md` |
| `feature/permission*` | `cr/permissions.md` |
| `feature/user*` or `feature/auth*` | `cr/users.md` |

## Branch-sensitive updating

1. Detect the current git branch name
2. Only update the CR file that matches the current feature branch (see mapping above)
3. If the branch doesn't match any known feature, ask the user which CR file to update

## CR file structure

CR files have two sections separated by a line containing `═══`:

```
[Pending CRs — active specs, ⏳ items, current endpoint docs]

# ═══ Completed CRs (Archive) ═══

[Completed CR summaries — short paragraph per CR]
```

## What to update

**Respond directly in the pending section** (above the `═══` separator):

1. Update endpoint documentation with the actual response shape if it differs from what was requested
2. Add implementation notes, constraints, or edge cases the frontend should know about
3. Mark completed items: change ⏳ to Done in the pending section
4. Never remove frontend-written request sections — only annotate with actual details

**Do NOT move completed CRs to the archive section.** The frontend team handles confirmation and archival. Only write your response in the pending area.

**In `docs/API_REFERENCE.md`:**
- Add new endpoints to the appropriate endpoint table if not already there
- Update change log rows from ⏳ to Done for completed items

## Rules

- Be explicit about any deviations from what was requested (different field names, extra fields, changed auth requirements)
- Keep response shape examples accurate — the frontend codes directly against these specs
- New features get new `docs/cr/<feature>.md` files — follow the same pattern as existing ones
- If a new CR file is created, add a reference row to the API_REFERENCE.md per-feature table
