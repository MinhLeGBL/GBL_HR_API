"""
Commission Repository for executing commission-related queries
"""
from typing import List, Dict, Any, Optional
import pandas as pd
from app.core.database.connection import get_oracle_connection, get_postgres_connection
from app.modules.commission.queries import CommissionQueries


class CommissionRepository:
    """Repository for commission-related data access"""

    def __init__(self):
        self.queries = CommissionQueries()

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
        Get store sales data with revenue breakdown

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

    def get_employee_sales_data(
        self,
        store_code: str,
        start_date: str,
        end_date: str
    ) -> List[Dict[str, Any]]:
        """
        Get employee sales data for a specific store

        Args:
            store_code: Store code
            start_date: Start date in 'YYYY-MM-DD HH:MI:SS' format
            end_date: End date in 'YYYY-MM-DD HH:MI:SS' format

        Returns:
            List of employee sales data
        """
        parameters = {
            'store_code': store_code,
            'start_date': start_date,
            'end_date': end_date
        }

        return self.execute_query(self.queries.EMPLOYEE_SALES_DATA, parameters)

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

    def get_personal_commission_sales_data(
        self,
        year: int,
        month: int
    ) -> pd.DataFrame:
        """
        Get sales data for personal commission calculation as pandas DataFrame

        Args:
            year: Year for the commission period
            month: Month for the commission period

        Returns:
            DataFrame containing sales data with columns:
            ['sale_id', 'employee_id', 'store_id', 'sale_date', 'sale_time',
             'revenue_before_vat', 'discount_rate', 'department']
        """
        year_month = f"{year:04d}-{month:02d}"
        parameters = {'year_month': year_month}

        results = self.execute_query(self.queries.PERSONAL_COMMISSION_SALES_DATA, parameters)

        # Convert to pandas DataFrame
        df = pd.DataFrame(results)

        # If no data, return empty DataFrame with expected columns
        if df.empty:
            return pd.DataFrame(columns=[
                'sale_id', 'employee_id', 'store_id', 'sale_date', 'sale_time',
                'sale_datetime', 'revenue_before_vat', 'discount_rate', 'department'
            ])

        return df

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

    def get_multiple_stores_employee_sales_data(
        self,
        store_codes: List[str],
        start_date: str,
        end_date: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get employee sales data for multiple stores

        Args:
            store_codes: List of store codes
            start_date: Start date in 'YYYY-MM-DD HH:MI:SS' format
            end_date: End date in 'YYYY-MM-DD HH24:MI:SS' format

        Returns:
            Dictionary mapping store_code to list of employee sales data
        """
        results = {}
        for store_code in store_codes:
            employee_data = self.get_employee_sales_data(store_code, start_date, end_date)
            results[store_code] = employee_data
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

            print(f"✓ Retrieved {len(upcs)} hand carry UPCs from PostgreSQL")
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
