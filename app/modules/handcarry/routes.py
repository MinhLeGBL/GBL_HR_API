"""
Hand Carry API Routes (CR #57, CR #58).

`rps.carrier_item` is the hand-carry catalog. Per CR #58 each row now
carries product info (description / brand / category / color / size /
season), an `quantity_imported`, and selling prices. `quantity_sold` is
computed live from Oracle (lifetime sales for the UPC).

The frontend imports rows from Excel/CSV and manages the list via these
endpoints. The UI is read-only outside the import flow.
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
@manager_required
def import_handcarry():
    """
    Bulk upsert hand-carry catalog rows.

    CR #58 full-record form (preferred):
        { "records": [
            { "upc", "description", "brand", "category", "color", "size",
              "season", "quantity_imported", "price_before_vat",
              "price_after_vat" },
            ...
        ] }
    REPLACE semantics — existing UPCs have all fields overwritten with
    the import row's values (the file is the source of truth).

    Legacy UPC-only form (still accepted for callers that haven't
    migrated):
        { "upcs": [12813, 15611, ...] }
    SKIP-on-exists semantics. New UPCs are inserted with only `scan_upc`
    populated.

    Returns `{ success, inserted, updated, skipped, errors, total_received }`.
    Validation errors are reported per-row in `errors` without aborting
    the batch.
    """
    data = request.get_json(silent=True) or {}

    if 'records' in data:
        result = handcarry_service.import_records(data['records'])
    elif 'upcs' in data:
        result = handcarry_service.import_upcs(data['upcs'])
    else:
        return jsonify({
            'success': False,
            'error': "Missing 'records' (or legacy 'upcs') array in request body",
        }), 400

    if not result['success']:
        # Shape validation errors → 400; backend failures → 500
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
