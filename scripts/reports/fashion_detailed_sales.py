"""
Fashion detailed sales report — one row per individual transactioned item,
plus one row per fashion UPC with NO 2026 transactions.

Period: 2026-01-01 -> today.

Scope (fashion only):
  - EXCLUDE fine-jewelry vendors:
      VHN, ROM, ATS, VIS, LUI, NAN, NAK, SPK, TED, BRT
  - EXCLUDE suitcase vendors:
      TVL, TIT
  - EXCLUDE HEA products in COSM department (boss-classified as "other"
    per CLAUDE.md commission rules)
  - EXCLUDE HOME department entirely (decor / lighting — not fashion)
  - EXCLUDE catalog ghost SKUs (i.first_rcvd_date IS NULL — master-only
    entries that never physically existed; matches v0.4.0 convention
    from /sizes report)

Row granularity:
  - For each UPC WITH 2026 transactions: one row per `rps.document_item`
    line, with UPC metadata repeated on every transaction row.
  - For each UPC WITHOUT 2026 transactions: ONE row with the UPC
    metadata + import figures populated and all transaction-side columns
    (store_code, bill_no, bill_date, qty_sold, revenue, discounts,
    associate, etc.) NULL.

Output:
  document/fashion_detailed_sales_2026-01-01_to_<YYYY-MM-DD>.csv
  document/fashion_detailed_sales_2026-01-01_to_<YYYY-MM-DD>.xlsx
"""
import csv
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from app.core.database import get_oracle_connection


