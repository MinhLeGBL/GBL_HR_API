# Weekly Report — Changelog

## 0.1.0 — 2026-09-16

Phase 2. Turns the standalone weekly Excel generator into a real module so the
report can be previewed, approved and emailed from the app.

### Added
- `period.py`, `queries.py`, `aggregate.py` — the aggregation logic lifted out
  of `scripts/reports/generate_periodic_report.py`. Reconciled against live
  Oracle to 1e-16 relative in Phase 1; moved verbatim.
- `excel.py` — the workbook renderer, also lifted from the script. The script
  now imports it, so the downloaded file, the emailed attachment and the CLI
  output are the same bytes and the layout cannot drift between them.
- `period_windows(..., through=...)` — ends the MTD and YTD windows on a day
  earlier than `as_of`. The scheduled Monday 03:00 run stops both on the Sunday
  that closes the reported week: at 03:00 the current Monday carries almost no
  sales while the prior-year side carries a full trading day, which understates
  MTD and YTD (severely in the first days of a month). The month and year are
  taken from `through`, so a run on the 1st reports the previous month in full.
- `service.py` — snapshot generation, email drafting, approval and dispatch
  state. Figures are SNAPSHOT at generation, never recomputed on read: Oracle is
  live, so a recompute would mean the numbers approved are not the numbers sent.
- `commentary.py` — the LLM-written analysis for the email. Best-effort; a
  failure leaves the placeholder empty rather than failing the run.
- Four tables: `periodic_report_runs`, `_recipients`, `_settings`, `_sends`.
  The .xlsx is stored in `runs.workbook` rather than on disk because
  `deploy.yml` runs `rm -rf document/` on the server.
- `section_required()` in `app/core/auth/middleware.py`, and two sections:
  `WEEKLY_REPORT` (view) and `WEEKLY_REPORT` (edit, approve,
  configure recipients).


## 0.2.0 — 2026-09-16

### Changed
- **The email template is now the company's own Vietnamese wording.** The fixed
  text (greeting, the sentence naming the three parts, the unit declaration, the
  sign-off) is template; only the numbered analysis is written by the model.
- New placeholders for that wording: `{{week_no}}`, `{{week_year}}`,
  `{{prior_week_no}}`, `{{month_no}}`, `{{mtd_range}}`, `{{ytd_range}}`, plus
  `{{week_range}}` now rendered day-first (`07/09–13/09`). `{{month_no}}` comes
  from the MTD window rather than `as_of`, so a run on Monday the 1st names the
  month that just ended instead of the new one.
- `commentary.py` rewritten to produce the three-part analysis in Vietnamese,
  in the house style: decompose a revenue move into its drivers, label
  "Điểm tích cực:" / "Cần lưu ý:", locate a problem in time by comparing the
  week against the month, and name store outliers.

### Added
- **Pre-computed derived facts.** Every cross-period ratio the house style
  quotes — a week's share of the month's returns, the gap between the week's and
  the month's discount rate — is calculated in Python and handed to the model,
  which is told not to compute anything new. An LLM dividing 375 by 489 in prose
  is usually right and occasionally not, and this email goes to the board.
- **Notable-movement detection** for the "fluctuations to watch" the user asked
  for: a store is named when its discount rate moves ≥ 5 percentage points or
  its sales move ≥ 15%. Thresholds rather than a forced top-N, so a quiet week
  flags nothing instead of manufacturing significance.
- `anthropic~=0.69.0` in `requirements.txt`. Optional at runtime — with no
  `ANTHROPIC_API_KEY` the report still generates and sends, with the analysis
  paragraph empty.

### Fixed
- `build_prompt` tolerates a partial payload instead of raising, so a run
  missing one period block still produces analysis of the blocks it has.


## 0.2.1 — 2026-09-16

### Fixed
- **Markdown in a plain-text email.** The first live generation wrapped its
  section headings in `**bold**`, which renders literally in a plain-text body.
  The system prompt now forbids Markdown explicitly, and `strip_markdown()`
  removes `**bold**` and leading `#` headings as a backstop. Deliberately
  narrow — a lone `*` is left alone, since a broader strip would risk mangling
  punctuation in Vietnamese prose.

