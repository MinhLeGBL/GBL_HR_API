import platform
import os
import threading
import oracledb
from config.database import DATABASE_CONFIG, POSTGRES_CONFIG, SSH_CONFIG

# Set once per process: oracledb.init_oracle_client() must not be called twice.
_thick_initialised = False
_thick_lock = threading.Lock()


def _instant_client_dir():
    """Where Instant Client lives, or None to run in thin mode.

    ORACLE_CLIENT_MODE=thin skips it entirely. Thin mode needs no Oracle
    libraries at all and supports Oracle 12.1 and later, which covers both
    production (12.1.0.2) and the local development container — so a new
    machine can run the app with nothing installed but Python.

    ORACLE_LIB_DIR names a directory explicitly. The defaults below are the
    paths that happen to exist on the two machines this has run on; they are
    a convenience, not a contract, which is why a missing one no longer raises.
    """
    if (os.getenv('ORACLE_CLIENT_MODE') or '').strip().lower() == 'thin':
        return None
    override = os.getenv('ORACLE_LIB_DIR')
    if override:
        return override or None

    if platform.system() == "Darwin" and platform.machine() == "x86_64":
        return os.path.join(os.environ.get("HOME", ""), "Downloads",
                            "instantclient_19_16")
    if platform.system() == "Windows":
        return r"E:\Oracle\instantclient_23_4"
    if platform.system() == "Linux":
        for d in ("/opt/oracle/instantclient_19_30",
                  "/opt/oracle/instantclient_19_16"):
            if os.path.exists(d):
                return d
        return "/opt/oracle/instantclient_19_30"
    return None


def get_oracle_connection():
    try:
        global _thick_initialised
        d = _instant_client_dir()

        # A missing Instant Client is no longer fatal. It used to raise, which
        # meant a machine without it at one hardcoded path could not run the
        # app at all — and thin mode would have worked fine. Falling through
        # to thin is strictly better than refusing to start.
        if d and os.path.exists(d) and not _thick_initialised:
            with _thick_lock:
                if not _thick_initialised:
                    oracledb.init_oracle_client(lib_dir=d)
                    _thick_initialised = True

        conn = oracledb.connect(
            user=DATABASE_CONFIG['username'],
            password=DATABASE_CONFIG['password'],
            host=DATABASE_CONFIG['host'],
            port=DATABASE_CONFIG['port'],
            service_name=DATABASE_CONFIG['service_name']
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute("ALTER SESSION SET CURRENT_SCHEMA = RPS")

            return conn
        except Exception as e:
            print(f"Failed to set default schema: {e}")
            conn.close()
            return None
    except FileNotFoundError as fnf_error:
        print(f"FileNotFoundError: {fnf_error}")
    except oracledb.DatabaseError as db_error:
        print(f"DatabaseError connecting to Oracle: {db_error}")
    except Exception as general_error:
        print(f"Unexpected error: {general_error}")
    return None


# Global SSH tunnel for development mode (guarded by _tunnel_lock)
_ssh_tunnel = None
_tunnel_lock = threading.Lock()
_MAX_TUNNEL_RETRIES = 3


def _stop_tunnel(tunnel):
    """Stop a tunnel and forcibly release its local port.

    Note: _server_list and _transport are private attrs tested with sshtunnel 0.4.x.
    """
    if tunnel is None:
        return
    # 1. Shutdown + close the internal TCPServer sockets FIRST (these hold the port)
    for server in getattr(tunnel, '_server_list', []):
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        # Last resort: close the raw socket fd
        for attr in ('socket', '_socket', 'server_socket'):
            try:
                sock = getattr(server, attr, None)
                if sock:
                    sock.close()
            except Exception:
                pass
    # 2. Close the SSH transport
    try:
        transport = getattr(tunnel, '_transport', None) or getattr(tunnel, 'ssh_transport', None)
        if transport:
            transport.close()
    except Exception:
        pass
    # 3. Now call tunnel.stop() for any remaining cleanup
    try:
        tunnel.stop()
    except Exception:
        pass


def _tunnel_is_healthy(tunnel):
    """Deep health check: tunnel active AND SSH transport alive."""
    if tunnel is None:
        return False
    try:
        if not tunnel.is_active:
            return False
        # sshtunnel uses _transport (private) or ssh_transport (older versions)
        transport = getattr(tunnel, '_transport', None) or getattr(tunnel, 'ssh_transport', None)
        return transport is not None and transport.is_active()
    except Exception:
        return False


def _create_tunnel():
    """Create and start a new SSH tunnel with keepalive enabled.

    Uses OS-assigned local port (port 0) to avoid fixed-port conflicts.
    The actual port is read from tunnel.local_bind_port after start().
    """
    from sshtunnel import SSHTunnelForwarder

    tunnel = SSHTunnelForwarder(
        (SSH_CONFIG['ssh_host'], SSH_CONFIG['ssh_port']),
        ssh_username=SSH_CONFIG['ssh_username'],
        ssh_password=SSH_CONFIG['ssh_password'],
        remote_bind_address=('localhost', SSH_CONFIG['remote_port']),
        local_bind_address=('localhost', 0),  # OS picks a free port
        allow_agent=False,
        host_pkey_directories=[],
        set_keepalive=30,  # Send keepalive every 30s to prevent silent drops
    )
    tunnel.start()

    # Double-check transport is actually connected
    if not _tunnel_is_healthy(tunnel):
        raise RuntimeError("Tunnel started but transport is not healthy")

    print(f"[OK] SSH tunnel established to {SSH_CONFIG['ssh_host']}:{SSH_CONFIG['ssh_port']}")
    print(f"  Local port: {tunnel.local_bind_port}")
    return tunnel


def _start_ssh_tunnel():
    """Start or reuse the global SSH tunnel for local development.

    Thread-safe: uses _tunnel_lock to prevent concurrent tunnel creation.
    Reuses an existing healthy tunnel; if stale, tears it down and retries.
    """
    global _ssh_tunnel

    with _tunnel_lock:
        # Reuse if healthy
        if _tunnel_is_healthy(_ssh_tunnel):
            return _ssh_tunnel

        # Stale or None — clean up before retry
        if _ssh_tunnel is not None:
            print("[WARN] SSH tunnel stale, forcing cleanup...")
            _stop_tunnel(_ssh_tunnel)
            _ssh_tunnel = None

        for attempt in range(1, _MAX_TUNNEL_RETRIES + 1):
            tunnel = None
            try:
                tunnel = _create_tunnel()
                _ssh_tunnel = tunnel
                return _ssh_tunnel
            except Exception as e:
                print(f"[WARN] SSH tunnel attempt {attempt}/{_MAX_TUNNEL_RETRIES} failed: {e}")
                _stop_tunnel(tunnel)
                import time
                time.sleep(1)  # Wait before retrying

        print("[ERROR] All SSH tunnel attempts exhausted")
        _ssh_tunnel = None
        return None


def _autocommit_requested():
    """Whether PG connections should commit each statement on its own.

    Set by `scripts/database/init_db.py` and nothing else. Schema creation runs
    a long series of independent, idempotent CREATE TABLE IF NOT EXISTS
    statements; in one transaction a single failure aborts the lot and rolls
    back even the tables that succeeded. Against an established database that
    never mattered, because nothing failed. Against an EMPTY one — a new
    developer machine, or rebuilding the server from nothing — the module
    foreign keys are circular, so something always fails and the whole pass is
    discarded. Committing per statement lets a second pass finish the job.
    """
    return (os.getenv('PG_AUTOCOMMIT') or '').strip().lower() in (
        'true', '1', 'yes', 'on')


def _make_pg_connection(host, port):
    """Create a PostgreSQL connection, trying psycopg2 then psycopg3.

    Import and connect are separated so a broken psycopg2 install
    (import succeeds but connect raises) doesn't silently fall through to psycopg.
    """
    try:
        import psycopg2
    except ImportError:
        psycopg2 = None

    if psycopg2 is not None:
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=POSTGRES_CONFIG['database'],
            user=POSTGRES_CONFIG['username'],
            password=POSTGRES_CONFIG['password'],
        )
        if _autocommit_requested():
            conn.autocommit = True
        return conn

    import psycopg
    conn = psycopg.connect(
        host=host,
        port=port,
        dbname=POSTGRES_CONFIG['database'],
        user=POSTGRES_CONFIG['username'],
        password=POSTGRES_CONFIG['password'],
    )
    if _autocommit_requested():
        conn.autocommit = True
    return conn


