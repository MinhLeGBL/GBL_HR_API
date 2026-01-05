"""
Sales Service - Business logic for sales operations
"""
from typing import List, Dict, Any
from app.repositories.sales_repository import RetailSalesRepository


class SalesService:
    """Business logic layer for sales operations"""

    def __init__(self):
        self.repository = RetailSalesRepository()

    def get_detailed_sales_report(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        Get detailed sales report with business logic applied

        Args:
            start_date: Start date in 'YYYY-MM-DD HH:MI:SS' format
            end_date: End date in 'YYYY-MM-DD HH:MI:SS' format

        Returns:
            List of sales transactions with details
        """
        # Add any business logic here before/after repository call
        return self.repository.get_detailed_sales_report(start_date, end_date)

    def get_store_sales_summary(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        Get store sales summary with aggregations

        Args:
            start_date: Start date in 'YYYY-MM-DD HH:MI:SS' format
            end_date: End date in 'YYYY-MM-DD HH:MI:SS' format

        Returns:
            List of store sales summaries
        """
        return self.repository.get_store_sales_summary(start_date, end_date)

    def get_customer_analysis(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        Get customer purchase analysis

        Args:
            start_date: Start date in 'YYYY-MM-DD HH:MI:SS' format
            end_date: End date in 'YYYY-MM-DD HH:MI:SS' format

        Returns:
            List of customer purchase analytics
        """
        return self.repository.get_customer_analysis(start_date, end_date)

    def get_sales_performance_metrics(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        Get overall sales performance metrics (business logic example)

        Args:
            start_date: Start date in 'YYYY-MM-DD HH:MI:SS' format
            end_date: End date in 'YYYY-MM-DD HH:MI:SS' format

        Returns:
            Dictionary with calculated performance metrics
        """
        # Get data from repository
        store_summary = self.repository.get_store_sales_summary(start_date, end_date)

        if not store_summary:
            return {
                'total_sales': 0,
                'total_transactions': 0,
                'average_transaction_value': 0,
                'total_stores': 0
            }

        # Apply business logic calculations
        total_sales = sum(item.get('TOTAL_SALES', 0) for item in store_summary)
        total_transactions = sum(item.get('TOTAL_TRANSACTIONS', 0) for item in store_summary)
        total_stores = len(set(item.get('STORE_CODE') for item in store_summary))

        return {
            'total_sales': total_sales,
            'total_transactions': total_transactions,
            'average_transaction_value': total_sales / total_transactions if total_transactions > 0 else 0,
            'total_stores': total_stores,
            'period': {
                'start_date': start_date,
                'end_date': end_date
            }
        }
