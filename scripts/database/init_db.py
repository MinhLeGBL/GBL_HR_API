"""
Database initialization script.

Run this once during deployment to create all required tables and seed data.
Tables are created in dependency order.

Usage:
    python scripts/database/init_db.py
"""
import sys
import os

# Add project root to path so imports work. Two levels up, not one: this script
# lives at scripts/database/, so '..' is scripts/ and every `from app...` import
# fails with ModuleNotFoundError.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

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
from app.modules.crm.service import CRMService
from app.modules.handcarry.service import HandCarryService
from app.modules.account_payable.service import AccountPayableService
from app.modules.reports.service import ReportsService
from app.modules.weekly_report.service import WeeklyReportService

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
    ('CRM (customer scores, segment snapshots)',
     CRMService().init_database),
    ('Hand-carry catalog (rps.carrier_item extended schema)',
     HandCarryService().init_database),
    ('Account Payable (payable_reconciliations, payable_item_custom_rates)',
     AccountPayableService().init_database),
    ('Reports (live_comparison_pins)',
     ReportsService().init_database),
    ('Periodic Report (runs, recipients, settings, sends)',
     WeeklyReportService().init_database),
]


# Commit each DDL statement on its own. The module foreign keys are circular
# (Permissions references `roles`, Auth references `departments`), so on an
# EMPTY database some statement in every pass is guaranteed to fail — and in a
# single transaction that failure discards the tables the pass DID create,
# leaving repeated passes to make no progress at all. See main().
os.environ.setdefault('PG_AUTOCOMMIT', 'true')


def main():
    """Create every table, in two passes.

    TWO PASSES, BECAUSE THE FOREIGN KEYS ARE CIRCULAR.
    The Permissions step's DDL references `roles`, which Auth creates; Auth's
    references `departments`, which Permissions creates. No single ordering
    satisfies both, so the first pass creates whatever it can and the second
    completes the rest.

    This never showed up while the only database was production, where the
    tables already existed and every run was incremental. It appears the moment
    the script meets an EMPTY database — a new developer machine, or restoring
    the server from nothing, which is the worse moment to discover it.

    A step that fails on pass 1 is not an error. A step that fails on pass 2 is.
    """
    print(f'Initializing database (FLASK_ENV={env})\n')

    pending = list(INIT_STEPS)
    for attempt in (1, 2):
        if attempt == 2:
            if not pending:
                break
            print(f'\n  -- pass 2, for the {len(pending)} step(s) whose tables '
                  f'did not exist yet --\n')
        still_failing = []
        for label, init_fn in pending:
            print(f'  [{label}] ... ', end='', flush=True)
            try:
                result = init_fn()
            except Exception as e:                              # noqa: BLE001
                result = {'success': False, 'error': str(e).splitlines()[0]}
            if result.get('success'):
                print('OK')
            else:
                error = result.get('error', 'unknown error')
                print('deferred' if attempt == 1 else f'FAILED: {error}')
                still_failing.append((label, init_fn))
        pending = still_failing

    print()
    if not pending:
        print('All tables initialized successfully.')
    else:
        print(f'{len(pending)} step(s) still failing after two passes:')
        for label, _ in pending:
            print(f'  - {label}')
        sys.exit(1)


if __name__ == '__main__':
    main()
