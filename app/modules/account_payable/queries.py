"""
Oracle + PostgreSQL SQL queries for the account_payable module (CR #60).

The Oracle side reuses the same Charge-tender ledger algorithm that
`CommissionRepository.get_unpaid_bill_amounts` walks — REF_SALE_SID first,
then FIFO across remaining open bills — but unscoped (running balance,
not month-scoped) and with extra columns the AP UI needs (`doc_store_code`,
`notes_lostdoc`, joined `customer_name`).

The PostgreSQL side reads/writes:
- `payable_reconciliations` — manual payment→bill linkages
- `payable_item_custom_rates` — per-item release-rate overrides
"""


class AccountPayableQueries:
    """Container for account-payable SQL queries.

    Each query is a placeholder for now; implementation tasks will fill
    them in. Kept as a class for parity with other modules
    (`CommissionQueries`, `HandCarryQueries`).
    """

    # Postgres DDL — `payable_reconciliations` linkage table.
    # Created idempotently from `init_database()`.
    CREATE_RECONCILIATIONS_TABLE = """
        CREATE TABLE IF NOT EXISTS payable_reconciliations (
            id              BIGSERIAL PRIMARY KEY,
            payment_doc_sid VARCHAR(40) NOT NULL,
            bill_sid        VARCHAR(40) NOT NULL,
            amount_applied  BIGINT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by      INT REFERENCES users(sid),
            UNIQUE (payment_doc_sid, bill_sid)
        )
    """
    CREATE_RECONCILIATIONS_BILL_INDEX = """
        CREATE INDEX IF NOT EXISTS idx_payable_recon_bill
            ON payable_reconciliations(bill_sid)
    """
    CREATE_RECONCILIATIONS_PAYMENT_INDEX = """
        CREATE INDEX IF NOT EXISTS idx_payable_recon_payment
            ON payable_reconciliations(payment_doc_sid)
    """

    # ────────────────────────────────────────────────────────────────────
    # Oracle — Charge-tender ledger for AP (unscoped, running balance).
    # ────────────────────────────────────────────────────────────────────
    # Reuses the same chronological-event shape as commission's
    # ORACLE_UNPAID_BILLS_BY_MONTH, but with:
    # - No target-month filter (returns the full ledger so AP can compute
    #   the current running balance for every customer).
    # - 24-month activity cutoff on the customer set: any customer who has
    #   placed at least one Charge bill in the past 24 months. Captures
    #   real open AR without scanning the entire Oracle history; older
    #   bills from inactive customers are effectively bad debt.
    # - Joined `customer_name` (CUSTOMER.FIRST_NAME, trimmed — Vietnamese
    #   last-first convention puts the full name in FIRST_NAME; see
    #   crm/queries.py for the same convention).
    # - `doc_store_code` and `notes_lostdoc` so the AP service can surface
    #   them for the Bills tab and the payments queue without a second query.
    #
    # Ordering: by customer + invc_post_date + doc_no so the per-customer
    # ledger replay can run in a single pass.
    ORACLE_ALL_CHARGE_LEDGER = """
        WITH active_customers AS (
            SELECT DISTINCT d.bt_cuid AS customer_sid
            FROM rps.document d
            JOIN rps.tender t ON t.doc_sid = d.sid
                              AND t.tender_name = 'Charge'
                              AND t.amount > 0
            WHERE d.status = 4
              AND d.invc_post_date >= ADD_MONTHS(SYSDATE, -24)
        )
        SELECT
            d.bt_cuid                                AS customer_sid,
            TRIM(c.first_name)                       AS customer_name,
            d.sid                                    AS doc_sid,
            d.doc_no                                 AS doc_no,
            d.store_code                             AS doc_store_code,
            t.amount                                 AS charge_amount,
            d.invc_post_date                         AS post_date,
            d.ref_sale_sid                           AS ref_sale_sid,
            d.sale_total_amt                         AS sale_total_amt,
            d.notes_lostdoc                          AS notes_lostdoc,
            TO_CHAR(d.invc_post_date, 'YYYY-MM-DD')  AS post_date_str,
            TO_CHAR(d.invc_post_date, 'YYYY-MM')     AS post_month
        FROM rps.document d
        JOIN rps.tender t ON t.doc_sid = d.sid
                          AND t.tender_name = 'Charge'
        JOIN active_customers ac ON ac.customer_sid = d.bt_cuid
        LEFT JOIN rps.customer c ON c.sid = d.bt_cuid
        WHERE d.status = 4
        ORDER BY d.bt_cuid, d.invc_post_date, d.doc_no
    """

    # ────────────────────────────────────────────────────────────────────
    # Oracle — Line items for a list of bill_sids.
    # ────────────────────────────────────────────────────────────────────
    # Column shape mirrors commission's ALL_SALES_DATA so the AP service can
    # apply the SAME classification logic if a `payable_bill_rates` row is
    # missing for an item (legacy bills from before CR #61 shipped).
    #
    # UPC alias is `upc` (sourced from di.SCAN_UPC, identical to commission)
    # so joining to `payable_bill_rates.upc` works without any normalization
    # diff. Employee resolution uses EMPLOYEE → CUSTOMER (EMPLOYEE1_SID → SID
    # → UDF4_STRING for the employee code).
    #
    # The bill_sid list is bound as :s0, :s1, ... — the repository expands
    # the IN clause and supplies bind values, so no string concatenation
    # of user input ever reaches the query.
    ORACLE_BILL_ITEMS_BY_SID = """
        SELECT
            d.SID                                                                AS bill_sid,
            di.SID                                                               AS sale_id,
            di.SCAN_UPC                                                          AS upc,
            i.DESCRIPTION1                                                       AS description,
            (CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END)        AS qty,
            di.VEND_CODE                                                         AS vendor_code,
            CASE
                WHEN di.VEND_CODE IN ('VHN', 'ROM', 'ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'SPK', 'TED', 'BRT')
                THEN 1
                ELSE 0
            END                                                                  AS is_jewelry,
            SUBSTR(i.DESCRIPTION2, INSTR(i.DESCRIPTION2, '-', -1) + 1)           AS category,
            dep.D_LONG_NAME                                                      AS department,
            ROUND((1 - (1 - di.DISC_PERC / 100) * (1 - d.DISC_PERC / 100)) * 100, 2) / 100
                                                                                 AS discount_rate,
            ROUND((CASE WHEN di.item_type = 2 THEN di.qty * -1 ELSE di.qty END) *
                  di.price, 0)                                                   AS revenue_with_vat,
            di.EMPLOYEE1_SID                                                     AS employee_sid,
            emp_cust.UDF4_STRING                                                 AS employee_code,
            emp_cust.FIRST_NAME                                                  AS employee_name,
            emp_store.STORE_CODE                                                 AS employee_store_code
        FROM DOCUMENT d
        JOIN DOCUMENT_ITEM di ON d.SID = di.DOC_SID
        LEFT JOIN INVN_SBS_ITEM i ON i.SID = di.INVN_SBS_ITEM_SID
        LEFT JOIN DCS dep ON dep.SID = i.DCS_SID
        LEFT JOIN EMPLOYEE emp ON di.EMPLOYEE1_SID = emp.SID
        LEFT JOIN CUSTOMER emp_cust ON emp_cust.SID = emp.CUST_SID
        LEFT JOIN STORE emp_store ON emp_store.SID = emp.BASE_STORE_SID
        WHERE d.STATUS = 4
          AND di.ITEM_TYPE = 1
          AND d.SID IN ({bind_list})
        ORDER BY d.SID, di.SCAN_UPC
    """

    # Postgres DDL — per-item user-set release-rate overrides.
    # Effective rate per item = custom_release_rate ?? auto_release_rate ?? 0
    # where auto_release_rate comes from commission's `payable_bill_rates`.
    # NUMERIC(10,8) per CR spec — frontend writes decimal values like 0.01500000.
    CREATE_CUSTOM_RATES_TABLE = """
        CREATE TABLE IF NOT EXISTS payable_item_custom_rates (
            bill_sid            VARCHAR(40)    NOT NULL,
            upc                 VARCHAR(50)    NOT NULL,
            custom_release_rate NUMERIC(10,8)  NOT NULL,
            set_by              INT REFERENCES users(sid),
            set_at              TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
            PRIMARY KEY (bill_sid, upc)
        )
    """
