"""
Commission API Routes
"""
from flask import Blueprint, jsonify, request, Response
from app.modules.commission.service import CommissionService, CommissionSettingsService, CommissionStoreSettingsService, CommissionRevenueService
from app.core.auth.middleware import token_required, manager_required

commission_bp = Blueprint('commission', __name__, url_prefix='/api/v1/commission')


def _parse_period(data, source='body'):
    """Parse and validate month/year from request data.

    Args:
        data: dict with 'month' and 'year' keys
        source: 'body' for JSON body, 'args' for query params

    Returns:
        (month, year, None) on success, or (None, None, error_response_tuple) on failure
    """
    month_raw = data.get('month')
    year_raw = data.get('year')

    if month_raw is None or year_raw is None:
        return None, None, (jsonify({
            'success': False,
            'error': 'Missing required field: month and year'
        }), 400)

    try:
        month = int(month_raw)
        year = int(year_raw)
    except (ValueError, TypeError):
        return None, None, (jsonify({
            'success': False,
            'error': 'month and year must be integers'
        }), 400)

    if not (1 <= month <= 12):
        return None, None, (jsonify({
            'success': False,
            'error': 'month must be between 1 and 12'
        }), 400)

    if not (2000 <= year <= 2100):
        return None, None, (jsonify({
            'success': False,
            'error': 'year must be between 2000 and 2100'
        }), 400)

    return month, year, None


def _parse_json_body():
    """Parse JSON request body, distinguishing parse failure from empty body.

    Returns:
        (data, None) on success, or (None, error_response_tuple) on failure
    """
    data = request.get_json(force=True, silent=True)
    if data is None:
        return None, (jsonify({
            'success': False,
            'error': 'Invalid or missing JSON body'
        }), 400)
    return data, None


def _safe_int(value, field_name):
    """Cast a value to int, returning (int_val, None) or (None, error_string)."""
    if value is None:
        return None, None  # None is allowed (nullable fields)
    try:
        return int(value), None
    except (ValueError, TypeError):
        return None, f'{field_name} must be an integer'


