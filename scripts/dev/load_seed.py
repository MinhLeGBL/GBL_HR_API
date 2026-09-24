"""
Load a development seed bundle into the local containers.

Phase B of two. Needs NO production access — that is the whole point. Copy the
bundle from `extract_seed.py` onto any machine, start the containers, run this.

    cd dev && docker compose up -d && cd ..
    FLASK_ENV=local python scripts/dev/load_seed.py dev/seed/<bundle>.tar.gz

Oracle's first container start creates the database and takes several minutes;
this waits for it.

SAFETY
------
This script writes. It TRUNCATES and REPLACES every table it touches, so
pointing it at the wrong database would destroy production. It therefore
refuses to run against anything that is not plainly local — see
`_refuse_unless_local`. `.env.development` in this repo points at PRODUCTION,
which is exactly the accident worth engineering against.

Usage:
    ... load_seed.py <bundle>            both databases
    ... load_seed.py <bundle> --oracle   Oracle only
    ... load_seed.py <bundle> --postgres Postgres only

Exit codes:
    0 — loaded
    1 — refused, or failed
    2 — containers not ready
"""
import argparse
import base64
import datetime as dt
import decimal
import gzip
import io
import json
import os
import re
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

env = os.getenv('FLASK_ENV', 'development')
env_file = f'.env.{env}'
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

from dev.spec import ORACLE_TABLES                              # noqa: E402

# Rows per round trip. Scaled DOWN for wide tables in `_batch_size` — 1,000
# rows of DOCUMENT is 382,000 binds at once, which is a lot of memory on a
# laptop-sized container.
BATCH = 1_000
MAX_BINDS = 40_000


