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
    bathroom/              # Bathroom product catalog (Dolomite, Valsir, Paffoni)
    crm/                   # Customer RFM segmentation (luxury hybrid approach)
    sale_through/          # Sale-through report (imported / sold / on-hand by brand × season × category)
  main.py                  # Flask app factory, blueprint registration
config/                    # Environment-based settings (database.py, settings.py)
scripts/                   # Operational scripts
  database/                # DB init & migrations (init_db.py)
  deployment/              # Deployment helpers
tests/                     # Mirrors module structure
```

## Branch & Version Conventions

Mirrors the org-wide convention in [GBL_HR_Frontend/docs/API_REFERENCE.md](../GBL_HR_Frontend/docs/API_REFERENCE.md) — source of truth for both repos.

### Long-lived feature branches
- One branch per module: `feature/<module_name>` (no version suffix — e.g. `feature/crm`, NOT `feature/crm_2.0`)
- Branch name mirrors `app/modules/<module_name>/`
- Branches are **never deleted** after merging — the same branch is reused for every iteration of that feature

### Per-module version file
Each module's `__init__.py` exports its current shipped version:
```python
__version__ = '1.0.0'
```
Format: 3-part semver (`MAJOR.MINOR.PATCH`). Backend and frontend versions are independent per feature.

| Change | Bump | PR title prefix |
|--------|------|-----------------|
| Breaking change / rewrite | MAJOR | `feat!:` |
| New endpoint / feature | MINOR | `feat:` |
| Bug fix / refactor / small tweak | PATCH | `fix:` or `chore:` |

### Workflow
1. Work directly on `feature/<name>` (no sub-branches per change).
2. Before opening a PR to staging, sync with staging: `git fetch origin && git merge origin/staging`. PR diff must be feature-only — a branch behind staging should not be merged.
3. Bump `__version__` in the same PR that ships the change.
4. PR title prefix matches the bump type (table above).
5. **Squash-merge** into staging. Do NOT delete the branch — it lives on for the next iteration.

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
| `modules/bathroom` | `bathroom_bp` | `/api/v1/bathroom` | `BathroomService` | Product catalog, price data import (Dolomite, Valsir, Paffoni) |
| `modules/crm` | `crm_bp` | `/api/v1/crm` | `CRMService` | Customer RFM scoring + 7-segment classification (luxury hybrid) |
| `modules/sale_through` | `sale_through_bp` | `/api/v1/sale-through` | `SaleThroughService` | Sale-through report: imported / sold / on-hand by brand × season × category (Oracle read-only) |

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
   - `__init__.py` — declares `__version__` + re-exports public API (blueprint + service class)
   - `routes.py` — Flask Blueprint with prefix `/api/v1/<name>`
   - `service.py` — Business logic class, imports `get_postgres_connection` from core
3. Register blueprint in `app/main.py`
4. If module needs database tables, add init step to `scripts/database/init_db.py`
5. Create test directory: `tests/unit/modules/<name>/` with `__init__.py`
6. Update this CLAUDE.md module registry table
7. Create the long-lived feature branch `feature/<name>` from staging (never deleted — see Branch & Version Conventions above)

### Blueprint naming convention
```python
<name>_bp = Blueprint('<name>', __name__, url_prefix='/api/v1/<name>')
```

### `__init__.py` pattern
```python
__version__ = '1.0.0'

from .routes import <name>_bp
from .service import <Name>Service
```

## Testing

### Branch-Test Strategy

Feature branches are long-lived (see Branch & Version Conventions). The full test
suite lives on every branch — never delete tests when iterating on a feature
branch — additive merges only.

| Branch | Test files | What CI runs |
|--------|-----------|--------------|
| `feature/<name>` | All tests | Core tests + path-filtered module tests (only modules touched by the diff) |
| `staging` | All tests | Full regression suite |
| `deployment` | All tests in git | None — `tests/` removed from server by `deploy.yml` |

**When creating a new feature branch from staging:**
1. Branch as normal — do NOT delete or prune any test directories
2. If the feature is for an existing module, add new tests to `tests/unit/modules/<name>/`
3. If the feature is a new module, create `tests/unit/modules/<name>/` with `__init__.py`
4. After creating a new module, update the path filter in [.github/workflows/feature.yml](.github/workflows/feature.yml) so CI knows about it

**When merging a feature branch back to staging:**
- Test additions and modifications merge naturally — no special handling needed
- Test removals require deliberate justification (test was wrong, code was deleted, etc.)
- Removal commits must use the prefix `test(remove):` so reviewers can spot them in `git log`

**CI behavior:**
- `feature.yml` — uses path filtering: always runs `tests/unit/core/`, plus tests for modules whose source files changed in the diff
- `staging.yml` — runs full regression (all tests, every push to staging)
- `deploy.yml` — no tests run; `tests/` removed from server after pull

### Structure
```
tests/
  unit/
    core/                          # Core tests (always run by CI)
    modules/<name>/                # Module tests (run when module source changes)
  integration/                     # Integration tests (real DB, not run in CI)
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

| Branch | CR file |
|--------|---------|
| `feature/commission` | `cr/commission.md` |
| `feature/employees` | `cr/employees.md` |
| `feature/permissions` | `cr/permissions.md` |
| `feature/users` or `feature/auth` | `cr/users.md` |
| `feature/bathroom` | `cr/bathroom-price-check.md` |
| `feature/crm` | `cr/crm.md` |
| `feature/sale_through` | `cr/sale-through.md` |

### Status markers

| Marker | Meaning |
|--------|---------|
| ⏳ | Requested by frontend — pending backend implementation |
| Done | Implemented and confirmed |

### When implementing a CR

1. **Respond in the pending section** of `docs/cr/<feature>.md` — add actual response shapes, implementation notes, mark ⏳ → Done
2. **Do NOT move CRs to the archive section** — the frontend team handles confirmation and archival
3. **Update `docs/API_REFERENCE.md`** — add endpoint to tables, update change log ⏳ → Done
4. **Bump version in both places**:
   - `__version__` in `app/modules/<feature>/__init__.py` (per the bump table — MAJOR/MINOR/PATCH)
   - **Backend** cell in the version table at the top of `docs/cr/<feature>.md` so the frontend can see what version their integration is talking to
5. **Never remove frontend-written sections** — only annotate with actual implementation details
6. **Document deviations** — if actual implementation differs from the request (field names, types, extra fields)

### Skills

- `/checkcr` — Reads the CR file for the current branch, checks pending items against backend code
- `/responsecr` — Updates the CR file for the current branch to reflect backend implementation status
- `/importbathroom` — Check bathroom data import status, then prompt for next action (import collection, update prices, reimport from source)

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