@commission_bp.route('/employees', methods=['GET'])
@token_required
def get_commission_employees():
    """
    Get active store employees with their commission settings for a given month/year.

    Query Parameters:
        - month: integer (1-12), required
        - year:  integer, required

    Returns:
        {
            "success": true,
            "month": 12,
            "year": 2025,
            "stores": [
                {
                    "store_code": "RWT",
                    "store_name": "...",
                    "employees": [
                        {
                            "employee_code": "GL013",
                            "full_name": "...",
                            "join_date": "2010-07-14",
                            "contract": "permanent",
                            "is_manager": true,
                            "personal_target": 650000000,
                            "working_day": null
                        }
                    ]
                }
            ]
        }
    """
    try:
        month, year, err = _parse_period(request.args)
        if err:
            return err

        service = CommissionSettingsService()
        result = service.get_commission_employees(month, year)

        if not result.get('success', False):
            return jsonify(result), 500

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@commission_bp.route('/employees/<employee_code>', methods=['PUT'])
@manager_required
def update_commission_employee(employee_code):
    """
    Create or update commission settings for a single employee for a given period.

    Upserts commission_settings. Optionally upserts employee_status_history
    when is_manager or contract are provided (CR #17).

    URL Parameter:
        - employee_code: string (e.g. "GL013")

    Request Body:
        {
            "month": integer (1-12),
            "year": integer,
            "personal_target": integer (VND),
            "working_day": integer,
            "is_commission_active": boolean (optional, default true),
            "store_code_override": string|null (optional),
            "is_manager": boolean (optional, upserts to employee_status_history),
            "contract": string (optional, e.g. "permanent", upserts to employee_status_history)
        }

    Returns:
        {
            "success": true,
            "data": {
                "employee_code": "GL013",
                "month": 12,
                "year": 2025,
                "is_manager": true,
                "personal_target": 650000000,
                "working_day": 22
            }
        }
    """
    try:
        data, err = _parse_json_body()
        if err:
            return err

        required_fields = ['month', 'year', 'personal_target', 'working_day']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        month, year, err = _parse_period(data)
        if err:
            return err

        personal_target, pt_err = _safe_int(data['personal_target'], 'personal_target')
        if pt_err:
            return jsonify({'success': False, 'error': pt_err}), 400

        working_day, wd_err = _safe_int(data['working_day'], 'working_day')
        if wd_err:
            return jsonify({'success': False, 'error': wd_err}), 400

        # Optional: is_commission_active (default True)
        is_commission_active = data.get('is_commission_active', True)
        if not isinstance(is_commission_active, bool):
            return jsonify({'success': False, 'error': 'is_commission_active must be a boolean'}), 400

        # Optional: store_code_override (string or null to clear)
        store_code_override = data.get('store_code_override')
        if store_code_override is not None and not isinstance(store_code_override, str):
            return jsonify({'success': False, 'error': 'store_code_override must be a string or null'}), 400

        # Optional CR #17: is_manager (bool) → upsert to employee_status_history
        is_manager = data.get('is_manager')
        if is_manager is not None and not isinstance(is_manager, bool):
            return jsonify({'success': False, 'error': 'is_manager must be a boolean'}), 400

        # Optional CR #17: contract (string code) → resolve and upsert to employee_status_history
        contract = data.get('contract')
        if contract is not None and not isinstance(contract, str):
            return jsonify({'success': False, 'error': 'contract must be a string'}), 400

        service = CommissionSettingsService()
        result = service.update_commission_settings(
            employee_code=employee_code,
            month=month,
            year=year,
            personal_target=personal_target,
            working_day=working_day,
            is_commission_active=is_commission_active,
            store_code_override=store_code_override,
            is_manager=is_manager,
            contract=contract
        )

        if not result.get('success', False):
            error_msg = result.get('error', '')
            status = 400 if ('not found' in error_msg.lower() or 'invalid' in error_msg.lower()) else 500
            return jsonify(result), status

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@commission_bp.route('/stores', methods=['GET'])
@token_required
def get_commission_stores():
    """
    Get all stores with their commission settings for a given month/year period.

    Query Parameters:
        - month: integer (1-12), required
        - year:  integer, required

    Returns:
        {
            "success": true,
            "month": 2,
            "year": 2026,
            "stores": [
                {
                    "store_code": "RWT",
                    "store_name": "Rolex Warranted Retailer T",
                    "store_target": 5000000000,
                    "fp_ratio_target": 60
                }
            ]
        }
    """
    try:
        month, year, err = _parse_period(request.args)
        if err:
            return err

        service = CommissionStoreSettingsService()
        result = service.get_commission_stores(month, year)

        if not result.get('success', False):
            return jsonify(result), 500

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@commission_bp.route('/stores/<store_code>', methods=['PUT'])
@manager_required
def update_commission_store(store_code):
    """
    Create or update commission settings for a store for a given period.

    URL Parameter:
        - store_code: string (e.g. "RWT")

    Request Body:
        {
            "month": 2,
            "year": 2026,
            "store_target": 5000000000,
            "fp_ratio_target": 60
        }

    Returns:
        {
            "success": true,
            "data": {
                "store_code": "RWT",
                "month": 2,
                "year": 2026,
                "store_target": 5000000000,
                "fp_ratio_target": 60
            }
        }
    """
    try:
        data, err = _parse_json_body()
        if err:
            return err

        for field in ['month', 'year']:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        month, year, err = _parse_period(data)
        if err:
            return err

        store_target, st_err = _safe_int(data.get('store_target'), 'store_target')
        if st_err:
            return jsonify({'success': False, 'error': st_err}), 400

        fp_ratio, fp_err = _safe_int(data.get('fp_ratio_target'), 'fp_ratio_target')
        if fp_err:
            return jsonify({'success': False, 'error': fp_err}), 400

        service = CommissionStoreSettingsService()
        result = service.update_commission_store_settings(
            store_code=store_code,
            month=month,
            year=year,
            store_target=store_target,
            fp_ratio_target=fp_ratio
        )

        if not result.get('success', False):
            if 'not found' in result.get('error', '').lower():
                return jsonify(result), 404
            return jsonify(result), 500

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@commission_bp.route('/revenue', methods=['GET'])
@token_required
def get_commission_revenue():
    """
    Get live Oracle revenue breakdown per employee per 7 revenue types,
    with stored adjustment deltas applied, for a given period.

    Query Parameters:
        - month: integer (1-12), required
        - year:  integer, required
    """
    try:
        month, year, err = _parse_period(request.args)
        if err:
            return err

        service = CommissionRevenueService()
        result = service.get_revenue_breakdown(month, year)

        if not result.get('success', False):
            return jsonify(result), 500

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@commission_bp.route('/revenue/store-view', methods=['GET'])
@token_required
def get_store_view_revenue():
    """
    CR #26: Revenue breakdown grouped by transaction location (doc_store_code).

    Shows contributors (employees + SYSADMIN) per physical store with FP/MD
    revenue split per category. Includes cross-store indicators.

    Query Parameters:
        - month: integer (1-12), required
        - year:  integer, required
    """
    try:
        month, year, err = _parse_period(request.args)
        if err:
            return err

        service = CommissionRevenueService()
        result = service.get_store_view_breakdown(month, year)

        if not result.get('success', False):
            return jsonify(result), 500

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@commission_bp.route('/revenue/adjustments/<employee_code>', methods=['PUT'])
@manager_required
def update_revenue_adjustments(employee_code):
    """
    Create or update revenue adjustment deltas for an employee at a specific store.

    URL Parameter:
        - employee_code: string (e.g. "GL013")

    Request Body:
        {
            "month": 3,
            "year": 2026,
            "store_code": "RWR",
            "adjustments": [
                {"revenue_type": "fashion_fp", "adjustment": 50000000},
                {"revenue_type": "fashion_md", "adjustment": -10000000}
            ]
        }

    Valid revenue_type values: fashion_fp, fashion_md, jewelry_fp, jewelry_md,
        vhernier_fp, vhernier_md, rosa_maria_fp, rosa_maria_md,
        hand_carry_fp, hand_carry_md, suitcase_fp, suitcase_md,
        home_decor_fp, home_decor_md, other_fp, other_md
    """
    try:
        data, err = _parse_json_body()
        if err:
            return err

        for field in ['month', 'year', 'store_code', 'adjustments']:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        month, year, err = _parse_period(data)
        if err:
            return err

        store_code = data['store_code']
        if not store_code or not isinstance(store_code, str) or not store_code.strip():
            return jsonify({
                'success': False,
                'error': 'store_code must be a non-empty string'
            }), 400
        store_code = store_code.strip()

        if not isinstance(data['adjustments'], list):
            return jsonify({
                'success': False,
                'error': 'adjustments must be an array'
            }), 400

        service = CommissionRevenueService()
        result = service.save_revenue_adjustments(
            employee_code=employee_code,
            month=month,
            year=year,
            store_code=store_code,
            adjustments=data['adjustments']
        )

        if not result.get('success', False):
            if result.get('not_found'):
                return jsonify(result), 404
            return jsonify(result), 400

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@commission_bp.route('/calculate', methods=['POST'])
@manager_required
def calculate_commission():
    """
    Calculate commissions for a period — fully DB-authoritative.

    The backend is the single source of truth: it loads the roster (commission-active
    employees by assigned store), store targets, and persisted revenue adjustments,
    then computes revenue, achievement, eligibility, store pool, and personal
    commission. Cross-store sellers are rostered under their HOME store; their
    out-of-store sales reach the destination store's eligibility revenue and their own
    personal commission, but never any store's commission pool.

    Request Body:
        { "month": 5, "year": 2026 }

    A legacy `stores` array (CR #29) is accepted but IGNORED — adjustments and targets
    are read from the database, not the request. Save them first via
    PUT /commission/revenue/adjustments/<employee_code> and PUT /commission/stores/<code>.
    """
    try:
        data, err = _parse_json_body()
        if err:
            return err

        month, year, err = _parse_period(data)
        if err:
            return err

        service = CommissionService()
        result = service.calculate_commissions(month=month, year=year)

        if not result.get('success', False):
            return jsonify(result), 500

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
