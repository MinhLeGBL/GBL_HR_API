"""
HR Employee API Routes - PostgreSQL-based employee management for frontend
"""
from functools import wraps
from flask import Blueprint, jsonify, request, g
from app.services.hr_employee_service import HREmployeeService
from app.services.auth_service import AuthService, UserRole

hr_employee_bp = Blueprint('hr_employees', __name__, url_prefix='/api/v1/employees')
hr_employee_service = HREmployeeService()
auth_service = AuthService()


def token_required(f):
    """Decorator to require a valid JWT token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None

        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header[7:]

        if not token:
            return jsonify({
                'success': False,
                'error': 'Authorization token is missing'
            }), 401

        payload = auth_service.verify_token(token)
        if not payload:
            return jsonify({
                'success': False,
                'error': 'Invalid or expired token'
            }), 401

        if payload.get('type') != 'access':
            return jsonify({
                'success': False,
                'error': 'Invalid token type'
            }), 401

        g.sid = payload.get('sid')
        g.email = payload.get('email')
        g.role = payload.get('role')

        user = auth_service.get_user_by_sid(g.sid)
        if user:
            g.department_id = user.get('department_id')
            g.department_code = user.get('department', {}).get('code') if user.get('department') else None

        return f(*args, **kwargs)

    return decorated


def admin_or_hr_it_manager_required(f):
    """Decorator to require admin or HR/IT manager"""
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        if g.role == UserRole.ADMIN:
            return f(*args, **kwargs)

        if g.role == UserRole.MANAGER and g.department_code in ['HR', 'IT']:
            return f(*args, **kwargs)

        return jsonify({
            'success': False,
            'error': 'Admin or HR/IT department manager access required'
        }), 403

    return decorated


@hr_employee_bp.route('', methods=['GET'])
@admin_or_hr_it_manager_required
def get_all_employees():
    """
    Get all employees

    Response:
        {
            "success": true,
            "employees": [...]
        }
    """
    result = hr_employee_service.get_all_employees()

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

    # Validate employee_type
    if data['employee_type'] not in ['store', 'office']:
        return jsonify({
            'success': False,
            'error': 'Invalid employee_type. Must be: store or office'
        }), 400

    # Validate contract
    if data['contract'] not in ['probation', 'intern', 'permanent']:
        return jsonify({
            'success': False,
            'error': 'Invalid contract. Must be: probation, intern, or permanent'
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

    # Validate employee_type if provided
    if 'employee_type' in data and data['employee_type'] not in ['store', 'office']:
        return jsonify({
            'success': False,
            'error': 'Invalid employee_type. Must be: store or office'
        }), 400

    # Validate contract if provided
    if 'contract' in data and data['contract'] not in ['probation', 'intern', 'permanent']:
        return jsonify({
            'success': False,
            'error': 'Invalid contract. Must be: probation, intern, or permanent'
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


@hr_employee_bp.route('/init', methods=['POST'])
@admin_or_hr_it_manager_required
def init_employees_table():
    """Initialize employees table (admin or HR/IT manager)"""
    result = hr_employee_service.init_database()

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500
