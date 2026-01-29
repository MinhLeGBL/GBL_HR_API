"""
User Management API Routes
"""
from functools import wraps
from flask import Blueprint, jsonify, request, g
from app.services.auth_service import AuthService, UserRole

user_bp = Blueprint('users', __name__, url_prefix='/api/v1/users')
auth_service = AuthService()


# ==================== Access Control ====================

def token_required(f):
    """Decorator to require a valid JWT token"""
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


def admin_or_it_manager_required(f):
    """Decorator to require admin role OR IT department manager"""
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        # Admin always has access
        if g.role == UserRole.ADMIN:
            return f(*args, **kwargs)

        # IT department manager has access
        if g.role == UserRole.MANAGER and g.department_code == 'IT':
            return f(*args, **kwargs)

        return jsonify({
            'success': False,
            'error': 'Admin or IT department manager access required'
        }), 403

    return decorated


# ==================== User Management Routes ====================

@user_bp.route('', methods=['GET'])
@admin_or_it_manager_required
def get_all_users():
    """
    Get all users (admin or IT manager)

    Response:
        {
            "success": true,
            "users": [
                {
                    "sid": 100000001,
                    "email": "john@example.com",
                    "full_name": "John Doe",
                    "role": "manager",
                    "department_id": 1,
                    "department": {
                        "id": 1,
                        "code": "HR",
                        "name": "Human Resources"
                    },
                    "is_active": true,
                    "created_at": "2026-01-01T00:00:00Z",
                    "last_login": "2026-01-29T10:00:00Z"
                }
            ]
        }
    """
    result = auth_service.get_all_users()

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@user_bp.route('/<int:sid>', methods=['GET'])
@admin_or_it_manager_required
def get_user(sid):
    """
    Get a specific user by SID (admin or IT manager)

    Response:
        {
            "success": true,
            "user": {
                "sid": 100000001,
                "email": "john@example.com",
                "full_name": "John Doe",
                "role": "manager",
                "department_id": 1,
                "department": {...},
                "is_active": true,
                "created_at": "...",
                "last_login": "..."
            }
        }
    """
    user = auth_service.get_user_by_sid(sid)

    if user:
        return jsonify({
            'success': True,
            'user': user
        }), 200

    return jsonify({
        'success': False,
        'error': 'User not found'
    }), 404


@user_bp.route('/<int:sid>', methods=['PUT'])
@admin_or_it_manager_required
def update_user(sid):
    """
    Update a user (admin or IT manager)

    Request Body (all fields optional):
        {
            "role": "admin" | "manager" | "staff",
            "department_id": 1,
            "is_active": true
        }

    Response:
        {
            "success": true,
            "user": {...}
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body is required'
        }), 400

    # Validate role if provided
    if 'role' in data and data['role'] not in [UserRole.ADMIN, UserRole.MANAGER, UserRole.STAFF]:
        return jsonify({
            'success': False,
            'error': 'Invalid role. Must be: admin, manager, or staff'
        }), 400

    # Only allow updating specific fields
    allowed_updates = {}
    if 'role' in data:
        allowed_updates['role'] = data['role']
    if 'department_id' in data:
        allowed_updates['department_id'] = data['department_id']
    if 'is_active' in data:
        allowed_updates['is_active'] = data['is_active']

    if not allowed_updates:
        return jsonify({
            'success': False,
            'error': 'No valid fields to update. Allowed: role, department_id, is_active'
        }), 400

    result = auth_service.update_user(sid, allowed_updates)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400
