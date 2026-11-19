"""Tracking system API — the employee-number gate. See service.py for the model."""
from functools import wraps

from flask import Blueprint, g, jsonify, request

from app.core.auth.middleware import token_required
from .service import TrackingSystemService

tracking_system_bp = Blueprint('tracking_system', __name__,
                               url_prefix='/api/v1/tracking-system')
tracking_system_service = TrackingSystemService()

UNLOCK_HEADER = 'X-Unlock-Token'

_ERROR_STATUS = {
    'INVALID_INPUT': 400,
    'INVALID_CODE':  403,
    'LOCKED':        403,
    'NOT_FOUND':     404,
    'RATE_LIMITED':  429,
    'SERVER_ERROR':  500,
}


def unlock_required(f):
    """Use under `@token_required`: refuses unless the request carries a live
    unlock token for the signed-in user who is still on the allowlist."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get(UNLOCK_HEADER)
        if not tracking_system_service.verify_unlock(g.sid, token):
            return jsonify({'success': False, 'code': 'LOCKED',
                            'error': 'Locked'}), 403
        return f(*args, **kwargs)
    return decorated


@tracking_system_bp.route('/unlock', methods=['POST'])
@token_required
def unlock():
    """Body: {"code": "<your employee number>"} → {"unlock_token", "expires_at"}."""
    data = request.get_json(silent=True) or {}
    result = tracking_system_service.unlock(g.sid, data.get('code'))
    if result.get('success'):
        return jsonify(result), 200
    return jsonify(result), _ERROR_STATUS.get(result.get('code'), 500)


@tracking_system_bp.route('/session', methods=['GET'])
@token_required
@unlock_required
def session():
    """Is the caller's unlock still good? The page calls this on load."""
    return jsonify({'success': True, 'unlocked': True}), 200


def _reply(result):
    if result.get('success'):
        return jsonify(result), 200
    return jsonify(result), _ERROR_STATUS.get(result.get('code'), 500)


# ---- In: one row per item. Tracking cost = (entered - landed) x qty.

@tracking_system_bp.route('/in', methods=['GET'])
@token_required
@unlock_required
def list_in():
    return _reply(tracking_system_service.list_in())


@tracking_system_bp.route('/in/search', methods=['GET'])
@token_required
@unlock_required
def search_in():
    """?q= UPC prefix or ALU substring → Oracle items with landed cost."""
    return _reply(tracking_system_service.search_items(request.args.get('q')))


@tracking_system_bp.route('/in/lookup', methods=['POST'])
@token_required
@unlock_required
def lookup_in():
    """Excel import. Body: {"upcs": ["157820", ...]} → each UPC's Oracle matches."""
    data = request.get_json(silent=True) or {}
    return _reply(tracking_system_service.lookup_upcs(data.get('upcs')))


@tracking_system_bp.route('/in', methods=['POST'])
@token_required
@unlock_required
def import_in():
    """Body: {"items": [{"item_sid", "qty", "entered_unit_cost"}]} — all or nothing."""
    data = request.get_json(silent=True) or {}
    return _reply(tracking_system_service.import_in(g.sid, data.get('items')))


@tracking_system_bp.route('/in/<int:row_id>', methods=['DELETE'])
@token_required
@unlock_required
def delete_in(row_id):
    return _reply(tracking_system_service.delete('in', row_id))


# ---- Out: one row per bill line. Tracking revenue = entered - Oracle ex-VAT.

@tracking_system_bp.route('/out', methods=['GET'])
@token_required
@unlock_required
def list_out():
    return _reply(tracking_system_service.list_out())


@tracking_system_bp.route('/out/search', methods=['GET'])
@token_required
@unlock_required
def search_out():
    """?q= UPC prefix or ALU substring → sale/return lines, newest first."""
    return _reply(tracking_system_service.search_lines(request.args.get('q')))


@tracking_system_bp.route('/out', methods=['POST'])
@token_required
@unlock_required
def import_out():
    """Body: {"lines": [{"line_id", "entered_revenue"}]} — all or nothing."""
    data = request.get_json(silent=True) or {}
    return _reply(tracking_system_service.import_out(g.sid, data.get('lines')))


@tracking_system_bp.route('/out/<int:row_id>', methods=['DELETE'])
@token_required
@unlock_required
def delete_out(row_id):
    return _reply(tracking_system_service.delete('out', row_id))