def _batch_size(columns):
    return max(50, min(BATCH, MAX_BINDS // max(1, len(columns))))
LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1', '0.0.0.0'}


def _decode(v):
    if isinstance(v, dict):
        if '__dec__' in v:
            return decimal.Decimal(v['__dec__'])
        if '__ts__' in v:
            return dt.datetime.fromisoformat(v['__ts__'])
        if '__date__' in v:
            return dt.date.fromisoformat(v['__date__'])
        if '__b64__' in v:
            return base64.b64decode(v['__b64__'])
        if '__float__' in v:
            return float(v['__float__'])
        if '__list__' in v:
            return [_decode(x) for x in v['__list__']]
        if '__time__' in v:
            return dt.time.fromisoformat(v['__time__'])
        if '__json__' in v:
            # Returned as TEXT. Postgres infers the parameter type from the
            # target column and parses it, so a json/jsonb column gets real
            # JSON rather than a quoted string.
            return v['__json__']
    return v


def _refuse_unless_local():
    """Abort unless both databases are plainly on this machine.

    Not a formality. This script truncates every table it loads, and the
    repository ships a `.env.development` pointing at the production server —
    one wrong FLASK_ENV and the command that sets up a laptop wipes the
    company's database instead. The check is on the host and the tunnel because
    those are the two things that decide WHERE the writes land.
    """
    problems = []
    pg_host = (os.getenv('POSTGRES_HOST') or '').strip()
    ora_host = (os.getenv('DB_HOST') or '').strip()
    tunnel = (os.getenv('USE_SSH_TUNNEL') or '').strip().lower()

    if pg_host not in LOCAL_HOSTS:
        problems.append(f'POSTGRES_HOST is {pg_host!r}, not local')
    if ora_host not in LOCAL_HOSTS:
        problems.append(f'DB_HOST is {ora_host!r}, not local')
    if tunnel in ('true', '1', 'yes', 'on'):
        problems.append('USE_SSH_TUNNEL is on — that tunnels to the server')

    if problems:
        print('REFUSING TO RUN. This loads over the top of whatever it is '
              'pointed at,\nand this does not look like a local database:\n')
        for p in problems:
            print(f'  - {p}')
        print(f'\nFLASK_ENV={env!r} loaded {env_file!r}.')
        print('Development should use the local containers: copy '
              '.env.development.example\nto .env.development. Production is '
              'reached only with FLASK_ENV=remote.')
        return False
    print(f'Target: postgres://{pg_host}:{os.getenv("POSTGRES_PORT")} '
          f'and oracle://{ora_host}:{os.getenv("DB_PORT")}  (both local)\n')
    return True


def _read_bundle(path):
    out = {}
    with tarfile.open(path, 'r:gz') as tar:
        for member in tar.getmembers():
            if member.isfile():
                out[member.name] = tar.extractfile(member).read()
    return out


def _rows(blob):
    """Yield (columns, row) from one .jsonl.gz member."""
    text = gzip.decompress(blob).decode('utf-8')
    lines = text.splitlines()
    header = json.loads(lines[0])
    cols = header['columns']
    for line in lines[1:]:
        if line:
            yield cols, [_decode(v) for v in json.loads(line)]


def _bind_types(ddl_text):
    """{TABLE: [oracledb type per column]}, parsed from the bundle's own DDL.

    WHY THIS IS NOT OPTIONAL
    ------------------------
    `executemany` infers each bind's type from the FIRST row of the batch. Many
    of these columns are NULL in their first row — DOCUMENT alone is 268 nulls
    out of 382 — so oracledb guesses VARCHAR, and the load dies the moment a
    later row carries a real datetime:

        DPY-3013: unsupported Python type datetime for database type
                  DB_TYPE_VARCHAR

    Declaring the types removes the guess. The DDL is in the bundle and was
    generated from production's own catalogue, so it is the authority on what
    each column actually is.
    """
    import oracledb
    # ONLY the types that get inferred WRONGLY. Character columns are
    # deliberately left as None, meaning "work it out from the data".
    #
    # Declaring them looked more thorough and was much worse: with no length,
    # oracledb allocates a maximum-width buffer per column per row, so a batch
    # of 1,000 DOCUMENT rows x 382 columns reserves gigabytes. It took the
    # database down mid-load —
    #     DPY-4011: the database or network closed the connection
    # — which reads like a container or network fault and is neither.
    #
    # Strings infer correctly from their own values anyway. The columns that do
    # not are dates and numbers, because a NULL in row one tells oracledb
    # nothing and it guesses VARCHAR.
    scalar = {
        'NUMBER': oracledb.DB_TYPE_NUMBER,
        'FLOAT': oracledb.DB_TYPE_BINARY_DOUBLE,
        'BINARY_DOUBLE': oracledb.DB_TYPE_BINARY_DOUBLE,
        'BINARY_FLOAT': oracledb.DB_TYPE_BINARY_FLOAT,
        'DATE': oracledb.DB_TYPE_DATE,
    }
    out = {}
    for stmt in ddl_text.split(';'):
        m = re.search(r'CREATE TABLE RPS\.(\w+)', stmt)
        if not m:
            continue
        types = []
        for _name, decl in re.findall(r'^\s+"(\w+)" (.+?),?$', stmt, re.M):
            d = decl.strip().rstrip(',').upper()
            if d.startswith('TIMESTAMP'):
                # The distinction that decides whether evening sales land on
                # the right day — see the session timezone note below.
                types.append(oracledb.DB_TYPE_TIMESTAMP_TZ
                             if 'WITH TIME ZONE' in d
                             else oracledb.DB_TYPE_TIMESTAMP)
                continue
            base = re.sub(r'\(.*', '', d).strip()
            types.append(scalar.get(base))          # None = infer
        out[m.group(1)] = types
    return out


def _wait_for_oracle(timeout=900):
    import oracledb
    started = time.time()
    last = None
    while time.time() - started < timeout:
        try:
            conn = oracledb.connect(
                user=os.getenv('DB_USERNAME'), password=os.getenv('DB_PASSWORD'),
                host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT')),
                service_name=os.getenv('DB_SERVICE_NAME'))
            return conn
        except Exception as e:                                  # noqa: BLE001
            msg = str(e).splitlines()[0]
            if msg != last:
                print(f'  waiting for Oracle... {msg}')
                last = msg
            time.sleep(10)
    return None


def _ensure_oracle_privileges():
    """Give RPS room to create 19 tables and insert ~582,000 rows.

    Issued over SQL as SYSTEM rather than mounted into the container as an
    init script: a bind mount of a repo directory is a reliable source of
    grief on Windows, and this needs to behave identically on both machines.

    Best effort. The image's own APP_USER setup may already have granted all
    of it, in which case these are harmless no-ops; if the password is not to
    hand, CREATE TABLE below will say so plainly.
    """
    import oracledb
    password = os.getenv('ORACLE_SYSTEM_PASSWORD')
    if not password:
        return
    try:
        conn = oracledb.connect(
            user='SYSTEM', password=password,
            host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT')),
            service_name=os.getenv('DB_SERVICE_NAME'))
    except Exception as e:                                      # noqa: BLE001
        print(f'  (could not connect as SYSTEM to check privileges: '
              f'{str(e).splitlines()[0][:60]})')
        return
    user = os.getenv('DB_USERNAME', 'RPS')
    with conn.cursor() as cur:
        for stmt in (f'ALTER USER {user} QUOTA UNLIMITED ON USERS',
                     f'GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, '
                     f'CREATE SEQUENCE, CREATE SYNONYM TO {user}'):
            try:
                cur.execute(stmt)
            except Exception:                                   # noqa: BLE001
                pass
    conn.commit()
    conn.close()
    print(f'  privileges confirmed for {user}')


# RetailPro stores wall-clock Vietnam time with a +07:00 offset. Vietnam has
# no daylight saving, so this is exact rather than an approximation.
REPORT_TZ = dt.timezone(dt.timedelta(hours=7))


def _insert(conn, sql, batch, tz_positions):
    """One batch, on a FRESH cursor, with timestamps made timezone-aware.

    TWO BUGS MET HERE, AND THE FIX FOR THE FIRST CAUSED THE SECOND.

    1. `executemany` infers each bind's type from the first non-None value,
       and then REUSES that decision for every later call on the same cursor.
       A batch where a column is entirely NULL settles it as VARCHAR, and the
       next batch carrying a real datetime dies with
       `DPY-3013: unsupported Python type datetime for database type
       DB_TYPE_VARCHAR`. A new cursor per batch scopes the inference to the
       batch, where it is always correct.

    2. Declaring the types with `setinputsizes` fixed that and silently broke
       something worse: `setinputsizes(DB_TYPE_TIMESTAMP_TZ)` DISCARDS the
       tzinfo, storing +00:00 where production has +07:00. Measured:

           aware, no setinputsizes    -> 19:56:52 +07:00   correct
           aware, setinputsizes TSTZ  -> 19:56:52 +00:00   wrong
           naive, setinputsizes TSTZ  -> 19:56:52 +00:00   wrong

       `CAST(... AS DATE)` then converts to the session's +07:00 and moves a
       19:56 sale on the 13th to the 14th. Seven evening sales left week 37
       and three entered it: nothing errored, the week looked complete, and it
       under-reported by 178 million dong.

    Inference needs no help — it scans past leading NULLs on its own. Making
    the values aware in Python is what makes it land on +07:00.
    """
    if tz_positions:
        for row in batch:
            for i in tz_positions:
                v = row[i]
                if isinstance(v, dt.datetime) and v.tzinfo is None:
                    row[i] = v.replace(tzinfo=REPORT_TZ)
    with conn.cursor() as c:
        c.executemany(sql, batch)


def _load_oracle(bundle, manifest):
    print('ORACLE')
    conn = _wait_for_oracle()
    if conn is None:
        print('  Oracle never came up. `cd dev && docker compose logs oracle`')
        return 2
    _ensure_oracle_privileges()
    cur = conn.cursor()

    # THE SESSION TIMEZONE IS NOT A DETAIL HERE.
    #
    # INVC_POST_DATE — the column the weekly report filters and groups on — is
    # TIMESTAMP(0) WITH TIME ZONE, and the driver returns it with tzinfo=None.
    # So the bundle carries naive wall-clock values, which is what production
    # itself reads.
    #
    # Inserting a naive value into such a column makes Oracle attach the
    # SESSION's offset. A container defaults to UTC, so without this line every
    # timestamp would be stored as +00:00 while meaning +07:00. Reports would
    # still run, and evening sales in Vietnam would quietly land on the day
    # before — a small, plausible, entirely wrong daily total.
    #
    # Vietnam does not observe daylight saving, so a fixed offset is exact.
    cur.execute("ALTER SESSION SET TIME_ZONE = '+07:00'")

    ddl = bundle['oracle/ddl.sql'].decode('utf-8')
    bind_types = _bind_types(ddl)
    import oracledb as _odb
    tz_cols = {t: [i for i, ty in enumerate(types)
                   if ty is _odb.DB_TYPE_TIMESTAMP_TZ]
               for t, types in bind_types.items()}
    for spec in ORACLE_TABLES:
        try:
            cur.execute(f'DROP TABLE RPS.{spec.name} CASCADE CONSTRAINTS')
        except Exception:                                       # noqa: BLE001
            pass                    # first run — nothing to drop
    for statement in [s.strip() for s in ddl.split(';\n') if s.strip()]:
        cur.execute(statement.rstrip(';'))
    conn.commit()
    print(f'  created {len(ORACLE_TABLES)} tables')

    total = 0
    for spec in ORACLE_TABLES:
        member = f'oracle/{spec.name}.jsonl.gz'
        if member not in bundle:
            continue
        started, n, cols, batch = time.time(), 0, None, []
        sql = None
        for cols, row in _rows(bundle[member]):
            if sql is None:
                binds = ', '.join(f':{i+1}' for i in range(len(cols)))
                names = ', '.join(f'"{c}"' for c in cols)
                sql = f'INSERT INTO RPS.{spec.name} ({names}) VALUES ({binds})'
            batch.append(row)
            if len(batch) >= _batch_size(cols):
                _insert(conn, sql, batch, tz_cols.get(spec.name))
                n += len(batch)
                batch = []
        if batch:
            _insert(conn, sql, batch, tz_cols.get(spec.name))
            n += len(batch)
        conn.commit()
        total += n
        print(f'  {spec.name:20} {n:>9,} rows  {time.time()-started:5.1f}s')

    cur.close()
    conn.close()
    print(f'  {total:,} rows\n')
    return 0


def _load_postgres(bundle, manifest):
    print('POSTGRES')
    from app.core.database import get_postgres_connection

    # The schema comes from init_db.py, which is already the source of truth
    # for it and is idempotent. Reimplementing it here would give us a second
    # definition to keep in step with the first.
    print('  creating schema via scripts/database/init_db.py')
    import subprocess
    r = subprocess.run([sys.executable, 'scripts/database/init_db.py'],
                       capture_output=True, text=True,
                       env={**os.environ, 'FLASK_ENV': env})
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        return 1

    conn = get_postgres_connection()
    cur = conn.cursor()
    members = sorted(m for m in bundle if m.startswith('postgres/'))

    # A bundle comes from PRODUCTION, which runs whatever is merged. The
    # machine loading it is on some branch, which may not have that code yet —
    # `pnl_*` lives on an unmerged feature branch, so init_db.py cannot create
    # those tables here. Skipping them with a note is correct; dying is not,
    # and neither is creating them behind the feature's back.
    cur.execute("""
        SELECT table_schema || '.' || table_name
        FROM information_schema.tables
        WHERE table_schema IN ('public', 'rps') AND table_type = 'BASE TABLE'
    """)
    existing = {r[0] for r in cur.fetchall()}

    targets, loadable, skipped = [], [], []
    for m in members:
        head = json.loads(gzip.decompress(bundle[m]).decode('utf-8')
                          .splitlines()[0])
        key = f'{head["schema"]}.{head["table"]}'
        if key in existing:
            loadable.append(m)
            targets.append(f'"{head["schema"]}"."{head["table"]}"')
        else:
            skipped.append(key)
    members = loadable

    if skipped:
        print(f'  skipping {len(skipped)} table(s) this branch has no schema '
              f'for: {", ".join(sorted(skipped))}')

    # Truncate everything first, then load: the tables carry foreign keys and
    # per-table truncate-then-insert fails on whichever order they happen to
    # come in. One CASCADE over the lot sidesteps the ordering problem.
    cur.execute(f'TRUNCATE {", ".join(targets)} RESTART IDENTITY CASCADE')

    # Load with foreign keys not enforced.
    #
    # Tables arrive in alphabetical order, so `dashboard_configs` lands before
    # the `users` it references. Sorting 39 tables topologically would work
    # until the first circular reference — and these already have one, since
    # Permissions and Auth reference each other, which is the same knot that
    # broke init_db.py on an empty database.
    #
    # `session_replication_role = replica` is what pg_restore itself does: the
    # constraints stay DEFINED and are enforced again the moment this session
    # ends. The data being loaded came out of a database that already
    # satisfied them.
    cur.execute('SET session_replication_role = replica')
    conn.commit()
    print(f'  truncated {len(targets)} tables '
          f'(foreign keys deferred for the load)')

    total = 0
    for m in members:
        head = json.loads(gzip.decompress(bundle[m]).decode('utf-8')
                          .splitlines()[0])
        schema, table = head['schema'], head['table']
        n, batch, sql = 0, [], None
        for cols, row in _rows(bundle[m]):
            if sql is None:
                names = ', '.join(f'"{c}"' for c in cols)
                ph = ', '.join(['%s'] * len(cols))
                sql = f'INSERT INTO "{schema}"."{table}" ({names}) VALUES ({ph})'
            batch.append(row)
            if len(batch) >= BATCH:
                cur.executemany(sql, batch)
                n += len(batch)
                batch = []
        if batch:
            cur.executemany(sql, batch)
            n += len(batch)
        conn.commit()
        total += n
        if n:
            print(f'  {schema}.{table:38} {n:>8,} rows')

    # Sequences are left behind by an explicit-id insert: the next INSERT
    # without an id would collide with row 1. Nothing about that is obvious
    # until the first thing you create locally fails.
    cur.execute('SET session_replication_role = DEFAULT')
    conn.commit()

    cur.execute("""
        SELECT quote_ident(s.schemaname) || '.' || quote_ident(s.sequencename),
               quote_ident(s.schemaname) || '.' || quote_ident(t.relname),
               quote_ident(a.attname)
        FROM pg_sequences s
        JOIN pg_class c   ON c.relname = s.sequencename
        JOIN pg_depend d  ON d.objid = c.oid AND d.deptype = 'a'
        JOIN pg_class t   ON t.oid = d.refobjid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
        WHERE s.schemaname IN ('public', 'rps')
    """)
    fixed = 0
    for seq, tbl, col in cur.fetchall():
        cur.execute(f'SELECT setval(%s, COALESCE((SELECT MAX({col}) '
                    f'FROM {tbl}), 0) + 1, false)', (seq,))
        fixed += 1
    conn.commit()
    print(f'  reset {fixed} sequences')

    cur.close()
    conn.close()
    print(f'  {total:,} rows\n')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('bundle')
    parser.add_argument('--oracle', action='store_true', help='Oracle only')
    parser.add_argument('--postgres', action='store_true', help='Postgres only')
    args = parser.parse_args()

    if not _refuse_unless_local():
        return 1
    if not os.path.exists(args.bundle):
        print(f'No such bundle: {args.bundle}')
        return 1

    bundle = _read_bundle(args.bundle)
    manifest = json.loads(bundle['manifest.json'])
    print(f'Bundle  : {args.bundle}')
    print(f'Created : {manifest["created"]}')
    print(f'Window  : {manifest["window"]["from"]} .. {manifest["window"]["to"]}')
    print(f'Rows    : {sum(manifest["oracle_rows"].values()):,} Oracle, '
          f'{sum(manifest["postgres_rows"].values()):,} Postgres\n')

    both = not (args.oracle or args.postgres)
    if both or args.oracle:
        rc = _load_oracle(bundle, manifest)
        if rc:
            return rc
    if both or args.postgres:
        rc = _load_postgres(bundle, manifest)
        if rc:
            return rc

    print('Done. Every local account signs in with the password '
          f'{manifest.get("dev_password", "devpassword")!r}.')
    print('Check it with:  FLASK_ENV=local python scripts/dev/verify_seed.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
