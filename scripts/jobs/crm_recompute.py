"""
CRM RFM Recompute Job

Standalone script — no Flask context required. Pulls customer transaction
data from Oracle, scores via the luxury hybrid RFM model, and writes the
results to PostgreSQL (crm_customer_scores + crm_segment_snapshots).

Designed to run daily via systemd timer (see scripts/jobs/systemd/).
Can also be triggered manually or via POST /api/v1/crm/admin/recompute.

Usage:
    PYTHONPATH=. python scripts/jobs/crm_recompute.py

Exit codes:
    0 — success
    1 — error (details printed to stderr)
"""
import os
import sys
import time
from collections import Counter
from datetime import date

# Add project root to path so imports work when invoked directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv

# Load environment
env = os.getenv('FLASK_ENV', 'production')
env_file = f'.env.{env}'
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

from app.modules.crm.repository import CRMRepository
from app.modules.crm.rfm import (
    SEGMENTS,
    pool_product_aggregates_by_segment,
    score_customers,
    score_product_groups,
)


def recompute() -> dict:
    """
    Full RFM recompute pipeline. Returns a summary dict.

    Steps:
        1. Fetch customer aggregates from Oracle (RFM raw values)
        2. Fetch top brand/category/breadth from Oracle
        3. Fetch phone numbers from Oracle (DOCUMENT.BT_PRIMARY_PHONE_NO)
        4. Merge supplementary data into customer records
        5. Score all customers (R/F/M scores, weighted, segment)
        6. Write scored customers to Postgres (TRUNCATE + INSERT)
        7. Write monthly segment snapshot to Postgres (UPSERT)
        8. Compute per-segment per-(brand|category) product analysis (CR #48)
        9. Write product scores to Postgres (TRUNCATE + INSERT)

    Returns:
        Dict with keys: success, customers_scored, product_rows_scored,
        segment_counts, oracle_time_s, postgres_time_s, total_time_s
    """
    repo = CRMRepository()
    t_start = time.time()

    # Load RFM weights from DB config (falls back to defaults if not set).
    # Three weight sets (CR #52): w_* segmentation, e_* engagement,
    # pw_* product-analysis ranking.
    weights = repo.get_config()
    # Remap pw_* to the w_* keys that compute_weighted_score expects, so the
    # product scorer can reuse the same helper without changing its
    # signature. Customer scoring keeps using the original `weights` dict.
    product_weights = {
        'w_recency':   weights['pw_recency'],
        'w_frequency': weights['pw_frequency'],
        'w_monetary':  weights['pw_monetary'],
    }
    print(f'  Weights: segmentation={weights["w_recency"]}/{weights["w_frequency"]}/{weights["w_monetary"]}'
          f'  engagement={weights["e_recency"]}/{weights["e_frequency"]}/{weights["e_monetary"]}'
          f'  product={weights["pw_recency"]}/{weights["pw_frequency"]}/{weights["pw_monetary"]}')

    # ------------------------------------------------------------------
    # Step 1-3: Oracle reads (parallelism possible later; sequential for v1)
    # ------------------------------------------------------------------
    t_oracle = time.time()

    print('  [1/9] Fetching customer aggregates from Oracle...', flush=True)
    raw_customers = repo.fetch_customer_aggregates()
    print(f'        → {len(raw_customers):,} customers')

    if not raw_customers:
        return {
            'success': True,
            'customers_scored': 0,
            'product_rows_scored': 0,
            'segment_counts': {s: 0 for s in SEGMENTS},
            'oracle_time_s': round(time.time() - t_oracle, 2),
            'postgres_time_s': 0,
            'total_time_s': round(time.time() - t_start, 2),
        }

    print('  [2/9] Fetching top brand/category/breadth...', flush=True)
    brand_category_map = repo.fetch_top_brand_category()
    print(f'        → {len(brand_category_map):,} entries')

    print('  [3/9] Fetching phone numbers...', flush=True)
    phone_map = repo.fetch_customer_phones()
    print(f'        → {len(phone_map):,} phones')

    oracle_elapsed = round(time.time() - t_oracle, 2)

    # ------------------------------------------------------------------
    # Step 4: Merge supplementary data into customer records
    # ------------------------------------------------------------------
    print('  [4/9] Merging brand/category/phone into customer records...', flush=True)
    for cust in raw_customers:
        sid = cust['customer_sid']
        bc = brand_category_map.get(sid, {})
        cust['top_brand'] = bc.get('top_brand')
        cust['top_category'] = bc.get('top_category')
        cust['category_breadth'] = bc.get('category_breadth', 0)
        cust['phone'] = phone_map.get(sid)

    # ------------------------------------------------------------------
    # Step 5: Score all customers
    # ------------------------------------------------------------------
    print('  [5/9] Scoring customers (RFM + segmentation)...', flush=True)
    scored = score_customers(raw_customers, weights=weights)

    # ------------------------------------------------------------------
    # Step 6-7: Postgres writes
    # ------------------------------------------------------------------
    t_pg = time.time()

    print('  [6/9] Writing scores to Postgres (TRUNCATE + INSERT)...', flush=True)
    inserted = repo.replace_customer_scores(scored)
    print(f'        → {inserted:,} rows inserted')

    # Build segment counts for snapshot
    segment_counts = Counter(c['segment'] for c in scored)
    # Ensure all 7 segments are present (even if count=0)
    for seg in SEGMENTS:
        segment_counts.setdefault(seg, 0)

    snapshot_month = date.today().replace(day=1)
    print(f'  [7/9] Upserting segment snapshot for {snapshot_month}...', flush=True)
    repo.upsert_segment_snapshot(snapshot_month, dict(segment_counts))

    # ------------------------------------------------------------------
    # Step 8-9: Product analysis (CR #48)
    #   Per-segment per-(brand|category) RFM scoring. Aggregate from Oracle
    #   at customer×group grain, pool by segment in Python, quintile-score
    #   within each segment, write back to Postgres.
    # ------------------------------------------------------------------
    customer_segments = {c['customer_sid']: c['segment'] for c in scored}

    print('  [8/9] Computing product analysis (brand + category)...', flush=True)
    t_oracle_2 = time.time()
    product_rows: list = []
    for group_by in ('brand', 'category'):
        per_customer = repo.fetch_product_aggregates(group_by)
        pooled = pool_product_aggregates_by_segment(per_customer, customer_segments)
        for seg, group_list in pooled.items():
            scored_groups = score_product_groups(group_list, weights=product_weights)
            for g in scored_groups:
                product_rows.append({
                    'group_by':       group_by,
                    'segment':        seg,
                    'name':           g['name'],
                    'customer_count': g['customer_count'],
                    'recency_days':   g['recency_days'],
                    'frequency':      g['frequency'],
                    'monetary':       g['monetary'],
                    'r_score':        g['r_score'],
                    'f_score':        g['f_score'],
                    'm_score':        g['m_score'],
                    'weighted_score': g['weighted_score'],
                    'c_score':        g['c_score'],
                    'compound_score': g['compound_score'],
                })
        print(f'        → {group_by}: {sum(len(v) for v in pooled.values()):,} rows '
              f'across {len(pooled)} segments')
    oracle_elapsed += round(time.time() - t_oracle_2, 2)

    print('  [9/9] Writing product scores to Postgres (TRUNCATE + INSERT)...', flush=True)
    product_inserted = repo.replace_product_scores(product_rows)
    print(f'        → {product_inserted:,} rows inserted')

    pg_elapsed = round(time.time() - t_pg, 2)
    total_elapsed = round(time.time() - t_start, 2)

    summary = {
        'success': True,
        'customers_scored': len(scored),
        'product_rows_scored': product_inserted,
        'segment_counts': dict(segment_counts),
        'oracle_time_s': oracle_elapsed,
        'postgres_time_s': pg_elapsed,
        'total_time_s': total_elapsed,
    }
    return summary


def main():
    print(f'CRM RFM Recompute — starting (FLASK_ENV={env})\n')
    try:
        summary = recompute()
    except Exception as e:
        print(f'\n  ERROR: {e}', file=sys.stderr)
        sys.exit(1)

    print(f'\n  Done in {summary["total_time_s"]}s '
          f'(Oracle: {summary["oracle_time_s"]}s, '
          f'Postgres: {summary["postgres_time_s"]}s)')
    print(f'  Customers scored: {summary["customers_scored"]:,}')
    print(f'  Product rows scored: {summary.get("product_rows_scored", 0):,}')
    print(f'  Segment breakdown:')
    for seg in SEGMENTS:
        count = summary['segment_counts'].get(seg, 0)
        print(f'    {seg:12s}: {count:,}')


if __name__ == '__main__':
    main()
