"""
Custom Decorators for API endpoints
"""
from functools import wraps
from flask import request, jsonify
import time


def timing_decorator(f):
    """
    Decorator to measure endpoint execution time

    Usage:
        @timing_decorator
        def my_endpoint():
            ...
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = f(*args, **kwargs)
        execution_time = time.time() - start_time

        # Add execution time to response if it's a tuple (response, status_code)
        if isinstance(result, tuple) and len(result) >= 1:
            response_data = result[0]
            if hasattr(response_data, 'json'):
                # It's a Flask Response object
                pass
            else:
                # Add metadata to response
                if 'metadata' not in response_data:
                    response_data['metadata'] = {}
                response_data['metadata']['execution_time_seconds'] = round(execution_time, 3)

        return result

    return wrapper


def validate_json(f):
    """
    Decorator to validate that request contains JSON data

    Usage:
        @validate_json
        def my_endpoint():
            ...
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Request must be JSON'
            }), 400

        return f(*args, **kwargs)

    return wrapper


def require_fields(*required_fields):
    """
    Decorator to validate required fields in JSON request

    Usage:
        @require_fields('start_date', 'end_date')
        def my_endpoint():
            ...
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not request.is_json:
                return jsonify({
                    'success': False,
                    'error': 'Request must be JSON'
                }), 400

            data = request.get_json()
            missing_fields = [field for field in required_fields if field not in data]

            if missing_fields:
                return jsonify({
                    'success': False,
                    'error': f"Missing required fields: {', '.join(missing_fields)}"
                }), 400

            return f(*args, **kwargs)

        return wrapper
    return decorator


def handle_exceptions(f):
    """
    Decorator to handle exceptions in endpoints

    Usage:
        @handle_exceptions
        def my_endpoint():
            ...
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ValueError as e:
            return jsonify({
                'success': False,
                'error': f'Validation error: {str(e)}'
            }), 400
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Internal server error: {str(e)}'
            }), 500

    return wrapper
