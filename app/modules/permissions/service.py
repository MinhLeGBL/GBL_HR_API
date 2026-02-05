"""
Permission Service
Handles departments, section groups, sections, and permission management

Permission Model (Two Layers):
  Layer 1: Section Group Access (department-based)
    - Controls which departments can see a sidebar group (MAIN, COMMISSION, REPORTS)
    - admin role bypasses this
  Layer 2: Item-Level Access (role-based, within a group)
    - Controls which roles can view/access specific section items
    - admin role bypasses this
"""
from typing import Optional, Dict, Any, List
from app.core.database.connection import get_postgres_connection


class PermissionService:
    """Service for handling departments and permission management"""

    # ==================== Database Schema ====================

    def init_permission_tables(self) -> Dict[str, Any]:
        """
        Initialize all permission tables in dependency order:
        1. departments
        2. section_groups
        3. sections (FK → section_groups)
        4. section_group_permissions (FK → section_groups)
        5. section_permissions (FK → sections)
        6. section_department_access (FK → sections, departments)
        7. section_exclusions (FK → sections, departments, employees)
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

            # ── 2. Section Groups ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS section_groups (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(50) UNIQUE NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # ── 3. Sections (FK → section_groups) ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sections (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(50) UNIQUE NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    description TEXT,
                    group_id INTEGER REFERENCES section_groups(id),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # ── 4. Section Group Permissions (department-based group access) ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS section_group_permissions (
                    id SERIAL PRIMARY KEY,
                    group_id INTEGER REFERENCES section_groups(id) ON DELETE CASCADE,
                    department_code VARCHAR(20) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(group_id, department_code)
                )
            ''')

            # ── 5. Section Permissions (role-based item access) ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS section_permissions (
                    id SERIAL PRIMARY KEY,
                    section_id INTEGER REFERENCES sections(id) ON DELETE CASCADE,
                    role VARCHAR(20) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(section_id, role)
                )
            ''')

            # ── 6. Section Department Access (per-department staff toggle) ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS section_department_access (
                    id SERIAL PRIMARY KEY,
                    section_id INTEGER REFERENCES sections(id) ON DELETE CASCADE,
                    department_id BIGINT REFERENCES departments(id) ON DELETE CASCADE,
                    staff_allowed BOOLEAN DEFAULT true,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(section_id, department_id)
                )
            ''')

            # ── 7. Section Exclusions (per-department employee exclusions) ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS section_exclusions (
                    id SERIAL PRIMARY KEY,
                    section_id INTEGER REFERENCES sections(id) ON DELETE CASCADE,
                    department_id BIGINT REFERENCES departments(id) ON DELETE CASCADE,
                    employee_sid BIGINT REFERENCES employees(sid) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(section_id, department_id, employee_sid)
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

            # Section Groups
            cursor.execute('SELECT COUNT(*) FROM section_groups')
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    INSERT INTO section_groups (code, name, description) VALUES
                    ('MAIN', 'Main', 'Dashboard and general overview'),
                    ('COMMISSION', 'Commission', 'Commission data, store commission, and sales performance'),
                    ('REPORTS', 'Reports', 'Report generation and analytics'),
                    ('ADMINISTRATION', 'Administration', 'User management, employee data, master data, settings')
                ''')

            # Sections (linked to groups via group_id)
            cursor.execute('SELECT COUNT(*) FROM sections')
            if cursor.fetchone()[0] == 0:
                cursor.execute("SELECT id, code FROM section_groups")
                groups = {row[1]: row[0] for row in cursor.fetchall()}

                section_data = [
                    ('DASHBOARD', 'Dashboard', 'Main dashboard', 'MAIN'),
                    ('COMMISSION_DATA', 'Commission Data', 'Commission data management', 'COMMISSION'),
                    ('STORE_COMMISSION', 'Store Commission', 'Store commission calculations', 'COMMISSION'),
                    ('SALES_PERFORMANCE', 'Sales Performance', 'Sales performance metrics', 'COMMISSION'),
                    ('REPORTS', 'Reports', 'Report generation', 'REPORTS'),
                    ('USER_MANAGEMENT', 'User Management', 'User administration', 'ADMINISTRATION'),
                    ('EMPLOYEE_DATA', 'Employee Data', 'Employee information', 'ADMINISTRATION'),
                    ('MASTER_DATA', 'Master Data', 'Master data configuration', 'ADMINISTRATION'),
                    ('SETTINGS', 'Settings', 'System settings', 'ADMINISTRATION'),
                ]
                for code, name, desc, group_code in section_data:
                    cursor.execute('''
                        INSERT INTO sections (code, name, description, group_id)
                        VALUES (%s, %s, %s, %s)
                    ''', (code, name, desc, groups.get(group_code)))

            # Section Group Permissions (which departments see which group)
            cursor.execute('SELECT COUNT(*) FROM section_group_permissions')
            if cursor.fetchone()[0] == 0:
                self._seed_group_permissions(cursor)

            # Section Permissions (which roles see which item)
            cursor.execute('SELECT COUNT(*) FROM section_permissions')
            if cursor.fetchone()[0] == 0:
                self._seed_section_permissions(cursor)

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

    def _seed_group_permissions(self, cursor):
        """Seed which departments can access which section groups"""
        cursor.execute("SELECT id, code FROM section_groups")
        groups = {row[1]: row[0] for row in cursor.fetchall()}

        all_departments = ['HR', 'ACC', 'IT', 'MKT', 'LOG', 'BOD']

        # MAIN - All departments
        if 'MAIN' in groups:
            for dept in all_departments:
                cursor.execute('''
                    INSERT INTO section_group_permissions (group_id, department_code)
                    VALUES (%s, %s)
                ''', (groups['MAIN'], dept))

        # COMMISSION - HR only
        if 'COMMISSION' in groups:
            for dept in ['HR']:
                cursor.execute('''
                    INSERT INTO section_group_permissions (group_id, department_code)
                    VALUES (%s, %s)
                ''', (groups['COMMISSION'], dept))

        # REPORTS - All departments
        if 'REPORTS' in groups:
            for dept in all_departments:
                cursor.execute('''
                    INSERT INTO section_group_permissions (group_id, department_code)
                    VALUES (%s, %s)
                ''', (groups['REPORTS'], dept))

        # ADMINISTRATION - No rows (IT access is hardcoded in frontend)

    def _seed_section_permissions(self, cursor):
        """Seed which roles can access which section items"""
        cursor.execute('SELECT id, code FROM sections')
        sections = {row[1]: row[0] for row in cursor.fetchall()}

        # (section_code, role) — if a row exists, that role can access the item
        default_permissions = [
            ('DASHBOARD', 'admin'),
            ('DASHBOARD', 'manager'),
            ('DASHBOARD', 'staff'),
            ('COMMISSION_DATA', 'admin'),
            ('COMMISSION_DATA', 'manager'),
            ('COMMISSION_DATA', 'staff'),
            ('STORE_COMMISSION', 'admin'),
            ('STORE_COMMISSION', 'manager'),
            ('SALES_PERFORMANCE', 'admin'),
            ('SALES_PERFORMANCE', 'manager'),
            ('SALES_PERFORMANCE', 'staff'),
            ('REPORTS', 'admin'),
            ('REPORTS', 'manager'),
            ('USER_MANAGEMENT', 'admin'),
            ('EMPLOYEE_DATA', 'admin'),
            ('EMPLOYEE_DATA', 'manager'),
            ('MASTER_DATA', 'admin'),
            ('SETTINGS', 'admin'),
        ]

        for section_code, role in default_permissions:
            section_id = sections.get(section_code)
            if section_id:
                cursor.execute('''
                    INSERT INTO section_permissions (section_id, role)
                    VALUES (%s, %s)
                    ON CONFLICT (section_id, role) DO NOTHING
                ''', (section_id, role))

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

    # ==================== Section Management ====================

    def get_all_sections(self) -> Dict[str, Any]:
        """Get all sections grouped by section_group, with their role access"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            cursor.execute('''
                SELECT s.id, s.code, s.name, s.description, s.is_active,
                       sg.code AS group_code, sg.name AS group_name,
                       COALESCE(array_agg(sp.role) FILTER (WHERE sp.role IS NOT NULL), '{}') AS allowed_roles
                FROM sections s
                LEFT JOIN section_groups sg ON s.group_id = sg.id
                LEFT JOIN section_permissions sp ON s.id = sp.section_id
                GROUP BY s.id, s.code, s.name, s.description, s.is_active, sg.id, sg.code, sg.name
                ORDER BY sg.id, s.id
            ''')

            sections = []
            for row in cursor.fetchall():
                sections.append({
                    'id': row[0],
                    'code': row[1],
                    'name': row[2],
                    'description': row[3],
                    'is_active': row[4],
                    'group_code': row[5],
                    'group_name': row[6],
                    'allowed_roles': list(row[7]) if row[7] else []
                })

            cursor.close()
            return {'success': True, 'sections': sections}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    # ==================== Permission Management ====================

    def get_user_permissions(self, user_role: str, department_id: int = None,
                             department_code: str = None) -> List[Dict[str, Any]]:
        """
        Get accessible section groups and items for a user.

        Layer 1: Filter groups by department (via section_group_permissions)
        Layer 2: Filter items by role (via section_permissions)
        Admin bypasses both layers.
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return []

            cursor = conn.cursor()

            # Resolve department_code if not provided
            if not department_code and department_id:
                cursor.execute('SELECT code FROM departments WHERE id = %s', (department_id,))
                row = cursor.fetchone()
                department_code = row[0] if row else None

            if user_role == 'admin':
                # Admin sees all groups and all items
                cursor.execute('''
                    SELECT sg.code AS group_code, sg.name AS group_name,
                           s.code AS section_code, s.name AS section_name
                    FROM section_groups sg
                    LEFT JOIN sections s ON s.group_id = sg.id AND s.is_active = TRUE
                    ORDER BY sg.id, s.id
                ''')
            else:
                # Non-admin: filter by department (group level) and role (item level)
                cursor.execute('''
                    SELECT sg.code AS group_code, sg.name AS group_name,
                           s.code AS section_code, s.name AS section_name
                    FROM section_groups sg
                    JOIN section_group_permissions sgp ON sg.id = sgp.group_id
                    LEFT JOIN sections s ON s.group_id = sg.id AND s.is_active = TRUE
                    LEFT JOIN section_permissions sp ON s.id = sp.section_id AND sp.role = %s
                    WHERE sgp.department_code = %s
                      AND (s.id IS NULL OR sp.id IS NOT NULL)
                    ORDER BY sg.id, s.id
                ''', (user_role, department_code))

            # Build grouped result
            groups = {}
            for row in cursor.fetchall():
                group_code = row[0]
                if group_code not in groups:
                    groups[group_code] = {
                        'group_code': row[0],
                        'group_name': row[1],
                        'sections': []
                    }
                if row[2]:  # section exists
                    groups[group_code]['sections'].append({
                        'section_code': row[2],
                        'section_name': row[3]
                    })

            cursor.close()
            return list(groups.values())

        except Exception as e:
            print(f"Error getting user permissions: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def update_section_roles(self, section_code: str, roles: List[str]) -> Dict[str, Any]:
        """
        Update which roles can access a section item.
        Replaces all existing role access for the section.
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Get section ID
            cursor.execute('SELECT id FROM sections WHERE code = %s', (section_code,))
            section = cursor.fetchone()
            if not section:
                return {'success': False, 'error': 'Section not found'}

            section_id = section[0]

            # Delete existing role permissions
            cursor.execute('DELETE FROM section_permissions WHERE section_id = %s', (section_id,))

            # Insert new role permissions
            valid_roles = ['admin', 'manager', 'staff']
            for role in roles:
                if role.lower() in valid_roles:
                    cursor.execute('''
                        INSERT INTO section_permissions (section_id, role)
                        VALUES (%s, %s)
                        ON CONFLICT (section_id, role) DO NOTHING
                    ''', (section_id, role.lower()))

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'Section roles updated successfully'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    # ==================== Section Groups Management ====================

    def get_section_group_permissions(self) -> Dict[str, Any]:
        """
        Get all section group permissions with their sections and role access.
        Excludes ADMINISTRATION (hardcoded to IT in frontend).
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Get groups with department access
            cursor.execute('''
                SELECT sg.id, sg.code, sg.name,
                       COALESCE(array_agg(sgp.department_code)
                           FILTER (WHERE sgp.department_code IS NOT NULL), '{}') as departments
                FROM section_groups sg
                LEFT JOIN section_group_permissions sgp ON sg.id = sgp.group_id
                WHERE sg.code != 'ADMINISTRATION'
                GROUP BY sg.id, sg.code, sg.name
                ORDER BY sg.id
            ''')

            permissions = []
            for row in cursor.fetchall():
                group_id = row[0]

                # Get sections within this group with their role access
                cursor.execute('''
                    SELECT s.code, s.name,
                           COALESCE(array_agg(sp.role)
                               FILTER (WHERE sp.role IS NOT NULL), '{}') AS allowed_roles
                    FROM sections s
                    LEFT JOIN section_permissions sp ON s.id = sp.section_id
                    WHERE s.group_id = %s AND s.is_active = TRUE
                    GROUP BY s.id, s.code, s.name
                    ORDER BY s.id
                ''', (group_id,))

                sections = []
                for sec_row in cursor.fetchall():
                    sections.append({
                        'code': sec_row[0],
                        'name': sec_row[1],
                        'allowed_roles': list(sec_row[2]) if sec_row[2] else []
                    })

                permissions.append({
                    'group_code': row[1],
                    'group_name': row[2],
                    'allowed_departments': list(row[3]) if row[3] else [],
                    'sections': sections
                })

            cursor.close()
            return {'success': True, 'permissions': permissions}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def update_section_group_departments(self, group_code: str,
                                          allowed_departments: List[str]) -> Dict[str, Any]:
        """
        Update the allowed departments for a section group.
        Empty list means admin-only access (no departments selected).
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Get group ID
            cursor.execute('SELECT id FROM section_groups WHERE code = %s', (group_code,))
            group = cursor.fetchone()
            if not group:
                return {'success': False, 'error': 'Section group not found'}

            group_id = group[0]

            # Delete existing permissions for this group
            cursor.execute('DELETE FROM section_group_permissions WHERE group_id = %s', (group_id,))

            # Insert new permissions
            for dept_code in allowed_departments:
                cursor.execute('''
                    INSERT INTO section_group_permissions (group_id, department_code)
                    VALUES (%s, %s)
                ''', (group_id, dept_code.upper()))

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

    # ==================== Per-Department Access Management ====================

    def get_department_permissions(self, department_code: str) -> Dict[str, Any]:
        """
        Get groups accessible by a department with per-department staff status
        and exclusions per section.
        Only returns groups where the department has Layer 1 access.
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Resolve department_code to department_id
            cursor.execute('SELECT id FROM departments WHERE code = %s', (department_code,))
            dept_row = cursor.fetchone()
            if not dept_row:
                return {'success': False, 'error': 'Department not found'}
            department_id = dept_row[0]

            # Get groups where this department has Layer 1 access
            cursor.execute('''
                SELECT sg.id, sg.code, sg.name
                FROM section_groups sg
                JOIN section_group_permissions sgp ON sg.id = sgp.group_id
                WHERE sgp.department_code = %s
                ORDER BY sg.id
            ''', (department_code,))

            permissions = []
            for group_row in cursor.fetchall():
                group_id, group_code, group_name = group_row

                # Get sections with per-department staff_allowed
                cursor.execute('''
                    SELECT s.id, s.code, s.name,
                           COALESCE(sda.staff_allowed, true) AS staff_allowed
                    FROM sections s
                    LEFT JOIN section_department_access sda
                        ON s.id = sda.section_id AND sda.department_id = %s
                    WHERE s.group_id = %s AND s.is_active = TRUE
                    ORDER BY s.id
                ''', (department_id, group_id))

                sections = []
                for sec_row in cursor.fetchall():
                    section_id, section_code, section_name, staff_allowed = sec_row

                    # Get excluded employees for this section+department
                    cursor.execute('''
                        SELECT e.sid, e.employee_code, e.full_name
                        FROM section_exclusions se
                        JOIN employees e ON se.employee_sid = e.sid
                        WHERE se.section_id = %s AND se.department_id = %s
                        ORDER BY e.full_name
                    ''', (section_id, department_id))

                    excluded_employees = [
                        {'sid': row[0], 'employee_code': row[1], 'full_name': row[2]}
                        for row in cursor.fetchall()
                    ]

                    sections.append({
                        'code': section_code,
                        'name': section_name,
                        'staff_allowed': staff_allowed,
                        'excluded_employees': excluded_employees
                    })

                permissions.append({
                    'group_code': group_code,
                    'group_name': group_name,
                    'sections': sections
                })

            cursor.close()
            return {
                'success': True,
                'department_code': department_code,
                'permissions': permissions
            }

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def update_department_staff_access(self, section_code: str, department_code: str,
                                       staff_allowed: bool) -> Dict[str, Any]:
        """Toggle whether staff of a specific department can access a section."""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Get section ID
            cursor.execute('SELECT id FROM sections WHERE code = %s', (section_code,))
            section = cursor.fetchone()
            if not section:
                return {'success': False, 'error': 'Section not found'}
            section_id = section[0]

            # Get department ID
            cursor.execute('SELECT id FROM departments WHERE code = %s', (department_code,))
            dept = cursor.fetchone()
            if not dept:
                return {'success': False, 'error': 'Department not found'}
            department_id = dept[0]

            # Upsert
            cursor.execute('''
                INSERT INTO section_department_access (section_id, department_id, staff_allowed)
                VALUES (%s, %s, %s)
                ON CONFLICT (section_id, department_id)
                DO UPDATE SET staff_allowed = %s, updated_at = CURRENT_TIMESTAMP
            ''', (section_id, department_id, staff_allowed, staff_allowed))

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'Department staff access updated successfully'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def get_section_exclusions(self, section_code: str, department_code: str) -> Dict[str, Any]:
        """Get employees excluded from a section within a department."""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Get section ID
            cursor.execute('SELECT id FROM sections WHERE code = %s', (section_code,))
            section = cursor.fetchone()
            if not section:
                return {'success': False, 'error': 'Section not found'}
            section_id = section[0]

            # Get department ID
            cursor.execute('SELECT id FROM departments WHERE code = %s', (department_code,))
            dept = cursor.fetchone()
            if not dept:
                return {'success': False, 'error': 'Department not found'}
            department_id = dept[0]

            cursor.execute('''
                SELECT e.sid, e.employee_code, e.full_name
                FROM section_exclusions se
                JOIN employees e ON se.employee_sid = e.sid
                WHERE se.section_id = %s AND se.department_id = %s
                ORDER BY e.full_name
            ''', (section_id, department_id))

            exclusions = [
                {'sid': row[0], 'employee_code': row[1], 'full_name': row[2]}
                for row in cursor.fetchall()
            ]

            cursor.close()
            return {'success': True, 'exclusions': exclusions}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def update_section_exclusions(self, section_code: str, department_code: str,
                                   excluded_employee_sids: List[int]) -> Dict[str, Any]:
        """Replace all exclusions for a section+department combination."""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Get section ID
            cursor.execute('SELECT id FROM sections WHERE code = %s', (section_code,))
            section = cursor.fetchone()
            if not section:
                return {'success': False, 'error': 'Section not found'}
            section_id = section[0]

            # Get department ID
            cursor.execute('SELECT id FROM departments WHERE code = %s', (department_code,))
            dept = cursor.fetchone()
            if not dept:
                return {'success': False, 'error': 'Department not found'}
            department_id = dept[0]

            # Delete existing exclusions for this section+department
            cursor.execute('''
                DELETE FROM section_exclusions
                WHERE section_id = %s AND department_id = %s
            ''', (section_id, department_id))

            # Insert new exclusions
            for employee_sid in excluded_employee_sids:
                cursor.execute('''
                    INSERT INTO section_exclusions (section_id, department_id, employee_sid)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (section_id, department_id, employee_sid) DO NOTHING
                ''', (section_id, department_id, employee_sid))

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'Exclusions updated successfully'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()
