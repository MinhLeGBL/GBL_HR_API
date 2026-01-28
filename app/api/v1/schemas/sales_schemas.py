"""
Sales API Request/Response Schemas
"""
from typing import Optional
from datetime import datetime


class SalesReportRequest:
    """Sales report request validation"""

    @staticmethod
    def validate(data: dict) -> tuple[bool, Optional[str]]:
        """
        Validate sales report request

        Returns:
            Tuple of (is_valid, error_message)
        """
        start_date = data.get('start_date')
        end_date = data.get('end_date')

        if not start_date:
            return False, "start_date is required"

        if not end_date:
            return False, "end_date is required"

        # Basic date format validation (YYYY-MM-DD HH24:MI:SS)
        try:
            datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S')
            datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return False, "Invalid date format. Use 'YYYY-MM-DD HH:MI:SS'"

        return True, None


class SalesReportResponse:
    """Sales report response schema"""

    @staticmethod
    def format(report_data: list, report_type: str) -> dict:
        """Format sales report for API response"""
        return {
            'report_type': report_type,
            'total_records': len(report_data),
            'data': report_data
        }


class StoreSalesSummaryResponse:
    """Store sales summary response schema"""

    @staticmethod
    def format(summary_data: list) -> dict:
        """Format store sales summary for API response"""
        return {
            'summary_type': 'store_sales',
            'total_stores': len(set(item.get('STORE_CODE') for item in summary_data)),
            'data': summary_data
        }


class CustomerAnalysisResponse:
    """Customer analysis response schema"""

    @staticmethod
    def format(analysis_data: list) -> dict:
        """Format customer analysis for API response"""
        return {
            'analysis_type': 'customer_purchases',
            'total_customers': len(analysis_data),
            'data': analysis_data
        }
