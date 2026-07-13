# Changelog — reports

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-07-13

### Added — CR #78: `GET /reports/sale-comparison` (live sale comparison)

New read-only module backing the **Report → Live Sale Comparison** page. The
user picks two independent date ranges (A and B); the endpoint returns revenue,
bill count, average bill, and a new-vs-returning customer breakdown per period.

#### Endpoint

`GET /api/v1/reports/sale-comparison` — `@token_required`.

Query params (all required, `YYYY-MM-DD`, inclusive both ends):
`from_a`, `to_a`, `from_b`, `to_b`.

Per-period response fields: `from`, `to`, `total_revenue`, `bill_count`,
`avg_bill` (`null` when `bill_count == 0`), `new_customers`,
`returning_customers`, `new_customer_revenue`, `returning_customer_revenue`.

- **400** when a date is unparseable, `from > to`, a required param is missing,
  or the inclusive range exceeds 366 days.
- **200** with zeros (and `avg_bill: null`) for a valid range with no data.
- **500** (`SERVER_ERROR`) on Oracle failure.

#### Business rules (decisions — see CR response for confirmation)

- **Revenue** uses the CRM net-line formula
  `(PRICE − TAX) × (1 − item_disc) × (1 − doc_disc)`, `ITEM_TYPE = 1` (sales
  only). `to` is inclusive (query uses `< to + 1 day`).
- **`total_revenue` / `bill_count`** cover all in-range sales **except the
  SYSADMIN** system account. Walk-in (TOURIST) and anonymous (`NULL BT_CUID`)
  sales are included — they are real revenue.
- **"New"** = a customer whose **first-ever** `ITEM_TYPE = 1` purchase across all
  history falls in `[from, to]` (stable — new for exactly one period ever).
  **"Returning"** = an identifiable customer who bought in range with a prior
  purchase before `from`. Only identifiable customers (`BT_CUID` not null, not
  SYSADMIN / TOURIST) are counted.
- **`returning_customer_revenue = total_revenue − new_customer_revenue`**, so
  walk-in / anonymous revenue folds into "returning" and
  `new_customer_revenue + returning_customer_revenue == total_revenue` **exactly**
  (stronger than the CR's "≈").

#### Notes

- No Postgres tables — pure Oracle aggregation. Nothing added to `init_db.py`.
- The `LIVE_COMPARISON` permission section is seeded (permissions module) so the
  frontend can show the sidebar entry and manage access; the endpoint itself
  stays `@token_required` until a section-access check is wired.
