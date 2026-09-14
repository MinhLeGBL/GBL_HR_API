"""
CR #75 — employees writers must bump the master change-timestamps ONLY when the
underlying field actually changes.

These tests drive `update_employee` / `set_manager_status` with a scripted cursor
mock and assert on the exact SQL issued (the `UPDATE employees ...` clause), which
is where the conditional-bump logic lives. No real DB.
"""
from datetime import date
from unittest.mock import MagicMock, patch

from app.modules.employees.service import HREmployeeService


def _make_conn(fetchone_script):
    """A mock connection whose cursor returns `fetchone_script` values in order
    and records every executed SQL string."""
    cursor = MagicMock()
    cursor.fetchone.side_effect = list(fetchone_script)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def _executed_sql(cursor):
    return [c.args[0] for c in cursor.execute.call_args_list if c.args]


def _update_employees_sql(cursor):
    return [s for s in _executed_sql(cursor) if 'UPDATE employees' in s]


class TestUpdateEmployeeContractTimestamp:
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_contract_change_bumps_contract_changed_at(self, mock_conn_fn):
        # existing(sid, store_id, contract=1) → resolve 'permanent'→2 → UPDATE RETURNING sid
        # → status snapshot reads (tracked contract changed)
        conn, cursor = _make_conn([
            (5, 10, 1),            # SELECT sid, store_id, contract_type_id
            (2,),                  # resolve contract 'permanent' -> id 2
            (5,),                  # UPDATE ... RETURNING sid
            (True, 10, 400, 2),    # snapshot read: is_active, store, dept, contract
            (False,),              # latest is_manager
        ])
        mock_conn_fn.return_value = conn
        svc = HREmployeeService()
        with patch.object(svc, 'get_employee_by_sid', return_value={'success': True}):
            svc.update_employee(5, {'contract': 'permanent'})

        upd = ' '.join(_update_employees_sql(cursor))
        assert 'contract_changed_at = NOW()' in upd

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_same_contract_value_does_not_bump(self, mock_conn_fn):
        conn, cursor = _make_conn([
            (5, 10, 1),            # current contract = 1
            (1,),                  # resolve 'probation' -> id 1 (unchanged)
            (5,),                  # UPDATE RETURNING sid
            (True, 10, 400, 1),    # snapshot read
            (False,),              # latest is_manager
        ])
        mock_conn_fn.return_value = conn
        svc = HREmployeeService()
        with patch.object(svc, 'get_employee_by_sid', return_value={'success': True}):
            svc.update_employee(5, {'contract': 'probation'})

        upd = ' '.join(_update_employees_sql(cursor))
        assert 'contract_changed_at' not in upd

    @patch('app.modules.employees.service.get_postgres_connection')
    def test_unrelated_field_edit_does_not_bump(self, mock_conn_fn):
        conn, cursor = _make_conn([
            (5, 10, 1),            # existing
            (5,),                  # UPDATE RETURNING sid (no contract, no tracked field)
        ])
        mock_conn_fn.return_value = conn
        svc = HREmployeeService()
        with patch.object(svc, 'get_employee_by_sid', return_value={'success': True}):
            svc.update_employee(5, {'full_name': 'New Name'})

        upd = ' '.join(_update_employees_sql(cursor))
        assert 'contract_changed_at' not in upd


class TestSetManagerStatusMaster:
    @patch('app.modules.employees.service.get_postgres_connection')
    def test_set_manager_status_mirrors_master_and_bumps(self, mock_conn_fn):
        conn, cursor = _make_conn([
            (5,),                       # SELECT sid FROM employees
            (True, date(2026, 6, 1)),   # INSERT manager_history RETURNING is_manager, effective_from
            (True, 10, 400, 1),         # snapshot read: is_active, store, dept, contract
        ])
        mock_conn_fn.return_value = conn
        svc = HREmployeeService()
        result = svc.set_manager_status(5, True)

        assert result['success'] is True
        upd = ' '.join(_update_employees_sql(cursor))
        assert 'is_manager' in upd and 'is_manager_changed_at' in upd
