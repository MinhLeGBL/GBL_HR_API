"""
User Management API Routes.

THE ADMIN ROLE IS NOT ASSIGNABLE THROUGH THIS API.

There is exactly one admin account, and admin bypasses every permission check
(see `PermissionService.get_user_permissions_v2` resolution rule 1), so handing
the role out through an endpoint means handing out everything. It used to be
possible: `PUT /<sid>` accepted any `role` value behind the
`admin_or_it_manager_required` gate, so an IT-department manager could promote
themselves. That is closed here — `_reject_admin_role` refuses the value on
both create and update.

Transferring admin is therefore a deliberate database operation, by decision:

    -- as a Postgres superuser, in ONE transaction
    BEGIN;
    UPDATE users SET role_id = (SELECT id FROM roles WHERE code = 'MANAGER')
     WHERE sid = <current admin sid>;
    UPDATE users SET role_id = (SELECT id FROM roles WHERE code = 'ADMIN')
     WHERE sid = <new admin sid>;
    COMMIT;

The admin row is also protected from role change, deactivation and deletion
through this API (`_protect_admin_row`), because any of those would leave zero
admins and no API path back.
"""
from flask import Blueprint, jsonify, request, g
from app.core.auth.service import UserRole
from app.core.auth.middleware import (
    token_required, admin_or_it_manager_required, auth_service
)

user_bp = Blueprint('users', __name__, url_prefix='/api/v1/users')

# Message shared by create and update so the two cannot drift apart.
_ADMIN_NOT_ASSIGNABLE = (
    'The admin role cannot be assigned through the API. There is exactly one '
    'admin account and it is changed by a direct database operation \u2014 see the '
    'module docstring in app/modules/auth/user_routes.py.'
)


def _reject_admin_role(role):
    """An error response when `role` is admin, else None.

    Returned for ANY caller, admin included: a second admin is not a thing this
    system has, so there is no one for whom granting it is correct.
    """
    if role and str(role).strip().lower() == UserRole.ADMIN:
        return jsonify({'success': False, 'error': _ADMIN_NOT_ASSIGNABLE}), 403
    return None


def _target_is_admin(sid):
    """Whether user `sid` holds the admin role.

    Its own query rather than `auth_service.get_user_by_sid`, deliberately:
    `user_routes` shares that service instance with the auth middleware, so a
    lookup of the TARGET and the middleware's lookup of the CALLER are the same
    method on the same object. One stub then answers both, every target looks
    like whoever is authenticated, and the guard below fires on innocent
    updates. Keeping it separate also keeps it separately patchable.

    False when the role cannot be read, so a database blip cannot start
    rejecting ordinary user edits. That means the guard FAILS OPEN, which is
    why the destructive paths carry a second, independent check that does not
    depend on this one: `delete_user` re-reads the target through
    `auth_service`, and `update_user` does the same before deactivating.
    """
    from app.core.database import get_postgres_connection
    conn = None
    try:
        conn = get_postgres_connection()
        if conn is None:
            return False
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.code FROM users u JOIN roles r ON r.id = u.role_id
                   WHERE u.sid = %s""",
                (sid,),
            )
            row = cur.fetchone()
        return bool(row) and str(row[0]).lower() == UserRole.ADMIN
    except Exception as e:                                    # pragma: no cover
        print(f'[WARN] admin-row check failed for sid={sid}: {e}')
        return False
    finally:
        if conn is not None:
            conn.close()


def _is_truthy(value):
    """Whether `value` means true, for a JSON field that should be a boolean.

    `not value` is not enough: a JSON body can carry the STRING "false", which
    is truthy in Python, so `not "false"` is False and a deactivation sailed
    past the guard — while psycopg2 then wrote boolean false to the column.
    Strings are compared explicitly, so "false", "0", "no" and "" all read as
    false, as does a real `False`.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ('false', '0', 'no', 'off', '')
    return bool(value)