def get_postgres_connection():
    """
    Connect to PostgreSQL database.
    Uses SSH tunnel if USE_SSH_TUNNEL=true in environment.

    Self-healing: if the DB connection fails through an existing tunnel,
    the tunnel is torn down and recreated once before giving up.

    Returns:
        connection object if successful, None otherwise
    """
    use_ssh = os.getenv('USE_SSH_TUNNEL', 'false').lower() == 'true'

    if not use_ssh:
        # Direct connection — no retry logic needed
        try:
            conn = _make_pg_connection(POSTGRES_CONFIG['host'], POSTGRES_CONFIG['port'])
            print(f"[OK] Connected to PostgreSQL at {POSTGRES_CONFIG['host']}:{POSTGRES_CONFIG['port']}")
            return conn
        except ImportError:
            print("ImportError: PostgreSQL driver not installed")
            print("  Please install: pip install psycopg2-binary")
        except Exception as error:
            print(f"Error connecting to PostgreSQL: {error}")
        return None

    # --- SSH tunnel path with self-healing retry ---
    for attempt in range(1, _MAX_TUNNEL_RETRIES + 1):
        tunnel = _start_ssh_tunnel()
        if not tunnel:
            break

        try:
            conn = _make_pg_connection('localhost', tunnel.local_bind_port)
            print(f"[OK] Connected to PostgreSQL via SSH tunnel (localhost:{tunnel.local_bind_port})")
            return conn
        except ImportError:
            print("ImportError: PostgreSQL driver not installed")
            print("  Please install: pip install psycopg2-binary")
            return None
        except Exception as error:
            print(f"[WARN] DB connection attempt {attempt}/{_MAX_TUNNEL_RETRIES} failed: {error}")
            # Tunnel might be half-dead — tear it down so next loop rebuilds it
            global _ssh_tunnel
            with _tunnel_lock:
                _stop_tunnel(_ssh_tunnel)
                _ssh_tunnel = None

    print("[ERROR] Could not connect to PostgreSQL via SSH tunnel")
    return None


def get_postgres_connection_ssh():
    """
    Connect to PostgreSQL database through SSH tunnel.

    Returns:
        tuple: (tunnel, connection) if successful, (None, None) otherwise
        Both tunnel and connection should be closed when done.
    """
    tunnel = None
    try:
        tunnel = _create_tunnel()
        conn = _make_pg_connection('localhost', tunnel.local_bind_port)
        print(f"[OK] Connected to PostgreSQL through SSH tunnel")
        return tunnel, conn
    except ImportError as e:
        print(f"ImportError: Required library not installed — {e}")
        print("  Please install: pip install sshtunnel psycopg2-binary")
        _stop_tunnel(tunnel)
        return None, None
    except Exception as error:
        print(f"Error connecting to PostgreSQL through SSH: {error}")
        _stop_tunnel(tunnel)
        return None, None
