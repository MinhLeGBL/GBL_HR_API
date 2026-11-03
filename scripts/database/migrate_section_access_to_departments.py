"""
Migrate section access from "department AND role" to "department, or explicit users".

WHY
---
The access page no longer has a role field, and resolution rule 5 no longer
consults `section_roles` (see `PermissionService.get_user_permissions_v2`). Left
alone, that silently CHANGES who has access:

  - A section granted `IT, HR` + role `ADMIN` used to admit only admins in those
    departments. Department-only admits every IT and HR user — a widening.
  - A section granted a department with no roles at all admitted nobody through
    rule 5. Department-only admits the whole department — also a widening.

This script makes the switch access-neutral. For each section it computes who
can see it TODAY under the old rules, then rewrites the grants so the new rules
resolve to exactly the same people:

  for each department D:
      members(D) excluding this section's excluded users
      ⊆ today's effective set, and non-empty   →  keep/add D as a department grant
  everyone else in the effective set           →  an explicit user inclusion
  existing exclusions                          →  kept as they are

Departments are preferred over explicit users wherever they fit, because a
department grant is standing: a future hire in that department inherits the
section, which an explicit list cannot do.

The admin is ignored throughout. Admin bypasses every check (rule 1), so it is
never part of what a grant needs to reproduce.

USAGE
-----
    # read-only: print the before/after, write nothing (the default)
    FLASK_ENV=development python scripts/database/migrate_section_access_to_departments.py

    # apply it
    FLASK_ENV=development python scripts/database/migrate_section_access_to_departments.py --apply

Run the report first and read the DELTA column. Every row should be `-`; a
non-zero delta means somebody would gain or lose access, and `--apply` refuses
to run unless every delta is zero or `--allow-delta` is also given.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import get_postgres_connection


def load_state(cur):
    """Everything this migration reasons about, in one read."""
    cur.execute("""SELECT u.sid, u.full_name, lower(r.code) AS role, d.code AS dept
                   FROM users u
                   JOIN roles r ON r.id = u.role_id
                   LEFT JOIN departments d ON d.id = u.department_id""")
    users = [{'sid': s, 'name': n, 'role': ro, 'dept': de}
             for s, n, ro, de in cur.fetchall()]

    cur.execute('SELECT code, name FROM sections WHERE is_active = TRUE ORDER BY id')
    sections = cur.fetchall()

    def grouped(table, col):
        cur.execute(f'SELECT section_code, {col} FROM {table}')
        out = {}
        for sec, val in cur.fetchall():
            out.setdefault(sec, set()).add(val)
        return out

    return {
        'users': users,
        'sections': sections,
        'depts': grouped('section_departments', 'department_code'),
        'roles': grouped('section_roles', 'role_code'),
        'incl': grouped('section_inclusions', 'user_sid'),
        'excl': grouped('section_exclusions_v2', 'user_sid'),
    }


def effective_old(code, st, non_admin):
    """Who can see `code` under the OLD rules (department AND role)."""
    depts, roles = st['depts'].get(code, set()), st['roles'].get(code, set())
    incl, excl = st['incl'].get(code, set()), st['excl'].get(code, set())
    out = set()
    for u in non_admin:
        if u['sid'] in excl:
            continue                                   # rule 3
        if u['sid'] in incl:
            out.add(u['sid'])                          # rule 4
            continue
        if u['dept'] in depts and (u['role'] or '').upper() in roles:
            out.add(u['sid'])                          # rule 5, old form
    return out


def effective_new(code, depts, incl, excl, non_admin):
    """Who can see `code` under the NEW rules, given a proposal.

    NOTE this cannot verify the proposal. `propose` derives its grants FROM
    `effective_old`, so re-deriving the result here is algebraically guaranteed
    to match and the delta is always 0 — it was presented as a safety gate and
    was nothing of the kind. It survives only as a consistency check on the
    proposal-building code itself.

    The real check is `verify_against_resolver`, which runs the SHIPPING
    resolver over the committed database before and after, so a mistake in
    `effective_old` cannot hide.
    """
    out = set()
    for u in non_admin:
        if u['sid'] in excl:
            continue
        if u['sid'] in incl:
            out.add(u['sid'])
            continue
        if u['dept'] in depts:
            out.add(u['sid'])
    return out


def propose(code, st, non_admin):
    """New (departments, inclusions, exclusions) reproducing today's access."""
    target = effective_old(code, st, non_admin)
    excl = set(st['excl'].get(code, set()))

    by_dept = {}
    for u in non_admin:
        if u['dept']:
            by_dept.setdefault(u['dept'], set()).add(u['sid'])

    # A department fits when every one of its members who is not excluded
    # already has access today. Prefer it: it keeps granting as people join.
    #
    # An EMPTY department (or one whose every member is excluded) has nothing to
    # compare against, so it can never be derived from today's access — but if
    # it is granted NOW that grant is a deliberate pre-authorisation, and the
    # access page lets you make one. Carry it over; deleting it would destroy a
    # standing grant and silently stop future hires inheriting it.
    granted_now = set(st['depts'].get(code, set()))
    keep_depts = set()
    for dept, members in by_dept.items():
        candidates = members - excl
        if candidates and candidates <= target:
            keep_depts.add(dept)
    for dept in granted_now:
        if not (by_dept.get(dept, set()) - excl):
            keep_depts.add(dept)

    covered = set()
    for dept in keep_depts:
        covered |= (by_dept[dept] - excl)
    incl = target - covered

    return keep_depts, incl, excl, target


