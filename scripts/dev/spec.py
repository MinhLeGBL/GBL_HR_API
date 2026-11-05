"""What the local development database contains, and what is faked in it.

This is the declarative half of the dev-database tooling: `extract_seed.py`
reads it to decide what to pull out of production, and `load_seed.py` reads it
to decide what order to put things back in.

WHY A SPEC AND NOT JUST "COPY EVERYTHING"
-----------------------------------------
Two reasons, and only one of them is size.

1. **Referential integrity under a date window.** `DOCUMENT_ITEM` has no usable
   date of its own; its rows belong to whichever `DOCUMENT` they hang off. Take
   a date window of each table independently and you get order lines whose
   order is missing — which does not crash, it just quietly reports the wrong
   totals. Children are therefore selected by their PARENT'S keys, never by a
   date of their own.

2. **Personal data must not leave production.** See ANONYMISE below.

THE THREE MODES
---------------
  reference  copied whole. Small, slow-changing, and every report joins them.
  window     filtered on a date column. The transactional parents.
  child      selected by parent key. Never by date, even where it has one.

ORACLE vs POSTGRES: THE `rps` TRAP
----------------------------------
`rps` is a schema name in BOTH databases. `rps.document` is Oracle (RetailPro);
`rps.carrier_item` is Postgres (our own catalogue). They are unrelated. Nothing
in this file describes Postgres — its schema is created by
`scripts/database/init_db.py`, which is already the source of truth for it.
"""
from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class Table:
    name: str
    mode: str                                   # reference | window | child
    date_column: Optional[str] = None           # mode='window'
    parent: Optional[str] = None                # mode='child'
    parent_key: Optional[str] = None            # column on THIS table
    parent_pk: str = 'SID'
    # Columns never extracted. LOBs are excluded on purpose: they are large,
    # slow to stream over the tunnel, and nothing in this application reads
    # them. A dev database that carries customer photographs is all cost.
    skip: Tuple[str, ...] = ()
    anonymise: Tuple[str, ...] = ()


# Order matters — parents before children, for loading.
ORACLE_TABLES: Tuple[Table, ...] = (
    # ---- reference ------------------------------------------------------
    Table('STORE', 'reference'),
    Table('DCS', 'reference'),
    Table('VENDOR', 'reference'),
    Table('PRICE_LEVEL', 'reference'),
    Table('INVN_SBS_ITEM', 'reference'),
    Table('INVN_SBS_PRICE', 'reference'),
    Table('INVN_SBS_EXTEND', 'reference'),
    Table('INVN_SBS_ITEM_QTY', 'reference'),
    Table(
        'CUSTOMER', 'reference',
        skip=('NOTES', 'IMAGE', 'UDF1_CLOB', 'UDF2_CLOB'),
        # FIRST_NAME carries the whole name here — Vietnamese records do not
        # split it, and LAST_NAME is populated on 12 rows out of 6,410.
        # EMAIL is empty in production but is faked anyway, so a future row
        # cannot leak by arriving after this was written.
        anonymise=('FIRST_NAME', 'LAST_NAME', 'EMAIL', 'WSC_USERNAME',
                   'PROMO_CUSTLISTNAME'),
    ),

    # ---- transactional parents -----------------------------------------
    Table(
        'DOCUMENT', 'window', date_column='INVC_POST_DATE',
        skip=('UDF_CLOB',),
        # The bulk of the personal data is HERE, not in CUSTOMER: 30,662
        # bill-to names and 30,162 phone numbers. Missing this and anonymising
        # only CUSTOMER would have felt thorough and leaked nearly everything.
        anonymise=(
            'BT_FIRST_NAME', 'BT_LAST_NAME', 'BT_COMPANY_NAME', 'BT_EMAIL',
            'BT_PRIMARY_PHONE_NO',
            'BT_ADDRESS_LINE1', 'BT_ADDRESS_LINE2', 'BT_ADDRESS_LINE3',
            'BT_ADDRESS_LINE4', 'BT_ADDRESS_LINE5', 'BT_ADDRESS_LINE6',
            'ST_FIRST_NAME', 'ST_LAST_NAME', 'ST_COMPANY_NAME', 'ST_EMAIL',
            'ST_PRIMARY_PHONE_NO',
            'ST_ADDRESS_LINE1', 'ST_ADDRESS_LINE2', 'ST_ADDRESS_LINE3',
            'ST_ADDRESS_LINE4', 'ST_ADDRESS_LINE5', 'ST_ADDRESS_LINE6',
            # Free text, and staff paste customer emails into it — 349 rows
            # contain an "@". Found by the leak scan, not by reading the
            # schema: nothing about the name suggests personal data.
            'NOTES_RETURN',
        ),
    ),
    Table('VOUCHER', 'window', date_column='POST_DATE'),
    Table('ADJUSTMENT', 'window', date_column='POST_DATE'),
    Table('PO', 'window', date_column='POST_DATE'),

    # ---- children -------------------------------------------------------
    Table('DOCUMENT_ITEM', 'child', parent='DOCUMENT', parent_key='DOC_SID',
          skip=('EFTDATA0',)),
    # TENDER has its own POST_DATE and could be windowed on it. It is a child
    # anyway, deliberately: a tender row whose DOCUMENT is outside the window
    # is a payment against an invoice that is not there, which reconciles to
    # nothing and looks like missing revenue.
    Table('TENDER', 'child', parent='DOCUMENT', parent_key='DOC_SID'),
    Table('VOU_ITEM', 'child', parent='VOUCHER', parent_key='VOU_SID'),
    Table('ADJ_ITEM', 'child', parent='ADJUSTMENT', parent_key='ADJ_SID'),
    Table('ADJ_QTY', 'child', parent='ADJ_ITEM', parent_key='ADJ_ITEM_SID'),
    Table('PO_ITEM', 'child', parent='PO', parent_key='PO_SID'),
)

