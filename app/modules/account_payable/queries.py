"""
Oracle + PostgreSQL SQL queries for the account_payable module (CR #60).

Phase 1 scaffold — query bodies are empty placeholders. Implementation
will populate these as endpoints are built out.

The Oracle side reuses the same Charge-tender ledger that
CommissionRepository.get_unpaid_bill_amounts (commission v2.0.0) already
walks. The PostgreSQL side reads/writes the new `payable_reconciliations`
table that this CR introduces.
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
