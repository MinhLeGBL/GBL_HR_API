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
    'sale_id', 'upc', 'bill_number', 'doc_store_code', 'sale_date', 'sale_time',
    'customer_sid', 'employee_sid', 'employee_username', 'store_code',
    'vendor_code', 'is_jewelry', 'category', 'department', 'discount_rate',
    'revenue_with_vat', 'revenue_before_vat'
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
        # `EMPLOYEE_COMMISSION_EXCEPTIONS`). Stringify SIDs before pandas
        # ever sees them so the column lands as object dtype with intact
        # values; downstream code compares string-to-string.
        for row in results:
            for field in ('SALE_ID', 'EMPLOYEE_SID', 'CUSTOMER_SID'):
                if row.get(field) is not None:
                    row[field] = str(row[field])

        df = pd.DataFrame(results)

        if df.empty:
            return pd.DataFrame(columns=ALL_SALES_COLUMNS)

        df.columns = df.columns.str.lower()
        if 'upc_clean' not in df.columns and 'upc' in df.columns:
            df['upc_clean'] = df['upc'].astype(str).str.strip()

        # Only COSM department items with HEA vendor are not eligible for commission.
        # Re-attribute to SYSADMIN so they count toward store revenue total
        # but not toward any employee's personal total or commission.
        # Non-HEA COSM items (e.g. NOTES DE BAS DE PAJE, ANN QUEEN) stay with
        # the original employee and are classified as fashion (FP/MD).
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