### Verified against live data (2026-09-16)
First real run on Week 37 2026: 694 words, ~50s, on `claude-opus-5`.
**Every one of 11 spot-checked figures reconciled exactly to the payload** —
store-level sales, bill-count changes, YTD rates and the prior-year HQ
comparison. The model used the pre-computed 76.6% returns-concentration figure
rather than dividing 375 by 489 itself, which is what the arithmetic policy is
for. The analysis reproduced every move of the house style: driver
decomposition, "Điểm tích cực:" / "Cần lưu ý:", locating the heavy discounting
in the early month from the week-vs-month gap, and the section-3 observation
that the year grew on bigger baskets while September reversed to more, smaller
ones.


## 0.3.0 — 2026-09-16

The email is now **HTML**, following the company's own formatting (see the
screenshot at `document/template/`): bold section headings, bullet points, and
bold on each claim and its figure — with supporting numbers in parentheses left
plain.

### Changed
- **Reversed the 0.2.1 Markdown strip.** That fix treated `**bold**` as an
  artifact; it is the intended format. The email had to become HTML for bold
  and bullets to mean anything, which is what this release does.
- `commentary.py` now asks for 2-4 bullets per section with bold on the claim
  and its number. Target length dropped 600 → 400 words: bullets are terser
  than flowing prose, which is the point of the change.

### Added
- `render.py` — renders the stored draft two ways. `markdown_to_html()` for the
  HTML part (inline styles only; mail clients strip `<style>` blocks, and Gmail
  ignores everything but inline CSS), `to_plain_text()` for the plain
  alternative. Everything is HTML-escaped BEFORE markup is inserted, so a stray
  `<` in an edited draft cannot break the email or inject anything.
- Messages are now `multipart/alternative`. **The plain part is never dropped**
  — some clients and most archiving tools refuse HTML, and a recipient should
  never receive an empty message.

### Why the draft stays lightly-marked text rather than HTML
One editable source of truth. A person can edit `**bold**` and `- ` in a
textarea without fighting markup, and both wire formats are derived from it, so
they cannot disagree.

### Verified against live data (2026-09-16)
Regenerated all three stored weeks. Week 37: 629 words, 11 bullets, ~35s.
**9 further figures spot-checked and all 9 reconciled exactly** to the payload
(store-level sales, AOV, returns percentages, prior-year YTD rates) — 20 of 20
across both format revisions.


## 0.4.0 — 2026-09-16

### Removed
- **`POST /runs/generate`.** There is no longer any way to generate a report
  over HTTP. The analysis costs money and is written exactly once per report,
  by the Monday 03:00 job.

  The endpoint existed so a run taken before a store finished posting could be
  refreshed. That reasoning was wrong: a sale posted late carries the later
  post date, so it falls into the NEXT week's window and re-running today
  cannot pull it back. The button solved nothing while keeping a token-spend
  path open on every press.

  Recovery from a failed Monday run is a manual job run on the server — see
  `scripts/jobs/README.md`.

- **`draft_stale`**, added earlier the same day. It flagged an analysis whose
  figures had moved under it, which could only happen through the refresh path
  above. With that gone nothing can set it, and unreachable state is a trap for
  the next reader. Column dropped.

### Added
- Guard against replacing a run while it is being sent. Resetting the row under
  an in-flight dispatcher would hand recipients the old workbook against a run
  claiming new figures — a seconds-wide window, but a confusing one.
- `tests/unit/modules/weekly_report/test_routes.py` — HTTP-layer tests,
  including one asserting the generate endpoint does not exist and one asserting
  `routes.py` does not even mention `draft_email`, so a future refactor cannot
  silently re-open the spend.

### Cost
One model call per week: the Monday 03:00 job. Roughly $0.13 a run, ~$7/year.
`draft_email=False` survives only for the CLI's `--no-email-draft` flag, which
produces figures without paying for prose; nothing over HTTP reaches it.


## 0.5.0 — 2026-09-16

A failed generation is now visible and recoverable, without reopening a casual
spend path.

### Added
- **`commentary_error`** on `periodic_report_runs` (with an `ADD COLUMN IF NOT
  EXISTS` upgrade path). Records WHY the analysis is missing. Previously a
  failure only printed a warning to the job log, so a dead API key looked
  exactly like a deliberately disabled one — weeks of analysis-free reports
  could have gone to the board before anyone asked why.
  `draft_email()` now returns `(subject, body, commentary_error)`.
  An unconfigured key counts as a failure: `generate_commentary()` returns `''`
  rather than raising, and that is still a missing analysis.
