# Tracking System — Changelog

## 1.0.0 — 2026-11-19

New module: the employee-number gate for the hidden tracking-system page.

- `POST /api/v1/tracking-system/unlock` — body `{"code": "<employee number>"}`.
  Succeeds only when the code is the caller's OWN `employees.employee_code` and
  that code is in `tracking_system_access`. Returns `{unlock_token, expires_at}`
  (8 h). Every refusal is the same `403 INVALID_CODE` with the same message.
  5 failed attempts per user in 15 min → `429 RATE_LIMITED`.
- `GET /api/v1/tracking-system/session` — header `X-Unlock-Token`; `200` while
  the unlock is valid, `403 LOCKED` otherwise. The allowlist is re-checked on
  every gated request, so deleting a row revokes immediately.
- `unlock_required` decorator for the page's future data endpoints.
- Tables (via `init_database`, registered in `scripts/database/init_db.py`):
  `tracking_system_access` (the allowlist, edited by hand with SQL — no UI by
  design) and `tracking_system_unlock_attempts` (every attempt, good or bad).

Tracked data (same release), all behind `token_required` + `unlock_required`:
- **In** — one row per item. `GET /in`, `GET /in/search?q=`, `POST /in`
  `{items:[{item_sid, qty, entered_unit_cost}]}`, `DELETE /in/<id>`.
  Tracking cost = (entered − landed) × qty. **Landed cost = `INVN_SBS_ITEM.UDF1_STRING`**
  (free text: numeric on ~93% of items, about 14% above COST; junk such as '  -  ' or
  'undefined' on a few hundred, and those items are refused).
- **Out** — one row per bill line. `GET /out`, `GET /out/search?q=`, `POST /out`
  `{lines:[{line_id, entered_revenue}]}`, `DELETE /out/<id>`. Tracking revenue =
  entered − Oracle net line revenue ex-VAT (same `_NET_LINE` as custom_reports).
  Returns (`item_type=2`) are negated.
- Search matches a UPC by prefix or an ALU by substring (2–50 chars). The Out search
  also matches a **bill number**: 'RWP-556', 'rwp 556' or 'RWP556' match that store's
  bill, and a bare '556' matches 556 in every store, because DOC_NO repeats per store.
  Digits are tried as both a bill number and a UPC prefix; results are merged, with
  bill matches first. Lines are found
  in two steps, items first then their lines (0.5–1 s vs 3.8 s for a single join).
- Item info, landed cost and Oracle revenue are **snapshotted at import**. Re-importing
  the same item or line updates its row. An import is all-or-nothing, with per-row errors.
- **Oracle SIDs travel as strings.** They have 18 digits, beyond 2**53, so a JS Number
  would round them.
- **Excel import for In**: `POST /in/lookup` `{upcs:[...]}` resolves up to 1000 UPCs
  in one batch. It returns each UPC's matches: 0, 1, or several (3 UPCs are on two items
  locally, and the caller must not guess between them). UPC is a NUMBER, so it is bound as
  a number: 0.15 s per 500, against 1.24 s via TO_CHAR.
- `import_in` now reads Oracle in chunked batches (`ORACLE_IN_CHUNK` = 500), not one query
  per item: 300 items take ~1 s. `MAX_IMPORT_ROWS` went from 200 to 1000. Two rows for
  the same item are rejected ("Same item as row N").
- **In rows are per item AND year.** `tracking_in_items.year` (SMALLINT, NULL = the cost
  applies to all units ever received). The unique key is now
  `(item_sid, COALESCE(year, 0))`: the COALESCE makes NULL count as one value, so an
  item can't have two all-time rows. `init_database` upgrades an item-only table in
  place (ADD COLUMN, DROP the old constraint, CREATE the index). `import_in` takes an
  optional `year` (2000–2100); duplicates are checked per item+year.
- `POST /in/lookup` now also returns `received_by_year` for each match, e.g.
  `{"2025": 3, "2026": 1}`: posted-receipt qty per calendar year of `POST_DATE`
  (`RECEIVED_BY_YEAR`).
- Tables `tracking_in_items` (unique `item_sid`) and `tracking_out_lines` (unique `line_id`).

- `X-Unlock-Token` added to the CORS `allow_headers` in `app/main.py`. Without it the
  browser's preflight drops the header and every gated call fails in the browser,
  while curl and the unit tests pass. `TestCors` guards it.

⚠ `deploy.yml` does not run `init_db.py`. Run it on the server after deploying,
then add allowed codes with `INSERT INTO tracking_system_access ...`.
