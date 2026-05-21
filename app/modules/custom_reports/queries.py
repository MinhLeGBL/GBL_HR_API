"""
Oracle SQL queries for the custom-reports module.

These queries are deliberately raw — they pull receipt/sale events at the
line-item grain so the service layer can do FIFO unit matching in pandas.
SQL alone is awkward for FIFO; pulling ~10k rows and matching in Python
is simpler and fast enough.
"""


class CustomReportsQueries:

    # -----------------------------------------------------------------
    # SS25 / SS26 (or any list of seasons) — item attributes
    # -----------------------------------------------------------------
    # One row per item. Brings brand (vendor), size, season, current
    # on-hand snapshot, and base cost. :seasons is a comma-separated
    # quoted list interpolated into the IN clause by the caller (no user
    # input — values are validated against `^[A-Z0-9]+$`).
    SIZE_REPORT_ITEMS = """
        SELECT
            i.sid AS item_sid,
            TO_CHAR(i.upc) AS upc,
            v.vend_name AS brand,
            i.item_size AS item_size,
            i.UDF5_STRING AS season,
            NVL(iq.qty, 0) AS on_hand_qty
        FROM rps.invn_sbs_item i
        LEFT JOIN rps.vendor v ON v.sid = i.vend_sid
        LEFT JOIN (
            SELECT invn_sbs_item_sid AS item_sid, SUM(qty) AS qty
            FROM rps.invn_sbs_item_qty
            GROUP BY invn_sbs_item_sid
        ) iq ON iq.item_sid = i.sid
        WHERE i.UDF5_STRING IN ({seasons})
    """

    # -----------------------------------------------------------------
    # Receipt events (vou_type=0 only — positive receipts; vendor
    # returns excluded for cleaner FIFO)
    # -----------------------------------------------------------------
    # `post_date` is the date the voucher was posted to the system. Used
    # as the receipt date for FIFO purposes. Filtered to the same set of
    # items as SIZE_REPORT_ITEMS via inner join.
    SIZE_REPORT_RECEIPTS = """
        SELECT
            vi.item_sid AS item_sid,
            vh.post_date AS post_date,
            vi.qty AS qty
        FROM rps.voucher vh
        JOIN rps.vou_item vi ON vi.vou_sid = vh.sid
        JOIN rps.invn_sbs_item i ON i.sid = vi.item_sid
        WHERE vh.vou_class = 0
          AND vh.slip_flag = 0
          AND vh.held = 0
          AND vh.status = 4
          AND vh.vou_type = 0
          AND i.UDF5_STRING IN ({seasons})
        ORDER BY vi.item_sid, vh.post_date
    """

    # -----------------------------------------------------------------
    # Sale events (item_type=1 only — straight sales; customer returns
    # excluded for cleaner FIFO)
    # -----------------------------------------------------------------
    SIZE_REPORT_SALES = """
        SELECT
            b.invn_sbs_item_sid AS item_sid,
            a.created_datetime AS sale_date,
            b.qty AS qty
        FROM rps.document a
        JOIN rps.document_item b ON a.sid = b.doc_sid
        JOIN rps.invn_sbs_item i ON i.sid = b.invn_sbs_item_sid
        WHERE a.is_held = 0
          AND a.receipt_type IN (0, 1)
          AND a.status = 4
          AND b.item_type = 1
          AND i.UDF5_STRING IN ({seasons})
        ORDER BY b.invn_sbs_item_sid, a.created_datetime
    """
