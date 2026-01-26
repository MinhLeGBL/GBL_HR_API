"""
Commission API Routes
"""
from flask import Blueprint, jsonify, request, Response
from app.services.commission_service import CommissionService
import pandas as pd
import io

commission_bp = Blueprint('commission', __name__, url_prefix='/api/v1/commission')


@commission_bp.route('/calculate', methods=['POST'])
def calculate_commission():
    """
    Calculate store commission for employees

    Request Body:
        {
            "store_code": "string",
            "store_name": "string",
            "target_revenue": number,
            "target_fp_ratio": number (0.0 to 1.0),
            "year": integer,
            "month": integer,
            "start_date": "YYYY-MM-DD HH:MI:SS",
            "end_date": "YYYY-MM-DD HH:MI:SS"
        }

    Returns:
        JSON response with commission calculation results
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = [
            'store_code', 'store_name', 'target_revenue', 'target_fp_ratio',
            'year', 'month', 'start_date', 'end_date'
        ]
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        # Validate target_fp_ratio range
        if not (0.0 <= data['target_fp_ratio'] <= 1.0):
            return jsonify({
                'success': False,
                'error': 'target_fp_ratio must be between 0.0 and 1.0'
            }), 400

        # Execute commission calculation
        service = CommissionService()
        result = service.calculate_store_commission(
            store_code=data['store_code'],
            store_name=data['store_name'],
            target_revenue=data['target_revenue'],
            target_fp_ratio=data['target_fp_ratio'],
            year=data['year'],
            month=data['month'],
            start_date=data['start_date'],
            end_date=data['end_date']
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


@commission_bp.route('/dataframe', methods=['POST'])
def get_commission_dataframe():
    """
    Get commission data as a pandas DataFrame (JSON format)

    Request Body:
        {
            "store_code": "string",
            "store_name": "string",
            "target_revenue": number,
            "target_fp_ratio": number (0.0 to 1.0),
            "year": integer,
            "month": integer,
            "start_date": "YYYY-MM-DD HH:MI:SS",
            "end_date": "YYYY-MM-DD HH:MI:SS"
        }

    Returns:
        JSON response with DataFrame in records format
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = [
            'store_code', 'store_name', 'target_revenue', 'target_fp_ratio',
            'year', 'month', 'start_date', 'end_date'
        ]
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        # Validate target_fp_ratio range
        if not (0.0 <= data['target_fp_ratio'] <= 1.0):
            return jsonify({
                'success': False,
                'error': 'target_fp_ratio must be between 0.0 and 1.0'
            }), 400

        # Get DataFrame
        service = CommissionService()
        df = service.get_commission_dataframe(
            store_code=data['store_code'],
            store_name=data['store_name'],
            target_revenue=data['target_revenue'],
            target_fp_ratio=data['target_fp_ratio'],
            year=data['year'],
            month=data['month'],
            start_date=data['start_date'],
            end_date=data['end_date']
        )

        # Convert DataFrame to JSON
        df_json = df.to_dict(orient='records')

        return jsonify({
            'success': True,
            'data': {
                'records': df_json,
                'columns': list(df.columns),
                'row_count': len(df)
            }
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@commission_bp.route('/export/csv', methods=['POST'])
def export_commission_csv():
    """
    Export commission data as CSV file

    Request Body:
        {
            "store_code": "string",
            "store_name": "string",
            "target_revenue": number,
            "target_fp_ratio": number (0.0 to 1.0),
            "year": integer,
            "month": integer,
            "start_date": "YYYY-MM-DD HH:MI:SS",
            "end_date": "YYYY-MM-DD HH:MI:SS"
        }

    Returns:
        CSV file download
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = [
            'store_code', 'store_name', 'target_revenue', 'target_fp_ratio',
            'year', 'month', 'start_date', 'end_date'
        ]
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        # Validate target_fp_ratio range
        if not (0.0 <= data['target_fp_ratio'] <= 1.0):
            return jsonify({
                'success': False,
                'error': 'target_fp_ratio must be between 0.0 and 1.0'
            }), 400

        # Get DataFrame
        service = CommissionService()
        df = service.get_commission_dataframe(
            store_code=data['store_code'],
            store_name=data['store_name'],
            target_revenue=data['target_revenue'],
            target_fp_ratio=data['target_fp_ratio'],
            year=data['year'],
            month=data['month'],
            start_date=data['start_date'],
            end_date=data['end_date']
        )

        # Convert to CSV
        output = io.StringIO()
        df.to_csv(output, index=False)
        output.seek(0)

        # Create response
        response = Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename=commission_{data["store_code"]}_{data["year"]}_{data["month"]}.csv'
            }
        )

        return response

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@commission_bp.route('/export/excel', methods=['POST'])
def export_commission_excel():
    """
    Export commission data as Excel file

    Request Body:
        {
            "store_code": "string",
            "store_name": "string",
            "target_revenue": number,
            "target_fp_ratio": number (0.0 to 1.0),
            "year": integer,
            "month": integer,
            "start_date": "YYYY-MM-DD HH:MI:SS",
            "end_date": "YYYY-MM-DD HH:MI:SS"
        }

    Returns:
        Excel file download
    """
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = [
            'store_code', 'store_name', 'target_revenue', 'target_fp_ratio',
            'year', 'month', 'start_date', 'end_date'
        ]
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        # Validate target_fp_ratio range
        if not (0.0 <= data['target_fp_ratio'] <= 1.0):
            return jsonify({
                'success': False,
                'error': 'target_fp_ratio must be between 0.0 and 1.0'
            }), 400

        # Get DataFrame
        service = CommissionService()
        df = service.get_commission_dataframe(
            store_code=data['store_code'],
            store_name=data['store_name'],
            target_revenue=data['target_revenue'],
            target_fp_ratio=data['target_fp_ratio'],
            year=data['year'],
            month=data['month'],
            start_date=data['start_date'],
            end_date=data['end_date']
        )

        # Convert to Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Commission')
        output.seek(0)

        # Create response
        response = Response(
            output.getvalue(),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={
                'Content-Disposition': f'attachment; filename=commission_{data["store_code"]}_{data["year"]}_{data["month"]}.xlsx'
            }
        )

        return response

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


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
