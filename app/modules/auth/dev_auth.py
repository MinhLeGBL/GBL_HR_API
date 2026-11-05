"""Development sign-in: pick an account instead of knowing its credentials.

WHY THIS EXISTS
---------------
The local database is seeded from production with personal data replaced, so
`users.email` is a pseudonym like `user947951169@example.com` and every
password hash is the same throwaway value. Both are deliberate: a laptop that
is copied between machines should not carry colleagues' real logins, and a
copied bcrypt hash would be a stealable credential that does not even let you
in. The cost is that nobody can guess their own account.

This closes that gap by listing the local accounts and letting you choose one.

WHAT IT DOES NOT DO
-------------------
It does NOT issue a token without a password, and it does not touch the real
login path. `POST /login` is unchanged, bcrypt still runs, and there is no
branch anywhere that skips verification. All this does is look up the account
and sign in with the development password that the seed loader already set —
which is exactly what you would do by hand if you knew which address to type.

That distinction matters more than the convenience: an authentication bypass
that is merely well-guarded is one misconfiguration away from being an
authentication bypass. There is nothing here to misconfigure.

HOW IT IS GATED
---------------
Three independent conditions, ALL required, and the blueprint is not registered
at all unless they hold — so in production these URLs do not exist and return
the ordinary 404:

  1. DEV_AUTH_BYPASS is explicitly true
  2. FLASK_ENV is neither production nor remote
  3. PostgreSQL is on localhost, and no SSH tunnel is configured
  4. ORACLE is on localhost too

(4) is the one that does the real work, and it exists because (3) does not.
The SERVER runs PostgreSQL on localhost as well, so that check alone
distinguishes a laptop from nothing. Oracle is the asymmetry: the server
reaches RetailPro across the network and never at 127.0.0.1, so a local Oracle
can only be a container holding a seeded development database.
"""
import os

from flask import Blueprint, jsonify, request

from app.core.auth import AuthService

dev_auth_bp = Blueprint('dev_auth', __name__, url_prefix='/api/v1/auth/dev')

auth_service = AuthService()

_LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}
# Matches what scripts/dev/load_seed.py writes into every local account.
DEV_PASSWORD = os.getenv('DEV_AUTH_PASSWORD', 'devpassword')

# The single account offered. The seed renames one admin row to this, so the
# name is not a real colleague's — see DEV_ACCOUNT_NAME in scripts/dev/spec.py.
DEV_ACCOUNT_NAME = os.getenv('DEV_AUTH_ACCOUNT', 'devaccount')


def dev_auth_status():
    """(enabled, reason). The reason is shown at startup so it is never a
    mystery why the panel did or did not appear."""
    flag = (os.getenv('DEV_AUTH_BYPASS') or '').strip().lower()
    if flag not in ('true', '1', 'yes', 'on'):
        return False, 'DEV_AUTH_BYPASS is not set'

    env = (os.getenv('FLASK_ENV') or '').strip().lower()
    if env in ('production', 'remote'):
        return False, f'refused: FLASK_ENV={env!r}'

    host = (os.getenv('POSTGRES_HOST') or '').strip()
    if host not in _LOCAL_HOSTS:
        return False, f'refused: POSTGRES_HOST={host!r} is not local'

    # Oracle must ALSO be local, and this is the gate that does the real work.
    #
    # On the server PostgreSQL *is* on localhost, so that check passes there —
    # it distinguishes a laptop from nothing. Oracle is different: the server
    # reaches RetailPro across the network and never at 127.0.0.1, so a local
    # Oracle means a container, which means a seeded development database.
    oracle = (os.getenv('DB_HOST') or '').strip()
    if oracle not in _LOCAL_HOSTS:
        return False, f'refused: DB_HOST={oracle!r} is not local (real Oracle)'

    if (os.getenv('USE_SSH_TUNNEL') or '').strip().lower() in (
            'true', '1', 'yes', 'on'):
        return False, 'refused: USE_SSH_TUNNEL is on'

    return True, f'enabled ({env or "development"}, postgres on {host})'


@dev_auth_bp.route('/accounts', methods=['GET'])
def accounts():
    """The one development account.

    ONE, not all nine. Listing every user put colleagues' real names on a
    login screen that gets screen-shared and screenshotted, and development
    needs exactly one account — an admin, because anything less cannot
    exercise the whole application.

    The row is named `devaccount` because the seed renamed it during
    extraction; the real name is not on this machine to leak.
    """
    from app.core.database import get_postgres_connection

    conn = get_postgres_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT u.sid, u.email, u.full_name, r.name
                FROM users u
                LEFT JOIN roles r ON r.id = u.role_id
                WHERE u.is_active AND u.full_name = %s
                LIMIT 1
            """, (DEV_ACCOUNT_NAME,))
            row = cur.fetchone()

            # A bundle extracted before the rename has no `devaccount`. Fall
            # back to the lowest-sid admin but DO NOT show its real name —
            # the point of the rename is that the name never appears.
            if row is None:
                cur.execute("""
                    SELECT u.sid, u.email, u.full_name, r.name
                    FROM users u
                    JOIN roles r ON r.id = u.role_id
                    WHERE u.is_active AND lower(r.name) = 'admin'
                    ORDER BY u.sid
                    LIMIT 1
                """)
                row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return jsonify({'success': True, 'password': DEV_PASSWORD,
                        'accounts': []}), 200

    return jsonify({
        'success': True,
        'password': DEV_PASSWORD,
        'accounts': [{
            'sid': row[0],
            'email': row[1],
            'full_name': DEV_ACCOUNT_NAME,   # never the underlying name
            'role': row[3],
        }],
    }), 200


@dev_auth_bp.route('/login', methods=['POST'])
def dev_login():
    """Sign in as a chosen account, by `email`, `sid` or `full_name`."""
    data = request.get_json(silent=True) or {}
    email = data.get('email')

    if not email:
        from app.core.database import get_postgres_connection
        sid, name = data.get('sid'), data.get('full_name')
        if not sid and not name:
            return jsonify({'success': False,
                            'error': 'Give one of: email, sid, full_name'}), 400
        conn = get_postgres_connection()
        try:
            with conn.cursor() as cur:
                if sid:
                    cur.execute('SELECT email FROM users WHERE sid = %s', (sid,))
                else:
                    cur.execute('SELECT email FROM users WHERE full_name = %s',
                                (name,))
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify({'success': False, 'error': 'No such account'}), 404
        email = row[0]

    # The ordinary login path, bcrypt and all.
    result = auth_service.authenticate(email, DEV_PASSWORD)
    if result.get('success'):
        return jsonify(result), 200

    return jsonify({
        'success': False,
        'error': (f'Could not sign in as {email}. The local password is no '
                  f'longer the development one — reload the seed, or use the '
                  f'real password.'),
    }), 401
