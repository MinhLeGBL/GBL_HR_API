# Changelog — custom_reports

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [0.6.0] — 2026-11-05

### Added — the QUERY ENGINE (CR #1)

`POST /api/v1/custom-reports/query` and
`GET /api/v1/custom-reports/dimensions/<dim>`.

Not a report. One engine over three subjects — **sales**, **imports**,
**sellThrough** — driven by a date range, a filter set, and a user-chosen
group-by of up to three dimensions. Roughly a dozen of `scripts/reports/`'s
one-off generators are this same query with the filters and grouping
hardcoded.

> ⚠️ **The specification is in the frontend.** This feature was built
> frontend-first on the user's instruction, so
> `GBL_MASTER_FRONTEND/src/features/custom-reports/mocks/engine.ts` is an
> executable spec that 868 frontend tests run against. The new
> `engine.py` mirrors it function for function — `facts_for` / `factsFor`,
> `value_of` / `valueOf`, `matches`, `accumulate`, `measure_value`,
> `notes_for` — so the two can be held side by side. The request contract
> is in `GBL_MASTER_FRONTEND/docs/cr/custom_reports.md`.

**New files:** `engine.py` (the engine), `EngineQueries` in `queries.py`
(four line-grain pulls). `/sizes` is untouched and keeps working alongside:
its FIFO days-to-sell has no equivalent in the engine.

#### Architecture — one line-grain pull per subject, grouped in Python

SQL does the date bound and the joins; it does **not** do the `GROUP BY`.
Three reasons, in order of weight:

1. **Two dimensions are not SQL-expressible.** `size` is normalised through
   `normalize_size()` (a per-brand Python table) and `customerType` is
   new-vs-returning *relative to the period*. Both would come back raw and
   be re-grouped anyway, so a SQL `GROUP BY` could only ever cover part of
   the vocabulary — and a half-SQL half-pandas grouping is two code paths
   that have to agree.
2. **Line detail needs the lines regardless**, so a SQL grouping engine
   would be a second implementation of the same measures.
3. **The volume does not justify anything cleverer.** A full year of sale
   lines is ~12.8k rows (2024: 16,557 · 2025: 16,125 · 2026-to-date:
   12,804); all 1,041 posted receipts ever are 33,182 lines.

#### Measured against production (read-only, 2026-11-05)

| query | rows | time |
|---|---|---|
| sales, 1 month, grouped by brand | 119 | 2.4s |
| sales, 10 months, grouped by brand | 236 | 5.6s |
| sales, 10 months, three groupings | 2,813 | 5.9s |
| sales, 10 months, **line detail** | 12,802 | 6.2s |
| imports, 10 months | 106 | 2.0s |
| sell-through, 10 months | 106 | 1.7s |

Pickers: 0.4s–3.0s (`customer` is the slowest at 5,585 values).

**Two performance findings, both measured rather than guessed:**

- **The `first_buy` LEFT JOIN cost 26 of 35 seconds.** Folded into
  `SALE_LINES` the pull took 35.10s; without it, 8.93s; the same aggregate
  **standalone takes 1.75s** and returns 5,142 rows. The optimiser re-ran
  the whole-history aggregate per driving row instead of materialising it
  once. It is now `CUSTOMER_FIRST_BUY`, fetched separately and joined in
  Python — **and skipped entirely unless `customerType` is grouped or
  filtered**, which is safe because a dimension that is neither cannot be
  reached by any column, group key or filter test.
- **`arraysize` cost another 4 seconds.** At the driver default a
  12,802-row pull took 8.93s; at 5,000, 5.12s — against 4.91s for a bare
  `COUNT(*)` over the same joins, so transfer is now ~0.2s and what remains
  is the query. Measured through the SSH tunnel from a workstation, so it
  is an upper bound on the deployed figure.

#### ⚠️ Reconciliation with the live-comparison dashboard — it reads LOW, by design

Same period (2026-01-01 → 2026-10-31), engine against
`reports.period_totals`:

| measure | `reports` | engine | delta |
|---|---|---|---|
| net sales | 196,346,486,795 | 195,935,978,458 | **−0.209%** |
| units | 15,274 | 15,246 | −0.183% |
| bills | 5,389 | 5,376 | −0.241% |
| FP revenue | 171,306,499,089 | 170,937,466,584 | −0.215% |

**This is a scope difference, not a defect in either module.** The engine
filters `is_held = 0 AND receipt_type IN (0,1) AND status = 4` (matching
`/sizes` and `commission`); `reports` filters none of the three.

