# Changelog — account_payable

All notable changes to this module. Versioning per
[CLAUDE.md → Branch & Version Conventions](../../../CLAUDE.md).

## [0.1.0] — 2026-05-29

Initial scaffold for CR #60. Pre-1.0 — endpoints exist but return 501.

### Added
- Module skeleton: `__init__.py` / `routes.py` / `service.py` /
  `queries.py`.
- 7 endpoint stubs under `/api/v1/account-payable/*`:
  - `GET  /employees`
  - `GET  /employees/:employee_code/bills`
  - `GET  /bills?search=&status=`
  - `GET  /bills/:bill_sid`
  - `GET  /payments?status=`
  - `POST /reconcile`
  - `POST /payments/:payment_doc_sid/unmatch`
- `AccountPayableService.init_database()` creates the
  `payable_reconciliations` Postgres table + indexes idempotently.
- Blueprint registered in `app/main.py`.

### Status
All read/write methods currently raise `NotImplementedError` and the
routes return 501. The frontend on `feature/account-payable` ships with
mocks (`VITE_PAYABLE_MOCK=true`) so UX can be reviewed before backend
implementation begins.

### Related
- CR #60 — full spec in
  `GBL_HR_Frontend/docs/cr/account-payable.md`.
- Contract types — `GBL_HR_Frontend/src/features/account-payable/types.ts`
  is the source of truth for request/response shapes.
- Builds on CR #59 Phase B (commission v2.0.0) — reuses the
  Charge-tender ledger replay from
  `CommissionRepository.get_unpaid_bill_amounts`. Once the linkage table
  is populated, commission's `released_by_category` will be wired up at
  query time.
