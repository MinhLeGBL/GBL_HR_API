"""
HR Employee Service - PostgreSQL-based employee management for HR frontend
"""
from typing import Dict, Any, List, Optional
from app.database.connection import get_postgres_connection


class HREmployeeService:
    """Service for managing employees in PostgreSQL (HR frontend)"""

    def init_database(self) -> Dict[str, Any]:
        """Initialize lookup tables and employees table"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()

            # ── Lookup: Employee Types (5-digit IDs, prefix 1) ──
            cursor.execute('''
                CREATE SEQUENCE IF NOT EXISTS employee_type_id_seq
                START WITH 10001
                INCREMENT BY 1
                NO MAXVALUE
                NO CYCLE
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS employee_types (
                    id INTEGER PRIMARY KEY DEFAULT nextval('employee_type_id_seq'),
                    code VARCHAR(20) UNIQUE NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE
                )
            ''')
            cursor.execute('SELECT COUNT(*) FROM employee_types')
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    INSERT INTO employee_types (code, name) VALUES
                    ('OFFICE', 'Office'),
                    ('STORE', 'Store')
                ''')

            # ── Lookup: Contract Types (5-digit IDs, prefix 2) ──
            cursor.execute('''
                CREATE SEQUENCE IF NOT EXISTS contract_type_id_seq
                START WITH 20001
                INCREMENT BY 1
                NO MAXVALUE
                NO CYCLE
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS contract_types (
                    id INTEGER PRIMARY KEY DEFAULT nextval('contract_type_id_seq'),
                    code VARCHAR(20) UNIQUE NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE
                )
            ''')
            cursor.execute('SELECT COUNT(*) FROM contract_types')
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    INSERT INTO contract_types (code, name) VALUES
                    ('PERMANENT', 'Permanent'),
                    ('INTERN', 'Intern'),
                    ('PROBATION', 'Probation')
                ''')

            # ── Employee SIDs: 9-digit starting with 3 (300000001+) ──
            cursor.execute('''
                CREATE SEQUENCE IF NOT EXISTS employee_sid_seq
                START WITH 300000001
                INCREMENT BY 1
                NO MAXVALUE
                NO CYCLE
            ''')

            # ── Employees table ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS employees (
                    sid BIGINT PRIMARY KEY DEFAULT nextval('employee_sid_seq'),
                    employee_code VARCHAR(50) UNIQUE NOT NULL,
                    full_name VARCHAR(255) NOT NULL,
                    employee_type_id INTEGER NOT NULL REFERENCES employee_types(id),
                    contract_type_id INTEGER NOT NULL REFERENCES contract_types(id),
                    department_id BIGINT NOT NULL REFERENCES departments(id),
                    store_id BIGINT REFERENCES stores(id),
                    email VARCHAR(255),
                    retailpro_username VARCHAR(100),
                    retailpro_sid VARCHAR(100),
                    is_active BOOLEAN DEFAULT true,
                    join_date DATE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_employees_employee_code ON employees(employee_code)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_employees_department ON employees(department_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_employees_store ON employees(store_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_employees_type ON employees(employee_type_id)')

            # ── Seed default employee if table is empty ──
            cursor.execute('SELECT COUNT(*) FROM employees')
            if cursor.fetchone()[0] == 0:
                # Look up IDs for OFFICE type and PERMANENT contract
                cursor.execute("SELECT id FROM employee_types WHERE code = 'OFFICE'")
                office_type_id = cursor.fetchone()[0]
                cursor.execute("SELECT id FROM contract_types WHERE code = 'PERMANENT'")
                permanent_id = cursor.fetchone()[0]
                cursor.execute("SELECT id FROM departments WHERE code = 'IT'")
                it_dept_id = cursor.fetchone()[0]

                cursor.execute('''
                    INSERT INTO employees (
                        employee_code, full_name, employee_type_id, contract_type_id,
                        department_id, email, retailpro_sid, retailpro_username,
                        is_active, join_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    'MQ027', 'Le Minh', office_type_id, permanent_id,
                    it_dept_id, 'minhle@globallink.vn', '708336140000166083', 'MINH_LE',
                    True, '2018-01-01'
                ))

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

    # ── Lookup helpers ──

    def _resolve_employee_type_id(self, cursor, code: str) -> Optional[int]:
        """Resolve employee type code to ID"""
        cursor.execute('SELECT id FROM employee_types WHERE code = %s', (code.upper(),))
        row = cursor.fetchone()
        return row[0] if row else None

    def _resolve_contract_type_id(self, cursor, code: str) -> Optional[int]:
        """Resolve contract type code to ID"""
        cursor.execute('SELECT id FROM contract_types WHERE code = %s', (code.upper(),))
        row = cursor.fetchone()
        return row[0] if row else None

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

    def _build_employee_dict(self, row) -> Dict[str, Any]:
        """Build employee dict from a JOIN query row"""
        employee = {
            'sid': row[0],
            'employee_code': row[1],
            'full_name': row[2],
            'employee_type_id': row[3],
            'employee_type': row[4],       # et.code
            'contract_type_id': row[5],
            'contract': row[6],            # ct.code
            'department_id': row[7],
            'store_id': row[8],
            'email': row[9],
            'retailpro_username': row[10],
            'retailpro_sid': row[11],
            'is_active': row[12],
            'join_date': row[13].isoformat() if row[13] and hasattr(row[13], 'isoformat') else str(row[13]) if row[13] else None,
            'created_at': row[14].isoformat() if row[14] else None,
            'updated_at': row[15].isoformat() if row[15] else None,
        }
        return employee

    _EMPLOYEE_SELECT = '''
        SELECT e.sid, e.employee_code, e.full_name,
               e.employee_type_id, et.code AS employee_type,
               e.contract_type_id, ct.code AS contract,
               e.department_id, e.store_id, e.email,
               e.retailpro_username, e.retailpro_sid,
               e.is_active, e.join_date, e.created_at, e.updated_at
        FROM employees e
        JOIN employee_types et ON e.employee_type_id = et.id
        JOIN contract_types ct ON e.contract_type_id = ct.id
    '''

    # ── CRUD ──

    def get_all_employees(self, department_code: str = None, role: str = None) -> Dict[str, Any]:
        """Get all employees with department and store info.

        Optional filters:
            department_code: Filter by department code (e.g. 'HR', 'IT')
            role: Filter by user role (e.g. 'staff', 'manager') — only returns
                  employees who have a matching user account.
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            query = self._EMPLOYEE_SELECT
            joins = []
            conditions = []
            params = []

            if department_code:
                joins.append('JOIN departments d ON e.department_id = d.id')
                conditions.append('UPPER(d.code) = %s')
                params.append(department_code.upper())

            if role:
                joins.append('JOIN users u ON u.employee_sid = e.sid')
                joins.append('JOIN roles r ON u.role_id = r.id')
                conditions.append('LOWER(r.code) = %s')
                params.append(role.lower())

            if joins:
                query += ' ' + ' '.join(joins)
            if conditions:
                query += ' WHERE ' + ' AND '.join(conditions)

            query += ' ORDER BY e.full_name'
            cursor.execute(query, params)

            employees = []
            for row in cursor.fetchall():
                employee = self._build_employee_dict(row)
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
            cursor.execute(self._EMPLOYEE_SELECT + ' WHERE e.sid = %s', (sid,))

            row = cursor.fetchone()
            if not row:
                cursor.close()
                return {'success': False, 'error': 'Employee not found'}

            employee = self._build_employee_dict(row)
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
        """Create a new employee. Accepts type/contract as code strings (e.g. 'OFFICE', 'PERMANENT')."""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Resolve codes to IDs
            employee_type_id = self._resolve_employee_type_id(cursor, employee_type)
            if not employee_type_id:
                return {'success': False, 'error': f'Invalid employee_type: {employee_type}'}

            contract_type_id = self._resolve_contract_type_id(cursor, contract)
            if not contract_type_id:
                return {'success': False, 'error': f'Invalid contract: {contract}'}

            # Validate type-specific constraints
            if employee_type.upper() == 'STORE' and not store_id:
                return {'success': False, 'error': 'store_id is required for store employees'}
            if employee_type.upper() == 'OFFICE' and not email:
                return {'success': False, 'error': 'email is required for office employees'}

            cursor.execute('''
                INSERT INTO employees (employee_code, full_name, employee_type_id, contract_type_id,
                                       department_id, store_id, email, is_active, join_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING sid
            ''', (employee_code, full_name, employee_type_id, contract_type_id,
                  department_id, store_id, email, is_active, join_date))

            new_sid = cursor.fetchone()[0]
            conn.commit()

            # Fetch full employee with JOINs
            return self.get_employee_by_sid(new_sid)

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
        """Update an employee (employee_code cannot be changed).
        Accepts employee_type/contract as code strings, resolves to IDs."""
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

            # Resolve code strings to FK IDs
            if 'employee_type' in updates:
                type_id = self._resolve_employee_type_id(cursor, updates['employee_type'])
                if not type_id:
                    return {'success': False, 'error': f"Invalid employee_type: {updates['employee_type']}"}
                updates['employee_type_id'] = type_id
                del updates['employee_type']

            if 'contract' in updates:
                contract_id = self._resolve_contract_type_id(cursor, updates['contract'])
                if not contract_id:
                    return {'success': False, 'error': f"Invalid contract: {updates['contract']}"}
                updates['contract_type_id'] = contract_id
                del updates['contract']

            # Build update query dynamically
            allowed_fields = ['full_name', 'employee_type_id', 'contract_type_id', 'department_id',
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

            query = f'UPDATE employees SET {", ".join(set_clauses)} WHERE sid = %s RETURNING sid'
            cursor.execute(query, values)

            row = cursor.fetchone()
            conn.commit()

            # Fetch full employee with JOINs
            return self.get_employee_by_sid(sid)

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

    # ── Lookup endpoints ──

    def get_employee_types(self) -> Dict[str, Any]:
        """Get all employee types"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()
            cursor.execute('SELECT id, code, name, is_active FROM employee_types ORDER BY id')
            types = [{'id': r[0], 'code': r[1], 'name': r[2], 'is_active': r[3]} for r in cursor.fetchall()]
            cursor.close()
            return {'success': True, 'employee_types': types}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def get_contract_types(self) -> Dict[str, Any]:
        """Get all contract types"""
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()
            cursor.execute('SELECT id, code, name, is_active FROM contract_types ORDER BY id')
            types = [{'id': r[0], 'code': r[1], 'name': r[2], 'is_active': r[3]} for r in cursor.fetchall()]
            cursor.close()
            return {'success': True, 'contract_types': types}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()
