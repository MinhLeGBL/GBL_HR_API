"""
Input Validation Utilities
"""
from datetime import datetime
from typing import Tuple, Optional


def validate_date_format(date_string: str, date_format: str = '%Y-%m-%d %H:%M:%S') -> Tuple[bool, Optional[str]]:
    """
    Validate date string format

    Args:
        date_string: Date string to validate
        date_format: Expected date format (default: 'YYYY-MM-DD HH:MI:SS')

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not date_string:
        return False, "Date string is required"

    try:
        datetime.strptime(date_string, date_format)
        return True, None
    except ValueError:
        return False, f"Invalid date format. Expected format: {date_format}"


def validate_date_range(start_date: str, end_date: str) -> Tuple[bool, Optional[str]]:
    """
    Validate that start_date is before end_date

    Args:
        start_date: Start date string
        end_date: End date string

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        start = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S')
        end = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S')

        if start >= end:
            return False, "start_date must be before end_date"

        return True, None
    except ValueError as e:
        return False, f"Date parsing error: {str(e)}"


def validate_required_fields(data: dict, required_fields: list) -> Tuple[bool, Optional[str]]:
    """
    Validate that all required fields are present in data

    Args:
        data: Dictionary to validate
        required_fields: List of required field names

    Returns:
        Tuple of (is_valid, error_message)
    """
    missing_fields = [field for field in required_fields if field not in data or data[field] is None]

    if missing_fields:
        return False, f"Missing required fields: {', '.join(missing_fields)}"

    return True, None


def validate_store_code(store_code: str) -> Tuple[bool, Optional[str]]:
    """
    Validate store code format

    Args:
        store_code: Store code to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not store_code:
        return False, "Store code is required"

    if not isinstance(store_code, str):
        return False, "Store code must be a string"

    # Add specific store code validation logic here
    return True, None
