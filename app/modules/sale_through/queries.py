"""
Oracle SQL queries for the Sale-Through Report.

Source-of-truth identity (per item):
    on_hand = imported - sold - transferred_out + adjustments + in_transit_in - returns_to_vendor

Column mapping from RetailPro/RPS schema:
    - imported_qty  → voucher receipts (vou_class=0, status=4) net of vendor returns
    - sold_qty      → document item_type 1/2 (sale - return)
    - on_hand_qty   → invn_sbs_item_qty.qty (current snapshot per store)
    - slip_qty      → outgoing transfer slips (qty leaving the store)
    - intran_qty    → in-transit transfer slips (status=3, slip_flag=1)
    - adj_qty       → inventory adjustments (adj_value - orig_value)

Brand / Season / Category source columns:
    - brand     → vendor.vend_name              (joined via invn_sbs_item.vend_sid)
    - season    → invn_sbs_item.UDF5_STRING     (e.g. "SS25", "FW24")
    - category  → invn_sbs_extend.udf8_string   (joined to invn_sbs_item.sid)
    - department → dcs.d_long_name              (joined via invn_sbs_item.dcs_sid)
    - supplier  → invn_sbs_extend.udf6_string

The item-level query was verified against direct table counts on 2026-05-04
(see /tmp/test_inventory_fullcompare.py): 55,303 rows, identity holds.
"""


