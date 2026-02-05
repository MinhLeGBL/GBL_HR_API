"""
Employee API Routes
"""
from flask import Blueprint, jsonify, request
from app.services.employee_service import EmployeeService
from app.api.v1.schemas.employee_schemas import EmployeeListResponse

employee_bp = Blueprint('employee', __name__, url_prefix='/api/v1/rp-employees')  # RetailPro employees (Oracle)


@employee_bp.route('/', methods=['GET'])
def get_employees():
    """
    Get all employees

    Returns:
        JSON response with employee list
    """
    try:
        service = EmployeeService()
        employees = service.get_all_employees()

        response = EmployeeListResponse.format(employees)
        return jsonify({
            'success': True,
            'data': response
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@employee_bp.route('/<employee_code>', methods=['GET'])
def get_employee_by_code(employee_code):
    """
    Get employee by employee code

    Args:
        employee_code: Employee code

    Returns:
        JSON response with employee details
    """
    try:
        service = EmployeeService()
        employee = service.get_employee_by_code(employee_code)

        if not employee:
            return jsonify({
                'success': False,
                'error': 'Employee not found'
            }), 404

        return jsonify({
            'success': True,
            'data': employee
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@employee_bp.route('/store/<store_code>', methods=['GET'])
def get_employees_by_store(store_code):
    """
    Get employees by store code

    Args:
        store_code: Store code

    Returns:
        JSON response with employee list
    """
    try:
        service = EmployeeService()
        employees = service.get_employees_by_store(store_code)

        response = EmployeeListResponse.format(employees)
        return jsonify({
            'success': True,
            'data': response
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
