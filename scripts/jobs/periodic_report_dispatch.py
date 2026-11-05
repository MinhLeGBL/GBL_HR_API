"""
Weekly Periodic Report — send dispatcher.

Standalone script — no Flask context required. Sends every APPROVED run whose
scheduled send time has arrived, with the .xlsx attached, then marks it sent.

A run only reaches this script after a human approved it in the app. Approval
with send mode 'immediate' queues it for now, so the next tick picks it up
within a few minutes; 'scheduled' queues it for the configured weekday and time.

Designed to run every 15 minutes via systemd timer (see scripts/jobs/systemd/).

Safety properties worth knowing:
  * Runs are CLAIMED atomically (status -> 'sending' in the same statement that
    selects them), so a send that overruns the timer interval cannot be picked
    up twice and emailed twice.
  * A failed send returns the run to 'approved' and records the error, so the
    next tick retries it rather than dropping the week on the floor.
  * Outside FLASK_ENV=production the mailer defaults to dry-run: it logs the
    message instead of sending, so running this against a copy of the database
    cannot email real recipients. Set MAIL_BACKEND explicitly to override.

Usage:
    PYTHONPATH=. python scripts/jobs/periodic_report_dispatch.py
    PYTHONPATH=. python scripts/jobs/periodic_report_dispatch.py --run-id 12

Exit codes:
    0 — nothing was due, or everything due was sent
    1 — at least one send failed (it stays queued for the next tick)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv

env = os.getenv('FLASK_ENV', 'production')
env_file = f'.env.{env}'
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

from app.modules.weekly_report.service import WeeklyReportService  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--run-id', type=int, default=None,
                        help='Send one specific run now, bypassing the schedule '
                             '(it must already be approved)')
    args = parser.parse_args()

    service = WeeklyReportService()

    if args.run_id is not None:
        results = [service.send_run(args.run_id)]
    else:
        results = service.dispatch_due().get('dispatched', [])

    if not results:
        print('Nothing due.')
        return

    failures = 0
    for r in results:
        if r.get('success'):
            print(f'  run {r["run_id"]}: {r.get("detail")}')
        else:
            failures += 1
            print(f'  run {r["run_id"]}: FAILED — {r.get("error")}',
                  file=sys.stderr)

    print(f'{len(results) - failures} sent, {failures} failed.')
    if failures:
        sys.exit(1)


if __name__ == '__main__':
    main()
