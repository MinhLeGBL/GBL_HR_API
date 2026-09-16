# Periodic Report — Backend → Frontend CR

**Backend module:** `app/modules/periodic_report/` v0.1.0
**Branch:** `feature/periodic-report` (both repos)
**Base URL:** `/api/v1/periodic-report`

The weekly sales report became a real module. This is the contract for the
preview-and-approve page.

## The weekly cycle

```
Mon 03:00  systemd timer generates the week that just closed →
           a run is stored with status `pending_approval`
Mon a.m.   the user opens the page, checks the figures, reads/edits the email
           draft, and presses Approve
on approve the run is queued; a dispatcher timer sends it (immediately, or at
           the configured weekday/time) with the .xlsx attached
```

## Permissions — two sections

| Section | Grants |
|---|---|
| `PERIODIC_REPORT` | see the page, the figures, the draft |
| `PERIODIC_REPORT_APPROVE` | edit the draft, approve, manage recipients and settings |

Both are enforced **server-side** (403 without them), not just hidden in the nav.
Seeded: `PERIODIC_REPORT` → ADMIN + MANAGER, `PERIODIC_REPORT_APPROVE` → ADMIN.

## Endpoints

All require `Authorization: Bearer <token>`. All responses use the usual
`{success, ...}` envelope; failures add `code`.

| Method | Path | Section | Purpose |
|---|---|---|---|
| GET | `/runs/latest` | view | The most recent run. `{run: null}` (200, not 404) before the first ever run. |
| GET | `/runs?limit=26` | view | Recent runs, newest first, **without** payloads — feeds the week selector. |
| GET | `/runs/<id>` | view | One run in full, including `payload` and the email draft. |
| GET | `/runs/<id>/workbook` | view | The stored `.xlsx` (binary attachment download). |
| PUT | `/runs/<id>/email` | approve | Save draft edits. Body `{subject, body}`. Only while `pending_approval`. |
| POST | `/runs/<id>/approve` | approve | Approve + queue. Body `{send_mode?: 'immediate'\|'scheduled'}`. |
| POST | `/runs/<id>/retry` | approve | Re-run a generation that FAILED (figures + analysis). Refused on a healthy run. |
| GET/PUT | `/recipients` | view / approve | The to/cc/bcc list. PUT replaces it wholesale. |
| GET/PUT | `/settings` | view / approve | Send mode, weekday/time, templates, commentary toggle. |

### Error codes → HTTP

`INVALID_INPUT` 400 · `NOT_FOUND` 404 · `INVALID_STATE` / `ALREADY_SENT` /
`NO_RECIPIENTS` 409 · `COMMENTARY_FAILED` 502 · `SERVER_ERROR` 500

### Run statuses

`pending_approval` → `approved` → `sending` → `sent`, plus `failed` (generation
failed — `error` says why) . A failed send returns to `approved` and retries.

## The run object

```jsonc
{
  "id": 1,
  "as_of": "2026-09-14",          // the Monday the job ran
  "through_date": "2026-09-13",   // last day MTD/YTD include — the closed Sunday
  "week_start": "monday",
  "status": "pending_approval",
  "email_subject": "Weekly Sales Report — Week 37 2026",
  "email_body": "...",
  "error": null,
  "commentary_error": null,   // why the analysis is missing, when it is
  "generated_at": "2026-09-16T12:55:01+07:00",
  "approved_by": null, "approved_at": null,
  "scheduled_send_at": null, "sent_at": null,
  "payload": { /* see below */ }
}
```

`workbook` bytes are never included in JSON — use the download endpoint.

Every run (single and list) also carries the week it reports on:

```jsonc
"week_from": "2026-09-07", "week_to": "2026-09-13", "week_label": "Week 37 2026"
```

Derived server-side on purpose — re-implementing "which week does this as-of date
report on?" in TypeScript would be a second place for the last-complete-week rule
to drift from `period.py`.

`GET /runs/<id>` additionally carries `sends` — every send attempt for that run:

