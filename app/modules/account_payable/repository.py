"""
Oracle data access for the account_payable module (CR #60).

Mirrors commission's repository/service split so the service stays focused
on shape/business logic while Oracle access lives here. Two responsibilities:

1. **Ledger replay** (`get_all_bills`) — walks every active customer's
   Charge tender events, applies REF_SALE_SID → FIFO matching, and emits
   one record per Charge bill with its current balance + payment
   chronology. Same algorithm as
   `CommissionRepository._replay_charge_ledger`, plus payment-record
   bookkeeping the AP UI needs.

2. **Bill line items** (`get_bill_items`) — DOCUMENT_ITEM rows joined to
   employee + product info for a list of bill_sids. Column shape matches
   commission's ALL_SALES_DATA so `payable_bill_rates` lookups by
   `(bill_sid, upc)` line up exactly.
"""
from typing import Any, Dict, List, Optional, Tuple

from app.core.database import get_oracle_connection
from .queries import AccountPayableQueries


class AccountPayableRepository:
    """Oracle read layer for the account_payable module."""

    def __init__(self, queries: Optional[AccountPayableQueries] = None):
        self.queries = queries or AccountPayableQueries()

    # ------------------------------------------------------------------
    # Ledger — every active customer's running balance
    # ------------------------------------------------------------------
    def get_all_bills(self) -> Dict[str, Dict[str, Any]]:
        """Return per-bill state for every Charge bill in the active window.

        Algorithm (REF_SALE_SID → FIFO replay, run per customer):
          1. Pull every Charge tender event for customers active in the
             past 24 months (see ORACLE_ALL_CHARGE_LEDGER).
          2. Pass 1: payments with REF_SALE_SID apply to that exact bill.
          3. Pass 2: leftover payments apply FIFO to the customer's oldest
             open bills (insertion order = chronological).
          4. Emit one record per bill (open / partial / fully_paid) with
             `payments[]` chronology.

        Returns:
            {
                bill_sid_str: {
                    'doc_no':            str,
                    'doc_store_code':    str,
                    'customer_sid':      str | None,
                    'customer_name':     str | None,   # trimmed FIRST_NAME
                    'original_charge':   int,
                    'total_paid':        int,
                    'remaining_unpaid':  int,
                    'status':            'open' | 'partial' | 'fully_paid',
                    'created_date':      'YYYY-MM-DD',
                    'last_payment_date': 'YYYY-MM-DD' | None,
                    'sale_total_amt':    int,
                    'post_month':        'YYYY-MM',
                    'payments': [
                        {
                            'payment_doc_sid':  str,
                            'payment_doc_no':   str | None,
                            'payment_date':     'YYYY-MM-DD',
                            'amount_applied':   int,
                            'source':           'ref_sale_sid' | 'fifo',
                        },
                        ...
                    ],
                },
                ...
            }
            Empty dict on connection failure.
        """
        conn = get_oracle_connection()
        if not conn:
            return {}

        try:
            with conn.cursor() as cursor:
                cursor.execute(self.queries.ORACLE_ALL_CHARGE_LEDGER)
                rows = cursor.fetchall()
                columns = [d[0].lower() for d in cursor.description]
        except Exception as e:
            print(f"ERROR querying AP ledger: {e}")
            return {}
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if not rows:
            return {}

        # Group events by customer for per-customer replay.
        events_by_customer: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            row = dict(zip(columns, r))
            # Stringify SIDs — Oracle 18-digit SIDs exceed float64 precision
            # (see commission/repository.py for the same treatment).
            customer_sid = str(row['customer_sid']) if row['customer_sid'] is not None else None
            row['customer_sid'] = customer_sid
            row['doc_sid'] = str(row['doc_sid'])
            row['ref_sale_sid'] = (
                str(row['ref_sale_sid']) if row['ref_sale_sid'] is not None else None
            )
            # Walk-in bills have no customer; group them under a sentinel
            # key so the replay still runs per-customer (each walk-in is
            # effectively its own customer — no cross-bill FIFO possible).
            key = customer_sid if customer_sid is not None else f'__walkin__:{row["doc_sid"]}'
            events_by_customer.setdefault(key, []).append(row)

        result: Dict[str, Dict[str, Any]] = {}
        for events in events_by_customer.values():
            bills, _payments = self._replay_charge_ledger_with_chronology(events)
            for bill_sid, b in bills.items():
                original = int(b['original'])
                remaining = max(0, int(b['remaining']))
                total_paid = original - remaining
                if remaining == 0:
                    status = 'fully_paid'
                elif total_paid == 0:
                    status = 'open'
                else:
                    status = 'partial'
                payments_chrono = b['payments_chrono']
                last_payment_date = (
                    payments_chrono[-1]['payment_date'] if payments_chrono else None
                )
                result[bill_sid] = {
                    'doc_no':            str(b['doc_no']),
                    'doc_store_code':    b['doc_store_code'],
                    'customer_sid':      b['customer_sid'],
                    'customer_name':     b['customer_name'],
                    'original_charge':   original,
                    'total_paid':        total_paid,
                    'remaining_unpaid':  remaining,
                    'status':            status,
                    'created_date':      b['post_date_str'],
                    'last_payment_date': last_payment_date,
                    'sale_total_amt':    int(b['sale_total_amt'] or 0),
                    'post_month':        b['post_month'],
                    'payments':          payments_chrono,
                }
        return result

    # ------------------------------------------------------------------
    # Ledger replay — exposed as a static method so unit tests can drive
    # it without an Oracle connection.
    # ------------------------------------------------------------------
    @staticmethod
    def _replay_charge_ledger_with_chronology(
        events: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
        """REF_SALE_SID → FIFO replay that also records per-bill payment chronology.

        Same matching rules as `CommissionRepository._replay_charge_ledger`
        but each bill keeps a list of which payment events touched it (in
        chronological order, with the source — 'ref_sale_sid' or 'fifo').

        Args:
            events: chronologically-sorted list of per-customer ledger
                rows. Must contain at least: doc_sid, doc_no, doc_store_code,
                customer_sid, customer_name, charge_amount, post_date_str,
                post_month, ref_sale_sid, sale_total_amt.

        Returns:
            (bills, payments) where `bills` is dict[doc_sid → bill record
            with `payments_chrono` list] and `payments` is the (now-exhausted)
            payment event list.
        """
        bills: Dict[str, Dict[str, Any]] = {}
        payments: List[Dict[str, Any]] = []
        for e in events:
            amount = int(e['charge_amount'])
            if amount > 0:
                bills[e['doc_sid']] = {
                    'doc_no':           e['doc_no'],
                    'doc_store_code':   e['doc_store_code'],
                    'customer_sid':     e['customer_sid'],
                    'customer_name':    e['customer_name'],
                    'original':         amount,
                    'remaining':        amount,
                    'sale_total_amt':   e['sale_total_amt'],
                    'post_date_str':    e['post_date_str'],
                    'post_month':       e['post_month'],
                    'payments_chrono':  [],
                }
            else:
                payments.append({
                    'doc_sid':       e['doc_sid'],
                    'doc_no':        e['doc_no'],
                    'amount':        -amount,    # positive magnitude
                    'remaining':     -amount,
                    'ref':           e['ref_sale_sid'],
                    'post_date_str': e['post_date_str'],
                })

        # Pass 1: REF_SALE_SID. Targeted payments win even if not chronologically first.
        for p in payments:
            if not p['ref'] or p['ref'] not in bills:
                continue
            b = bills[p['ref']]
            consumed = min(b['remaining'], p['remaining'])
            if consumed <= 0:
                continue
            b['remaining'] -= consumed
            p['remaining'] -= consumed
            b['payments_chrono'].append({
                'payment_doc_sid': str(p['doc_sid']),
                'payment_doc_no':  p['doc_no'],
                'payment_date':    p['post_date_str'],
                'amount_applied':  int(consumed),
                'source':          'ref_sale_sid',
            })

        # Pass 2: FIFO over remaining open bills, in customer insertion order.
        for p in payments:
            if p['remaining'] <= 0:
                continue
            for b in bills.values():
                if p['remaining'] <= 0:
                    break
                if b['remaining'] <= 0:
                    continue
                consumed = min(b['remaining'], p['remaining'])
                b['remaining'] -= consumed
                p['remaining'] -= consumed
                b['payments_chrono'].append({
                    'payment_doc_sid': str(p['doc_sid']),
                    'payment_doc_no':  p['doc_no'],
                    'payment_date':    p['post_date_str'],
                    'amount_applied':  int(consumed),
                    'source':          'fifo',
                })

        # Sort each bill's payment chronology by date — Pass 1 (ref) can
        # have arrived chronologically before Pass 2 (fifo) hits the same
        # bill, but our two-pass ordering interleaves them by pass, not date.
        for b in bills.values():
            b['payments_chrono'].sort(key=lambda x: x['payment_date'])

        return bills, payments

    # ------------------------------------------------------------------
    # Line items for a set of bills
    # ------------------------------------------------------------------
    def get_bill_items(self, bill_sids: List[str]) -> List[Dict[str, Any]]:
        """Return DOCUMENT_ITEM rows for the given bills.

        Bill SIDs are stringified; the IN clause is built with named bind
        variables so no user input is concatenated into the SQL.

        Returns:
            list of dicts with keys: bill_sid, sale_id, upc, description,
            qty, vendor_code, is_jewelry, category, department,
            discount_rate, revenue_with_vat, employee_sid, employee_code,
            employee_name. SIDs are strings.
        """
        if not bill_sids:
            return []
        # Sanity: ensure every SID is a digit string. Belt-and-suspenders
        # in addition to the bind-variable parameterization below.
        for s in bill_sids:
            if not str(s).isdigit():
                raise ValueError(f'bill_sids must be numeric strings; got {s!r}')

        binds = {f's{i}': str(s) for i, s in enumerate(bill_sids)}
        bind_list = ', '.join(f':{k}' for k in binds.keys())
        sql = self.queries.ORACLE_BILL_ITEMS_BY_SID.format(bind_list=bind_list)

        conn = get_oracle_connection()
        if not conn:
            return []
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, binds)
                rows = cursor.fetchall()
                columns = [d[0].lower() for d in cursor.description]
        except Exception as e:
            print(f"ERROR querying AP bill items: {e}")
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

        out: List[Dict[str, Any]] = []
        for r in rows:
            row = dict(zip(columns, r))
            # Stringify SIDs.
            row['bill_sid'] = str(row['bill_sid'])
            row['sale_id'] = str(row['sale_id']) if row['sale_id'] is not None else None
            row['employee_sid'] = (
                str(row['employee_sid']) if row['employee_sid'] is not None else None
            )
            # UPC: keep as string with whitespace stripped to match
            # `payable_bill_rates.upc` which is written as `upc_clean`
            # (commission service strips before insert).
            row['upc'] = str(row['upc']).strip() if row['upc'] is not None else None
            out.append(row)
        return out
