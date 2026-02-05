"""
Data Formatting Utilities
"""
from datetime import datetime
from typing import Any, Dict, List


def format_oracle_date(date_obj: Any) -> str:
    """
    Format Oracle date object to string

    Args:
        date_obj: Oracle date object

    Returns:
        Formatted date string
    """
    if date_obj is None:
        return None

    if isinstance(date_obj, datetime):
        return date_obj.strftime('%Y-%m-%d %H:%M:%S')

    return str(date_obj)


def format_currency(amount: float, currency: str = 'VND') -> str:
    """
    Format amount as currency

    Args:
        amount: Numeric amount
        currency: Currency code

    Returns:
        Formatted currency string
    """
    if amount is None:
        return f"0 {currency}"

    return f"{amount:,.0f} {currency}"


def sanitize_response_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize response data by removing None values and formatting dates

    Args:
        data: Dictionary to sanitize

    Returns:
        Sanitized dictionary
    """
    sanitized = {}
    for key, value in data.items():
        if value is None:
            sanitized[key] = None
        elif isinstance(value, datetime):
            sanitized[key] = format_oracle_date(value)
        else:
            sanitized[key] = value

    return sanitized


def format_api_response(success: bool, data: Any = None, error: str = None, metadata: Dict = None) -> Dict:
    """
    Standard API response format

    Args:
        success: Whether the request was successful
        data: Response data
        error: Error message if any
        metadata: Additional metadata

    Returns:
        Formatted API response dictionary
    """
    response = {'success': success}

    if data is not None:
        response['data'] = data

    if error is not None:
        response['error'] = error

    if metadata is not None:
        response['metadata'] = metadata

    return response


def format_pagination(items: List, page: int, page_size: int, total_count: int) -> Dict:
    """
    Format paginated response

    Args:
        items: List of items for current page
        page: Current page number
        page_size: Number of items per page
        total_count: Total number of items

    Returns:
        Formatted pagination dictionary
    """
    total_pages = (total_count + page_size - 1) // page_size

    return {
        'items': items,
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total_items': total_count,
            'total_pages': total_pages,
            'has_next': page < total_pages,
            'has_previous': page > 1
        }
    }
