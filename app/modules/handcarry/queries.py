"""
Oracle SQL queries for the handcarry module.

Two queries are needed across both `list_items` (live join when GET runs)
and the one-time `backfill_handcarry.py` migration:

  - ORACLE_PRODUCT_INFO   per UPC: description, brand, category, colour,
                          size, season, selling price.
  - ORACLE_LIFETIME_SOLD  per UPC: sum of all sold qty (net of returns)
                          across all time. Lifetime per CR #58 spec.

Both queries accept a comma-separated bind-safe UPC list via the
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