# Vendor exclusion lists (mirrors commission classifier in CLAUDE.md).
JEWELRY_VENDORS = ('VHN', 'ROM', 'ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'SPK', 'TED', 'BRT')
SUITCASE_VENDORS = ('TVL', 'TIT')

_JEWELRY_LIST_SQL = ", ".join(f"'{v}'" for v in JEWELRY_VENDORS)
_SUITCASE_LIST_SQL = ", ".join(f"'{v}'" for v in SUITCASE_VENDORS)


# Per-UPC `qty_imported` is computed as:
#
#   inventory_at_2026_01_01  +  imported_since_2026_01_01
#
# `inventory_at_2026_01_01` is reverse-walked from the CURRENT on-hand
# inventory (`rps.invn_sbs_item_qty.qty` summed across all stores), not
# forward-built from historical receipt vs sale events. The forward
# build had two failure modes:
#   - it depended on enumerating every "import" mechanism (POs +
#     adjustment vouchers + transfers + ...), and any missed channel
#     produced spurious negatives
#   - even after adding adj_type=0 adjustments, ~1% of items still
#     showed negative inv@start due to other unaccounted-for movements
#     (returns to vendor, mis-tagged transfers, etc.)
#
# Reverse-walking from current inventory is robust by construction —
# whatever moved the stock (PO / adjustment / transfer / vendor return /
# manual edit / anything else), it's already baked into the current qty.
# We only need to subtract movements WE CAN see between 2026-01-01 and
# today to recover the 2026-01-01 state:
#
#   inv@start =   current_qty
#               + net sold since 2026-01-01
#               - PO received since 2026-01-01
#               - adjustment received since 2026-01-01
#
# `imported_since_2026` = PO_rcvd_since + adj_rcvd_since (unchanged).
# `qty_imported` = inv@start + imported_since_2026.
#
# Notes:
#   - adj_type=1 / adj_type=2 are price/metadata adjustments, NOT
#     inventory — verified via master totals (qty unchanged across
#     43k+ rows 2024+). Only adj_type=0, cdt=8 affects on-hand qty.
#   - Qty for adj_type=0 lives in `adj_item.adj_value` for ~85% of
#     rows and in `cmp_qty` for ~15% — GREATEST() picks whichever is
#     populated.
#   - Negative inv@start is now possible only when there's net OUT
#     movement since 2026-01-01 that we can't see (e.g. transfers to
#     external location, write-offs without adj records). Should be
#     rare in practice.
#
# Landed cost is taken from the item master (i.UDF1_STRING) — stable
# per-unit value, not a per-PO weighted average.
QUERY = rf"""
    WITH current_inv AS (
        SELECT
            q.invn_sbs_item_sid                                          AS item_sid,
            SUM(NVL(q.qty, 0))                                           AS current_qty
        FROM rps.invn_sbs_item_qty q
        GROUP BY q.invn_sbs_item_sid
    ),
    po_received_since AS (
        SELECT
            pi.item_sid                                                  AS item_sid,
            SUM(pi.rcvd_qty)                                             AS rcvd_since_2026
        FROM rps.po_item pi
        WHERE pi.rcvd_qty IS NOT NULL AND pi.rcvd_qty > 0
          AND pi.post_date >= DATE '2026-01-01'
        GROUP BY pi.item_sid
    ),
    adj_received_since AS (
        -- adj_type=0, cdt=8 = manual inventory-in voucher (phiếu nhập).
        -- Qty lives in adj_value (most rows) or cmp_qty (some rows);
        -- GREATEST captures whichever field is populated.
        SELECT
            ai.item_sid                                                  AS item_sid,
            SUM(GREATEST(NVL(ai.adj_value, 0), NVL(ai.cmp_qty, 0)))      AS adj_since_2026
        FROM rps.adjustment a
        JOIN rps.adj_item ai ON ai.adj_sid = a.sid
        WHERE a.adj_type = 0
          AND a.creating_doc_type = 8
          AND a.status = 4
          AND a.post_date >= DATE '2026-01-01'
        GROUP BY ai.item_sid
    ),
    sold_since_2026 AS (
        SELECT
            di.invn_sbs_item_sid                                         AS item_sid,
            SUM(CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) AS net_sold
        FROM rps.document d
        JOIN rps.document_item di ON d.sid = di.doc_sid
        WHERE d.status = 4
          AND d.receipt_type IN (0, 1)
          AND di.item_type IN (1, 2)
          AND d.invc_post_date >= :start_date
          AND d.invc_post_date <  :end_date
        GROUP BY di.invn_sbs_item_sid
    ),
    txns_2026 AS (
        -- Pre-filter to all transactioned items in scope. The outer SELECT
        -- LEFT-JOINs this so items with zero rows here still appear as a
        -- single UPC-only row with transaction columns NULL.
        SELECT
            di.invn_sbs_item_sid                                         AS item_sid,
            d.store_code,
            d.doc_no,
            d.invc_post_date,
            d.receipt_type,
            d.disc_perc                                                  AS bill_disc_perc,
            di.sid                                                       AS doc_item_sid,
            di.item_size,
            di.attribute,
            di.item_type,
            di.qty,
            di.price,
            di.tax_amt,
            di.orig_price,
            di.orig_tax_amt,
            di.disc_perc                                                 AS item_disc_perc,
            di.employee1_full_name                                       AS associate
        FROM rps.document d
        JOIN rps.document_item di ON d.sid = di.doc_sid
        WHERE d.status = 4
          AND d.receipt_type IN (0, 1)
          AND di.item_type IN (1, 2)
          AND d.invc_post_date >= :start_date
          AND d.invc_post_date <  :end_date
    )
    SELECT
        -- ITEM / UPC INFO (always populated, even for no-txn UPCs)
        TO_CHAR(i.upc)                                                       AS upc,
        i.text1                                                              AS description,
        v.vend_code                                                          AS vend_code,
        v.vend_name                                                          AS brand,
        i.udf5_string                                                        AS season,
        ie.udf8_string                                                       AS category,
        SUBSTR(i.description2, INSTR(i.description2, '-', -1) + 1)           AS sub_category,
        dep.d_long_name                                                      AS department,
        -- Per-UPC import breakdown — inventory-anchored formula:
        -- inv@start = current_qty + sold_since - received_since
        (COALESCE(ci.current_qty, 0)
         + COALESCE(ss.net_sold, 0)
         - COALESCE(pr.rcvd_since_2026, 0)
         - COALESCE(ar.adj_since_2026, 0))                                   AS inventory_at_2026_start,
        (COALESCE(pr.rcvd_since_2026, 0) + COALESCE(ar.adj_since_2026, 0))   AS imported_since_2026,
        -- qty_imported = inv@start + imported_since  (telescopes to
        -- current_qty + sold_since — kept as the sum-of-the-two for
        -- readability against the column definitions)
        (COALESCE(ci.current_qty, 0) + COALESCE(ss.net_sold, 0))             AS qty_imported,
        CASE
            WHEN REGEXP_LIKE(i.udf1_string, '^[0-9]+(\.[0-9]+)?$')
            THEN TO_NUMBER(i.udf1_string)
        END                                                                  AS landed_cost_per_unit,

        -- TRANSACTION INFO (NULL for UPCs without 2026 transactions)
        t.store_code                                                         AS store_code,
        t.doc_no                                                             AS bill_no,
        TO_CHAR(t.invc_post_date, 'YYYY-MM-DD')                              AS bill_date,
        TO_CHAR(t.invc_post_date, 'YYYY-MM')                                 AS bill_month,
        -- LINE-LEVEL receipt classification (based on item_type, not doc):
        -- An EXCHANGE bill is one SALE document containing both sale lines
        -- (item_type=1) and return lines (item_type=2) — e.g. bill 3925
        -- RWD 2026-03-20 swapping UPC 226307 for UPC 226306. The negative
        -- qty / revenue on the return leg should read 'RETURN', not
        -- 'SALE'. Doc-level d.receipt_type is intentionally NOT surfaced.
        CASE t.item_type
            WHEN 1 THEN 'SALE'
            WHEN 2 THEN 'RETURN'
            ELSE NULL END                                                    AS receipt_type,
        t.doc_item_sid                                                       AS doc_item_sid,
        t.item_size                                                          AS item_size,
        t.attribute                                                          AS attribute,
        -- Signed qty: returns count as negative.
        (CASE WHEN t.item_type = 2 THEN t.qty * -1 ELSE t.qty END)           AS qty_sold,

        -- Revenue (non-VAT) = qty * (price - tax_amt) * (1 - bill_disc/100)
        -- Retail Pro stores `price` with the ITEM discount applied but
        -- NOT the bill-level discount. We multiply by (1 - bill_disc%)
        -- to recover the actual customer-paid revenue. Without this,
        -- bills with bill-level discounts (e.g. bill 5982 RWT 2026-01-04
        -- with bill_disc=100% on top of item_disc=60%) showed positive
        -- revenue on lines that were given away free.
        ROUND(
            (CASE WHEN t.item_type = 2 THEN t.qty * -1 ELSE t.qty END)
            * (t.price - t.tax_amt)
            * (1 - t.bill_disc_perc / 100), 0)                               AS revenue_non_vat,

        -- Per-line bill-level + item-level raw discount percentages.
        t.bill_disc_perc                                                     AS bill_disc_perc,
        t.item_disc_perc                                                     AS item_disc_perc,

        -- Combined effective discount rate (% off full price), 2 decimals.
        -- Matches the commission formula: 1 - (1-itemDisc)(1-billDisc).
        ROUND(
            (1 - (1 - t.item_disc_perc / 100) * (1 - t.bill_disc_perc / 100)) * 100,
            2)                                                               AS effective_disc_perc,

        -- Discount amount (non-VAT) = qty * (orig_price - tax_amt) * eff_rate
        -- (Matches DISC_AMT in nak_detailed_sales.py — non-VAT side.)
        ROUND(
            (CASE WHEN t.item_type = 2 THEN t.qty * -1 ELSE t.qty END)
            * (t.orig_price - t.orig_tax_amt)
            * (1 - (1 - t.item_disc_perc / 100) * (1 - t.bill_disc_perc / 100)),
            0)                                                               AS disc_amount_non_vat,

        -- Full-price reference (non-VAT) — useful for sanity checks.
        ROUND(t.orig_price - t.orig_tax_amt, 0)                              AS full_price_non_vat,

        -- Employee attribution (associate 1 is the commissioning employee).
        t.associate                                                          AS associate

    FROM rps.invn_sbs_item i
    JOIN rps.vendor v             ON v.sid = i.vend_sid
    JOIN rps.dcs dep              ON dep.sid = i.dcs_sid
    LEFT JOIN rps.invn_sbs_extend ie ON ie.invn_sbs_item_sid = i.sid
    LEFT JOIN current_inv         ci ON ci.item_sid = i.sid
    LEFT JOIN po_received_since   pr ON pr.item_sid = i.sid
    LEFT JOIN adj_received_since  ar ON ar.item_sid = i.sid
    LEFT JOIN sold_since_2026     ss ON ss.item_sid = i.sid
    LEFT JOIN txns_2026           t  ON t.item_sid  = i.sid

    -- Scope: an item is in the inventory chain if ANY of the following holds.
    -- (`first_rcvd_date` alone is PO-biased — Retail Pro doesn't set it on
    -- adjustment-in receipts, so items received via "phiếu nhập" need
    -- the inventory OR sales fallbacks to be included. Catalog ghost
    -- SKUs that fail all three conditions are excluded by construction.)
    WHERE (
            i.first_rcvd_date IS NOT NULL
         OR COALESCE(ci.current_qty, 0) > 0
         OR ss.net_sold IS NOT NULL
         OR ar.adj_since_2026 IS NOT NULL
         OR pr.rcvd_since_2026 IS NOT NULL
      )

      -- Fashion-only exclusions (now on item-master vendor, not txn vendor)
      AND v.vend_code NOT IN ({_JEWELRY_LIST_SQL})
      AND v.vend_code NOT IN ({_SUITCASE_LIST_SQL})
      AND NOT (dep.d_long_name = 'COSM' AND v.vend_code = 'HEA')
      AND dep.d_long_name <> 'HOME'

    ORDER BY i.upc, t.invc_post_date NULLS FIRST, t.store_code, t.doc_no
"""


def _write_csv(rows, columns, path):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(row)


def _write_xlsx(rows, columns, path):
    try:
        from openpyxl import Workbook
    except ImportError:
        print("[WARN] openpyxl not installed — skipping .xlsx output. "
              "Install with: pip install openpyxl")
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = 'Fashion Sales'
    ws.append(columns)
    for row in rows:
        ws.append(list(row))
    ws.freeze_panes = 'A2'
    # Auto-size columns (cap at 50 chars).
    for i, col in enumerate(columns, start=1):
        header_len = len(str(col))
        sample_len = max(
            (len(str(r[i - 1])) for r in rows[:200] if r[i - 1] is not None),
            default=0,
        )
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = (
            min(max(header_len, sample_len) + 2, 50)
        )
    wb.save(path)
    return True


def main():
    start_date = datetime(2026, 1, 1)
    end_date = datetime.now()

    output_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', 'document')
    )
    os.makedirs(output_dir, exist_ok=True)

    base_name = (
        f"fashion_detailed_sales_"
        f"{start_date.strftime('%Y-%m-%d')}_to_"
        f"{end_date.strftime('%Y-%m-%d')}"
    )
    csv_path = os.path.join(output_dir, base_name + '.csv')
    xlsx_path = os.path.join(output_dir, base_name + '.xlsx')

    print(f"Connecting to Oracle...")
    conn = get_oracle_connection()
    if conn is None:
        print("ERROR: Could not connect to Oracle.")
        sys.exit(1)

    try:
        cursor = conn.cursor()
        print(
            f"Running query (Fashion only, "
            f"{start_date.date()} -> {end_date.date()})..."
        )
        cursor.execute(QUERY, {'start_date': start_date, 'end_date': end_date})

        columns = [col[0].lower() for col in cursor.description]
        rows = cursor.fetchall()
        print(f"  fetched {len(rows):,} rows")

        print(f"Writing CSV...")
        _write_csv(rows, columns, csv_path)
        print(f"  {csv_path}")

        print(f"Writing Excel...")
        if _write_xlsx(rows, columns, xlsx_path):
            print(f"  {xlsx_path}")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
