"""
HR Employee Service - PostgreSQL-based employee management for HR frontend
"""
from typing import Dict, Any, List, Optional
from datetime import date, datetime
from app.core.database.connection import get_postgres_connection, get_oracle_connection


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

            # ── CR #75: master is_manager + change-timestamps for point-in-time
            # contract/is_manager resolution in commission. contract_changed_at /
            # is_manager_changed_at bump ONLY when that field actually changes (set by
            # the writers below), so the commission resolver can compare the master's
            # change date against a history snapshot's period. Backfilled below. ──
            cursor.execute('ALTER TABLE employees ADD COLUMN IF NOT EXISTS is_manager BOOLEAN NOT NULL DEFAULT FALSE')
            cursor.execute('ALTER TABLE employees ADD COLUMN IF NOT EXISTS contract_changed_at TIMESTAMPTZ')
            cursor.execute('ALTER TABLE employees ADD COLUMN IF NOT EXISTS is_manager_changed_at TIMESTAMPTZ')

            # ── History: is_manager status changes ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS employee_manager_history (
                    id SERIAL PRIMARY KEY,
                    employee_sid BIGINT NOT NULL REFERENCES employees(sid) ON DELETE CASCADE,
                    is_manager BOOLEAN NOT NULL,
                    effective_from DATE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_emp_manager_history_sid
                ON employee_manager_history (employee_sid, effective_from DESC)
            ''')

            # ── History: store transfers ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS employee_store_history (
                    id SERIAL PRIMARY KEY,
                    employee_sid BIGINT NOT NULL REFERENCES employees(sid) ON DELETE CASCADE,
                    store_id BIGINT NOT NULL REFERENCES stores(id),
                    effective_from DATE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_emp_store_history_sid
                ON employee_store_history (employee_sid, effective_from DESC)
            ''')

            # ── History: unified employee status snapshots (CR #16) ──
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS employee_status_history (
                    id BIGSERIAL PRIMARY KEY,
                    employee_sid BIGINT NOT NULL REFERENCES employees(sid) ON DELETE CASCADE,
                    period_month INT NOT NULL,
                    period_year INT NOT NULL,
                    is_active BOOLEAN NOT NULL,
                    store_id BIGINT REFERENCES stores(id),
                    department_id BIGINT NOT NULL REFERENCES departments(id),
                    contract_type_id BIGINT NOT NULL REFERENCES contract_types(id),
                    is_manager BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (employee_sid, period_month, period_year)
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_esh_employee_period
                ON employee_status_history (employee_sid, period_year DESC, period_month DESC)
            ''')

            # ── Backfill: initial snapshot for employees without any history ──
            cursor.execute('''
                INSERT INTO employee_status_history
                    (employee_sid, period_month, period_year, is_active, store_id,
                     department_id, contract_type_id, is_manager)
                SELECT
                    e.sid,
                    EXTRACT(MONTH FROM e.join_date)::INT,
                    EXTRACT(YEAR FROM e.join_date)::INT,
                    e.is_active,
                    e.store_id,
                    e.department_id,
                    e.contract_type_id,
                    COALESCE(
                        (SELECT emh.is_manager FROM employee_manager_history emh
                         WHERE emh.employee_sid = e.sid
                         ORDER BY emh.effective_from DESC, emh.id DESC LIMIT 1),
                        FALSE
                    )
                FROM employees e
                ON CONFLICT (employee_sid, period_month, period_year) DO NOTHING
            ''')

            # ── CR #75 backfill: master is_manager + change-timestamps (idempotent;
            # the nullable *_changed_at columns are the "already backfilled" markers).
            # Each timestamp is set to "when the current master value started being
            # true" — created_at, or the latest history period whose value matches the
            # master — so the first post-deploy master edit (NOW()) correctly overrides. ──
            cursor.execute('''
                UPDATE employees e
                SET is_manager = COALESCE(
                    (SELECT h.is_manager FROM employee_status_history h
                     WHERE h.employee_sid = e.sid
                     ORDER BY h.period_year DESC, h.period_month DESC, h.id DESC
                     LIMIT 1), FALSE)
                WHERE e.is_manager_changed_at IS NULL
            ''')
            cursor.execute('''
                UPDATE employees e
                SET is_manager_changed_at = GREATEST(
                    e.created_at,
                    COALESCE((SELECT MAX(make_date(h.period_year, h.period_month, 1))
                              FROM employee_status_history h
                              WHERE h.employee_sid = e.sid AND h.is_manager = e.is_manager),
                             e.created_at))
                WHERE e.is_manager_changed_at IS NULL
            ''')
            cursor.execute('''
                UPDATE employees e
                SET contract_changed_at = GREATEST(
                    e.created_at,
                    COALESCE((SELECT MAX(make_date(h.period_year, h.period_month, 1))
                              FROM employee_status_history h
                              WHERE h.employee_sid = e.sid AND h.contract_type_id = e.contract_type_id),
                             e.created_at))
                WHERE e.contract_changed_at IS NULL
            ''')

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

    def _upsert_status_snapshot(self, cursor, employee_sid: int,
                                period_month: int, period_year: int,
                                is_active: bool, store_id, department_id: int,
                                contract_type_id: int, is_manager: bool):
        """Insert or update an employee status snapshot for a given period."""
        cursor.execute('''
            INSERT INTO employee_status_history
                (employee_sid, period_month, period_year, is_active, store_id,
                 department_id, contract_type_id, is_manager)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (employee_sid, period_month, period_year) DO UPDATE SET
                is_active = EXCLUDED.is_active,
                store_id = EXCLUDED.store_id,
                department_id = EXCLUDED.department_id,
                contract_type_id = EXCLUDED.contract_type_id,
                is_manager = EXCLUDED.is_manager,
                updated_at = NOW()
        ''', (employee_sid, period_month, period_year, is_active, store_id,
              department_id, contract_type_id, is_manager))

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
            'is_manager': row[16],
        }
        return employee

    _EMPLOYEE_SELECT = '''
        SELECT e.sid, e.employee_code, e.full_name,
               e.employee_type_id, et.code AS employee_type,
               e.contract_type_id, ct.code AS contract,
               e.department_id, e.store_id, e.email,
               e.retailpro_username, e.retailpro_sid,
               e.is_active, e.join_date, e.created_at, e.updated_at,
               (SELECT esh.is_manager FROM employee_status_history esh
                WHERE esh.employee_sid = e.sid
                ORDER BY esh.period_year DESC, esh.period_month DESC
                LIMIT 1) AS is_manager
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

            # Record initial store assignment in history (dual-write for transition)
            # CURRENT_DATE uses the DB server's timezone (UTC on deployment)
            if store_id:
                cursor.execute('''
                    INSERT INTO employee_store_history (employee_sid, store_id, effective_from)
                    VALUES (%s, %s, CURRENT_DATE)
                ''', (new_sid, store_id))

            # Record initial status snapshot (CR #16)
            join_dt = datetime.strptime(join_date, '%Y-%m-%d')
            self._upsert_status_snapshot(
                cursor, new_sid, join_dt.month, join_dt.year,
                is_active, store_id, department_id, contract_type_id, False
            )

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

            # Check if employee exists and fetch current store_id + contract for history tracking
            cursor.execute('SELECT sid, store_id, contract_type_id FROM employees WHERE sid = %s', (sid,))
            existing = cursor.fetchone()
            if not existing:
                cursor.close()
                return {'success': False, 'error': 'Employee not found'}
            current_store_id = existing[1]
            current_contract_type_id = existing[2]

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

            # CR #75: bump contract_changed_at ONLY when the contract actually changes,
            # so unrelated edits (name/email/store) don't make the master win in the
            # commission point-in-time resolver.
            if ('contract_type_id' in updates
                    and updates['contract_type_id'] != current_contract_type_id):
                set_clauses.append('contract_changed_at = NOW()')

            # Add updated_at
            set_clauses.append('updated_at = CURRENT_TIMESTAMP')
            values.append(sid)

            query = f'UPDATE employees SET {", ".join(set_clauses)} WHERE sid = %s RETURNING sid'
            cursor.execute(query, values)

            row = cursor.fetchone()

            # Insert store transfer history if store_id changed (dual-write for transition)
            # CURRENT_DATE uses the DB server's timezone (UTC on deployment)
            new_store_id = updates.get('store_id')
            if 'store_id' in updates and new_store_id != current_store_id:
                cursor.execute('''
                    INSERT INTO employee_store_history (employee_sid, store_id, effective_from)
                    VALUES (%s, %s, CURRENT_DATE)
                ''', (sid, new_store_id))

            # Insert status snapshot if any tracked field changed (CR #16)
            tracked_fields = ['is_active', 'store_id', 'department_id', 'contract_type_id']
            if any(f in updates for f in tracked_fields):
                cursor.execute(
                    'SELECT is_active, store_id, department_id, contract_type_id FROM employees WHERE sid = %s',
                    (sid,))
                current = cursor.fetchone()
                cursor.execute('''
                    SELECT is_manager FROM employee_status_history
                    WHERE employee_sid = %s ORDER BY period_year DESC, period_month DESC LIMIT 1
                ''', (sid,))
                mgr_row = cursor.fetchone()
                is_manager = mgr_row[0] if mgr_row else False

                today = date.today()
                self._upsert_status_snapshot(
                    cursor, sid, today.month, today.year,
                    current[0], current[1], current[2], current[3], is_manager
                )

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

    # ── Manager Status History ──

    def set_manager_status(self, sid: int, is_manager: bool) -> Dict[str, Any]:
        """
        Append a new is_manager history row for a store employee.

        effective_from is always today's date (server-side).
        History is append-only — each call inserts a new row.
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            cursor.execute('SELECT sid FROM employees WHERE sid = %s', (sid,))
            if not cursor.fetchone():
                cursor.close()
                return {'success': False, 'error': 'Employee not found'}

            # CURRENT_DATE uses the DB server's timezone (UTC on deployment)
            # Dual-write to old table for transition
            cursor.execute('''
                INSERT INTO employee_manager_history (employee_sid, is_manager, effective_from)
                VALUES (%s, %s, CURRENT_DATE)
                RETURNING is_manager, effective_from
            ''', (sid, is_manager))

            row = cursor.fetchone()

            # Also upsert status snapshot (CR #16)
            cursor.execute(
                'SELECT is_active, store_id, department_id, contract_type_id FROM employees WHERE sid = %s',
                (sid,))
            emp = cursor.fetchone()
            today = date.today()
            self._upsert_status_snapshot(
                cursor, sid, today.month, today.year,
                emp[0], emp[1], emp[2], emp[3], is_manager
            )

            # CR #75: Employee-Management is the master writer for is_manager — mirror
            # the value onto employees and bump is_manager_changed_at only on an actual
            # change, so the commission resolver can compare master recency vs history.
            cursor.execute('''
                UPDATE employees
                SET is_manager = %s,
                    is_manager_changed_at = CASE WHEN is_manager IS DISTINCT FROM %s
                                                 THEN NOW() ELSE is_manager_changed_at END
                WHERE sid = %s
            ''', (is_manager, is_manager, sid))

            conn.commit()
            cursor.close()

            return {
                'success': True,
                'is_manager': row[0],
                'effective_from': row[1].isoformat()
            }

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    # ── RetailPro Sync ──

    def sync_retailpro_data(self, sid: int) -> Dict[str, Any]:
        """
        Sync RetailPro account data for an employee using their employee code.

        1. Look up employee by SID to get employee_code
        2. Query Oracle RetailPro to find matching user by employee code
        3. Update retailpro_username and retailpro_sid in PostgreSQL
        4. Return full updated employee object
        """
        pg_conn = None
        oracle_conn = None
        try:
            # Step 1: Get employee from PostgreSQL
            pg_conn = get_postgres_connection()
            if not pg_conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = pg_conn.cursor()
            cursor.execute('SELECT employee_code FROM employees WHERE sid = %s', (sid,))
            row = cursor.fetchone()

            if not row:
                cursor.close()
                return {'success': False, 'error': 'Employee not found'}

            employee_code = row[0]

            # Step 2: Query Oracle RetailPro for matching employee
            oracle_conn = get_oracle_connection()
            if not oracle_conn:
                cursor.close()
                return {'success': False, 'error': 'Failed to connect to RetailPro database'}

            oracle_cursor = oracle_conn.cursor()
            oracle_cursor.execute("""
                SELECT
                    emp.SID as RETAILPRO_SID,
                    emp.USER_NAME as RETAILPRO_USERNAME
                FROM EMPLOYEE emp
                JOIN CUSTOMER cust ON emp.CUST_SID = cust.SID
                WHERE cust.UDF4_STRING = :employee_code
                  AND emp.USER_NAME IS NOT NULL
            """, {'employee_code': employee_code})

            rp_row = oracle_cursor.fetchone()
            oracle_cursor.close()
            oracle_conn.close()
            oracle_conn = None

            if not rp_row:
                cursor.close()
                return {'success': False, 'error': f'No RetailPro account found for employee code {employee_code}'}

            retailpro_sid = str(rp_row[0])
            retailpro_username = rp_row[1]

            # Step 3: Update PostgreSQL employee record
            cursor.execute('''
                UPDATE employees
                SET retailpro_sid = %s,
                    retailpro_username = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE sid = %s
            ''', (retailpro_sid, retailpro_username, sid))

            pg_conn.commit()
            cursor.close()

            # Step 4: Return full updated employee
            return self.get_employee_by_sid(sid)

        except Exception as e:
            if pg_conn:
                pg_conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if oracle_conn:
                oracle_conn.close()
            if pg_conn:
                pg_conn.close()
