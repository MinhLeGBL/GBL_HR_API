"""
Dashboard API Routes
"""
from flask import Blueprint, jsonify, request, g
from app.modules.dashboard.service import DashboardService
from app.core.auth.middleware import token_required, manager_required

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/api/v1/dashboard')
dashboard_service = DashboardService()


# ==================== Configuration Routes ====================

@dashboard_bp.route('/config/<department_code>', methods=['GET'])
@token_required
def get_dashboard_config(department_code):
    """
    Get dashboard configuration for a department

    Returns null config if no configuration exists yet.
    Frontend will use default widgets when config is null.

    Response (Config Exists):
        {
            "success": true,
            "config": {
                "id": 1,
                "department_code": "SALES",
                "widgets": [
                    { "id": "widget-uuid-1", "type": "total_employees", "position": 0 },
                    { "id": "widget-uuid-2", "type": "monthly_sales", "position": 1 }
                ],
                "updated_at": "2026-01-29T10:00:00",
                "updated_by": 123
            }
        }

    Response (No Config):
        {
            "success": true,
            "config": null
        }
    """
    result = dashboard_service.get_dashboard_config(department_code)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@dashboard_bp.route('/config/<department_code>', methods=['PUT'])
@manager_required
def save_dashboard_config(department_code):
    """
    Save dashboard configuration for a department (manager or admin only)

    Request Body:
        {
            "widgets": [
                { "id": "widget-uuid-1", "type": "total_employees", "position": 0 },
                { "id": "widget-uuid-2", "type": "monthly_sales", "position": 1 }
            ]
        }

    Response:
        {
            "success": true,
            "config": {
                "id": 1,
                "department_code": "SALES",
                "widgets": [...],
                "updated_at": "2026-01-29T10:00:00",
                "updated_by": 123
            }
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    widgets = data.get('widgets', [])

    if not isinstance(widgets, list):
        return jsonify({'success': False, 'error': 'widgets must be an array'}), 400

    # Validate widget structure
    for widget in widgets:
        if not isinstance(widget, dict):
            return jsonify({'success': False, 'error': 'Each widget must be an object'}), 400
        if 'id' not in widget or 'type' not in widget or 'position' not in widget:
            return jsonify({'success': False, 'error': 'Each widget must have id, type, and position'}), 400

    result = dashboard_service.save_dashboard_config(
        department_code=department_code,
        widgets=widgets,
        updated_by=g.sid
    )

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


# ==================== Widget Data Routes ====================

@dashboard_bp.route('/widgets/<widget_type>', methods=['GET'])
@token_required
def get_widget_data(widget_type):
    """
    Get widget data for a specific widget type

    Query Parameters:
        - department (required): Department code (e.g., "SALES", "HR")

    Example: GET /api/v1/dashboard/widgets/total_employees?department=SALES

    Response:
        {
            "success": true,
            "data": {
                "value": 42,
                "label": "Total Employees",
                "trend": {
                    "direction": "up",
                    "percentage": 5.2
                },
                "subtext": "vs last month"
            }
        }
    """
    department = request.args.get('department')

    if not department:
        return jsonify({'success': False, 'error': 'department query parameter is required'}), 400

    result = dashboard_service.get_widget_data(widget_type, department)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400
