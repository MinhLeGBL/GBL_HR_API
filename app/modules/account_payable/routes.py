"""
Account Payable HTTP routes (CR #60).

Endpoints under `/api/v1/account-payable/*` (URL prefix uses a hyphen
to match the frontend feature path; Python module name uses underscore
for import compat).

The full contract is in `GBL_HR_Frontend/src/features/account-payable/types.ts`
and `docs/cr/account-payable.md`.

Auth: token + admin/manager. Department gate (HR, ACC) is enforced by
section permissions once the `ACCOUNT_PAYABLE` section is seeded.
"""
from flask import Blueprint, jsonify, request

from app.core.auth import token_required, manager_required
from .service import AccountPayableService

account_payable_bp = Blueprint(
    'account_payable', __name__, url_prefix='/api/v1/account-payable',
)

_service = AccountPayableService()


def _not_implemented(endpoint: str):
    """Stub response for endpoints not yet implemented in this branch."""
    return jsonify({
        'success': False,
        'error': (
            f'{endpoint} not yet implemented — CR #60 backend in progress. '
            'Frontend should use VITE_PAYABLE_MOCK=true until implementation lands.'
        ),
    }), 501


# ─────────────────────────────────────────────────────────────────────
# Read endpoints
# ─────────────────────────────────────────────────────────────────────

@account_payable_bp.route('/employees', methods=['GET'])
@token_required
def get_employees():
    """List employees with non-zero open payable. No period filter — running."""
    result = _service.get_employees()
    return jsonify(result), 200 if result.get('success') else 500


@account_payable_bp.route('/employees/<employee_code>/bills', methods=['GET'])
@token_required
def get_employee_bills(employee_code: str):
    """List one employee's open bills, scoped to their item share."""
    result = _service.get_employee_bills(employee_code)
    return jsonify(result), 200 if result.get('success') else 500


@account_payable_bp.route('/bills', methods=['GET'])
@token_required
def get_bills():
    """List all bills. Query params: ?search=&status=open|partial|fully_paid"""
    search = request.args.get('search')
    status = request.args.get('status')
    if status and status not in ('open', 'partial', 'fully_paid'):
        return jsonify({
            'success': False,
            'error': f"status must be one of: open, partial, fully_paid (got {status!r})",
        }), 400
    result = _service.get_bills(search=search, status=status)
    return jsonify(result), 200 if result.get('success') else 500


@account_payable_bp.route('/bills/<bill_sid>', methods=['GET'])
@token_required
def get_bill_detail(bill_sid: str):
    """Full bill detail: items table with multi-employee fan-out."""
    if not bill_sid.isdigit():
        return jsonify({
            'success': False,
            'error': 'bill_sid must be a numeric string',
        }), 400
    result = _service.get_bill_detail(bill_sid)
    if not result.get('success') and result.get('not_found'):
        return jsonify(result), 404
    return jsonify(result), 200 if result.get('success') else 500


@account_payable_bp.route('/payments', methods=['GET'])
@token_required
def get_pending_payments():
    """Payments queue. Query param: ?status=unmatched|matched_pending"""
    return _not_implemented('GET /account-payable/payments')


# ─────────────────────────────────────────────────────────────────────
# Write endpoints — Phase D
# ─────────────────────────────────────────────────────────────────────

@account_payable_bp.route('/reconcile', methods=['POST'])
@manager_required
def reconcile():
    """Save a manual payment→bill linkage (or replace an existing one).

    Request: { payment_doc_sid, allocations: [{ bill_sid, amount }] }
    """
    return _not_implemented('POST /account-payable/reconcile')


@account_payable_bp.route('/payments/<payment_doc_sid>/unmatch', methods=['POST'])
@manager_required
def unmatch_payment(payment_doc_sid: str):
    """Remove an existing manual linkage for the given payment."""
    return _not_implemented(f'POST /account-payable/payments/{payment_doc_sid}/unmatch')
