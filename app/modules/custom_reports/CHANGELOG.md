# Changelog — custom_reports

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [0.2.0] — 2026-05-21

### Changed
- `/sizes` now filters to a curated `FOCUS_BRANDS` list by default
  (13 brands: AKRIS, AKRIS PUNTO, ALAIA, ALEXANDER MCQUEEN, COURREGES,
  ELEVENTY, IBLUES, JIL SANDER, KHAITE, MAISON MARGIELA MM6, MARELLA,
  MARNI INTERNATIONAL S.A, THE ATTICO). ELEVENTY and THE ATTICO have
  no SS25/SS26 items today but are kept in the list for future seasons.
- Single-brand query param `?brand=X` replaced with multi-brand
  `?brands=A,B,C` (comma-separated `vendor.vend_name` values).
- Pass `?brands=*` to disable the focus-brand filter and return every
  brand (previous v0.1 behaviour).
- Response now includes a `brands` field echoing the resolved filter
  (the `FOCUS_BRANDS` list when no override, or `null` when filter is
  disabled via `?brands=*`).

### Notes
- Vendor-name resolution for common aliases is encoded in `FOCUS_BRANDS`:
  AMQ → ALEXANDER MCQUEEN, MARNI → MARNI INTERNATIONAL S.A,
  MM / MM6 → MAISON MARGIELA MM6 (single vendor row in `rps.vendor`).

## [0.1.0] — 2026-05-21

Initial release. Pre-1.0 — the API may still change while the reports
catalogue grows.

### Added
- `GET /api/v1/custom-reports/sizes` — Brand × Size × Season report.
  - Per (brand, size, season): `imported_qty`, `sold_qty`,
    `on_hand_qty`, `sell_through_pct`, `avg_days_to_sell`, `sku_count`,
    `units_matched`.
  - `avg_days_to_sell` is FIFO unit-matched: each sold unit is paired
    with the oldest available receipt batch for the same SKU, and the
    average is taken across all matched units in the group.
  - Query params: `seasons` (default `SS25,SS26`), `brand`, `size`.

### Notes
- Receipts source: voucher events with `vou_class=0`, `slip_flag=0`,
  `held=0`, `status=4`, `vou_type=0` (positive receipts only — vendor
  returns excluded for cleaner FIFO).
- Sales source: document line items with `item_type=1` (sales only —
  customer returns excluded for cleaner FIFO).
- Receipt date = `voucher.post_date`. Sale date = `document.created_datetime`.
- Sold units exceeding total receipts for an item (data anomaly) are
  dropped from the FIFO output but still count in `sold_qty`.
- `units_matched` ≈ `sold_qty` is a sanity check — large divergence
  flags incomplete receipt history for a SKU (likely items received
  before the period covered).

### Open follow-ups
- Median days-to-sell (currently avg only).
- Period filter on sales (currently all-time within the selected seasons).
- Refactor to share the receipt/sale event pull with sale_through.
