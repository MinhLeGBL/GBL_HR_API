"""Tracking system — a hidden page behind an employee-number gate.

HOW ACCESS WORKS
----------------
The page is reached only through a command typed into the frontend's header
search; nothing lists it. That keeps it out of sight, but it is not the
protection — the command ships in the frontend bundle (as a hash) and the API
URL is guessable. The protection is here:

  1. The caller is signed in (`token_required` on every route).
  2. They type an employee number, and it must be THEIR OWN — the
     `employee_code` of the employee record linked to their account — so knowing
     a colleague's code is worthless.
  3. That code must be on the allowlist, `tracking_system_access`. There is no
     UI for it on purpose; it is edited by hand with SQL:

         INSERT INTO tracking_system_access (employee_code, note)
         VALUES ('GH135', 'why this person has access');
         DELETE FROM tracking_system_access WHERE employee_code = 'GH135';

A successful unlock returns a short-lived signed token (type
`tracking_unlock`, bound to the user's sid). Every gated request re-checks the
allowlist, so deleting a row revokes access on the next request rather than
when the token expires.

Employee numbers are short and guessable, so failed attempts are rate-limited
per user. The counter lives in Postgres, not in memory, because production runs
several gunicorn workers and an in-memory counter would be split between them.
Every attempt, good or bad, is kept in `tracking_system_unlock_attempts`.

WHAT IS TRACKED
---------------
Payments for merchandise that are not recorded in Oracle. Two lists, each
filled by importing from Oracle and typing the real figure:

  In  — one row per ITEM AND YEAR. Tracking cost = (entered unit cost −
        landed unit cost) × qty. Landed cost is UDF1_STRING (see queries.py).
        `year` NULL = the cost applies to all units ever received; a year =
        only to the units received that year (the Excel import's Year column),
        so one item can carry a different cost per year.
  Out — one row per BILL LINE. Tracking revenue = entered revenue − Oracle
        net line revenue ex-VAT. The entered amount is unofficial, so it
        carries no VAT; Oracle's figure is taken ex-VAT to compare like with
        like.

Item details, landed cost and Oracle revenue are SNAPSHOTTED at import, so a
recorded entry does not drift when Oracle is edited later. Importing the same
item and year (In) or line (Out) again updates that row rather than adding another.
"""
import hmac
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import jwt

from app.core.database import get_oracle_connection, get_postgres_connection
from config.database import JWT_CONFIG
from .queries import TrackingQueries

logger = logging.getLogger(__name__)

UNLOCK_TOKEN_TYPE = 'tracking_unlock'
UNLOCK_TTL = timedelta(hours=8)
MAX_FAILED_ATTEMPTS = 5
FAILED_WINDOW = timedelta(minutes=15)

# One message for every refusal reason, so a wrong guess cannot tell
# "not your code" apart from "your code, but not on the list".
INVALID_CODE_MESSAGE = 'Incorrect code'


SEARCH_MIN_LEN = 2
SEARCH_MAX_LEN = 50
ITEM_SEARCH_LIMIT = 25
LINE_SEARCH_LIMIT = 100
MAX_IMPORT_ROWS = 1000
ORACLE_IN_CHUNK = 500     # Oracle caps an IN list at 1000
YEAR_MIN, YEAR_MAX = 2000, 2100


MAX_CODE_LEN = 50
# pg_advisory_xact_lock(int, int) namespace for unlock attempts ('trk' + 0).
_UNLOCK_LOCK_NS = 0x74726B00


def _norm(code: Any) -> str:
    """Trimmed, upper-cased code. Anything but a string normalises to '' (= no code)."""
    return code.strip().upper() if isinstance(code, str) else ''


def _ascii_digits(text: str) -> bool:
    """str.isdigit() alone accepts '²' and other Unicode digits that int() rejects."""
    return text.isascii() and text.isdigit()


def parse_landed_cost(raw: Any) -> Optional[float]:
    """UDF1_STRING → a positive unit cost, or None.

    The field is free text. Observed: plain integers ('10582853'), and junk
    ('  -  ', 'undefined'). Commas and spaces are treated as thousands
    separators; more than one '.' means '.' is too (Vietnamese grouping).
    """
    if raw is None:
        return None
    s = re.sub(r'[\s,]', '', str(raw))
    if s.count('.') > 1:
        s = s.replace('.', '')
    if not re.fullmatch(r'\d+(\.\d+)?', s):
        return None
    value = float(s)
    return value if value > 0 else None


