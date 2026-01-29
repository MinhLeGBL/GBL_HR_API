import platform
import os
import oracledb
from config.database import DATABASE_CONFIG, POSTGRES_CONFIG, SSH_CONFIG

def get_oracle_connection():
    try:
        d = None  # default suitable for Linux
        if platform.system() == "Darwin" and platform.machine() == "x86_64":  # macOS
            d = os.environ.get("HOME") + ("/Downloads/instantclient_19_16")
        elif platform.system() == "Windows":
            d = r"W:\Oracle\instantclient_23_4"

        if d and os.path.exists(d):
            oracledb.init_oracle_client(lib_dir=d)
        elif d:
            raise FileNotFoundError(f"Oracle Instant Client not found at: {d}")

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


# Global SSH tunnel for development mode
_ssh_tunnel = None


def _start_ssh_tunnel():
    """Start SSH tunnel for local development"""
    global _ssh_tunnel
    if _ssh_tunnel is not None:
        return _ssh_tunnel

    try:
        from sshtunnel import SSHTunnelForwarder

        _ssh_tunnel = SSHTunnelForwarder(
            (SSH_CONFIG['ssh_host'], SSH_CONFIG['ssh_port']),
            ssh_username=SSH_CONFIG['ssh_username'],
            ssh_password=SSH_CONFIG['ssh_password'],
            remote_bind_address=('localhost', SSH_CONFIG['remote_port']),
            local_bind_address=('localhost', SSH_CONFIG['local_port'])
        )
        _ssh_tunnel.start()
        print(f"✓ SSH tunnel established to {SSH_CONFIG['ssh_host']}:{SSH_CONFIG['ssh_port']}")
        print(f"  Local port: {_ssh_tunnel.local_bind_port}")
        return _ssh_tunnel
    except Exception as e:
        print(f"Failed to start SSH tunnel: {e}")
        return None


def get_postgres_connection():
    """
    Connect to PostgreSQL database.
    Uses SSH tunnel if USE_SSH_TUNNEL=true in environment.

    Returns:
        connection object if successful, None otherwise
    """
    use_ssh = os.getenv('USE_SSH_TUNNEL', 'false').lower() == 'true'

    host = POSTGRES_CONFIG['host']
    port = POSTGRES_CONFIG['port']

    # If using SSH tunnel, start it and use local port
    if use_ssh:
        tunnel = _start_ssh_tunnel()
        if tunnel:
            host = 'localhost'
            port = tunnel.local_bind_port
        else:
            print("Warning: SSH tunnel failed, trying direct connection...")

    try:
        # Try to import psycopg2 first, then fall back to psycopg
        try:
            import psycopg2

            conn = psycopg2.connect(
                host=host,
                port=port,
                database=POSTGRES_CONFIG['database'],
                user=POSTGRES_CONFIG['username'],
                password=POSTGRES_CONFIG['password']
            )
            if use_ssh:
                print(f"✓ Connected to PostgreSQL via SSH tunnel (localhost:{port})")
            else:
                print(f"✓ Successfully connected to PostgreSQL at {host}:{port}")
            return conn

        except ImportError:
            # Try psycopg (version 3)
            import psycopg

            conn = psycopg.connect(
                host=host,
                port=port,
                dbname=POSTGRES_CONFIG['database'],
                user=POSTGRES_CONFIG['username'],
                password=POSTGRES_CONFIG['password']
            )
            if use_ssh:
                print(f"✓ Connected to PostgreSQL via SSH tunnel (localhost:{port})")
            else:
                print(f"✓ Successfully connected to PostgreSQL at {host}:{port}")
            return conn

    except ImportError as import_error:
        print(f"ImportError: PostgreSQL driver not installed")
        print(f"  Please install: pip install psycopg2-binary")
        print(f"  Error details: {import_error}")
    except Exception as error:
        print(f"Error connecting to PostgreSQL: {error}")
        print(f"  Host: {host}")
        print(f"  Port: {port}")
        print(f"  Database: {POSTGRES_CONFIG['database']}")
        print(f"  User: {POSTGRES_CONFIG['username']}")
    return None


def get_postgres_connection_ssh():
    """
    Connect to PostgreSQL database through SSH tunnel

    Returns:
        tuple: (tunnel, connection) if successful, (None, None) otherwise
        Both tunnel and connection should be closed when done
    """
    try:
        from sshtunnel import SSHTunnelForwarder

        # Create SSH tunnel
        tunnel = SSHTunnelForwarder(
            (SSH_CONFIG['ssh_host'], SSH_CONFIG['ssh_port']),
            ssh_username=SSH_CONFIG['ssh_username'],
            ssh_password=SSH_CONFIG['ssh_password'],
            remote_bind_address=('localhost', SSH_CONFIG['remote_port']),
            local_bind_address=('localhost', SSH_CONFIG['local_port'])
        )

        # Start the tunnel
        tunnel.start()
        print(f"✓ SSH tunnel established to {SSH_CONFIG['ssh_host']}:{SSH_CONFIG['ssh_port']}")
        print(f"  Local port: {tunnel.local_bind_port}")
        print(f"  Remote port: {SSH_CONFIG['remote_port']}")

        # Try to connect to PostgreSQL through the tunnel
        try:
            import psycopg2

            conn = psycopg2.connect(
                host='localhost',
                port=tunnel.local_bind_port,
                database=POSTGRES_CONFIG['database'],
                user=POSTGRES_CONFIG['username'],
                password=POSTGRES_CONFIG['password']
            )
            print(f"✓ Successfully connected to PostgreSQL through SSH tunnel")
            print(f"  Database: {POSTGRES_CONFIG['database']}")
            print(f"  User: {POSTGRES_CONFIG['username']}")
            return tunnel, conn

        except ImportError:
            # Try psycopg (version 3)
            import psycopg

            conn = psycopg.connect(
                host='localhost',
                port=tunnel.local_bind_port,
                dbname=POSTGRES_CONFIG['database'],
                user=POSTGRES_CONFIG['username'],
                password=POSTGRES_CONFIG['password']
            )
            print(f"✓ Successfully connected to PostgreSQL through SSH tunnel")
            print(f"  Database: {POSTGRES_CONFIG['database']}")
            print(f"  User: {POSTGRES_CONFIG['username']}")
            return tunnel, conn

    except ImportError as import_error:
        print(f"ImportError: Required library not installed")
        print(f"  Please install: pip install sshtunnel psycopg2-binary")
        print(f"  Error details: {import_error}")
        return None, None
    except Exception as error:
        print(f"Error connecting to PostgreSQL through SSH: {error}")
        print(f"  SSH Host: {SSH_CONFIG['ssh_host']}:{SSH_CONFIG['ssh_port']}")
        print(f"  SSH User: {SSH_CONFIG['ssh_username']}")
        print(f"  Database: {POSTGRES_CONFIG['database']}")
        if 'tunnel' in locals() and tunnel:
            tunnel.stop()
        return None, None