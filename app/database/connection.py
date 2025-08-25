import platform
import os
import oracledb
from config.database import DATABASE_CONFIG

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