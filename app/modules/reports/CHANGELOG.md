# Changelog — reports

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.5.0] — 2026-07-17

### Added — CR #83: `avg_discount_rate` per period on `GET /reports/sale-comparison`

New field on each of `period_a` / `period_b`:

- `avg_discount_rate` — **value-weighted** average discount rate over the
  period's sale lines, as a **percent** rounded to 1 decimal (e.g. `73.5`).
  `null` when the period has no gross sales.

**Definition:** `100 × (gross − net) / gross`, where
- `gross` = ex-tax **pre-discount** list value of sale lines =
  `Σ (di.ORIG_PRICE − di.ORIG_TAX_AMT) × QTY` (commission's `FULL_PRICE`;
  `ORIG_PRICE ≥ PRICE` so `gross ≥ net ≥ 0`), and
- `net` = the existing ex-tax revenue `Σ _NET_LINE`. `gross − net` therefore
  captures **both** discount layers (item discount baked into `di.PRICE`, and
  the document-level `d.DISC_PERC`).

**Scope decisions:**
- **Sales only** — computed on `ITEM_TYPE = 1` lines inside the period. It does
  **not** use the returns-netted `total_revenue` (a discount rate is a property
  of the sale line), matching the FE's reading. So `avg_discount_rate` is
  unaffected by the v1.4.0 returns tail.
- Respects the CR #81 `store_a` / `store_b` store filter and the date range,
  exactly like the other metrics.

Implemented by adding two sale-only aggregates (`gross_sales_revenue`,
`net_sales_revenue`) to `period_totals`; the service derives the rate.
**Response shape gains one field** (additive, backward-compatible) — the FE
treats it as optional. Verified live: RWD 2026-07-15 → gross 1.63B, net 432M,
`avg_discount_rate = 73.5` (== manual SQL).

## [1.4.0] — 2026-07-16

### Added — net returns into `total_revenue` (30-day tail window)

`GET /reports/sale-comparison` now subtracts returns (`ITEM_TYPE = 2`) from
`total_revenue`. A return is netted when it posts within a **flat 30 days after
the period's last day** — window `[from, to + 30 days]` — regardless of whether
its original sale fell in the period (deliberately simple: no linkage to
`RETURNED_ITEM_INVOICE_SID`). Sale lines are still bounded to the period itself,
so a sale in the tail is not counted. Response **shape unchanged**; no frontend
CR. (Because the tail can extend past today, a recent period's figure keeps
decreasing as late returns arrive — inherent to the rule.)

**Scope of the netting (product decisions):**
- **Only `total_revenue` nets returns.** `bill_count` stays the count of
  distinct **sale** bills in `[from, to]` (so `avg_bill = net_revenue /
  sale_bills`); `new_customers` / `returning_customers`, `new_customer_revenue`,
  `tourist_customers`, and `tourist_customer_revenue` remain **sales-only**.
- The service's residual `returning_customer_revenue = total − new − tourist`
  therefore **absorbs** the return adjustment (it can go negative if returns
  exceed identifiable-returning sales in the window). The invariant
  `new + returning + tourist == total_revenue` still holds exactly.

Implemented in `period_totals` via a broadened scan (`ITEM_TYPE IN (1,2)` up to
`:returns_cutoff = to_exclusive + 30d`) with per-aggregate CASE gating; returns
are also store-scoped by the CR #81 `store_filter`.

### Fixed — review follow-ups (PR #46)

- **Unmapped vs unknown store id** — a store that exists in `GET /stores` but has
  no `store_rp_sid` now returns a distinct `... not linked to a POS store (no
  Retail Pro mapping)` error instead of the misleading "unknown store id".
  `get_store_sids` returns `{id: sid|None}` so the service can tell them apart.
- **Out-of-range pin `store_ids` → 400** — an int outside Postgres BIGINT range
  in a pin's `store_ids` is now rejected as `INVALID_INPUT` instead of raising
  at INSERT and escaping as a 500.
- **Docstrings** — `set_pins` / `fetch_period_metrics` updated to reflect
  `store_ids` and the returns-netted `total_revenue`.

## [1.3.1] — 2026-07-16

### Fixed — revenue formula double-applied the item discount (undercount ~2.6×)

Surfaced by CR #81: scoping to a single store (Runway Diamond / `RWD`) showed
2026-07-15 revenue of ~166M when the real figure is ~432M. Response **shape is
unchanged** — only the numbers are corrected — so this is a pure backend fix
(no frontend CR); all `*_revenue` / `avg_bill` values on
`GET /reports/sale-comparison` now read materially higher.

