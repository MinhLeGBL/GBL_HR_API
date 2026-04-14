"""
Database initialization script.

Run this once during deployment to create all required tables and seed data.
Tables are created in dependency order.

Usage:
    python scripts/init_db.py
"""
import sys
import os

# Add project root to path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dotenv import load_dotenv

# Load environment based on FLASK_ENV
env = os.getenv('FLASK_ENV', 'development')
env_file = f'.env.{env}'
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

from app.modules.permissions.service import PermissionService
from app.modules.stores.service import StoreService
from app.modules.employees.service import HREmployeeService
from app.core.auth.service import AuthService
from app.modules.dashboard.service import DashboardService
from app.modules.commission.service import CommissionSettingsService, CommissionStoreSettingsService, CommissionRevenueService
from app.modules.bathroom.service import BathroomService

INIT_STEPS = [
    ('Permissions (departments, section_groups, sections, permissions)',
     PermissionService().init_permission_tables),
    ('Stores',
     StoreService().init_database),
    ('Stores sync from RetailPro',
     StoreService().sync_stores_from_retailpro),
    ('Employees (employee_types, contract_types, employees)',
     HREmployeeService().init_database),
    ('Auth (roles, users)',
     AuthService().init_database),
    ('Dashboard (dashboard_configs, dashboard_widgets)',
     DashboardService().init_dashboard_tables),
    ('Commission settings',
     CommissionSettingsService().init_database),
    ('Commission store settings',
     CommissionStoreSettingsService().init_database),
    ('Commission revenue adjustments',
     CommissionRevenueService().init_database),
    ('Bathroom products',
     BathroomService().init_database),
]


def main():
    print(f'Initializing database (FLASK_ENV={env})\n')

    all_ok = True
    for label, init_fn in INIT_STEPS:
        print(f'  [{label}] ... ', end='', flush=True)
        result = init_fn()
        if result.get('success'):
            print('OK')
        else:
            print(f'FAILED: {result.get("error", "unknown error")}')
            all_ok = False

    print()
    if all_ok:
        print('All tables initialized successfully.')
    else:
        print('Some steps failed. Check the output above.')
        sys.exit(1)


if __name__ == '__main__':
    main()