```jsonc
"sends": [
  { "id": 1, "recipients": ["board@example.com"], "success": true,
    "detail": "sent via mail.example.com:587 to 1 recipient(s)",
    "sent_at": "2026-09-14T09:00:00+07:00" }
]
```

**Show this, not the current recipient list, for a report already sent.** The
recipient list changes over time, so rendering today's addresses beside an old
report claims people received something they never did. Empty until sent; a
failed attempt appears with `success: false` and stays visible.

## The payload (the figures to preview)

```jsonc
{
  "as_of": "2026-09-14",
  "through": "2026-09-13",
  "week_start": "monday",
  "store_names": { "RWT": "RUNWAY TAKASHIMAYA", "...": "..." },
  "metrics": [ { "key": "total_sales", "unit": "money", "lower_is_better": false }, ... ],
  "periods": {
    "WOW": {
      "label": "Week by Week",
      "current": { "from": "2026-09-07", "to": "2026-09-13", "label": "Week 37 2026" },
      "prior":   { "from": "2026-08-31", "to": "2026-09-06", "label": "Week 36 2026" },
      "total":   { "current": {...}, "prior": {...}, "change": {...} },
      "stores": [
        { "store": "RWT",
          "current": {...}, "prior": {...}, "change": {...},
          "departments": [ { "department": "RTW", "current": {...}, "prior": {...}, "change": {...} } ] }
      ]
    },
    "MTD": { ... }, "YTD": { ... }
  }
}
```

`current` / `prior` are `{metric_key: number|null}`. `change` is
`{metric_key: {value, kind, unfavourable}}`.

### Reading `change` — please don't re-derive it

- `kind` is `"pct"` (a percentage change) or `"pp"` (**percentage POINTS**).
  `avg_discount_pct` is always `pp`: 26.5% against 26.4% is +0.1pp, and
  rendering that as "+0.4%" invites the reader to compare it against the sales
  change, which means something different.
- `value` is `null` when the prior side is zero — a percentage change from zero
  is undefined. Render a dash, not "∞" or "100%".
- **`unfavourable` is the only thing that should drive red.** Polarity is per
  metric, not per sign: `avg_discount_pct` and `returns_value` are
  lower-is-better, so a FALL in either is a win. Colouring on `value < 0` paints
  a shrinking discount rate red, which is backwards.

### Units

Money is **whole VND** in the payload (the `.xlsx` displays millions; the JSON
does not). `avg_discount_pct` is already a percentage (26.5 = 26.5%). Metrics:
`total_sales`, `qty_sold`, `bills`, `avg_unit_price`, `avg_transaction_value`,
`avg_discount_pct`, `returns_value`.

### Caveat to surface in the UI

**Department bill counts do not sum to the store total.** One bill can span
departments, so a department's `bills` means "bills containing this department".
Store and grand totals come from a separate document-grain query and are
authoritative. The same applies to `avg_transaction_value` on department rows.
Please don't render a "sum of departments" check that will always look wrong.

## Generation is not exposed — with one narrow exception

The analysis costs money and is written exactly once per report, by the Monday
03:00 job. Please do not add a general "regenerate" button: it would spend
tokens on every press and buy nothing, because a sale posted late carries the
later post date and falls into the NEXT week's window.

`POST /runs/<id>/retry` is the exception — it re-runs a generation that FAILED,
and is refused on a healthy run. Surface it ONLY when `commentary_error` is set
or `status` is `failed`, and tell the user it costs an API call.

## Notes

- Figures are a **snapshot** taken at generation, never recomputed on read —
  Oracle is live, so a recompute would mean the numbers approved are not the
  numbers emailed. Treat the payload as immutable for a given run.
- The preview does **not** need to reproduce the Excel formatting (agreed with
  the user 2026-09-16). Plain data tables; the sheet layout is fixed and only
  the data changes.
- `payload` is large (~110KB for 6 stores). `GET /runs` omits it deliberately.
