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
scripts/                   # Operational scripts
  database/                # DB init & migrations (init_db.py)
  deployment/              # Deployment helpers
tests/                     # Mirrors module structure
```

## Module Registry

| Module | Blueprint | URL Prefix | Service Class | Purpose |
|--------|-----------|------------|---------------|---------|
| `core/database` | — | — | — | PostgreSQL + Oracle connections, SSH tunnel |
| `core/auth` | — | — | `AuthService`, `UserRole` | JWT tokens, password hashing, auth decorators |
| `core/utils` | — | — | — | Date validators, formatters, request decorators |
| `modules/auth` | `auth_bp`, `user_bp` | `/api/v1/auth`, `/api/v1/users` | — (uses core AuthService) | Login, register, user CRUD |
| `modules/commission` | `commission_bp` | `/api/v1/commission` | `CommissionService` | Store + personal commission calculations |
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
4. If module needs database tables, add init step to `scripts/database/init_db.py`
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

### Branch-Test Strategy

Each branch only carries tests relevant to its scope:

| Branch | Tests included | Purpose |
|--------|---------------|---------|
| `feature/<name>` | `tests/unit/core/` + `tests/unit/modules/<name>/` only | Feature-specific tests + shared core |
| `staging` | All tests from all feature branches | Full regression suite |
| `deployment` | Tests in git but removed from server by deploy.yml | Production has no test files |

**When creating a new feature branch from staging:**
1. Remove test directories for modules you're NOT working on
2. Keep `tests/conftest.py`, `tests/unit/core/`, and `tests/unit/modules/__init__.py`
3. Create `tests/unit/modules/<your-module>/` for your new tests

**CI behavior:**
- `feature.yml` — runs only the tests present on the feature branch
- `staging.yml` — runs full regression (all module tests merged together)
- `deploy.yml` — no tests run; `tests/` removed from server after pull

### Structure
```
tests/
  unit/
    core/                          # Core tests (kept on all feature branches)
    modules/<name>/                # Module tests (only on owning feature branch)
  integration/                     # Integration tests (real DB)
  conftest.py                      # Shared fixtures (kept on all branches)
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

**Windows (dev machine):** Always activate the venv first — `python`, `python3`, and `py` are unreliable on this machine. Use:
```bash
source venv/Scripts/activate && FLASK_ENV=testing pytest tests/ -x --ignore=tests/integration -v
source venv/Scripts/activate && FLASK_ENV=testing python -c "from app.main import app; print('OK')"
source venv/Scripts/activate && python scripts/database/init_db.py
```

**Linux/CI (deployment server):** Standard commands work as-is:
```bash
FLASK_ENV=testing pytest tests/ -x --ignore=tests/integration -v
FLASK_ENV=testing python -c "from app.main import app; print('OK')"
python scripts/database/init_db.py
```

## Frontend CR Documentation (API Change Requests)

The frontend project (`GBL_HR_Frontend`) uses per-feature CR files to communicate API requirements:

```
GBL_HR_Frontend/docs/
├── API_REFERENCE.md          # Slim index: architecture, endpoint tables, change log
└── cr/
    ├── commission.md          # Commission feature
    ├── employees.md           # Employee management
    ├── permissions.md         # Permissions & access
    └── users.md               # User management & auth
```

### CR file structure

Each CR file has two sections separated by `# ═══ Completed CRs (Archive) ═══`:

1. **Pending section** (top) — active specs, ⏳ items, current endpoint docs
2. **Completed section** (bottom) — archived summaries of done CRs

### Branch-sensitive CR checking

CR files are feature-scoped. Only read/update the CR file matching the current branch:

| Branch pattern | CR file |
|----------------|---------|
| `feature/commission*` | `cr/commission.md` |
| `feature/employee*` | `cr/employees.md` |
| `feature/permission*` | `cr/permissions.md` |
| `feature/user*` or `feature/auth*` | `cr/users.md` |

### Status markers

| Marker | Meaning |
|--------|---------|
| ⏳ | Requested by frontend — pending backend implementation |
| Done | Implemented and confirmed |

### When implementing a CR

1. **Respond in the pending section** of `docs/cr/<feature>.md` — add actual response shapes, implementation notes, mark ⏳ → Done
2. **Do NOT move CRs to the archive section** — the frontend team handles confirmation and archival
3. **Update `docs/API_REFERENCE.md`** — add endpoint to tables, update change log ⏳ → Done
4. **Never remove frontend-written sections** — only annotate with actual implementation details
5. **Document deviations** — if actual implementation differs from the request (field names, types, extra fields)

### Skills

- `/checkcr` — Reads the CR file for the current branch, checks pending items against backend code
- `/responsecr` — Updates the CR file for the current branch to reflect backend implementation status

## Running the API Locally (dev/staging/feature branches)

### Kill stale processes and free ports first

Before starting or restarting the API locally, **always kill processes on port 5200 (API) and port 6543 (SSH tunnel)**. Stale tunnel listeners cause "Couldn't open tunnel localhost:6543" errors.

```bash
# macOS — kill by port
lsof -ti:5200 | xargs kill -9 2>/dev/null
lsof -ti:6543 | xargs kill -9 2>/dev/null

# Windows — kill all python processes
taskkill //F //IM python.exe

# Verify ports are free
# macOS:
lsof -i:5200 -i:6543
# Windows:
netstat -ano | grep ":5200\|:6543" | grep LISTEN
```

### Start the API

```bash
# macOS (background, no reloader to avoid port conflicts)
nohup .venv/bin/python -c "from app.main import app; app.run(host='0.0.0.0', port=5200, debug=False, use_reloader=False)" > /tmp/gbl_api.log 2>&1 &

# Windows
cd "e:/Git Project/GBL_HR_API" && source venv/Scripts/activate && FLASK_ENV=development python app.py
```

The SSH tunnel starts lazily on the first PostgreSQL request.

### Verify API and tunnel health after startup

On `staging` and `feature/*` branches, `USE_SSH_TUNNEL=true` is required for PostgreSQL. After starting, verify:

```bash
# 1. Health check (no DB needed)
curl -s http://127.0.0.1:5200/api/v1/health/

# 2. DB-backed endpoint — should return "Invalid email or password" (not a tunnel error)
curl -s -X POST http://127.0.0.1:5200/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@test.com","password":"test"}'
```

A tunnel error looks like: `{"error": "Server is not started. Please .start() first!"}` or `"Couldn't open tunnel localhost:6543 <> localhost:5432 might be in use"`

If you see a tunnel error:
1. Kill processes on ports 5200 and 6543 (see commands above)
2. Confirm both ports are free
3. Restart the API

