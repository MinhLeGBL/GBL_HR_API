"""
Extract a portable development-database seed from production.

Phase A of two. Run this ONCE, from a machine that can reach production; the
bundle it writes is then copied to any machine and loaded by `load_seed.py`,
which needs no production access at all. That split is the point: a laptop at
home should never need an SSH tunnel into the company network to have a working
database.

WHAT COMES OUT
    dev/seed/gbl-dev-seed-<date>.tar.gz
        manifest.json          window, row counts, salt, source versions
        oracle/ddl.sql         CREATE TABLE for the 19 rps tables, generated
                               from production's own catalogue — 1,495 columns
                               across DOCUMENT(383), DOCUMENT_ITEM(242) and the
                               rest, which is far past hand-writing
        oracle/<TABLE>.jsonl.gz
        postgres/<table>.jsonl.gz

WHAT IS NOT IN IT
    Personal data. Customer names, phones, emails and addresses are replaced
    with deterministic pseudonyms (see anonymise.py). LOB columns are dropped.
    `users.password_hash` is replaced with one known development password.

Usage:
    python scripts/dev/devdb.py extract          # preferred
    FLASK_ENV=remote PYTHONPATH=. python scripts/dev/extract_seed.py
    ... --from 2025-01-01 --to 2026-09-22      # explicit window
    ... --dry-run                              # count rows, write nothing

Exit codes:
    0 — bundle written
    1 — extraction failed
"""
import argparse
import base64
import datetime as dt
import decimal
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import time

# Vietnamese store names, department names and pseudonyms all end up on stdout.
# A Windows console in a legacy code page raises UnicodeEncodeError on those and
# kills the run partway through - which, mid-load, leaves a half-populated
# database and no obvious cause. Force UTF-8 and degrade rather than die.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dotenv import load_dotenv

env = os.getenv('FLASK_ENV', 'remote')
env_file = f'.env.{env}'
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

from dev.anonymise import DEFAULT_SALT, anonymise_value          # noqa: E402
from dev.spec import (DEV_ACCOUNT_NAME, DEV_ACCOUNT_ROLE,        # noqa: E402
                      DEV_PASSWORD, ORACLE_BY_NAME,
                      ORACLE_TABLES, POSTGRES_ANONYMISE,
                      POSTGRES_DROP_COLUMNS, POSTGRES_REPLACE,
                      STAFF_NAME_COLUMNS)

BATCH = 2_000
SEED_DIR = os.path.join('dev', 'seed')

# Strings that must not survive anonymisation. Checked against the finished
# bundle, because the spec being RIGHT and the spec being APPLIED are different
# claims and only the second one matters. A column named in the spec that does
# not exist matches nothing, fakes nothing, and raises no error.
LEAK_PATTERNS = (
    '@globallink.vn',
    '@gmail.com',
    '@yahoo.com',
    '@hotmail.com',
)


def _check_columns_exist(named, actual, where):
    """Fail loudly when the spec names a column that is not there.

    Silence here is the dangerous outcome: the extract finishes, the bundle
    looks complete, and the data it was supposed to protect is in it.
    """
    missing = sorted(set(named) - set(actual))
    if missing:
        raise RuntimeError(
            f'{where}: spec names column(s) that do not exist: '
            f'{", ".join(missing)}. Nothing would have been anonymised in '
            f'them. Fix scripts/dev/spec.py against the live schema.')


def _scan_for_leaks(bundle):
    """Grep the finished bundle for anything that looks like real contact data."""
    found = {}
    for name, blob in bundle.items():
        if not name.endswith('.jsonl.gz'):
            continue
        text = gzip.decompress(blob).decode('utf-8', 'replace')
        for pattern in LEAK_PATTERNS:
            n = text.count(pattern)
            if n:
                found.setdefault(name, []).append(f'{pattern} x{n}')
    return found


