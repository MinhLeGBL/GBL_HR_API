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
    handcarry/             # Hand-carry UPC list management (sub-feature of commission)
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
| `modules/bathroom` | `bathroom_bp` | `/api/v1/bathroom` | `BathroomService` | Product catalog, price data import (Dolomite, Valsir, Paffoni) |
| `modules/crm` | `crm_bp` | `/api/v1/crm` | `CRMService` | Customer RFM scoring + 7-segment classification (luxury hybrid) |
| `modules/handcarry` | `handcarry_bp` | `/api/v1/handcarry` | `HandCarryService` | Hand-carry UPC list (rps.carrier_item) — list / bulk import / edit / delete |
| `modules/account_payable` | `account_payable_bp` | `/api/v1/account-payable` | `AccountPayableService` | Manual reconciliation tool — closes CR #59 Phase C release tracking (Phase 1 scaffold, endpoints return 501) |

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

The full test suite lives on every branch. Never delete tests when creating or
merging a feature branch — additive merges only.

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

## CR System (API Change Requests) — symmetric model (CR #74)

The two repos exchange change requests symmetrically. Each repo's `docs/cr/`
holds its **outgoing** asks; the fulfiller reads the requester's repo.

```
GBL_HR_API/                              GBL_MASTER_FRONTEND/
├── docs/cr/<feature>.md                 ├── docs/cr/<feature>.md
│   (backend → frontend outgoing,        │   (frontend → backend outgoing,
│    pending only; feature branch only)  │    pending only; feature branch only)
└── app/modules/<x>/CHANGELOG.md         └── docs/changes/<feature>.md
    (backend's own change log)               (frontend's own change log)
```

### Branch isolation

- **CR files** (`docs/cr/*`) exist on their **own feature branch only**.
  Stripped at deploy by [.github/workflows/deploy.yml](.github/workflows/deploy.yml)
  alongside `tests/` and `document/`. Don't push CR files onto `staging` or
  `deployment` via PR merges — if one leaks, revert in a follow-up.
- **Backend's CHANGELOG.md per module** persists on every branch and into deployment.

### Feature-to-file mapping

| Branch pattern | Incoming CR file (frontend repo) |
|----------------|---------------------------------|
| `feature/commission*` | `cr/commission.md` |
| `feature/employee*` | `cr/employees.md` |
| `feature/permission*` | `cr/permissions.md` |
| `feature/user*` or `feature/auth*` | `cr/users.md` |
| `feature/bathroom*` | `cr/bathroom-price-check.md` |
| `feature/crm*` | `cr/crm.md` |
| `feature/handcarry*` | `cr/handcarry.md` |
| `feature/account-payable*` | `cr/account-payable.md` |

Same key for backend's outgoing CRs in this repo's `docs/cr/<feature>.md`.

### CR file structure

After CR #74 migration, each `docs/cr/<feature>.md` holds **only pending/active
requests**. Completed CRs move out:
- Frontend → `docs/changes/<feature>.md`
- Backend → its module's `CHANGELOG.md` + `__version__` bump

During the transition some files may still carry the legacy
`# ═══ Completed CRs (Archive) ═══` separator. The skills handle both
shapes transparently — see `.claude/skills/checkcr/SKILL.md`.

### Status markers

| Marker | Meaning |
|--------|---------|
| ⏳ | Pending the recipient's implementation |
| Done | Implemented and confirmed |

### When implementing an incoming CR (frontend → backend)

1. **Respond in the pending section** of the frontend's `docs/cr/<feature>.md` —
   add actual response shapes, implementation notes, mark ⏳ → Done
2. **Do NOT move CRs to the archive section** if one is still present — the
   frontend team handles confirmation and migration to `docs/changes/`
3. **Update the module CHANGELOG.md** + bump `__version__` to record the change
4. **Never remove frontend-written sections** — only annotate with actual details
5. **Document deviations** — different field names, extra fields, changed auth, etc.

### When raising an outgoing CR (backend → frontend)

1. Write the request in this repo's `docs/cr/<feature>.md` on the relevant
   feature branch. Mark ⏳.
2. Frontend reads cross-repo, responds inline in the same file, then logs the
   resulting frontend change in their `docs/changes/<feature>.md`.
3. Once green, delete the entry from this repo's `docs/cr/<feature>.md` — the
   record now lives in the receiver's change log.

### Skills

- `/checkcr` — Reads the frontend's CR file for the current branch, checks pending items against backend code
- `/responsecr` — Updates the frontend's CR file for the current branch with backend implementation status
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