The divergence is small for a reason worth recording: **`INVC_POST_DATE` is
NULL on every status-2 and status-3 document** — all 41,480 such lines — so
any query bounded on that date has already dropped the unposted ones, and
`status = 4` is implied by having a date at all. What is left is
`is_held = 1 AND status = 4` (50 lines in all history) plus
`receipt_type NOT IN (0,1)`: 0.00% of 2023, 0.02% of 2024, 0.12% of 2025,
0.22% of 2026.

Separately, `reports.total_revenue` **nets returns** over a 30-day tail
(189,085,084,811 for the same period) while the engine carries them
alongside, so that figure reads ~3.5% below the engine's `netSales`. The
`noteReturnsNotNetted` note says exactly this to the reader.

#### Returns — the `weekly_report` shape

Carried as `returnValue` / `returnedUnits`, **positive**, never folded into
`netSales` / `units`. The user's call, and a real choice: four conventions
already exist here and none is wrong — `reports` nets revenue but not
units, `commission` and `account_payable` net both, `crm` and
`custom_reports/sizes` exclude outright.

Two reasons the netting options were rejected, the second measured: a
return has no discount rate so it has no FP or MD side, and netting it into
`netSales` while the components stay sale-only puts `fpSales + mdSales` at
**107.1% of the total, on 13 of 14 brands**.

Consequences kept rather than papered over:
- `fpSales + mdSales == netSales` and `fpUnits + mdUnits == units` hold
  **exactly** over the sale-only population (asserted in tests).
- `bills` counts **sale** bills, matching `reports`, so a returns-only
  document shows as a row with `bills = 0` when grouping by bill.
- **Sell-through skips returns outright** — a unit that came back was not
  sold through. Agrees with the shipped `/sizes` (`item_type = 1`).

#### Sell-through — the cohort rule, and the FIFO model behind it

