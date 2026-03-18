"""
Permission and Department API Routes

Permission Model (v2 — Flat Sections):
  Per-section access control with departments, roles, inclusions, exclusions.
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


# ==================== Permission Routes ====================

@permission_bp.route('/permissions/me', methods=['GET'])
@token_required
def get_my_permissions():
    """
    Get current user's accessible sections (v2 flat list).

    Resolution: admin → all; exclusion → denied; inclusion → allowed;
    dept+role match → allowed; else denied. DASHBOARD always included.

    Response:
        {
            "success": true,
            "permissions": [
                {"section_code": "DASHBOARD", "section_name": "Dashboard"},
                {"section_code": "COMMISSION_DATA", "section_name": "Commission Data"}
            ]
        }
    """
    permissions = permission_service.get_user_permissions_v2(
        user_sid=g.sid,
        user_role=g.role,
        department_code=getattr(g, 'department_code', None)
    )
    return jsonify({'success': True, 'permissions': permissions}), 200


# ==================== V2 Flat Permission Routes ====================

@permission_bp.route('/sections/permissions', methods=['GET'])
@token_required
def get_section_permissions_v2():
    """
    Get all sections with their full access config (v2 flat model).
    Auth: admin or user with ACCESS_MANAGEMENT access.
    """
    # Check access: admin or has ACCESS_MANAGEMENT
    if g.role != UserRole.ADMIN:
        user_perms = permission_service.get_user_permissions_v2(
            user_sid=g.sid,
            user_role=g.role,
            department_code=getattr(g, 'department_code', None)
        )
        has_access = any(p['section_code'] == 'ACCESS_MANAGEMENT' for p in user_perms)
        if not has_access:
            return jsonify({'success': False, 'error': 'Access denied'}), 403

    result = permission_service.get_all_section_permissions()
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@permission_bp.route('/sections/<section_code>/access', methods=['PUT'])
@token_required
def update_section_access(section_code):
    """
    Update a section's full access config (v2 flat model).
    Auth: admin or user with ACCESS_MANAGEMENT access.
    ACCESS_MANAGEMENT section itself: admin only.
    """
    section_code = section_code.upper()

    # ACCESS_MANAGEMENT section: admin only
    if section_code == 'ACCESS_MANAGEMENT' and g.role != UserRole.ADMIN:
        return jsonify({'success': False, 'error': 'Only admin can modify ACCESS_MANAGEMENT'}), 403

    # Check access: admin or has ACCESS_MANAGEMENT
    if g.role != UserRole.ADMIN:
        user_perms = permission_service.get_user_permissions_v2(
            user_sid=g.sid,
            user_role=g.role,
            department_code=getattr(g, 'department_code', None)
        )
        has_access = any(p['section_code'] == 'ACCESS_MANAGEMENT' for p in user_perms)
        if not has_access:
            return jsonify({'success': False, 'error': 'Access denied'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Request body is required'}), 400

    allowed_departments = data.get('allowed_departments', [])
    allowed_roles = data.get('allowed_roles', [])
    included_user_sids = data.get('included_user_sids', [])
    excluded_user_sids = data.get('excluded_user_sids', [])

    # Type validation
    if not isinstance(allowed_departments, list):
        return jsonify({'success': False, 'error': 'allowed_departments must be an array'}), 400
    if not isinstance(allowed_roles, list):
        return jsonify({'success': False, 'error': 'allowed_roles must be an array'}), 400
    if not isinstance(included_user_sids, list):
        return jsonify({'success': False, 'error': 'included_user_sids must be an array'}), 400
    if not isinstance(excluded_user_sids, list):
        return jsonify({'success': False, 'error': 'excluded_user_sids must be an array'}), 400

    # Normalize department codes to uppercase
    allowed_departments = [d.upper() for d in allowed_departments]

    result = permission_service.update_section_access(
        section_code=section_code,
        allowed_departments=allowed_departments,
        allowed_roles=allowed_roles,
        included_user_sids=included_user_sids,
        excluded_user_sids=excluded_user_sids
    )

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400
