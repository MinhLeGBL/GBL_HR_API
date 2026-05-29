"""
One-time backfill for the handcarry catalog (CR #58 / v0.2.2).

The pre-CR-58 `rps.carrier_item` was a flat (sid, scan_upc) flag list.
After `python scripts/database/init_db.py` adds the new columns, the
existing rows have NULL everywhere except `sid` / `scan_upc`. This script
fills them in:

- Product info (description, brand, category, color, size, season,
  price_before_vat, price_after_vat) → joined from Oracle by UPC.
- `quantity_imported` → for Oracle-known UPCs, set equal to the UPC's
  **lifetime received qty** from Oracle's posted receiving vouchers
  (v0.2.2 — same source Oracle uses for non-hand-carry items). Floors
  to 1 if the UPC has zero receiving records. For orphan UPCs that
  Oracle doesn't know about, default to 1.
- `quantity_sold` (stored override) → set to `quantity_imported` for
  orphan UPCs that Oracle no longer knows about (pre-Oracle items sold
  before the migration cut-off). This forces them to display as
  sold-out (remaining = 0) instead of the live Oracle join's 0.

Idempotent: only touches rows where every CR-58 column is NULL. Re-runs
are no-ops. Use `--force` to re-backfill every row (e.g. to correct
existing rows after the v0.2.2 received-qty rule change).

Usage:
    python scripts/database/backfill_handcarry.py
    python scripts/database/backfill_handcarry.py --dry-run
    python scripts/database/backfill_handcarry.py --force
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
            # Pick up:
            #   (a) rows that have never been backfilled (every column NULL); or
            #   (b) orphan rows from the previous backfill that still have
            #       no quantity_sold override set. The orphan-detection
            #       criterion mirrors the rule used downstream: no
            #       description AND no brand AND quantity_sold IS NULL.
            cur.execute("""
                SELECT sid, scan_upc
                FROM rps.carrier_item
                WHERE (
                        description       IS NULL
                    AND brand             IS NULL
                    AND category          IS NULL
                    AND color             IS NULL
                    AND size              IS NULL
                    AND season            IS NULL
                    AND price_before_vat  IS NULL
                    AND price_after_vat   IS NULL
                )
                   OR (
                        description IS NULL AND brand IS NULL
                    AND quantity_sold IS NULL
                )
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

    # 2. Pull product info + lifetime received from Oracle
    print(f'Querying Oracle product info for {len(upcs)} UPCs...')
    product_info = HandCarryService.fetch_oracle_product_info(upcs)
    print(f'  matched {len(product_info)} / {len(upcs)} in Oracle')

    print(f'Querying Oracle lifetime received qty for {len(upcs)} UPCs...')
    received_by_upc = HandCarryService._lifetime_received_for(upcs)
    print(f'  matched {len(received_by_upc)} / {len(upcs)} have receiving vouchers')

    # 3. Compute the update tuple per row
    updates = []  # list of (description, brand, category, color, size,
                  #          season, quantity_imported, quantity_sold,
                  #          price_before_vat, price_after_vat, sid)
    orphan_count = 0
    for sid, scan_upc in candidates:
        upc = int(scan_upc)
        info = product_info.get(upc)

        # Orphan = Oracle has no master record for this UPC.
        # For orphans, freeze quantity_sold = quantity_imported = 1 so
        # they display as sold-out (remaining = 0).
        # For Oracle-matched items, drive quantity_imported from the
        # receiving voucher sum (v0.2.2 — matches how Oracle tracks
        # imports for every other item). Floor at 1 so the row never
        # shows imported=0.
        is_orphan = info is None
        if is_orphan:
            qty_imp = 1
            quantity_sold_override = 1
            orphan_count += 1
        else:
            received = received_by_upc.get(upc, 0)
            qty_imp = max(1, received)
            quantity_sold_override = None

        info = info or {}
        updates.append((
            info.get('description'),
            info.get('brand'),
            info.get('category'),
            info.get('color'),
            info.get('size'),
            info.get('season'),
            qty_imp,
            quantity_sold_override,
            info.get('price_before_vat'),
            info.get('price_after_vat'),
            int(sid),
        ))

    # 4. Summary
    print(f'\nRows about to be updated:        {len(updates)}')
    print(f'  with Oracle product info:      {len(updates) - orphan_count}')
    print(f'  orphans (no Oracle match):     {orphan_count}')
    print(f'  → orphans get quantity_sold = quantity_imported (sold-out)')

    if args.dry_run:
        print('\n(dry-run; nothing written)')
        for u in updates[:3]:
            print(f'  sid={u[-1]} → qty_imp={u[6]}, qty_sold_override={u[7]}, '
                  f'brand={u[1]!r}')
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
                quantity_sold     = %s,
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
