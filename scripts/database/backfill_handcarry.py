"""
One-time backfill for the handcarry catalog (CR #58).

The pre-CR-58 `rps.carrier_item` was a flat (sid, scan_upc) flag list.
After `python scripts/database/init_db.py` adds the new columns, the
existing rows have NULL everywhere except `sid` / `scan_upc`. This script
fills them in:

- Product info (description, brand, category, color, size, season,
  price_before_vat, price_after_vat) → joined from Oracle by UPC.
- `quantity_imported` → set equal to the UPC's **lifetime sold qty**.
  If the UPC has zero sales, default to 1 (per CR #58 answer:
  "we treat old import = sale, if there are no sale then we leave
  import value at 1").

Idempotent: only touches rows where every CR-58 column is NULL. Re-runs
are no-ops.

Usage:
    python scripts/database/backfill_handcarry.py
    python scripts/database/backfill_handcarry.py --dry-run
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv

env = os.getenv('FLASK_ENV', 'development')
env_file = f'.env.{env}'
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

from app.core.database import get_postgres_connection
from app.modules.handcarry.service import HandCarryService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be updated without writing.',
    )
    parser.add_argument(
        '--force', action='store_true',
        help='Backfill all rows, even those that already have non-NULL '
             'CR-58 columns. Default: only rows where every new column is NULL.',
    )
    args = parser.parse_args()

    # 1. Pull candidate rows from Postgres
    conn = get_postgres_connection()
    if conn is None:
        print('FAILED to connect to PostgreSQL')
        sys.exit(1)

    try:
        cur = conn.cursor()
        if args.force:
            cur.execute('SELECT sid, scan_upc FROM rps.carrier_item ORDER BY sid')
        else:
            cur.execute("""
                SELECT sid, scan_upc
                FROM rps.carrier_item
                WHERE description       IS NULL
                  AND brand             IS NULL
                  AND category          IS NULL
                  AND color             IS NULL
                  AND size              IS NULL
                  AND season            IS NULL
                  AND quantity_imported IS NULL
                  AND price_before_vat  IS NULL
                  AND price_after_vat   IS NULL
                ORDER BY sid
            """)
        candidates = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    print(f'Candidates to backfill: {len(candidates)}')
    if not candidates:
        print('Nothing to do.')
        return

    upcs = [int(r[1]) for r in candidates]

    # 2. Pull product info + lifetime sold from Oracle
    print(f'Querying Oracle product info for {len(upcs)} UPCs...')
    product_info = HandCarryService.fetch_oracle_product_info(upcs)
    print(f'  matched {len(product_info)} / {len(upcs)} in Oracle')

    print(f'Querying Oracle lifetime sold qty for {len(upcs)} UPCs...')
    sold_by_upc = HandCarryService._lifetime_sold_for(upcs)
    print(f'  matched {len(sold_by_upc)} / {len(upcs)} have sales history')

    # 3. Compute the update tuple per row
    updates = []  # list of (description, brand, category, color, size,
                  #          season, quantity_imported, price_before_vat,
                  #          price_after_vat, sid)
    for sid, scan_upc in candidates:
        upc = int(scan_upc)
        info = product_info.get(upc, {})
        sold = sold_by_upc.get(upc, 0)
        qty_imp = sold if sold > 0 else 1   # ← CR #58 answer
        updates.append((
            info.get('description'),
            info.get('brand'),
            info.get('category'),
            info.get('color'),
            info.get('size'),
            info.get('season'),
            qty_imp,
            info.get('price_before_vat'),
            info.get('price_after_vat'),
            int(sid),
        ))

    # 4. Summary
    matched = sum(1 for u in updates if u[0] or u[1] or u[2] or u[3] or u[4] or u[5])
    print(f'\nRows about to be updated:        {len(updates)}')
    print(f'  with any Oracle product info:  {matched}')
    print(f'  with no Oracle match:          {len(updates) - matched}')
    print(f'  qty_imported = sold_qty (>0):  {sum(1 for u in updates if u[6] > 1 or (u[6] == 1 and sold_by_upc.get(int(candidates[i][1]), 0) > 0 for i in range(len(candidates))))}'  # noqa
          if False else '')

    if args.dry_run:
        print('\n(dry-run; nothing written)')
        # Show a few sample rows
        for u in updates[:3]:
            print(f'  sid={u[-1]} → qty_imp={u[6]}, brand={u[1]!r}, season={u[5]!r}, price_after_vat={u[8]}')
        return

    # 5. Apply
    print('\nApplying updates...')
    conn = get_postgres_connection()
    if conn is None:
        print('FAILED to reconnect for write')
        sys.exit(1)
    try:
        cur = conn.cursor()
        cur.executemany("""
            UPDATE rps.carrier_item SET
                description       = %s,
                brand             = %s,
                category          = %s,
                color             = %s,
                size              = %s,
                season            = %s,
                quantity_imported = %s,
                price_before_vat  = %s,
                price_after_vat   = %s
            WHERE sid = %s
        """, updates)
        conn.commit()
        cur.close()
    finally:
        conn.close()

    print(f'Done — {len(updates)} rows updated.')


if __name__ == '__main__':
    main()
