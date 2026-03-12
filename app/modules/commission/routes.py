"""
Commission API Routes
"""
from flask import Blueprint, jsonify, request, Response
from app.modules.commission.service import CommissionService, CommissionSettingsService, CommissionStoreSettingsService, CommissionRevenueService
from app.core.auth.middleware import token_required, manager_required
import pandas as pd
import io

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


@commission_bp.route('/personal/calculate', methods=['POST'])
def calculate_personal_commission():
    """
    Calculate personal commissions for employees

    Request Body:
        {
            "month": integer (1-12),
            "year": integer,
            "employees": [
                {
                    "employee_id": "string",
                    "target": number,
                    "employee_name": "string" (optional),
                    "department": "string" (optional)
                }
            ]
        }

    Returns:
        JSON response with personal commission calculation results for all employees
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ['month', 'year', 'employees']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        # Validate month range
        if not (1 <= data['month'] <= 12):
            return jsonify({
                'success': False,
                'error': 'month must be between 1 and 12'
            }), 400

        # Validate employees list
        if not isinstance(data['employees'], list) or len(data['employees']) == 0:
            return jsonify({
                'success': False,
                'error': 'employees must be a non-empty list'
            }), 400

        # Validate each employee entry
        for i, employee in enumerate(data['employees']):
            if 'employee_id' not in employee or 'target' not in employee:
                return jsonify({
                    'success': False,
                    'error': f'Employee at index {i} is missing required field: employee_id or target'
                }), 400

        # Execute personal commission calculation
        service = CommissionService()
        result = service.calculate_personal_commissions(
            month=data['month'],
            year=data['year'],
            employees=data['employees']
        )

        # Check if calculation was successful
        if not result.get('success', False):
            return jsonify(result), 400

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500



@commission_bp.route('/store/calculate-v2', methods=['POST'])
def calculate_store_commission_v2():
    """
    Calculate store commission following the updated pseudocode algorithm

    Request Body (following the pseudocode structure):
        {
            "store_code": "string" (e.g., "RHN", "HBT", "HDG"),
            "store_target": number (store's revenue target),
            "store_fp_ratio_target": number (target full-price ratio, 0.0 to 1.0),
            "query_date": {
                "from_date": "YYYY-MM-DD HH:MI:SS" (start date),
                "to_date": "YYYY-MM-DD HH:MI:SS" (end date)
            },
            "employees": [
                {
                    "employee_code": "string" (unique employee identifier),
                    "full_name": "string" (employee's full name),
                    "seniority": number (tenure in years),
                    "personal_target": number (employee's personal sales target),
                    "working_day_count": number (days worked in the period),
                    "is_manager": boolean (true if employee is store manager),
                    "is_probation": boolean (true if employee is on probation)
                }
            ]
        }

    Returns:
        JSON response with commission calculation results following pseudocode output structure:
        {
            "success": true,
            "data": {
                "eligible": boolean,
                "store_code": "string",
                "achievement_pct": number,
                "actual_fp_ratio": number,
                "store_target": number,
                "actual_revenue": number,
                "store_pool": number,
                "total_employee_count": number,
                "total_working_days": number,
                "employees": [
                    {
                        "employee_code": "string",
                        "full_name": "string",
                        "seniority": number (years),
                        "is_manager": boolean,
                        "is_probation": boolean,
                        "working_day_count": number,
                        "store_code": "string",
                        "individual_share": number (70% based on contribution),
                        "equal_share": number (30% share),
                        "manager_bonus": number (0, 750000, or 3000000),
                        "total_store_commission": number (equal_share only for probation employees, or individual_share + equal_share + manager_bonus for regular employees)
                    }
                ]
            }
        }
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ['store_code', 'store_target', 'store_fp_ratio_target', 'query_date', 'employees']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        # Validate query_date structure
        if not isinstance(data['query_date'], dict):
            return jsonify({
                'success': False,
                'error': 'query_date must be an object with from_date and to_date'
            }), 400

        if 'from_date' not in data['query_date'] or 'to_date' not in data['query_date']:
            return jsonify({
                'success': False,
                'error': 'query_date must contain from_date and to_date'
            }), 400

        # Validate store_fp_ratio_target range
        if not (0.0 <= data['store_fp_ratio_target'] <= 1.0):
            return jsonify({
                'success': False,
                'error': 'store_fp_ratio_target must be between 0.0 and 1.0'
            }), 400

        # Validate employees list
        if not isinstance(data['employees'], list) or len(data['employees']) == 0:
            return jsonify({
                'success': False,
                'error': 'employees must be a non-empty list'
            }), 400

        # Validate each employee entry
        required_emp_fields = ['employee_code', 'full_name', 'seniority', 'personal_target', 'working_day_count', 'is_manager', 'is_probation']
        for i, employee in enumerate(data['employees']):
            for field in required_emp_fields:
                if field not in employee:
                    return jsonify({
                        'success': False,
                        'error': f'Employee at index {i} is missing required field: {field}'
                    }), 400

        # Execute commission calculation using the v2 method
        service = CommissionService()
        result = service.calculate_store_commission_v2(
            store_code=data['store_code'],
            store_target=data['store_target'],
            store_fp_ratio_target=data['store_fp_ratio_target'],
            query_date=data['query_date'],
            employees=data['employees']
        )

        return jsonify({
            'success': True,
            'data': result
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


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
    Create or update revenue adjustment deltas for an employee for a given period.

    URL Parameter:
        - employee_code: string (e.g. "GL013")

    Request Body:
        {
            "month": 3,
            "year": 2026,
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

        for field in ['month', 'year', 'adjustments']:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        month, year, err = _parse_period(data)
        if err:
            return err

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
    Trigger full commission calculation pipeline for a given month/year.

    Reads settings from PostgreSQL, fetches Oracle sales, applies revenue adjustments,
    and calculates all commission components.

    Request Body:
        {
            "month": 3,
            "year": 2026
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

        service = CommissionService()
        result = service.calculate_commissions_for_period(
            month=month,
            year=year
        )

        if not result.get('success', False):
            return jsonify(result), 500

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
