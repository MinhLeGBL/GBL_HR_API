# Commission — Backend → Frontend Change Requests

Backend-originated changes to the commission API that the **frontend** must action.
See [README.md](./README.md) for how this bidirectional CR system works.

---

## CR #B1 — `/commission/calculate` is now DB-authoritative (2026-06-23, backend v3.0.0) ⏳ — Frontend Done (v3.4.0)

The calculate endpoint no longer consumes the frontend-built roster/revenue payload.
The backend is now the single source of truth: it derives the roster, store targets,
persisted revenue adjustments, achievement, and eligibility from the database.

**Why:** the old CR #29 flow built each store's roster from the frontend payload, which is
assembled from the sale-location-grouped store-view. A cross-store seller (home store ≠
sale store) rode into the destination store's roster and inflated its headcount, diluting
the 30% equal-share. Verified on RWT May 2026: shared share `402,832` (÷13) vs correct
`436,402` (÷12).

### Endpoint change

**POST** `/api/v1/commission/calculate` — Admin/Manager

**New request body:**
```json
{ "month": 5, "year": 2026 }
```

- The roster = commission-**active** employees by **assigned** store (override → status-history
  → default). Cross-store sellers are rostered under their **home** store; their out-of-store
  sales feed the destination store's eligibility revenue and their own personal commission,
  but never any store's commission pool.
- Revenue adjustments and store targets are read from the database. **Save them first** via
  `PUT /commission/revenue/adjustments/<employee_code>` and `PUT /commission/stores/<store_code>`
  before calling calculate.

> ⚠️ **Breaking vs CR #29.** A legacy `{ month, year, stores[] }` body is still accepted but the
> `stores[]` array is **ignored**. The response shape (`stores[] → employees[] → result{}`) is
> unchanged, but results now group a cross-store seller under their **home** store, not where
> they sold.

### Frontend action items

1. **Done (frontend v3.4.0)** — `CalculateRequest` shrunk to `{ month, year }` only.
   `commissionService.calculate()` posts just the period; the legacy CR #29
   `stores[] → employees[] → revenue[]` builder in `CommissionPage.handleApplyAdjustments`
   was deleted (~80 lines). Tests updated.
2. **Done** — save flow unchanged and verified. `handleApplyAdjustments` Step 1 still POSTs
   all pending adjustment edits via `commissionService.saveAdjustments(...)` before calculate
   fires; store targets are persisted on edit (via `ImportTab` and
   `CommissionStoreSettingsDialog` calling `updateStoreSettings`). By calculate time, both
   are in the DB.
3. **No code change needed for cross-store seller display** — the result table iterates
   `calcResult.stores[].employees[]` as returned by the backend. With the new home-store
   rostering, a cross-store seller will appear under their home store automatically; the
   UI doesn't assume sale-location grouping in result rendering. Will QA live once the
   backend is deployed.
4. **Done** — `docs/changes/commission.md` calculate spec updated to describe the new
   `{ month, year }` request; old CR #29 fat-payload description replaced. CR #B1 added
   to the change log (frontend v3.4.0, backend v3.0.0). Frontend's `docs/cr/commission.md`
   does not exist (no outgoing requests).

### Removed endpoints

- `POST /api/v1/commission/store/calculate-v2`
- `POST /api/v1/commission/personal/calculate`

Both were unused by the frontend. The standalone per-store / per-employee calculation is now
only available through the consolidated `/commission/calculate`.

---

# ═══ Completed CRs (Archive) ═══
