"""Periodic report service — snapshot generation, approval, dispatch state.

The weekly cycle this serves:

  Mon 03:00   the scheduled job calls `generate_run()`. One Oracle fetch, three
              period windows, a stored snapshot + workbook + email draft.
  Mon morning the page reads that snapshot back and shows it for approval.
  on approve  the run is queued; the dispatcher sends it when its time arrives.

Why the figures are SNAPSHOT, not recomputed on read
----------------------------------------------------
Oracle is live and moves during the day — a verification run once saw qty go
13,431 -> 13,435 mid-session. If the preview recomputed, the numbers approved
would not be the numbers emailed. `generate_run` writes the aggregates once and
every later read serves that payload.
"""
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from .aggregate import METRICS, aggregate, delta_for, is_unfavourable
from .excel import build_workbook_bytes
from .render import html_document, markdown_to_html, to_plain_text
from .period import (COMMENTARY_LABELS, PERIOD_LABELS, PERIOD_TITLES,
                     fetch_span, last_complete_week,
                     period_windows, week_label)
from .repository import PeriodicReportRepository

# Money and quantities are exact `Decimal` through aggregation; the JSON payload
# carries them as floats rounded to 2dp. That is a display artifact — VND has no
# subunit in practice, and the exact figures survive in the stored workbook,
# whose cells still hold the Decimals.
_ROUND_TO = 2

# The server clock is UTC; the business runs on Vietnam time. Every send time a
# user picks is meant in THEIR timezone, so schedules are computed here and
# stored as timezone-AWARE values. Naive `datetime.now()` on a UTC server
# silently turned "00:30" into 00:30 UTC — 07:30 in Vietnam — and the browser
# then faithfully displayed 07:30 for a time the user had typed as 00:30.
REPORT_TZ = ZoneInfo(os.getenv('REPORT_TIMEZONE', 'Asia/Ho_Chi_Minh'))


def now_local() -> datetime:
    """Current time as an AWARE datetime in the report timezone."""
    return datetime.now(REPORT_TZ)

DEFAULT_SUBJECT_TEMPLATE = 'Báo cáo bán hàng định kỳ — Tuần {{week_no}}/{{week_year}}'

# Placeholders are `{{name}}` so a template can contain literal braces without
# escaping. `render_template` documents the full set it substitutes.
#
# The fixed wording is the company's own. Only the numbered analysis is written
# by the model, through `{{commentary}}` — everything around it is template.
DEFAULT_BODY_TEMPLATE = """Dear all,

Đính kèm là báo cáo bán hàng định kỳ, gồm ba phần: Tuần {{week_no}} so với Tuần {{prior_week_no}}, lũy kế tháng {{month_no}} ({{mtd_range}}) và lũy kế từ đầu năm, chia theo cửa hàng và ngành hàng. (Đơn vị tiền: triệu đồng, chưa bao gồm VAT.)

Xin tóm tắt các điểm chính, theo thứ tự từ tuần gần nhất đến lũy kế năm:

{{commentary}}

Best regards,
"""


