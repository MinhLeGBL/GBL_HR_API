"""
Permission and Department API Routes

Permission Model:
  Layer 1 (Group): section_groups → section_group_permissions (department-based)
  Layer 2 (Item):  sections → section_permissions (role-based)
"""
from flask import Blueprint, jsonify, request, g
from app.modules.permissions.service import PermissionService
from app.core.auth.service import UserRole
from app.core.auth.middleware import token_required, admin_required

permission_bp = Blueprint('permissions', __name__, url_prefix='/api/v1')
permission_service = PermissionService()


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
    Get all sections with their group and role access (admin only)

    Response:
        {
            "success": true,
            "sections": [
                {
                    "id": 1,
                    "code": "DASHBOARD",
                    "name": "Dashboard",
                    "group_code": "MAIN",
                    "group_name": "Main",
                    "allowed_roles": ["admin", "manager", "staff"]
                }
            ]
        }
    """
    result = permission_service.get_all_sections()
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@permission_bp.route('/sections/<section_code>/roles', methods=['PUT'])
@admin_required
def update_section_roles(section_code):
    """
    Update which roles can access a section item (admin only)

    Request Body:
        {
            "roles": ["admin", "manager", "staff"]
        }

    Response:
        {
            "success": true,
            "message": "Section roles updated successfully"
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    roles = data.get('roles')
    if roles is None or not isinstance(roles, list):
        return jsonify({'success': False, 'error': 'roles must be an array'}), 400

    result = permission_service.update_section_roles(
        section_code=section_code.upper(),
        roles=roles
    )

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


# ==================== Permission Routes ====================

@permission_bp.route('/permissions/me', methods=['GET'])
@token_required
def get_my_permissions():
    """
    Get current user's accessible section groups and items.

    Returns groups filtered by department (Layer 1) and
    items filtered by role (Layer 2). Admin sees everything.

    Response:
        {
            "success": true,
            "permissions": [
                {
                    "group_code": "MAIN",
                    "group_name": "Main",
                    "sections": [
                        {"section_code": "DASHBOARD", "section_name": "Dashboard"}
                    ]
                }
            ]
        }
    """
    permissions = permission_service.get_user_permissions(
        user_role=g.role,
        department_id=getattr(g, 'department_id', None),
        department_code=getattr(g, 'department_code', None)
    )
    return jsonify({'success': True, 'permissions': permissions}), 200


# ==================== Section Groups Routes ====================

@permission_bp.route('/section-groups/permissions', methods=['GET'])
@token_required
def get_section_group_permissions():
    """
    Get all section group permissions with sections and role access.
    ADMINISTRATION group is excluded (hardcoded to IT in frontend).

    Response:
        {
            "success": true,
            "permissions": [
                {
                    "group_code": "MAIN",
                    "group_name": "Main",
                    "allowed_departments": ["HR", "ACC", "IT", ...],
                    "sections": [
                        {
                            "code": "DASHBOARD",
                            "name": "Dashboard",
                            "allowed_roles": ["admin", "manager", "staff"]
                        }
                    ]
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
            "allowed_departments": ["HR", "SALES", "ACC"]
        }

    Empty array means admin-only access (no departments selected).
    """
    data = request.get_json()

    if data is None:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    allowed_departments = data.get('allowed_departments', [])

    if not isinstance(allowed_departments, list):
        return jsonify({'success': False, 'error': 'allowed_departments must be an array'}), 400

    result = permission_service.update_section_group_departments(
        group_code=group_code.upper(),
        allowed_departments=allowed_departments
    )

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


# ==================== Per-Department Access Routes ====================

def _is_admin_or_department_manager(department_code):
    """Check if current user is admin or manager of the target department."""
    if g.role == UserRole.ADMIN:
        return True
    if g.role == UserRole.MANAGER and getattr(g, 'department_code', None) == department_code:
        return True
    return False


@permission_bp.route('/section-groups/permissions/department/<department_code>', methods=['GET'])
@token_required
def get_department_permissions(department_code):
    """
    Get all section groups and sections accessible by a department,
    with staff_allowed toggle and excluded employees per section.

    Response:
        {
            "success": true,
            "department_code": "HR",
            "permissions": [
                {
                    "group_code": "MAIN",
                    "group_name": "Main",
                    "sections": [
                        {
                            "section_code": "DASHBOARD",
                            "section_name": "Dashboard",
                            "staff_allowed": true,
                            "excluded_employees": [
                                {"sid": 300000001, "employee_code": "EMP001", "full_name": "Nguyen Van A"}
                            ]
                        }
                    ]
                }
            ]
        }
    """
    result = permission_service.get_department_permissions(department_code.upper())

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@permission_bp.route('/sections/<section_code>/department-access', methods=['PUT'])
@token_required
def update_department_staff_access(section_code):
    """
    Toggle staff access for a section within a department.

    Requires: Admin or manager of the target department.

    Request Body:
        {
            "department_code": "HR",
            "staff_allowed": false
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    department_code = data.get('department_code')
    staff_allowed = data.get('staff_allowed')

    if not department_code or staff_allowed is None:
        return jsonify({'success': False, 'error': 'department_code and staff_allowed are required'}), 400

    if not isinstance(staff_allowed, bool):
        return jsonify({'success': False, 'error': 'staff_allowed must be a boolean'}), 400

    department_code = department_code.upper()

    if not _is_admin_or_department_manager(department_code):
        return jsonify({'success': False, 'error': 'Admin or department manager access required'}), 403

    result = permission_service.update_department_staff_access(
        section_code=section_code.upper(),
        department_code=department_code,
        staff_allowed=staff_allowed
    )

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


@permission_bp.route('/sections/<section_code>/exclusions', methods=['GET'])
@token_required
def get_section_exclusions(section_code):
    """
    Get excluded employees for a section within a department.

    Query Params:
        department_code (required): e.g. "HR"

    Response:
        {
            "success": true,
            "excluded_employees": [
                {"sid": 300000001, "employee_code": "EMP001", "full_name": "Nguyen Van A"}
            ]
        }
    """
    department_code = request.args.get('department_code')

    if not department_code:
        return jsonify({'success': False, 'error': 'department_code query parameter is required'}), 400

    result = permission_service.get_section_exclusions(
        section_code=section_code.upper(),
        department_code=department_code.upper()
    )

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


@permission_bp.route('/sections/<section_code>/exclusions', methods=['PUT'])
@token_required
def update_section_exclusions(section_code):
    """
    Update excluded employees for a section within a department.

    Requires: Admin or manager of the target department.

    Request Body:
        {
            "department_code": "HR",
            "excluded_employee_sids": [300000001, 300000002]
        }

    Empty array clears all exclusions.
    """
    data = request.get_json()

    if not data:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    department_code = data.get('department_code')
    excluded_employee_sids = data.get('excluded_employee_sids')

    if not department_code or excluded_employee_sids is None:
        return jsonify({'success': False, 'error': 'department_code and excluded_employee_sids are required'}), 400

    if not isinstance(excluded_employee_sids, list):
        return jsonify({'success': False, 'error': 'excluded_employee_sids must be an array'}), 400

    department_code = department_code.upper()

    if not _is_admin_or_department_manager(department_code):
        return jsonify({'success': False, 'error': 'Admin or department manager access required'}), 403

    result = permission_service.update_section_exclusions(
        section_code=section_code.upper(),
        department_code=department_code,
        excluded_employee_sids=excluded_employee_sids
    )

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400
