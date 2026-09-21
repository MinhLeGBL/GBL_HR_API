"""
Weekly Periodic Report — generation job.

Standalone script — no Flask context required. Aggregates the week that just
closed out of Oracle, renders the .xlsx, drafts the email, and stores the lot as
a run awaiting approval. Nothing is emailed here; that needs a human to approve
it first (see periodic_report_dispatch.py).

Designed to run Monday 03:00 via systemd timer (see scripts/jobs/systemd/).

Why 03:00 and not midnight
--------------------------
The week closes at Sunday midnight, but sales posted late on Sunday keep landing
in Oracle for a while afterwards. Generating at 03:00 gives them time to arrive.
The figures are frozen into a snapshot at generation, so a sale that posts after
the job runs will NOT appear in the report that gets emailed — which is why the
job does not run the instant the week ends.

Why `--through` defaults to the closing Sunday
---------------------------------------------
The week sheet already reports the last complete week. MTD and YTD would
otherwise run to the Monday the job fires on — a day with essentially no sales
at 03:00 — while the prior-year side of the comparison carries a full trading
day. That understates both, severely in the first days of a month. Ending them
on the Sunday that closes the reported week compares complete days with complete
days.

Usage:
    PYTHONPATH=. python scripts/jobs/periodic_report_generate.py
    PYTHONPATH=. python scripts/jobs/periodic_report_generate.py --as-of 2026-09-14

Exit codes:
    0 — a run was stored and is awaiting approval
    1 — generation failed (a `failed` run is recorded so the page can show why)
"""
import argparse
import os
import sys
from datetime import date

# Add project root to path so imports work when invoked directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv

env = os.getenv('FLASK_ENV', 'production')
env_file = f'.env.{env}'
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

from app.modules.weekly_report.period import last_complete_week  # noqa: E402
from app.modules.weekly_report.service import WeeklyReportService  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--as-of', default=None,
                        help='Run date, YYYY-MM-DD (default: today)')
    parser.add_argument('--through', default=None,
                        help='Last day MTD/YTD include (default: the Sunday '
                             'that closes the reported week)')
    parser.add_argument('--week-start', choices=('monday', 'sunday'),
                        default='monday')
    parser.add_argument('--no-email-draft', action='store_true',
                        help='Skip drafting the email (figures only)')
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    if args.through:
        through = date.fromisoformat(args.through)
    else:
        # The closing Sunday of the week being reported.
        through = last_complete_week(as_of, args.week_start)[1]

    week_begin, week_end = last_complete_week(as_of, args.week_start)
    print(f'Periodic report: as-of {as_of}, reporting {week_begin} .. {week_end}, '
          f'MTD/YTD through {through}')

    result = WeeklyReportService().generate_run(
        as_of=as_of, week_start=args.week_start, through=through,
        draft_email=not args.no_email_draft)

    if not result.get('success'):
        print(f'FAILED: {result.get("error")}', file=sys.stderr)
        sys.exit(1)

    print(f'OK: run {result["run_id"]} stored for {result["as_of"]}, '
          f'awaiting approval')


if __name__ == '__main__':
    main()
