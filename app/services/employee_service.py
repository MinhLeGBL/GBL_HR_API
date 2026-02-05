"""
Employee Service - Business logic for employee operations
"""
from typing import List, Dict, Any, Optional
from app.repositories.employee_repository import EmployeeRepository


class EmployeeService:
    """Business logic layer for employee operations"""

    def __init__(self):
        self.repository = EmployeeRepository(db_connection=None)

    def get_all_employees(self) -> List[Dict[str, Any]]:
        """
        Get all employees

        Returns:
            List of employee dictionaries
        """
        query = self.repository.queries.EMPLOYEE_INFO
        return self.repository.execute(query)

    def get_employee_by_code(self, employee_code: str) -> Optional[Dict[str, Any]]:
        """
        Get employee by employee code

        Args:
            employee_code: Employee code to search for

        Returns:
            Employee dictionary or None if not found
        """
        query = f"{self.repository.queries.EMPLOYEE_INFO} WHERE cust.UDF4_STRING = :employee_code"
        results = self.repository.execute(query, {'employee_code': employee_code})

        return results[0] if results else None

    def get_employees_by_store(self, store_code: str) -> List[Dict[str, Any]]:
        """
        Get all employees for a specific store

        Args:
            store_code: Store code to filter by

        Returns:
            List of employee dictionaries
        """
        query = f"{self.repository.queries.EMPLOYEE_INFO} WHERE s.STORE_CODE = :store_code"
        return self.repository.execute(query, {'store_code': store_code})

    def get_active_employees(self) -> List[Dict[str, Any]]:
        """
        Get all active employees

        Returns:
            List of active employee dictionaries
        """
        query = f"{self.repository.queries.EMPLOYEE_INFO} WHERE emp.USER_ACTIVE = 1"
        return self.repository.execute(query)

    def get_inactive_employees(self) -> List[Dict[str, Any]]:
        """
        Get all inactive employees

        Returns:
            List of inactive employee dictionaries
        """
        query = f"{self.repository.queries.EMPLOYEE_INFO} WHERE emp.USER_ACTIVE = 0"
        return self.repository.execute(query)
