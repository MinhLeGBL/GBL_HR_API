"""
Account Payable HTTP routes (CR #60 — Phase 1 scaffold).

Endpoints under `/api/v1/account-payable/*` (the URL prefix uses a hyphen
to match the frontend feature path; Python module name uses underscore
for import compat).

All routes return 501 Not Implemented in Phase 1 — the frontend ships
with mocks (`VITE_PAYABLE_MOCK=true`) until each endpoint is filled in.
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


def _not_implemented(endpoint: str):
    """Phase 1 stub response. Returns 501 with a clear pointer to the spec."""
    return jsonify({
        'success': False,
        'error': (
            f'{endpoint} not yet implemented — CR #60 backend scaffold. '
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
    return _not_implemented('GET /account-payable/employees')


@account_payable_bp.route('/employees/<employee_code>/bills', methods=['GET'])
@token_required
def get_employee_bills(employee_code: str):
    """List one employee's open bills, scoped to their item share."""
    return _not_implemented(f'GET /account-payable/employees/{employee_code}/bills')


@account_payable_bp.route('/bills', methods=['GET'])
@token_required
def get_bills():
    """List all bills. Query params: ?search=&status=open|partial|fully_paid"""
    return _not_implemented('GET /account-payable/bills')


@account_payable_bp.route('/bills/<bill_sid>', methods=['GET'])
@token_required
def get_bill_detail(bill_sid: str):
    """Full bill detail: items table with multi-employee fan-out."""
    return _not_implemented(f'GET /account-payable/bills/{bill_sid}')


@account_payable_bp.route('/payments', methods=['GET'])
@token_required
def get_pending_payments():
    """Payments queue. Query param: ?status=unmatched|matched_pending"""
    return _not_implemented('GET /account-payable/payments')


# ─────────────────────────────────────────────────────────────────────
# Write endpoints
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
