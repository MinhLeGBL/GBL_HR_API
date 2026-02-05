"""
Store API Routes - PostgreSQL-based store management
"""
from flask import Blueprint, jsonify, request
from app.services.store_service import StoreService
from app.api.v1.routes.auth_middleware import token_required, manager_required

store_bp = Blueprint('stores', __name__, url_prefix='/api/v1/stores')
store_service = StoreService()


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


@store_bp.route('', methods=['POST'])
@manager_required
def create_store():
    """Create a new store (admin or manager only)"""
    data = request.get_json()

    if not data:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    if not data.get('store_code') or not data.get('store_name'):
        return jsonify({'success': False, 'error': 'store_code and store_name are required'}), 400

    result = store_service.create_store(
        store_code=data['store_code'],
        store_name=data['store_name'],
        store_rp_sid=data.get('store_rp_sid')
    )

    if result['success']:
        return jsonify(result), 201
    return jsonify(result), 400
