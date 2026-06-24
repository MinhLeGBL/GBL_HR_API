# Backend → Frontend Change Requests

This directory is the **backend's** change-request store. It holds changes the
**backend** has made (or needs) that the **frontend** (`GBL_HR_Frontend`) must act on —
new/changed/removed endpoints, request/response shape changes, and breaking changes.

It is the mirror of the frontend's `GBL_HR_Frontend/docs/cr/`, which holds
**frontend → backend** requests. Together they form a bidirectional CR system:

| Direction | Lives in | Authored with | Read by | Responded in |
|-----------|----------|---------------|---------|--------------|
| frontend → backend | `GBL_HR_Frontend/docs/cr/` | frontend `/updatecr` | backend `/checkcr` | backend `/responsecr` (writes back into the frontend doc) |
| backend → frontend | `GBL_HR_API/docs/cr/` (here) | backend `/updatecr` | frontend `/checkcr` | frontend `/responsecr` (writes back into this doc) |

**Rule of thumb:** each repo owns the CR doc for requests it *receives the response to*.
A request and its response live in the **same** file — the requester's file. `/checkcr`
and `/responsecr` therefore both operate on the *other* repo's `docs/cr/`.

## File structure

Per-feature files mirror the frontend's naming, split by a `═══` separator:

```
[Pending CRs — active specs, ⏳ items the frontend must action]

# ═══ Completed CRs (Archive) ═══

[Completed CR summaries — frontend confirmed + archived]
```

Status markers: ⏳ = pending frontend action · Done = frontend confirmed.

### Feature-to-file mapping (by frontend branch)

| Branch pattern | CR file |
|----------------|---------|
| `feature/commission*` | `cr/commission.md` |
| `feature/employee*` | `cr/employees.md` |
| `feature/permission*` | `cr/permissions.md` |
| `feature/user*` or `feature/auth*` | `cr/users.md` |
| `feature/bathroom*` | `cr/bathroom-price-check.md` |
| `feature/account-payable*` | `cr/account-payable.md` |
| `feature/crm*` | `cr/crm.md` |

## Change Log

| CR | Date | Description | Status |
|----|------|-------------|--------|
| #B1 | 2026-06-23 | Commission: `POST /commission/calculate` is now DB-authoritative — body `{month, year}`, legacy `stores[]` ignored; roster + 30% equal-share divisor derived from the assignment roster (commission-active only); cross-store sellers rostered under home store. Removed `POST /store/calculate-v2` + `POST /personal/calculate` | Done (backend v3.0.0 · frontend v3.4.0) |
