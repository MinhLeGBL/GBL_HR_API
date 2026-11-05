"""
Weekly Periodic Report — backfill past weeks as historical reports.

Standalone script — no Flask context required. Generates a `historical` report
for every closed Mon-Sun week in a date range, so past weeks can be browsed and
sent from the week selector.

NO ANALYSIS IS GENERATED and no API call is made. The LLM analysis is paid for
once per report, by the Monday job; history is figures, workbook and the email
template only. A person can write the analysis for any single week via Edit.

One Oracle fetch covers the whole range; each week is then aggregated in memory.
That is one query against the live database instead of one history-spanning
query per week.

Existing runs are NEVER overwritten — weeks already generated, including ones
carrying a real paid analysis, are skipped. Safe to re-run.

Usage:
    PYTHONPATH=. python scripts/jobs/periodic_report_backfill.py \\
        --from 2026-01-01 --to 2026-09-13
    PYTHONPATH=. python scripts/jobs/periodic_report_backfill.py \\
        --from 2026-01-01 --dry-run

Exit codes:
    0 — every week was created or already existed
    1 — at least one week failed
"""
import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv

env = os.getenv('FLASK_ENV', 'production')
env_file = f'.env.{env}'
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

from app.modules.weekly_report.period import (  # noqa: E402
    fetch_span, last_complete_week, period_windows)
from app.modules.weekly_report.service import WeeklyReportService  # noqa: E402


def weeks_between(start: date, end: date):
    """Every closed Mon-Sun week whose last day falls in [start, end]."""
    # First Sunday on or after `start`.
    sunday = start + timedelta(days=(6 - start.weekday()) % 7)
    out = []
    while sunday <= end:
        out.append((sunday - timedelta(days=6), sunday))
        sunday += timedelta(days=7)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--from', dest='date_from', required=True,
                        help='Earliest week END to include, YYYY-MM-DD')
    parser.add_argument('--to', dest='date_to', default=None,
                        help='Latest week END to include (default: the last '
                             'closed week)')
    parser.add_argument('--overwrite', action='store_true',
                        help='Re-render weeks this job produced (status '
                             'historical). Approved or sent weeks are still '
                             'skipped — they carry analysis this job cannot '
                             'write, and recipients have read it.')
    parser.add_argument('--dry-run', action='store_true',
                        help='List the weeks and what would happen; write nothing')
    args = parser.parse_args()

    start = date.fromisoformat(args.date_from)
    end = (date.fromisoformat(args.date_to) if args.date_to
           else last_complete_week(date.today())[1])
    weeks = weeks_between(start, end)
    if not weeks:
        print('No closed weeks in that range.')
        return

    service = WeeklyReportService()
    stored = {w: service.repo.get_run_by_as_of(w[1] + timedelta(days=1))
              for w in weeks}
    if args.overwrite:
        # Only weeks this job owns are re-renderable; the rest stay put.
        existing = [w for w, run in stored.items()
                    if run is not None and run['status'] != 'historical']
        redo = [w for w, run in stored.items()
                if run is not None and run['status'] == 'historical']
    else:
        existing = [w for w, run in stored.items() if run is not None]
        redo = []
    todo = [w for w in weeks if w not in existing and w not in redo]

    print(f'{len(weeks)} weeks from {weeks[0][0]} to {weeks[-1][1]}: '
          f'{len(existing)} kept, {len(redo)} to re-render, '
          f'{len(todo)} to create.')
    for begin, finish in weeks:
        tag = ('kept' if (begin, finish) in existing
               else 're-render' if (begin, finish) in redo else 'create')
        print(f'  [{tag}] ISO week {begin.isocalendar().week:>2}  '
              f'{begin} .. {finish}')

    # Weeks that will actually be written: new ones, plus re-renders.
    work = todo + redo

    if args.dry_run or not work:
        print('\nDry run — nothing written.' if args.dry_run else '\nNothing to do.')
        return

    # One fetch spanning every window of every week being written.
    spans = [fetch_span(period_windows(f + timedelta(days=1), 'monday', f))
             for _b, f in work]
    fetch_from = min(s[0] for s in spans)
    fetch_to = max(s[1] for s in spans)
    print(f'\nFetching {fetch_from} .. {fetch_to} once ...')
    dept_rows, store_rows = service.repo.fetch_sales(fetch_from, fetch_to)
    print(f'  -> {len(dept_rows)} day x store x dept rows, '
          f'{len(store_rows)} day x store rows')

    failures = protected = 0
    for begin, finish in sorted(work):
        try:
            # 'monday' throughout this script, matching the window maths above.
            result = service.backfill_week(finish, dept_rows, store_rows,
                                           'monday',
                                           overwrite=args.overwrite)
            if result.get('skipped'):
                # Asked for, but not this job's to overwrite — it carries
                # analysis the backfill cannot write.
                protected += bool(result.get('protected'))
                state = f"skipped ({result.get('status', 'exists')})"
            else:
                state = f"run {result['run_id']}"
            print(f'  ok   {begin} .. {finish}  ({state})')
        except Exception as e:   # noqa: BLE001 — keep going; report at the end
            failures += 1
            print(f'  FAIL {begin} .. {finish}: {e}', file=sys.stderr)

    print(f'\n{len(work) - failures - protected} written, '
          f'{protected} protected, {failures} failed.')
    if protected:
        print('Protected weeks carry an analysis this job does not write. '
              'Re-render them with:\n'
              '  periodic_report_generate.py --as-of <date> --force '
              '--keep-analysis')
    if failures:
        sys.exit(1)


if __name__ == '__main__':
    main()
