# Changelog — handcarry

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [0.1.0] — 2026-05-20

Initial release. Pre-1.0 — the API may still change while the frontend
import UX settles.

### Added
- `GET /api/v1/handcarry` — list all hand-carry UPCs (`?search=` partial filter)
- `POST /api/v1/handcarry/import` — bulk upsert UPCs from an Excel/CSV
  import. Existing UPCs are skipped; non-integer values surface in
  `errors` without aborting the batch. Manager-level auth.
- `PUT /api/v1/handcarry/:id` — update a single record's UPC.
  409 on duplicate, 404 on missing id. Manager-level auth.
- `DELETE /api/v1/handcarry/:id` — remove a single record.
  404 on missing id. Manager-level auth.

### Notes
- Backing table is the existing `rps.carrier_item` (columns: `sid`,
  `scan_upc`). No migration introduced — the table predates this module
  and is currently read by `CommissionRepository.get_hand_carry_upcs`
  for commission pool exclusion (CR #56).
- A future PATCH may migrate commission to call this module's service
  directly so there's a single owner of the UPC list. For now both
  paths read the same table, so consistency is preserved.

### Related
- CR #57 (initial scaffold + endpoints) — see
  `GBL_HR_Frontend/docs/cr/handcarry.md`.
- CR #56 (verified that the commission pool excludes hand carry) — the
  source of truth for what UPCs flow through this exclusion lives in
  `rps.carrier_item`, which this module now owns.
