"""
Authentication API Routes
"""
from functools import wraps
from flask import Blueprint, jsonify, request, g
from app.services.auth_service import AuthService, UserRole

auth_bp = Blueprint('auth', __name__, url_prefix='/api/v1/auth')
auth_service = AuthService()


# ==================== Authentication Middleware ====================

def token_required(f):
    """Decorator to require a valid JWT token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None

        # Get token from header
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header[7:]

        if not token:
            return jsonify({
                'success': False,
                'error': 'Authorization token is missing'
            }), 401

        # Verify token
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

        # Store user info in g for access in route handlers
        g.sid = payload.get('sid')
        g.email = payload.get('email')
        g.role = payload.get('role')

        # Get department_id from database for permission checks
        user = auth_service.get_user_by_sid(g.sid)
        if user:
            g.department_id = user.get('department_id')

        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    """Decorator to require admin role"""
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
    """Decorator to require manager or admin role"""
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


# ==================== Public Routes ====================

@auth_bp.route('/init-db', methods=['POST'])
def init_database():
    """
    Initialize the users database table
    Run this once to set up the authentication system

    Note: In production, this should be protected or removed
    """
    result = auth_service.init_database()
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


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

    # Validate role if provided
    role = data.get('role', UserRole.STAFF)
    if role not in [UserRole.ADMIN, UserRole.MANAGER, UserRole.STAFF]:
        return jsonify({
            'success': False,
            'error': 'Invalid role. Must be: admin, manager, or staff'
        }), 400

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

    # Validate role if provided
    if 'role' in data and data['role'] not in [UserRole.ADMIN, UserRole.MANAGER, UserRole.STAFF]:
        return jsonify({
            'success': False,
            'error': 'Invalid role. Must be: admin, manager, or staff'
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
