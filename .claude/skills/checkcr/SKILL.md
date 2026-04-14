---
name: checkcr
description: Check the frontend API change request against the current backend implementation
---

Read the frontend CR documentation and compare it against the current backend implementation.

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
| `feature/bathroom*` | `cr/bathroom-price-check.md` |

## Branch-sensitive checking

1. Detect the current git branch name
2. Only read the CR file that matches the current feature branch (see mapping above)
3. If the branch doesn't match any known feature, fall back to reading all CR files

## CR file structure

CR files have two sections separated by a line containing `═══`:

```
[Pending CRs — active specs, ⏳ items, current endpoint docs]

# ═══ Completed CRs (Archive) ═══

[Completed CR summaries — only read if looking up a specific old CR]
```

**Only read down to the `═══` separator.** The completed section below it is an archive — skip it unless you need to look up a specific old CR by number.

## What to check

1. Read the matching CR file **up to the separator only**
2. Look for any items marked with ⏳ (pending backend implementation)
3. For each pending item and endpoint documented above the separator, verify against the actual backend routes and service code:
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
