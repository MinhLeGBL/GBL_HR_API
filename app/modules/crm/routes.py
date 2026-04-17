"""CRM API routes — RFM customer segmentation (CR #40)."""
from flask import Blueprint, jsonify, request
from app.core.auth.middleware import token_required, admin_required
from .service import CRMService

crm_bp = Blueprint('crm', __name__, url_prefix='/api/v1/crm')
crm_service = CRMService()


@crm_bp.route('/customers', methods=['GET'])
@token_required
def get_customers():
    segment = request.args.get('segment')
    min_score = request.args.get('min_weighted_score', type=float)
    result = crm_service.get_customers(segment=segment, min_weighted_score=min_score)
    if not result['success']:
        return jsonify(result), 503
    return jsonify(result), 200


@crm_bp.route('/segments/summary', methods=['GET'])
@token_required
def get_segments_summary():
    result = crm_service.get_segments_summary()
    if not result['success']:
        return jsonify(result), 503
    return jsonify(result), 200


@crm_bp.route('/segments/trends', methods=['GET'])
@token_required
def get_segments_trends():
    months = request.args.get('months', default=12, type=int)
    months = max(1, min(months, 60))
    result = crm_service.get_segments_trends(months=months)
    return jsonify(result), 200


@crm_bp.route('/heatmap', methods=['GET'])
@token_required
def get_heatmap():
    result = crm_service.get_heatmap()
    if not result['success']:
        return jsonify(result), 503
    return jsonify(result), 200


@crm_bp.route('/admin/recompute', methods=['POST'])
@admin_required
def admin_recompute():
    try:
        summary = recompute()
        return jsonify(summary), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def recompute():
    """Import and run the recompute job. Kept as a top-level function for easy mocking in tests."""
    import importlib
    mod = importlib.import_module('scripts.jobs.crm_recompute')
    return mod.recompute()
