"""Reports API routes (CR #78 sale comparison, CR #79 pinned periods)."""
from flask import Blueprint, g, jsonify, request

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

    Query params (required): from_a, to_a, from_b, to_b (YYYY-MM-DD).
    Optional (CR #81): store_a, store_b — comma-separated `GET /stores` ids
    scoping each period to a union of stores; omitted → all stores.
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
        store_a=request.args.get('store_a'),
        store_b=request.args.get('store_b'),
    )
    if not result['success']:
        status = _ERROR_STATUS.get(result.get('code'), 400)
        return jsonify(result), status
    return jsonify(result), 200


# ----------------------------------------------------------------------
# CR #79 — per-user pinned periods
# ----------------------------------------------------------------------
@reports_bp.route('/live-comparison/pins', methods=['GET'])
@token_required
def get_pins():
    """The caller's pinned periods (200 always; unset → null)."""
    result = reports_service.get_pins(user_id=g.sid)
    if not result['success']:
        return jsonify(result), _ERROR_STATUS.get(result.get('code'), 500)
    return jsonify(result), 200


@reports_bp.route('/live-comparison/pins', methods=['PUT'])
@token_required
def put_pins():
    """Replace the caller's pins. Body: {period_a, period_b} — each a
    {from, to, store_ids?} or null (CR #81: store_ids is an optional int array,
    []/omitted = all stores). Returns the persisted pins."""
    body = request.get_json(silent=True)
    result = reports_service.set_pins(user_id=g.sid, body=body)
    if not result['success']:
        return jsonify(result), _ERROR_STATUS.get(result.get('code'), 400)
    return jsonify(result), 200
