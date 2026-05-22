# Changelog — handcarry

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [0.2.0] — 2026-05-22

### Added (CR #58)
- Extended `rps.carrier_item` schema with 9 nullable columns
  (`description`, `brand`, `category`, `color`, `size`, `season`,
  `quantity_imported`, `price_before_vat`, `price_after_vat`). Migration
  lives in `HandCarryService.init_database()` and is idempotent.
- New `import_records` service method + matching route body shape:
  ```
  POST /api/v1/handcarry/import
  { "records": [ { "upc", "description", "brand", "category", "color",
                   "size", "season", "quantity_imported",
                   "price_before_vat", "price_after_vat" }, ... ] }
  ```
  REPLACE semantics — existing UPCs have all fields overwritten by the
  import row's values (the file is the source of truth). Response adds
  an `updated` count alongside `inserted` / `skipped` / `errors`.
- `GET /api/v1/handcarry` response extended with all new fields plus
  `quantity_sold` — live-joined per request from Oracle as the
  **lifetime** sum of sales for the UPC.
- Backfill script `scripts/database/backfill_handcarry.py`: one-time
  migration that, for each of the 1,270 existing rows, sets product
  info from Oracle and `quantity_imported = lifetime_sold_qty`
  (or `1` if the UPC has no sales).
- Shared Oracle queries lifted into `queries.py` (`HandCarryQueries`).

### Backward compatibility
- The legacy `{ upcs: int[] }` body shape is still accepted by
  `POST /import` for callers that haven't migrated. New UPCs are
  inserted with only `scan_upc` set; existing UPCs are skipped (old
  behaviour). The new `records[]` shape is preferred.
- The legacy `import_upcs(upcs)` service method is preserved.

### Internals
- `HandCarryService.fetch_oracle_product_info` and `_lifetime_sold_for`
  helpers shared between `list_items` and the backfill script.
- Oracle IN-list calls chunk at 500 UPCs to stay under the 1000-element
  parser limit.

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
