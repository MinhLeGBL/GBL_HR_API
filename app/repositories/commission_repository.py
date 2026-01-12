"""
Commission Repository for executing commission-related queries
"""
from typing import List, Dict, Any, Optional
from app.database.connection import get_oracle_connection
from app.queries.commission_queries import CommissionQueries


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
