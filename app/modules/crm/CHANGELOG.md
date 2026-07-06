# Changelog — crm

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

The CRM module predates per-module versioning (it shipped incrementally as
CR #40–#53). Version tracking starts here at `1.0.0`; this entry records the
first change made under the tracked scheme.

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