- **`POST /runs/<id>/retry`** — re-runs a generation that FAILED. One model call.

### The guard is the point
It is refused unless something actually went wrong:
- healthy run → `INVALID_STATE` ("edit the text instead of regenerating it")
- already approved or sent → `INVALID_STATE`
- run not found → `NOT_FOUND`

So it cannot become the casual regenerate button removed in 0.4.0. Verified
against the three live stored runs: all three refuse, no API call made.

### One path, not two
Whether the whole run failed or only the analysis did, the retry regenerates
**everything** — figures, workbook and analysis together.

Re-fetching is safe here, for the same reason the refresh button was removed:
every window ends in the past (the closed Mon-Sun week; MTD/YTD through that
Sunday), and a sale posted late carries the later post date, so it lands in the
NEXT week's window rather than changing this one. Re-querying a closed window
returns what it returned before. An earlier draft branched — re-draft from the
stored payload when only the prose failed — which was more code for no benefit.

A retry whose analysis fails again returns `COMMENTARY_FAILED` (502) rather than
reporting success; the refreshed figures are still saved.


## 0.6.0 — 2026-09-16

Recipients are now independent of approval.

### The rule
Approval locks the **subject, body and figures** — what was signed off. It does
NOT lock **who receives them**. The recipient list is global and changes over
time, so a past report can be forwarded to someone added since.

### Added
- **`POST /runs/<id>/resend`** — send an already-sent report again, to the
  CURRENT recipient list. No fresh approval: the content is unchanged and was
  already approved. Still gated by the APPROVE section.
- `repository.requeue_run()`, guarded on `status = 'sent'` and kept separate
  from `approve_run` so a re-send and a first approval cannot stand in for each
  other.

### Why this works without a schema change
Nothing about recipients was ever stored on a run — `create_run` has no
recipient parameter, and `send_run` reads the live list at send time. A test now
pins that, because it is the guarantee the whole feature rests on.

### Note on `periodic_report_sends`
Kept as an operational log of send attempts (success, failure detail, timestamp),
which is what makes a failed delivery diagnosable. It is no longer presented as
"who received this report" — the UI shows a short "last sent" line instead.


## 0.7.0 — 2026-09-16

**Approval and sending are now separate decisions**, with one Send action
covering both a first send and a re-send.

### Changed
- **`POST /runs/<id>/approve`** signs off the CONTENT only. It takes no body,
  queues nothing, and **no longer requires recipients** — the list is edited
  independently and may legitimately be empty at that moment.
  `approve_run` sets `scheduled_send_at = NULL`, which is what keeps an approved
  run out of `claim_due_runs`: the dispatcher matches on
  `scheduled_send_at <= now`, and NULL never compares true. So "approved" and
  "queued" are distinguishable without another column.
- **`POST /runs/<id>/send`** replaces `/resend`. One endpoint for both cases,
  because sending the first time and sending again differ only in what the row
  said beforehand. Guarded on `status IN ('approved', 'sent')`, so a run still
  awaiting approval cannot be sent and one mid-send cannot be disturbed.
  Recipients ARE required here — a send with no visible addressee is refused by
  the mailer anyway, so it is better refused up front.
- **A schedule passed to `/send` is saved as the new default**, the same
  contract as the recipient list: set it once and it stays until changed.

### Removed
- `resend()` and `requeue_run`'s sent-only guard — both folded into `send()`
  and `queue_run`.


## 0.7.1 — 2026-09-17

Two bugs found on the deployed system.

### Fixed: a schedule was interpreted in the SERVER's timezone, not the user's
The user picked **00:30** and it was stored as **00:30 UTC** — 07:30 in Vietnam.
The browser then faithfully displayed 07:30 for a time typed as 00:30, and the
run was not due for another seven hours.

`datetime.now()` on a UTC server is naive UTC, and that is what reached a
`TIMESTAMPTZ` column. Schedules are now computed as timezone-AWARE values in a
configurable report timezone (`REPORT_TIMEZONE`, default `Asia/Ho_Chi_Minh`),
so `00:30` means 00:30 where the user is.