**Root cause** (verified against live Oracle data): the net-line expression was
`(PRICE − TAX) × (1 − item_disc) × (1 − doc_disc)`, but `di.PRICE` is **already
the discounted unit selling price** — `ORIG_PRICE × (1 − di.DISC_PERC/100) ==
di.PRICE` holds on every line. Re-applying `di.DISC_PERC` discounted a second
time; with Diamond's ~69% average item discount that cut revenue to roughly a
third. A secondary defect: `di.PRICE` is a **unit** price, so line totals were
missing `× QTY` (masked at RWD 07-15 where every line was qty 1).

**Corrected** `_NET_LINE` (ex-tax, preserves the CR #78 net intent):
`(di.PRICE − NVL(di.TAX_AMT,0)) × NVL(di.QTY,0) × (1 − NVL(d.DISC_PERC,0)/100)`
- item discount **removed** (already in `PRICE`);
- `× QTY` **added** (`PRICE` is per-unit; `TAX_AMT` is per-unit too);
- document-level discount **kept** — confirmed NOT baked into `PRICE`.

Tax basis: subtracts each item's **real** `di.TAX_AMT` (the data carries mixed
8% / 10% VAT), NOT the fixed `price / 1.1` shortcut — that 10% rate exists only
for the employee-commission calc and would over-strip tax on 8%-VAT lines.
Reconciles with commission's ex-tax `TOTAL_SALES` to within 0.0035% over 6
months (the residual is qty>1 / doc-discount lines where this formula handles
per-unit tax more precisely).

Applies to every figure derived from `_NET_LINE`: `total_revenue`,
`new_customer_revenue`, `tourist_customer_revenue`, and the derived
`returning_customer_revenue` / `avg_bill`.

> **Known follow-up:** `app/modules/crm` computes `monetary` with the identical
> pre-fix expression and carries the same bug — to be corrected on the CRM
> branch (RFM scores/segments will shift), tracked separately.

## [1.3.0] — 2026-07-16

### Added — CR #81: per-period store scope (multi-select)

Each period on the Live Comparison page can now be scoped to one or more
stores (union). Backward-compatible — omitting the scope keeps the aggregate
all-stores behavior.

#### `GET /reports/sale-comparison` — two new optional query params

- `store_a`, `store_b` — comma-separated `GET /stores` ids (e.g. `3` or `3,7`).
  Each side is independent; omitted/blank → all stores for that period.
- Scope is a **union**: `store_a=3,7` → period A = stores 3 + 7 combined. All
  `PeriodMetrics` fields (revenue, bills, new/returning/tourist) reflect it.
- **Id resolution:** the params are Postgres `stores.id`; the backend resolves
  each to its Oracle `STORE.SID` via `stores.store_rp_sid` and filters
  `DOCUMENT.STORE_SID IN (...)`. Any **unknown or unmapped** id (no row, or a
  null/blank `store_rp_sid`) → **`400 INVALID_INPUT`** (confirms the CR ask). A
  non-integer token → `400`.
- **"New vs returning" under a store scope:** the store filter narrows *which
  in-range bills count*, but the first-ever-purchase scan stays **global**.
  "New" therefore keeps its documented meaning — first purchase *ever, any
  store* — so a long-time customer's first visit to a newly-scoped store is
  still "returning", not "new". (Flagged to the frontend for confirmation.)

#### Pins — `store_ids` array per side

`GET` / `PUT /reports/live-comparison/pins` — each of `period_a` / `period_b`
now carries `store_ids`:

```jsonc
{ "period_a": { "from": "2026-06-01", "to": "2026-06-30", "store_ids": [3, 7] },
  "period_b": { "from": "2026-07-01", "to": "2026-07-31", "store_ids": [] } }
```

- `store_ids`: array of int store ids; `[]` / omitted → all stores (stored as
  `NULL`, always read back as `[]`).
- **Validation:** must be an array of ints (rejects non-list, non-int, bool,
  `null` element → `400`). Pin ids are persisted **as-is** — not
  existence-checked here; the check happens when they're later sent to
  `sale-comparison`.
- **Storage / migration:** two `BIGINT[]` columns
  (`period_a_store_ids`, `period_b_store_ids`) added to `live_comparison_pins`
  via `ADD COLUMN IF NOT EXISTS` in `init_database` — no migration break; pins
  saved before this change read back as `[]`.

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