class PeriodicReportService:

    def __init__(self):
        self.repo = PeriodicReportRepository()

    # ==================================================================
    # Schema
    # ==================================================================
    def init_database(self) -> Dict[str, Any]:
        """Create the four `periodic_report_*` tables. Idempotent."""
        from app.core.database import get_postgres_connection

        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to database'}
        try:
            with conn.cursor() as cur:
                # One row per generated report. `payload` is the snapshot the
                # preview and the email both read; `workbook` is the .xlsx
                # itself — held here rather than on disk because deploy.yml runs
                # `rm -rf document/` on the server, so a file written there does
                # not survive the next release. ~336KB/week is ~17MB/year.
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS periodic_report_runs (
                        id                SERIAL PRIMARY KEY,
                        as_of             DATE NOT NULL UNIQUE,
                        week_start        VARCHAR(10) NOT NULL DEFAULT 'monday',
                        through_date      DATE,
                        status            VARCHAR(20) NOT NULL
                                          DEFAULT 'pending_approval',
                        payload           JSONB,
                        workbook          BYTEA,
                        email_subject     TEXT,
                        email_body        TEXT,
                        error             TEXT,
                        generated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        approved_by       BIGINT REFERENCES users(sid)
                                          ON DELETE SET NULL,
                        approved_at       TIMESTAMPTZ,
                        scheduled_send_at TIMESTAMPTZ,
                        sent_at           TIMESTAMPTZ,
                        commentary_error  TEXT
                    )
                ''')
                # v0.5.0 upgrade path: why the analysis is missing, so the page
                # can explain a blank section instead of just showing nothing.
                cur.execute('''
                    ALTER TABLE periodic_report_runs
                        ADD COLUMN IF NOT EXISTS commentary_error TEXT
                ''')
                # The dispatcher polls on (status, scheduled_send_at) every few
                # minutes forever; without this it is a sequential scan that
                # grows a row a week.
                cur.execute('''
                    CREATE INDEX IF NOT EXISTS periodic_report_runs_due_idx
                        ON periodic_report_runs (status, scheduled_send_at)
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS periodic_report_recipients (
                        id         SERIAL PRIMARY KEY,
                        email      VARCHAR(255) NOT NULL,
                        name       VARCHAR(255),
                        kind       VARCHAR(10) NOT NULL DEFAULT 'to',
                        is_active  BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (email, kind)
                    )
                ''')
                # Singleton (id is pinned to 1) — these are tool-wide settings,
                # not per-user, and a one-row table keeps the read trivial.
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS periodic_report_settings (
                        id                 INTEGER PRIMARY KEY DEFAULT 1,
                        send_mode          VARCHAR(20) NOT NULL DEFAULT 'immediate',
                        send_time          TIME,
                        send_weekday       SMALLINT,
                        subject_template   TEXT,
                        body_template      TEXT,
                        commentary_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CONSTRAINT periodic_report_settings_singleton
                            CHECK (id = 1)
                    )
                ''')
                cur.execute('''
                    INSERT INTO periodic_report_settings
                        (id, send_mode, subject_template, body_template)
                    VALUES (1, 'immediate', %s, %s)
                    ON CONFLICT (id) DO NOTHING
                ''', (DEFAULT_SUBJECT_TEMPLATE, DEFAULT_BODY_TEMPLATE))
                # Append-only audit of what actually left the building.
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS periodic_report_sends (
                        id         SERIAL PRIMARY KEY,
                        run_id     INTEGER NOT NULL
                                   REFERENCES periodic_report_runs(id)
                                   ON DELETE CASCADE,
                        recipients TEXT[] NOT NULL,
                        success    BOOLEAN NOT NULL,
                        detail     TEXT,
                        sent_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                ''')
            conn.commit()
            return {'success': True}
        except Exception as e:
            conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            conn.close()

    # ==================================================================
    # Generation
    # ==================================================================
    def build_snapshot(self, as_of: date, week_start: str = 'monday',
                       through: Optional[date] = None) -> Dict[str, Any]:
        """Fetch from Oracle and aggregate into the JSON-serialisable payload.

        No database writes — `generate_run` persists. Split out so the figures
        can be produced and inspected without touching stored state.
        """
        windows = period_windows(as_of, week_start, through)
        fetch_from, fetch_to = fetch_span(windows)
        dept_rows, store_rows = self.repo.fetch_sales(fetch_from, fetch_to)
        return self._snapshot_from_rows(as_of, week_start, through, windows,
                                        fetch_from, fetch_to,
                                        dept_rows, store_rows)

    def _snapshot_from_rows(self, as_of, week_start, through, windows,
                            fetch_from, fetch_to, dept_rows, store_rows):
        """Aggregate already-fetched rows into the payload.

        Kept separate from the fetch so `generate_run` can build the payload and
        the workbook from ONE set of rows. Fetching twice would let the live
        Oracle move between them and put different figures in the preview and
        the attachment — the exact mismatch the snapshot exists to prevent.
        """
        periods = {}
        store_names: Dict[str, str] = {}
        for label in PERIOD_LABELS:
            cur_win, pri_win = windows[label]
            cur_data = aggregate(dept_rows, store_rows, cur_win)
            pri_data = aggregate(dept_rows, store_rows, pri_win)
            store_names.update(pri_data[3])
            store_names.update(cur_data[3])
            periods[label] = self._period_payload(label, cur_win, pri_win,
                                                  cur_data, pri_data)

        return {
            'as_of': as_of.isoformat(),
            'week_start': week_start,
            'through': (through or as_of).isoformat(),
            'fetch_from': fetch_from.isoformat(),
            'fetch_to': fetch_to.isoformat(),
            'store_names': store_names,
            'metrics': [{'key': k, 'unit': u,
                         'lower_is_better': k in _LOWER} for k, u in METRICS],
            'periods': periods,
        }

    def _period_payload(self, label, cur_win, pri_win, cur_data, pri_data):
        cur_dept, cur_store, cur_grand, _ = cur_data
        pri_dept, pri_store, pri_grand, _ = pri_data

        stores = []
        for store in sorted(set(cur_store) | set(pri_store)):
            depts = sorted({d for (s, d) in cur_dept if s == store} |
                           {d for (s, d) in pri_dept if s == store})
            stores.append({
                'store': store,
                **self._compare(cur_store.get(store), pri_store.get(store)),
                'departments': [{
                    'department': d,
                    **self._compare(cur_dept.get((store, d)),
                                    pri_dept.get((store, d))),
                } for d in depts],
            })

        return {
            'label': PERIOD_TITLES[label],
            'current': self._window_payload(label, cur_win),
            'prior': self._window_payload(label, pri_win),
            'total': self._compare(cur_grand, pri_grand),
            'stores': stores,
        }

    # Periods whose windows are whole weeks, so an ISO week label means
    # something. MTD and YTD spans are not weeks and get no label.
    _WEEK_PERIODS = ('WOW', 'WTD')

    @classmethod
    def _window_payload(cls, label, window):
        start, end = window
        out = {'from': start.isoformat(), 'to': end.isoformat()}
        if label in cls._WEEK_PERIODS:
            # On WTD this is the whole point of the comparison: both sides
            # carry their ISO label, so the page can say "Week 38 2026 vs
            # Week 38 2025" rather than leaving the reader to check that the
            # dates really are the same week a year apart.
            out['label'] = week_label(start)
        return out

    @staticmethod
    def _compare(cur_m, pri_m) -> Dict[str, Any]:
        """One row: current values, prior values, and the change per metric."""
        cur_m = cur_m or {}
        pri_m = pri_m or {}
        current, prior, change = {}, {}, {}
        for key, unit in METRICS:
            cv, pv = cur_m.get(key), pri_m.get(key)
            current[key] = _num(cv)
            prior[key] = _num(pv)
            value, kind = delta_for(key, unit, cv, pv)
            change[key] = {
                'value': _num(value),
                'kind': kind,
                'unfavourable': is_unfavourable(key, value),
            }
        return {'current': current, 'prior': prior, 'change': change}

    def generate_run(self, as_of: Optional[date] = None,
                     week_start: str = 'monday',
                     through: Optional[date] = None,
                     draft_email: bool = True) -> Dict[str, Any]:
        """Produce and store this week's run. Called by the Monday job.

        A run already `sent` is never overwritten — regenerating after the email
        has gone out would silently replace the record of what was sent. Any
        other status is replaced, so a retry after a failure is safe.
        """
        as_of = as_of or date.today()
        # Normalise to the canonical day for the week being reported: the day
        # after that week ends. Runs are keyed by `as_of`, but EVERY day from
        # Mon-Sun reports the same closed week — so a manual run on Wednesday
        # used to create a SECOND row for a week that already had one, and the
        # week selector showed it twice. Collapsing to the canonical day makes
        # `ON CONFLICT (as_of)` replace the existing run, which is what "run it
        # again for this week" should mean.
        week_end = last_complete_week(as_of, week_start)[1]
        as_of = week_end + timedelta(days=1)
        # Default MTD/YTD to that same closing Sunday. Previously only the job
        # passed `through`, so any other caller silently got windows running to
        # the Monday — a day with almost no sales against a full prior-year day.
        # The correct behaviour should not depend on the caller remembering.
        if through is None:
            through = week_end

        existing = self.repo.get_run_by_as_of(as_of)
        if existing and existing['status'] == 'sent':
            return {'success': False,
                    'error': f'The report for {as_of} has already been sent.',
                    'code': 'ALREADY_SENT'}
        if existing and existing['status'] == 'sending':
            # A dispatcher is mid-send. Replacing the row now would reset it to
            # pending_approval underneath that send, and the recipients would
            # get the OLD workbook against a run that claims to hold new
            # figures. It is a seconds-wide window, but a confusing one.
            return {'success': False,
                    'error': 'This report is being sent right now — try again '
                             'in a moment.',
                    'code': 'INVALID_STATE'}

        windows = period_windows(as_of, week_start, through)
        fetch_from, fetch_to = fetch_span(windows)
        try:
            # ONE fetch feeds both the payload and the workbook — see
            # `_snapshot_from_rows`.
            dept_rows, store_rows = self.repo.fetch_sales(fetch_from, fetch_to)
            payload = self._snapshot_from_rows(
                as_of, week_start, through, windows, fetch_from, fetch_to,
                dept_rows, store_rows)
            workbook = build_workbook_bytes(windows, dept_rows, store_rows)
        except Exception as e:
            # Record the failure as a run so the page can show that Monday's
            # job ran and why it produced nothing, rather than showing stale
            # data from last week as if it were current.
            self.repo.create_run(
                as_of=as_of, week_start=week_start, through_date=through,
                status='failed', payload=None, workbook=None,
                email_subject=None, email_body=None, error=str(e))
            return {'success': False, 'error': str(e), 'code': 'SERVER_ERROR'}

        # The analysis is written exactly ONCE per report. `draft_email=False`
        # exists only for the CLI's --no-email-draft flag, which produces the
        # figures without paying for prose; nothing over HTTP can reach it.
        subject = body = None
        commentary_error = None
        if draft_email:
            subject, body, commentary_error = self.draft_email(payload)

        run_id = self.repo.create_run(
            as_of=as_of, week_start=week_start, through_date=through,
            status='pending_approval', payload=payload, workbook=workbook,
            email_subject=subject, email_body=body, error=None,
            commentary_error=commentary_error)
        return {'success': True, 'run_id': run_id, 'as_of': as_of.isoformat(),
                'commentary_error': commentary_error}

    def backfill_week(self, week_end: date, dept_rows, store_rows,
                      week_start: str = 'monday') -> Dict[str, Any]:
        """Store a past week as a `historical` report, from rows already fetched.

        Used by the backfill script, which fetches Oracle ONCE for the whole
        range and calls this per week — rather than issuing a full
        history-spanning query per week against the live database.

        Deliberately calls NO model. The analysis is paid for once per report,
        by the Monday job; history is figures, workbook and template only. A
        person can still write the analysis for any single week via Edit.

        Never overwrites an existing run: weeks already generated — including
        ones carrying a real, paid analysis — are left untouched.
        """
        as_of = week_end + timedelta(days=1)
        if self.repo.get_run_by_as_of(as_of) is not None:
            return {'success': True, 'skipped': True, 'as_of': as_of.isoformat()}

        windows = period_windows(as_of, week_start, week_end)
        fetch_from, fetch_to = fetch_span(windows)

        # Narrow the shared rows to THIS week's own span. The figures would be
        # unaffected either way (aggregation filters by window), but the
        # workbook's Raw Data sheet shows every row it is given — and week 1's
        # sheet must not contain months of data from after that week.
        dept = [r for r in dept_rows if fetch_from <= r['day'] <= fetch_to]
        store = [r for r in store_rows if fetch_from <= r['day'] <= fetch_to]

        payload = self._snapshot_from_rows(
            as_of, week_start, week_end, windows, fetch_from, fetch_to,
            dept, store)
        workbook = build_workbook_bytes(windows, dept, store)

        settings = self.repo.get_settings() or {}
        subject = render_template(
            settings.get('subject_template') or DEFAULT_SUBJECT_TEMPLATE, payload)
        body = render_template(
            settings.get('body_template') or DEFAULT_BODY_TEMPLATE, payload, '')

        run_id = self.repo.create_run(
            as_of=as_of, week_start=week_start, through_date=week_end,
            status='historical', payload=payload, workbook=workbook,
            email_subject=subject, email_body=body, error=None,
            # Not a failure — the analysis was deliberately not generated — so
            # no error is recorded and the page raises no "could not be
            # generated" notice.
            commentary_error=None)
        return {'success': True, 'skipped': False, 'run_id': run_id,
                'as_of': as_of.isoformat()}

    # ==================================================================
    # Email draft
    # ==================================================================
    def draft_email(self, payload: Dict[str, Any]) -> tuple:
        """Render subject and body. Returns (subject, body, commentary_error).

        Commentary is best-effort: if the call fails or no API key is
        configured, the run still completes with that placeholder empty — a
        missing paragraph is a far better outcome than no report at all.

        But it must never fail SILENTLY. `commentary_error` carries the reason
        so the page can say why the analysis is blank; otherwise a dead API key
        looks exactly like a disabled one, and nobody finds out until someone
        notices weeks of analysis-free reports.
        """
        settings = self.repo.get_settings() or {}
        subject_tpl = settings.get('subject_template') or DEFAULT_SUBJECT_TEMPLATE
        body_tpl = settings.get('body_template') or DEFAULT_BODY_TEMPLATE

        commentary = ''
        error = None
        if settings.get('commentary_enabled', True):
            try:
                from .commentary import generate_commentary
                commentary = generate_commentary(payload) or ''
                if not commentary:
                    # generate_commentary returns '' when unconfigured rather
                    # than raising — that is still a missing analysis.
                    error = ('No ANTHROPIC_API_KEY is configured, so the '
                             'analysis was not generated.')
            except Exception as e:   # noqa: BLE001 — never fail the run on this
                error = f'{type(e).__name__}: {e}'
                print(f'[WARN] periodic report commentary unavailable: {e}')

        return (render_template(subject_tpl, payload, commentary),
                render_template(body_tpl, payload, commentary),
                error)

    def retry_generation(self, run_id: int) -> Dict[str, Any]:
        """Re-run a generation that FAILED. Costs one model call.

        Regenerates everything — figures, workbook and analysis — whether the
        whole run failed or only the analysis did. One path rather than two,
        because re-fetching is cheap and deterministic here: every window ends
        in the past (the closed Mon-Sun week; MTD/YTD through that Sunday), and
        a sale posted late carries the later post date, so it lands in the NEXT
        week's window rather than changing this one. Re-querying a closed window
        returns what it returned before.

        Deliberately narrow: refused unless something actually went wrong. The
        analysis is written once per report, and a button that worked on a
        healthy run would be exactly the casual spend path that was removed.
        """
        run = self.repo.get_run(run_id)
        if run is None:
            return {'success': False, 'error': 'Run not found', 'code': 'NOT_FOUND'}

        if run['status'] not in ('failed', 'pending_approval'):
            return {'success': False,
                    'error': f"This report is {run['status']} — it can no longer "
                             'be regenerated.',
                    'code': 'INVALID_STATE'}

        if run['status'] == 'pending_approval' and not run.get('commentary_error'):
            return {'success': False,
                    'error': 'This report generated successfully. The analysis '
                             'is written once per report — edit the text '
                             'instead of regenerating it.',
                    'code': 'INVALID_STATE'}

        result = self.generate_run(
            as_of=run['as_of'], week_start=run['week_start'] or 'monday',
            through=run['through_date'], draft_email=True)
        if result.get('success') and result.get('commentary_error'):
            # Figures came back, the analysis failed again. Reported rather than
            # returned as success, so the page does not claim it worked.
            return {'success': False, 'error': result['commentary_error'],
                    'code': 'COMMENTARY_FAILED'}
        return result

    # ==================================================================
    # Reads
    # ==================================================================
    def get_latest(self) -> Dict[str, Any]:
        run = self.repo.get_latest_run()
        if run is None:
            return {'success': True, 'run': None}
        return self.get_run(run['id'])

    def get_run(self, run_id: int) -> Dict[str, Any]:
        run = self.repo.get_run(run_id)
        if run is None:
            return {'success': False, 'error': 'Run not found',
                    'code': 'NOT_FOUND'}
        out = _serialise_run(self._with_week(run))
        # What actually went out, for a run already sent. Deliberately NOT the
        # current recipient list — that changes over time, and showing today's
        # addresses beside an old report would claim people received something
        # they never did.
        out['sends'] = [
            {**s, 'sent_at': s['sent_at'].isoformat() if s['sent_at'] else None}
            for s in self.repo.list_sends(run_id)
        ]
        return {'success': True, 'run': out}

    def list_runs(self, limit: int = 26) -> Dict[str, Any]:
        runs = self.repo.list_runs(limit)
        return {'success': True,
                'runs': [_serialise_run(self._with_week(r)) for r in runs]}

    @staticmethod
    def _with_week(run: Dict[str, Any]) -> Dict[str, Any]:
        """Annotate a run with the week it reports on.

        The week is derived here rather than in the frontend so there is one
        implementation of "which week does an as-of date report?". Duplicating
        the last-complete-week rule in TypeScript would be a second place for it
        to drift from `period.py`.
        """
        as_of = run.get('as_of')
        if as_of is None:
            return run
        begin, end = last_complete_week(as_of, run.get('week_start') or 'monday')
        return {**run, 'week_from': begin, 'week_to': end,
                'week_label': week_label(begin)}

    def get_workbook(self, run_id: int):
        run = self.repo.get_run(run_id, with_workbook=True)
        if run is None or run.get('workbook') is None:
            return None
        return run

    # ==================================================================
    # Approval
    # ==================================================================
    def update_draft(self, run_id: int, subject: str, body: str) -> Dict[str, Any]:
        if not (subject or '').strip():
            return {'success': False, 'error': 'Subject cannot be empty',
                    'code': 'INVALID_INPUT'}
        if not (body or '').strip():
            return {'success': False, 'error': 'Body cannot be empty',
                    'code': 'INVALID_INPUT'}
        if not self.repo.update_email_draft(run_id, subject, body):
            return {'success': False,
                    'error': 'Only a draft awaiting approval, or a historical '
                             'report, can be edited.',
                    'code': 'INVALID_STATE'}
        return {'success': True}

    def approve(self, run_id: int, user_id: int) -> Dict[str, Any]:
        """Sign off the CONTENT. Does not send anything.

        Approval and sending are separate decisions. Approving locks the
        subject, body and figures; it deliberately does NOT require recipients,
        because who receives the report is a standing list edited independently
        and can legitimately be empty at this moment.
        """
        run = self.repo.get_run(run_id)
        if run is None:
            return {'success': False, 'error': 'Run not found', 'code': 'NOT_FOUND'}
        if run['status'] != 'pending_approval':
            return {'success': False,
                    'error': f"Run is {run['status']}, not awaiting approval.",
                    'code': 'INVALID_STATE'}
        if not self.repo.approve_run(run_id, user_id):
            # Lost a race with another approver between the read and the write.
            return {'success': False,
                    'error': 'Run is no longer awaiting approval.',
                    'code': 'INVALID_STATE'}
        return {'success': True, 'status': 'approved'}

    def send(self, run_id: int, user_id: int, mode: Optional[str] = None,
             send_weekday: Optional[int] = None,
             send_time: Optional[str] = None) -> Dict[str, Any]:
        """Queue an approved — or already sent — report for delivery.

        One action for both: sending for the first time and sending again
        differ only in what the row said beforehand. That is why there is one
        Send button rather than a separate "send again".

        `mode` is 'immediate' or 'scheduled'. A schedule supplied here is SAVED
        as the new default, the same way the recipient list is — set it once and
        it stays until changed.

        Recipients ARE required here (unlike approval): a send with no visible
        addressee is refused by the mailer anyway, so it is better refused now.
        """
        run = self.repo.get_run(run_id)
        if run is None:
            return {'success': False, 'error': 'Run not found', 'code': 'NOT_FOUND'}
        # `historical` is a backfilled past week: nothing to approve (it was
        # never a live draft), but it can be forwarded like any sent report.
        if run['status'] not in ('approved', 'sent', 'historical'):
            return {'success': False,
                    'error': f"Run is {run['status']} — approve it before sending."
                             if run['status'] == 'pending_approval' else
                             f"Run is {run['status']} and cannot be sent now.",
                    'code': 'INVALID_STATE'}

        recipients = self.repo.list_recipients()
        if not any(r['kind'] == 'to' for r in recipients):
            return {'success': False,
                    'error': 'Add at least one “To” recipient before sending.',
                    'code': 'NO_RECIPIENTS'}

        settings = self.repo.get_settings() or {}
        mode = mode or settings.get('send_mode') or 'immediate'
        if mode not in ('immediate', 'scheduled'):
            return {'success': False, 'error': f'Unknown send mode: {mode}',
                    'code': 'INVALID_INPUT'}

        if mode == 'scheduled':
            # A schedule chosen at send time becomes the standing default, the
            # same contract as the recipient list.
            fields: Dict[str, Any] = {'send_mode': 'scheduled'}
            if send_weekday is not None:
                if not (isinstance(send_weekday, int) and 0 <= send_weekday <= 6):
                    return {'success': False,
                            'error': 'send_weekday must be 0-6 (Monday=0)',
                            'code': 'INVALID_INPUT'}
                fields['send_weekday'] = send_weekday
            if send_time is not None:
                try:
                    fields['send_time'] = time.fromisoformat(send_time)
                except (TypeError, ValueError):
                    return {'success': False, 'error': 'send_time must be HH:MM',
                            'code': 'INVALID_INPUT'}
            merged = {**settings, **fields}
            if merged.get('send_weekday') is None or merged.get('send_time') is None:
                return {'success': False,
                        'error': 'Scheduled sending needs both a weekday and a time.',
                        'code': 'INVALID_INPUT'}
            self.repo.update_settings(fields)
            settings = merged

        send_at = (now_local() if mode == 'immediate'
                   else next_send_time(now_local(),
                                       settings.get('send_weekday'),
                                       settings.get('send_time')))
        if not self.repo.queue_run(run_id, user_id, send_at):
            return {'success': False,
                    'error': 'This report can no longer be queued.',
                    'code': 'INVALID_STATE'}
        return {'success': True, 'scheduled_send_at': send_at.isoformat(),
                'send_mode': mode}

    # ==================================================================
    # Dispatch
    # ==================================================================
    def dispatch_due(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Send every approved run whose send time has arrived.

        Called by the dispatcher timer. Claiming and sending are separate steps:
        `claim_due_runs` flips each row to `sending` in the same statement that
        selects it, so a slow send that overruns the timer interval cannot have
        the next tick pick up the same run and email the report twice.
        """
        now = now or now_local()
        results = []
        for run_id in self.repo.claim_due_runs(now):
            results.append(self.send_run(run_id))
        return {'success': True, 'dispatched': results}

    def send_run(self, run_id: int) -> Dict[str, Any]:
        """Send one claimed run. Never raises — a failure returns the run to
        `approved` so the next tick retries it."""
        from app.core.mail import MailError, build_message, get_mailer
        from app.core.mail.mailer import mail_identity

        run = self.repo.get_run(run_id, with_workbook=True)
        if run is None:
            return {'run_id': run_id, 'success': False, 'error': 'not found'}

        recipients = self.repo.list_recipients()
        buckets = {'to': [], 'cc': [], 'bcc': []}
        for r in recipients:
            buckets[r['kind']].append(r['email'])
        envelope = buckets['to'] + buckets['cc'] + buckets['bcc']

        if not buckets['to']:
            error = 'No active "to" recipients are configured.'
            self.repo.mark_send_failed(run_id, error)
            self.repo.log_send(run_id, envelope, False, error)
            return {'run_id': run_id, 'success': False, 'error': error}

        try:
            from_addr, from_name = mail_identity()
            attachments = []
            if run.get('workbook'):
                attachments.append((
                    f"periodic_report_{run['as_of']}.xlsx",
                    'application/vnd.openxmlformats-officedocument.'
                    'spreadsheetml.sheet',
                    run['workbook'],
                ))
            # The draft is stored as lightly-marked text; both wire formats
            # are derived from it so they can never disagree.
            draft = run['email_body'] or ''
            message = build_message(
                subject=run['email_subject'] or '',
                body=to_plain_text(draft),
                html=html_document(markdown_to_html(draft)),
                to=buckets['to'], cc=buckets['cc'], bcc=buckets['bcc'],
                from_addr=from_addr, from_name=from_name,
                attachments=attachments)
            detail = get_mailer().send(message, envelope)
        except (MailError, Exception) as e:   # noqa: BLE001
            error = str(e)
            self.repo.mark_send_failed(run_id, error)
            self.repo.log_send(run_id, envelope, False, error)
            print(f'[ERROR] periodic report send failed for run {run_id}: {error}')
            return {'run_id': run_id, 'success': False, 'error': error}

        self.repo.mark_sent(run_id)
        self.repo.log_send(run_id, envelope, True, detail)
        return {'run_id': run_id, 'success': True, 'detail': detail,
                'recipients': len(envelope)}

    # ==================================================================
    # Recipients and settings
    # ==================================================================
    def get_recipients(self) -> Dict[str, Any]:
        return {'success': True,
                'recipients': self.repo.list_recipients(active_only=False)}

    def set_recipients(self, recipients: Any) -> Dict[str, Any]:
        if not isinstance(recipients, list):
            return {'success': False, 'error': 'Expected a list of recipients',
                    'code': 'INVALID_INPUT'}
        seen = set()
        for r in recipients:
            if not isinstance(r, dict) or not (r.get('email') or '').strip():
                return {'success': False, 'error': 'Each recipient needs an email',
                        'code': 'INVALID_INPUT'}
            email = r['email'].strip().lower()
            r['email'] = email
            if '@' not in email or email.startswith('@') or email.endswith('@'):
                return {'success': False, 'error': f'Not a valid email: {email}',
                        'code': 'INVALID_INPUT'}
            kind = r.get('kind', 'to')
            if kind not in ('to', 'cc', 'bcc'):
                return {'success': False, 'error': f'Unknown recipient kind: {kind}',
                        'code': 'INVALID_INPUT'}
            if (email, kind) in seen:
                return {'success': False,
                        'error': f'{email} is listed twice as {kind}',
                        'code': 'INVALID_INPUT'}
            seen.add((email, kind))
        self.repo.replace_recipients(recipients)
        return {'success': True, 'recipients': self.repo.list_recipients(False)}

    def get_settings(self) -> Dict[str, Any]:
        s = self.repo.get_settings()
        if s is None:
            return {'success': False, 'error': 'Settings row is missing — run '
                                               'init_db.py', 'code': 'SERVER_ERROR'}
        return {'success': True, 'settings': {
            **s,
            'send_time': s['send_time'].isoformat() if s['send_time'] else None,
            'updated_at': s['updated_at'].isoformat() if s['updated_at'] else None,
        }}

    def update_settings(self, body: Any) -> Dict[str, Any]:
        if not isinstance(body, dict):
            return {'success': False, 'error': 'Expected a JSON object',
                    'code': 'INVALID_INPUT'}
        fields = {}
        if 'send_mode' in body:
            if body['send_mode'] not in ('immediate', 'scheduled'):
                return {'success': False, 'error': 'send_mode must be '
                        '"immediate" or "scheduled"', 'code': 'INVALID_INPUT'}
            fields['send_mode'] = body['send_mode']
        if 'send_weekday' in body:
            wd = body['send_weekday']
            if wd is not None and not (isinstance(wd, int) and 0 <= wd <= 6):
                return {'success': False, 'error': 'send_weekday must be 0-6 '
                        '(Monday=0) or null', 'code': 'INVALID_INPUT'}
            fields['send_weekday'] = wd
        if 'send_time' in body:
            raw = body['send_time']
            if raw is None:
                fields['send_time'] = None
            else:
                try:
                    fields['send_time'] = time.fromisoformat(raw)
                except (TypeError, ValueError):
                    return {'success': False, 'error': 'send_time must be HH:MM',
                            'code': 'INVALID_INPUT'}
        for key in ('subject_template', 'body_template'):
            if key in body:
                if not (body[key] or '').strip():
                    return {'success': False, 'error': f'{key} cannot be empty',
                            'code': 'INVALID_INPUT'}
                fields[key] = body[key]
        if 'commentary_enabled' in body:
            fields['commentary_enabled'] = bool(body['commentary_enabled'])

        if fields.get('send_mode') == 'scheduled':
            merged = {**(self.repo.get_settings() or {}), **fields}
            if merged.get('send_time') is None or merged.get('send_weekday') is None:
                return {'success': False,
                        'error': 'Scheduled sending needs both send_weekday and '
                                 'send_time.',
                        'code': 'INVALID_INPUT'}

        self.repo.update_settings(fields)
        return self.get_settings()


# ======================================================================
# Helpers
# ======================================================================
_LOWER = {'avg_discount_pct', 'returns_value'}


def _num(value):
    """Decimal -> float for JSON. None passes through."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(round(value, _ROUND_TO))
    return float(value)


def _serialise_run(run: Dict[str, Any]) -> Dict[str, Any]:
    """Dates and timestamps to ISO strings; never emit the workbook bytes."""
    out = {}
    for k, v in run.items():
        if k == 'workbook':
            continue
        out[k] = v.isoformat() if isinstance(v, (date, datetime)) else v
    out['has_workbook'] = run.get('workbook') is not None or None
    return out


def next_send_time(now: datetime, weekday: Optional[int],
                   at: Optional[time]) -> datetime:
    """The next occurrence of `weekday` at `at`, strictly after `now`.

    `now` must be timezone-AWARE and the result carries the same zone, because
    the weekday and time the user picked are meant in THEIR timezone. Combining
    them into a naive value would let the server's own zone decide, which is how
    "00:30" became 00:30 UTC (07:30 in Vietnam).

    Falls back to `now` when either is unset, so a send can never be queued to a
    time that will not arrive.
    """
    if weekday is None or at is None:
        return now
    days_ahead = (weekday - now.weekday()) % 7
    candidate = datetime.combine(now.date() + timedelta(days=days_ahead), at,
                                 tzinfo=now.tzinfo)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def summary_lines(payload: Dict[str, Any]) -> str:
    """A plain-text all-stores summary, one line per period.

    Money is rendered in millions to match the workbook, so a reader comparing
    the email against the attachment sees the same unit.
    """
    lines = []
    # COMMENTARY_LABELS: the email's summary lines mirror what the analysis
    # discusses, and WTD is figures-only.
    for label in COMMENTARY_LABELS:
        period = payload['periods'][label]
        total = period['total']
        sales = total['current'].get('total_sales')
        change = total['change'].get('total_sales', {}).get('value')
        window = f"{period['current']['from']} to {period['current']['to']}"
        sales_txt = '-' if sales is None else f'{sales / 1e6:,.0f}m VND'
        change_txt = '' if change is None else f' ({change:+.1f}%)'
        lines.append(f'{period["label"]} ({window}): {sales_txt}{change_txt}')
    return '\n'.join(lines)


def _vi_date(iso: str) -> str:
    """'2026-09-13' -> '13/09'. The report's audience reads day/month."""
    try:
        _y, m, d = iso.split('-')
        return f'{d}/{m}'
    except (AttributeError, ValueError):
        return iso or ''


def _iso_week(iso: str) -> tuple:
    """(week number, ISO year) for a date string, or ('', '') if unparseable."""
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return '', ''
    cal = d.isocalendar()
    return cal.week, cal.year


def render_template(template: str, payload: Dict[str, Any],
                    commentary: str = '') -> str:
    """Substitute `{{placeholder}}` tokens in a template.

    Available placeholders:
      {{week_no}}        '37'                    ISO week of the reported week
      {{week_year}}      '2026'                  ISO year of that week
      {{prior_week_no}}  '36'                    the comparison week
      {{week_range}}     '07/09–13/09'           the reported week
      {{month_no}}       '9'                     month the MTD window sits in
      {{mtd_range}}      '01/09–13/09'
      {{ytd_range}}      '01/01–13/09'
      {{week_label}}     'Week 37 2026'          English label (subject lines)
      {{as_of}}          '2026-09-14'            the date the job ran
      {{through}}        '2026-09-13'            last day MTD/YTD include
      {{summary}}        one all-stores line per period
      {{commentary}}     the generated three-part analysis

    An unknown token is left untouched rather than raising — a typo in a
    template must not take down the Monday run.
    """
    periods = payload.get('periods') or {}
    wow = periods.get('WOW') or {'current': {}, 'prior': {}}
    mtd = periods.get('MTD') or {'current': {}}
    ytd = periods.get('YTD') or {'current': {}}
    cur, pri = wow.get('current', {}), wow.get('prior', {})

    week_no, week_year = _iso_week(cur.get('from', ''))
    prior_week_no, _ = _iso_week(pri.get('from', ''))
    mtd_from = (mtd.get('current') or {}).get('from', '')
    # The month comes from the MTD window, not from `as_of`: a run on Monday the
    # 1st reports the month that just ended, so taking it from `as_of` would
    # name the wrong month in the opening sentence.
    month_no = mtd_from.split('-')[1].lstrip('0') if '-' in mtd_from else ''

    def _range(block):
        b = block.get('current') or {}
        return f"{_vi_date(b.get('from', ''))}–{_vi_date(b.get('to', ''))}"

    values = {
        'week_no': week_no,
        'week_year': week_year,
        'prior_week_no': prior_week_no,
        'week_range': _range(wow),
        'month_no': month_no,
        'mtd_range': _range(mtd),
        'ytd_range': _range(ytd),
        'week_label': cur.get('label', ''),
        'as_of': payload.get('as_of', ''),
        'through': payload.get('through', ''),
        'summary': summary_lines(payload),
        'commentary': commentary,
    }
    out = template
    for key, value in values.items():
        out = out.replace('{{' + key + '}}', str(value))
    return out
