---
name: checkcr
description: Check the frontend API change request against the current backend implementation
---

Read the frontend CR documentation and compare it against the current backend implementation.

## CR file locations

- Index: /Users/minhle/Desktop/GBL_HR_Frontend/docs/API_REFERENCE.md
- Per-feature CRs: /Users/minhle/Desktop/GBL_HR_Frontend/docs/cr/*.md
  - commission.md — Commission feature (CR #4–#10)
  - employees.md — Employee management (CR #2, #5)
  - permissions.md — Permissions & access (CR #1)
  - users.md — User management & auth

## What to check

1. Read ALL files under docs/cr/ and the API_REFERENCE.md index
2. Look for any items marked with ⏳ (pending backend implementation)
3. For each endpoint documented in the CR files, verify against the actual backend routes and service code:
   - Does the endpoint exist?
   - Do request/response field names and types match?
   - Do validation rules match?
   - Are there behavioral differences (auth requirements, error shapes, edge cases)?
4. Check for any notes or instructions from the frontend team that need attention

## Output

Report back with a summary organized by feature file:
- **Pending items** (⏳) that need implementation
- **Mismatches** between spec and actual backend code
- **Notes** from the frontend team requiring attention
- **All clear** sections where everything matches
