"""
CRM Segment Trend Backfill (CR #41)

One-time script: retroactively computes RFM segment distributions for
the past 12 months (May 2025 – Mar 2026) and inserts them into
crm_segment_snapshots. Safe to re-run — uses ON CONFLICT UPSERT.

Does NOT overwrite the current month's snapshot (only backfills months
where no snapshot exists or updates existing backfilled months).

Does NOT update crm_customer_scores — only writes snapshot counts.

Usage:
    PYTHONPATH=. python scripts/jobs/crm_backfill_trends.py
"""
import os
import sys
import time
import calendar
from collections import Counter
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv

env = os.getenv('FLASK_ENV', 'production')
env_file = f'.env.{env}'
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

from app.modules.crm.repository import CRMRepository
from app.modules.crm.rfm import SEGMENTS, score_customers


def backfill_month(repo: CRMRepository, year: int, month: int,
                   weights: dict = None) -> dict:
    """
    Compute RFM segments "as of" the last day of the given month.

    Only fetches aggregates + brand/category (no phone — not needed for
    segment classification). Scores customers and returns segment counts.
    """
    last_day = calendar.monthrange(year, month)[1]
    as_of = datetime(year, month, last_day, 23, 59, 59)
    snapshot_month = date(year, month, 1)

    # Fetch aggregates as of month-end
    raw_customers = repo.fetch_customer_aggregates_as_of(as_of)
    if not raw_customers:
        # No customers in the window for this month — all segments 0
        segment_counts = {seg: 0 for seg in SEGMENTS}
        repo.upsert_segment_snapshot(snapshot_month, segment_counts)
        return {'month': str(snapshot_month), 'customers': 0, 'segments': segment_counts}

    # Fetch brand/category (needed for category_breadth, though not for segment classification)
    brand_category_map = repo.fetch_top_brand_category_as_of(as_of)

    for cust in raw_customers:
        sid = cust['customer_sid']
        bc = brand_category_map.get(sid, {})
        cust['top_brand'] = bc.get('top_brand')
        cust['top_category'] = bc.get('top_category')
        cust['category_breadth'] = bc.get('category_breadth', 0)
        cust['phone'] = None  # Not needed for backfill

    # Score
    scored = score_customers(raw_customers, weights=weights)

    # Count segments
    segment_counts = Counter(c['segment'] for c in scored)
    for seg in SEGMENTS:
        segment_counts.setdefault(seg, 0)

    # Upsert snapshot
    repo.upsert_segment_snapshot(snapshot_month, dict(segment_counts))

    return {
        'month': str(snapshot_month),
        'customers': len(scored),
        'segments': dict(segment_counts),
    }


def main():
    print(f'CRM Segment Trend Backfill — starting (FLASK_ENV={env})\n')
    repo = CRMRepository()
    weights = repo.get_config()
    print(f'  Weights: weighted={weights["w_recency"]}/{weights["w_frequency"]}/{weights["w_monetary"]}')
    t_start = time.time()

    # Backfill May 2025 through March 2026 (11 months)
    # April 2026 is the current month — already populated by daily recompute
    months_to_backfill = []
    y, m = 2025, 5
    while (y, m) <= (2026, 3):
        months_to_backfill.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    for year, month in months_to_backfill:
        label = f'{year}-{month:02d}'
        print(f'  [{label}] Computing RFM as of month-end...', end=' ', flush=True)
        t = time.time()
        try:
            result = backfill_month(repo, year, month, weights=weights)
            elapsed = time.time() - t
            print(f'{result["customers"]:,} customers, {elapsed:.1f}s')
            # Show segment counts on one line
            segs = ' | '.join(f'{s[:3]}:{result["segments"].get(s, 0)}'
                              for s in SEGMENTS)
            print(f'           {segs}')
        except Exception as e:
            print(f'ERROR: {e}')

    total = time.time() - t_start
    print(f'\n  Done in {total:.1f}s')


if __name__ == '__main__':
    main()