def _number(value: Any) -> Optional[Decimal]:
    """A finite Decimal from JSON input, or None. Booleans are not numbers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _sid(value: Any) -> Optional[int]:
    """An Oracle SID from JSON. SIDs are 18 digits — past 2**53, so a browser
    holding one as a Number has already rounded it. They travel as STRINGS;
    an int is accepted too (server-side callers, tests)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and _ascii_digits(value):
        return int(value)
    return None


# Columns holding Oracle SIDs, sent to the client as strings (see `_sid`).
_SID_KEYS = ('item_sid', 'line_id', 'bill_id')


def _year(value: Any) -> Optional[int]:
    """A receipt year from JSON (int or digit string), or None if out of range."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and _ascii_digits(value.strip()):
        value = int(value.strip())
    if isinstance(value, int) and YEAR_MIN <= value <= YEAR_MAX:
        return value
    return None


def _f(value: Any) -> Optional[float]:
    return None if value is None else float(value)


_BILL_RE = re.compile(r'^(?:([A-Za-z]{2,6})\s*-?\s*)?(\d{1,9})$')


def parse_bill_no(q: str) -> Optional[tuple]:
    """'RWP-556', 'rwp 556', 'RWP556' → ('RWP', 556); '556' → (None, 556); else None.

    A bare number is ambiguous on purpose: DOC_NO repeats across stores, so it
    matches that number in every store.
    """
    m = _BILL_RE.match(q.strip())
    if not m:
        return None
    store = m.group(1).upper() if m.group(1) else None
    return store, int(m.group(2))


def _search_binds(q: str) -> Dict[str, str]:
    return {'upc_prefix': f'{q}%', 'alu_like': f'%{q.upper()}%'}


def _rows(cur) -> List[Dict[str, Any]]:
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


class TrackingSystemService:

    def __init__(self):
        self.secret_key = JWT_CONFIG['secret_key']
        self.algorithm = JWT_CONFIG['algorithm']

    # ------------------------------------------------------------------ schema

    def init_database(self) -> Dict[str, Any]:
        """Create the allowlist and the attempt log. Idempotent."""
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'error': 'Failed to connect to database'}
        try:
            with conn.cursor() as cur:
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS tracking_system_access (
                        employee_code VARCHAR(50) PRIMARY KEY,
                        note          TEXT,
                        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS tracking_system_unlock_attempts (
                        id           BIGSERIAL PRIMARY KEY,
                        user_sid     BIGINT NOT NULL
                                     REFERENCES users(sid) ON DELETE CASCADE,
                        success      BOOLEAN NOT NULL,
                        attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                ''')
                cur.execute('''
                    CREATE INDEX IF NOT EXISTS ix_tracking_unlock_attempts_user_time
                    ON tracking_system_unlock_attempts (user_sid, attempted_at)
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS tracking_in_items (
                        id                BIGSERIAL PRIMARY KEY,
                        item_sid          BIGINT NOT NULL,
                        upc               VARCHAR(50),
                        alu               VARCHAR(100),
                        description       TEXT,
                        brand             VARCHAR(255),
                        department        VARCHAR(50),
                        season            VARCHAR(50),
                        year              SMALLINT,
                        qty               NUMERIC(18, 2) NOT NULL,
                        entered_unit_cost NUMERIC(18, 2) NOT NULL,
                        landed_unit_cost  NUMERIC(18, 2) NOT NULL,
                        created_by        BIGINT REFERENCES users(sid) ON DELETE SET NULL,
                        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_by        BIGINT REFERENCES users(sid) ON DELETE SET NULL,
                        updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                ''')
                # One row per item AND year. NULL year (all units) is a value
                # of its own here, hence COALESCE: a plain UNIQUE treats NULLs
                # as distinct and would allow two all-time rows for one item.
                # The two statements before it upgrade a table created when
                # the key was the item alone.
                cur.execute('ALTER TABLE tracking_in_items ADD COLUMN IF NOT EXISTS year SMALLINT')
                cur.execute('ALTER TABLE tracking_in_items '
                            'DROP CONSTRAINT IF EXISTS tracking_in_items_item_sid_key')
                cur.execute('''
                    CREATE UNIQUE INDEX IF NOT EXISTS ux_tracking_in_items_item_year
                    ON tracking_in_items (item_sid, (COALESCE(year, 0)))
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS tracking_out_lines (
                        id                BIGSERIAL PRIMARY KEY,
                        line_id           BIGINT NOT NULL UNIQUE,
                        bill_id           BIGINT,
                        bill_no           VARCHAR(50),
                        bill_date         DATE,
                        store             VARCHAR(20),
                        item_sid          BIGINT,
                        upc               VARCHAR(50),
                        alu               VARCHAR(100),
                        description       TEXT,
                        brand             VARCHAR(255),
                        department        VARCHAR(50),
                        season            VARCHAR(50),
                        qty               NUMERIC(18, 2) NOT NULL,
                        oracle_revenue    NUMERIC(18, 2) NOT NULL,
                        entered_revenue   NUMERIC(18, 2) NOT NULL,
                        created_by        BIGINT REFERENCES users(sid) ON DELETE SET NULL,
                        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_by        BIGINT REFERENCES users(sid) ON DELETE SET NULL,
                        updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                ''')
            conn.commit()
            return {'success': True}
        except Exception as e:
            conn.rollback()
            logger.exception('tracking_system init_database failed')
            return {'success': False, 'error': str(e)}
        finally:
            conn.close()

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _own_employee_code(cur, user_sid: int) -> Optional[str]:
        cur.execute('''
            SELECT e.employee_code
            FROM users u
            JOIN employees e ON e.sid = u.employee_sid
            WHERE u.sid = %s AND u.is_active AND e.is_active
        ''', (user_sid,))
        row = cur.fetchone()
        return row[0] if row else None

    @staticmethod
    def _is_allowed(cur, employee_code: str) -> bool:
        cur.execute(
            'SELECT 1 FROM tracking_system_access WHERE upper(trim(employee_code)) = %s',
            (_norm(employee_code),),
        )
        return cur.fetchone() is not None

    # ------------------------------------------------------------------ unlock

    def unlock(self, user_sid: int, entered_code: Optional[str]) -> Dict[str, Any]:
        """Check the typed employee number; on success return an unlock token."""
        if not _norm(entered_code) or len(_norm(entered_code)) > MAX_CODE_LEN:
            return {'success': False, 'code': 'INVALID_INPUT',
                    'error': 'Enter your employee number'}

        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'code': 'SERVER_ERROR',
                    'error': 'Failed to connect to database'}
        try:
            with conn.cursor() as cur:
                # One attempt per user at a time, across all gunicorn workers:
                # without this, N parallel guesses all read "< 5 failures"
                # before any of them is recorded. Released at commit/rollback.
                cur.execute('SELECT pg_advisory_xact_lock(%s, %s)',
                            (_UNLOCK_LOCK_NS, user_sid % 2**31))
                cur.execute('''
                    SELECT count(*) FROM tracking_system_unlock_attempts
                    WHERE user_sid = %s AND NOT success AND attempted_at > %s
                ''', (user_sid, datetime.now(timezone.utc) - FAILED_WINDOW))
                if cur.fetchone()[0] >= MAX_FAILED_ATTEMPTS:
                    return {'success': False, 'code': 'RATE_LIMITED',
                            'error': 'Too many attempts. Try again later.'}

                own = self._own_employee_code(cur, user_sid)
                # Both checks always run, so "right code, not on the list" and
                # "wrong code" cost the same queries and read the same.
                allowed = self._is_allowed(cur, own) if own else False
                matches = bool(own) and hmac.compare_digest(
                    _norm(entered_code).encode('utf-8'), _norm(own).encode('utf-8'))
                ok = matches and allowed

                cur.execute(
                    'INSERT INTO tracking_system_unlock_attempts (user_sid, success) '
                    'VALUES (%s, %s)',
                    (user_sid, ok),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception('tracking_system unlock failed')
            return {'success': False, 'code': 'SERVER_ERROR',
                    'error': 'Unlock failed'}
        finally:
            conn.close()

        if not ok:
            logger.warning('tracking_system: refused unlock for user %s', user_sid)
            return {'success': False, 'code': 'INVALID_CODE',
                    'error': INVALID_CODE_MESSAGE}

        expires = datetime.now(timezone.utc) + UNLOCK_TTL
        token = jwt.encode(
            {'sid': user_sid, 'type': UNLOCK_TOKEN_TYPE, 'exp': expires},
            self.secret_key, algorithm=self.algorithm,
        )
        logger.info('tracking_system: unlocked for user %s', user_sid)
        return {'success': True, 'unlock_token': token,
                'expires_at': expires.isoformat()}

    # ------------------------------------------------------------------ verify

    def verify_unlock(self, user_sid: int, token: Optional[str]) -> bool:
        """True when `token` is a live unlock token for this user AND the
        user's own employee code is still on the allowlist."""
        if not token:
            return False
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
        except jwt.InvalidTokenError:
            return False
        if payload.get('type') != UNLOCK_TOKEN_TYPE or payload.get('sid') != user_sid:
            return False

        conn = get_postgres_connection()
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                own = self._own_employee_code(cur, user_sid)
                return bool(own) and self._is_allowed(cur, own)
        except Exception:
            logger.exception('tracking_system verify_unlock failed')
            return False
        finally:
            conn.close()

    # ======================================================= other features

    def is_user_allowed(self, user_sid: int) -> bool:
        """True when the user's own employee code is on the allowlist.

        No unlock token: for other features that show tracked figures to the
        same people, where the gate is the account itself (the user's call,
        2026-11-26, for custom reports' reconciliation). The tracker page
        keeps its own employee-number unlock. Any failure answers False.
        """
        conn = get_postgres_connection()
        if conn is None:
            return False
        try:
            with conn.cursor() as cur:
                own = self._own_employee_code(cur, user_sid)
                return bool(own) and self._is_allowed(cur, own)
        except Exception:
            logger.exception('tracking_system is_user_allowed failed')
            return False
        finally:
            conn.close()

    def reconciliation_deltas(self) -> Optional[Dict[str, Dict[Any, float]]]:
        """Every tracked difference, for adding to the system's figures.

            {'out': {line_id: entered_revenue - oracle_revenue},
             'in':  {(item_sid, year or None): entered_unit_cost - landed_unit_cost}}

        `out` is per bill line, exactly the tracking revenue the Out tab
        shows (a return line is negative here, as on that tab). `in` is per
        UNIT: the caller multiplies by the units it counts. None when the
        database cannot answer, so a caller can refuse rather than show
        system figures as if reconciled.
        """
        conn = get_postgres_connection()
        if conn is None:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT line_id, entered_revenue - oracle_revenue '
                            'FROM tracking_out_lines')
                out = {int(lid): float(d) for lid, d in cur.fetchall()}
                cur.execute('SELECT item_sid, year, entered_unit_cost - landed_unit_cost '
                            'FROM tracking_in_items')
                in_ = {(int(sid), int(yr) if yr is not None else None): float(d)
                       for sid, yr, d in cur.fetchall()}
            return {'out': out, 'in': in_}
        except Exception:
            logger.exception('tracking_system reconciliation_deltas failed')
            return None
        finally:
            conn.close()

    # ================================================================ Oracle

    @staticmethod
    def _clean_query(q: Optional[str]) -> Optional[str]:
        q = (q or '').strip()
        if len(q) < SEARCH_MIN_LEN or len(q) > SEARCH_MAX_LEN:
            return None
        return q

    @staticmethod
    def _item_out(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'item_sid': str(row['item_sid']),
            'upc': row.get('upc'),
            'alu': row.get('alu'),
            'description': row.get('description'),
            'brand': (row.get('brand') or '').strip() or None,
            'department': row.get('department'),
            'season': row.get('season'),
        }

    def _in_candidate(self, row: Dict[str, Any]) -> Dict[str, Any]:
        out = self._item_out(row)
        out['landed_unit_cost'] = parse_landed_cost(row.get('landed_raw'))
        out['received_qty'] = _f(row.get('received_qty')) or 0.0
        return out

    def _out_candidate(self, row: Dict[str, Any]) -> Dict[str, Any]:
        out = self._item_out(row)
        out.update({
            'line_id': str(row['line_id']),
            'bill_id': str(row['bill_id']) if row.get('bill_id') is not None else None,
            'bill_no': row.get('bill_no'),
            'bill_date': row.get('bill_date'),
            'store': row.get('store'),
            'qty': _f(row.get('qty')) or 0.0,
            'oracle_revenue': round(_f(row.get('oracle_revenue')) or 0.0, 2),
        })
        return out

    def search_items(self, q: Optional[str]) -> Dict[str, Any]:
        """In import: items whose UPC starts with, or ALU contains, `q`."""
        q = self._clean_query(q)
        if q is None:
            return {'success': False, 'code': 'INVALID_INPUT',
                    'error': f'Search needs {SEARCH_MIN_LEN}-{SEARCH_MAX_LEN} characters'}
        conn = get_oracle_connection()
        if conn is None:
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Oracle unavailable'}
        try:
            cur = conn.cursor()
            cur.execute(TrackingQueries.ITEM_SEARCH, {**_search_binds(q), 'lim': ITEM_SEARCH_LIMIT})
            items = [self._in_candidate(r) for r in _rows(cur)]
            return {'success': True, 'items': items, 'truncated': len(items) >= ITEM_SEARCH_LIMIT}
        except Exception:
            logger.exception('tracking_system search_items failed')
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Search failed'}
        finally:
            conn.close()

    def search_lines(self, q: Optional[str]) -> Dict[str, Any]:
        """Out import: sale/return lines, newest first, matching `q` as a bill
        number ('RWP-556' or bare '556'), a UPC prefix or an ALU substring.
        Digits can be either a bill number or the start of a UPC, so both are
        searched and the results merged."""
        q = self._clean_query(q)
        if q is None:
            return {'success': False, 'code': 'INVALID_INPUT',
                    'error': f'Search needs {SEARCH_MIN_LEN}-{SEARCH_MAX_LEN} characters'}
        conn = get_oracle_connection()
        if conn is None:
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Oracle unavailable'}
        try:
            cur = conn.cursor()
            found: Dict[str, Dict[str, Any]] = {}
            truncated = False

            bill = parse_bill_no(q)
            if bill is not None:
                store, doc_no = bill
                cur.execute(TrackingQueries.LINES_FOR_BILL,
                            {'doc_no': doc_no, 'store': store, 'lim': LINE_SEARCH_LIMIT})
                rows = _rows(cur)
                truncated = truncated or len(rows) >= LINE_SEARCH_LIMIT
                for r in rows:
                    c = self._out_candidate(r)
                    found[c['line_id']] = c

            cur.execute(TrackingQueries.ITEM_SIDS_MATCHING,
                        {**_search_binds(q), 'lim': ITEM_SEARCH_LIMIT})
            sids = [int(r[0]) for r in cur.fetchall()]
            if sids:
                binds: Dict[str, Any] = {f's{i}': v for i, v in enumerate(sids)}
                binds['lim'] = LINE_SEARCH_LIMIT
                placeholders = ', '.join(f':s{i}' for i in range(len(sids)))
                cur.execute(TrackingQueries.LINES_FOR_ITEMS.format(item_sids=placeholders), binds)
                rows = _rows(cur)
                truncated = truncated or len(rows) >= LINE_SEARCH_LIMIT
                for r in rows:
                    c = self._out_candidate(r)
                    found.setdefault(c['line_id'], c)

            # Bill matches first (the more specific ask), then newest first.
            bill_ids = set()
            if bill is not None:
                bill_ids = {lid for lid, c in found.items()
                            if c['bill_no'] and c['bill_no'].split('-')[-1] == str(bill[1])
                            and (bill[0] is None or c['bill_no'].upper().startswith(bill[0] + '-'))}
            # Two stable sorts: newest first, then bill matches ahead of UPC/ALU ones.
            lines = sorted(found.values(), key=lambda c: c['bill_date'] or '', reverse=True)
            lines.sort(key=lambda c: c['line_id'] not in bill_ids)
            if len(lines) > LINE_SEARCH_LIMIT:
                lines, truncated = lines[:LINE_SEARCH_LIMIT], True
            return {'success': True, 'lines': lines, 'truncated': truncated}
        except Exception:
            logger.exception('tracking_system search_lines failed')
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Search failed'}
        finally:
            conn.close()

    @staticmethod
    def _oracle_in(cur, sql: str, key: str, values: List[int]) -> List[Dict[str, Any]]:
        """Run `sql` with `{key}` expanded to bind placeholders, in chunks."""
        rows: List[Dict[str, Any]] = []
        for start in range(0, len(values), ORACLE_IN_CHUNK):
            chunk = values[start:start + ORACLE_IN_CHUNK]
            ph = ', '.join(f':v{i}' for i in range(len(chunk)))
            cur.execute(sql.format(**{key: ph}), {f'v{i}': v for i, v in enumerate(chunk)})
            rows.extend(_rows(cur))
        return rows

    def lookup_upcs(self, upcs: Any) -> Dict[str, Any]:
        """Excel import into In: resolve UPCs to Oracle items in one batch.

        Returns one entry per distinct requested UPC, in request order, with
        every matching item. Normally 0 or 1; three UPCs are on two items each
        locally, and the caller must not guess between them.
        """
        if not isinstance(upcs, list) or not upcs or len(upcs) > MAX_IMPORT_ROWS:
            return {'success': False, 'code': 'INVALID_INPUT',
                    'error': f'Send 1-{MAX_IMPORT_ROWS} UPCs'}
        wanted: List[int] = []
        for u in upcs:
            text = str(u).strip() if isinstance(u, (str, int)) and not isinstance(u, bool) else ''
            if not _ascii_digits(text) or len(text) > 15 or int(text) <= 0:
                return {'success': False, 'code': 'INVALID_INPUT',
                        'error': f'Not a UPC: {u!r}'}
            n = int(text)
            if n not in wanted:
                wanted.append(n)

        conn = get_oracle_connection()
        if conn is None:
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Oracle unavailable'}
        try:
            cur = conn.cursor()
            rows = self._oracle_in(cur, TrackingQueries.ITEMS_BY_UPCS, 'upcs', wanted)
            by_year: Dict[int, Dict[str, float]] = {}
            sids = [int(r['item_sid']) for r in rows]
            if sids:
                for y in self._oracle_in(cur, TrackingQueries.RECEIVED_BY_YEAR, 'sids', sids):
                    by_year.setdefault(int(y['item_sid']), {})[str(int(y['yr']))] = _f(y['qty']) or 0.0
        except Exception:
            logger.exception('tracking_system lookup_upcs failed')
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Lookup failed'}
        finally:
            conn.close()

        by_upc: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            cand = self._in_candidate(r)
            cand['received_by_year'] = by_year.get(int(r['item_sid']), {})
            by_upc.setdefault(str(r['upc']), []).append(cand)
        return {'success': True,
                'results': [{'upc': str(n), 'matches': by_upc.get(str(n), [])} for n in wanted]}

    @staticmethod
    def _oracle_one(sql: str, binds: Dict[str, Any], cur) -> Optional[Dict[str, Any]]:
        cur.execute(sql, binds)
        rows = _rows(cur)
        return rows[0] if rows else None

    # ============================================================ In / Out

    _IN_COLS = ('id', 'item_sid', 'upc', 'alu', 'description', 'brand', 'department',
                'season', 'year', 'qty', 'entered_unit_cost', 'landed_unit_cost', 'updated_at')
    _OUT_COLS = ('id', 'line_id', 'bill_id', 'bill_no', 'bill_date', 'store', 'item_sid',
                 'upc', 'alu', 'description', 'brand', 'department', 'season', 'qty',
                 'oracle_revenue', 'entered_revenue', 'updated_at')

    @staticmethod
    def _serialise(row: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for k, v in row.items():
            if k in _SID_KEYS and v is not None:
                v = str(v)
            elif isinstance(v, Decimal):
                v = float(v)
            elif hasattr(v, 'isoformat'):
                v = v.isoformat()
            out[k] = v
        return out

    def list_in(self) -> Dict[str, Any]:
        return self._list('tracking_in_items', self._IN_COLS,
                          'brand, alu, upc, year NULLS FIRST', self._in_row)

    def list_out(self) -> Dict[str, Any]:
        return self._list('tracking_out_lines', self._OUT_COLS,
                          'bill_date DESC, bill_no DESC, id DESC', self._out_row)

    def _in_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        r = self._serialise(row)
        r['tracking_cost'] = round((r['entered_unit_cost'] - r['landed_unit_cost']) * r['qty'], 2)
        return r

    def _out_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        r = self._serialise(row)
        r['tracking_revenue'] = round(r['entered_revenue'] - r['oracle_revenue'], 2)
        return r

    def _list(self, table, cols, order, shape) -> Dict[str, Any]:
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Database unavailable'}
        try:
            with conn.cursor() as cur:
                cur.execute(f'SELECT {", ".join(cols)} FROM {table} ORDER BY {order}')
                rows = [shape(dict(zip(cols, r))) for r in cur.fetchall()]
            return {'success': True, 'rows': rows}
        except Exception:
            logger.exception('tracking_system list %s failed', table)
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Load failed'}
        finally:
            conn.close()

    def import_in(self, user_sid: int, items: Any) -> Dict[str, Any]:
        """Body rows: {item_sid, qty, entered_unit_cost, year?}. Item details and
        the landed cost are read from Oracle here, never taken from the client.
        `year` absent/null = all-time; otherwise the row is that year's cost."""
        if not isinstance(items, list) or not items or len(items) > MAX_IMPORT_ROWS:
            return {'success': False, 'code': 'INVALID_INPUT',
                    'error': f'Send 1-{MAX_IMPORT_ROWS} items'}
        errors, checked = [], []
        seen: Dict[tuple, int] = {}
        for i, it in enumerate(items):
            it = it if isinstance(it, dict) else {}
            sid, qty, cost = _sid(it.get('item_sid')), _number(it.get('qty')), _number(it.get('entered_unit_cost'))
            raw_year = it.get('year')
            year = None if raw_year in (None, '') else _year(raw_year)
            if sid is None:
                errors.append({'index': i, 'error': 'item_sid must be a SID string'}); continue
            if qty is None or qty <= 0:
                errors.append({'index': i, 'item_sid': str(sid), 'error': 'qty must be greater than 0'}); continue
            if cost is None or cost < 0:
                errors.append({'index': i, 'item_sid': str(sid), 'error': 'entered_unit_cost must be 0 or more'}); continue
            if raw_year not in (None, '') and year is None:
                errors.append({'index': i, 'item_sid': str(sid),
                               'error': f'year must be {YEAR_MIN}-{YEAR_MAX}'}); continue
            if (sid, year) in seen:
                # Two rows for one item and year would silently keep only the last.
                errors.append({'index': i, 'item_sid': str(sid),
                               'error': f'Same item and year as row {seen[(sid, year)] + 1}'}); continue
            seen[(sid, year)] = i
            checked.append((i, sid, qty, cost, year))

        ready = []
        if checked:
            ora = get_oracle_connection()
            if ora is None:
                return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Oracle unavailable'}
            try:
                rows = self._oracle_in(ora.cursor(), TrackingQueries.ITEMS_BY_SIDS, 'sids',
                                       list({sid for _, sid, _, _, _ in checked}))
            except Exception:
                logger.exception('tracking_system import_in Oracle read failed')
                return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Import failed'}
            finally:
                ora.close()
            by_sid = {int(r['item_sid']): r for r in rows}
            for i, sid, qty, cost, year in checked:
                row = by_sid.get(sid)
                if row is None:
                    errors.append({'index': i, 'item_sid': str(sid), 'error': 'Item not found in Oracle'}); continue
                cand = self._in_candidate(row)
                if cand['landed_unit_cost'] is None:
                    errors.append({'index': i, 'item_sid': str(sid), 'error': 'Item has no landed cost in Oracle'}); continue
                ready.append({**cand, 'qty': qty, 'entered_unit_cost': cost, 'year': year})
        errors.sort(key=lambda e: e['index'])

        if errors:
            # All or nothing: a partial import would leave the user guessing
            # which rows landed.
            return {'success': False, 'code': 'INVALID_INPUT', 'error': 'Some rows were rejected',
                    'errors': errors}
        return self._upsert('tracking_in_items', 'item_sid, (COALESCE(year, 0))', ('item_sid', 'year'),
                            ready, user_sid,
                            ('item_sid', 'upc', 'alu', 'description', 'brand', 'department',
                             'season', 'year', 'qty', 'entered_unit_cost', 'landed_unit_cost'))

    def import_out(self, user_sid: int, lines: Any) -> Dict[str, Any]:
        """Body rows: {line_id, entered_revenue}. The bill line is read from Oracle."""
        if not isinstance(lines, list) or not lines or len(lines) > MAX_IMPORT_ROWS:
            return {'success': False, 'code': 'INVALID_INPUT',
                    'error': f'Send 1-{MAX_IMPORT_ROWS} lines'}
        errors, ready = [], []
        seen_lines: Dict[int, int] = {}
        ora = get_oracle_connection()
        if ora is None:
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Oracle unavailable'}
        try:
            cur = ora.cursor()
            for i, ln in enumerate(lines):
                ln = ln if isinstance(ln, dict) else {}
                lid, rev = _sid(ln.get('line_id')), _number(ln.get('entered_revenue'))
                if lid is None:
                    errors.append({'index': i, 'error': 'line_id must be a SID string'}); continue
                if rev is None:
                    errors.append({'index': i, 'line_id': str(lid), 'error': 'entered_revenue must be a number'}); continue
                if lid in seen_lines:
                    errors.append({'index': i, 'line_id': str(lid),
                                   'error': f'Same bill line as row {seen_lines[lid] + 1}'}); continue
                seen_lines[lid] = i
                row = self._oracle_one(TrackingQueries.LINE_BY_ID, {'line_id': lid}, cur)
                if row is None:
                    errors.append({'index': i, 'line_id': str(lid), 'error': 'Bill line not found in Oracle'}); continue
                ready.append({**self._out_candidate(row), 'entered_revenue': rev})
        except Exception:
            logger.exception('tracking_system import_out Oracle read failed')
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Import failed'}
        finally:
            ora.close()

        if errors:
            return {'success': False, 'code': 'INVALID_INPUT', 'error': 'Some rows were rejected',
                    'errors': errors}
        return self._upsert('tracking_out_lines', 'line_id', ('line_id',), ready, user_sid,
                            ('line_id', 'bill_id', 'bill_no', 'bill_date', 'store', 'item_sid',
                             'upc', 'alu', 'description', 'brand', 'department', 'season',
                             'qty', 'oracle_revenue', 'entered_revenue'))

    @staticmethod
    def _upsert(table: str, conflict: str, key_cols: tuple, rows: List[Dict[str, Any]],
                user_sid: int, cols: tuple) -> Dict[str, Any]:
        """INSERT ... ON CONFLICT (`conflict`) DO UPDATE. `key_cols` are the
        columns that identify a row, left out of the update."""
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Database unavailable'}
        updates = ', '.join(f'{c} = EXCLUDED.{c}' for c in cols if c not in key_cols)
        sql = (f'INSERT INTO {table} ({", ".join(cols)}, created_by, updated_by) '
               f'VALUES ({", ".join(["%s"] * (len(cols) + 2))}) '
               f'ON CONFLICT ({conflict}) DO UPDATE SET {updates}, '
               f'updated_by = EXCLUDED.updated_by, updated_at = NOW() '
               f'RETURNING (xmax = 0) AS inserted')
        try:
            inserted = 0
            with conn.cursor() as cur:
                for r in rows:
                    vals = [int(r[c]) if c in _SID_KEYS and r.get(c) is not None else r.get(c)
                            for c in cols]
                    cur.execute(sql, vals + [user_sid, user_sid])
                    inserted += 1 if cur.fetchone()[0] else 0
            conn.commit()
            return {'success': True, 'inserted': inserted, 'updated': len(rows) - inserted}
        except Exception:
            conn.rollback()
            logger.exception('tracking_system upsert into %s failed', table)
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Save failed'}
        finally:
            conn.close()

    def delete(self, kind: str, row_id: int) -> Dict[str, Any]:
        table = {'in': 'tracking_in_items', 'out': 'tracking_out_lines'}.get(kind)
        if table is None:
            return {'success': False, 'code': 'INVALID_INPUT', 'error': 'Unknown list'}
        conn = get_postgres_connection()
        if conn is None:
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Database unavailable'}
        try:
            with conn.cursor() as cur:
                cur.execute(f'DELETE FROM {table} WHERE id = %s', (row_id,))
                found = cur.rowcount > 0
            conn.commit()
            if not found:
                return {'success': False, 'code': 'NOT_FOUND', 'error': 'Row not found'}
            return {'success': True}
        except Exception:
            conn.rollback()
            logger.exception('tracking_system delete from %s failed', table)
            return {'success': False, 'code': 'SERVER_ERROR', 'error': 'Delete failed'}
        finally:
            conn.close()
