# Changelog — handcarry

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [0.3.0] — 2026-06-02

### Changed — CR #64: revert catalog to a UPC flag list, source product info live from Oracle

Oracle is the system of record for hand-carry items: goods are received
into Oracle first (with all metadata), then the user merely tags UPCs
in this tool. CR #58's denormalized columns were duplicating that data
and got skipped in real-world workflow — 616 of 1,313 rows in production
had NULL description/brand/etc. because the import dialog wasn't being
used.

#### Schema
- `rps.carrier_item` reverted to a UPC flag list. Idempotent
  `DROP COLUMN IF EXISTS` in `init_database` removes
  `description, brand, category, color, size, season, quantity_imported,
  price_before_vat, price_after_vat`.
- **Kept** `quantity_sold` (orphan-UPC override from v0.2.2) — for items
  Oracle no longer recognises, a non-NULL stored value wins over the
  (null) live join so the row shows as already sold-through.
- Final schema: `sid, scan_upc, quantity_sold`.

#### `GET /api/v1/handcarry`
- Response shape **unchanged** — same 12 fields per item. All except
  `id`, `upc`, and `quantity_sold` (override) now come live from Oracle
  via three existing helpers:
  - `fetch_oracle_product_info` → description, brand, category, color, size,
    season, prices.
  - `_lifetime_received_for` → `quantity_imported`.
  - `_lifetime_sold_for` → `quantity_sold` when the override is NULL.
- Live verification on 1,313 production UPCs: 823 (63%) now have full
  description/brand/price (up from the CR #58 backfill's 697 / 53%).
  Orphan UPCs continue to use their stored `quantity_sold` value.

#### `POST /api/v1/handcarry/import`
- Body simplifies back to `{ upcs: int[] }` (CR #57's original shape).
  The `records[]` path is removed entirely — there's no per-row data
  left to import. SKIP-on-exists semantics for existing UPCs.
- Response: `{ success, inserted, skipped, errors, total_received }`.
  `updated` field removed (nothing to update).

#### Tests
- 27 unit tests pass. `TestListItems` rewritten to mock the three Oracle
  join helpers + the simplified Postgres SELECT. `TestImportRecords`
  removed entirely. Route test `test_records_body_no_longer_accepted`
  asserts the legacy shape returns 400.

#### Migration / live impact
- `init_db.py` reverts the schema with idempotent `DROP COLUMN IF EXISTS`.
  All 1,313 UPC rows preserved; 490 orphan-override rows preserved.
  Existing UI calls keep working — same response shape, fresher data.

#### Bumped MINOR — breaking request body, additive shape stability
The `POST /import` request body shape narrows (drops `records[]`), so
strict MINOR per Keep-A-Changelog. `GET /handcarry` response is identical,
so existing UI doesn't need to ship for the backend change.

## [0.2.2] — 2026-05-25

### Changed
- `quantity_imported` for Oracle-known UPCs now comes from Oracle's
  posted receiving vouchers (`rps.voucher` + `rps.vou_item`,
  `vou_class=0, vou_type=0, status=4`) — the same source Oracle uses
  for non-hand-carry inventory. Previously the v0.2.0 backfill used
  lifetime sold qty, which under-reported imported counts whenever
  some units were still on hand. Floors at 1 so the row never shows
  imported=0.
- Orphan UPCs (no Oracle master record) keep the v0.2.1 behaviour —
  `quantity_imported = quantity_sold = 1` so they display as sold-out.

### Added
- `HandCarryQueries.ORACLE_LIFETIME_RECEIVED` — per-UPC sum of receiving
  voucher qty.
- `HandCarryService._lifetime_received_for(upcs)` helper, mirrors
  `_lifetime_sold_for`.

### Data migration
- One-off reconciliation against the canonical hand-carry template
  (`document/template/LIST ITEM TỔNG HỢP HÀNG CHA.xlsx`):
  - Deleted 83 junk rows that had been mis-imported as UPCs (80 Excel
    date serials in the 40K–50K range, plus 5 small numbers that were
    likely TK declaration numbers). Two rows in those numeric ranges
    were preserved because Oracle confirmed they're real products
    (UPC 27111 Fornasetti, UPC 45629 Marc Jacobs).
  - Inserted 126 new UPCs from the template — all Oracle-matched, no
    orphans.
- Re-ran `backfill_handcarry.py --force` with the new received-qty
  rule to correct `quantity_imported` for every Oracle-known row.

## [0.2.1] — 2026-05-25

### Changed
- Added a stored `quantity_sold` column to `rps.carrier_item` (nullable)
  that **takes precedence over the live Oracle join** when populated.
  Used for orphan UPCs — items in our hand-carry catalog that Oracle
  doesn't have a master record for (pre-Oracle items sold before the
  late-2023 migration cut-off, 573 of them in our dev DB).
- The one-time backfill script now sets `quantity_sold = quantity_imported`
  for those orphan rows, so they display as sold-out (remaining = 0)
  instead of "imported = N, sold = 0, remaining = N".
- Active items (those Oracle still knows about) keep `quantity_sold`
  NULL in storage — the live Oracle lifetime-sales join continues to
  drive the response value, so new sales keep updating in real time.

### API
- No contract change — `GET /api/v1/handcarry` still returns
  `quantity_sold` per item; the value just has a different source for
  orphans.

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
