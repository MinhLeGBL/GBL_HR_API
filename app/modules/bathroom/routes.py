"""
Bathroom Products API Routes
"""
from flask import Blueprint, jsonify, request
from app.core.auth.middleware import token_required, manager_required
from .service import BathroomService

bathroom_bp = Blueprint('bathroom', __name__, url_prefix='/api/v1/bathroom')
bathroom_service = BathroomService()


@bathroom_bp.route('/products', methods=['GET'])
@token_required
def get_products():
    brand = request.args.get('brand')
    search = request.args.get('search')
    page = request.args.get('page', type=int)
    per_page = request.args.get('per_page', default=100, type=int)
    per_page = min(max(per_page, 1), 500)
    result = bathroom_service.get_products(brand=brand, search=search, page=page, per_page=per_page)
    if not result['success']:
        return jsonify(result), 500
    return jsonify(result), 200


@bathroom_bp.route('/brand-settings', methods=['GET'])
@token_required
def get_brand_settings():
    result = bathroom_service.get_brand_settings()
    if not result['success']:
        return jsonify(result), 500
    return jsonify(result), 200


@bathroom_bp.route('/brand-settings', methods=['PUT'])
@manager_required
def update_brand_settings():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    brands = data.get('brands')
    tax_rates = data.get('tax_rates')

    if not brands or not isinstance(brands, list):
        return jsonify({'success': False, 'error': 'brands array is required'}), 400
    if not tax_rates or not isinstance(tax_rates, dict):
        return jsonify({'success': False, 'error': 'tax_rates object is required'}), 400

    result = bathroom_service.update_brand_settings(brands=brands, tax_rates=tax_rates)
    if not result['success']:
        return jsonify(result), 400
    return jsonify(result), 200
