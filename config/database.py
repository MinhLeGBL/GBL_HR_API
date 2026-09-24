"""
Database and JWT configuration.

Loads environment variables from .env.{FLASK_ENV} (e.g. .env.development),
falling back to .env if the environment-specific file doesn't exist.
Sensitive values (credentials, secrets) have no defaults and will raise
ValueError at startup if missing.
"""
import os
from dotenv import load_dotenv

def _default_env():
    """Which .env file to load when FLASK_ENV is not set.

    'development', which since 2026-09-24 means THE LOCAL CONTAINERS, not the
    company's live database over an SSH tunnel. Development needs no network,
    no VPN and no tunnel.

    `.env.local` is still honoured if present, for a machine set up before the
    rename; it is no longer created.

    Production is reachable only by asking for it: FLASK_ENV=remote, which
    loads `.env.remote`. That is the only file holding live credentials, and it
    is named `remote` rather than `production` on purpose — several scripts in
    scripts/jobs/ default to FLASK_ENV=production, so that name would let a
    bare `python scripts/jobs/<anything>.py` on a laptop quietly reach the live
    database.

    The server is unaffected: it has neither file, so this returns
    'development', finds no `.env.development` there, and loads plain `.env`
    exactly as before.
    """
    explicit = os.getenv('FLASK_ENV')
    if explicit:
        return explicit
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(here, '.env.local')):
        return 'local'          # pre-rename machines
    return 'development'


# Load the correct env file.
#
# FLASK_ENV is deliberately NOT pinned here. An earlier version called
# os.environ.setdefault('FLASK_ENV', _env) at this point, which broke the
# SERVER: load_dotenv() does not overwrite a variable that already exists, so
# pinning 'development' first meant the server's own `.env`, which says
# FLASK_ENV=production, could never take effect. The app then believed it was
# in development while serving production traffic — silently, because nothing
# reads FLASK_ENV loudly.
#
# `_default_env()` decides WHICH FILE to read. What that file says about
# FLASK_ENV then stands.
_env = _default_env()
_env_file = f'.env.{_env}'
if os.path.exists(_env_file):
    load_dotenv(_env_file)
else:
    load_dotenv()  # fallback to .env


def _require(key: str) -> str:
    """Return an env var or raise if missing.

    In testing mode, returns a placeholder so unit tests (which mock all
    DB access) can import the app without real credentials.
    """
    value = os.getenv(key)
    if not value:
        if _env == 'testing':
            return 'test-placeholder'
        raise ValueError(f'Missing required environment variable: {key}')
    return value


# Oracle Database Configuration
DATABASE_CONFIG = {
    'host': _require('DB_HOST'),
    'port': os.getenv('DB_PORT', '1521'),
    'service_name': _require('DB_SERVICE_NAME'),
    'username': _require('DB_USERNAME'),
    'password': _require('DB_PASSWORD'),
    'encoding': os.getenv('DB_ENCODING', 'UTF-8')
}

# SSH Tunnel Configuration for PostgreSQL
# Only required when USE_SSH_TUNNEL=true (development); skipped in production.
_use_ssh = os.getenv('USE_SSH_TUNNEL', 'false').lower() == 'true'
SSH_CONFIG = {
    'ssh_host': _require('SSH_HOST') if _use_ssh else os.getenv('SSH_HOST', ''),
    'ssh_port': int(os.getenv('SSH_PORT', 22)),
    'ssh_username': _require('SSH_USERNAME') if _use_ssh else os.getenv('SSH_USERNAME', ''),
    'ssh_password': _require('SSH_PASSWORD') if _use_ssh else os.getenv('SSH_PASSWORD', ''),
    'local_port': int(os.getenv('SSH_LOCAL_PORT', 6543)),
    'remote_port': int(os.getenv('SSH_REMOTE_PORT', 5432))
}

# PostgreSQL Database Configuration (via SSH tunnel)
POSTGRES_CONFIG = {
    'username': _require('POSTGRES_USER'),
    'password': _require('POSTGRES_PASSWORD'),
    'database': _require('POSTGRES_DB'),
    'host': os.getenv('POSTGRES_HOST', 'localhost'),
    'port': int(os.getenv('POSTGRES_PORT', 5432))
}

# Connection Pool Settings
POOL_CONFIG = {
    'min_pool_size': int(os.getenv('DB_MIN_POOL_SIZE', 2)),
    'max_pool_size': int(os.getenv('DB_MAX_POOL_SIZE', 10)),
    'increment': int(os.getenv('DB_POOL_INCREMENT', 1))
}

# JWT Configuration
JWT_CONFIG = {
    'secret_key': _require('JWT_SECRET_KEY'),
    'algorithm': 'HS256',
    'access_token_expire_minutes': int(os.getenv('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', 480)),  # 8 hours
    'refresh_token_expire_days': int(os.getenv('JWT_REFRESH_TOKEN_EXPIRE_DAYS', 7))
}