Same class of bug as the systemd timer fixed in 0.7.0 — that fix addressed the
TIMER and left the APPLICATION untouched.

### Fixed: the week selector showed the same week twice
Runs are keyed by `as_of`, but **every day Mon-Sun reports the same closed
week** — so a manual run mid-week created a second row for a week that already
had one. `generate_run` now normalises `as_of` to the canonical day for the week
being reported (the day after it ends), so `ON CONFLICT (as_of)` replaces the
existing run, which is what "run it again for this week" should mean.

### Also
`through` now defaults to that same closing Sunday. Previously only the job
passed it, so any other caller silently got MTD/YTD windows running to the
Monday — a day with almost no sales measured against a full prior-year day. The
correct behaviour should not depend on the caller remembering.


## 0.8.0 — 2026-09-17

Past weeks can be backfilled as **historical** reports, so the week selector
covers history rather than only weeks the job happened to run.

### Why the selector only showed four weeks
It lists GENERATED REPORTS, not available data. A week appeared only once the
job had run for it — three had been generated by hand and one by a manual run —
while Oracle holds data back to 2024.

### Added
- **`historical` status.** Figures, workbook and the email template, with **no
  analysis and no API call**. Editable (a person may write the analysis by hand)
  and sendable, but never approvable — it was never a live draft. Kept distinct
  from `pending_approval` so ~40 past weeks don't all claim to need sign-off and
  bury the real Monday report.
- **`scripts/jobs/periodic_report_backfill.py`** with `--from/--to/--dry-run`.
  **One Oracle fetch** covers the whole range and each week is aggregated in
  memory — one query against the live database instead of one history-spanning
  query per week.
- **`service.backfill_week()`**. Never overwrites an existing run, so weeks that
  already carry a real, paid analysis are left untouched. Safe to re-run.

### Guarantees, pinned by tests
- **No model call**, and no path from a historical report to one:
  `retry_generation` refuses it.
- **Backfilled figures equal a live generation** of the same week.
- A week's workbook contains only rows from **its own span** — the shared fetch
  must not put months of later data into week 1's Raw Data sheet.

### Verified against production (dry run, 2026-09-17)
2026 through ISO week 37: **37 weeks, 3 already exist (kept), 34 to create.**


## 0.8.1 — 2026-09-18

### Fixed: a complete month was never reported
Reported from production on week 36 (31 Aug - 6 Sep), whose MTD sheet showed six
days of September.

A week that straddles a month boundary now reports the month that **ended**
inside it, in full. Week 36's MTD is the whole of August.

Without this, a complete month is never reported at all: the last week ending in
August stops on the 30th, and the next week jumps to September — so August is
only ever seen a day short. That happens in **every month whose last day is not
a Sunday**. Six days of a new month is also a poor comparison against six days
of the prior year, while a complete month is the natural unit.

The rule applies only when the report is **anchored to that week**
(`through <= week_end`), which production always is. An ad-hoc run asking for a
later cut-off — `--as-of 9 Sep` with no `--through` — still gets month-to-date on
the 9th.

A week spanning New Year gets the same treatment: it now reports the whole of
December, which it never did before.

### Known, not changed at the time
YTD had the same flaw one level up. Fixed in 0.8.2 below.


## 0.8.2 — 2026-09-18

### Fixed: a complete year was never reported either
The same rule as 0.8.1, one level up. A week spanning New Year now reports the
whole of the year that **ended**, instead of the two or three days of the new
one — which would otherwise have sat next to a complete December and said far
less.

A complete year was likewise never reported at all, since 31 December is rarely
a Sunday.

Affects exactly one week a year, and it is the week where YTD was least useful.
Every other week is untouched. The same `through <= week_end` guard applies, so
an ad-hoc run asking for a later cut-off still gets year-to-date at that date.

The two rules now agree in that week: a complete December inside a complete
prior year.


## 0.9.0 — 2026-09-19

