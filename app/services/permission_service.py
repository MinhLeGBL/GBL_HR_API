"""
Permission Service
Handles departments, sections, and permission management
"""
from typing import Optional, Dict, Any, List
from app.database.connection import get_postgres_connection


class PermissionService:
    """Service for handling departments and permission management"""

    # ==================== Database Schema ====================

    def init_permission_tables(self) -> Dict[str, Any]:
        """
        Initialize departments, sections, and permissions tables
        Run this once to create the necessary tables
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()

            # Create departments table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS departments (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(20) UNIQUE NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    description TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create sections table
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

            # Create section_permissions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS section_permissions (
                    id SERIAL PRIMARY KEY,
                    section_id INTEGER REFERENCES sections(id) ON DELETE CASCADE,
                    department_id INTEGER REFERENCES departments(id) ON DELETE CASCADE,
                    role VARCHAR(20) NOT NULL,
                    can_view BOOLEAN DEFAULT TRUE,
                    can_edit BOOLEAN DEFAULT FALSE,
                    can_delete BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_by BIGINT REFERENCES users(sid),
                    UNIQUE(section_id, department_id, role)
                )
            ''')

            # Add department_id to users table if not exists
            cursor.execute('''
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'users' AND column_name = 'department_id'
                    ) THEN
                        ALTER TABLE users ADD COLUMN department_id INTEGER REFERENCES departments(id);
                    END IF;
                END $$;
            ''')

            # Seed departments if empty
            cursor.execute('SELECT COUNT(*) FROM departments')
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    INSERT INTO departments (code, name, description) VALUES
                    ('HR', 'Human Resources', 'Human Resources Department'),
                    ('SALES', 'Sales', 'Sales Department'),
                    ('ACC', 'Accounting', 'Accounting Department'),
                    ('IT', 'Information Technology', 'IT Department'),
                    ('OPERATIONS', 'Operations', 'Operations Department'),
                    ('ADMIN', 'Administration', 'Administration Department'),
                    ('MKT', 'Marketing', 'Marketing Department'),
                    ('BOD', 'Management Board', 'Management Board')
                ''')

            # Seed sections if empty
            cursor.execute('SELECT COUNT(*) FROM sections')
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    INSERT INTO sections (code, name, description) VALUES
                    ('DASHBOARD', 'Dashboard', 'Main dashboard'),
                    ('COMMISSION_DATA', 'Commission Data', 'Commission data management'),
                    ('STORE_COMMISSION', 'Store Commission', 'Store commission calculations'),
                    ('SALES_PERFORMANCE', 'Sales Performance', 'Sales performance metrics'),
                    ('REPORTS', 'Reports', 'Report generation'),
                    ('USER_MANAGEMENT', 'User Management', 'User administration'),
                    ('EMPLOYEE_DATA', 'Employee Data', 'Employee information'),
                    ('MASTER_DATA', 'Master Data', 'Master data configuration'),
                    ('SETTINGS', 'Settings', 'System settings')
                ''')

            # Seed default permissions if empty
            cursor.execute('SELECT COUNT(*) FROM section_permissions')
            if cursor.fetchone()[0] == 0:
                self._seed_default_permissions(cursor)

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

    def _seed_default_permissions(self, cursor):
        """Seed default section permissions"""
        # Get section and department IDs
        cursor.execute('SELECT id, code FROM sections')
        sections = {row[1]: row[0] for row in cursor.fetchall()}

        cursor.execute('SELECT id, code FROM departments')
        departments = {row[1]: row[0] for row in cursor.fetchall()}

        # Default permission rules
        default_permissions = [
            # DASHBOARD - All departments, all roles
            ('DASHBOARD', None, 'admin', True, True, True),
            ('DASHBOARD', None, 'manager', True, False, False),
            ('DASHBOARD', None, 'staff', True, False, False),
            # COMMISSION_DATA - HR department
            ('COMMISSION_DATA', 'HR', 'admin', True, True, True),
            ('COMMISSION_DATA', 'HR', 'manager', True, True, False),
            ('COMMISSION_DATA', 'HR', 'staff', True, False, False),
            # STORE_COMMISSION - HR and SALES
            ('STORE_COMMISSION', 'HR', 'admin', True, True, True),
            ('STORE_COMMISSION', 'HR', 'manager', True, True, False),
            ('STORE_COMMISSION', 'SALES', 'admin', True, True, True),
            ('STORE_COMMISSION', 'SALES', 'manager', True, True, False),
            # SALES_PERFORMANCE - HR and SALES, all roles
            ('SALES_PERFORMANCE', 'HR', 'admin', True, True, True),
            ('SALES_PERFORMANCE', 'HR', 'manager', True, False, False),
            ('SALES_PERFORMANCE', 'HR', 'staff', True, False, False),
            ('SALES_PERFORMANCE', 'SALES', 'admin', True, True, True),
            ('SALES_PERFORMANCE', 'SALES', 'manager', True, False, False),
            ('SALES_PERFORMANCE', 'SALES', 'staff', True, False, False),
            # REPORTS - All departments, admin and manager
            ('REPORTS', None, 'admin', True, True, True),
            ('REPORTS', None, 'manager', True, False, False),
            # USER_MANAGEMENT - Admin only
            ('USER_MANAGEMENT', None, 'admin', True, True, True),
            # EMPLOYEE_DATA - HR
            ('EMPLOYEE_DATA', 'HR', 'admin', True, True, True),
            ('EMPLOYEE_DATA', 'HR', 'manager', True, True, False),
            # MASTER_DATA - Admin only
            ('MASTER_DATA', None, 'admin', True, True, True),
            # SETTINGS - Admin only
            ('SETTINGS', None, 'admin', True, True, True),
        ]

        for section_code, dept_code, role, can_view, can_edit, can_delete in default_permissions:
            section_id = sections.get(section_code)
            dept_id = departments.get(dept_code) if dept_code else None
            if section_id:
                cursor.execute('''
                    INSERT INTO section_permissions
                    (section_id, department_id, role, can_view, can_edit, can_delete)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (section_id, department_id, role) DO NOTHING
                ''', (section_id, dept_id, role, can_view, can_edit, can_delete))

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
        """Get all sections with their permissions"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Get sections
            cursor.execute('''
                SELECT id, code, name, description, is_active
                FROM sections ORDER BY name
            ''')
            sections_rows = cursor.fetchall()

            sections = []
            for row in sections_rows:
                section_id = row[0]

                # Get permissions for this section
                cursor.execute('''
                    SELECT sp.department_id, d.code as department_code, sp.role,
                           sp.can_view, sp.can_edit, sp.can_delete
                    FROM section_permissions sp
                    LEFT JOIN departments d ON sp.department_id = d.id
                    WHERE sp.section_id = %s
                ''', (section_id,))

                permissions = []
                for perm_row in cursor.fetchall():
                    permissions.append({
                        'department_id': perm_row[0],
                        'department_code': perm_row[1],
                        'role': perm_row[2],
                        'can_view': perm_row[3],
                        'can_edit': perm_row[4],
                        'can_delete': perm_row[5]
                    })

                sections.append({
                    'id': row[0],
                    'code': row[1],
                    'name': row[2],
                    'description': row[3],
                    'is_active': row[4],
                    'permissions': permissions
                })

            cursor.close()
            return {'success': True, 'sections': sections}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    # ==================== Permission Management ====================

    def get_user_permissions(self, user_role: str, department_id: int = None) -> List[Dict[str, Any]]:
        """Get permissions for a user based on role and department"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return []

            cursor = conn.cursor()

            # Admin gets all sections
            if user_role == 'admin':
                cursor.execute('''
                    SELECT DISTINCT s.code, s.name
                    FROM sections s
                    WHERE s.is_active = TRUE
                ''')
                permissions = []
                for row in cursor.fetchall():
                    permissions.append({
                        'section_code': row[0],
                        'section_name': row[1],
                        'allowed_departments': [],
                        'allowed_roles': ['admin', 'manager', 'staff']
                    })
                cursor.close()
                return permissions

            # For non-admin users
            cursor.execute('''
                SELECT DISTINCT s.code, s.name,
                       array_agg(DISTINCT d.code) FILTER (WHERE d.code IS NOT NULL) as departments,
                       array_agg(DISTINCT sp.role) as roles
                FROM sections s
                JOIN section_permissions sp ON s.id = sp.section_id
                LEFT JOIN departments d ON sp.department_id = d.id
                WHERE s.is_active = TRUE
                  AND sp.can_view = TRUE
                  AND sp.role = %s
                  AND (sp.department_id IS NULL OR sp.department_id = %s)
                GROUP BY s.code, s.name
            ''', (user_role, department_id))

            permissions = []
            for row in cursor.fetchall():
                permissions.append({
                    'section_code': row[0],
                    'section_name': row[1],
                    'allowed_departments': row[2] or [],
                    'allowed_roles': row[3] or []
                })

            cursor.close()
            return permissions

        except Exception as e:
            print(f"Error getting user permissions: {e}")
            return []
        finally:
            if conn:
                conn.close()

    def update_section_permission(self, section_id: int, department_id: int,
                                   role: str, can_view: bool, can_edit: bool,
                                   can_delete: bool, created_by: int = None) -> Dict[str, Any]:
        """Update or create a section permission"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO section_permissions
                (section_id, department_id, role, can_view, can_edit, can_delete, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (section_id, department_id, role)
                DO UPDATE SET can_view = %s, can_edit = %s, can_delete = %s
                RETURNING id
            ''', (section_id, department_id, role, can_view, can_edit, can_delete, created_by,
                  can_view, can_edit, can_delete))

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'Permission updated successfully'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def delete_section_permission(self, section_id: int, department_id: int) -> Dict[str, Any]:
        """Remove department from section"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            cursor.execute('''
                DELETE FROM section_permissions
                WHERE section_id = %s AND department_id = %s
                RETURNING id
            ''', (section_id, department_id))

            result = cursor.fetchone()
            conn.commit()
            cursor.close()

            if not result:
                return {'success': False, 'error': 'Permission not found'}

            return {'success': True, 'message': 'Permission removed successfully'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    # ==================== Section Groups Management ====================

    def init_section_groups(self) -> Dict[str, Any]:
        """
        Initialize section_groups and section_group_permissions tables
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()

            # Create section_groups table
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

            # Create section_group_permissions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS section_group_permissions (
                    id SERIAL PRIMARY KEY,
                    group_id INTEGER REFERENCES section_groups(id) ON DELETE CASCADE,
                    department_code VARCHAR(20) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_by BIGINT REFERENCES users(sid),
                    UNIQUE(group_id, department_code)
                )
            ''')

            # Seed section_groups if empty
            cursor.execute('SELECT COUNT(*) FROM section_groups')
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    INSERT INTO section_groups (code, name, description) VALUES
                    ('MAIN', 'Main', 'Dashboard and general overview'),
                    ('COMMISSION', 'Commission', 'Commission data, store commission, and sales performance'),
                    ('REPORTS', 'Reports', 'Report generation and analytics')
                ''')

            # Seed default permissions
            cursor.execute('SELECT COUNT(*) FROM section_group_permissions')
            if cursor.fetchone()[0] == 0:
                # Get all group IDs
                cursor.execute("SELECT id, code FROM section_groups")
                groups = {row[1]: row[0] for row in cursor.fetchall()}

                all_departments = ['HR', 'SALES', 'ACC', 'IT', 'OPERATIONS', 'ADMIN', 'MKT', 'BOD']

                # MAIN - All departments have access
                if 'MAIN' in groups:
                    for dept in all_departments:
                        cursor.execute('''
                            INSERT INTO section_group_permissions (group_id, department_code)
                            VALUES (%s, %s)
                        ''', (groups['MAIN'], dept))

                # COMMISSION - HR and SALES only
                if 'COMMISSION' in groups:
                    for dept in ['HR', 'SALES']:
                        cursor.execute('''
                            INSERT INTO section_group_permissions (group_id, department_code)
                            VALUES (%s, %s)
                        ''', (groups['COMMISSION'], dept))

                # REPORTS - All departments have access
                if 'REPORTS' in groups:
                    for dept in all_departments:
                        cursor.execute('''
                            INSERT INTO section_group_permissions (group_id, department_code)
                            VALUES (%s, %s)
                        ''', (groups['REPORTS'], dept))

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'Section groups initialized successfully'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def get_section_group_permissions(self) -> Dict[str, Any]:
        """
        Get all section group permissions
        Returns groups with their allowed_departments
        Empty allowed_departments means all departments have access
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Get all section groups (excluding ADMINISTRATION which is hardcoded in frontend)
            cursor.execute('''
                SELECT sg.id, sg.code, sg.name,
                       COALESCE(array_agg(sgp.department_code) FILTER (WHERE sgp.department_code IS NOT NULL), '{}') as departments
                FROM section_groups sg
                LEFT JOIN section_group_permissions sgp ON sg.id = sgp.group_id
                GROUP BY sg.id, sg.code, sg.name
                ORDER BY sg.id
            ''')

            permissions = []
            for row in cursor.fetchall():
                permissions.append({
                    'group_code': row[1],
                    'group_name': row[2],
                    'allowed_departments': list(row[3]) if row[3] else []
                })

            cursor.close()
            return {'success': True, 'permissions': permissions}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def update_section_group_departments(self, group_code: str, allowed_departments: List[str],
                                          created_by: int = None) -> Dict[str, Any]:
        """
        Update the allowed departments for a section group
        Empty list means all departments have access
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
                    INSERT INTO section_group_permissions (group_id, department_code, created_by)
                    VALUES (%s, %s, %s)
                ''', (group_id, dept_code.upper(), created_by))

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
