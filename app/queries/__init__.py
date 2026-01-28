"""
SQL Queries - Oracle database queries for HR operations
"""
from .employee_queries import EmployeeQueries
from .sales_queries import RetailSalesQueries

__all__ = ['EmployeeQueries', 'RetailSalesQueries']