### Added
- The prior-period block on every period sheet (Week by Week, MTD, YTD) is now
  an Excel column group that opens COLLAPSED. The three blocks together are
  ~1543px, which overflows a 15" screen; the prior figures are reference
  material while the current figures and the deltas are what gets read.
  Collapsing puts the delta block on screen beside the current one, and the
  outline button restores the prior figures in one click.
  - The `+` control sits on the first delta column — Excel draws it on the
    column AFTER the group (`summaryRight`) and marks that column `collapsed`,
    mirroring the summary-cell rule the existing row outline already follows
    with `summaryBelow`. Both outlines coexist in one `outlinePr`.
  - Implemented by setting `outlineLevel`/`hidden` per column rather than with
    `column_dimensions.group()`. That helper deletes the per-column dimensions
    across the range and leaves one spanning dimension keyed to the first
    column, so the remaining columns fall back to Excel's default width of 13:
    measured, `group('J','P')` over widths `[10,8,7,9,9,8,9]` yields
    `[10,13,13,13,13,13,13]`, which would have made the block wider on expand
    than the layout is sized for.

### Tests
- New `tests/unit/modules/weekly_report/test_excel.py` — 37 tests, the first
  direct coverage of the workbook layout. Every assertion runs against a real
  save/load round trip, since the outline only counts if it survives into the
  file Excel opens. Confirmed to fail against the previous renderer (9 failures
  across the three sheets) before being committed.
- Covers the guard against the `group()` regression, that the current and delta
  blocks stay visible, that the existing row outline and `summaryBelow` still
  work alongside the new column outline, and that `Raw Data` is untouched.


## 0.9.1 — 2026-09-21

### Fixed
- **YTD now ends where MTD ends.** The straddle rule added in 0.8.x moved MTD
  to the completed month and YTD to the completed year, but YTD was left alone
  by a MONTH boundary. Week 36 of 2026 therefore reported MTD as the whole of
  August beside a YTD running to 6 September — so YTD carried six days the MTD
  excluded and was not the sum of the months shown. Two columns silently
  describing different spans.
  - The YTD branch tested `week_begin.year != week_end.year`; it now uses the
    same `week_begin.month != week_end.month` condition as MTD, with
    `month_end(week_begin)` as the end date.
  - This SUBSUMES the year-boundary case rather than special-casing it: the
    week spanning New Year closes December, so `month_end` is 31 December and
    the year is still reported whole. One rule, two effects.
  - Effect on week 36: YTD 1 Jan–6 Sep becomes 1 Jan–31 Aug; sales
    158,837,587,819 → 155,157,421,731, bills 4,534 → 4,446.

### Tests
- Two tests asserting the old behaviour were replaced — both were written with
  the original straddle work and encoded the gap.
- Added a sweep that walks every week of a year and asserts MTD and YTD end
  together whenever a week closes a month, rather than spot-checking one date.
- 1197 tests pass.

### Note
Stored runs keep the figures they were generated with, so eight existing runs
still carry the old YTD: seven `historical` from the backfill, plus week 36,
which was regenerated by hand after this change.


## 0.10.0 — 2026-09-21

### Added
- **WTD period** — the reported week against the SAME ISO WEEK NUMBER a year
  earlier. Week 38 of 2026 (14–20 Sep) pairs with week 38 of 2025 (15–21 Sep).
  - Not `shift_year`. MTD and YTD align on the calendar date, which is right
    for a month or a year, but a week shifted by date lands on different
    weekdays and carries a different number of Saturdays. Retail weeks are only
    comparable weekday-for-weekday.
  - `prior_iso_week()` takes the ISO week from a day three into the week, so a
    Sunday-start week works too, and places the prior-year anchor on the same
    ISO weekday. A year with no week 53 falls back to its week 52.
  - Both WTD windows carry their ISO label, so the page reads
    "Week 38 2026 vs Week 38 2025" rather than leaving the reader to check the
    dates really are the same week.
- `PERIOD_TITLES` — display names for every period, used by the payload.

### Changed
- `PERIOD_LABELS` is now `('WTD', 'MTD', 'YTD', 'WOW')`. WTD, MTD and YTD all
  compare against a year earlier; WOW is the odd one out and sits last.

### Deliberately unchanged
- **WOW, MTD and YTD windows.** Verified against staging across 4,380 window
  comparisons — two years of dates, both through-modes — with 0 differences.
- **The workbook.** Still three sheets in the same order; WTD is an app-only
  view, so the emailed attachment is untouched. `excel.py` iterates
  `SHEET_NAMES` rather than `PERIOD_LABELS` to keep it that way.
