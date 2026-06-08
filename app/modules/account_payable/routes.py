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
from flask import Blueprint, g, jsonify, request

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
    """Payments queue. Query param: ?status=unmatched|matched_partially|matched_pending|released

    CR #67 extended the state model — released payments now stay in the
    queue so finance can edit allocations after release.
    """
    status = request.args.get('status')
    allowed = AccountPayableService._ALLOWED_PAYMENT_STATUSES
    if status and status not in allowed:
        return jsonify({
            'success': False,
            'error': (
                f"status must be one of {', '.join(repr(s) for s in allowed)} "
                f"(got {status!r})"
            ),
        }), 400
    result = _service.get_pending_payments(status=status)
    return jsonify(result), 200 if result.get('success') else 500


# ─────────────────────────────────────────────────────────────────────
# Write endpoints — Phase D
# ─────────────────────────────────────────────────────────────────────

def _current_user_sid() -> int:
    """Pull the authenticated user's SID off Flask `g` (set by token_required)."""
    return getattr(g, 'user_sid', None)


def _current_user_identifier() -> str:
    """Display identifier for write-side audit fields (CR #68 `voided_by`).
    Prefers email; falls back to SID-as-string, then 'unknown'."""
    email = getattr(g, 'email', None)
    if email:
        return str(email)
    sid = getattr(g, 'sid', None)
    if sid is not None:
        return f'sid:{sid}'
    return 'unknown'


@account_payable_bp.route('/reconcile', methods=['POST'])
@manager_required
def reconcile():
    """Save a manual payment→bill linkage (or replace an existing one).

    Request: { payment_doc_sid, allocations: [{ bill_sid, amount }] }
    """
    body = request.get_json(silent=True) or {}
    payment_doc_sid = body.get('payment_doc_sid')
    allocations = body.get('allocations') or []
    if not payment_doc_sid:
        return jsonify({'success': False, 'error': 'payment_doc_sid is required'}), 400

    result = _service.reconcile(
        payment_doc_sid=str(payment_doc_sid),
        allocations=allocations,
        created_by_user_sid=_current_user_sid(),
    )
    if result.get('success'):
        return jsonify(result), 200
    if result.get('not_found'):
        return jsonify(result), 404
    if result.get('conflict'):
        return jsonify(result), 409
    return jsonify(result), 400


@account_payable_bp.route('/payments/<payment_doc_sid>/unmatch', methods=['POST'])
@manager_required
def unmatch_payment(payment_doc_sid: str):
    """Remove an existing manual linkage for the given payment.

    Returns 404 when no manual linkage exists (CR #62 — REF_SALE_SID
    cases cannot be unmatched directly; reconcile manually to override).
    """
    result = _service.unmatch(str(payment_doc_sid))
    if result.get('success'):
        return jsonify(result), 200
    if result.get('not_found'):
        return jsonify(result), 404
    return jsonify(result), 400


@account_payable_bp.route('/payments/<payment_doc_sid>/tag-remake', methods=['POST'])
@manager_required
def tag_payment_remake(payment_doc_sid: str):
    """CR #70: tag a corrective payment as remake.

    Drops any existing allocations and excludes the payment from
    commission release. Returns 200 with the updated PendingPayment.

    Errors:
      400 — current status is matched_pending/released (unmatch first)
      400 — match_source is ref_sale_sid (fix at source in Oracle)
      404 — payment not in active Charge ledger
    """
    result = _service.tag_remake(
        payment_doc_sid=str(payment_doc_sid),
        tagged_by=_current_user_identifier(),
    )
    if result.get('success'):
        return jsonify(result), 200
    if result.get('not_found'):
        return jsonify(result), 404
    return jsonify(result), 400


@account_payable_bp.route('/payments/<payment_doc_sid>/untag-remake', methods=['POST'])
@manager_required
def untag_payment_remake(payment_doc_sid: str):
    """CR #70: reverse a remake tag — payment returns to the unmatched
    queue. Returns 200 with the updated PendingPayment.

    Errors:
      400 — payment is not currently tagged as remake
    """
    result = _service.untag_remake(
        payment_doc_sid=str(payment_doc_sid),
        untagged_by=_current_user_identifier(),
    )
    if result.get('success'):
        return jsonify(result), 200
    return jsonify(result), 400


@account_payable_bp.route('/bills/<bill_sid>/void', methods=['POST'])
@manager_required
def void_bill_remaining(bill_sid: str):
    """CR #68: write off part (or all) of a bill's remaining balance.

    Request: { amount: int, reason?: string }
    - `amount` required, > 0, <= bill.remaining_unpaid
    - `reason` optional (nullable)

    Returns 200 with `{ bill: BillDetail, void_id: str }`.
    Errors: 400 invalid amount; 404 bill not in active AP ledger.
    """
    body = request.get_json(silent=True) or {}
    result = _service.void_remaining(
        bill_sid=str(bill_sid),
        amount=body.get('amount'),
        reason=body.get('reason'),
        voided_by=_current_user_identifier(),
    )
    if result.get('success'):
        return jsonify(result), 200
    if result.get('not_found'):
        return jsonify(result), 404
    return jsonify(result), 400


@account_payable_bp.route('/bills/<bill_sid>/items/<upc>/rate', methods=['PATCH'])
@manager_required
def set_item_custom_rate(bill_sid: str, upc: str):
    """Set or clear a per-item custom release rate (with legacy propagation)."""
    body = request.get_json(silent=True) or {}
    if 'custom_release_rate' not in body:
        return jsonify({
            'success': False,
            'error': 'custom_release_rate is required (number or null)',
        }), 400
    rate = body['custom_release_rate']
    result = _service.set_item_custom_rate(
        bill_sid=bill_sid,
        upc=upc,
        custom_release_rate=rate,
        set_by_user_sid=_current_user_sid(),
    )
    if result.get('success'):
        return jsonify(result), 200
    if result.get('not_found'):
        return jsonify(result), 404
    return jsonify(result), 400
