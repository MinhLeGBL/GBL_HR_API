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


# Map service error codes → HTTP status. Service returns
# {success: False, error: '...', code: '<CODE>'}; routes look up the status.
_DRILLDOWN_ERROR_STATUS = {
    'NOT_COMPUTED':    503,
    'INVALID_SEGMENT': 400,
    'NOT_FOUND':       404,
}


def _drilldown_response(result):
    if result['success']:
        return jsonify(result), 200
    status = _DRILLDOWN_ERROR_STATUS.get(result.get('code'), 500)
    return jsonify(result), status


@crm_bp.route('/customers/<int:customer_sid>/drilldown', methods=['GET'])
@token_required
def get_customer_drilldown(customer_sid):
    return _drilldown_response(crm_service.get_customer_drilldown(customer_sid))


@crm_bp.route('/brand-drilldown', methods=['GET'])
@token_required
def get_brand_drilldown():
    brand_name = request.args.get('brand')
    segment    = request.args.get('segment')
    if not brand_name:
        return jsonify({'success': False,
                        'error':   'brand query param is required',
                        'code':    'INVALID_REQUEST'}), 400
    if not segment:
        return jsonify({'success': False,
                        'error':   'segment query param is required',
                        'code':    'INVALID_REQUEST'}), 400
    return _drilldown_response(crm_service.get_brand_drilldown(brand_name, segment))


@crm_bp.route('/product-analysis', methods=['GET'])
@token_required
def get_product_analysis():
    group_by = request.args.get('group_by')
    if group_by not in ('brand', 'category'):
        return jsonify({'success': False,
                        'error': "group_by must be 'brand' or 'category'"}), 400

    segment = request.args.get('segment') or None
    result = crm_service.get_product_analysis(group_by=group_by, segment=segment)
    if not result['success']:
        return jsonify(result), 503
    return jsonify(result), 200


@crm_bp.route('/admin/config', methods=['GET'])
@admin_required
def get_config():
    result = crm_service.get_config()
    return jsonify(result), 200


@crm_bp.route('/admin/config', methods=['PUT'])
@admin_required
def update_config():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Request body required'}), 400

    required = ('w_recency',  'w_frequency',  'w_monetary',
                'e_recency',  'e_frequency',  'e_monetary',
                'pw_recency', 'pw_frequency', 'pw_monetary')
    missing = [k for k in required if k not in data]
    if missing:
        return jsonify({'success': False, 'error': f'Missing fields: {", ".join(missing)}'}), 400

    config = {k: float(data[k]) for k in required}
    result = crm_service.update_config(config)
    if not result['success']:
        return jsonify(result), 400
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
