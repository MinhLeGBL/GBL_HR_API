"""CRM API routes — RFM customer segmentation."""
from flask import Blueprint, jsonify, request
from app.core.auth.middleware import token_required, admin_required
from .service import CRMService

crm_bp = Blueprint('crm', __name__, url_prefix='/api/v1/crm')
crm_service = CRMService()


# ----------------------------------------------------------------------
# Stubs — wired up but not implemented yet (Step 6 will fill these in).
# Returning 503 keeps the API surface visible during scaffolding.
# ----------------------------------------------------------------------
_NOT_IMPLEMENTED = ({'success': False, 'error': 'CRM endpoints not yet implemented'}, 503)


@crm_bp.route('/customers', methods=['GET'])
@token_required
def get_customers():
    return jsonify(_NOT_IMPLEMENTED[0]), _NOT_IMPLEMENTED[1]


@crm_bp.route('/segments/summary', methods=['GET'])
@token_required
def get_segments_summary():
    return jsonify(_NOT_IMPLEMENTED[0]), _NOT_IMPLEMENTED[1]


@crm_bp.route('/segments/trends', methods=['GET'])
@token_required
def get_segments_trends():
    return jsonify(_NOT_IMPLEMENTED[0]), _NOT_IMPLEMENTED[1]


@crm_bp.route('/heatmap', methods=['GET'])
@token_required
def get_heatmap():
    return jsonify(_NOT_IMPLEMENTED[0]), _NOT_IMPLEMENTED[1]


@crm_bp.route('/admin/recompute', methods=['POST'])
@admin_required
def admin_recompute():
    return jsonify(_NOT_IMPLEMENTED[0]), _NOT_IMPLEMENTED[1]
