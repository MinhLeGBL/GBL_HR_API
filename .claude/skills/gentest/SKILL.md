---
name: gentest
description: Auto-generate unit tests for changed services and routes on the current feature branch
---

Generate unit tests for all module files that have changed on the current feature branch but don't yet have corresponding unit tests.

**Branch-test strategy:** Each feature branch only carries tests for its own modules + core tests. Only generate tests for modules that belong to the current branch. Do NOT generate tests for modules owned by other feature branches — those tests will exist on their respective branches and merge into staging.

## Step 1: Identify changed files

Run this command to find files changed on the current branch compared to deployment:

```bash
git diff --name-only deployment...HEAD
```

Filter the output to only files matching the domain-based module structure:
- `app/modules/*/service.py` (module service files)
- `app/modules/*/routes.py` (module route files)
- `app/modules/*/user_routes.py` (additional route files like auth user_routes)
- `app/modules/*/repository.py` (repository files)
- `app/modules/*/sheets_service.py` (external service integrations)
- `app/core/auth/service.py` (core auth service)
- `app/core/auth/middleware.py` (core auth middleware)

Ignore `__init__.py` files.

If no module files have changed, report "No module changes detected on this branch" and stop.

## Step 2: Check for existing tests

For each changed file, check if a corresponding test file already exists. Tests mirror the module structure:

- `app/modules/<name>/service.py` → `tests/unit/modules/<name>/test_<name>_service.py`
- `app/modules/<name>/routes.py` → `tests/unit/modules/<name>/test_<name>_routes.py`
- `app/modules/<name>/user_routes.py` → `tests/unit/modules/<name>/test_user_routes.py`
- `app/modules/<name>/repository.py` → `tests/unit/modules/<name>/test_<name>_repository.py`
- `app/modules/<name>/sheets_service.py` → `tests/unit/modules/<name>/test_sheets_service.py`
- `app/core/auth/service.py` → `tests/unit/core/test_auth_service.py`
- `app/core/auth/middleware.py` → `tests/unit/core/test_auth_middleware.py`

Skip files that already have tests. Report which files already have coverage and which are missing.

If all changed files already have tests, report "All changed files have test coverage" and stop.

## Step 3: Set up test infrastructure (if needed)

If `tests/conftest.py` is empty or missing shared fixtures, populate it with the current project fixtures:

```python
import pytest
from unittest.mock import MagicMock, patch
from app.main import create_app


@pytest.fixture
def app():
    """Create Flask test app."""
    app = create_app()
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    """Create Flask test client."""
    return app.test_client()


@pytest.fixture
def mock_pg_connection():
    """Mock PostgreSQL connection and cursor."""
    with patch('app.core.database.connection.get_postgres_connection') as mock_get_conn:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        yield mock_conn, mock_cursor


@pytest.fixture
def mock_oracle_connection():
    """Mock Oracle database connection and cursor."""
    with patch('app.core.database.connection.get_oracle_connection') as mock_get_conn:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn
        yield mock_conn, mock_cursor


@pytest.fixture
def mock_auth_token():
    """Mock a valid JWT token and user for protected endpoints."""
    user_data = {
        'sid': 1,
        'email': 'test@example.com',
        'role': 'admin',
        'role_code': 'ADMIN',
        'full_name': 'Test User',
        'department_id': 1,
        'department_code': 'IT'
    }
    with patch('app.core.auth.service.AuthService.verify_token') as mock_verify:
        mock_verify.return_value = user_data
        yield user_data
```

Create test directories with `__init__.py` if they don't exist. The test directory structure mirrors the module structure:
- `tests/unit/__init__.py`
- `tests/unit/core/__init__.py`
- `tests/unit/modules/__init__.py`
- `tests/unit/modules/<name>/__init__.py` (for each module being tested)

## Step 4: Read source files and generate tests

For each changed file that needs tests:

1. **Read the source file** to understand all classes, methods, and their logic
2. **Read existing test files in the same module directory** to understand patterns already used
3. **Generate a test file** following these rules:

### Mock target rules (CRITICAL)

Mock where the object is **looked up**, not where it's defined:

```python
# Route tests — mock the module-level service instance
@patch('app.modules.stores.routes.store_service.get_all_stores')

# Route tests — mock auth middleware for protected endpoints
@patch('app.core.auth.middleware.auth_service.verify_token')
@patch('app.core.auth.middleware.auth_service.get_user_by_sid')

# Service tests — mock the database connection at the module level
@patch('app.modules.stores.service.get_postgres_connection')

# Core auth service tests — mock at the core module level
@patch('app.core.auth.service.get_postgres_connection')
```

### Service test rules (`tests/unit/modules/<name>/test_<name>_service.py`):
- Mock database connections using `@patch('app.modules.<name>.service.get_postgres_connection')`
- Mock external services (Google Sheets, etc.)
- Test each public method of the service class
- Test success paths: correct return values, expected data structures
- Test error paths: invalid input, database errors, not-found cases
- Assert on the `success` field and key data in the response dict
- Use `@patch` decorators to mock dependencies

### Route test rules (`tests/unit/modules/<name>/test_<name>_routes.py`):
- Use the Flask test `client` fixture from conftest
- Mock the service layer using `@patch('app.modules.<name>.routes.<service_instance>.<method>')` — don't mock the database directly
- Mock auth middleware: `@patch('app.core.auth.middleware.auth_service.verify_token')` and `@patch('app.core.auth.middleware.auth_service.get_user_by_sid')`
- For protected endpoints, `verify_token` should return a user dict (see `mock_auth_token` fixture), and `get_user_by_sid` should return a user dict with at minimum `sid`, `role`, `role_code`, `department_code`
- Test each endpoint: correct HTTP method, URL, status code, response body
- Test authentication/authorization where `token_required` or role decorators are used
- Test both valid and invalid request scenarios
- Assert on status codes (200, 201, 400, 401, 403, 404, 500) and response JSON structure

### Core auth test rules (`tests/unit/core/test_auth_service.py`):
- Mock database: `@patch('app.core.auth.service.get_postgres_connection')`
- Mock bcrypt/jwt operations as needed
- Test `authenticate`, `register`, `verify_token`, `refresh_token`, etc.

### General rules:
- Every test file must be importable and runnable independently
- Use descriptive test names: `test_{method_name}_{scenario}` (e.g., `test_authenticate_invalid_password`)
- Don't test private methods (those starting with `_`) directly
- Keep mocks minimal — only mock what's necessary for the test to run without real databases
- Use `FLASK_ENV=testing` when running tests
- Add `Authorization: Bearer test-token` header for protected endpoint tests

## Step 5: Run tests

Run the test suite to verify all generated tests pass:

```bash
FLASK_ENV=testing pytest tests/ -x --ignore=tests/integration -v
```

If any tests fail, read the error output, fix the failing tests, and re-run until all pass.

## Step 6: Report summary

Report:
- Branch name and base comparison
- Number of changed module files found
- Which files already had tests (skipped)
- Which test files were generated (new)
- Test run results (pass/fail count)
