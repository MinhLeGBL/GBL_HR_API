# Changelog — permissions

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Baseline — formal `__version__` tracking begins. Reflects the cumulative
result of CRs #1, #2 (the major v2 rewrite), and #44 (full specs in
`GBL_HR_Frontend/docs/cr/permissions.md`).

### Endpoints
- `GET /api/v1/departments` — departments lookup
- `GET /api/v1/permissions/me` — current user's accessible sections (flat)
- `GET /api/v1/sections/permissions` — full per-section config
- `PUT /api/v1/sections/:code/access` — update section access config

### Pre-versioning history (chronological, newest first)

- **2026-04-17** — CR #44: Seeded `BATHROOM_PRICE_CHECK` and `CRM` sections
  in `init_db.py` with empty defaults (admin-only until configured).
- **2026-03-18** — CR #2: Flat section permission model (v2). Replaced
  two-layer group-based system with per-section
  `(departments, roles, includes, excludes_v2)`. New tables:
  `section_departments`, `section_roles`, `section_inclusions`,
  `section_exclusions_v2`. v1 grouped endpoints/tables deprecated.
- **2026-02-04** — CR #1: Per-department access management — 5 endpoints
  for staff-access toggle and per-section employee exclusions.
  Superseded by CR #2.
