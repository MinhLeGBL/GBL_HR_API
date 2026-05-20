# Changelog — bathroom

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [1.0.0] — 2026-05-20

Baseline — formal `__version__` tracking begins. Reflects the cumulative
result of CRs #36 through #39 (full specs in
`GBL_HR_Frontend/docs/cr/bathroom-price-check.md`).

### Endpoints
- `GET /api/v1/bathroom/products` — product catalog (~7,834 items) with
  `?brand=` / `?search=` / `?page=` / `?per_page=` filters
- `GET /api/v1/bathroom/brand-settings` — per-brand pricing parameters +
  tax rates
- `PUT /api/v1/bathroom/brand-settings` — Admin/Manager UPSERT

### Pre-versioning history (chronological, newest first)

- **2026-04-14** — CR #39: Pagination support for `GET /products`
  (backward-compatible; omit `page` to fetch all).
- **2026-04-14** — CR #38: Brand name casing standardized — products
  endpoint returns title-case (`"Paffoni"`).
- **2026-04-10** — CR #37: `PUT /brand-settings` — Admin/Manager. Sends
  `brands` array + `tax_rates` object; UPSERTs in single transaction;
  validates rates 0–1.
- **2026-04-10** — CR #36: Initial product catalog & brand settings
  GET endpoints. Tables: `bathroom_products`, `bathroom_brand_settings`,
  `bathroom_tax_rates`.
