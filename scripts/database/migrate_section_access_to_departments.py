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
    """Who can see `code` under the NEW rules (department only)."""
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
    keep_depts = set()
    for dept, members in by_dept.items():
        candidates = members - excl
        if candidates and candidates <= target:
            keep_depts.add(dept)

    covered = set()
    for dept in keep_depts:
        covered |= (by_dept[dept] - excl)
    incl = target - covered

    return keep_depts, incl, excl, target


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('--apply', action='store_true',
                    help='write the changes (default: report only)')
    ap.add_argument('--allow-delta', action='store_true',
                    help='permit --apply even where access would change')
    args = ap.parse_args()

    conn = get_postgres_connection()
    if conn is None:
        raise SystemExit('Could not connect to PostgreSQL')
    try:
        cur = conn.cursor()
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
            return

        if total_delta and not args.allow_delta:
            raise SystemExit(
                f'\nRefusing to apply: access would change for {total_delta} '
                'user/section pairs. Re-run with --allow-delta if that is '
                'genuinely intended.')

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
            # section_roles is left untouched: dead to resolution, and it is the
            # only record of what the old rules granted.
        conn.commit()
        print('\nApplied.')
        cur.close()
    finally:
        conn.close()


if __name__ == '__main__':
    main()
