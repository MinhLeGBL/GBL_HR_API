"""
Commission API Routes
"""
from flask import Blueprint, jsonify, request, Response
from app.modules.commission.service import CommissionService, CommissionSettingsService, CommissionStoreSettingsService, CommissionRevenueService
from app.modules.commission.sheets_service import GoogleSheetsService
from app.core.auth.middleware import token_required, manager_required
import pandas as pd
import io

commission_bp = Blueprint('commission', __name__, url_prefix='/api/v1/commission')


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


@commission_bp.route('/batch/calculate', methods=['POST'])
def calculate_batch_commission():
    """
    Calculate store commissions for multiple employees across multiple stores

    Request Body:
        {
            "year": integer,
            "month": integer,
            "start_date": "YYYY-MM-DD HH:MI:SS",
            "end_date": "YYYY-MM-DD HH:MI:SS",
            "employees": [
                {
                    "employee_code": "string",
                    "employee_name": "string",
                    "store_code": "string",
                    "personal_target": number,
                    "seniority": integer (tenure in months)
                }
            ],
            "stores": [
                {
                    "store_code": "string",
                    "store_name": "string" (optional),
                    "target_revenue": number,
                    "target_fp_ratio": number (0.0 to 1.0)
                }
            ]
        }

    Returns:
        JSON response with commission calculation results for all employees and stores
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ['year', 'month', 'start_date', 'end_date', 'employees', 'stores']
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

        # Validate stores list
        if not isinstance(data['stores'], list) or len(data['stores']) == 0:
            return jsonify({
                'success': False,
                'error': 'stores must be a non-empty list'
            }), 400

        # Validate each employee entry
        for i, employee in enumerate(data['employees']):
            required_emp_fields = ['employee_code', 'employee_name', 'store_code', 'seniority']
            for field in required_emp_fields:
                if field not in employee:
                    return jsonify({
                        'success': False,
                        'error': f'Employee at index {i} is missing required field: {field}'
                    }), 400

        # Validate each store entry
        for i, store in enumerate(data['stores']):
            required_store_fields = ['store_code', 'target_revenue', 'target_fp_ratio']
            for field in required_store_fields:
                if field not in store:
                    return jsonify({
                        'success': False,
                        'error': f'Store at index {i} is missing required field: {field}'
                    }), 400

            # Validate target_fp_ratio range
            if not (0.0 <= store['target_fp_ratio'] <= 1.0):
                return jsonify({
                    'success': False,
                    'error': f'Store at index {i}: target_fp_ratio must be between 0.0 and 1.0'
                }), 400

        # Execute batch commission calculation
        service = CommissionService()
        result = service.calculate_batch_store_commissions(
            employees=data['employees'],
            stores=data['stores'],
            year=data['year'],
            month=data['month'],
            start_date=data['start_date'],
            end_date=data['end_date']
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


@commission_bp.route('/store/calculate-from-sheet', methods=['GET'])
def calculate_store_commission_from_sheet():
    """
    Calculate store and personal commissions for ALL stores by reading input data from Google Sheets,
    then upload results back to the sheet

    Query Parameters:
        - spreadsheet_title: Google Sheets spreadsheet title/name (optional - if provided, will search for sheet by name)
        - spreadsheet_id: Google Sheets spreadsheet ID (optional - direct ID access)
        - sheet_name: Name of the sheet/tab (optional, default: 'Sheet1')

    Note: Provide either spreadsheet_title OR spreadsheet_id (title takes precedence)

    Returns:
        JSON response with commission calculation results and upload status for all stores
    """
    try:
        # Get query parameters
        spreadsheet_title = request.args.get('spreadsheet_title')
        spreadsheet_id = request.args.get('spreadsheet_id')
        sheet_name = request.args.get('sheet_name', 'Sheet1')

        # Initialize services
        sheets_service = GoogleSheetsService()
        commission_service = CommissionService()

        # Find spreadsheet ID if title is provided
        if spreadsheet_title:
            try:
                spreadsheet_id = sheets_service.find_spreadsheet_by_title(spreadsheet_title)
            except Exception as e:
                return jsonify({
                    'success': False,
                    'error': f'Could not find spreadsheet: {str(e)}'
                }), 404

        # Validate that we have a spreadsheet ID
        if not spreadsheet_id:
            return jsonify({
                'success': False,
                'error': 'Missing required parameter: spreadsheet_title or spreadsheet_id'
            }), 400

        # Step 1: Clear output ranges
        sheets_service.clear_output_ranges(
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name
        )

        # Step 2: Get input data from Google Sheets
        sheet_data = sheets_service.get_sheet_data(spreadsheet_id, sheet_name)

        # Extract query period from sheet selector
        from_date = sheet_data['query_period']['from_date']
        to_date = sheet_data['query_period']['to_date']
        month = sheet_data['query_period'].get('month')
        year = sheet_data['query_period'].get('year')

        # Parse month and year from selector
        month_map = {
            'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
            'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
            'January': 1, 'February': 2, 'March': 3, 'April': 4, 'May': 5, 'June': 6,
            'July': 7, 'August': 8, 'September': 9, 'October': 10, 'November': 11, 'December': 12
        }
        month_num = month_map.get(month, 1) if isinstance(month, str) else month
        year_num = year if isinstance(year, int) else int(year)

        # Step 3: Process all stores
        all_store_results = []
        all_combined_dfs = []

        for store_data in sheet_data['stores']:
            store_code = store_data['store_code']

            # Filter employees for this store
            store_employees = [emp for emp in sheet_data['employees'] if emp['store_code'] == store_code]

            if not store_employees:
                # Skip stores with no employees
                continue

            # Calculate store commission
            store_commission_result = commission_service.calculate_store_commission_v2(
                store_code=store_code,
                store_target=store_data['store_target'],
                store_fp_ratio_target=store_data['store_fp_ratio_target'],
                query_date={'from_date': from_date, 'to_date': to_date},
                employees=store_employees
            )

            # Calculate personal commission
            personal_commission_df = commission_service.calculate_personal_commissions(
                month=month_num,
                year=year_num,
                employees=store_employees,
                store_code=store_code
            )

            # Combine both commissions
            combined_commission_df = commission_service.calculate_combined_commission(
                store_commission_result=store_commission_result,
                personal_commission_df=personal_commission_df
            )

            # Collect results
            all_store_results.append(store_commission_result)
            all_combined_dfs.append(combined_commission_df)

        # Step 4: Concatenate all employee DataFrames
        if not all_combined_dfs:
            return jsonify({
                'success': False,
                'error': 'No employees found in any store'
            }), 400

        final_combined_df = pd.concat(all_combined_dfs, ignore_index=True)

        # Step 5: Upload results back to Google Sheets
        sheets_service.upload_commission_results(
            spreadsheet_id=spreadsheet_id,
            store_commission_results=all_store_results,
            combined_commission_df=final_combined_df,
            sheet_name=sheet_name
        )

        # Step 6: Prepare summary for all stores
        total_employees = len(final_combined_df)
        total_commission = float(final_combined_df['total_handout_commission'].sum())

        store_summaries = []
        for store_result in all_store_results:
            store_code = store_result['store_code']
            store_df = final_combined_df[final_combined_df['store_code'] == store_code]
            store_summaries.append({
                'store_code': store_code,
                'achievement_pct': store_result.get('achievement_pct', 0),
                'actual_fp_ratio': store_result.get('actual_fp_ratio', 0),
                'eligible': store_result.get('eligible', False),
                'employee_count': len(store_df),
                'total_commission': float(store_df['total_handout_commission'].sum())
            })

        return jsonify({
            'success': True,
            'message': 'Commission calculated and uploaded to sheet successfully for all stores',
            'data': {
                'total_stores': len(all_store_results),
                'total_employees': total_employees,
                'total_commission': total_commission,
                'stores': store_summaries,
                'period': {
                    'from_date': from_date,
                    'to_date': to_date,
                    'month': month,
                    'year': year
                }
            }
        }), 200

    except ValueError as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400

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
        month_str = request.args.get('month')
        year_str = request.args.get('year')

        if not month_str or not year_str:
            return jsonify({
                'success': False,
                'error': 'Missing required query parameters: month, year'
            }), 400

        try:
            month = int(month_str)
            year = int(year_str)
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'month and year must be integers'
            }), 400

        if not (1 <= month <= 12):
            return jsonify({
                'success': False,
                'error': 'month must be between 1 and 12'
            }), 400

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

    Note: is_manager is NOT persisted here. It is determined at query time
    from employee_manager_history and can be overridden at runtime by the
    frontend during commission calculation (never saved).

    URL Parameter:
        - employee_code: string (e.g. "GL013")

    Request Body:
        {
            "month": integer (1-12),
            "year": integer,
            "personal_target": integer (VND),
            "working_day": integer
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
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({
                'success': False,
                'error': 'Request body is required'
            }), 400

        required_fields = ['month', 'year', 'personal_target', 'working_day']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        try:
            month = int(data['month'])
            year = int(data['year'])
        except (ValueError, TypeError):
            return jsonify({
                'success': False,
                'error': 'month and year must be integers'
            }), 400

        if not (1 <= month <= 12):
            return jsonify({
                'success': False,
                'error': 'month must be between 1 and 12'
            }), 400

        service = CommissionSettingsService()
        result = service.update_commission_settings(
            employee_code=employee_code,
            month=month,
            year=year,
            personal_target=data['personal_target'],
            working_day=data['working_day']
        )

        if not result.get('success', False):
            return jsonify(result), 500

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
        month_str = request.args.get('month')
        year_str = request.args.get('year')

        if not month_str or not year_str:
            return jsonify({
                'success': False,
                'error': 'Missing required query parameters: month, year'
            }), 400

        try:
            month = int(month_str)
            year = int(year_str)
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'month and year must be integers'
            }), 400

        if not (1 <= month <= 12):
            return jsonify({
                'success': False,
                'error': 'month must be between 1 and 12'
            }), 400

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
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({
                'success': False,
                'error': 'Request body is required'
            }), 400

        for field in ['month', 'year']:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        try:
            month = int(data['month'])
            year = int(data['year'])
        except (ValueError, TypeError):
            return jsonify({
                'success': False,
                'error': 'month and year must be integers'
            }), 400

        if not (1 <= month <= 12):
            return jsonify({
                'success': False,
                'error': 'month must be between 1 and 12'
            }), 400

        service = CommissionStoreSettingsService()
        result = service.update_commission_store_settings(
            store_code=store_code,
            month=month,
            year=year,
            store_target=data.get('store_target'),
            fp_ratio_target=data.get('fp_ratio_target')
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
        month_str = request.args.get('month')
        year_str = request.args.get('year')

        if not month_str or not year_str:
            return jsonify({
                'success': False,
                'error': 'Missing required query parameters: month, year'
            }), 400

        try:
            month = int(month_str)
            year = int(year_str)
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'month and year must be integers'
            }), 400

        if not (1 <= month <= 12):
            return jsonify({
                'success': False,
                'error': 'month must be between 1 and 12'
            }), 400

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
                {"revenue_type": "full_price", "adjustment": 50000000},
                {"revenue_type": "markdown",   "adjustment": -10000000}
            ]
        }

    Valid revenue_type values: full_price, markdown, jewelry, vhernier, rosa_maria,
                               hand_carry, suitcase
    """
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({
                'success': False,
                'error': 'Request body is required'
            }), 400

        for field in ['month', 'year', 'adjustments']:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        try:
            month = int(data['month'])
            year = int(data['year'])
        except (ValueError, TypeError):
            return jsonify({
                'success': False,
                'error': 'month and year must be integers'
            }), 400

        if not (1 <= month <= 12):
            return jsonify({
                'success': False,
                'error': 'month must be between 1 and 12'
            }), 400

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
    calculates all commission components, and optionally writes results to Google Sheets.

    Request Body:
        {
            "month": 3,
            "year": 2026,
            "spreadsheet_id": "..." (optional — if provided, results are written to Sheets),
            "sheet_name": "Sheet1" (optional, default "Sheet1")
        }
    """
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({
                'success': False,
                'error': 'Request body is required'
            }), 400

        for field in ['month', 'year']:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        try:
            month = int(data['month'])
            year = int(data['year'])
        except (ValueError, TypeError):
            return jsonify({
                'success': False,
                'error': 'month and year must be integers'
            }), 400

        if not (1 <= month <= 12):
            return jsonify({
                'success': False,
                'error': 'month must be between 1 and 12'
            }), 400

        service = CommissionService()
        result = service.calculate_commissions_for_period(
            month=month,
            year=year,
            spreadsheet_id=data.get('spreadsheet_id'),
            sheet_name=data.get('sheet_name', 'Sheet1')
        )

        if not result.get('success', False):
            return jsonify(result), 500

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