def _protect_admin_row(sid, fields):
    """An error response when `fields` would strip the admin of its powers.

    Changing the admin's role or deactivating it leaves the system with zero
    admins, and since the role cannot be granted back through the API that is
    unrecoverable without database access. Blocked rather than warned about.
    `fields` is None for a delete.
    """
    if not _target_is_admin(sid):
        return None
    if fields is None:
        return jsonify({
            'success': False,
            'error': 'The admin account cannot be deleted through the API.',
        }), 403
    if 'role' in fields:
        return jsonify({
            'success': False,
            'error': "The admin account's role cannot be changed through the API.",
        }), 403
    if 'is_active' in fields and not _is_truthy(fields['is_active']):
        return jsonify({
            'success': False,
            'error': 'The admin account cannot be deactivated through the API.',
        }), 403
    return None


# ==================== User Management Routes ====================

@user_bp.route('', methods=['GET'])
@admin_or_it_manager_required
def get_all_users():
    """
    Get all users (admin or IT manager)

    Response:
        {
            "success": true,
            "users": [
                {
                    "sid": 100000001,
                    "email": "john@example.com",
                    "full_name": "John Doe",
                    "role": "manager",
                    "department_id": 1,
                    "department": {
                        "id": 1,
                        "code": "HR",
                        "name": "Human Resources"
                    },
                    "is_active": true,
                    "created_at": "2026-01-01T00:00:00Z",
                    "last_login": "2026-01-29T10:00:00Z"
                }
            ]
        }
    """
    result = auth_service.get_all_users()

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 500


@user_bp.route('/<int:sid>', methods=['GET'])
@admin_or_it_manager_required
def get_user(sid):
    """
    Get a specific user by SID (admin or IT manager)

    Response:
        {
            "success": true,
            "user": {
                "sid": 100000001,
                "email": "john@example.com",
                "full_name": "John Doe",
                "role": "manager",
                "department_id": 1,
                "department": {...},
                "is_active": true,
                "created_at": "...",
                "last_login": "..."
            }
        }
    """
    user = auth_service.get_user_by_sid(sid)

    if user:
        return jsonify({
            'success': True,
            'user': user
        }), 200

    return jsonify({
        'success': False,
        'error': 'User not found'
    }), 404


@user_bp.route('', methods=['POST'])
@admin_or_it_manager_required
def create_user():
    """
    Create a new user (admin or manager)

    Request Body:
        {
            "email": "newuser@company.com",
            "full_name": "Nguyen Van A",
            "password": "123456",
            "role": "staff",
            "department_id": 1,
            "is_active": true,
            "employee_sid": 300000001  # Optional - link to employee record
        }

    Response:
        {
            "success": true,
            "user": {...}
        }
    """
    # Who may create a user is now the decorator's business: admin, or a manager
    # in the IT department. It used to be ANY manager (ACC, HR, BOD included),
    # which did not match "the manager role is assigned by an admin or an IT
    # manager" — creating a user with role=manager is assigning it.
    data = request.get_json()

    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body is required'
        }), 400

    # Validate required fields
    required_fields = ['email', 'full_name', 'password', 'role']
    for field in required_fields:
        if field not in data or not data[field]:
            return jsonify({
                'success': False,
                'error': f'Missing required field: {field}'
            }), 400

    # Not "only an admin may create an admin" — no one may, through the API.
    # That rule still allowed a second admin, and admin bypasses every
    # permission check.
    denied = _reject_admin_role(data['role'])
    if denied:
        return denied

    result = auth_service.create_user(
        email=data['email'],
        password=data['password'],
        full_name=data['full_name'],
        role=data['role'],
        department_id=data.get('department_id'),
        employee_sid=data.get('employee_sid'),
        is_active=data.get('is_active', True)
    )

    if result['success']:
        return jsonify(result), 201
    return jsonify(result), 400


