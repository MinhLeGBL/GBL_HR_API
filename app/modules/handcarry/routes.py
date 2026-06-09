"""
Hand Carry API Routes (CR #57 / CR #64).

`rps.carrier_item` is a UPC flag list — the set of items GBL treats as
hand-carry. Product info (description / brand / category / color / size /
season), prices, and lifetime imported / sold counts are all sourced
live from Oracle on every `GET /handcarry` request (CR #64).

`quantity_sold` has a stored override column for orphan UPCs that Oracle
no longer recognises — when non-NULL the override wins over the live count.

The frontend uploads UPCs from Excel/CSV via the import endpoint; the UI
is read-only outside that flow.
"""
from flask import Blueprint, jsonify, request

from app.core.auth.middleware import token_required, manager_required
from .service import HandCarryService

handcarry_bp = Blueprint('handcarry', __name__, url_prefix='/api/v1/handcarry')
handcarry_service = HandCarryService()


@handcarry_bp.route('', methods=['GET'])
@token_required
def list_handcarry():
    """
    List all hand-carry catalog rows. Optional `?search=12345` partial
    filter on `scan_upc`.

    Each item: id, upc, description, brand, category, color, size, season,
    quantity_imported, quantity_sold (live Oracle lifetime), price_before_vat,
    price_after_vat.
    """
    search = request.args.get('search')
    result = handcarry_service.list_items(search=search)
    if not result['success']:
        return jsonify(result), 500
    return jsonify(result), 200


@handcarry_bp.route('/import', methods=['POST'])
@token_required
def import_handcarry():
    """
    Bulk import UPCs into the hand-carry flag list (CR #64).

    Request body:
        { "upcs": [12813, 15611, ...] }

    SKIP-on-exists semantics — existing UPCs are left alone, new ones
    are inserted with only `scan_upc` populated. All product info is
    sourced live from Oracle on read, so there's nothing to update.

    Returns `{ success, inserted, skipped, errors, total_received }`.
    Validation errors are reported per-row in `errors` without aborting
    the batch.
    """
    data = request.get_json(silent=True) or {}

    if 'upcs' not in data:
        return jsonify({
            'success': False,
            'error': "Missing 'upcs' array in request body",
        }), 400

    result = handcarry_service.import_upcs(data['upcs'])
    if not result['success']:
        status = 400 if 'array' in result.get('error', '').lower() else 500
        return jsonify(result), status
    return jsonify(result), 200


@handcarry_bp.route('/<int:item_id>', methods=['PUT'])
@manager_required
def update_handcarry(item_id: int):
    """Update a single record's UPC. Request: { "upc": <int> }."""
    data = request.get_json(silent=True)
    if not data or 'upc' not in data:
        return jsonify({'success': False, 'error': "Missing 'upc' in request body"}), 400

    result = handcarry_service.update_item(item_id, data['upc'])
    if not result['success']:
        return jsonify(result), result.get('status', 500)
    return jsonify(result), 200


@handcarry_bp.route('/<int:item_id>', methods=['DELETE'])
@manager_required
def delete_handcarry(item_id: int):
    """Delete a single record."""
    result = handcarry_service.delete_item(item_id)
    if not result['success']:
        return jsonify(result), result.get('status', 500)
    return jsonify(result), 200
