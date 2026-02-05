# GBL HR API — Architecture Guide

Flask REST API for GBL company. Domain-based module architecture with shared core infrastructure.

## Project Layout

```
app/
  core/                    # Shared infrastructure — every module may import from here
    database/              # get_postgres_connection, get_oracle_connection
    auth/                  # AuthService, UserRole, JWT decorators (token_required, etc.)
    utils/                 # validators, formatters, decorators
  modules/                 # Feature modules — each has routes.py + service.py
    auth/                  # Login, token refresh, user CRUD
    commission/            # Commission calculations (store + personal)
    employees/             # HR employee management
    stores/                # Store management
    permissions/           # Departments, sections, role-based permissions
    dashboard/             # Dashboard config and widgets
    health/                # Health check endpoints
  main.py                  # Flask app factory, blueprint registration
config/                    # Environment-based settings (database.py, settings.py)
scripts/                   # One-off scripts (init_db.py)
tests/                     # Mirrors module structure
```

## Module Registry

| Module | Blueprint | URL Prefix | Service Class | Purpose |
|--------|-----------|------------|---------------|---------|
| `core/database` | — | — | — | PostgreSQL + Oracle connections, SSH tunnel |
| `core/auth` | — | — | `AuthService`, `UserRole` | JWT tokens, password hashing, auth decorators |
| `core/utils` | — | — | — | Date validators, formatters, request decorators |
| `modules/auth` | `auth_bp`, `user_bp` | `/api/v1/auth`, `/api/v1/users` | — (uses core AuthService) | Login, register, user CRUD |
| `modules/commission` | `commission_bp` | `/api/v1/commission` | `CommissionService`, `GoogleSheetsService` | Store + personal commission calculations |
| `modules/employees` | `hr_employee_bp` | `/api/v1/employees` | `HREmployeeService` | Employee CRUD, types, contracts |
| `modules/stores` | `store_bp` | `/api/v1/stores` | `StoreService` | Store CRUD |
| `modules/permissions` | `permission_bp` | `/api/v1` | `PermissionService` | Departments, sections, group permissions |
| `modules/dashboard` | `dashboard_bp` | `/api/v1/dashboard` | `DashboardService` | Dashboard config, widget data |
| `modules/health` | `health_bp` | `/api/v1/health` | — | API + database health checks |

## Import Rules

1. **Any module may import from `app.core`**
   ```python
   from app.core.database import get_postgres_connection
   from app.core.auth import AuthService, UserRole, token_required
   ```

2. **Cross-module imports: use the service class, never internals**
   ```python
   # Good — import the public API
   from app.modules.permissions import PermissionService

   # Bad — reaching into internals
   from app.modules.commission.repository import CommissionRepository
   ```

3. **Use lazy imports for cross-module dependencies** (avoids circular imports)
   ```python
   def some_method(self):
       from app.modules.permissions import PermissionService
       return PermissionService().get_user_permissions(...)
   ```

4. **Current cross-module dependencies:**
   - `core.auth.service` → `modules.permissions.service` (lazy import in `authenticate()`)

## Adding a New Module

1. Create directory: `app/modules/<name>/`
2. Create files:
   - `__init__.py` — re-export public API (blueprint + service class)
   - `routes.py` — Flask Blueprint with prefix `/api/v1/<name>`
   - `service.py` — Business logic class, imports `get_postgres_connection` from core
3. Register blueprint in `app/main.py`
4. If module needs database tables, add init step to `scripts/init_db.py`
5. Create test directory: `tests/unit/modules/<name>/` with `__init__.py`
6. Update this CLAUDE.md module registry table

### Blueprint naming convention
```python
<name>_bp = Blueprint('<name>', __name__, url_prefix='/api/v1/<name>')
```

### `__init__.py` pattern
```python
from .routes import <name>_bp
from .service import <Name>Service
```

## Testing

### Structure
```
tests/
  unit/
    core/                          # Core service tests
    modules/<name>/                # Module tests (routes + service)
  integration/                     # Integration tests (real DB)
  conftest.py                      # Shared fixtures
```

### Mock targets
Mock where the object is **looked up**, not where it's defined:
```python
# Route-level service mock
@patch('app.modules.stores.routes.store_service.get_all_stores')

# Auth middleware mock (for protected endpoints)
@patch('app.core.auth.middleware.auth_service.verify_token')
@patch('app.core.auth.middleware.auth_service.get_user_by_sid')

# Service-level DB mock
@patch('app.modules.stores.service.get_postgres_connection')
```

### Commands
```bash
# Run all unit tests
FLASK_ENV=testing pytest tests/ -x --ignore=tests/integration -v

# Import check
FLASK_ENV=testing python -c "from app.main import app; print('OK')"

# Init database tables
python scripts/init_db.py
```