- **The written analysis and the email summary lines.** Both now iterate
  `COMMENTARY_LABELS` — the same three periods as before — so WTD costs no
  tokens and narrates nothing.

### Tests
- 1201 pass. Two tests were updated because the behaviour they pinned
  deliberately changed: the payload now carries four periods, and WTD's prior
  window can begin earlier than YTD's in early January, so the fetch span is no
  longer bounded by YTD. Added a test for the property that actually matters —
  the span covers every window.


## 0.11.0 — 2026-09-21

### Changed
- **Renamed from "periodic report" to "weekly report"**, which is what the
  feature is actually called. Module `app/modules/weekly_report/`, class
  `WeeklyReportService`, blueprint `weekly_report_bp`, URL
  `/api/v1/weekly-report`, downloaded file `weekly_report_<date>.xlsx`.
  - `/api/v1/periodic-report` is kept as a TEMPORARY alias so the two repos'
    independent deploys cannot leave the page calling a path that no longer
    exists. Remove it once the frontend rename has been live a while.
- **One permission section.** `PERIODIC_REPORT` and `PERIODIC_REPORT_APPROVE`
  collapse into `WEEKLY_REPORT`: access to the page is access to the page, the
  same as every other feature. Splitting view from approve/send bought nothing
  — nobody was granted the view without needing to act on it — and only made
  this page inconsistent.
  - `_migrate_periodic_report_to_weekly()` carries grants over as a UNION, so
    anyone holding EITHER old section holds the new one. It is idempotent and
    runs from `init_db.py`.
  - This WIDENS access: a view-only holder can now approve and send.

### Deliberately NOT renamed
- The four `periodic_report_*` tables. Renaming them is a migration over live
  data that nobody ever sees.
- `scripts/jobs/periodic_report_*.py` and the `gbl-periodic-report` systemd
  units. A timer pointing at a moved script fails SILENTLY — the Monday report
  simply never runs. Both are recorded in CLAUDE.md.

### Future idea, not built
- Per-user views of the page — e.g. a different recipient list per user.
  Raised 2026-09-21; not a priority.


## 0.12.0 — 2026-09-21

### Added
- **Average revenue by weekday**, in every period block of the payload, for the
  chart view behind each tab. Seven entries, Monday first, each carrying the
  weekday's total, how many times it occurred, and the average.
  - Averaged over CALENDAR occurrences, not over days that traded. A Monday
    with no sales is a real zero and pulls the Monday average down; counting
    only days with rows would quietly report the average of the Mondays that
    went well.
  - A weekday the window never contains gets `average: None`, not zero — a zero
    would assert that trading happened and earned nothing.
  - Revenue is `sale_net - return_net`, the same `total_sales` the tables show,
    so the seven bars sum back to the period total. A test asserts this for all
    four periods; verified against live Oracle to 0.0m in each.
  - Monday first, matching `week_start='monday'` and every week label the
    report prints.

### Note
Computed at generation because the daily rows are not stored — the snapshot is
all the page ever sees. Reports generated before this release carry no weekday
block and never will; the page says so rather than drawing an empty chart.


## 0.13.0 — 2026-09-21

### Added
- **`--force` and `--keep-analysis`** on the generate job, for re-rendering a
  stored week because the REPORT changed rather than the data — a new period,
  a corrected window, a new view.

      PYTHONPATH=. python scripts/jobs/periodic_report_generate.py \
          --as-of 2026-09-21 --force --keep-analysis

  - `--force` lifts the guard on a week already `sent`. That guard exists so a
    re-render cannot quietly replace the record of what was emailed; what
    actually went out lives in `weekly_report_sends` and is untouched. It does
    NOT open the `sending` window — recipients would get the old workbook
    against a rewritten run, and no version of that is safe.
  - `--keep-analysis` carries the stored subject and body across verbatim,
    including human edits, and implies `--no-email-draft`. Re-rendering figures
    should neither discard prose somebody paid for nor quietly spend a model
    call replacing it. Refused alongside drafting, and refused when there is no
    stored run to keep anything from.

### Why
Adding WTD and then the weekday chart each meant re-rendering a stored week
without touching its prose. Both times that was done by hand-editing the
database: stash the analysis, clear the status, regenerate, restore. Three
occurrences is enough to make it a flag.

