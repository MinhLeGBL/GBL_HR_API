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
