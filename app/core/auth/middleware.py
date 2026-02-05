"""
Shared authentication middleware and decorators.

All auth decorators are defined here to avoid duplication across route files.
Route modules should import decorators (and auth_service if needed) from this module.
"""
from functools import wraps
from flask import jsonify, request, g
from app.core.auth.service import AuthService, UserRole

auth_service = AuthService()


def token_required(f):
    """Decorator to require a valid JWT token.

    Sets g.sid, g.email, g.role, g.department_id, g.department_code.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None

        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header[7:]

        if not token:
            return jsonify({
                'success': False,
                'error': 'Authorization token is missing'
            }), 401

        payload = auth_service.verify_token(token)
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401

        if payload.get('type') != 'access':
            return jsonify({
                'success': False,
                'error': 'Invalid token type'
            }), 401

        g.sid = payload.get('sid')
        g.email = payload.get('email')
        g.role = payload.get('role')

        user = auth_service.get_user_by_sid(g.sid)
        if user:
            g.department_id = user.get('department_id')
            g.department_code = user.get('department', {}).get('code') if user.get('department') else None

        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    """Decorator to require admin role."""
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        if g.role != UserRole.ADMIN:
            return jsonify({
                'success': False,
                'error': 'Admin access required'
            }), 403
        return f(*args, **kwargs)
    return decorated


def manager_required(f):
    """Decorator to require manager or admin role."""
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        if g.role not in [UserRole.ADMIN, UserRole.MANAGER]:
            return jsonify({
                'success': False,
                'error': 'Manager or admin access required'
            }), 403
        return f(*args, **kwargs)
    return decorated


def admin_or_hr_it_manager_required(f):
    """Decorator to require admin or HR/IT department manager."""
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        if g.role == UserRole.ADMIN:
            return f(*args, **kwargs)

        if g.role == UserRole.MANAGER and getattr(g, 'department_code', None) in ['HR', 'IT']:
            return f(*args, **kwargs)

        return jsonify({
            'success': False,
            'error': 'Admin or HR/IT department manager access required'
        }), 403

    return decorated


def admin_or_it_manager_required(f):
    """Decorator to require admin or IT department manager."""
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        if g.role == UserRole.ADMIN:
            return f(*args, **kwargs)

        if g.role == UserRole.MANAGER and getattr(g, 'department_code', None) == 'IT':
            return f(*args, **kwargs)

        return jsonify({
            'success': False,
            'error': 'Admin or IT department manager access required'
        }), 403

    return decorated
