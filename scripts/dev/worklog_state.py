#!/usr/bin/env python3
"""Derive the true cross-repo state of every feature, from git rather than prose.

MASTER_FEATURE_WORKLOG.md carries two kinds of statement. One is reasoning —
decisions, traps, why something was done the way it was. Only a human writes
that and it does not rot. The other is derivable fact: which branch, what
version, how far from staging, whether anything is still unmerged. That half
was hand-maintained, and on 2026-11-12 the weekly-report entry was found five
weeks stale, claiming work was uncommitted that had shipped through three PRs.

This script owns the second kind so nobody has to type it again.

    worklog_state.py              # print the table
    worklog_state.py --check      # exit 1 if the worklog's block is stale
    worklog_state.py --write      # regenerate the block in the worklog

Branches are DISCOVERED rather than listed here, so a new feature appears by
itself. Run it from anywhere; paths resolve from this file, and it assumes the
two repos are siblings — the same assumption the CR skills make.

KNOWN BLIND SPOT: it compares one directory per feature, so code that MOVES
out of a feature folder reads as deleted. `live-comparison` tripped this on
2026-11-12 — its `DateRangePicker`, `utils/date` and `utils/stores` were
hoisted to `@/shared/` by the custom_reports work, so the branch looked like
it held 696 unshipped lines when nothing had been lost. If a feature shows
UNMERGED and the diff is mostly deletions, check for a move before believing
it.
"""
import argparse
import difflib
import io
import json
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
# scripts/dev/ -> repo root -> GBL_MASTER/
WORKSPACE = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
BE = os.path.join(WORKSPACE, 'GBL_MASTER_API')
FE = os.path.join(WORKSPACE, 'GBL_MASTER_FRONTEND')
WORKLOG = os.path.join(WORKSPACE, 'MASTER_FEATURE_WORKLOG.md')

BEGIN = ('<!-- BEGIN GENERATED STATE -- do not edit by hand; '
         'regenerate with GBL_MASTER_API/scripts/dev/worklog_state.py --write -->')
END = '<!-- END GENERATED STATE -->'

# The one place a branch name does not predict the frontend directory.
FE_BRANCH_ALIAS = {'bathroom': 'bathroom-price-check'}
# Features whose backend module is not just the branch name.
BE_MODULE_OVERRIDE = {'live-comparison': 'reports',
                      'bathroom-price-check': 'bathroom'}


