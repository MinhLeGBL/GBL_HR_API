# Changelog — crm

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Baseline — formal `__version__` tracking begins. Reflects the cumulative
result of CRs #40 through #55 (full specs in
`GBL_HR_Frontend/docs/cr/crm.md`).

### Endpoints
- `GET /api/v1/crm/customers` — all customers with RFM scores + segment
- `GET /api/v1/crm/segments/summary` — aggregate stats per segment
- `GET /api/v1/crm/segments/trends` — monthly segment counts over N months
- `GET /api/v1/crm/heatmap` — 5×5 (R, F) grid with count + monetary
- `GET /api/v1/crm/product-analysis` — ranked brand/category list per segment
- `GET /api/v1/crm/customers/:sid/drilldown` — per-customer KPIs + distributions
- `GET /api/v1/crm/brand-drilldown` — per-brand KPIs + season/category distributions
- `GET/PUT /api/v1/crm/admin/config` — RFM weights (admin)
- `POST /api/v1/crm/admin/recompute` — trigger full recompute (admin)

### Pre-versioning history (chronological, newest first)

- **2026-04-29** — CR #55: Bug fix — serialize customer SIDs as strings
  (JS Number precision loss for 18-digit IDs).
- **2026-04-29** — CR #54: Brand drilldown endpoint — per-brand KPIs +
  season/category distributions (segment vs all).
- **2026-04-29** — CR #53: Customer drilldown endpoint — per-customer KPIs,
  brand/category/FP-MD distributions.
- **2026-04-29** — CR #52: Separate RFM weights for product analysis
  (`pw_*` in `crm_config`).
- **2026-04-29** — CR #51: Compound score (geometric mean of
  `weighted_score × c_score`) for product analysis ranking.
- **2026-04-29** — CR #50: Add `customer_count` field to product analysis
  rows.
- **2026-04-29** — CR #49: Product analysis switched from pooled to nested
  per-customer averages.
- **2026-04-23** — CR #48: Product Analysis by Segment endpoint —
  ranks brands/categories per segment with pooled R/F/M + weighted score.
- **2026-04-20** — CR #47: Bug fix — PUT `/crm/admin/config` 500 from
  missing table; auto-create `crm_config` if missing.
- **2026-04-20** — CR #46: DB-configurable RFM weights — `crm_config`
  table + admin GET/PUT endpoints (no restart needed).
- **2026-04-20** — CR #45: Tune RFM weights to (0.3 / 0.3 / 0.4) + add
  `engagement_score`. 34 customers moved Loyalist → Core.
- **2026-04-17** — CR #43: Investigation — empty F5×R1/R2 heatmap cells
  are data-correct (F5 group is small, only 7 churned heavy buyers).
- **2026-04-17** — CR #42: Top brand/category use composite affinity score
  (0.6 × freq + 0.4 × monetary); C_NAME for categories.
- **2026-04-17** — CR #41: Backfill segment trend snapshots — 11 months
  retroactively (May 2025 – Mar 2026), parameterized `as_of_date` queries.
- **2026-04-17** — CR #40: RFM calculation pipeline & API — luxury hybrid
  approach with 7 segments, 4 endpoints + admin recompute.
