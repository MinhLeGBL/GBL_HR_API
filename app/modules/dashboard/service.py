"""
Dashboard Service
Handles dashboard configuration and widget data management
"""
from typing import Optional, Dict, Any, List
from app.core.database.connection import get_postgres_connection


class DashboardService:
    """Service for handling dashboard configurations and widget data"""

    # Available widget types
    WIDGET_TYPES = [
        'total_employees',
        'active_employees',
        'pending_approvals',
        'total_commissions',
        'monthly_sales',
        'open_positions',
        'attendance_rate'
    ]

    # ==================== Database Schema ====================

    def init_dashboard_tables(self) -> Dict[str, Any]:
        """
        Initialize dashboard_configs and dashboard_widgets tables
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to PostgreSQL'}

            cursor = conn.cursor()

            # Create dashboard_configs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS dashboard_configs (
                    id SERIAL PRIMARY KEY,
                    department_code VARCHAR(20) NOT NULL UNIQUE,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_by BIGINT REFERENCES users(sid),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create dashboard_widgets table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS dashboard_widgets (
                    id SERIAL PRIMARY KEY,
                    config_id INTEGER REFERENCES dashboard_configs(id) ON DELETE CASCADE,
                    widget_id VARCHAR(50) NOT NULL,
                    widget_type VARCHAR(50) NOT NULL,
                    position INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(config_id, widget_id)
                )
            ''')

            conn.commit()
            cursor.close()

            return {'success': True, 'message': 'Dashboard tables initialized successfully'}

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    # ==================== Configuration Management ====================

    def get_dashboard_config(self, department_code: str) -> Dict[str, Any]:
        """
        Get dashboard configuration for a department
        Returns null config if no configuration exists
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()

            # Get config
            cursor.execute('''
                SELECT id, department_code, updated_at, updated_by
                FROM dashboard_configs
                WHERE department_code = %s
            ''', (department_code.upper(),))

            config_row = cursor.fetchone()

            if not config_row:
                cursor.close()
                return {'success': True, 'config': None}

            config_id = config_row[0]

            # Get widgets for this config
            cursor.execute('''
                SELECT widget_id, widget_type, position
                FROM dashboard_widgets
                WHERE config_id = %s
                ORDER BY position
            ''', (config_id,))

            widgets = []
            for row in cursor.fetchall():
                widgets.append({
                    'id': row[0],
                    'type': row[1],
                    'position': row[2]
                })

            cursor.close()

            return {
                'success': True,
                'config': {
                    'id': config_row[0],
                    'department_code': config_row[1],
                    'widgets': widgets,
                    'updated_at': config_row[2].isoformat() if config_row[2] else None,
                    'updated_by': config_row[3]
                }
            }

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    def save_dashboard_config(self, department_code: str, widgets: List[Dict[str, Any]],
                               updated_by: int = None) -> Dict[str, Any]:
        """
        Save dashboard configuration for a department
        Creates new config or updates existing one
        """
        conn = None
        try:
            conn = get_postgres_connection()
            if not conn:
                return {'success': False, 'error': 'Failed to connect to database'}

            cursor = conn.cursor()
            dept_code = department_code.upper()

            # Check if config exists
            cursor.execute('''
                SELECT id FROM dashboard_configs WHERE department_code = %s
            ''', (dept_code,))

            existing = cursor.fetchone()

            if existing:
                config_id = existing[0]
                # Update existing config
                cursor.execute('''
                    UPDATE dashboard_configs
                    SET updated_at = CURRENT_TIMESTAMP, updated_by = %s
                    WHERE id = %s
                ''', (updated_by, config_id))

                # Delete existing widgets
                cursor.execute('DELETE FROM dashboard_widgets WHERE config_id = %s', (config_id,))
            else:
                # Create new config
                cursor.execute('''
                    INSERT INTO dashboard_configs (department_code, updated_by)
                    VALUES (%s, %s)
                    RETURNING id
                ''', (dept_code, updated_by))
                config_id = cursor.fetchone()[0]

            # Insert widgets
            for widget in widgets:
                cursor.execute('''
                    INSERT INTO dashboard_widgets (config_id, widget_id, widget_type, position)
                    VALUES (%s, %s, %s, %s)
                ''', (config_id, widget['id'], widget['type'], widget['position']))

            conn.commit()

            # Get the saved config to return
            cursor.execute('''
                SELECT id, department_code, updated_at, updated_by
                FROM dashboard_configs
                WHERE id = %s
            ''', (config_id,))

            config_row = cursor.fetchone()

            cursor.execute('''
                SELECT widget_id, widget_type, position
                FROM dashboard_widgets
                WHERE config_id = %s
                ORDER BY position
            ''', (config_id,))

            saved_widgets = []
            for row in cursor.fetchall():
                saved_widgets.append({
                    'id': row[0],
                    'type': row[1],
                    'position': row[2]
                })

            cursor.close()

            return {
                'success': True,
                'config': {
                    'id': config_row[0],
                    'department_code': config_row[1],
                    'widgets': saved_widgets,
                    'updated_at': config_row[2].isoformat() if config_row[2] else None,
                    'updated_by': config_row[3]
                }
            }

        except Exception as e:
            if conn:
                conn.rollback()
            return {'success': False, 'error': str(e)}
        finally:
            if conn:
                conn.close()

    # ==================== Widget Data ====================

    def get_widget_data(self, widget_type: str, department_code: str) -> Dict[str, Any]:
        """
        Get widget data for a specific widget type and department
        Returns mock data for now - can be connected to real data sources later
        """
        if widget_type not in self.WIDGET_TYPES:
            return {'success': False, 'error': f'Invalid widget type: {widget_type}'}

        # Mock data based on widget type
        # In production, this would query actual data sources
        widget_data = {
            'total_employees': {
                'value': 156,
                'label': 'Total Employees',
                'trend': {'direction': 'up', 'percentage': 3.2},
                'subtext': 'vs last month'
            },
            'active_employees': {
                'value': 148,
                'label': 'Active Employees',
                'trend': {'direction': 'up', 'percentage': 2.1},
                'subtext': 'vs last month'
            },
            'pending_approvals': {
                'value': 12,
                'label': 'Pending Approvals',
                'trend': {'direction': 'down', 'percentage': 8.5},
                'subtext': 'vs last week'
            },
            'total_commissions': {
                'value': '$45,230',
                'label': 'Total Commissions',
                'trend': {'direction': 'up', 'percentage': 12.4},
                'subtext': 'This month'
            },
            'monthly_sales': {
                'value': '$128,450',
                'label': 'Monthly Sales',
                'trend': {'direction': 'up', 'percentage': 7.8},
                'subtext': 'vs last month'
            },
            'open_positions': {
                'value': 8,
                'label': 'Open Positions',
                'trend': {'direction': 'neutral', 'percentage': 0},
                'subtext': 'Currently hiring'
            },
            'attendance_rate': {
                'value': '94.5%',
                'label': 'Attendance Rate',
                'trend': {'direction': 'up', 'percentage': 1.2},
                'subtext': 'This week'
            }
        }

        return {
            'success': True,
            'data': widget_data.get(widget_type, {
                'value': 0,
                'label': widget_type.replace('_', ' ').title(),
                'subtext': 'No data available'
            })
        }
