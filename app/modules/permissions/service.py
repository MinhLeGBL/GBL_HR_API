"""
Permission Service
Handles departments, sections, and permission management

Permission Model (v2 — Flat Sections):
  Per-section access control with 4 dimensions:
    - Departments: which departments can access
    - Roles: which roles can access
    - Inclusions: specific users granted access regardless of dept/role
    - Exclusions: specific users denied access even if dept/role allows
  Admin bypasses all checks. DASHBOARD accessible to all authenticated users.
"""
from typing import Optional, Dict, Any, List
from app.core.database.connection import get_postgres_connection


class PermissionService:
    """Service for handling departments and permission management"""

    # ==================== Database Schema ====================

    def init_permission_tables(self) -> Dict[str, Any]:
        """
        Initialize permission tables in dependency order:
        1. departments
        2. sections
        3. section_departments, section_roles, section_inclusions, section_exclusions_v2
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()

            # ── 1. Departments (9-digit IDs, prefix 4) ──
            cursor.execute('''
                CREATE SEQUENCE IF NOT EXISTS department_id_seq
                START WITH 400000001
                INCREMENT BY 1
                NO MAXVALUE
                NO CYCLE
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS departments (
                    id BIGINT PRIMARY KEY DEFAULT nextval('department_id_seq'),
                    code VARCHAR(20) UNIQUE NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    description TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # ── 2. Sections ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sections (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(50) UNIQUE NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    description TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # ── 3. Per-section department access ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS section_departments (
                    section_code VARCHAR(50) REFERENCES sections(code),
                    department_code VARCHAR(10) REFERENCES departments(code),
                    PRIMARY KEY (section_code, department_code)
                )
            ''')

            # ── 4. Per-section role access ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS section_roles (
                    section_code VARCHAR(50) REFERENCES sections(code),
                    role_code VARCHAR(20) REFERENCES roles(code),
                    PRIMARY KEY (section_code, role_code)
                )
            ''')

            # ── 5. Per-section user inclusions ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS section_inclusions (
                    section_code VARCHAR(50) REFERENCES sections(code),
                    user_sid BIGINT REFERENCES users(sid),
                    PRIMARY KEY (section_code, user_sid)
                )
            ''')

            # ── 6. Per-section user exclusions ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS section_exclusions_v2 (
                    section_code VARCHAR(50) REFERENCES sections(code),
                    user_sid BIGINT REFERENCES users(sid),
                    PRIMARY KEY (section_code, user_sid)
                )
            ''')

            # ── Seed data ──

            # Departments
            cursor.execute('SELECT COUNT(*) FROM departments')
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    INSERT INTO departments (code, name, description) VALUES
                    ('HR', 'Human Resources', 'Human Resources Department'),
                    ('ACC', 'Accounting', 'Accounting Department'),
                    ('IT', 'Information Technology', 'IT Department'),
                    ('MKT', 'Marketing', 'Marketing Department'),
                    ('LOG', 'Logistic', 'Logistic Department'),
                    ('BOD', 'Management Board', 'Management Board')
                ''')

            # Sections (additive — ON CONFLICT so new sections are inserted in existing environments)
            cursor.execute('''
                INSERT INTO sections (code, name, description) VALUES
                ('DASHBOARD', 'Dashboard', 'Main dashboard'),
                ('COMMISSION_DATA', 'Commission Data', 'Commission data management'),
                ('USER_MANAGEMENT', 'User Management', 'User administration'),
                ('EMPLOYEE_DATA', 'Employee Data', 'Employee information'),
                ('ACCESS_MANAGEMENT', 'Access Management', 'Permission and access control'),
                ('BATHROOM_PRICE_CHECK', 'Bathroom Price Check', 'Bathroom product catalog and pricing'),
                ('CRM', 'CRM', 'Customer relationship management and RFM analytics'),
                ('ACCOUNT_PAYABLE', 'Account Payable', 'AR payable reconciliation tool (CR #60)'),
                ('HAND_CARRY', 'Hand Carry', 'Hand-carry UPC catalog under Commission (CR #57)')
                ON CONFLICT (code) DO NOTHING
            ''')

            # Section permissions seed (additive — ON CONFLICT in _seed_v2_permissions)
            self._seed_v2_permissions(cursor)

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'Permission tables initialized successfully'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def _seed_v2_permissions(self, cursor):
        """Seed v2 flat permission tables with default access config."""
        # Section → departments mapping
        section_depts = {
            'COMMISSION_DATA': ['HR'],
            'USER_MANAGEMENT': ['IT', 'HR'],
            'EMPLOYEE_DATA': ['HR'],
            'ACCESS_MANAGEMENT': ['IT'],
            'BATHROOM_PRICE_CHECK': [],
            'CRM': [],
            'ACCOUNT_PAYABLE': ['HR', 'ACC'],
            'HAND_CARRY': ['HR'],
        }
        for section_code, depts in section_depts.items():
            for dept in depts:
                cursor.execute(
                    'INSERT INTO section_departments (section_code, department_code) VALUES (%s, %s) ON CONFLICT DO NOTHING',
                    (section_code, dept)
                )

        # Section → roles mapping
        section_roles = {
            'COMMISSION_DATA': ['ADMIN', 'MANAGER', 'STAFF'],
            'USER_MANAGEMENT': ['ADMIN'],
            'EMPLOYEE_DATA': ['ADMIN', 'MANAGER'],
            'ACCESS_MANAGEMENT': ['ADMIN', 'MANAGER'],
            'BATHROOM_PRICE_CHECK': [],
            'CRM': [],
            'ACCOUNT_PAYABLE': ['ADMIN', 'MANAGER'],
            'HAND_CARRY': ['ADMIN', 'MANAGER', 'STAFF'],
        }
        for section_code, roles in section_roles.items():
            for role in roles:
                cursor.execute(
                    'INSERT INTO section_roles (section_code, role_code) VALUES (%s, %s) ON CONFLICT DO NOTHING',
                    (section_code, role)
                )

    # ==================== Department Management ====================

    def get_all_departments(self) -> Dict[str, Any]:
        """Get all departments"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, code, name, description, is_active, created_at
                FROM departments ORDER BY name
            ''')

            departments = []
            for row in cursor.fetchall():
                departments.append({
                    'id': row[0],
                    'code': row[1],
                    'name': row[2],
                    'description': row[3],
                    'is_active': row[4],
                    'created_at': row[5].isoformat() if row[5] else None
                })

            cursor.close()
            return {'success': True, 'departments': departments}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def get_department_by_id(self, dept_id: int) -> Optional[Dict[str, Any]]:
        """Get department by ID"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return None

            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, code, name, description, is_active
                FROM departments WHERE id = %s
            ''', (dept_id,))

            row = cursor.fetchone()
            cursor.close()

            if not row:
                return None

            return {
                'id': row[0],
                'code': row[1],
                'name': row[2],
                'description': row[3],
                'is_active': row[4]
            }

        except Exception as e:
            print(f"Error getting department: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def create_department(self, code: str, name: str, description: str = None) -> Dict[str, Any]:
        """Create a new department"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO departments (code, name, description)
                VALUES (%s, %s, %s)
                RETURNING id, code, name, description, is_active
            ''', (code.upper(), name, description))

            row = cursor.fetchone()
            conn.commit()
            cursor.close()

            return {
                'success': True,
                'department': {
                    'id': row[0],
                    'code': row[1],
                    'name': row[2],
                    'description': row[3],
                    'is_active': row[4]
                }
            }

        except Exception as e:
            if conn:
                conn.rollback()
            if 'duplicate key' in str(e).lower():
                return {'success': False, 'error': 'Department code already exists'}
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def update_department(self, dept_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update department"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            allowed_fields = ['code', 'name', 'description', 'is_active']
            update_parts = []
            values = []

            for field in allowed_fields:
                if field in updates:
                    value = updates[field].upper() if field == 'code' else updates[field]
                    update_parts.append(f"{field} = %s")
                    values.append(value)

            if not update_parts:
                return {'success': False, 'error': 'No valid fields to update'}

            update_parts.append("updated_at = CURRENT_TIMESTAMP")
            values.append(dept_id)

            query = f"UPDATE departments SET {', '.join(update_parts)} WHERE id = %s RETURNING id"
            cursor.execute(query, values)

            result = cursor.fetchone()
            if not result:
                return {'success': False, 'error': 'Department not found'}

            conn.commit()
            cursor.close()

            department = self.get_department_by_id(dept_id)
            return {'success': True, 'department': department}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def delete_department(self, dept_id: int) -> Dict[str, Any]:
        """Delete a department"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Check if department has users
            cursor.execute('SELECT COUNT(*) FROM users WHERE department_id = %s', (dept_id,))
            if cursor.fetchone()[0] > 0:
                return {'success': False, 'error': 'Cannot delete department with assigned users'}

            cursor.execute('DELETE FROM departments WHERE id = %s RETURNING id', (dept_id,))
            result = cursor.fetchone()

            if not result:
                return {'success': False, 'error': 'Department not found'}

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'Department deleted successfully'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    # ==================== V2 Flat Permission Model ====================

    def get_all_section_permissions(self) -> Dict[str, Any]:
        """
        Get all sections with their full access config (v2 flat model).
        Excludes DASHBOARD (accessible to all, no permission config).
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Get all active sections except DASHBOARD
            cursor.execute('''
                SELECT code, name FROM sections
                WHERE is_active = TRUE AND code != 'DASHBOARD'
                ORDER BY id
            ''')
            sections_rows = cursor.fetchall()

            sections = []
            for section_code, section_name in sections_rows:
                # Allowed departments
                cursor.execute('''
                    SELECT department_code FROM section_departments
                    WHERE section_code = %s ORDER BY department_code
                ''', (section_code,))
                allowed_departments = [r[0] for r in cursor.fetchall()]

                # Allowed roles
                cursor.execute('''
                    SELECT role_code FROM section_roles
                    WHERE section_code = %s ORDER BY role_code
                ''', (section_code,))
                allowed_roles = [r[0] for r in cursor.fetchall()]

                # Included users
                cursor.execute('''
                    SELECT u.sid, u.full_name, e.employee_code
                    FROM section_inclusions si
                    JOIN users u ON si.user_sid = u.sid
                    LEFT JOIN employees e ON u.employee_sid = e.sid
                    WHERE si.section_code = %s
                    ORDER BY u.full_name
                ''', (section_code,))
                included_users = [
                    {'sid': r[0], 'full_name': r[1], 'employee_code': r[2]}
                    for r in cursor.fetchall()
                ]

                # Excluded users
                cursor.execute('''
                    SELECT u.sid, u.full_name, e.employee_code
                    FROM section_exclusions_v2 sev
                    JOIN users u ON sev.user_sid = u.sid
                    LEFT JOIN employees e ON u.employee_sid = e.sid
                    WHERE sev.section_code = %s
                    ORDER BY u.full_name
                ''', (section_code,))
                excluded_users = [
                    {'sid': r[0], 'full_name': r[1], 'employee_code': r[2]}
                    for r in cursor.fetchall()
                ]

                sections.append({
                    'section_code': section_code,
                    'section_name': section_name,
                    'allowed_departments': allowed_departments,
                    'allowed_roles': allowed_roles,
                    'included_users': included_users,
                    'excluded_users': excluded_users,
                })

            cursor.close()
            return {'success': True, 'sections': sections}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def update_section_access(self, section_code: str, allowed_departments: List[str],
                              allowed_roles: List[str], included_user_sids: List[int],
                              excluded_user_sids: List[int]) -> Dict[str, Any]:
        """
        Replace the full access config for a section (v2 flat model).
        All 4 arrays are overwritten.
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Verify section exists and is not DASHBOARD
            cursor.execute('SELECT code FROM sections WHERE code = %s', (section_code,))
            if not cursor.fetchone():
                return {'success': False, 'error': 'Section not found'}

            # Validate departments
            if allowed_departments:
                cursor.execute(
                    'SELECT code FROM departments WHERE code = ANY(%s)',
                    (allowed_departments,)
                )
                valid_depts = {r[0] for r in cursor.fetchall()}
                invalid = set(allowed_departments) - valid_depts
                if invalid:
                    return {'success': False, 'error': f'Invalid departments: {", ".join(invalid)}'}

            # Validate roles
            if allowed_roles:
                cursor.execute(
                    'SELECT code FROM roles WHERE code = ANY(%s)',
                    (allowed_roles,)
                )
                valid_roles = {r[0] for r in cursor.fetchall()}
                invalid = set(allowed_roles) - valid_roles
                if invalid:
                    return {'success': False, 'error': f'Invalid roles: {", ".join(invalid)}'}

            # Validate user SIDs
            all_sids = list(set(included_user_sids + excluded_user_sids))
            if all_sids:
                cursor.execute(
                    'SELECT sid FROM users WHERE sid = ANY(%s)',
                    (all_sids,)
                )
                valid_sids = {r[0] for r in cursor.fetchall()}
                invalid_included = set(included_user_sids) - valid_sids
                invalid_excluded = set(excluded_user_sids) - valid_sids
                if invalid_included:
                    return {'success': False, 'error': f'Invalid included user SIDs: {list(invalid_included)}'}
                if invalid_excluded:
                    return {'success': False, 'error': f'Invalid excluded user SIDs: {list(invalid_excluded)}'}

            # Replace departments
            cursor.execute('DELETE FROM section_departments WHERE section_code = %s', (section_code,))
            for dept in allowed_departments:
                cursor.execute(
                    'INSERT INTO section_departments (section_code, department_code) VALUES (%s, %s)',
                    (section_code, dept)
                )

            # Replace roles
            cursor.execute('DELETE FROM section_roles WHERE section_code = %s', (section_code,))
            for role in allowed_roles:
                cursor.execute(
                    'INSERT INTO section_roles (section_code, role_code) VALUES (%s, %s)',
                    (section_code, role)
                )

            # Replace inclusions
            cursor.execute('DELETE FROM section_inclusions WHERE section_code = %s', (section_code,))
            for sid in included_user_sids:
                cursor.execute(
                    'INSERT INTO section_inclusions (section_code, user_sid) VALUES (%s, %s)',
                    (section_code, sid)
                )

            # Replace exclusions
            cursor.execute('DELETE FROM section_exclusions_v2 WHERE section_code = %s', (section_code,))
            for sid in excluded_user_sids:
                cursor.execute(
                    'INSERT INTO section_exclusions_v2 (section_code, user_sid) VALUES (%s, %s)',
                    (section_code, sid)
                )

            conn.commit()
            cursor.close()
            return {'success': True}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def get_user_permissions_v2(self, user_sid: int, user_role: str,
                                department_code: str = None) -> List[Dict[str, Any]]:
        """
        Get accessible sections for a user (v2 flat model).

        Resolution order:
        1. Admin → all sections
        2. DASHBOARD → always included
        3. Excluded via section_exclusions_v2 → denied
        4. Included via section_inclusions → allowed
        5. Department in section_departments AND role in section_roles → allowed
        6. Otherwise → denied
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return []

            cursor = conn.cursor()

            # Get all active sections
            cursor.execute('''
                SELECT code, name FROM sections
                WHERE is_active = TRUE ORDER BY id
            ''')
            all_sections = cursor.fetchall()

            if user_role == 'admin':
                cursor.close()
                return [
                    {'section_code': code, 'section_name': name}
                    for code, name in all_sections
                ]

            # Build sets for efficient lookup
            # Excluded sections for this user
            cursor.execute(
                'SELECT section_code FROM section_exclusions_v2 WHERE user_sid = %s',
                (user_sid,)
            )
            excluded = {r[0] for r in cursor.fetchall()}

            # Included sections for this user
            cursor.execute(
                'SELECT section_code FROM section_inclusions WHERE user_sid = %s',
                (user_sid,)
            )
            included = {r[0] for r in cursor.fetchall()}

            # Sections where user's department is allowed
            dept_sections = set()
            if department_code:
                cursor.execute(
                    'SELECT section_code FROM section_departments WHERE department_code = %s',
                    (department_code,)
                )
                dept_sections = {r[0] for r in cursor.fetchall()}

            # Sections where user's role is allowed
            cursor.execute(
                'SELECT section_code FROM section_roles WHERE role_code = %s',
                (user_role.upper(),)
            )
            role_sections = {r[0] for r in cursor.fetchall()}

            result = []
            for code, name in all_sections:
                # DASHBOARD always included
                if code == 'DASHBOARD':
                    result.append({'section_code': code, 'section_name': name})
                    continue

                # Exclusion check
                if code in excluded:
                    continue

                # Inclusion check
                if code in included:
                    result.append({'section_code': code, 'section_name': name})
                    continue

                # Department + role check
                if code in dept_sections and code in role_sections:
                    result.append({'section_code': code, 'section_name': name})

            cursor.close()
            return result

        except Exception as e:
            print(f"Error getting user permissions v2: {e}")
            return []
        finally:
            if conn:
                conn.close()
