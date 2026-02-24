---
name: responsecr
description: Update the API change request file to reflect the current backend implementation status
---

Update the frontend CR documentation to reflect the current backend implementation status.

## CR file locations

- Index: /Users/minhle/Desktop/GBL_HR_Frontend/docs/API_REFERENCE.md
- Per-feature CRs: /Users/minhle/Desktop/GBL_HR_Frontend/docs/cr/*.md
  - commission.md — Commission feature (CR #4–#10)
  - employees.md — Employee management (CR #2, #5)
  - permissions.md — Permissions & access (CR #1)
  - users.md — User management & auth

## What to update

For each feature file affected by recent backend changes:

1. **In `docs/cr/<feature>.md`:**
   - Update endpoint documentation with the actual response shape if it differs from what was requested
   - Add implementation notes, constraints, or edge cases the frontend should know about
   - Update change log rows from ⏳ to Done for completed items
   - Never remove frontend-written request sections — only annotate with actual details

2. **In `docs/API_REFERENCE.md`:**
   - Add new endpoints to the appropriate endpoint table if not already there
   - Update change log rows from ⏳ to Done for completed items

## Rules

- Be explicit about any deviations from what was requested (different field names, extra fields, changed auth requirements)
- Keep response shape examples accurate — the frontend codes directly against these specs
- New features get new `docs/cr/<feature>.md` files — follow the same pattern as existing ones
- If a new CR file is created, add a reference row to the API_REFERENCE.md per-feature table
