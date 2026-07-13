"""Reports API routes (CR #78 — live sale comparison)."""
from flask import Blueprint, jsonify, request

from app.core.auth.middleware import token_required
from .service import ReportsService

reports_bp = Blueprint('reports', __name__, url_prefix='/api/v1/reports')
reports_service = ReportsService()

# Service error code → HTTP status.
_ERROR_STATUS = {
    'INVALID_INPUT': 400,
    'SERVER_ERROR':  500,
}


@reports_bp.route('/sale-comparison', methods=['GET'])
@token_required
def get_sale_comparison():
    """Two independent date ranges → revenue / bill-count / avg-bill and the
    new-vs-returning customer breakdown for each period.

    Query params (all required): from_a, to_a, from_b, to_b (YYYY-MM-DD).
    """
    required = ('from_a', 'to_a', 'from_b', 'to_b')
    params = {k: request.args.get(k) for k in required}
    missing = [k for k in required if not params[k]]
    if missing:
        return jsonify({
            'success': False,
            'error': f"Missing required query param(s): {', '.join(missing)}",
            'code': 'INVALID_INPUT',
        }), 400

    result = reports_service.get_sale_comparison(
        from_a=params['from_a'], to_a=params['to_a'],
        from_b=params['from_b'], to_b=params['to_b'],
    )
    if not result['success']:
        status = _ERROR_STATUS.get(result.get('code'), 400)
        return jsonify(result), status
    return jsonify(result), 200
