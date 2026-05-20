"""
Hand Carry API Routes (CR #57).

`rps.carrier_item` is a flat list of UPCs flagged as hand-carry. Used by the
commission pipeline to keep hand-carry sales out of the store commission
pool while still attributing them to the seller's personal commission.

The frontend imports UPCs from Excel/CSV and manages the list via these
endpoints.
"""
from flask import Blueprint, jsonify, request

from app.core.auth.middleware import token_required, manager_required
from .service import HandCarryService

handcarry_bp = Blueprint('handcarry', __name__, url_prefix='/api/v1/handcarry')
handcarry_service = HandCarryService()


@handcarry_bp.route('', methods=['GET'])
@token_required
def list_handcarry():
    """List all hand-carry UPCs. Optional `?search=12345` partial filter."""
    search = request.args.get('search')
    result = handcarry_service.list_items(search=search)
    if not result['success']:
        return jsonify(result), 500
    return jsonify(result), 200


@handcarry_bp.route('/import', methods=['POST'])
@manager_required
def import_handcarry():
    """
    Bulk upsert UPCs.

    Request: { "upcs": [12813, 15611, ...] }
    Existing UPCs are skipped (no duplicates). Non-integer values surface
    in the `errors` array but do NOT abort the batch.
    """
    data = request.get_json(silent=True)
    if not data or 'upcs' not in data:
        return jsonify({'success': False, 'error': "Missing 'upcs' array in request body"}), 400

    result = handcarry_service.import_upcs(data['upcs'])
    if not result['success']:
        return jsonify(result), 500
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