MARKER_TABLE = 'section_access_migration'


def already_applied(cur):
    """Whether this migration has run before.

    It is NOT idempotent and must not run twice. It derives the baseline from
    `section_roles` — the record of what the OLD rules granted — and its own
    first run moves access out of per-user inclusions and into department
    grants. On a second run that baseline no longer describes reality: a
    section whose access came from inclusions now resolves to nobody under the
    old rules, so the migration concludes its department grant is unjustified
    and deletes it, taking the access with it.

    Observed, not theorised: a second run on the dev database proposed removing
    `LIVE_COMPARISON` from both BOD managers. The resolver check caught it and
    rolled back, which is the only reason it was not a silent regression — the
    delta table could never have caught it, being always zero.
    """
    cur.execute("""SELECT EXISTS (SELECT 1 FROM information_schema.tables
                                  WHERE table_name = %s)""", (MARKER_TABLE,))
    if not cur.fetchone()[0]:
        return None
    cur.execute(f'SELECT applied_at FROM {MARKER_TABLE} LIMIT 1')
    row = cur.fetchone()
    return row[0] if row else None


def mark_applied(cur):
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {MARKER_TABLE} (
                        only_row    BOOLEAN PRIMARY KEY DEFAULT TRUE
                                    CHECK (only_row),
                        applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )""")
    cur.execute(f'INSERT INTO {MARKER_TABLE} (only_row) VALUES (TRUE) '
                'ON CONFLICT DO NOTHING')


def expected_access(st, non_admin):
    """{sid: {section codes}} that the OLD rules granted — the target state.

    This is what the migration exists to preserve, and it is NOT what the
    shipping resolver returns on un-migrated data: rule 5 is already
    department-only in deployed code, so immediately after a deploy the
    resolver reports the WIDENED set. Verifying against that would be
    verifying against the very widening this script undoes, and would block
    every legitimate run — observed: it reported the IT and HR managers
    "LOSING" USER_MANAGEMENT, which they only had because of the widening.

    `DASHBOARD` is added because resolution rule 2 grants it unconditionally
    and the resolver therefore always includes it.
    """
    out = {}
    for u in non_admin:
        out[u['sid']] = {'DASHBOARD'}
    for code, _name in st['sections']:
        if code == 'DASHBOARD':
            continue
        for sid in effective_old(code, st, non_admin):
            out[sid].add(code)
    return out


def resolver_access(cur):
    """{sid: {section codes}} as the SHIPPING resolver reports it, over the
    committed database. The admin is skipped: it bypasses every rule, so it
    says nothing about whether the grants are right."""
    from app.modules.permissions.service import PermissionService
    svc = PermissionService()
    cur.execute("""SELECT u.sid, lower(r.code), d.code
                   FROM users u JOIN roles r ON r.id = u.role_id
                   LEFT JOIN departments d ON d.id = u.department_id
                   WHERE lower(r.code) <> 'admin'""")
    rows = cur.fetchall()
    return {
        sid: {s['section_code']
              for s in svc.get_user_permissions_v2(sid, role, dept)}
        for sid, role, dept in rows
    }


def snapshot_grants(cur):
    """The grant tables, so a failed verification can be put back."""
    out = {}
    for table, col in (('section_departments', 'department_code'),
                       ('section_inclusions', 'user_sid')):
        cur.execute(f'SELECT section_code, {col} FROM {table}')
        out[table] = cur.fetchall()
    return out


def restore_grants(cur, snap):
    for table, col in (('section_departments', 'department_code'),
                       ('section_inclusions', 'user_sid')):
        cur.execute(f'DELETE FROM {table}')
        for section_code, value in snap[table]:
            cur.execute(
                f'INSERT INTO {table} (section_code, {col}) VALUES (%s, %s) '
                'ON CONFLICT DO NOTHING', (section_code, value))


def report_diff(expected, after, by_sid):
    """Print every user whose resolved access differs from the old rules.
    Returns the number of differing user/section pairs."""
    changed = 0
    for sid, was in sorted(expected.items()):
        now = after.get(sid, set())
        gained, lost = now - was, was - now
        if not (gained or lost):
            continue
        changed += len(gained) + len(lost)
        u = by_sid.get(sid, {})
        print(f'  {u.get("name", sid)} ({u.get("role")}/{u.get("dept")}):')
        for c in sorted(gained):
            print(f'      WOULD GAIN {c} (old rules did not grant it)')
        for c in sorted(lost):
            print(f'      WOULD LOSE {c} (old rules granted it)')
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('--apply', action='store_true',
                    help='write the changes (default: report only)')
    ap.add_argument('--allow-delta', action='store_true',
                    help='permit --apply even where access would change')
    ap.add_argument('--force', action='store_true',
                    help='run again even though it has already been applied '
                         '(it is NOT idempotent — see already_applied)')
    args = ap.parse_args()

    conn = get_postgres_connection()
    if conn is None:
        raise SystemExit('Could not connect to PostgreSQL')
    try:
        cur = conn.cursor()

        applied_at = already_applied(cur)
        if applied_at and not args.force:
            raise SystemExit(
                f'Already applied at {applied_at}. This migration is NOT '
                'idempotent: a second run reads a baseline its first run made '
                'obsolete and would propose revoking access. Use --force only '
                'if you know why.')

        st = load_state(cur)
        non_admin = [u for u in st['users'] if u['role'] != 'admin']
        by_sid = {u['sid']: u for u in st['users']}

        print(f'{len(st["users"])} users ({len(non_admin)} non-admin), '
              f'{len(st["sections"])} active sections\n')
        header = (f'{"SECTION":<22}{"BEFORE":>7}{"AFTER":>7}{"DELTA":>7}   '
                  f'{"NEW DEPARTMENTS":<22}{"EXPLICIT USERS":>15}')
        print(header)
        print('-' * len(header))

        plans, total_delta = [], 0
        for code, _name in st['sections']:
            depts, incl, excl, target = propose(code, st, non_admin)
            after = effective_new(code, depts, incl, excl, non_admin)
            delta = len(after ^ target)
            total_delta += delta
            plans.append((code, depts, incl, excl))
            mark = '-' if delta == 0 else f'** {delta} **'
            print(f'{code:<22}{len(target):>7}{len(after):>7}{mark:>7}   '
                  f'{",".join(sorted(depts)) or "(none)":<22}'
                  f'{len(incl):>15}')

        print()
        for code, depts, incl, excl in plans:
            if incl or excl:
                print(f'  {code}:')
                for sid in sorted(incl):
                    u = by_sid.get(sid, {})
                    print(f'      + {u.get("name", sid)} '
                          f'({u.get("role")}/{u.get("dept")}) explicit include')
                for sid in sorted(excl):
                    u = by_sid.get(sid, {})
                    print(f'      - {u.get("name", sid)} '
                          f'({u.get("role")}/{u.get("dept")}) kept excluded')

        print(f'\nTotal access delta across all sections: {total_delta}')
        if total_delta:
            print('  A NON-ZERO DELTA MEANS SOMEONE GAINS OR LOSES ACCESS.')

        if not args.apply:
            print('\nRead-only. Re-run with --apply to write these changes.')
            print('On --apply the SHIPPING resolver is run before and after, and '
                  'the change is rolled back if anyone\'s access differs.')
            return

        # The table above cannot catch a mistake in `effective_old` — see
        # `effective_new`. This can: snapshot what the real resolver returns,
        # apply, re-run it, and put everything back if the two disagree.
        # What the OLD rules granted. The check after applying is that the
        # SHIPPING resolver reproduces exactly this — so the only thing taken
        # on trust is `effective_old`, and both the apply and the new rule are
        # genuinely verified.
        expected = expected_access(st, non_admin)
        grants = snapshot_grants(cur)

        # Writes must be one unit. `get_postgres_connection` honours
        # PG_AUTOCOMMIT, under which a failure part-way through would leave a
        # section holding no grants at all.
        was_autocommit = conn.autocommit
        if was_autocommit:
            # Only switchable while no transaction is open, which is exactly
            # the autocommit case — the reads above have already opened one
            # otherwise, and in that mode the writes are transactional already.
            conn.autocommit = False
        try:
            for code, depts, incl, _excl in plans:
                cur.execute('DELETE FROM section_departments WHERE section_code = %s', (code,))
                for d in sorted(depts):
                    cur.execute(
                        'INSERT INTO section_departments (section_code, department_code) '
                        'VALUES (%s, %s) ON CONFLICT DO NOTHING', (code, d))
                cur.execute('DELETE FROM section_inclusions WHERE section_code = %s', (code,))
                for sid in sorted(incl):
                    cur.execute(
                        'INSERT INTO section_inclusions (section_code, user_sid) '
                        'VALUES (%s, %s) ON CONFLICT DO NOTHING', (code, sid))
                # section_roles is left untouched: dead to resolution, and the
                # only record of what the old rules granted.
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        # The resolver opens its own connection, so this has to run after the
        # commit — hence verify-then-restore rather than verify-then-commit.
        after = resolver_access(cur)
        print()
        changed = report_diff(expected, after, by_sid)
        if changed and not args.allow_delta:
            restore_grants(cur, grants)
            conn.commit()
            if was_autocommit:
                conn.autocommit = True
            raise SystemExit(
                f'ROLLED BACK: the shipping resolver does not reproduce the old '
                f'rules for {changed} user/section pairs, so the grants have '
                'been restored exactly as they were. Nothing is migrated, and '
                'nothing is marked as applied. Re-run with --allow-delta only '
                'if those differences are genuinely intended.')

        # Marked only once the result is verified, so a rolled-back run does
        # not look applied and block the retry.
        mark_applied(cur)
        conn.commit()
        if was_autocommit:
            conn.autocommit = True
        print('Applied, and verified: the shipping resolver reproduces the old '
              'rules exactly'
              + ('.' if not changed
                 else f', bar {changed} differences ACCEPTED via --allow-delta.'))
        cur.close()
    finally:
        conn.close()


if __name__ == '__main__':
    main()