**Both sides are bounded by the period** (the user's rule): the period picks
the receipts, and counts only those receipts' own sales that also fall
inside it. The alternative — a cohort's sales whenever they happened — was
rejected for cross-tab consistency.

⚠️ **Retail Pro carries no link from a sale line to the receipt that
brought the unit in.** The attribution therefore has to be *constructed*,
and this uses FIFO per `item_sid` (oldest open receipt line first), the
same rule `/sizes` uses for days-to-sell. **It is a model, not a fact:**
two receipts of one SKU in the same period cannot be told apart and the
older absorbs the sales. Every grouping the engine offers is coarser than
`item_sid` except `receiving` itself, so a per-brand or per-category rate
is unaffected; grouping **by** `receiving`, or reading line detail, is
where the model shows.

A sale attributable to no in-period receipt is **dropped**, which is what
keeps the rate at or below 100%. And stock landing late in the period has
had almost no time to sell inside it, so a **short period reads low by
construction** — disclosed by `noteSellThroughCohort`.

#### A dimension that does not apply is REJECTED, not ignored

`VOUCHER` / `VOU_ITEM` carries no customer and no discount, so a customer
or FP/MD filter on imports is a *nonsense* query rather than an empty one;
and sell-through is sold ÷ received, so filtering only the numerator
returns a **wrong ratio** rather than a narrower one. Both 400.

#### Fixed on the way through

- **Shoe sizes were being translated as clothing.** `normalize_size()` keys
  its pass-through on `'WSHO'` / `'MSHO'`, but the engine's `department`
  has the gender prefix stripped, so it was handed `'SHO'` and missed:
  `normalize_size('38', 'AKRIS', 'SHO') == 'M'` against
  `normalize_size('38', 'AKRIS', 'WSHO') == '38'`. The pulls now carry the
  raw `dcs_code` alongside the split gender and department.
- **`VOU_ITEM.COST` is a UNIT cost**, verified against
  `INVN_SBS_ITEM.COST` on qty>3 lines. `receivedCost` is `cost × qty`;
  summing `cost` alone understates by 21% (293.4B against 372.6B over all
  receipts).
- **`category` is the THIRD segment of `DESCRIPTION2`, not the last.**
  Last-segment looks better on coverage and is worse: `DESCRIPTION2` is
  sometimes two segments (`OWH-TSH`, `ULJ-DRS`) whose tail is an
  *abbreviation*, so it would file those beside `TSHIRT` and `DRESS` — one
  category in two buckets. Occurrence 3 is NULL on 66 of 15,478 2026 sold
  units (0.43% → the `—` bucket); last-segment is NULL on 4 but splits 63
  units into synonym buckets. An honest unknown beats a plausible
  duplicate.
- **`VOUCHER.ARRIVED_DATE` is NULL on all 1,041 posted receipts**, so the
  receipt date is `POST_DATE`.
- **`CUSTOMER.INFO1` holds the membership tier** (DIAMOND 291 · MEMBER
  1,925 · GOLD 195 · STAFF 1). Not `LTY_LVL_SID`, which is NULL on all
  6,517 customers — `LTY_LEVEL`, `LTY_CUST_LVL_HIST` and
  `LTY_CUST_CENTRAL` are all empty tables.

#### `GET /dimensions/store` — stores with ACTIVITY only

The user's call. `rps.store` has nine rows, but three of them have **zero
sale lines and zero receipt lines in all history**:

| store | sale lines | receipt lines |
|---|---|---|
| HQ | 609 | 33,170 |
| RHN | 14,606 | 0 |
| RWT | 14,557 | 7 *(Dec 2023 setup)* |
| RWD | 9,068 | 5 *(Dec 2023 setup)* |
| RWR | 7,030 | 0 |
| RWP | 2,150 | 0 |
| **LOA / RWL / DEMO** | **0** | **0** |

Offering those three is a filter value that can only ever return an empty
result — the "control no data could ever trigger" shape, and `DEMO` is not
a shop at all.

"Activity" is defined as **the scope this engine reports on**, not mere
existence in a table: a store is offered iff one of the two pulls could
return a row for it. **Either document type counts** — HQ takes
essentially all receiving against only 609 sale lines, so a sales-only
definition would nearly have dropped it.

Unbounded by date, like every other picker: a store that sold nothing this
quarter must still be offered, or the filter cannot reach the rows you are
about to widen the range to find.

Three formulations were measured and all returned the same six stores; the
`UNION` of the two document scopes has the cheapest plan (126ms against
202ms for correlated `EXISTS` on a warm cursor; 1.88s against 2.22s end to
end). ⚠️ It is still **~1.5s dearer than a plain `SELECT DISTINCT`**
(0.4s), because proving activity means touching DOCUMENT and VOUCHER —
paid once when the filter panel opens.

#### Raised back to the frontend

- **`size` is declared `kind: 'multi'`** (a checkbox list) but the picker
  returns **367 values**, including unmapped hybrids like `1/36` and
  `1/UK4.5`. It wants `typeahead`.
- **Grouped-row sort key.** The frontend sorts by `netSales`, which is
  always 0 on imports and sell-through, making that sort a no-op there.
  The engine sorts each subject by its own headline measure instead —
  `receivedCost` for imports, `receivedUnits` for sell-through.
- **No row cap was added.** A year of line detail is ~17k rows, well inside
  what the browser pages; a cap would be a limit with nothing to limit.

## [0.5.0] — 2026-11-05

### Changed
- `/sizes` now groups by **(brand, department, normalized_size)** instead of
  (brand, size, season). Raw `invn_sbs_item.item_size` is not comparable
  across brands — AKRIS `36` is FR 36 (= **S**) while MARNI `36` is IT 36
  (= XS) — so grouping the raw label scattered one physical size across
  several rows. *(Corrected 2026-11-05: this line read "= M". The point
  stands — one label, two sizes — but `RTW_NUMERIC_TO_LETTER['FR']['36']`
  is `'S'`, and `'M'` is FR 38. The same slip is in the frontend's
  `constants.ts`.)*
- `FOCUS_BRANDS` is no longer applied by default. Pass the list explicitly
  for the previous behaviour.

### Added
- `normalize_size(raw_size, brand, department)` — translates a numeric label
  through the brand's sizing system, keeps shoe departments' EU numerics
  as-is, takes the letter half of merchant-prenormalised hybrids (`40/M`,
  `L/XL`), and passes anything unmatched through as its raw value so it
  surfaces as an unmapped bucket rather than landing in a wrong one.
- `is_meaningful_size()` — drops NULL, empty and one-size labels.
- `BRAND_SIZE_SYSTEMS`, `RTW_NUMERIC_TO_LETTER`, `SIZED_DEPARTMENTS`,
  `SHOE_DEPARTMENTS`.

### ⚠️ The mapping is PROVISIONAL — not ratified
Stated by the user when adopting it: the normalisation *"is still in testing
and is not officially recognize yet"*. Two specifics a reader must know
before trusting a row:

- `BRAND_SIZE_SYSTEMS` declares a sizing system for **13 brands**. Every
  other vendor falls through to `DEFAULT_SIZE_SYSTEM = 'IT'`, which is a
  guess rather than a vendor-published chart.
- `RTW_NUMERIC_TO_LETTER` follows modern luxury-fashion industry
  convention — a judgement, not per-brand size charts.

So a normalised size is a useful **grouping**, not a fact about the garment,
and anything built on it must say so WHERE IT IS READ. The frontend does:
the `size` dimension carries `caveatKey: 'caveatSizeNormalised'`, which
surfaces as a note on any result grouped or filtered by size.

### Notes
- Recovered from `stash@{1}`, where it had been living on this machine
  alone — invisible to every other checkout and lost with the clone.

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
