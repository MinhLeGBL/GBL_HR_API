"""
Authentication API Routes
"""
from flask import Blueprint, jsonify, request, g
from app.core.auth.service import UserRole
from app.core.auth.middleware import token_required, admin_required, manager_required, auth_service

auth_bp = Blueprint('auth', __name__, url_prefix='/api/v1/auth')


# ==================== Public Routes ====================

@auth_bp.route('/login', methods=['POST'])
def login():
    """
    Authenticate user by email and return tokens

    Request Body:
        {
            "email": "string",
            "password": "string"
        }

    Returns:
        {
            "success": true,
            "access_token": "string",
            "refresh_token": "string",
            "token_type": "bearer",
            "user": {
                "id": int,
                "sid": int,
                "email": "string",
                "full_name": "string",
                "role": "string"
            }
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body is required'
        }), 400

    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({
            'success': False,
            'error': 'Email and password are required'
        }), 400

    result = auth_service.authenticate(email, password)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 401


@auth_bp.route('/refresh', methods=['POST'])
def refresh_token():
    """
    Refresh access token using refresh token

    Request Body:
        {
            "refresh_token": "string"
        }

    Returns:
        {
            "success": true,
            "access_token": "string",
            "token_type": "bearer"
        }
    """
    data = request.get_json()

    if not data or not data.get('refresh_token'):
        return jsonify({
            'success': False,
            'error': 'Refresh token is required'
        }), 400

    result = auth_service.refresh_access_token(data['refresh_token'])

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 401


# ==================== Protected Routes ====================

@auth_bp.route('/me', methods=['GET'])
@token_required
def get_current_user():
    """
    Get current authenticated user details

    Requires: Valid access token

    Returns:
        {
            "success": true,
            "user": {
                "sid": int,
                "email": "string",
                "full_name": "string",
                "role": "string",
                "is_active": boolean,
                "created_at": "string",
                "last_login": "string"
            }
        }
    """
    user = auth_service.get_user_by_sid(g.sid)

    if user:
        return jsonify({
            'success': True,
            'user': user
        }), 200

    return jsonify({
        'success': False,
        'error': 'User not found'
    }), 404


@auth_bp.route('/change-password', methods=['POST'])
@token_required
def change_password():
    """
    Change current user's password

    Requires: Valid access token

    Request Body:
        {
            "old_password": "string",
            "new_password": "string"
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body is required'
        }), 400

    old_password = data.get('old_password')
    new_password = data.get('new_password')

    if not old_password or not new_password:
        return jsonify({
            'success': False,
            'error': 'Old password and new password are required'
        }), 400

    if len(new_password) < 6:
        return jsonify({
            'success': False,
            'error': 'New password must be at least 6 characters'
        }), 400

    result = auth_service.change_password(g.sid, old_password, new_password)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


# ==================== Admin Routes ====================

@auth_bp.route('/register', methods=['POST'])
@admin_required
def register_user():
    """
    Register a new user (admin only)

    Requires: Admin access token

    Request Body:
        {
            "email": "string",
            "password": "string",
            "full_name": "string",
            "role": "staff" | "manager" | "admin" (optional, default: staff)
        }

    Returns:
        {
            "success": true,
            "user": {
                "sid": int (auto-generated 9-digit ID, primary key),
                "email": "string",
                "full_name": "string",
                "role": "string",
                "is_active": boolean,
                "created_at": "string"
            }
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body is required'
        }), 400

    required_fields = ['email', 'password', 'full_name']
    for field in required_fields:
        if not data.get(field):
            return jsonify({
                'success': False,
                'error': f'{field} is required'
            }), 400

    # Validate password length
    if len(data['password']) < 6:
        return jsonify({
            'success': False,
            'error': 'Password must be at least 6 characters'
        }), 400

    role = data.get('role', UserRole.STAFF)

    result = auth_service.create_user(
        email=data['email'],
        password=data['password'],
        full_name=data['full_name'],
        role=role,
        department_id=data.get('department_id')
    )

    if result['success']:
        return jsonify(result), 201
    return jsonify(result), 400


@auth_bp.route('/users', methods=['GET'])
@admin_required
def get_all_users():
    """
    Get all users (admin only)

    Requires: Admin access token
    """
    result = auth_service.get_all_users()

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@auth_bp.route('/users/<int:sid>', methods=['GET'])
@admin_required
def get_user(sid):
    """
    Get a specific user by SID (admin only)

    Requires: Admin access token
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


@auth_bp.route('/users/<int:sid>', methods=['PUT'])
@admin_required
def update_user(sid):
    """
    Update a user (admin only)

    Requires: Admin access token

    Request Body (all fields optional):
        {
            "email": "string",
            "full_name": "string",
            "role": "staff" | "manager" | "admin",
            "is_active": boolean
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body is required'
        }), 400

    result = auth_service.update_user(sid, data)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


@auth_bp.route('/users/<int:sid>', methods=['DELETE'])
@admin_required
def delete_user(sid):
    """
    Delete a user (admin only)

    Requires: Admin access token
    """
    # Prevent deleting yourself
    if sid == g.sid:
        return jsonify({
            'success': False,
            'error': 'Cannot delete your own account'
        }), 400

    result = auth_service.delete_user(sid)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


# ==================== First Admin Setup ====================

@auth_bp.route('/setup-admin', methods=['POST'])
def setup_first_admin():
    """
    Create the first admin user
    This endpoint only works if no admin exists

    Request Body:
        {
            "email": "string",
            "password": "string",
            "full_name": "string"
        }

    Returns:
        {
            "success": true,
            "message": "Admin user created successfully",
            "user": {
                "sid": int (auto-generated 9-digit ID, primary key),
                "email": "string",
                "full_name": "string",
                "role": "admin",
                "is_active": boolean,
                "created_at": "string"
            }
        }
    """
    # First, check if any admin already exists
    result = auth_service.get_all_users()

    if result.get('success'):
        users = result.get('users', [])
        admin_exists = any(user['role'] == UserRole.ADMIN for user in users)

        if admin_exists:
            return jsonify({
                'success': False,
                'error': 'Admin user already exists. Use /register with admin token to create more users.'
            }), 403

    data = request.get_json()

    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body is required'
        }), 400

    required_fields = ['email', 'password', 'full_name']
    for field in required_fields:
        if not data.get(field):
            return jsonify({
                'success': False,
                'error': f'{field} is required'
            }), 400

    if len(data['password']) < 6:
        return jsonify({
            'success': False,
            'error': 'Password must be at least 6 characters'
        }), 400

    # Create admin user
    result = auth_service.create_user(
        email=data['email'],
        password=data['password'],
        full_name=data['full_name'],
        role=UserRole.ADMIN
    )

    if result['success']:
        return jsonify({
            'success': True,
            'message': 'Admin user created successfully',
            'user': result['user']
        }), 201
    return jsonify(result), 400


# ==================== Lookup Endpoints ====================

@auth_bp.route('/roles', methods=['GET'])
@token_required
def get_roles():
    """
    Get all roles (lookup data)

    Response:
        {
            "success": true,
            "roles": [
                {"id": 30001, "code": "ADMIN", "name": "Admin", "is_active": true},
                {"id": 30002, "code": "MANAGER", "name": "Manager", "is_active": true},
                {"id": 30003, "code": "STAFF", "name": "Staff", "is_active": true}
            ]
        }
    """
    result = auth_service.get_roles()

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500
