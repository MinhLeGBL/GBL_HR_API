"""
Commission Repository for executing commission-related queries
"""
import calendar
from typing import List, Dict, Any, Optional
import pandas as pd
from app.core.database.connection import get_oracle_connection, get_postgres_connection
from app.modules.commission.queries import CommissionQueries


# Expected columns from ALL_SALES_DATA query
ALL_SALES_COLUMNS = [
    'sale_id', 'upc', 'bill_number', 'bill_sid', 'doc_store_code',
    'sale_date', 'sale_time', 'customer_sid', 'employee_sid',
    'employee_username', 'store_code', 'vendor_code', 'is_jewelry',
    'category', 'department', 'discount_rate',
    'revenue_with_vat', 'revenue_before_vat', 'qty_sold'
]


class CommissionRepository:
    """Repository for commission-related data access"""

    def __init__(self):
        self.queries = CommissionQueries()

    # FUTURE: Uncomment if next-month return policy is enabled (see queries.py comments)
    # @staticmethod
    # def _next_month_end(end_date: str) -> str:
    #     """Compute last day of the month after end_date."""
    #     year = int(end_date[:4])
    #     month = int(end_date[5:7])
    #     if month == 12:
    #         next_year, next_month = year + 1, 1
    #     else:
    #         next_year, next_month = year, month + 1
    #     last_day = calendar.monthrange(next_year, next_month)[1]
    #     return f'{next_year:04d}-{next_month:02d}-{last_day:02d} 23:59:59'

    def execute_query(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Execute a SQL query and return results as list of dictionaries

        Args:
            query: SQL query string
            parameters: Query parameters

        Returns:
            List of dictionaries containing query results
        """
        conn = get_oracle_connection()
        if not conn:
            raise Exception("Failed to connect to database")

        try:
            with conn.cursor() as cursor:
                if parameters:
                    cursor.execute(query, parameters)
                else:
                    cursor.execute(query)

                # Get column names
                columns = [desc[0] for desc in cursor.description]

                # Fetch all results and convert to list of dictionaries
                results = []
                for row in cursor.fetchall():
                    result_dict = dict(zip(columns, row))
                    results.append(result_dict)

                return results

        finally:
            conn.close()

    def get_store_sales_data(
        self,
        store_code: str,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """
        Get store sales data with revenue breakdown (legacy 4-bucket query)

        Args:
            store_code: Store code
            start_date: Start date in 'YYYY-MM-DD HH:MI:SS' format
            end_date: End date in 'YYYY-MM-DD HH:MI:SS' format

        Returns:
            Dictionary containing store sales data
        """
        parameters = {
            'store_code': store_code,
            'start_date': start_date,
            'end_date': end_date
        }

        results = self.execute_query(self.queries.STORE_SALES_DATA, parameters)

        # Return first result or empty dict
        return results[0] if results else {}

    def get_all_sales_data(
        self,
        year: int,
        month: int
    ) -> pd.DataFrame:
        """
        Get ALL transaction line items for a period as a single DataFrame.

        This is the single source of truth — replaces get_store_sales_detail,
        get_employee_sales_data, and get_personal_commission_sales_data.

        Uses LEFT JOINs so non-employee transactions (SYSADMIN, walk-ins) and
        items without inventory records are included.

        Args:
            year: Year for the period
            month: Month for the period

        Returns:
            DataFrame with columns: sale_id, upc, bill_number, doc_store_code,
            sale_date, sale_time, customer_sid, employee_sid, employee_username,
            store_code, vendor_code, is_jewelry, category, department,
            discount_rate, revenue_with_vat, revenue_before_vat
        """
        last_day = calendar.monthrange(year, month)[1]
        start_date = f'{year:04d}-{month:02d}-01 00:00:00'
        end_date = f'{year:04d}-{month:02d}-{last_day:02d} 23:59:59'
        parameters = {
            'start_date': start_date,
            'end_date': end_date
        }

        results = self.execute_query(self.queries.ALL_SALES_DATA, parameters)

        # Oracle SIDs are 18-digit integers — past float64's ~15-digit
        # mantissa. The moment a single LEFT JOIN walk-in produces a NULL
        # in a SID column, pandas promotes the whole column to float64 and
        # every SID in it gets silently rounded (off by up to ~100). That
        # breaks exact-value lookups against hardcoded SIDs (notably
        # `EMPLOYEE_COMMISSION_EXCEPTIONS`) and against the AR payable
        # bill_sid map (CR #59). Stringify SIDs before pandas ever sees them
        # so the column lands as object dtype with intact values; downstream
        # code compares string-to-string.
        for row in results:
            for field in ('SALE_ID', 'EMPLOYEE_SID', 'CUSTOMER_SID', 'BILL_SID'):
                if row.get(field) is not None:
                    row[field] = str(row[field])

        df = pd.DataFrame(results)

        if df.empty:
            return pd.DataFrame(columns=ALL_SALES_COLUMNS)

        df.columns = df.columns.str.lower()
        if 'upc_clean' not in df.columns and 'upc' in df.columns:
            df['upc_clean'] = df['upc'].astype(str).str.strip()

        # COSM+HEA items historically went to SYSADMIN (no commission). CR #61
        # (effective from `HEA_AS_FASHION_FROM_MONTH`) reclassifies them as
        # fashion. For pre-cutoff periods we keep the old rewrite so historical
        # commission output stays stable.
        from app.modules.commission.service import hea_is_fashion
        if not hea_is_fashion(year, month):
            cosm_hea_mask = (df['department'] == 'COSM') & (df['vendor_code'] == 'HEA')
            if cosm_hea_mask.any():
                df.loc[cosm_hea_mask, 'employee_sid'] = None
                df.loc[cosm_hea_mask, 'employee_username'] = 'SYSADMIN'
                df.loc[cosm_hea_mask, 'store_code'] = None

        return df

    def get_employee_info(self, store_code: str) -> List[Dict[str, Any]]:
        """
        Get employee information including tenure

        Args:
            store_code: Store code

        Returns:
            List of employee information
        """
        parameters = {'store_code': store_code}

        return self.execute_query(self.queries.EMPLOYEE_INFO, parameters)

    def get_multiple_stores_sales_data(
        self,
        store_codes: List[str],
        start_date: str,
        end_date: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get sales data for multiple stores

        Args:
            store_codes: List of store codes
            start_date: Start date in 'YYYY-MM-DD HH:MI:SS' format
            end_date: End date in 'YYYY-MM-DD HH24:MI:SS' format

        Returns:
            Dictionary mapping store_code to store sales data
        """
        results = {}
        for store_code in store_codes:
            store_data = self.get_store_sales_data(store_code, start_date, end_date)
            if store_data:
                results[store_code] = store_data
        return results

    def get_multiple_stores_employee_info(
        self,
        store_codes: List[str]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get employee information for multiple stores

        Args:
            store_codes: List of store codes

        Returns:
            Dictionary mapping store_code to list of employee info
        """
        results = {}
        for store_code in store_codes:
            employee_info = self.get_employee_info(store_code)
            results[store_code] = employee_info
        return results

    def get_hand_carry_upcs(self) -> List[str]:
        """
        Get list of hand carry item UPCs from PostgreSQL rps.carrier_item table

        Uses SSH tunnel if USE_SSH_TUNNEL=true in environment (for development).
        Connects directly if USE_SSH_TUNNEL=false (for deployment on same server).

        Returns:
            List of UPC strings for hand carry items
        """
        conn = None
        try:
            # Connect to PostgreSQL (uses SSH tunnel based on USE_SSH_TUNNEL env var)
            conn = get_postgres_connection()

            if not conn:
                print("WARNING: Failed to connect to PostgreSQL. Hand carry commission will not be calculated.")
                return []

            # Query hand carry UPCs from rps.carrier_item table
            query = """
                SELECT DISTINCT scan_upc::TEXT as scan_upc
                FROM rps.carrier_item
                WHERE scan_upc IS NOT NULL
                  AND TRIM(scan_upc::TEXT) != ''
                ORDER BY scan_upc
            """

            with conn.cursor() as cursor:
                cursor.execute(query)
                results = cursor.fetchall()

            # Extract UPCs from results (remove any whitespace)
            upcs = [str(row[0]).strip() for row in results if row[0]]

            print(f"[OK] Retrieved {len(upcs)} hand carry UPCs from PostgreSQL")
            return upcs

        except Exception as e:
            print(f"ERROR querying hand carry UPCs: {e}")
            return []

        finally:
            # Close connection (SSH tunnel is managed globally)
            if conn:
                try:
                    conn.close()
                except:
                    pass

    # ──────────────────────────────────────────────────────────────────
    # CR #59: Account-payable detection
    # ──────────────────────────────────────────────────────────────────
    def get_unpaid_bill_amounts(
        self, year: int, month: int,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Return per-bill unpaid balances for AR (Charge tender) bills
        created in (year, month) and still unpaid at end of month.

        Algorithm (see CR #59 Phase A research, two-tier matching):
          1. Pull every Charge tender event up to end-of-month for every
             customer who has at least one Charge bill in the target month.
          2. Initialise per-bill `remaining = original` from positive Charge
             events.
          3. Pass 1: apply payments with `REF_SALE_SID` to that exact bill.
          4. Pass 2: apply leftover payments FIFO to oldest open bills.
          5. Filter to bills created in the target month with `remaining > 0`.

        Returns:
            {
                bill_sid_str: {
                    'doc_no': str,
                    'customer_sid': str,
                    'original_charge': int,       # positive Charge tender amount
                    'remaining_unpaid': int,      # after ledger replay
                    'sale_total_amt': int,        # full bill total (with VAT)
                    'unpaid_ratio': float,        # remaining_unpaid / sale_total_amt
                                                  # — used by service for per-item
                                                  # withholding (uniform-distribution
                                                  # assumption when bill mixes Charge
                                                  # with other tenders).
                },
                ...
            }
            Empty dict on connection failure or no in-period bills.
        """
        target_month_str = f'{year:04d}-{month:02d}'
        # Exclusive upper bound: first day of NEXT month
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1
        period_end_exclusive = f'{next_year:04d}-{next_month:02d}-01 00:00:00'

        conn = get_oracle_connection()
        if not conn:
            return {}

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    self.queries.ORACLE_UNPAID_BILLS_BY_MONTH,
                    {
                        'target_month': target_month_str,
                        'period_end_exclusive': period_end_exclusive,
                    },
                )
                rows = cursor.fetchall()
                columns = [d[0].lower() for d in cursor.description]
        except Exception as e:
            print(f"ERROR querying unpaid bills: {e}")
            return {}
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if not rows:
            return {}

        # Group events by customer for per-customer ledger replay
        events_by_customer: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            row = dict(zip(columns, r))
            # Stringify SIDs to match elsewhere (see comments in
            # get_all_sales_data — Oracle SIDs exceed float64 precision).
            customer_sid = str(row['customer_sid'])
            row['customer_sid'] = customer_sid
            row['doc_sid'] = str(row['doc_sid'])
            row['ref_sale_sid'] = str(row['ref_sale_sid']) if row['ref_sale_sid'] is not None else None
            events_by_customer.setdefault(customer_sid, []).append(row)

        # Per-customer ledger replay
        result: Dict[str, Dict[str, Any]] = {}
        for customer_sid, events in events_by_customer.items():
            bills, payments = self._replay_charge_ledger(events)
            # Keep only target-month bills with remaining > 0
            for bill_sid, b in bills.items():
                if b['post_month'] != target_month_str:
                    continue
                if b['remaining'] <= 0:
                    continue
                sale_total = int(b['sale_total_amt'] or 0)
                # Bill total can theoretically be 0 in malformed data; guard
                # against div-by-zero and default to no withholding.
                ratio = (b['remaining'] / sale_total) if sale_total > 0 else 0.0
                result[bill_sid] = {
                    'doc_no':           str(b['doc_no']),
                    'customer_sid':     customer_sid,
                    'original_charge':  int(b['original']),
                    'remaining_unpaid': int(b['remaining']),
                    'sale_total_amt':   sale_total,
                    'unpaid_ratio':     ratio,
                }
        return result

    @staticmethod
    def _replay_charge_ledger(events: List[Dict[str, Any]]):
        """Two-tier (REF_SALE_SID → FIFO) replay of a single customer's
        Charge tender events. See get_unpaid_bill_amounts for algorithm.

        Args:
            events: list of dicts with keys doc_sid, doc_no, charge_amount,
                    post_date, ref_sale_sid, sale_total_amt, post_month.
                    Must be sorted by post_date.
        Returns:
            (bills, payments) where bills is dict[doc_sid -> bill record]
            and payments is the (now-exhausted) list of payment records.
        """
        bills: Dict[str, Dict[str, Any]] = {}
        payments: List[Dict[str, Any]] = []
        for e in events:
            amount = int(e['charge_amount'])
            if amount > 0:
                bills[e['doc_sid']] = {
                    'doc_no':         e['doc_no'],
                    'original':       amount,
                    'remaining':      amount,
                    'sale_total_amt': e['sale_total_amt'],
                    'post_month':     e['post_month'],
                }
            else:
                payments.append({
                    'amount':    -amount,    # positive magnitude
                    'remaining': -amount,
                    'ref':       e['ref_sale_sid'],
                })

        # Pass 1: REF_SALE_SID
        for p in payments:
            if not p['ref'] or p['ref'] not in bills:
                continue
            b = bills[p['ref']]
            consumed = min(b['remaining'], p['remaining'])
            b['remaining'] -= consumed
            p['remaining'] -= consumed

        # Pass 2: FIFO across remaining open bills (bills dict insertion
        # order matches event order, which is chronological).
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

        return bills, payments
