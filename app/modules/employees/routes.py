"""
HR Employee API Routes - PostgreSQL-based employee management for frontend
"""
from flask import Blueprint, jsonify, request
from app.modules.employees.service import HREmployeeService
from app.core.auth.middleware import token_required, admin_or_hr_it_manager_required

hr_employee_bp = Blueprint('hr_employees', __name__, url_prefix='/api/v1/employees')
hr_employee_service = HREmployeeService()


@hr_employee_bp.route('', methods=['GET'])
@admin_or_hr_it_manager_required
def get_all_employees():
    """
    Get all employees

    Query Params (optional):
        department_code: Filter by department code (e.g. "HR", "IT")
        role: Filter by user role (e.g. "staff", "manager")

    Response:
        {
            "success": true,
            "employees": [...]
        }
    """
    department_code = request.args.get('department_code')
    role = request.args.get('role')

    result = hr_employee_service.get_all_employees(
        department_code=department_code,
        role=role
    )

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@hr_employee_bp.route('/<int:sid>', methods=['GET'])
@admin_or_hr_it_manager_required
def get_employee(sid):
    """
    Get an employee by SID

    Response:
        {
            "success": true,
            "employee": {...}
        }
    """
    result = hr_employee_service.get_employee_by_sid(sid)

    if result['success']:
        return jsonify(result), 200
    if 'not found' in result.get('error', '').lower():
        return jsonify(result), 404
    return jsonify(result), 500


@hr_employee_bp.route('', methods=['POST'])
@admin_or_hr_it_manager_required
def create_employee():
    """
    Create a new employee

    Request Body (Store Employee):
        {
            "employee_code": "EMP001",
            "full_name": "Nguyen Van A",
            "employee_type": "store",
            "contract": "permanent",
            "department_id": 1,
            "store_id": 1,
            "join_date": "2024-01-15",
            "is_active": true
        }

    Request Body (Office Employee):
        {
            "employee_code": "EMP002",
            "full_name": "Tran Thi B",
            "employee_type": "office",
            "contract": "probation",
            "department_id": 4,
            "email": "tran.b@company.com",
            "join_date": "2025-06-01",
            "is_active": true
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body is required'
        }), 400

    # Validate required fields
    required_fields = ['employee_code', 'full_name', 'employee_type', 'contract',
                       'department_id', 'join_date']
    for field in required_fields:
        if field not in data or data[field] is None:
            return jsonify({
                'success': False,
                'error': f'Missing required field: {field}'
            }), 400

    result = hr_employee_service.create_employee(
        employee_code=data['employee_code'],
        full_name=data['full_name'],
        employee_type=data['employee_type'],
        contract=data['contract'],
        department_id=data['department_id'],
        join_date=data['join_date'],
        store_id=data.get('store_id'),
        email=data.get('email'),
        is_active=data.get('is_active', True)
    )

    if result['success']:
        return jsonify(result), 201
    return jsonify(result), 400


@hr_employee_bp.route('/<int:sid>', methods=['PUT'])
@admin_or_hr_it_manager_required
def update_employee(sid):
    """
    Update an employee (employee_code cannot be changed)

    Request Body:
        {
            "full_name": "Nguyen Van A Updated",
            "employee_type": "store",
            "contract": "permanent",
            "department_id": 1,
            "store_id": 2,
            "join_date": "2024-01-15",
            "is_active": true
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body is required'
        }), 400

    # Build updates dict (exclude employee_code as it cannot be changed)
    allowed_fields = ['full_name', 'employee_type', 'contract', 'department_id',
                      'store_id', 'email', 'is_active', 'join_date']
    updates = {k: v for k, v in data.items() if k in allowed_fields}

    if not updates:
        return jsonify({
            'success': False,
            'error': 'No valid fields to update'
        }), 400

    result = hr_employee_service.update_employee(sid, updates)

    if result['success']:
        return jsonify(result), 200
    if 'not found' in result.get('error', '').lower():
        return jsonify(result), 404
    return jsonify(result), 400


@hr_employee_bp.route('/<int:sid>', methods=['DELETE'])
@admin_or_hr_it_manager_required
def delete_employee(sid):
    """
    Delete an employee

    Response:
        {
            "success": true
        }
    """
    result = hr_employee_service.delete_employee(sid)

    if result['success']:
        return jsonify({'success': True}), 200
    if 'not found' in result.get('error', '').lower():
        return jsonify(result), 404
    return jsonify(result), 400


@hr_employee_bp.route('/<int:sid>/sync-retailpro', methods=['POST'])
@admin_or_hr_it_manager_required
def sync_retailpro(sid):
    """
    Sync RetailPro account data for an employee.

    Looks up the employee's RetailPro account by employee_code and updates
    retailpro_username and retailpro_sid fields.

    Response (success):
        {
            "success": true,
            "employee": {...}
        }

    Response (no RetailPro account):
        {
            "success": false,
            "error": "No RetailPro account found for employee code EMP001"
        }
    """
    result = hr_employee_service.sync_retailpro_data(sid)

    if result['success']:
        return jsonify(result), 200
    if 'not found' in result.get('error', '').lower():
        return jsonify(result), 404
    return jsonify(result), 400


# ==================== Lookup Endpoints ====================

@hr_employee_bp.route('/types', methods=['GET'])
@token_required
def get_employee_types():
    """
    Get all employee types (lookup data)

    Response:
        {
            "success": true,
            "employee_types": [
                {"id": 10001, "code": "OFFICE", "name": "Office", "is_active": true},
                {"id": 10002, "code": "STORE", "name": "Store", "is_active": true}
            ]
        }
    """
    result = hr_employee_service.get_employee_types()

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@hr_employee_bp.route('/contracts', methods=['GET'])
@token_required
def get_contract_types():
    """
    Get all contract types (lookup data)

    Response:
        {
            "success": true,
            "contract_types": [
                {"id": 20001, "code": "PERMANENT", "name": "Permanent", "is_active": true},
                {"id": 20002, "code": "INTERN", "name": "Intern", "is_active": true},
                {"id": 20003, "code": "PROBATION", "name": "Probation", "is_active": true}
            ]
        }
    """
    result = hr_employee_service.get_contract_types()

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500
