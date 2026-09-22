"""
One-off data migration — point the email's opening sentence at the year-on-year
week.

Why this is needed
------------------
On 2026-09-22 the weekly report's first part changed from week-over-week (week
38 against week 37) to year-on-year (week 38 of this year against week 38 of
last year). `{{prior_week_no}}` therefore now resolves to the SAME number as
`{{week_no}}`.

The stored template is not in git — it lives in `periodic_report_settings` — so
the code change alone leaves the live email announcing:

    "... gồm ba phần: Tuần 38 so với Tuần 38, ..."

which reads like a bug and contradicts the analysis below it. This rewrites that
one sentence to carry both years.

Safety
------
- **Dry run by default.** Nothing is written without `--apply`.
- **Only rewrites the exact sentence it knows.** A template that has been edited
  by hand does not match, is left completely alone, and is printed so a human
  can decide. It will never clobber the company's own wording.
- **Idempotent.** Once migrated the pattern no longer matches, so re-running is
  a no-op.

Usage:
    PYTHONPATH=. python scripts/database/migrate_weekly_report_yoy_template.py
    PYTHONPATH=. python scripts/database/migrate_weekly_report_yoy_template.py --apply

Exit codes:
    0 — migrated, or already migrated, or nothing to do
    1 — the template has been customised and needs a human
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

from app.core.database import get_postgres_connection  # noqa: E402

OLD = 'Tuần {{week_no}} so với Tuần {{prior_week_no}}'
NEW = 'Tuần {{week_no}}/{{week_year}} so với Tuần {{prior_week_no}}/{{prior_week_year}}'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--apply', action='store_true',
                        help='Write the change. Without it, nothing is saved.')
    args = parser.parse_args()

    conn = get_postgres_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT body_template FROM periodic_report_settings '
                        'WHERE id = 1')
            row = cur.fetchone()
            if row is None:
                print('No settings row — nothing to migrate.')
                return 0

            body = row[0] or ''
            if NEW in body:
                print('Already migrated — the sentence already carries both '
                      'years. Nothing to do.')
                return 0
            if OLD not in body:
                print('The stored template does not contain the sentence this '
                      'migration knows how to rewrite, so NOTHING has been '
                      'changed. It has probably been edited by hand.\n')
                print('Current template:')
                print('-' * 68)
                print(body)
                print('-' * 68)
                print('\nCheck the opening sentence names the comparison the '
                      'report now makes: week N of this year against week N of '
                      'LAST year, not against week N-1. Edit it on the '
                      'settings page if it does not.')
                return 1

            updated = body.replace(OLD, NEW)
            print('Rewriting the opening sentence:\n')
            print(f'  - {OLD}')
            print(f'  + {NEW}\n')
            if not args.apply:
                print('DRY RUN — nothing written. Re-run with --apply.')
                return 0

            cur.execute('UPDATE periodic_report_settings '
                        'SET body_template = %s, updated_at = NOW() '
                        'WHERE id = 1', (updated,))
            conn.commit()
            print('Applied.')
            return 0
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
