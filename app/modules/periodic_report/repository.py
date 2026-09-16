"""Periodic report repository — Oracle sales fetch + Postgres run state.

Two very different jobs behind one class, matching how `reports` pairs its
Oracle queries with its Postgres pins:

* `fetch_sales` reads the day x store x department grain out of Retail Pro.
* everything else is CRUD on `periodic_report_*`, where a generated run lives
  from the moment the Monday job writes it until it has been emailed.

One connection per call, always closed in `finally`.
"""
import json
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import oracledb

from app.core.database import get_oracle_connection, get_postgres_connection
from .period import merge_department
from .queries import QUERY_BY_DEPT, QUERY_BY_STORE

ZERO = Decimal(0)

# Columns shared by every run read, in a fixed order so the row unpacking below
# stays in step with the SELECTs.
_RUN_COLUMNS = (
    'id, as_of, week_start, through_date, status, payload, '
    'email_subject, email_body, error, '
    'generated_at, approved_by, approved_at, scheduled_send_at, sent_at, '
    'commentary_error'
)


def _decimal_output_handler(cursor, name, default_type, size, precision, scale):
    """Return Oracle NUMBERs as Decimal instead of float.

    Money is summed across thousands of day x store x department rows. In
    float64 that accumulates ~1e-12 relative drift, which showed up as a
    sub-VND disagreement with a single-shot query over the same range. Oracle's
    NUMBER is exact decimal, so keeping it exact all the way through makes the
    report reconcile to the unit.
    """
    if default_type == oracledb.DB_TYPE_NUMBER:
        return cursor.var(Decimal, arraysize=cursor.arraysize)
    return None


