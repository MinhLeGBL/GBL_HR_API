"""
HR Employee Service - PostgreSQL-based employee management for HR frontend
"""
from typing import Dict, Any, List, Optional
from app.database.connection import get_postgres_connection


class HREmployeeService:
    """Service for managing employees in PostgreSQL (HR frontend)"""

    def init_database(self) -> Dict[str, Any]:
        """Initialize the employees table"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()

            # Create employees table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS employees (
                    sid SERIAL PRIMARY KEY,
                    employee_code VARCHAR(50) UNIQUE NOT NULL,
                    full_name VARCHAR(255) NOT NULL,
                    employee_type VARCHAR(20) NOT NULL,
                    contract VARCHAR(20) NOT NULL,
                    department_id INTEGER NOT NULL REFERENCES departments(id),
                    store_id INTEGER REFERENCES stores(id),
                    email VARCHAR(255),
                    retailpro_username VARCHAR(100),
                    retailpro_sid VARCHAR(100),
                    is_active BOOLEAN DEFAULT true,
                    join_date DATE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT valid_employee_type CHECK (employee_type IN ('store', 'office')),
                    CONSTRAINT valid_contract CHECK (contract IN ('probation', 'intern', 'permanent')),
                    CONSTRAINT store_required_for_store_type CHECK (employee_type != 'store' OR store_id IS NOT NULL),
                    CONSTRAINT email_required_for_office CHECK (employee_type = 'store' OR email IS NOT NULL)
                )
            ''')

            # Create indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_employees_employee_code ON employees(employee_code)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_employees_department ON employees(department_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_employees_store ON employees(store_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_employees_type ON employees(employee_type)')

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'Employees table initialized'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def _format_employee(self, row, columns) -> Dict[str, Any]:
        """Format employee row with department and store info"""
        employee = dict(zip(columns, row))

        # Format dates
        if employee.get('join_date'):
            employee['join_date'] = employee['join_date'].isoformat() if hasattr(employee['join_date'], 'isoformat') else str(employee['join_date'])
        if employee.get('created_at'):
            employee['created_at'] = employee['created_at'].isoformat()
        if employee.get('updated_at'):
            employee['updated_at'] = employee['updated_at'].isoformat()

        return employee

    def _get_department_info(self, cursor, department_id: int) -> Optional[Dict]:
        """Get department info by ID"""
        if not department_id:
            return None
        cursor.execute('SELECT id, code, name FROM departments WHERE id = %s', (department_id,))
        row = cursor.fetchone()
        if row:
            return {'id': row[0], 'code': row[1], 'name': row[2]}
        return None

    def _get_store_info(self, cursor, store_id: int) -> Optional[Dict]:
        """Get store info by ID"""
        if not store_id:
            return None
        cursor.execute('SELECT id, store_code, store_name, store_rp_sid FROM stores WHERE id = %s', (store_id,))
        row = cursor.fetchone()
        if row:
            return {'id': row[0], 'store_code': row[1], 'store_name': row[2], 'store_rp_sid': row[3]}
        return None

    def get_all_employees(self) -> Dict[str, Any]:
        """Get all employees with department and store info"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()
            cursor.execute('''
                SELECT sid, employee_code, full_name, employee_type, contract,
                       department_id, store_id, email, retailpro_username, retailpro_sid,
                       is_active, join_date, created_at, updated_at
                FROM employees
                ORDER BY full_name
            ''')

            columns = ['sid', 'employee_code', 'full_name', 'employee_type', 'contract',
                       'department_id', 'store_id', 'email', 'retailpro_username', 'retailpro_sid',
                       'is_active', 'join_date', 'created_at', 'updated_at']

            employees = []
            for row in cursor.fetchall():
                employee = self._format_employee(row, columns)
                employee['department'] = self._get_department_info(cursor, employee['department_id'])
                employee['store'] = self._get_store_info(cursor, employee['store_id'])
                employees.append(employee)

            cursor.close()
            return {'success': True, 'employees': employees}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def get_employee_by_sid(self, sid: int) -> Dict[str, Any]:
        """Get an employee by SID"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()
            cursor.execute('''
                SELECT sid, employee_code, full_name, employee_type, contract,
                       department_id, store_id, email, retailpro_username, retailpro_sid,
                       is_active, join_date, created_at, updated_at
                FROM employees WHERE sid = %s
            ''', (sid,))

            row = cursor.fetchone()
            if not row:
                cursor.close()
                return {'success': False, 'error': 'Employee not found'}

            columns = ['sid', 'employee_code', 'full_name', 'employee_type', 'contract',
                       'department_id', 'store_id', 'email', 'retailpro_username', 'retailpro_sid',
                       'is_active', 'join_date', 'created_at', 'updated_at']

            employee = self._format_employee(row, columns)
            employee['department'] = self._get_department_info(cursor, employee['department_id'])
            employee['store'] = self._get_store_info(cursor, employee['store_id'])

            cursor.close()
            return {'success': True, 'employee': employee}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def create_employee(self, employee_code: str, full_name: str, employee_type: str,
                        contract: str, department_id: int, join_date: str,
                        store_id: int = None, email: str = None,
                        is_active: bool = True) -> Dict[str, Any]:
        """Create a new employee"""
        conn = None
        try:
            # Validate employee type constraints
            if employee_type == 'store' and not store_id:
                return {'success': False, 'error': 'store_id is required for store employees'}
            if employee_type == 'office' and not email:
                return {'success': False, 'error': 'email is required for office employees'}

            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO employees (employee_code, full_name, employee_type, contract,
                                       department_id, store_id, email, is_active, join_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING sid, employee_code, full_name, employee_type, contract,
                          department_id, store_id, email, retailpro_username, retailpro_sid,
                          is_active, join_date, created_at, updated_at
            ''', (employee_code, full_name, employee_type, contract,
                  department_id, store_id, email, is_active, join_date))

            row = cursor.fetchone()
            conn.commit()

            columns = ['sid', 'employee_code', 'full_name', 'employee_type', 'contract',
                       'department_id', 'store_id', 'email', 'retailpro_username', 'retailpro_sid',
                       'is_active', 'join_date', 'created_at', 'updated_at']

            employee = self._format_employee(row, columns)
            employee['department'] = self._get_department_info(cursor, employee['department_id'])
            employee['store'] = self._get_store_info(cursor, employee['store_id'])

            cursor.close()
            return {'success': True, 'employee': employee}

        except Exception as e:
            if conn:
                conn.rollback()
            error_msg = str(e)
            if 'employee_code' in error_msg.lower() and ('unique' in error_msg.lower() or 'duplicate' in error_msg.lower()):
                return {'success': False, 'error': 'Employee code already exists'}
            if 'email' in error_msg.lower() and ('unique' in error_msg.lower() or 'duplicate' in error_msg.lower()):
                return {'success': False, 'error': 'Email already exists'}
            return {'success': False, 'error': error_msg}
        finally:
            if conn:
                conn.close()

    def update_employee(self, sid: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update an employee (employee_code cannot be changed)"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Check if employee exists
            cursor.execute('SELECT sid FROM employees WHERE sid = %s', (sid,))
            if not cursor.fetchone():
                cursor.close()
                return {'success': False, 'error': 'Employee not found'}

            # Build update query dynamically
            allowed_fields = ['full_name', 'employee_type', 'contract', 'department_id',
                              'store_id', 'email', 'is_active', 'join_date']

            set_clauses = []
            values = []
            for field in allowed_fields:
                if field in updates:
                    set_clauses.append(f'{field} = %s')
                    values.append(updates[field])

            if not set_clauses:
                return {'success': False, 'error': 'No valid fields to update'}

            # Add updated_at
            set_clauses.append('updated_at = CURRENT_TIMESTAMP')
            values.append(sid)

            query = f'''
                UPDATE employees SET {', '.join(set_clauses)}
                WHERE sid = %s
                RETURNING sid, employee_code, full_name, employee_type, contract,
                          department_id, store_id, email, retailpro_username, retailpro_sid,
                          is_active, join_date, created_at, updated_at
            '''

            cursor.execute(query, values)
            row = cursor.fetchone()
            conn.commit()

            columns = ['sid', 'employee_code', 'full_name', 'employee_type', 'contract',
                       'department_id', 'store_id', 'email', 'retailpro_username', 'retailpro_sid',
                       'is_active', 'join_date', 'created_at', 'updated_at']

            employee = self._format_employee(row, columns)
            employee['department'] = self._get_department_info(cursor, employee['department_id'])
            employee['store'] = self._get_store_info(cursor, employee['store_id'])

            cursor.close()
            return {'success': True, 'employee': employee}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def delete_employee(self, sid: int) -> Dict[str, Any]:
        """Delete an employee"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            cursor.execute('DELETE FROM employees WHERE sid = %s RETURNING sid', (sid,))
            deleted = cursor.fetchone()

            if not deleted:
                cursor.close()
                return {'success': False, 'error': 'Employee not found'}

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
