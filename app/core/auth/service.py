"""
Authentication Service
Handles user authentication, JWT tokens, and role-based access control
"""
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from config.database import JWT_CONFIG, POSTGRES_CONFIG
from app.core.database.connection import get_postgres_connection


# User roles
class UserRole:
    ADMIN = 'admin'
    MANAGER = 'manager'
    STAFF = 'staff'


# SID Configuration - 9 digits starting from 100,000,000
SID_START = 100000000  # First SID will be 100000001


class AuthService:
    """Service for handling authentication and user management"""

    def __init__(self):
        self.secret_key = JWT_CONFIG['secret_key']
        self.algorithm = JWT_CONFIG['algorithm']
        self.access_token_expire_minutes = JWT_CONFIG['access_token_expire_minutes']
        self.refresh_token_expire_days = JWT_CONFIG['refresh_token_expire_days']

    # ==================== SID Generation ====================

    def get_next_sid(self, cursor) -> int:
        """Get next SID from PostgreSQL sequence (guaranteed unique)"""
        cursor.execute("SELECT nextval('user_sid_seq')")
        return cursor.fetchone()[0]

    # ==================== Lookup Helpers ====================

    def _resolve_role_id(self, cursor, code: str) -> Optional[int]:
        """Resolve role code to ID"""
        cursor.execute('SELECT id FROM roles WHERE code = %s', (code.upper(),))
        row = cursor.fetchone()
        return row[0] if row else None

    # ==================== Database Schema ====================

    def init_database(self) -> Dict[str, Any]:
        """
        Initialize roles and users tables in PostgreSQL.
        Requires: departments and employees tables must exist first.
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()

            # ── Lookup: Roles (5-digit IDs, prefix 3) ──
            cursor.execute('''
                CREATE SEQUENCE IF NOT EXISTS role_id_seq
                START WITH 30001
                INCREMENT BY 1
                NO MAXVALUE
                NO CYCLE
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS roles (
                    id INTEGER PRIMARY KEY DEFAULT nextval('role_id_seq'),
                    code VARCHAR(20) UNIQUE NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE
                )
            ''')
            cursor.execute('SELECT COUNT(*) FROM roles')
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    INSERT INTO roles (code, name) VALUES
                    ('ADMIN', 'Admin'),
                    ('MANAGER', 'Manager'),
                    ('STAFF', 'Staff')
                ''')

            # ── User SIDs: 9-digit starting with 1 (100000001+) ──
            cursor.execute('''
                CREATE SEQUENCE IF NOT EXISTS user_sid_seq
                START WITH 100000001
                INCREMENT BY 1
                NO MAXVALUE
                NO CYCLE
            ''')

            # ── Users table ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    sid BIGINT PRIMARY KEY,
                    email VARCHAR(100) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    full_name VARCHAR(100) NOT NULL,
                    role_id INTEGER NOT NULL REFERENCES roles(id),
                    department_id BIGINT REFERENCES departments(id),
                    employee_sid BIGINT REFERENCES employees(sid),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP
                )
            ''')

            cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)')

            # ── Seed default admin user from employee MQ027 ──
            cursor.execute('SELECT COUNT(*) FROM users')
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    SELECT sid, employee_code, full_name, email, department_id
                    FROM employees WHERE employee_code = %s
                ''', ('MQ027',))
                emp = cursor.fetchone()
                if emp:
                    emp_sid, emp_code, emp_name, emp_email, emp_dept_id = emp

                    admin_role_id = self._resolve_role_id(cursor, 'ADMIN')
                    user_sid = self.get_next_sid(cursor)
                    password_hash = self.hash_password('##*OCobc1bmV%&dc')

                    cursor.execute('''
                        INSERT INTO users (sid, email, password_hash, full_name, role_id,
                                           department_id, employee_sid, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (user_sid, emp_email, password_hash, emp_name, admin_role_id,
                          emp_dept_id, emp_sid, True))

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'Database initialized successfully'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    # ==================== Password Hashing ====================

    def hash_password(self, password: str) -> str:
        """Hash a password using bcrypt"""
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')

    def verify_password(self, password: str, password_hash: str) -> bool:
        """Verify a password against its hash"""
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

    # ==================== JWT Token Management ====================

    def create_access_token(self, sid: int, email: str, role: str) -> str:
        """Create a JWT access token"""
        expire = datetime.now(timezone.utc) + timedelta(minutes=self.access_token_expire_minutes)
        payload = {
            'sid': sid,
            'email': email,
            'role': role,
            'exp': expire,
            'type': 'access'
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def create_refresh_token(self, sid: int) -> str:
        """Create a JWT refresh token"""
        expire = datetime.now(timezone.utc) + timedelta(days=self.refresh_token_expire_days)
        payload = {
            'sid': sid,
            'exp': expire,
            'type': 'refresh'
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify and decode a JWT token"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    # ==================== User Management ====================

    def create_user(self, email: str, password: str,
                    full_name: str, role: str = UserRole.STAFF,
                    department_id: int = None,
                    employee_sid: int = None,
                    is_active: bool = True) -> Dict[str, Any]:
        """Create a new user. Accepts role as code string (e.g. 'ADMIN'), resolves to FK."""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Resolve role code to FK ID
            role_id = self._resolve_role_id(cursor, role)
            if not role_id:
                return {'success': False, 'error': f'Invalid role: {role}'}

            # Check if email already exists
            cursor.execute('SELECT sid FROM users WHERE email = %s', (email,))
            if cursor.fetchone():
                return {'success': False, 'error': 'Email already exists'}

            # Validate department_id if provided
            if department_id:
                cursor.execute('SELECT id FROM departments WHERE id = %s', (department_id,))
                if not cursor.fetchone():
                    return {'success': False, 'error': 'Invalid department_id'}

            # Validate employee_sid if provided
            if employee_sid:
                cursor.execute('SELECT sid FROM employees WHERE sid = %s', (employee_sid,))
                if not cursor.fetchone():
                    return {'success': False, 'error': 'Invalid employee_sid - employee not found'}

            # Get next SID from sequence (guaranteed unique)
            sid = self.get_next_sid(cursor)

            # Hash password and create user
            password_hash = self.hash_password(password)

            cursor.execute('''
                INSERT INTO users (sid, email, password_hash, full_name, role_id,
                                   department_id, employee_sid, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (sid, email, password_hash, full_name, role_id,
                  department_id, employee_sid, is_active))

            conn.commit()

            # Fetch full user with JOINs
            return self.get_user_by_sid_result(cursor, sid)

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def authenticate(self, email: str, password: str) -> Dict[str, Any]:
        """Authenticate a user by email and return tokens with department and permissions"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Get user by email with role, department and employee
            cursor.execute('''
                SELECT u.sid, u.email, u.password_hash, u.full_name, r.code AS role,
                       u.is_active, u.department_id,
                       d.id AS dept_id, d.code AS dept_code, d.name AS dept_name,
                       u.employee_sid, e.employee_code, e.join_date
                FROM users u
                JOIN roles r ON u.role_id = r.id
                LEFT JOIN departments d ON u.department_id = d.id
                LEFT JOIN employees e ON u.employee_sid = e.sid
                WHERE u.email = %s
            ''', (email,))

            user = cursor.fetchone()

            if not user:
                return {'success': False, 'error': 'Invalid email or password'}

            sid, email, password_hash, full_name, role, is_active, department_id, dept_id, dept_code, dept_name, employee_sid, employee_code, join_date = user

            # Check if user is active
            if not is_active:
                return {'success': False, 'error': 'Account is deactivated'}

            # Verify password
            if not self.verify_password(password, password_hash):
                return {'success': False, 'error': 'Invalid email or password'}

            # Update last login
            cursor.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE sid = %s',
                          (sid,))
            conn.commit()
            cursor.close()

            # role code is uppercase from DB, lowercase for API compatibility
            role = role.lower()

            # Generate tokens
            access_token = self.create_access_token(sid, email, role)
            refresh_token = self.create_refresh_token(sid)

            # Build department object
            department = None
            if dept_id:
                department = {'id': dept_id, 'code': dept_code, 'name': dept_name}

            # Get permissions
            from app.modules.permissions.service import PermissionService
            permission_service = PermissionService()
            permissions = permission_service.get_user_permissions(role, department_id)

            return {
                'success': True,
                'access_token': access_token,
                'refresh_token': refresh_token,
                'token_type': 'bearer',
                'user': {
                    'sid': sid,
                    'employee_sid': employee_sid,
                    'employee_code': employee_code,
                    'join_date': join_date.isoformat() if join_date else None,
                    'email': email,
                    'full_name': full_name,
                    'role': role,
                    'department_id': department_id,
                    'department': department,
                    'is_active': is_active
                },
                'permissions': permissions
            }

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Generate a new access token using a refresh token"""
        payload = self.verify_token(refresh_token)

        if not payload:
            return {'success': False, 'error': 'Invalid or expired refresh token'}

        if payload.get('type') != 'refresh':
            return {'success': False, 'error': 'Invalid token type'}

        sid = payload.get('sid')

        # Get user details
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()
            cursor.execute('''
                SELECT u.sid, u.email, r.code AS role, u.is_active
                FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE u.sid = %s
            ''', (sid,))

            user = cursor.fetchone()
            cursor.close()

            if not user:
                return {'success': False, 'error': 'User not found'}

            sid, email, role, is_active = user
            role = role.lower()

            if not is_active:
                return {'success': False, 'error': 'Account is deactivated'}

            # Generate new access token
            access_token = self.create_access_token(sid, email, role)

            return {
                'success': True,
                'access_token': access_token,
                'token_type': 'bearer'
            }

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    _USER_SELECT = '''
        SELECT u.sid, u.email, u.full_name, r.code AS role, u.is_active,
               u.created_at, u.last_login,
               u.department_id, d.id AS dept_id, d.code AS dept_code, d.name AS dept_name,
               u.employee_sid, e.employee_code, e.join_date
        FROM users u
        JOIN roles r ON u.role_id = r.id
        LEFT JOIN departments d ON u.department_id = d.id
        LEFT JOIN employees e ON u.employee_sid = e.sid
    '''

    def _build_user_dict(self, user) -> Dict[str, Any]:
        """Build user dict from a JOIN query row"""
        department = None
        if user[8]:
            department = {'id': user[8], 'code': user[9], 'name': user[10]}

        return {
            'sid': user[0],
            'email': user[1],
            'full_name': user[2],
            'role': user[3].lower(),
            'is_active': user[4],
            'created_at': user[5].isoformat() if user[5] else None,
            'last_login': user[6].isoformat() if user[6] else None,
            'department_id': user[7],
            'department': department,
            'employee_sid': user[11],
            'employee_code': user[12],
            'join_date': user[13].isoformat() if user[13] else None
        }

    def get_user_by_sid_result(self, cursor, sid: int) -> Dict[str, Any]:
        """Get user by SID using an existing cursor, returns success dict"""
        cursor.execute(self._USER_SELECT + ' WHERE u.sid = %s', (sid,))
        user = cursor.fetchone()
        if not user:
            return {'success': False, 'error': 'User not found'}
        return {'success': True, 'user': self._build_user_dict(user)}

    def get_user_by_sid(self, sid: int) -> Optional[Dict[str, Any]]:
        """Get user by SID with role, department and employee info"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return None

            cursor = conn.cursor()
            cursor.execute(self._USER_SELECT + ' WHERE u.sid = %s', (sid,))

            user = cursor.fetchone()
            cursor.close()

            if not user:
                return None

            return self._build_user_dict(user)

        except Exception as e:
            print(f"Error getting user: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def get_all_users(self) -> Dict[str, Any]:
        """Get all users with role, department and employee info"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()
            cursor.execute(self._USER_SELECT + ' ORDER BY u.created_at DESC')

            users = cursor.fetchall()
            cursor.close()

            user_list = [self._build_user_dict(user) for user in users]

            return {'success': True, 'users': user_list}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def update_user(self, sid: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update user details. Accepts role as code string, resolves to FK."""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Resolve role code to FK ID if provided
            if 'role' in updates:
                role_id = self._resolve_role_id(cursor, updates['role'])
                if not role_id:
                    return {'success': False, 'error': f"Invalid role: {updates['role']}"}
                updates['role_id'] = role_id
                del updates['role']

            # Build update query dynamically
            allowed_fields = ['email', 'full_name', 'role_id', 'is_active', 'department_id', 'employee_sid']
            update_parts = []
            values = []

            for field in allowed_fields:
                if field in updates:
                    update_parts.append(f"{field} = %s")
                    values.append(updates[field])

            if not update_parts:
                return {'success': False, 'error': 'No valid fields to update'}

            # Add updated_at
            update_parts.append("updated_at = CURRENT_TIMESTAMP")
            values.append(sid)

            query = f"UPDATE users SET {', '.join(update_parts)} WHERE sid = %s RETURNING sid"
            cursor.execute(query, values)

            result = cursor.fetchone()
            if not result:
                return {'success': False, 'error': 'User not found'}

            conn.commit()
            cursor.close()

            # Get updated user
            user = self.get_user_by_sid(sid)

            return {'success': True, 'user': user}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def change_password(self, sid: int, old_password: str, new_password: str) -> Dict[str, Any]:
        """Change user password"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Get current password hash
            cursor.execute('SELECT password_hash FROM users WHERE sid = %s', (sid,))
            result = cursor.fetchone()

            if not result:
                return {'success': False, 'error': 'User not found'}

            current_hash = result[0]

            # Verify old password
            if not self.verify_password(old_password, current_hash):
                return {'success': False, 'error': 'Current password is incorrect'}

            # Hash new password and update
            new_hash = self.hash_password(new_password)
            cursor.execute('''
                UPDATE users SET password_hash = %s, updated_at = CURRENT_TIMESTAMP
                WHERE sid = %s
            ''', (new_hash, sid))

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'Password changed successfully'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    # ==================== Lookup Endpoints ====================

    def get_roles(self) -> Dict[str, Any]:
        """Get all roles"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()
            cursor.execute('SELECT id, code, name, is_active FROM roles ORDER BY id')
            roles = [{'id': r[0], 'code': r[1], 'name': r[2], 'is_active': r[3]} for r in cursor.fetchall()]
            cursor.close()
            return {'success': True, 'roles': roles}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def delete_user(self, sid: int) -> Dict[str, Any]:
        """Delete a user (admin only)"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            cursor.execute('DELETE FROM users WHERE sid = %s RETURNING sid', (sid,))
            result = cursor.fetchone()

            if not result:
                return {'success': False, 'error': 'User not found'}

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'User deleted successfully'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()
