"""
Employee API Request/Response Schemas
"""
from typing import Optional
from datetime import datetime


class EmployeeResponse:
    """Employee information response schema"""

    @staticmethod
    def format(employee_data: dict) -> dict:
        """Format employee data for API response"""
        return {
            'employee_sid': employee_data.get('EMPLOYEE_SID'),
            'employee_code': employee_data.get('EMPLOYEE_CODE'),
            'employee_name': employee_data.get('EMPLOYEE_NAME'),
            'employee_login': employee_data.get('EMPLOYEE_LOGIN'),
            'status': employee_data.get('EMPLOYEE_STATUS'),
            'store_code': employee_data.get('EMPLOYEE_STORE')
        }


class EmployeeListResponse:
    """Employee list response schema"""

    @staticmethod
    def format(employees: list) -> dict:
        """Format employee list for API response"""
        return {
            'total_count': len(employees),
            'employees': [EmployeeResponse.format(emp) for emp in employees]
        }