@user_bp.route('/<int:sid>', methods=['PUT'])
@admin_or_it_manager_required
def update_user(sid):
    """
    Update a user (admin or IT manager)

    Request Body (all fields optional):
        {
            "role": "admin" | "manager" | "staff",
            "department_id": 1,
            "is_active": true
        }

    Response:
        {
            "success": true,
            "user": {...}
        }
    """
    data = request.get_json()

    if not data:
        return jsonify({
            'success': False,
            'error': 'Request body is required'
        }), 400

    # Only allow updating specific fields
    allowed_updates = {}
    if 'role' in data:
        denied = _reject_admin_role(data['role'])
        if denied:
            return denied
        allowed_updates['role'] = data['role']
    if 'department_id' in data:
        allowed_updates['department_id'] = data['department_id']
    if 'is_active' in data:
        allowed_updates['is_active'] = data['is_active']
    if 'employee_sid' in data:
        allowed_updates['employee_sid'] = data['employee_sid']

    if not allowed_updates:
        return jsonify({
            'success': False,
            'error': 'No valid fields to update. Allowed: role, department_id, is_active, employee_sid'
        }), 400

    # The admin row itself: no role change, no deactivation. Either would leave
    # zero admins, and the role cannot be granted back through the API.
    denied = _protect_admin_row(sid, allowed_updates)
    if denied:
        return denied

    # `_protect_admin_row` fails open when the role cannot be read. Deactivation
    # is unrecoverable, so it gets a second check through a different path —
    # the same belt-and-braces `delete_user` already had.
    if 'is_active' in allowed_updates and not _is_truthy(allowed_updates['is_active']):
        target = auth_service.get_user_by_sid(sid)
        if target and str(target.get('role', '')).lower() == UserRole.ADMIN:
            return jsonify({
                'success': False,
                'error': 'The admin account cannot be deactivated through the API.',
            }), 403

    result = auth_service.update_user(sid, allowed_updates)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


@user_bp.route('/<int:sid>/reset-password', methods=['POST'])
@token_required
def reset_user_password(sid):
    """
    Reset a user's password to default (123456) and force password change on next login.
    Auth: Admin or Manager.
    """
    if g.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        return jsonify({'success': False, 'error': 'Only admin or manager can reset passwords'}), 403

    # Resetting the admin's password is a way to BECOME the admin: this sets a
    # known default, so any manager could reset it and then log in with it.
    # Every other door was locked (`_reject_admin_role`, `_protect_admin_row`)
    # and this one was left open, which made the rest of it decoration.
    # Only the admin may reset the admin — i.e. itself.
    if g.sid != sid and _target_is_admin(sid):
        return jsonify({
            'success': False,
            'error': "The admin account's password cannot be reset by another "
                     'user. Use the password-change flow as the admin, or reset '
                     'it directly in the database.',
        }), 403

    result = auth_service.reset_password(sid)

    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400


@user_bp.route('/<int:sid>', methods=['DELETE'])
@admin_or_it_manager_required
def delete_user(sid):
    """
    Delete a user

    Access Control:
        - Only admin can delete admin users
        - Users cannot delete themselves
        - Manager can delete staff and manager users

    Response:
        {
            "success": true
        }
    """
    # Users cannot delete themselves
    if g.sid == sid:
        return jsonify({
            'success': False,
            'error': 'Cannot delete yourself'
        }), 400

    # Nor delete the admin. The self-delete check above already stops the admin
    # deleting itself; this stops anyone ELSE doing it, which would leave the
    # system with no admin and no API way to appoint one.
    denied = _protect_admin_row(sid, None)
    if denied:
        return denied

    # Get the target user to check their role
    target_user = auth_service.get_user_by_sid(sid)
    if not target_user:
        return jsonify({
            'success': False,
            'error': 'User not found'
        }), 404

    # Only admin can delete admin users
    if target_user.get('role') == UserRole.ADMIN and g.role != UserRole.ADMIN:
        return jsonify({
            'success': False,
            'error': 'Only admin can delete admin users'
        }), 403

    # Manager can only delete staff and manager users. The decorator already
    # narrowed this to admin or an IT-department manager, matching create and
    # update — deleting a user is at least as consequential as creating one,
    # and it was reachable by any manager in any department.
    if g.role == UserRole.MANAGER:
        if target_user.get('role') not in [UserRole.STAFF, UserRole.MANAGER]:
            return jsonify({
                'success': False,
                'error': 'Manager can only delete staff or manager users'
            }), 403
    elif g.role != UserRole.ADMIN:
        return jsonify({
            'success': False,
            'error': 'Only admin or manager can delete users'
        }), 403

    result = auth_service.delete_user(sid)

    if result['success']:
        return jsonify({'success': True}), 200
    return jsonify(result), 400
