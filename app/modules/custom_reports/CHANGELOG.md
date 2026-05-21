# Changelog — custom_reports

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [0.4.0] — 2026-05-21

### Changed
- `/sizes` now drops "catalog ghost" SKUs by default — items whose
  `invn_sbs_item.first_rcvd_date` is `NULL` (never physically received).
  These SKUs were inflating `sku_count` without contributing to any qty
  metric. For SS25+SS26 focus-brand scope this removed 20 SS25 + 42 SS26
  master-only entries (e.g. ALEXANDER MCQUEEN SS25 sizes 26/27/28 jeans
  registered 2024-12-03 with `first_rcvd_date IS NULL` after 17 months).
- New `include_never_received` parameter on both the service method
  and the `?include_never_received=true` query string opts back in to
  the previous behaviour (ghost SKUs included with all-zero qty metrics).
- `scripts/reports/generate_size_report.py` now accepts
  `--include-never-received`.

### Internals
- `SIZE_REPORT_ITEMS` query selects `i.first_rcvd_date` so the filter
  can be applied in pandas rather than templated into SQL.

## [0.3.0] — 2026-05-21

### Added
- CLI export script `scripts/reports/generate_size_report.py` that runs
  the size-by-brand-season report and writes the output to
  `document/reports/size_report_<seasons>_<YYYY-MM-DD>.xlsx` (+ a `.csv`
  by default). The Excel file has two sheets: `Size Report` (the rows)
  and `Metadata` (filter parameters + grand totals).
- Script flags: `--seasons`, `--brands` (`*` for all), `--format`
  (`xlsx` / `csv` / `both`), `--out-dir`, `--no-zero-sales`.
- Both `scripts/reports/` and `document/reports/` are gitignored, so
  generated files stay local-only.

### Notes
- Excel output uses `openpyxl`. Header row is frozen and columns are
  auto-sized for readability.
- `--no-zero-sales` drops rows with `sold_qty = 0` (the 59 SS26 rows in
  the current focus-brand snapshot are mostly zero-sale "new arrival"
  groups — useful to exclude when reading the report by hand).

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
