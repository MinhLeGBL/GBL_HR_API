# Changelog — custom_reports

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

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
