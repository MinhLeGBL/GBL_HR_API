# Changelog — reports

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.2.0] — 2026-07-13

### Changed — CR #80: break out TOURIST as a third customer bucket

Follow-up to CR #78. Walk-in (TOURIST) revenue was previously folded into
`returning_customer_revenue`. The PO considers tourists conceptually *new /
one-time* rather than returning, so `GET /reports/sale-comparison` now exposes
them as their own bucket. **Two new fields** on each `period_a` / `period_b`:

- `tourist_customers` — count of **distinct TOURIST bills** in the period
  (one bill = one tourist; the POS books all walk-ins under a single TOURIST
  record, so bills stand in for physical customers).
- `tourist_customer_revenue` — net revenue from those bills.

**Behavior change:** `returning_customer_revenue` **no longer includes** TOURIST
revenue — it is now `total_revenue − new_customer_revenue − tourist_customer_revenue`
(identifiable repeat customers only).

Updated invariant (still exact):
`new_customer_revenue + returning_customer_revenue + tourist_customer_revenue == total_revenue`.

`new_customers` / `returning_customers` remain identifiable-only counts;
`tourist_customers` is orthogonal (bills, not distinct customers).

**Anonymous (`NULL` customer) decision:** folded into the tourist bucket — a
NULL-customer bill is an anonymous walk-in, same nature as TOURIST. Confirmed
against live data: **zero** NULL-customer bills exist today (every walk-in is
booked under TOURIST), so this is future-proofing with no effect on current
numbers. SYSADMIN remains excluded from all figures.

## [1.1.0] — 2026-07-13

### Added — CR #79: per-user pinned periods for the Live Comparison page

Users can pin Period A and/or Period B so the selectors default to their saved
dates across devices. Per-user, per-side, independent.

#### Endpoints (both `@token_required`; `user_id` derived from the JWT `g.sid`)

- **`GET /api/v1/reports/live-comparison/pins`** — the caller's pins. Always
  `200`; unset side → `null` (no 404).
  ```jsonc
  { "success": true, "period_a": { "from": "2026-06-01", "to": "2026-06-30" }, "period_b": null }
  ```
- **`PUT /api/v1/reports/live-comparison/pins`** — replace the caller's pins.
  Body requires both `period_a` and `period_b`; each is `{from, to}` or `null`
  (unpin). Returns the persisted pins (same shape as GET).
  - **400** — missing top-level `period_a`/`period_b`, a pin that isn't null or
    `{from, to}`, an unparseable date, `from > to`, or a range > 366 days
    (same rules as CR #78).
  - No delete endpoint — `PUT {period_a: null, period_b: null}` clears both.
  - **500** (`SERVER_ERROR`) on Postgres failure.

#### Storage

New `live_comparison_pins` table (Postgres) — one row per user:
`user_id BIGINT PK REFERENCES users(sid) ON DELETE CASCADE`, four nullable DATE
columns (`period_a_from/to`, `period_b_from/to`), `updated_at`. Created via
`ReportsService.init_database` (wired into `scripts/database/init_db.py`, after
auth so the `users` FK target exists). Upsert is `INSERT … ON CONFLICT
(user_id) DO UPDATE`. This is the module's first Postgres table (CR #78 was
Oracle-only). Went with a dedicated table (no general user-preferences table
exists) as the CR suggested.

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