class PeriodicReportRepository:

    # ------------------------------------------------------------------
    # Oracle — sales data
    # ------------------------------------------------------------------
    def fetch_sales(self, from_date, to_date):
        """Pull both grains over [from_date, to_date] inclusive.

        Returns (dept_rows, store_rows). The caller slices these into period
        windows in memory — one Oracle round trip serves all six windows.
        """
        binds = {'from_date': from_date,
                 'to_exclusive': to_date + timedelta(days=1)}

        conn = get_oracle_connection()
        # get_oracle_connection() returns None on failure rather than raising.
        # Surface a clear error so the service maps it to SERVER_ERROR, and keep
        # the finally safe (no None.close()).
        if conn is None:
            raise RuntimeError('Oracle connection unavailable')
        try:
            conn.outputtypehandler = _decimal_output_handler
            cur = conn.cursor()

            cur.execute(QUERY_BY_DEPT, binds)
            dept_rows = []
            for (day, store_code, store_name, dept,
                 s_net, r_net, s_gross, qty, qty_ret, bills) in cur:
                dept_rows.append({
                    'day': day.date() if hasattr(day, 'date') else day,
                    'store_code': store_code,
                    'store_name': store_name,
                    'department': merge_department(dept),
                    'sale_net': s_net or ZERO,
                    'return_net': r_net or ZERO,
                    'sale_gross': s_gross or ZERO,
                    'qty_sold': qty or ZERO,
                    'qty_returned': qty_ret or ZERO,
                    'dept_bills': int(bills or 0),
                })

            cur.execute(QUERY_BY_STORE, binds)
            store_rows = []
            for day, store_code, bills in cur:
                store_rows.append({
                    'day': day.date() if hasattr(day, 'date') else day,
                    'store_code': store_code,
                    'bills': int(bills or 0),
                })

            cur.close()
        finally:
            conn.close()

        return dept_rows, store_rows

    # ------------------------------------------------------------------
    # Postgres — run state
    # ------------------------------------------------------------------
    def _connect(self):
        conn = get_postgres_connection()
        if conn is None:
            raise RuntimeError('Postgres connection unavailable')
        return conn

    @staticmethod
    def _run_from_row(row, workbook=None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        (run_id, as_of, week_start, through_date, status, payload,
         subject, body, error, generated_at, approved_by, approved_at,
         scheduled_send_at, sent_at, commentary_error) = row
        run = {
            'id': run_id,
            'as_of': as_of,
            'week_start': week_start,
            'through_date': through_date,
            'status': status,
            'payload': payload,
            'email_subject': subject,
            'email_body': body,
            'error': error,
            'generated_at': generated_at,
            'approved_by': approved_by,
            'approved_at': approved_at,
            'scheduled_send_at': scheduled_send_at,
            'sent_at': sent_at,
            'commentary_error': commentary_error,
        }
        if workbook is not None:
            run['workbook'] = workbook
        return run

    def create_run(self, *, as_of, week_start, through_date, status, payload,
                   workbook: Optional[bytes], email_subject: Optional[str],
                   email_body: Optional[str], error: Optional[str],
                   commentary_error: Optional[str] = None) -> int:
        """Insert (or replace) the run for `as_of` and return its id.

        One run per as-of date: re-running the job for a date that already has a
        run replaces it, so a retry after a failure — or a manual regenerate —
        does not leave two rows competing to be "this week's report". A run that
        has already been sent is never replaced; the service checks that first.
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO periodic_report_runs
                        (as_of, week_start, through_date, status, payload,
                         workbook, email_subject, email_body, error,
                         commentary_error, generated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (as_of) DO UPDATE SET
                        week_start        = EXCLUDED.week_start,
                        through_date      = EXCLUDED.through_date,
                        status            = EXCLUDED.status,
                        payload           = EXCLUDED.payload,
                        workbook          = EXCLUDED.workbook,
                        email_subject     = EXCLUDED.email_subject,
                        email_body        = EXCLUDED.email_body,
                        error             = EXCLUDED.error,
                        commentary_error  = EXCLUDED.commentary_error,
                        generated_at      = NOW(),
                        approved_by       = NULL,
                        approved_at       = NULL,
                        scheduled_send_at = NULL
                    RETURNING id
                ''', (as_of, week_start, through_date, status,
                      json.dumps(payload) if payload is not None else None,
                      workbook, email_subject, email_body, error,
                      commentary_error))
                run_id = cur.fetchone()[0]
            conn.commit()
            return run_id
        finally:
            conn.close()

    def get_run(self, run_id: int, with_workbook: bool = False):
        cols = _RUN_COLUMNS + (', workbook' if with_workbook else '')
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT {cols} FROM periodic_report_runs WHERE id = %s',
                    (run_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._run_from_row(row[:15], row[15] if with_workbook else None)

    def get_run_by_as_of(self, as_of):
        """The run for one as-of date, if any. One run per date — see
        `create_run`."""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT {_RUN_COLUMNS} FROM periodic_report_runs '
                    'WHERE as_of = %s',
                    (as_of,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return self._run_from_row(row)

    def get_latest_run(self):
        """The most recently generated run, whatever its status."""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT {_RUN_COLUMNS} FROM periodic_report_runs '
                    'ORDER BY as_of DESC LIMIT 1'
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return self._run_from_row(row)

    def list_runs(self, limit: int = 26) -> List[Dict[str, Any]]:
        """Recent runs, newest first. Payload and workbook are omitted — this
        feeds a history list, and a payload per row would be megabytes."""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT id, as_of, week_start, through_date, status,
                           generated_at, approved_by, approved_at,
                           scheduled_send_at, sent_at, error, commentary_error
                    FROM periodic_report_runs
                    ORDER BY as_of DESC
                    LIMIT %s
                ''', (limit,))
                rows = cur.fetchall()
        finally:
            conn.close()
        return [{
            'id': r[0], 'as_of': r[1], 'week_start': r[2], 'through_date': r[3],
            'status': r[4], 'generated_at': r[5], 'approved_by': r[6],
            'approved_at': r[7], 'scheduled_send_at': r[8], 'sent_at': r[9],
            'error': r[10], 'commentary_error': r[11],
        } for r in rows]

    def list_sends(self, run_id: int) -> List[Dict[str, Any]]:
        """Every send attempt for one run, oldest first.

        This is the record of what ACTUALLY went out and to whom. The current
        recipient list is not a substitute: it changes over time, so showing
        today's addresses beside a report sent months ago would claim people
        received something they never did.
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT id, recipients, success, detail, sent_at
                    FROM periodic_report_sends
                    WHERE run_id = %s
                    ORDER BY sent_at
                ''', (run_id,))
                rows = cur.fetchall()
        finally:
            conn.close()
        return [{'id': r[0], 'recipients': list(r[1] or []), 'success': r[2],
                 'detail': r[3], 'sent_at': r[4]} for r in rows]

    def update_email_draft(self, run_id: int, subject: str, body: str) -> bool:
        """Save edits to the draft. Only while the run is awaiting approval —
        once approved, the text is what was approved and must not move under it.
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    UPDATE periodic_report_runs
                       SET email_subject = %s, email_body = %s,
                           commentary_error = NULL
                     WHERE id = %s AND status = 'pending_approval'
                ''', (subject, body, run_id))
                changed = cur.rowcount
            conn.commit()
            return changed > 0
        finally:
            conn.close()

    def approve_run(self, run_id: int, user_id: int, scheduled_send_at) -> bool:
        """Mark a pending run approved. Guarded on the current status so two
        approvals racing cannot both win — the second updates zero rows."""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    UPDATE periodic_report_runs
                       SET status = 'approved',
                           approved_by = %s,
                           approved_at = NOW(),
                           scheduled_send_at = %s
                     WHERE id = %s AND status = 'pending_approval'
                ''', (user_id, scheduled_send_at, run_id))
                changed = cur.rowcount
            conn.commit()
            return changed > 0
        finally:
            conn.close()

    def claim_due_runs(self, now) -> List[int]:
        """Atomically claim approved runs whose send time has arrived.

        The UPDATE ... RETURNING moves each row to `sending` in the same
        statement that selects it, so a dispatcher that overruns its 15-minute
        timer cannot have a second instance pick up the same run and send the
        report twice.
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    UPDATE periodic_report_runs
                       SET status = 'sending'
                     WHERE id IN (
                         SELECT id FROM periodic_report_runs
                          WHERE status = 'approved'
                            AND scheduled_send_at <= %s
                          ORDER BY as_of
                            FOR UPDATE SKIP LOCKED
                     )
                    RETURNING id
                ''', (now,))
                ids = [r[0] for r in cur.fetchall()]
            conn.commit()
            return ids
        finally:
            conn.close()

    def mark_sent(self, run_id: int) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    UPDATE periodic_report_runs
                       SET status = 'sent', sent_at = NOW(), error = NULL
                     WHERE id = %s
                ''', (run_id,))
            conn.commit()
        finally:
            conn.close()

    def mark_send_failed(self, run_id: int, error: str) -> None:
        """Return a failed send to `approved` so the next dispatcher tick retries
        it. The error is kept so the page can show why the last attempt failed."""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    UPDATE periodic_report_runs
                       SET status = 'approved', error = %s
                     WHERE id = %s
                ''', (error[:2000], run_id))
            conn.commit()
        finally:
            conn.close()

    def log_send(self, run_id: int, recipients: List[str], success: bool,
                 detail: Optional[str]) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    INSERT INTO periodic_report_sends
                        (run_id, recipients, success, detail, sent_at)
                    VALUES (%s, %s, %s, %s, NOW())
                ''', (run_id, recipients, success,
                      detail[:2000] if detail else None))
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Recipients and settings
    # ------------------------------------------------------------------
    def list_recipients(self, active_only: bool = True) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT id, email, name, kind, is_active '
                    'FROM periodic_report_recipients '
                    + ('WHERE is_active ' if active_only else '')
                    + 'ORDER BY kind, email'
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [{'id': r[0], 'email': r[1], 'name': r[2], 'kind': r[3],
                 'is_active': r[4]} for r in rows]

    def replace_recipients(self, recipients: List[Dict[str, Any]]) -> None:
        """Replace the whole list in one transaction — the UI edits it as a
        list, so a diff would only add ways for it to end up half-applied."""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM periodic_report_recipients')
                for r in recipients:
                    cur.execute('''
                        INSERT INTO periodic_report_recipients
                            (email, name, kind, is_active)
                        VALUES (%s, %s, %s, %s)
                    ''', (r['email'], r.get('name'), r.get('kind', 'to'),
                          r.get('is_active', True)))
            conn.commit()
        finally:
            conn.close()

    def get_settings(self) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    SELECT send_mode, send_time, send_weekday, subject_template,
                           body_template, commentary_enabled, updated_at
                    FROM periodic_report_settings WHERE id = 1
                ''')
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return {'send_mode': row[0], 'send_time': row[1],
                'send_weekday': row[2], 'subject_template': row[3],
                'body_template': row[4], 'commentary_enabled': row[5],
                'updated_at': row[6]}

    def update_settings(self, fields: Dict[str, Any]) -> None:
        allowed = ('send_mode', 'send_time', 'send_weekday', 'subject_template',
                   'body_template', 'commentary_enabled')
        sets = [f'{k} = %s' for k in allowed if k in fields]
        if not sets:
            return
        values = [fields[k] for k in allowed if k in fields]
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    'UPDATE periodic_report_settings SET '
                    + ', '.join(sets) + ', updated_at = NOW() WHERE id = 1',
                    values,
                )
            conn.commit()
        finally:
            conn.close()
