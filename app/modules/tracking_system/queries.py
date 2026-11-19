"""Oracle (RetailPro) reads for the tracking system. Read-only.

Definitions are shared with `custom_reports` on purpose, so a figure here
matches the same figure in a custom report:

  · net line revenue (`_NET_LINE`) is ex-VAT and applies the DOCUMENT
    discount only — `di.PRICE` is already net of the item discount;
  · a bill number is store-prefixed (`RWR-4307`), because DOC_NO runs one
    sequence per store and the bare number collides on most bills;
  · a return is `item_type = 2` (its qty is POSITIVE in Oracle) — here its
    qty and revenue are negated so a return reads as a negative line;
  · receipts are posted receiving vouchers, excluding transfer slips and
    vendor returns;
  · SYSADMIN bills are not sales.

LANDED COST is `INVN_SBS_ITEM.UDF1_STRING` — a free-text field, numeric on
~93% of items (typically ~14% above COST, i.e. cost plus freight/duty) and
junk ('  -  ', 'undefined') on a few hundred. It is returned RAW and parsed
in Python (`parse_landed_cost`); an item without a parseable value cannot be
tracked on the In tab.
"""

_NET_LINE = (
    "(di.PRICE - NVL(di.TAX_AMT, 0)) "
    "* NVL(di.QTY, 0) "
    "* (1 - NVL(d.DISC_PERC, 0) / 100)"
)

_ITEM_COLS = """
            i.sid                         AS item_sid,
            TO_CHAR(i.upc)                AS upc,
            i.alu                         AS alu,
            i.text1                       AS description,
            v.vend_name                   AS brand,
            dcs.d_long_name               AS department,
            i.udf5_string                 AS season
"""

_ITEM_JOINS = """
        LEFT JOIN rps.vendor v ON v.sid = i.vend_sid
        LEFT JOIN rps.dcs dcs  ON dcs.sid = i.dcs_sid AND dcs.sbs_sid = i.sbs_sid
"""

# Matches a UPC by prefix or an ALU by substring. `:upc_prefix` / `:alu_like`
# are built in Python ('123%' / '%ABC%', upper-cased).
_ITEM_MATCH = "(TO_CHAR(i.upc) LIKE :upc_prefix OR UPPER(i.alu) LIKE :alu_like)"

_POSTED_RECEIPT = """
            vh.vou_class = 0 AND vh.slip_flag = 0 AND vh.held = 0
            AND vh.status = 4 AND vh.vou_type = 0
"""

_SALE_FILTER = """
          di.item_type IN (1, 2)
          AND d.is_held = 0
          AND d.receipt_type IN (0, 1)
          AND d.status = 4
          AND (
              d.bt_cuid IS NULL
              OR d.bt_cuid NOT IN (
                  SELECT sid FROM rps.customer
                  WHERE UPPER(TRIM(first_name)) = 'SYSADMIN'
              )
          )
"""


class TrackingQueries:

    # Items for the In import. `received_qty` = lifetime qty on posted receipts,
    # the default qty offered in the import.
    _ITEMS = f"""
        SELECT
{_ITEM_COLS},
            i.udf1_string                 AS landed_raw,
            (SELECT NVL(SUM(vi.qty), 0)
               FROM rps.vou_item vi
               JOIN rps.voucher vh ON vh.sid = vi.vou_sid
              WHERE vi.item_sid = i.sid AND {_POSTED_RECEIPT})
                                          AS received_qty
        FROM rps.invn_sbs_item i
{_ITEM_JOINS}
    """

    ITEM_SEARCH = _ITEMS + f"""
        WHERE {_ITEM_MATCH}
        ORDER BY i.alu, i.upc
        FETCH FIRST :lim ROWS ONLY
    """

    ITEM_BY_SID = _ITEMS + "\n        WHERE i.sid = :sid\n"

    # Batch lookups. `{upcs}` / `{sids}` are bind placeholder lists built in
    # Python, at most ORACLE_IN_CHUNK long. UPC is a NUMBER column: binding
    # numbers keeps the lookup on the column itself (0.15 s per 500 locally)
    # where TO_CHAR(i.upc) IN (...) took 1.24 s.
    ITEMS_BY_UPCS = _ITEMS + "\n        WHERE i.upc IN ({upcs})\n"
    ITEMS_BY_SIDS = _ITEMS + "\n        WHERE i.sid IN ({sids})\n"

    # Sale and return lines for the Out import, newest first.
    _LINES = f"""
        SELECT
            di.sid                                        AS line_id,
            d.sid                                         AS bill_id,
            st.store_code || '-' || d.doc_no              AS bill_no,
            TO_CHAR(d.invc_post_date, 'YYYY-MM-DD')       AS bill_date,
            st.store_code                                 AS store,
{_ITEM_COLS},
            CASE WHEN di.item_type = 2 THEN -1 ELSE 1 END * NVL(di.qty, 0)
                                                          AS qty,
            CASE WHEN di.item_type = 2 THEN -1 ELSE 1 END * {_NET_LINE}
                                                          AS oracle_revenue
        FROM rps.document d
        JOIN rps.document_item di ON di.doc_sid = d.sid
        JOIN rps.invn_sbs_item i  ON i.sid = di.invn_sbs_item_sid
        LEFT JOIN rps.store st    ON st.sid = d.store_sid
{_ITEM_JOINS}
        WHERE {_SALE_FILTER}
    """

    # Two steps, not one: matching the UPC/ALU pattern inside the sales join
    # scans every sale line (3.8 s locally); finding the items first is 0.1 s
    # and the lines by item sid are then an index lookup. `{item_sids}` is a
    # list of bind placeholders built in Python (`:s0, :s1, ...`).
    ITEM_SIDS_MATCHING = f"""
        SELECT i.sid FROM rps.invn_sbs_item i
        WHERE {_ITEM_MATCH}
        FETCH FIRST :lim ROWS ONLY
    """

    LINES_FOR_ITEMS = _LINES + """
          AND di.invn_sbs_item_sid IN ({item_sids})
        ORDER BY d.invc_post_date DESC, d.doc_no DESC
        FETCH FIRST :lim ROWS ONLY
    """

    LINE_BY_ID = _LINES + "\n          AND di.sid = :line_id\n"

    # Lines of one bill number. DOC_NO runs one sequence PER STORE, so a bare
    # number (`:store` NULL) returns that number from every store, each shown
    # with its store prefix; 'RWP-556' narrows it to one store.
    LINES_FOR_BILL = _LINES + """
          AND d.doc_no = :doc_no
          AND (:store IS NULL OR UPPER(st.store_code) = :store)
        ORDER BY d.invc_post_date DESC, d.doc_no DESC
        FETCH FIRST :lim ROWS ONLY
    """

    # Received qty per item per calendar year of the receipt's POST_DATE, for
    # the Excel import's optional Year column: a cost given for a year applies
    # only to the units received in that year.
    RECEIVED_BY_YEAR = f"""
        SELECT vi.item_sid                            AS item_sid,
               EXTRACT(YEAR FROM vh.post_date)        AS yr,
               SUM(vi.qty)                            AS qty
          FROM rps.vou_item vi
          JOIN rps.voucher vh ON vh.sid = vi.vou_sid
         WHERE vi.item_sid IN ({{sids}}) AND {_POSTED_RECEIPT}
         GROUP BY vi.item_sid, EXTRACT(YEAR FROM vh.post_date)
    """