# ── JSON encoding ────────────────────────────────────────────────────────────
# Money is Decimal end to end in this codebase and must not touch float on the
# way through a seed file: 1234.56 through a float and back is not 1234.56, and
# a dev database that disagrees with production in the 12th decimal place is
# exactly the kind of "close enough" that wastes a day later.
def _encode(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    # Already-tagged by the caller (JSON columns) — do not re-encode.
    if isinstance(value, dict) and len(value) == 1 and '__json__' in value:
        return value
    if isinstance(value, decimal.Decimal):
        return {'__dec__': str(value)}
    if isinstance(value, dt.datetime):
        return {'__ts__': value.isoformat()}
    if isinstance(value, dt.date):
        return {'__date__': value.isoformat()}
    if isinstance(value, dt.time):
        return {'__time__': value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'__b64__': base64.b64encode(bytes(value)).decode('ascii')}
    if isinstance(value, float):
        # Oracle BINARY_DOUBLE / FLOAT. Kept as a string for the same reason.
        return {'__float__': repr(value)}
    if isinstance(value, (list, tuple)):
        # Postgres arrays — `periodic_report_sends.recipients` is text[].
        # Without this the list falls through to str() and comes back as the
        # literal "['a@x', 'b@y']", a string where the schema wants an array:
        # the load fails, or worse, succeeds into a text column.
        return {'__list__': [_encode(v) for v in value]}
    return str(value)


def _oracle_columns(cur, table, skip):
    """Column metadata from production's catalogue, in declaration order."""
    cur.execute("""
        SELECT column_name, data_type, data_length, data_precision, data_scale,
               char_used, nullable, char_length
        FROM all_tab_columns
        WHERE owner = 'RPS' AND table_name = :t
        ORDER BY column_id
    """, t=table)
    out = []
    for (name, dtype, length, prec, scale, char_used,
         nullable, char_len) in cur.fetchall():
        if name in skip or dtype in ('CLOB', 'NCLOB', 'BLOB', 'LONG',
                                     'LONG RAW', 'XMLTYPE'):
            continue
        out.append({'name': name, 'type': dtype, 'length': length,
                    'precision': prec, 'scale': scale, 'char_used': char_used,
                    'nullable': nullable == 'Y', 'char_length': char_len})
    return out


def _ddl_type(c):
    t, prec, scale = c['type'], c['precision'], c['scale']
    if t == 'NUMBER':
        if prec is None:
            return 'NUMBER'
        return f'NUMBER({prec},{scale or 0})'
    if t in ('VARCHAR2', 'CHAR'):
        n = c['char_length'] or c['length'] or 1
        unit = 'CHAR' if c['char_used'] == 'C' else 'BYTE'
        return f'{t}({n} {unit})'
    if t in ('NVARCHAR2', 'NCHAR'):
        return f'{t}({c["char_length"] or c["length"] or 1})'
    if t.startswith('TIMESTAMP'):
        return t
    if t == 'RAW':
        return f'RAW({c["length"]})'
    return t                       # DATE, FLOAT, BINARY_DOUBLE, ...


def _where(table, binds, depth=0):
    """The row filter for one table, as SQL plus bind names.

    A child is filtered by its PARENT'S predicate through a subquery rather
    than by a list of parent keys: there are tens of thousands of them, Oracle
    caps an IN list at 1000, and REPORTUSER has SELECT and nothing else — there
    is no temp table to stage them in. Recursion handles ADJ_QTY, which is two
    hops from a date.
    """
    t = ORACLE_BY_NAME[table]
    if t.mode == 'reference':
        return ''
    if t.mode == 'window':
        binds['from_date'] = binds.get('from_date')
        return (f'WHERE {t.date_column} >= :from_date '
                f'AND {t.date_column} < :to_exclusive')
    parent = ORACLE_BY_NAME[t.parent]
    inner = _where(parent.name, binds, depth + 1)
    return (f'WHERE {t.parent_key} IN '
            f'(SELECT {t.parent_pk} FROM RPS.{parent.name} {inner})')


def _dump_oracle(conn, bundle, window, salt, anon_staff, dry_run):
    cur = conn.cursor()
    ddl, counts, files = [], {}, {}

    for spec in ORACLE_TABLES:
        cols = _oracle_columns(cur, spec.name, set(spec.skip))
        if not cols:
            raise RuntimeError(f'{spec.name}: no readable columns')

        body = ',\n'.join(f'  "{c["name"]}" {_ddl_type(c)}' for c in cols)
        ddl.append(f'CREATE TABLE RPS.{spec.name} (\n{body}\n);')

        present = {c['name'] for c in cols}
        anon_cols = set(spec.anonymise)
        if anon_staff:
            anon_cols |= set(STAFF_NAME_COLUMNS.get(spec.name, ()))
        # A LOB column named for anonymisation is skipped by design, not by
        # accident, so exclude those before checking the rest really exist.
        _check_columns_exist(anon_cols - set(spec.skip), present,
                             f'ORACLE_TABLES[{spec.name}].anonymise')
        anon_cols &= present

        binds = {'from_date': window[0], 'to_exclusive': window[1]}
        names = ', '.join(f'"{c["name"]}"' for c in cols)
        sql = f'SELECT {names} FROM RPS.{spec.name} {_where(spec.name, binds)}'
        use_binds = binds if spec.mode != 'reference' else {}

        started = time.time()
        if dry_run:
            cur.execute(f'SELECT COUNT(*) FROM RPS.{spec.name} '
                        f'{_where(spec.name, binds)}', **use_binds)
            n = cur.fetchone()[0]
            counts[spec.name] = n
            print(f'  {spec.name:20} {spec.mode:10} {n:>9,} rows  '
                  f'{len(cols):>3} cols  {len(anon_cols)} faked')
            continue

        cur.execute(sql, **use_binds)
        buf = io.BytesIO()
        n = 0
        with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
            col_names = [c['name'] for c in cols]
            gz.write((json.dumps({'columns': col_names}) + '\n').encode())
            while True:
                rows = cur.fetchmany(BATCH)
                if not rows:
                    break
                chunk = []
                for row in rows:
                    rec = list(row)
                    for i, name in enumerate(col_names):
                        if name in anon_cols:
                            rec[i] = anonymise_value(name, rec[i], salt)
                    chunk.append(json.dumps([_encode(v) for v in rec],
                                            ensure_ascii=False))
                    n += 1
                gz.write(('\n'.join(chunk) + '\n').encode('utf-8'))
        files[f'oracle/{spec.name}.jsonl.gz'] = buf.getvalue()
        counts[spec.name] = n
        print(f'  {spec.name:20} {spec.mode:10} {n:>9,} rows  '
              f'{len(cols):>3} cols  {len(anon_cols)} faked  '
              f'{len(buf.getvalue())/1e6:6.1f} MB  {time.time()-started:5.1f}s')

    cur.close()
    if not dry_run:
        files['oracle/ddl.sql'] = ('\n\n'.join(ddl) + '\n').encode('utf-8')
    bundle.update(files)
    return counts


def _dev_account_sid(cur):
    """The one user row the development sign-in will offer, renamed in place.

    Picked by ROLE rather than by name or id, so it survives the account list
    changing. Lowest sid breaks a tie deterministically — two extracts of the
    same database must produce the same dev account or the picker moves around
    between machines.
    """
    cur.execute("""
        SELECT u.sid FROM users u
        JOIN roles r ON r.id = u.role_id
        WHERE lower(r.name) = %s AND u.is_active
        ORDER BY u.sid
        LIMIT 1
    """, (DEV_ACCOUNT_ROLE,))
    row = cur.fetchone()
    return row[0] if row else None


def _dump_postgres(conn, bundle, salt, dry_run, dev_password_hash):
    cur = conn.cursor()
    dev_sid = _dev_account_sid(cur)
    if dev_sid is None:
        raise RuntimeError(
            f'No active {DEV_ACCOUNT_ROLE} user found — the development '
            f'sign-in would have no account to offer.')
    cur.execute("""
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_schema IN ('public', 'rps') AND table_type = 'BASE TABLE'
        ORDER BY table_schema, table_name
    """)
    tables = cur.fetchall()
    counts = {}
    for schema, table in tables:
        cur.execute("""
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position
        """, (schema, table))
        meta = cur.fetchall()
        cols = [r[0] for r in meta]
        # Decided by the COLUMN type, never the value's. A jsonb column holds
        # an object OR an array, and an array value would otherwise be encoded
        # as a Postgres array and loaded into the wrong type entirely.
        json_cols = {c for c, t in meta if t in ('json', 'jsonb')}
        drop = set(POSTGRES_DROP_COLUMNS.get(table, ()))
        cols = [c for c in cols if c not in drop]
        _check_columns_exist(POSTGRES_ANONYMISE.get(table, ()), cols,
                             f'POSTGRES_ANONYMISE[{table}]')
        replace = POSTGRES_REPLACE.get(table, {})
        _check_columns_exist(replace, cols, f'POSTGRES_REPLACE[{table}]')
        anon = set(POSTGRES_ANONYMISE.get(table, ())) & set(cols)

        key = f'{schema}.{table}'
        if dry_run:
            cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
            counts[key] = cur.fetchone()[0]
            print(f'  {key:42} {counts[key]:>8,} rows  '
                  f'{len(anon)} faked  {len(drop)} dropped')
            continue

        names = ', '.join(f'"{c}"' for c in cols)
        cur.execute(f'SELECT {names} FROM "{schema}"."{table}"')
        buf = io.BytesIO()
        n = 0
        with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
            gz.write((json.dumps({'columns': cols, 'schema': schema,
                                  'table': table}) + '\n').encode())
            while True:
                rows = cur.fetchmany(BATCH)
                if not rows:
                    break
                chunk = []
                for row in rows:
                    rec = list(row)
                    # Rename the chosen admin in the bundle itself, so a real
                    # colleague's name is never written to a laptop at all.
                    if (table == 'users' and 'sid' in cols
                            and rec[cols.index('sid')] == dev_sid
                            and 'full_name' in cols):
                        rec[cols.index('full_name')] = DEV_ACCOUNT_NAME
                    for i, c in enumerate(cols):
                        if c in replace:
                            rec[i] = dev_password_hash
                        elif c in anon:
                            rec[i] = anonymise_value(c, rec[i], salt)
                        elif c in json_cols and rec[i] is not None:
                            # As JSON TEXT. psycopg2 hands these back as Python
                            # objects, and str() on a dict yields a Python repr
                            # with single quotes — which Postgres rejects as
                            # invalid JSON on the way back in.
                            rec[i] = {'__json__': json.dumps(rec[i],
                                                             ensure_ascii=False)}
                    chunk.append(json.dumps([_encode(v) for v in rec],
                                            ensure_ascii=False))
                    n += 1
                gz.write(('\n'.join(chunk) + '\n').encode('utf-8'))
        bundle[f'postgres/{schema}.{table}.jsonl.gz'] = buf.getvalue()
        counts[key] = n
        print(f'  {key:42} {counts[key]:>8,} rows  '
              f'{len(anon)} faked  {len(drop)} dropped')
    cur.close()
    return counts


def main():
    today = dt.date.today()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--from', dest='date_from',
                        default=dt.date(today.year - 1, 1, 1).isoformat(),
                        help='Window start, YYYY-MM-DD (default: 1 Jan last '
                             'year, so year-on-year comparisons reconcile)')
    parser.add_argument('--to', dest='date_to', default=today.isoformat(),
                        help='Window end, exclusive (default: today)')
    parser.add_argument('--salt', default=DEFAULT_SALT,
                        help='Pseudonym salt. Recorded in the manifest so a '
                             'later extract can reproduce the same names.')
    parser.add_argument('--anonymise-staff', action='store_true',
                        help='Also fake employee names. Off by default — see '
                             'spec.py, it breaks commission joins unless both '
                             'databases are done together.')
    parser.add_argument('--out', default=SEED_DIR)
    parser.add_argument('--dry-run', action='store_true',
                        help='Count rows and write nothing.')
    args = parser.parse_args()

    window = (dt.date.fromisoformat(args.date_from),
              dt.date.fromisoformat(args.date_to))
    print(f'Window: {window[0]} .. {window[1]} (exclusive)\n')

    from app.core.database import (get_oracle_connection,      # noqa: E402
                                   get_postgres_connection)

    # One hash, computed once: bcrypt is deliberately slow and there is no
    # reason for every user row to pay for it.
    import bcrypt
    dev_password_hash = bcrypt.hashpw(DEV_PASSWORD.encode(),
                                      bcrypt.gensalt()).decode()

    bundle = {}
    print('ORACLE')
    ora = get_oracle_connection()
    try:
        ora_counts = _dump_oracle(ora, bundle, window, args.salt,
                                  args.anonymise_staff, args.dry_run)
    finally:
        ora.close()

    print('\nPOSTGRES')
    pg = get_postgres_connection()
    try:
        pg_counts = _dump_postgres(pg, bundle, args.salt, args.dry_run,
                                   dev_password_hash)
    finally:
        pg.close()

    if args.dry_run:
        print(f'\nDRY RUN — nothing written. '
              f'{sum(ora_counts.values()):,} Oracle rows, '
              f'{sum(pg_counts.values()):,} Postgres rows.')
        return 0

    print('\nLEAK SCAN')
    leaks = _scan_for_leaks(bundle)
    if leaks:
        print('  REFUSING TO WRITE — real contact data survived anonymisation:')
        for name, hits in sorted(leaks.items()):
            print(f'    {name}: {", ".join(hits)}')
        print('\n  Add the offending columns to scripts/dev/spec.py and re-run.')
        return 1
    print(f'  clean — no real contact data in {len(bundle)} files')

    manifest = {
        'created': dt.datetime.now().isoformat(timespec='seconds'),
        'window': {'from': window[0].isoformat(), 'to': window[1].isoformat()},
        'salt': args.salt,
        'anonymise_staff': args.anonymise_staff,
        'oracle_rows': ora_counts,
        'postgres_rows': pg_counts,
        'dev_password': DEV_PASSWORD,
        'dev_account': DEV_ACCOUNT_NAME,
        'format': 1,
    }
    bundle['manifest.json'] = json.dumps(manifest, indent=2,
                                         ensure_ascii=False).encode('utf-8')

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f'gbl-dev-seed-{today.isoformat()}.tar.gz')
    with tarfile.open(path, 'w:gz') as tar:
        for name in sorted(bundle):
            data = bundle[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))

    size = os.path.getsize(path)
    sha = hashlib.sha256(open(path, 'rb').read()).hexdigest()[:16]
    print(f'\nWrote {path}')
    print(f'  {size/1e6:.1f} MB   sha256:{sha}')
    print(f'  {sum(ora_counts.values()):,} Oracle rows, '
          f'{sum(pg_counts.values()):,} Postgres rows')
    print('\nCopy this file to any machine and run:')
    print(f'  python scripts/dev/load_seed.py {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
