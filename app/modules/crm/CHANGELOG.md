# Changelog — crm

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Baseline version — formal version tracking begins. Covers existing
functionality:

- RFM segmentation pipeline (luxury hybrid, 7 segments) with daily
  recompute from Oracle
- Customer list (`GET /api/v1/crm/customers`)
- Segment summaries and monthly trend snapshots
  (`GET /api/v1/crm/segments/summary`, `/segments/trends`)
- 5×5 (R, F) heatmap (`GET /api/v1/crm/heatmap`)
- Product analysis by segment (`GET /api/v1/crm/product-analysis`)
  with pooled R/F/M and compound ranking
- Customer drilldown (`GET /api/v1/crm/customers/:sid/drilldown`)
- Brand drilldown (`GET /api/v1/crm/brand-drilldown`) with segment-scoped
  KPIs and season/category distributions
- DB-configurable RFM weights via `crm_config` (admin GET/PUT)
- Customer SIDs serialized as strings (JS precision safety)

Historical CRs for this module are tracked in
`GBL_HR_Frontend/docs/cr/crm.md` (CRs #40 through #55).
