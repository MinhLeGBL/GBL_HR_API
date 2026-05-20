# Changelog — sale_through

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Initial release.

### Added
- `GET /api/v1/sale-through/by-brand-season-category` — aggregates
  imported / sold / on-hand quantities by brand × season × category,
  with sell-through % per group. Optional `?brand=`, `?season=`,
  `?category=` filters.
- `ITEM_INVENTORY` query: per-item rollup pulling voucher receipts
  (imported), document sales, current on-hand snapshot, in-transit,
  slip transfers, and adjustments. Verified byte-equivalent to the
  team's reference query (55,303 rows).
- `BRAND_SEASON_CATEGORY` query: aggregation wrapper over `ITEM_INVENTORY`.
- `PO_LINES` query: line-level PO detail with base cost
  (`po_item.cost` / `invn_sbs_item.cost`) and landed cost
  (`invn_sbs_item.UDF1_STRING`), computed `ord_*_value` / `rcvd_*_value`
  columns. Not yet exposed as an endpoint.
