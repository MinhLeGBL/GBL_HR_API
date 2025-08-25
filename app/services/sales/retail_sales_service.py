"""
Retail Sales Service for executing sales queries
"""
from typing import List, Dict, Any, Optional
from datetime import datetime, date
from app.database.connection import get_oracle_connection
from app.queries.sales_reports.retail_queries import RetailSalesQueries


class RetailSalesService:
    """Service for executing retail sales-related reports and queries"""
    
    def __init__(self):
        self.queries = RetailSalesQueries()
    
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
    
    def get_detailed_sales_report(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        Execute the detailed sales report query
        
        Args:
            start_date: Start date in 'YYYY-MM-DD HH24:MI:SS' format
            end_date: End date in 'YYYY-MM-DD HH24:MI:SS' format
        """
        # Convert string dates to proper Oracle format
        start_datetime = f"TO_DATE('{start_date}', 'YYYY-MM-DD HH24:MI:SS')"
        end_datetime = f"TO_DATE('{end_date}', 'YYYY-MM-DD HH24:MI:SS')"
        
        # Replace placeholders in query
        query = self.queries.DETAILED_SALES_REPORT.replace(':start_date', start_datetime)
        query = query.replace(':end_date', end_datetime)
        
        return self.execute_query(query)
    
    def get_store_sales_summary(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Get sales summary by store"""
        start_datetime = f"TO_DATE('{start_date}', 'YYYY-MM-DD HH24:MI:SS')"
        end_datetime = f"TO_DATE('{end_date}', 'YYYY-MM-DD HH24:MI:SS')"
        
        query = self.queries.STORE_SALES_SUMMARY.replace(':start_date', start_datetime)
        query = query.replace(':end_date', end_datetime)
        
        return self.execute_query(query)
    
    def get_customer_analysis(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Get customer purchase analysis"""
        start_datetime = f"TO_DATE('{start_date}', 'YYYY-MM-DD HH24:MI:SS')"
        end_datetime = f"TO_DATE('{end_date}', 'YYYY-MM-DD HH24:MI:SS')"
        
        query = self.queries.CUSTOMER_ANALYSIS.replace(':start_date', start_datetime)
        query = query.replace(':end_date', end_datetime)
        
        return self.execute_query(query)
    
    def test_query(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Test a query and return execution info along with sample results
        
        Returns:
            Dictionary containing execution time, row count, and sample data
        """
        import time
        
        start_time = time.time()
        
        try:
            results = self.execute_query(query, parameters)
            execution_time = time.time() - start_time
            
            return {
                'success': True,
                'execution_time_seconds': round(execution_time, 3),
                'row_count': len(results),
                'sample_data': results[:5],  # First 5 rows
                'columns': list(results[0].keys()) if results else [],
                'parameters_used': parameters or {},
                'error': None
            }
            
        except Exception as e:
            execution_time = time.time() - start_time
            return {
                'success': False,
                'execution_time_seconds': round(execution_time, 3),
                'row_count': 0,
                'sample_data': [],
                'columns': [],
                'parameters_used': parameters or {},
                'error': str(e)
            }