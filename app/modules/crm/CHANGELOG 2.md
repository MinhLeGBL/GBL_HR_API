# Changelog — crm

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

The CRM module predates per-module versioning (it shipped incrementally as
CR #40–#53). Version tracking starts here at `1.0.0`; this entry records the
first change made under the tracked scheme.

## [1.2.0] — 2026-07-16

### Fixed — revenue double-applied the item discount (RFM monetary undercounted)

Same defect found and fixed in the `reports` module (v1.3.1): the net-revenue
expression was `(PRICE − TAX) × (1 − item_disc) × (1 − doc_disc)`, but `di.PRICE`
is **already the discounted unit selling price** (`ORIG_PRICE × (1 −
di.DISC_PERC/100) == di.PRICE` on every live line). Re-applying `di.DISC_PERC`
**double-discounted** every revenue figure, and — because `di.PRICE` is a **unit**
price — the missing `× QTY` further undercounted multi-unit lines.

**Corrected** net revenue (ex-tax), now a single shared fragment `_NET_REVENUE`:
`(di.PRICE − NVL(di.TAX_AMT,0)) × NVL(di.QTY,0) × (1 − NVL(d.DISC_PERC,0)/100)`.
Applied to every revenue SUM across the module — `monetary`, `fp_revenue` /
`discounted_revenue`, the brand/category aggregates, and all customer/brand
drilldown revenues. Verified live: a top customer's `monetary` rose from
15.29B → 17.26B (query result == manual net-revenue formula, exact).

**`di.DISC_PERC` is still used** — legitimately — in the Full-Price vs Markdown
*classification* (`_COMBINED_DISC`, the combined item×doc discount **rate**, not
revenue). That logic is unchanged; only revenue sums were corrected.

**Response shape is unchanged** (same fields, corrected values) — pure backend
fix, no frontend CR. **Impact to be aware of:** every customer's `monetary`
increases (by a customer-specific factor that depends on their discount mix and
multi-unit purchases), so **RFM monetary quintiles and the resulting segments
will shift** on the next recompute. The FP/MD revenue split also rises with the
same correction.

> **Known pre-existing nit (not introduced here):** `fp_revenue` and
> `discounted_revenue` are each `ROUND()`ed independently of `monetary`, so
> `fp + md` can differ from `monetary` by ±1 VND. Structure is unchanged by this
> fix; left as-is.

## [1.1.0] — 2026-07-07

### Added — CR #77 (frontend): per-customer FP/MD split on `GET /crm/customers`

Continuation of CR #76. The FP-vs-MD-by-segment chart is gaining a
click-to-drilldown that lists every customer in a segment with their FP/MD
revenue and units. Fetching that via N `GET /crm/customers/<sid>` calls is
prohibitive (a segment can hold 200+ customers), so the **customer list**
endpoint now carries the split inline.

#### `GET /crm/customers` — four new fields per `customers[]` element

```jsonc
{
  "id": "...", "name": "...", "monetary": 12500000, "segment": "VIC",
  "total_full_price": 10800000,   // FP revenue (VND)
  "total_discounted":  1700000,   // MD revenue (VND)
  "count_full_price":       12,   // FP units sold
  "count_discounted":        3    // MD units sold
}
```

Names match `SegmentSummary` (`total_*` / `count_*`), **not** the drilldown's
`full_price_units` / `discounted_units` — matching the frontend request.
Invariants per customer: `total_full_price + total_discounted == monetary`
(to per-customer VND rounding, per [1.0.0]); `count_full_price +
count_discounted == total units bought` (exact).

#### No new storage / migration

Pure surfacing change — reads the `fp_revenue` / `discounted_revenue` /
`fp_units` / `discounted_units` columns added in [1.0.0]. Service coalesces
missing values to `0`, so a legacy score row (before the first recompute)
returns zeros rather than erroring.

## [1.0.0] — 2026-07-06

### Added — CR #76 (frontend): FP vs MD split per segment + per customer

The CRM dashboard's new "FP vs MD by segment" chart needed an aggregate
full-price / markdown split per RFM segment, which previously only existed
(proportionally split) inside the per-customer drilldown.

#### FP/MD classification rule (binary, per line item)

An item line is **Full Price** when its **combined** discount rate — item-level
`DISC_PERC` and document-level `DISC_PERC` together,
`1 − (1 − item_disc) × (1 − doc_disc)` — is **≤ 30%**, and **Markdown** when
**> 30%**. Each line's *entire* net revenue and unit count lands in exactly one
bucket:

- `total_full_price + total_discounted == revenue` — up to per-customer VND
  rounding. `monetary`, `fp_revenue`, and `discounted_revenue` are each
  independently `ROUND()`ed, so the two aggregates can differ by a few VND
  across the population (observed: single digits on tens of billions —
  immaterial for the ratio chart the frontend renders).
- `count_full_price + count_discounted == total units sold` — exact (integer
  `QTY`, no rounding).

Only `ITEM_TYPE = 1` lines participate (suitcase / gift-certificate / service
items excluded), matching the existing revenue window (rolling 24 months).

This **replaces** the pre-CR#76 per-line *proportional* split that the customer
drilldown used, so the drilldown and the segment dashboard now agree.

#### `GET /crm/segments/summary` — four new fields per segment

- `total_full_price` (int VND) — FP revenue summed across the segment
- `total_discounted` (int VND) — MD revenue
- `count_full_price` (int) — FP **units sold** (`SUM(QTY)`)
- `count_discounted` (int) — MD units sold

Zero-count segments return all four as `0`.

#### `GET /crm/customers/<sid>` drilldown — `price_distribution` extended

Now carries units alongside revenue (additive, backward-compatible):

```jsonc
"price_distribution": {
  "full_price":       10100000000,   // FP revenue (VND)
  "discounted":        2400000000,   // MD revenue (VND)
  "full_price_units":        3400,   // NEW — FP units sold
  "discounted_units":         900    // NEW — MD units sold
}
```

#### Storage

Four columns added to `crm_customer_scores` (`fp_revenue`,
`discounted_revenue`, `fp_units`, `discounted_units`), populated by the
`crm_recompute` job from the Oracle aggregate query. Added idempotently
(`ADD COLUMN IF NOT EXISTS`); values are `0` on legacy rows until the next
recompute runs.

**Deviation from the CR request:** the CR suggested classifying FP/MD via the
commission pipeline's `fp_or_md` / Retail Pro `PRICE_LEVEL` heuristic on
`payable_bill_items`. CRM does not use that table and its revenue is computed
from `DOCUMENT_ITEM` discount rates, so we implemented the binary ≤30%
combined-discount rule (confirmed with the product owner) instead — consistent
with how CRM already measures revenue.