ORACLE_BY_NAME = {t.name: t for t in ORACLE_TABLES}


# ── staff names ──────────────────────────────────────────────────────────────
# CASHIER_FULL_NAME and EMPLOYEE1..5_FULL_NAME on DOCUMENT are NOT anonymised
# by default, and that is a decision rather than an oversight:
#
#   - They are colleagues, not customers, and the developer running this has
#     legitimate access to them through the HR module already.
#   - Commission joins sales to employees BY NAME. The same names live in
#     Postgres `employees`. Faking them on one side and not the other silently
#     breaks every commission figure — the reports still run, they just return
#     zero for everyone.
#   - The HR module's entire purpose is managing named people. A dev database
#     of pseudonyms cannot be used to check it.
#
# `--anonymise-staff` turns them on, applying the SAME value-keyed mapping to
# both databases so the join survives.
STAFF_NAME_COLUMNS = {
    'DOCUMENT': (
        'CASHIER_FULL_NAME', 'CASHIER_LOGIN_NAME',
        'EMPLOYEE1_FULL_NAME', 'EMPLOYEE1_LOGIN_NAME',
        'EMPLOYEE2_FULL_NAME', 'EMPLOYEE2_LOGIN_NAME',
        'EMPLOYEE3_FULL_NAME', 'EMPLOYEE3_LOGIN_NAME',
        'EMPLOYEE4_FULL_NAME', 'EMPLOYEE4_LOGIN_NAME',
        'EMPLOYEE5_FULL_NAME', 'EMPLOYEE5_LOGIN_NAME',
    ),
}

# Postgres columns holding personal data. The schema itself comes from
# init_db.py; this only says what to fake on the way out.
#
# EVERY NAME HERE IS CHECKED against the live catalogue by `extract_seed.py`,
# which refuses to run if one does not exist. The first draft of this dict was
# written from memory and got it wrong twice — it named `employees.phone`,
# `address`, `id_number` and `bank_account`, none of which exist, and named
# `crm_customer_scores.customer_name` when the column is `name`. A silently
# unmatched column name is not a typo, it is a leak: the extract succeeds, the
# bundle looks finished, and real customer names are sitting in it.
POSTGRES_ANONYMISE = {
    # `full_name` is staff — see STAFF_NAME_COLUMNS above for why it stays.
    'employees': ('email',),
    'users': ('email',),
    'crm_customer_scores': ('name', 'email', 'phone'),
    # The management board's real addresses. Absent from the first draft
    # entirely, which would have put them in a database that a mis-set
    # MAIL_BACKEND could send from.
    'periodic_report_recipients': ('email', 'name'),
    # The three below were all found by the leak scan on real data. None of
    # them has a column name that hints at personal data, which is the point:
    # a spec written by reading the schema would have missed every one.
    'periodic_report_sends': ('recipients',),      # text[] of board addresses
    'pnl_imports': ('uploaded_by',),
    'payable_payment_remakes': ('tagged_by', 'untagged_by'),
}

# Replaced outright rather than faked. A copied bcrypt hash is a credential
# worth stealing AND useless for logging in, which is the worst of both. Every
# local account gets the same known development password instead.
POSTGRES_REPLACE = {
    'users': {'password_hash': '__DEV_PASSWORD_HASH__'},
}

DEV_PASSWORD = 'devpassword'

# The single account the development sign-in offers.
#
# The picker used to list all nine staff by their real names, which put
# colleagues' names on a login screen that gets screen-shared and
# screenshotted for no reason at all — one admin account is everything
# development needs. The chosen row is renamed IN THE BUNDLE, so the name
# never reaches a laptop rather than being hidden at display time.
#
# It is the admin because anything less cannot exercise the whole application.
DEV_ACCOUNT_NAME = 'devaccount'
DEV_ACCOUNT_ROLE = 'admin'

# Tables whose contents are not worth moving between machines.
#   periodic_report_runs — each row carries a ~340KB .xlsx in `workbook`;
#                          38 of them is ~13MB of blob that regenerates in
#                          one command. Rows are kept, the blob is dropped.
POSTGRES_DROP_COLUMNS = {
    'periodic_report_runs': ('workbook',),
}
