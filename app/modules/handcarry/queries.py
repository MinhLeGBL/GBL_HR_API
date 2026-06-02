"""
Oracle SQL queries for the handcarry module — every read joins live
from Oracle (CR #64), so `list_items` calls all four queries per request:

  - ORACLE_PRODUCT_INFO       per UPC: description, brand, category, colour,
                              size, season, selling price.
  - ORACLE_LIFETIME_SOLD      per UPC: sum of all sold qty (net of returns)
                              across all time.
  - ORACLE_LIFETIME_RECEIVED  per UPC: sum of received qty from posted
                              receiving vouchers (vou_class=0, vou_type=0,
                              status=4). Same source Oracle uses for
                              non-hand-carry inventory.
  - ORACLE_LIFETIME_ADJ_IN    per UPC: sum of direct stock-in adjustments
                              (ADJ_TYPE=1, status=4). Unioned with
                              ORACLE_LIFETIME_RECEIVED at the service layer
                              for the full "ever received" count (CR #65).

All queries accept a comma-separated bind-safe UPC list via the
`{upcs}` placeholder. The caller is responsible for validating that
every value is an integer before interpolation.
"""


class HandCarryQueries:

    # Per-UPC product info, joined across the standard item/vendor/dcs/extend
    # chain used elsewhere in the codebase. `price_after_vat` reuses
    # `invn_sbs_item_price.price` (with-VAT — Vietnamese retail convention)
    # and divides by 1.1 to derive a before-VAT figure, mirroring the
    # commission module's flat-10% rule (CR #20).
    ORACLE_PRODUCT_INFO = """
        SELECT
            TO_CHAR(i.upc)                                  AS upc,
            i.description1                                  AS description,
            v.vend_name                                     AS brand,
            ie.udf8_string                                  AS category,
            i.description4                                  AS color,
            i.item_size                                     AS item_size,
            i.UDF5_STRING                                   AS season,
            ROUND(ip.price)                                 AS price_after_vat,
            ROUND(ip.price / 1.1)                           AS price_before_vat
        FROM rps.invn_sbs_item i
        LEFT JOIN rps.vendor v             ON v.sid = i.vend_sid
        LEFT JOIN rps.invn_sbs_extend ie   ON ie.invn_sbs_item_sid = i.sid
        LEFT JOIN rps.invn_sbs_price ip    ON ip.sbs_sid = i.sbs_sid
                                          AND ip.invn_sbs_item_sid = i.sid
        WHERE TO_CHAR(i.upc) IN ({upcs})
    """

    # Lifetime sold qty per UPC. Same filter set the commission module
    # uses for sales (status=4 posted, receipt_type 0/1, item_type 1 sale
    # or 2 return — net qty signs flip for returns).
    ORACLE_LIFETIME_SOLD = """
        SELECT
            TO_CHAR(i.upc)                                                       AS upc,
            SUM(CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END)     AS quantity_sold
        FROM rps.document d
        JOIN rps.document_item di         ON di.doc_sid = d.sid
        JOIN rps.invn_sbs_item i          ON i.sid = di.invn_sbs_item_sid
        WHERE d.is_held = 0
          AND d.receipt_type IN (0, 1)
          AND d.status = 4
          AND di.item_type IN (1, 2)
          AND TO_CHAR(i.upc) IN ({upcs})
        GROUP BY TO_CHAR(i.upc)
    """

    # Lifetime received qty per UPC. Posted receiving vouchers only
    # (vou_class=0 receiving, vou_type=0, slip_flag=0, held=0, status=4).
    # Same filter set the custom_reports size FIFO uses.
    ORACLE_LIFETIME_RECEIVED = """
        SELECT
            TO_CHAR(i.upc) AS upc,
            SUM(vi.qty)    AS quantity_received
        FROM rps.voucher vh
        JOIN rps.vou_item vi      ON vi.vou_sid = vh.sid
        JOIN rps.invn_sbs_item i  ON i.sid = vi.item_sid
        WHERE vh.vou_class = 0
          AND vh.slip_flag = 0
          AND vh.held = 0
          AND vh.status = 4
          AND vh.vou_type = 0
          AND TO_CHAR(i.upc) IN ({upcs})
        GROUP BY TO_CHAR(i.upc)
    """

    # Lifetime adjustment-in qty per UPC (CR #65). Hand-carry jewelry
    # often enters inventory via direct stock adjustment (ADJ_TYPE=1)
    # rather than a voucher receipt — these wouldn't show up in
    # ORACLE_LIFETIME_RECEIVED above. Union both sources at the service
    # layer to get the full "ever received" count.
    ORACLE_LIFETIME_ADJ_IN = """
        SELECT
            TO_CHAR(i.upc) AS upc,
            SUM(aq.qty)    AS quantity_adjusted_in
        FROM rps.adj_item ai
        JOIN rps.adjustment a    ON a.sid = ai.adj_sid
                                 AND a.adj_type = 1
                                 AND a.status = 4
        JOIN rps.adj_qty aq      ON aq.adj_item_sid = ai.sid
        JOIN rps.invn_sbs_item i ON i.sid = ai.item_sid
        WHERE aq.qty > 0
          AND TO_CHAR(i.upc) IN ({upcs})
        GROUP BY TO_CHAR(i.upc)
    """
