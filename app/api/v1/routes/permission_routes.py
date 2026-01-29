"""
Permission and Department API Routes
"""
from flask import Blueprint, jsonify, request, g
from app.services.permission_service import PermissionService
from app.api.v1.routes.auth_routes import token_required, admin_required

permission_bp = Blueprint('permissions', __name__, url_prefix='/api/v1')
permission_service = PermissionService()


# ==================== Permission Tables Initialization ====================

@permission_bp.route('/permissions/init-db', methods=['POST'])
def init_permission_tables():
    """
    Initialize departments, sections, and permissions tables
    Run this once to set up the permission system
    """
    result = permission_service.init_permission_tables()
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


# ==================== Department Routes ====================

@permission_bp.route('/departments', methods=['GET'])
@token_required
def get_departments():
    """
    Get all departments

    Requires: Valid access token
    """
    result = permission_service.get_all_departments()
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@permission_bp.route('/departments/<int:dept_id>', methods=['GET'])
@token_required
def get_department(dept_id):
    """
    Get department by ID

    Requires: Valid access token
    """
    department = permission_service.get_department_by_id(dept_id)
    if department:
        return jsonify({'success': True, 'department': department}), 200
    return jsonify({'success': False, 'error': 'Department not found'}), 404


@permission_bp.route('/departments', methods=['POST'])
@admin_required
def create_department():
    """
    Create a new department (admin only)

    Request Body:
        {
            "code": "MARKETING",
            "name": "Marketing",
            "description": "Marketing Department"
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    code = data.get('code')
    name = data.get('name')

    if not code or not name:
        return jsonify({'success': False, 'error': 'code and name are required'}), 400

    result = permission_service.create_department(
        code=code,
        name=name,
        description=data.get('description')
    )

    if result['success']:
        return jsonify(result), 201
    return jsonify(result), 400


@permission_bp.route('/departments/<int:dept_id>', methods=['PUT'])
@admin_required
def update_department(dept_id):
    """
    Update a department (admin only)

    Request Body (all fields optional):
        {
            "code": "MARKETING",
            "name": "Marketing Department",
            "description": "Updated description",
            "is_active": true
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    result = permission_service.update_department(dept_id, data)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


@permission_bp.route('/departments/<int:dept_id>', methods=['DELETE'])
@admin_required
def delete_department(dept_id):
    """
    Delete a department (admin only)

    Cannot delete departments with assigned users.
    """
    result = permission_service.delete_department(dept_id)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


# ==================== Section Routes ====================

@permission_bp.route('/sections', methods=['GET'])
@admin_required
def get_sections():
    """
    Get all sections with permissions (admin only)
    """
    result = permission_service.get_all_sections()
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


# ==================== Permission Routes ====================

@permission_bp.route('/permissions/me', methods=['GET'])
@token_required
def get_my_permissions():
    """
    Get current user's permissions

    Returns sections the user can access based on role and department.
    """
    permissions = permission_service.get_user_permissions(g.role, getattr(g, 'department_id', None))
    return jsonify({'success': True, 'permissions': permissions}), 200


@permission_bp.route('/sections/<int:section_id>/permissions', methods=['PUT'])
@admin_required
def update_section_permission(section_id):
    """
    Update section permission (admin only)

    Request Body:
        {
            "department_id": 1,
            "role": "manager",
            "can_view": true,
            "can_edit": true,
            "can_delete": false
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    required = ['department_id', 'role']
    for field in required:
        if field not in data:
            return jsonify({'success': False, 'error': f'{field} is required'}), 400

    if data['role'] not in ['admin', 'manager', 'staff']:
        return jsonify({'success': False, 'error': 'Invalid role'}), 400

    result = permission_service.update_section_permission(
        section_id=section_id,
        department_id=data['department_id'],
        role=data['role'],
        can_view=data.get('can_view', True),
        can_edit=data.get('can_edit', False),
        can_delete=data.get('can_delete', False),
        created_by=g.sid
    )

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


@permission_bp.route('/sections/<int:section_id>/departments', methods=['POST'])
@admin_required
def assign_section_to_department(section_id):
    """
    Assign section to department with roles (admin only)

    Request Body:
        {
            "department_id": 1,
            "roles": ["manager", "staff"],
            "can_view": true,
            "can_edit": false,
            "can_delete": false
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    department_id = data.get('department_id')
    roles = data.get('roles', ['staff'])

    if not department_id:
        return jsonify({'success': False, 'error': 'department_id is required'}), 400

    # Create permission for each role
    for role in roles:
        if role not in ['admin', 'manager', 'staff']:
            continue
        permission_service.update_section_permission(
            section_id=section_id,
            department_id=department_id,
            role=role,
            can_view=data.get('can_view', True),
            can_edit=data.get('can_edit', False),
            can_delete=data.get('can_delete', False),
            created_by=g.sid
        )

    return jsonify({'success': True, 'message': 'Permissions assigned successfully'}), 200


@permission_bp.route('/sections/<int:section_id>/departments/<int:department_id>', methods=['DELETE'])
@admin_required
def remove_department_from_section(section_id, department_id):
    """
    Remove department from section (admin only)
    """
    result = permission_service.delete_section_permission(section_id, department_id)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


# ==================== Section Groups Routes ====================

@permission_bp.route('/section-groups/init-db', methods=['POST'])
def init_section_groups():
    """
    Initialize section_groups and section_group_permissions tables
    Run this once to set up the section groups system
    """
    result = permission_service.init_section_groups()
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@permission_bp.route('/section-groups/permissions', methods=['GET'])
@token_required
def get_section_group_permissions():
    """
    Get all section group permissions

    Returns sections groups with their allowed_departments.
    Empty allowed_departments means all departments have access.
    ADMINISTRATION group is not returned (hardcoded to IT in frontend).

    Response:
        {
            "success": true,
            "permissions": [
                {
                    "group_code": "MAIN",
                    "group_name": "Main",
                    "allowed_departments": []
                },
                {
                    "group_code": "COMMISSION",
                    "group_name": "Commission",
                    "allowed_departments": ["HR", "SALES"]
                }
            ]
        }
    """
    result = permission_service.get_section_group_permissions()
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@permission_bp.route('/section-groups/<group_code>/departments', methods=['PUT'])
@admin_required
def update_section_group_access(group_code):
    """
    Update section group department access (admin only)

    Request Body:
        {
            "allowed_departments": ["HR", "SALES", "FINANCE"]
        }

    Empty array means all departments have access.
    """
    data = request.get_json()

    if data is None:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    allowed_departments = data.get('allowed_departments', [])

    if not isinstance(allowed_departments, list):
        return jsonify({'success': False, 'error': 'allowed_departments must be an array'}), 400

    result = permission_service.update_section_group_departments(
        group_code=group_code.upper(),
        allowed_departments=allowed_departments,
        created_by=g.sid
    )

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400