class SaleThroughQueries:
    """Oracle queries for the Sale-Through report module."""

    # -----------------------------------------------------------------
    # 1. Item-level inventory + activity rollup
    # -----------------------------------------------------------------
    # One row per (UPC, brand, season, category, item attributes, price).
    # All quantity columns are NVL'd to 0. No row filter — caller decides
    # whether to drop zero-on-hand items.
    ITEM_INVENTORY = """
        SELECT
            TO_CHAR(i.upc) AS upc,
            v.vend_name AS vendor_name,
            d.d_long_name AS department,
            i.UDF5_STRING AS season,
            ie.udf6_string AS supplier,
            ie.udf8_string AS category,
            i.description1 AS item_name,
            i.description2 AS description2,
            i.alu AS alu,
            i.text1 AS item_description,
            i.text5 AS material,
            i.description4 AS color_vendor,
            i.item_size AS item_size,
            ip.price AS price,
            i.cost AS cost,
            NVL(SUM(iq.qty), 0) AS on_hand,
            NVL(SUM(CASE WHEN st.store_code = 'RHN' THEN iq.qty END), 0) AS oh_rhn,
            NVL(SUM(CASE WHEN st.store_code = 'RWR' THEN iq.qty END), 0) AS oh_rwr,
            NVL(SUM(CASE WHEN st.store_code = 'RWT' THEN iq.qty END), 0) AS oh_rwt,
            NVL(SUM(CASE WHEN st.store_code = 'RWD' THEN iq.qty END), 0) AS oh_rwd,
            NVL(SUM(CASE WHEN st.store_code = 'HQ'  THEN iq.qty END), 0) AS oh_hq,
            NVL(SUM(CASE WHEN st.store_code = 'RWL' THEN iq.qty END), 0) AS oh_rwl,
            NVL(SUM(CASE WHEN st.store_code = 'RWP' THEN iq.qty END), 0) AS oh_rwp,
            NVL(SUM(CASE WHEN st.store_code = 'LOA' THEN iq.qty END), 0) AS oh_loa,
            NVL(SUM(sli.qty), 0) AS slip_qty,
            NVL(SUM(sal.qty), 0) AS sold_qty,
            NVL(SUM(vou.qty), 0) AS imported_qty,
            NVL(SUM(adj.qty), 0) AS adj_qty,
            NVL(SUM(intran.qty), 0) AS intran_qty
        FROM rps.invn_sbs_item i
        LEFT JOIN rps.subsidiary s
               ON i.sbs_sid = s.sid
        LEFT JOIN rps.invn_sbs_item_qty iq
               ON i.sbs_sid = iq.sbs_sid
              AND i.sid = iq.invn_sbs_item_sid
        LEFT JOIN rps.store st
               ON iq.sbs_sid = st.sbs_sid
              AND iq.store_sid = st.sid
              AND s.sid = st.sbs_sid
        LEFT JOIN rps.dcs d
               ON i.sbs_sid = d.sbs_sid
              AND i.dcs_sid = d.sid
        LEFT JOIN rps.invn_sbs_price ip
               ON i.sbs_sid = ip.sbs_sid
              AND i.sid = ip.invn_sbs_item_sid
        LEFT JOIN rps.vendor v
               ON i.sbs_sid = v.sbs_sid
              AND i.vend_sid = v.sid
        LEFT JOIN rps.invn_sbs_extend ie
               ON i.sid = ie.invn_sbs_item_sid
        LEFT JOIN (
            SELECT a.sbs_no, a.store_no, b.invn_sbs_item_sid,
                   SUM(DECODE(b.item_type, 2, b.qty * -1, b.qty)) AS qty
              FROM rps.document a
              INNER JOIN rps.document_item b ON a.sid = b.doc_sid
              INNER JOIN rps.subsidiary s    ON a.sbs_no = s.sbs_no
              INNER JOIN rps.store st        ON a.store_no = st.store_no
             WHERE a.is_held = 0
               AND a.receipt_type IN (0, 1)
               AND a.status = 4
               AND b.item_type IN (1, 2)
             GROUP BY a.sbs_no, a.store_no, b.invn_sbs_item_sid
        ) sal
            ON s.sbs_no = sal.sbs_no
           AND st.store_no = sal.store_no
           AND iq.invn_sbs_item_sid = sal.invn_sbs_item_sid
        LEFT JOIN (
            SELECT s.sbs_no, st.store_no, b.item_sid,
                   SUM(DECODE(a.vou_type, 1, b.qty * -1, b.qty)) qty
              FROM rps.voucher a, rps.vou_item b, rps.subsidiary s, rps.store st
             WHERE a.sid = b.vou_sid
               AND a.status = 4
               AND a.vou_type IN (0, 1)
               AND a.vou_class = 0
               AND a.slip_flag = 0
               AND a.held = 0
               AND a.sbs_sid = s.sid
               AND a.sbs_sid = st.sbs_sid
               AND a.store_sid = st.sid
             GROUP BY s.sbs_no, st.store_no, b.item_sid
        ) vou
            ON s.sbs_no = vou.sbs_no
           AND st.store_no = vou.store_no
           AND iq.invn_sbs_item_sid = vou.item_sid
        LEFT JOIN (
            SELECT s.sbs_no, st.store_no, b.item_sid,
                   SUM(adj_value - orig_value) qty
              FROM rps.adjustment a, rps.adj_item b, rps.subsidiary s, rps.store st
             WHERE a.sid = b.adj_sid
               AND a.adj_type = 0
               AND s.sbs_no >= 0
               AND a.held = 0
               AND a.sbs_sid = s.sid
               AND s.sid = st.sbs_sid
               AND a.store_sid = st.sid
             GROUP BY s.sbs_no, st.store_no, b.item_sid
        ) adj
            ON s.sbs_no = adj.sbs_no
           AND st.store_no = adj.store_no
           AND iq.invn_sbs_item_sid = adj.item_sid
        LEFT JOIN (
            SELECT s.sbs_no, st.store_no, b.item_sid,
                   SUM(DECODE(a.vou_type, 1, b.qty * -1, b.qty)) qty
              FROM rps.voucher a, rps.vou_item b, rps.subsidiary s, rps.store st
             WHERE a.sid = b.vou_sid
               AND a.status = 3
               AND a.vou_type IN (0, 1)
               AND a.vou_class IN (0, 2)
               AND a.slip_flag = 1
               AND a.held = 0
               AND a.verified = 0
               AND a.active = 1
               AND a.sbs_sid = s.sid
               AND a.sbs_sid = st.sbs_sid
               AND a.store_sid = st.sid
             GROUP BY s.sbs_no, st.store_no, b.item_sid
        ) intran
            ON s.sbs_no = intran.sbs_no
           AND st.store_no = intran.store_no
           AND iq.invn_sbs_item_sid = intran.item_sid
        LEFT JOIN (
            SELECT s.sbs_no, st.store_no, b.item_sid, SUM(b.qty) qty
              FROM rps.slip a, rps.slip_item b, rps.subsidiary s, rps.store st
             WHERE a.sid = b.slip_sid
               AND a.held = 0
               AND a.out_sbs_sid = s.sid
               AND s.sid = st.sbs_sid
               AND a.out_store_sid = st.sid
             GROUP BY s.sbs_no, st.store_no, b.item_sid
        ) sli
            ON s.sbs_no = sli.sbs_no
           AND st.store_no = sli.store_no
           AND iq.invn_sbs_item_sid = sli.item_sid
        GROUP BY
            TO_CHAR(i.upc),
            v.vend_name,
            d.d_long_name,
            i.UDF5_STRING,
            ie.udf6_string,
            ie.udf8_string,
            i.description1,
            i.description2,
            i.alu,
            i.text1,
            i.text5,
            i.description4,
            i.item_size,
            ip.price,
            i.cost
    """

    # -----------------------------------------------------------------
    # 2. Brand × Season × Category aggregation (the v0.1 report)
    # -----------------------------------------------------------------
    # Wraps ITEM_INVENTORY and rolls up to (vendor, season, category).
    # Returns NULL groupings as-is (UI/service can label them 'Unknown').
    BRAND_SEASON_CATEGORY = f"""
        SELECT
            vendor_name,
            season,
            category,
            COUNT(*) AS sku_count,
            SUM(imported_qty) AS imported_qty,
            SUM(sold_qty)     AS sold_qty,
            SUM(on_hand)      AS on_hand_qty,
            SUM(slip_qty)     AS transferred_out_qty,
            SUM(intran_qty)   AS in_transit_qty,
            SUM(adj_qty)      AS adjustment_qty
        FROM ({ITEM_INVENTORY})
        GROUP BY vendor_name, season, category
    """

    # -----------------------------------------------------------------
    # 3. Purchase Order line-level detail
    # -----------------------------------------------------------------
    # One row per PO line. Joins:
    #   po → po_item                       — header to line
    #   po.vendor_sid → vendor             — brand
    #   po_item.item_sid → invn_sbs_item   — UPC, ALU, season (UDF5_STRING),
    #                                        base/landed cost
    #   invn_sbs_item.sid → invn_sbs_extend — category (UDF8), supplier (UDF6)
    #   invn_sbs_item.dcs_sid → dcs        — department long name
    #
    # Cost convention (confirmed with the team):
    #   - base_cost   = invn_sbs_item.cost / po_item.cost
    #                   (FOB / supplier invoice price; treat 0 as "not set")
    #   - landed_cost = invn_sbs_item.UDF1_STRING
    #                   (after tax + transport; stored as NVARCHAR2 — must
    #                    REGEXP_LIKE-guard before TO_NUMBER, otherwise the
    #                    332 non-numeric rows trigger ORA-01722)
    #
    # Caveat: landed_cost lives on the item master, not the PO line, so it
    # reflects the *current* landed cost for the SKU, not the historical
    # landing cost at the time the PO was raised. Fine for "what's open / what
    # are we exposed to today"; not appropriate for back-dated PO valuation.
    #
    # Verified 2026-05-04: 29,217 lines / 429 POs / 147 vendors / 13 seasons.
    # Header↔line qty totals reconcile (49,182 ord / 44,811 rcvd / 4,371 due).
    # 99.6% of lines have at least one of base or landed cost.
    PO_LINES = r"""
        SELECT
            -- PO header
            po.po_no,
            po.created_by,
            po.created_datetime,
            po.modified_by,
            po.modified_datetime,
            po.po_total,
            po.note,

            -- Vendor (brand)
            v.vend_code,
            v.vend_name,

            -- PO line
            pi.item_pos,
            pi.ord_qty,
            pi.rcvd_qty,
            pi.ord_qty - COALESCE(pi.rcvd_qty, 0) AS due_qty,

            -- Item master identifiers / descriptors
            TO_CHAR(i.upc) AS upc,
            i.alu,
            i.description1 AS item_name,
            i.description2,
            i.description4 AS color_vendor,
            i.item_size,
            i.text1 AS item_description,
            i.text5 AS material,

            -- Item attributes (department × season × category × supplier)
            d.d_long_name AS department,
            i.UDF5_STRING AS season,
            ie.udf6_string AS supplier,
            ie.udf8_string AS category,

            -- Costs
            NULLIF(pi.cost, 0) AS base_cost,
            pi.fc_cost AS base_cost_fc,
            pi.orig_cost AS base_cost_orig,
            pi.currency_sid,
            CASE
                WHEN REGEXP_LIKE(i.UDF1_STRING, '^[0-9]+(\.[0-9]+)?$')
                THEN TO_NUMBER(i.UDF1_STRING)
            END AS landed_cost,

            -- Computed line values (NULL when the underlying cost is missing)
            pi.ord_qty * NULLIF(pi.cost, 0) AS ord_base_value,
            CASE
                WHEN REGEXP_LIKE(i.UDF1_STRING, '^[0-9]+(\.[0-9]+)?$')
                THEN pi.ord_qty * TO_NUMBER(i.UDF1_STRING)
            END AS ord_landed_value,
            pi.rcvd_qty * NULLIF(pi.cost, 0) AS rcvd_base_value,
            CASE
                WHEN REGEXP_LIKE(i.UDF1_STRING, '^[0-9]+(\.[0-9]+)?$')
                THEN pi.rcvd_qty * TO_NUMBER(i.UDF1_STRING)
            END AS rcvd_landed_value
        FROM rps.po po
        JOIN rps.po_item pi    ON pi.po_sid = po.sid
        JOIN rps.vendor v      ON po.vendor_sid = v.sid
        JOIN rps.invn_sbs_item i ON pi.item_sid = i.sid
        LEFT JOIN rps.invn_sbs_extend ie ON ie.invn_sbs_item_sid = i.sid
        LEFT JOIN rps.dcs d    ON i.dcs_sid = d.sid AND i.sbs_sid = d.sbs_sid
        WHERE po.po_no IS NOT NULL
    """
