"""
User Management API Routes
"""
from flask import Blueprint, jsonify, request, g
from app.core.auth.service import UserRole
from app.core.auth.middleware import (
    token_required, admin_or_it_manager_required, auth_service
)

user_bp = Blueprint('users', __name__, url_prefix='/api/v1/users')


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


@user_bp.route('', methods=['POST'])
@token_required
def create_user():
    """
    Create a new user (admin or manager)

    Request Body:
        {
            "email": "newuser@company.com",
            "full_name": "Nguyen Van A",
            "password": "123456",
            "role": "staff",
            "department_id": 1,
            "is_active": true,
            "employee_sid": 300000001  # Optional - link to employee record
        }

    Response:
        {
            "success": true,
            "user": {...}
        }
    """
    # Only admin and manager can create users
    if g.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        return jsonify({
            'success': False,
            'error': 'Only admin or manager can create users'
        }), 403

    data = request.get_json()

    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body is required'
        }), 400

    # Validate required fields
    required_fields = ['email', 'full_name', 'password', 'role']
    for field in required_fields:
        if field not in data or not data[field]:
            return jsonify({
                'success': False,
                'error': f'Missing required field: {field}'
            }), 400

    # Only admin can create admin users
    if data['role'].lower() == UserRole.ADMIN and g.role != UserRole.ADMIN:
        return jsonify({
            'success': False,
            'error': 'Only admin can create admin users'
        }), 403

    result = auth_service.create_user(
        email=data['email'],
        password=data['password'],
        full_name=data['full_name'],
        role=data['role'],
        department_id=data.get('department_id'),
        employee_sid=data.get('employee_sid'),
        is_active=data.get('is_active', True)
    )

    if result['success']:
        return jsonify(result), 201
    return jsonify(result), 400


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

    # Only allow updating specific fields
    allowed_updates = {}
    if 'role' in data:
        allowed_updates['role'] = data['role']
    if 'department_id' in data:
        allowed_updates['department_id'] = data['department_id']
    if 'is_active' in data:
        allowed_updates['is_active'] = data['is_active']
    if 'employee_sid' in data:
        allowed_updates['employee_sid'] = data['employee_sid']

    if not allowed_updates:
        return jsonify({
            'success': False,
            'error': 'No valid fields to update. Allowed: role, department_id, is_active, employee_sid'
        }), 400

    result = auth_service.update_user(sid, allowed_updates)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


@user_bp.route('/<int:sid>/reset-password', methods=['POST'])
@token_required
def reset_user_password(sid):
    """
    Reset a user's password to default (123456) and force password change on next login.
    Auth: Admin or Manager.
    """
    if g.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        return jsonify({'success': False, 'error': 'Only admin or manager can reset passwords'}), 403

    result = auth_service.reset_password(sid)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


@user_bp.route('/<int:sid>', methods=['DELETE'])
@token_required
def delete_user(sid):
    """
    Delete a user

    Access Control:
        - Only admin can delete admin users
        - Users cannot delete themselves
        - Manager can delete staff and manager users

    Response:
        {
            "success": true
        }
    """
    # Users cannot delete themselves
    if g.sid == sid:
        return jsonify({
            'success': False,
            'error': 'Cannot delete yourself'
        }), 400

    # Get the target user to check their role
    target_user = auth_service.get_user_by_sid(sid)
    if not target_user:
        return jsonify({
            'success': False,
            'error': 'User not found'
        }), 404

    # Only admin can delete admin users
    if target_user.get('role') == UserRole.ADMIN and g.role != UserRole.ADMIN:
        return jsonify({
            'success': False,
            'error': 'Only admin can delete admin users'
        }), 403

    # Manager can only delete staff and manager users
    if g.role == UserRole.MANAGER:
        if target_user.get('role') not in [UserRole.STAFF, UserRole.MANAGER]:
            return jsonify({
                'success': False,
                'error': 'Manager can only delete staff or manager users'
            }), 403
    elif g.role != UserRole.ADMIN:
        return jsonify({
            'success': False,
            'error': 'Only admin or manager can delete users'
        }), 403

    result = auth_service.delete_user(sid)

    if result['success']:
        return jsonify({'success': True}), 200
    return jsonify(result), 400
