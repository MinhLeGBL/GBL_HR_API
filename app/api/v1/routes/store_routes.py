"""
Store API Routes - PostgreSQL-based store management
"""
from functools import wraps
from flask import Blueprint, jsonify, request, g
from app.services.store_service import StoreService
from app.services.auth_service import AuthService, UserRole

store_bp = Blueprint('stores', __name__, url_prefix='/api/v1/stores')
store_service = StoreService()
auth_service = AuthService()


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

        return f(*args, **kwargs)

    return decorated


@store_bp.route('', methods=['GET'])
@token_required
def get_all_stores():
    """
    Get all stores

    Response:
        {
            "success": true,
            "stores": [
                {
                    "id": 1,
                    "store_code": "ST001",
                    "store_name": "Store One",
                    "store_rp_sid": "RP12345"
                }
            ]
        }
    """
    result = store_service.get_all_stores()

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@store_bp.route('/init', methods=['POST'])
@token_required
def init_stores_table():
    """Initialize stores table (admin or HR/IT manager)"""
    if g.role != UserRole.ADMIN:
        # Also allow HR/IT managers
        user = auth_service.get_user_by_sid(g.sid)
        dept_code = user.get('department', {}).get('code') if user and user.get('department') else None
        if not (g.role == UserRole.MANAGER and dept_code in ['HR', 'IT']):
            return jsonify({
                'success': False,
                'error': 'Admin or HR/IT manager access required'
            }), 403

    result = store_service.init_database()

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500
