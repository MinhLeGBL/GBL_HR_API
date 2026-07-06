# Commission — Backend → Frontend Change Requests

Backend-originated changes to the commission API that the **frontend** must action.
See [README.md](./README.md) for how this bidirectional CR system works.

_No pending CRs._

---

# ═══ Completed CRs (Archive) ═══

## CR #B1 — `/commission/calculate` is now DB-authoritative — Done (backend v3.0.0 · frontend v3.4.0)

`POST /commission/calculate` now takes `{ month, year }`; the backend derives the roster
(commission-active employees by **assigned** store), store targets, persisted adjustments,
achievement, and eligibility from the database. A legacy `{ month, year, stores[] }` body is
accepted but **ignored**; cross-store sellers roster under their **home** store (their
out-of-store sales feed the destination store's eligibility revenue + their personal
commission, never a store pool). Removed `POST /store/calculate-v2` and
`POST /personal/calculate` (no callers).

**Frontend response (v3.4.0):** `CalculateRequest` shrunk to `{ month, year }`;
`commissionService.calculate()` posts just the period; the legacy CR #29
`stores[] → employees[] → revenue[]` builder was deleted. Save flow unchanged (adjustments +
targets persisted before calculate). Result table already iterates the backend-returned
`stores[].employees[]`, so home-store rostering renders automatically (QA once backend deploys).
