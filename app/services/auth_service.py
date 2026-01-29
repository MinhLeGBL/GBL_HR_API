"""
Authentication Service
Handles user authentication, JWT tokens, and role-based access control
"""
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from config.database import JWT_CONFIG, POSTGRES_CONFIG
from app.database.connection import get_postgres_connection


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

    # ==================== Database Schema ====================

    def init_database(self) -> Dict[str, Any]:
        """
        Initialize the users table in PostgreSQL
        Run this once to create the necessary tables
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()

            # Drop existing table if needed for schema changes (comment out in production)
            # cursor.execute('DROP TABLE IF EXISTS users')
            # cursor.execute('DROP SEQUENCE IF EXISTS user_sid_seq')

            # Create SID sequence starting at 100,000,001 (9 digits, guaranteed unique)
            cursor.execute('''
                CREATE SEQUENCE IF NOT EXISTS user_sid_seq
                START WITH 100000001
                INCREMENT BY 1
                NO MAXVALUE
                NO CYCLE
            ''')

            # Create users table with SID as primary key
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    sid BIGINT PRIMARY KEY,
                    email VARCHAR(100) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    full_name VARCHAR(100) NOT NULL,
                    role VARCHAR(20) NOT NULL DEFAULT 'staff',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP,
                    CONSTRAINT valid_role CHECK (role IN ('admin', 'manager', 'staff'))
                )
            ''')

            # Create index on email for faster lookups
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)
            ''')

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
                    is_active: bool = True) -> Dict[str, Any]:
        """Create a new user with auto-generated SID"""
        conn = None
        try:
            # Validate role
            if role not in [UserRole.ADMIN, UserRole.MANAGER, UserRole.STAFF]:
                return {'success': False, 'error': 'Invalid role'}

            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Check if email already exists
            cursor.execute('SELECT sid FROM users WHERE email = %s', (email,))
            if cursor.fetchone():
                return {'success': False, 'error': 'Email already exists'}

            # Validate department_id if provided
            if department_id:
                cursor.execute('SELECT id FROM departments WHERE id = %s', (department_id,))
                if not cursor.fetchone():
                    return {'success': False, 'error': 'Invalid department_id'}

            # Get next SID from sequence (guaranteed unique)
            sid = self.get_next_sid(cursor)

            # Hash password and create user
            password_hash = self.hash_password(password)

            cursor.execute('''
                INSERT INTO users (sid, email, password_hash, full_name, role, department_id, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING sid, email, full_name, role, is_active, created_at, department_id
            ''', (sid, email, password_hash, full_name, role, department_id, is_active))

            user = cursor.fetchone()

            # Get department info if department_id exists
            department = None
            if user[6]:
                cursor.execute('SELECT id, code, name FROM departments WHERE id = %s', (user[6],))
                dept_row = cursor.fetchone()
                if dept_row:
                    department = {'id': dept_row[0], 'code': dept_row[1], 'name': dept_row[2]}

            conn.commit()
            cursor.close()

            return {
                'success': True,
                'user': {
                    'sid': user[0],
                    'email': user[1],
                    'full_name': user[2],
                    'role': user[3],
                    'is_active': user[4],
                    'created_at': user[5].isoformat() if user[5] else None,
                    'department_id': user[6],
                    'department': department
                }
            }

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

            # Get user by email with department
            cursor.execute('''
                SELECT u.sid, u.email, u.password_hash, u.full_name, u.role, u.is_active,
                       u.department_id, d.id as dept_id, d.code as dept_code, d.name as dept_name
                FROM users u
                LEFT JOIN departments d ON u.department_id = d.id
                WHERE u.email = %s
            ''', (email,))

            user = cursor.fetchone()

            if not user:
                return {'success': False, 'error': 'Invalid email or password'}

            sid, email, password_hash, full_name, role, is_active, department_id, dept_id, dept_code, dept_name = user

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

            # Generate tokens
            access_token = self.create_access_token(sid, email, role)
            refresh_token = self.create_refresh_token(sid)

            # Build department object
            department = None
            if dept_id:
                department = {'id': dept_id, 'code': dept_code, 'name': dept_name}

            # Get permissions
            from app.services.permission_service import PermissionService
            permission_service = PermissionService()
            permissions = permission_service.get_user_permissions(role, department_id)

            return {
                'success': True,
                'access_token': access_token,
                'refresh_token': refresh_token,
                'token_type': 'bearer',
                'user': {
                    'sid': sid,
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
                SELECT sid, email, role, is_active FROM users WHERE sid = %s
            ''', (sid,))

            user = cursor.fetchone()
            cursor.close()

            if not user:
                return {'success': False, 'error': 'User not found'}

            sid, email, role, is_active = user

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

    def get_user_by_sid(self, sid: int) -> Optional[Dict[str, Any]]:
        """Get user by SID with department info"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return None

            cursor = conn.cursor()
            cursor.execute('''
                SELECT u.sid, u.email, u.full_name, u.role, u.is_active, u.created_at, u.last_login,
                       u.department_id, d.id as dept_id, d.code as dept_code, d.name as dept_name
                FROM users u
                LEFT JOIN departments d ON u.department_id = d.id
                WHERE u.sid = %s
            ''', (sid,))

            user = cursor.fetchone()
            cursor.close()

            if not user:
                return None

            # Build department object
            department = None
            if user[8]:
                department = {'id': user[8], 'code': user[9], 'name': user[10]}

            return {
                'sid': user[0],
                'email': user[1],
                'full_name': user[2],
                'role': user[3],
                'is_active': user[4],
                'created_at': user[5].isoformat() if user[5] else None,
                'last_login': user[6].isoformat() if user[6] else None,
                'department_id': user[7],
                'department': department
            }

        except Exception as e:
            print(f"Error getting user: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def get_all_users(self) -> Dict[str, Any]:
        """Get all users with department info (admin only)"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()
            cursor.execute('''
                SELECT u.sid, u.email, u.full_name, u.role, u.is_active, u.created_at, u.last_login,
                       u.department_id, d.id as dept_id, d.code as dept_code, d.name as dept_name
                FROM users u
                LEFT JOIN departments d ON u.department_id = d.id
                ORDER BY u.created_at DESC
            ''')

            users = cursor.fetchall()
            cursor.close()

            user_list = []
            for user in users:
                department = None
                if user[8]:
                    department = {'id': user[8], 'code': user[9], 'name': user[10]}

                user_list.append({
                    'sid': user[0],
                    'email': user[1],
                    'full_name': user[2],
                    'role': user[3],
                    'is_active': user[4],
                    'created_at': user[5].isoformat() if user[5] else None,
                    'last_login': user[6].isoformat() if user[6] else None,
                    'department_id': user[7],
                    'department': department
                })

            return {'success': True, 'users': user_list}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def update_user(self, sid: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update user details"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Build update query dynamically
            allowed_fields = ['email', 'full_name', 'role', 'is_active', 'department_id']
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
