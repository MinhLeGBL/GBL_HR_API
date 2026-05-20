# Changelog — bathroom

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Baseline version — formal version tracking begins. Covers existing
functionality:

- Product catalog for Dolomite, Valsir, Paffoni
  (`GET /api/v1/bathroom/products`)
- Brand settings + tax rates (`GET/PUT /api/v1/bathroom/brand-settings`)
- Excel/CSV import pipelines for brand and price updates

Historical CRs for this module are tracked in
`GBL_HR_Frontend/docs/cr/bathroom-price-check.md` (CR #36, #37, #38, #39).
