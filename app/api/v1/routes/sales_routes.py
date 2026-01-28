"""
Sales API Routes
"""
from flask import Blueprint, jsonify, request
from app.services.sales_service import SalesService
from app.api.v1.schemas.sales_schemas import (
    SalesReportRequest,
    SalesReportResponse,
    StoreSalesSummaryResponse,
    CustomerAnalysisResponse
)

sales_bp = Blueprint('sales', __name__, url_prefix='/api/v1/sales')


@sales_bp.route('/reports/detailed', methods=['POST'])
def get_detailed_sales_report():
    """
    Get detailed sales report

    Request Body:
        {
            "start_date": "YYYY-MM-DD HH:MI:SS",
            "end_date": "YYYY-MM-DD HH:MI:SS"
        }

    Returns:
        JSON response with detailed sales report
    """
    try:
        data = request.get_json()

        # Validate request
        is_valid, error_msg = SalesReportRequest.validate(data)
        if not is_valid:
            return jsonify({
                'success': False,
                'error': error_msg
            }), 400

        # Execute query
        service = SalesService()
        report_data = service.get_detailed_sales_report(
            data['start_date'],
            data['end_date']
        )

        response = SalesReportResponse.format(report_data, 'detailed_sales')
        return jsonify({
            'success': True,
            'data': response
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@sales_bp.route('/reports/store-summary', methods=['POST'])
def get_store_sales_summary():
    """
    Get store sales summary

    Request Body:
        {
            "start_date": "YYYY-MM-DD HH:MI:SS",
            "end_date": "YYYY-MM-DD HH:MI:SS"
        }

    Returns:
        JSON response with store sales summary
    """
    try:
        data = request.get_json()

        # Validate request
        is_valid, error_msg = SalesReportRequest.validate(data)
        if not is_valid:
            return jsonify({
                'success': False,
                'error': error_msg
            }), 400

        # Execute query
        service = SalesService()
        summary_data = service.get_store_sales_summary(
            data['start_date'],
            data['end_date']
        )

        response = StoreSalesSummaryResponse.format(summary_data)
        return jsonify({
            'success': True,
            'data': response
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@sales_bp.route('/reports/customer-analysis', methods=['POST'])
def get_customer_analysis():
    """
    Get customer purchase analysis

    Request Body:
        {
            "start_date": "YYYY-MM-DD HH:MI:SS",
            "end_date": "YYYY-MM-DD HH:MI:SS"
        }

    Returns:
        JSON response with customer analysis
    """
    try:
        data = request.get_json()

        # Validate request
        is_valid, error_msg = SalesReportRequest.validate(data)
        if not is_valid:
            return jsonify({
                'success': False,
                'error': error_msg
            }), 400

        # Execute query
        service = SalesService()
        analysis_data = service.get_customer_analysis(
            data['start_date'],
            data['end_date']
        )

        response = CustomerAnalysisResponse.format(analysis_data)
        return jsonify({
            'success': True,
            'data': response
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