def git(repo: str, *args: str) -> str:
    out = subprocess.run(('git',) + args, cwd=repo,
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ''


def batch_check(repo: str, specs: List[str]) -> Dict[str, Optional[str]]:
    """`<rev>:<path>` -> object SHA (None if absent), in ONE git process.

    Worth the indirection: the straightforward version spawned a git per
    fact — about a hundred of them — and took seven seconds, which is far
    too slow for something that runs at every session start. `cat-file
    --batch-check` answers the whole repo in one.
    """
    if not specs:
        return {}
    out = subprocess.run(('git', 'cat-file', '--batch-check'), cwd=repo,
                         input='\n'.join(specs) + '\n',
                         capture_output=True, text=True)
    found: Dict[str, Optional[str]] = {}
    for spec, line in zip(specs, out.stdout.splitlines()):
        parts = line.split()
        found[spec] = None if len(parts) < 2 or parts[-1] == 'missing' else parts[0]
    return found


def batch_blobs(repo: str, specs: List[str]) -> Dict[str, Optional[str]]:
    """`<rev>:<path>` -> file contents (None if absent), in ONE git process."""
    if not specs:
        return {}
    # bytes, not text: a blob may hold anything, and the record framing is
    # byte-counted, so decoding the stream as a whole would shift every offset.
    out = subprocess.run(('git', 'cat-file', '--batch'), cwd=repo,
                         input=('\n'.join(specs) + '\n').encode(),
                         capture_output=True)
    blobs: Dict[str, Optional[str]] = {}
    data, pos = out.stdout, 0
    for spec in specs:
        nl = data.find(b'\n', pos)
        if nl < 0:
            blobs[spec] = None
            break
        header = data[pos:nl].split()
        if len(header) < 3 or header[-1] == b'missing':
            blobs[spec] = None
            pos = nl + 1
            continue
        size = int(header[2])
        body = data[nl + 1:nl + 1 + size]
        blobs[spec] = body.decode('utf-8', 'replace')
        pos = nl + 1 + size + 1  # trailing newline
    return blobs


def feature_branches(repo: str) -> List[str]:
    raw = git(repo, 'branch', '-r', '--format=%(refname:short)')
    return sorted(b[len('origin/'):] for b in raw.splitlines()
                  if b.startswith('origin/feature/'))


def key_of(branch: str) -> str:
    k = branch[len('feature/'):].replace('_', '-').lower()
    return FE_BRANCH_ALIAS.get(k, k)


def ahead_behind(repo: str, branch: str) -> str:
    """Ahead/behind origin/staging, in that order.

    `--left-right` puts the LEFT ref's exclusive commits first, and the left
    ref here is staging — so the first number is how far BEHIND the branch is.
    Getting this backwards is easy and was in fact gotten backwards once.
    """
    raw = git(repo, 'rev-list', '--left-right', '--count',
              f'origin/staging...origin/{branch}')
    if not raw:
        return '?'
    behind, ahead = raw.split()
    return f'{ahead}/{behind}'


BE_VERSION = r"__version__\s*=\s*['\"]([^'\"]+)['\"]"
FE_VERSION = r"VERSION\s*=\s*['\"]([^'\"]+)['\"]"


def find(pattern: str, blob: Optional[str]) -> Optional[str]:
    if not blob:
        return None
    m = re.search(pattern, blob)
    return m.group(1) if m else None


def verdict(repo: str, branch: str, path: str,
            branch_tree: Optional[str], staging_tree: Optional[str]) -> str:
    """Does this branch hold work on `path` that staging does not have?

    Two traps, and the answer needs both halves to dodge each:

    * Commit counts lie, because both repos squash-merge: a fully shipped
      branch reports commits "ahead" forever. So CONTENT decides, and two
      identical directories have the same tree SHA — which is why this takes
      SHAs rather than running a diff.
    * Comparing against staging alone lies the other way. It is symmetric,
      so a branch that has done nothing at all "differs" the moment staging
      moves underneath it, which reads as unshipped work when the truth is
      the exact opposite.

    So a branch holds unshipped work only if it CHANGED the path since
    diverging AND the content still differs from staging. The second half
    needs a `merge-base`, so it is only asked for the few branches that
    actually differ.
    """
    if branch_tree is None:
        return '-'
    if branch_tree == staging_tree:
        return 'shipped'
    base = git(repo, 'merge-base', 'origin/staging', f'origin/{branch}')
    if not base:
        return 'UNMERGED'
    base_tree = batch_check(repo, [f'{base}:{path}']).get(f'{base}:{path}')
    return 'UNMERGED' if branch_tree != base_tree else 'behind only'


def dirty(repo: str) -> int:
    raw = git(repo, 'status', '--porcelain')
    return len([l for l in raw.splitlines() if l.strip()])


def open_prs(repo: str) -> Optional[int]:
    out = subprocess.run(
        ('gh', 'pr', 'list', '--state', 'open', '--json', 'number'),
        cwd=repo, capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return out.stdout.count('"number"')


def collect(extras: bool = False) -> List[Dict]:
    """Ground truth per feature. `extras` adds the volatile fields.

    Ahead/behind counts are deliberately NOT part of the generated block.
    They move whenever staging moves, through nobody's fault, so a block
    containing them would go stale on every merge and `--check` would cry
    drift at a worklog that is perfectly accurate. Same for open PR counts,
    which also cost a network round trip each. Both are printed for a human,
    never stored. What IS stored only changes when someone ships: the
    versions, and whether the branch still holds unshipped content.
    """
    be_branches = {key_of(b): b for b in feature_branches(BE)}
    fe_branches = {key_of(b): b for b in feature_branches(FE)}
    keys = sorted(set(be_branches) | set(fe_branches))

    be_mod = {k: BE_MODULE_OVERRIDE.get(k, k.replace('-', '_')) for k in keys}
    be_dir = {k: f'app/modules/{be_mod[k]}' for k in keys}
    be_ver = {k: f'{be_dir[k]}/__init__.py' for k in keys}
    fe_dir = {k: f'src/features/{k}' for k in keys}
    fe_ver = {k: f'{fe_dir[k]}/version.ts' for k in keys}

    be_trees = batch_check(BE, [f'origin/{be_branches[k]}:{be_dir[k]}'
                                for k in keys if k in be_branches] +
                               [f'origin/staging:{be_dir[k]}' for k in keys])
    fe_trees = batch_check(FE, [f'origin/{fe_branches[k]}:{fe_dir[k]}'
                                for k in keys if k in fe_branches] +
                               [f'origin/staging:{fe_dir[k]}' for k in keys])
    be_blobs = batch_blobs(BE, [f'origin/{be_branches[k]}:{be_ver[k]}'
                                for k in keys if k in be_branches] +
                               [f'origin/staging:{be_ver[k]}' for k in keys])
    fe_blobs = batch_blobs(FE, [f'origin/{fe_branches[k]}:{fe_ver[k]}'
                                for k in keys if k in fe_branches] +
                               [f'origin/staging:{fe_ver[k]}' for k in keys])

    rows: List[Dict] = []
    for key in keys:
        be_b, fe_b = be_branches.get(key), fe_branches.get(key)
        row: Dict = {'key': key, 'be_branch': be_b, 'fe_branch': fe_b}
        if be_b:
            if extras:
                row['be_ahead_behind'] = ahead_behind(BE, be_b)
            row['be_version'] = find(
                BE_VERSION, be_blobs.get(f'origin/{be_b}:{be_ver[key]}'))
            row['be_staging_version'] = find(
                BE_VERSION, be_blobs.get(f'origin/staging:{be_ver[key]}'))
            row['be_state'] = verdict(
                BE, be_b, be_dir[key],
                be_trees.get(f'origin/{be_b}:{be_dir[key]}'),
                be_trees.get(f'origin/staging:{be_dir[key]}'))
        if fe_b:
            if extras:
                row['fe_ahead_behind'] = ahead_behind(FE, fe_b)
            row['fe_version'] = find(
                FE_VERSION, fe_blobs.get(f'origin/{fe_b}:{fe_ver[key]}'))
            row['fe_staging_version'] = find(
                FE_VERSION, fe_blobs.get(f'origin/staging:{fe_ver[key]}'))
            row['fe_state'] = verdict(
                FE, fe_b, fe_dir[key],
                fe_trees.get(f'origin/{fe_b}:{fe_dir[key]}'),
                fe_trees.get(f'origin/staging:{fe_dir[key]}'))
        rows.append(row)
    return rows


def ver(on_branch: Optional[str], on_staging: Optional[str]) -> str:
    if on_branch is None and on_staging is None:
        return '-'
    if on_branch == on_staging:
        return on_branch or '-'
    return f'{on_branch or "-"} vs staging {on_staging or "-"}'


def render(rows: List[Dict]) -> str:
    """The block stored in the worklog. Stable fields only — see `collect`."""
    lines = [
        BEGIN,
        '',
        '### Generated state — every feature, derived from git',
        '',
        'Nothing below this heading is hand-written. If a version in the prose',
        'elsewhere in this file disagrees with this table, the table is right',
        'and the prose is stale.',
        '',
        '- `shipped` — the feature directory is byte-identical to `staging`.',
        '- `behind only` — the branch changed nothing; staging moved on.',
        '- `UNMERGED` — the branch changed this path and still differs. Look,',
        '  but check for a move to `@/shared/` first (see the script docstring).',
        '',
        'Content decides what shipped, never commit counts: both repos',
        'squash-merge, so a merged branch reports commits "ahead" forever.',
        'Ahead/behind and open-PR counts are deliberately NOT stored here —',
        'they move on their own and would make this block read as stale when',
        'it is not. Run the script with no arguments to see them.',
        '',
        '| feature | BE version | BE | FE version | FE |',
        '|---|---|---|---|---|',
    ]
    for r in rows:
        lines.append(
            f'| `{r["key"]}` '
            f'| {ver(r.get("be_version"), r.get("be_staging_version"))} '
            f'| {r.get("be_state", "-")} '
            f'| {ver(r.get("fe_version"), r.get("fe_staging_version"))} '
            f'| {r.get("fe_state", "-")} |')
    lines += ['', END]
    return '\n'.join(lines)


def table(rows: List[Dict]) -> str:
    """For a human: the block plus the volatile numbers it leaves out."""
    out = [render(rows), '', 'Not stored in the block, because they move on',
           'their own:', '']
    out.append('| feature | BE ahead/behind | FE ahead/behind |')
    out.append('|---|---|---|')
    for r in rows:
        out.append(f'| `{r["key"]}` | {r.get("be_ahead_behind", "-")} '
                   f'| {r.get("fe_ahead_behind", "-")} |')
    be_prs, fe_prs = open_prs(BE), open_prs(FE)
    out += ['', f'Open PRs: {be_prs if be_prs is not None else "?"} backend, '
                f'{fe_prs if fe_prs is not None else "?"} frontend.']
    return '\n'.join(out)


def warn(rows: List[Dict]) -> int:
    """Say the things worth a human's attention. Never fails the run.

    None of these is an error. A branch can sit unmerged for months on
    purpose — `pnl` and `sale_through` both do. The point is that they are
    said out loud on every run instead of being discovered weeks later.
    """
    notes = []
    for r in rows:
        for side in ('be', 'fe'):
            if r.get(f'{side}_state') == 'UNMERGED':
                notes.append(f'{r["key"]} ({side.upper()}) holds content '
                             f'staging does not have')
    for label, repo in (('GBL_MASTER_API', BE), ('GBL_MASTER_FRONTEND', FE)):
        n = dirty(repo)
        if n:
            notes.append(f'{n} uncommitted path(s) in {label} — '
                         f'`git -C {label} status --short`')
    if not os.path.isdir(os.path.join(WORKSPACE, '.git')):
        notes.append('MASTER_FEATURE_WORKLOG.md is in no git repository: '
                     'no history, no backup, one copy on this machine')
    for n in notes:
        print(f'  look: {n}')
    return 0


def check(rows: List[Dict]) -> int:
    """Fail if the worklog's generated block is missing or out of date.

    An earlier version policed the PROSE — scraping every `x.y.z` out of a
    feature's section and comparing. It was abandoned: it pulled `127.0.0`
    out of `127.0.0.1`, called every historical version drift, and flagged
    features that were recorded correctly. Noisy warnings get ignored, which
    is how a five-week-stale entry survived in the first place.

    So this checks the one thing that cannot be argued with: regenerate the
    block and see whether the file matches. A session that shipped something
    and did not rerun `--write` fails here.
    """
    if not os.path.exists(WORKLOG):
        print(f'FAIL: worklog not found at {WORKLOG}')
        return 1
    with open(WORKLOG, encoding='utf-8') as fh:
        text = fh.read()

    if BEGIN not in text or END not in text:
        print('FAIL: the worklog has no generated state block.')
        print('      Run: GBL_MASTER_API/scripts/dev/worklog_state.py --write')
        return 1

    stored = text[text.index(BEGIN):text.index(END) + len(END)]
    fresh = render(rows)
    if stored.strip() != fresh.strip():
        print("FAIL: the worklog's generated state is out of date.")
        print('      Run: GBL_MASTER_API/scripts/dev/worklog_state.py --write')
        print()
        for line in difflib.unified_diff(
                stored.splitlines(), fresh.splitlines(),
                fromfile='worklog (stored)', tofile='git (actual)',
                lineterm='', n=1):
            print(f'  {line}')
        return 1

    print("OK: the worklog's generated state matches git.")
    return warn(rows)


def write(rows: List[Dict]) -> int:
    block = render(rows)
    with open(WORKLOG, encoding='utf-8') as fh:
        text = fh.read()
    if BEGIN in text and END in text:
        text = (text[:text.index(BEGIN)] + block +
                text[text.index(END) + len(END):])
    else:
        anchor = '## Feature index'
        if anchor not in text:
            print(f'could not find "{anchor}" in the worklog to anchor the block')
            return 1
        text = text.replace(anchor, block + '\n\n' + anchor, 1)
    with open(WORKLOG, 'w', encoding='utf-8') as fh:
        fh.write(text)
    print(f'generated block written to {WORKLOG}')
    return warn(rows)


def hook() -> int:
    """SessionStart form: report drift without ever failing the session start.

    Prints the envelope Claude Code reads — `systemMessage` for the person,
    `additionalContext` so the session itself knows the worklog is suspect
    before it reads a word of it. Always exits 0: a stale worklog is a thing
    to be told about, not a reason to refuse to start.
    """
    buf = io.StringIO()
    stdout, sys.stdout = sys.stdout, buf
    try:
        rows = collect()
        failed = check(rows) != 0
    except Exception as e:  # never break a session start
        sys.stdout = stdout
        print(json.dumps({'systemMessage':
                          f'worklog_state.py could not run: {e}'}))
        return 0
    finally:
        sys.stdout = stdout

    body = buf.getvalue().strip()
    lead = ('MASTER_FEATURE_WORKLOG.md is OUT OF DATE against git'
            if failed else 'Worklog state matches git')
    print(json.dumps({
        'systemMessage': f'{lead}\n{body}',
        'hookSpecificOutput': {
            'hookEventName': 'SessionStart',
            'additionalContext': (
                f'Cross-repo worklog check (GBL_MASTER_API/scripts/dev/'
                f'worklog_state.py --check):\n{lead}\n{body}\n'
                f'The generated state block in MASTER_FEATURE_WORKLOG.md is '
                f'authoritative for branches, versions and ahead/behind. '
                f'Prose elsewhere in that file may be stale; regenerate with '
                f'--write after shipping anything.'),
        },
    }))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--check', action='store_true',
                    help="exit 1 if the worklog's block is out of date")
    ap.add_argument('--write', action='store_true',
                    help='regenerate the block in the worklog')
    ap.add_argument('--hook', action='store_true',
                    help='SessionStart form: JSON envelope, always exit 0')
    args = ap.parse_args()

    if args.hook:
        return hook()

    if args.write:
        return write(collect())
    if args.check:
        return check(collect())
    rows = collect(extras=True)
    print(table(rows))
    return warn(rows)


if __name__ == '__main__':
    sys.exit(main())
