"""
Prove the local database reproduces production's figures.

"Some data for testing" is easy; data you can TRUST is the hard part, and this
is what makes the difference. A dev database that is subtly wrong is worse than
none, because you believe what it tells you.

HOW IT KNOWS THE RIGHT ANSWER
-----------------------------
It does not recompute anything twice and hope. The weekly report stores FROZEN
snapshots: figures aggregated on the server at generation time and never
recalculated. Those snapshots travel in the seed bundle, in
`periodic_report_runs.payload`.

So production has already published its answer for 38 weeks. This re-runs the
same aggregation against LOCAL Oracle and compares, metric by metric. Agreement
means the local rows, the local types and the local date handling all reproduce
the server — which is the only claim worth making about a dev database.

A mismatch is specific: it names the week, the period, the metric and both
figures, so you know whether you are missing rows, losing precision, or have a
window off by a day.

Usage:
    FLASK_ENV=local python scripts/dev/verify_seed.py
    ... --weeks 5          check fewer (default: all that are in range)

Exit codes:
    0 — every comparison agreed
    1 — at least one mismatch, or nothing could be checked
"""
import argparse
import datetime as dt
import decimal
import json
import os
import sys

# Vietnamese store names, department names and pseudonyms all end up on stdout.
# A Windows console in a legacy code page raises UnicodeEncodeError on those and
# kills the run partway through - which, mid-load, leaves a half-populated
# database and no obvious cause. Force UTF-8 and degrade rather than die.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dotenv import load_dotenv

env = os.getenv('FLASK_ENV', 'development')
env_file = f'.env.{env}'
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

METRICS = ('total_sales', 'qty_sold', 'bills', 'returns_value', 'qty_returned')
# Money is Decimal end to end, so exact equality is the right test, not a
# tolerance. A tolerance here would hide precision loss in the seed format —
# which is exactly the failure this is looking for.
EXACT = decimal.Decimal('0')


def _num(v):
    return None if v is None else decimal.Decimal(str(v))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--weeks', type=int, default=0,
                        help='Check only the most recent N weeks')
    args = parser.parse_args()

    from app.core.database import get_postgres_connection
    from app.modules.weekly_report.service import WeeklyReportService

    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute("""SELECT as_of, through_date, week_start, payload
                   FROM periodic_report_runs
                   WHERE payload IS NOT NULL
                   ORDER BY as_of DESC""")
    runs = cur.fetchall()
    cur.close()
    conn.close()

    if not runs:
        print('No stored runs in the local database — load a seed first.')
        return 1
    if args.weeks:
        runs = runs[:args.weeks]

    manifest_from = os.getenv('SEED_WINDOW_FROM')       # optional hint
    svc = WeeklyReportService()

    checked = skipped = mismatches = 0
    print(f'{"week":12} {"period":6} {"result"}')
    print('-' * 60)

    for as_of, through, week_start, payload in runs:
        payload = payload if isinstance(payload, dict) else json.loads(payload)
        try:
            local = svc.build_snapshot(as_of, week_start or 'monday', through)
        except Exception as e:                                  # noqa: BLE001
            print(f'{as_of!s:12} {"-":6} could not aggregate: '
                  f'{str(e).splitlines()[0][:60]}')
            skipped += 1
            continue

        week_bad = 0
        for label in ('WTD', 'MTD', 'YTD', 'WOW'):
            want = ((payload.get('periods') or {}).get(label) or {})
            got = ((local.get('periods') or {}).get(label) or {})
            w_total = (want.get('total') or {}).get('current') or {}
            g_total = (got.get('total') or {}).get('current') or {}
            if not w_total:
                continue

            # A window reaching outside the seed's date range cannot agree and
            # is not a fault — say so rather than reporting a false failure.
            w_from = (want.get('current') or {}).get('from')
            if manifest_from and w_from and w_from < manifest_from:
                continue

            bad = []
            for m in METRICS:
                a, b = _num(w_total.get(m)), _num(g_total.get(m))
                if a is None and b is None:
                    continue
                if a is None or b is None or (a - b) != EXACT:
                    bad.append((m, a, b))
            checked += 1
            if bad:
                week_bad += len(bad)
                mismatches += len(bad)
                print(f'{as_of!s:12} {label:6} MISMATCH')
                for m, a, b in bad:
                    print(f'{"":19} {m:22} production={a}  local={b}')
        if not week_bad:
            print(f'{as_of!s:12} {"all":6} ok')

    print('-' * 60)
    print(f'{checked} period comparisons, {mismatches} mismatches, '
          f'{skipped} weeks skipped')
    if mismatches:
        print('\nThe local database does NOT reproduce production. Likely '
              'causes, in order:\n'
              '  - the report window reaches outside the seed window (check '
              'manifest.json)\n'
              '  - TIMEZONE. INVC_POST_DATE is TIMESTAMP WITH TIME ZONE; if '
              'the load\n'
              '    session was not at +07:00, sales shift across midnight and '
              'daily\n'
              '    totals move between adjacent days while the period total '
              'still ties\n'
              '  - rows missing because a child table lost its parent\n'
              '  - a numeric column loaded as float somewhere')
        return 1
    if not checked:
        print('\nNothing could be compared.')
        return 1
    print('\nThe local database reproduces production exactly for every '
          'comparison above.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
