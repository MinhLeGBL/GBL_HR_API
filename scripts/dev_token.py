"""
Dev utility: Generate an access token for any user by SID or email.
Usage:
    .venv/bin/python scripts/dev_token.py                    # default: first admin user
    .venv/bin/python scripts/dev_token.py --sid 100000001
    .venv/bin/python scripts/dev_token.py --email minhle@globallink.vn
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.auth.service import AuthService
from app.core.database import get_postgres_connection


def get_token(sid=None, email=None):
    conn = get_postgres_connection()
    if not conn:
        print("ERROR: Failed to connect to database")
        sys.exit(1)

    cursor = conn.cursor()

    if sid:
        cursor.execute(
            "SELECT u.sid, u.role_id, r.name FROM users u JOIN roles r ON u.role_id = r.id WHERE u.sid = %s",
            (sid,)
        )
    elif email:
        cursor.execute(
            "SELECT u.sid, u.role_id, r.name FROM users u JOIN roles r ON u.role_id = r.id WHERE u.email = %s",
            (email,)
        )
    else:
        # Default: first admin user
        cursor.execute(
            "SELECT u.sid, u.role_id, r.name FROM users u JOIN roles r ON u.role_id = r.id WHERE r.name = 'Admin' LIMIT 1"
        )

    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user:
        print("ERROR: User not found")
        sys.exit(1)

    auth = AuthService()
    token = auth.create_access_token(user[0], user[1], user[2])
    print(f"SID:   {user[0]}")
    print(f"Role:  {user[2]}")
    print(f"Token: {token}")
    return token


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate a dev access token')
    parser.add_argument('--sid', type=int, help='User SID')
    parser.add_argument('--email', type=str, help='User email')
    args = parser.parse_args()
    get_token(sid=args.sid, email=args.email)
