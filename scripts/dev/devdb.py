"""
One command for the local development database, on macOS and on Windows.

    python scripts/dev/devdb.py app         run the API — the DAILY command

    python scripts/dev/devdb.py doctor      what is missing on this machine
    python scripts/dev/devdb.py up          start the containers, wait for them
    python scripts/dev/devdb.py load        load the newest seed bundle
    python scripts/dev/devdb.py verify      prove it matches production
    python scripts/dev/devdb.py status      what is running and what is in it
    python scripts/dev/devdb.py extract     make a bundle (needs production)
    python scripts/dev/devdb.py down        stop, keeping the data
    python scripts/dev/devdb.py reset       delete both databases and start over

WHY THIS EXISTS RATHER THAN A LIST OF COMMANDS IN A README
----------------------------------------------------------
The obvious instruction is `FLASK_ENV=local python scripts/dev/load_seed.py`.
That is bash syntax. In PowerShell it is not a variable assignment, it is an
error — so a README written on a Mac hands a Windows machine a set of commands
that all fail, and the first half hour of a new machine goes on translating
them. Setting the environment in Python instead means one instruction that is
literally identical on both.

HOW OFTEN YOU ACTUALLY RUN THESE
--------------------------------
Only `app` is daily. The containers are `unless-stopped` and their data lives
in named volumes, so they come back by themselves and keep everything:

    app        every day
    up         after a reboot, and only if Docker Desktop was not running
    load       when you want fresher data — weekly, monthly, or never
    verify     after a load, or when a number looks wrong
    doctor     on a new machine, or when something is broken

`doctor` runs on the STANDARD LIBRARY ALONE. It has to: the first thing a fresh
machine needs is to be told what to install, and that answer is worthless if
getting it requires having already installed things.
"""
import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys

# Vietnamese store names, department names and pseudonyms all end up on stdout.
# A Windows console in a legacy code page raises UnicodeEncodeError on those and
# kills the run partway through - which, mid-load, leaves a half-populated
# database and no obvious cause. Force UTF-8 and degrade rather than die.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
DEV = os.path.join(ROOT, 'dev')
SEED_DIR = os.path.join(DEV, 'seed')
COMPOSE = os.path.join(DEV, 'docker-compose.yml')

MIN_PYTHON = (3, 12)
MIN_NODE = (20, 19)          # Vite 7 requires 20.19+ or 22.12+
PORTS = {'postgres': 55432, 'oracle': 51521}

# The CR skills and the dev tooling both resolve the other repo as a SIBLING.
# Cloning them anywhere else quietly breaks the cross-repo workflow, which is
# the sort of thing that is obvious on the machine it was set up on and
# baffling on the next one.
FRONTEND = os.path.abspath(os.path.join(ROOT, '..', 'GBL_MASTER_FRONTEND'))

OK, WARN, BAD = 'ok  ', 'warn', 'FAIL'


def _say(status, label, detail=''):
    print(f'  [{status}] {label:34} {detail}')


def _run(args, **kw):
    return subprocess.run(args, cwd=ROOT, text=True, **kw)


def _docker():
    """The docker command, or None. Windows and macOS both ship `docker`."""
    return shutil.which('docker')


def _compose(*args, capture=False):
    cmd = [_docker(), 'compose', '-f', COMPOSE, *args]
    if capture:
        return _run(cmd, capture_output=True)
    return _run(cmd)


def _port_open(port):
    with socket.socket() as s:
        s.settimeout(1.0)
        return s.connect_ex(('127.0.0.1', port)) == 0


def _bundles():
    if not os.path.isdir(SEED_DIR):
        return []
    found = [os.path.join(SEED_DIR, f) for f in os.listdir(SEED_DIR)
             if f.endswith('.tar.gz')]
    return sorted(found, key=os.path.getmtime, reverse=True)


# ── doctor ───────────────────────────────────────────────────────────────────
def cmd_doctor(args):
    """Check everything a fresh machine needs, and say how to fix each gap."""
    print(f'\n{platform.system()} {platform.release()} '
          f'({platform.machine()}), Python {platform.python_version()}\n')
    problems = []

    v = sys.version_info
    if (v.major, v.minor) >= MIN_PYTHON:
        _say(OK, 'Python >= 3.12', f'{v.major}.{v.minor}.{v.micro}')
    else:
        _say(BAD, 'Python >= 3.12', f'found {v.major}.{v.minor}')
        problems.append('Install Python 3.12 — the server runs 3.12 and a '
                        'version gap here has taken production down before.')

    in_venv = sys.prefix != sys.base_prefix
    _say(OK if in_venv else WARN, 'running in a virtualenv',
         sys.prefix if in_venv else 'using the system Python')
    if not in_venv:
        problems.append(
            'Create and activate a virtualenv:\n'
            '     python -m venv .venv\n'
            '     macOS:    source .venv/bin/activate\n'
            '     Windows:  .venv\\Scripts\\Activate.ps1')

    missing = []
    for mod, pip in (('dotenv', 'python-dotenv'), ('oracledb', 'oracledb'),
                     ('psycopg2', 'psycopg2-binary'), ('bcrypt', 'bcrypt')):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip)
    if missing:
        _say(BAD, 'Python packages', f'missing: {", ".join(missing)}')
        problems.append('pip install -r requirements.txt')
    else:
        _say(OK, 'Python packages', 'oracledb, psycopg2, bcrypt, dotenv')

    docker = _docker()
    if not docker:
        _say(BAD, 'Docker', 'not on PATH')
        problems.append(
            'Install Docker Desktop — https://docker.com/products/docker-desktop\n'
            '     It covers macOS and Windows with the same images and commands.\n'
            '     On Windows choose the WSL2 backend.')
    else:
        r = _run([docker, 'info'], capture_output=True)
        if r.returncode == 0:
            _say(OK, 'Docker', 'installed and running')
        else:
            _say(BAD, 'Docker', 'installed but not running')
            problems.append('Start Docker Desktop and wait for it to say '
                            '"Engine running".')

    _say(OK if os.path.exists(COMPOSE) else BAD, 'dev/docker-compose.yml',
         'present' if os.path.exists(COMPOSE) else 'missing')

    envfile = os.path.join(ROOT, '.env.development')
    if os.path.exists(envfile):
        _say(OK, '.env.development', 'present')
    else:
        _say(BAD, '.env.development', 'missing')
        problems.append(
            'Copy the template:\n'
            '     macOS:    cp .env.development.example .env.development\n'
            '     Windows:  copy .env.development.example .env.development')

    for name, port in PORTS.items():
        if _port_open(port):
            _say(OK, f'{name} reachable', f'127.0.0.1:{port}')
        else:
            _say(WARN, f'{name} not reachable', f'127.0.0.1:{port} — run `up`')

    # ── the frontend, which is half the setup ─────────────────────────────
    if os.path.isdir(FRONTEND):
        _say(OK, 'frontend repo alongside', FRONTEND)

        node = shutil.which('node')
        if not node:
            _say(BAD, 'Node.js', 'not on PATH')
            problems.append('Install Node.js 20.19+ (or 22.12+) — nodejs.org.\n'
                            '     Vite 7 refuses to start on anything older.')
        else:
            raw = _run([node, '-v'], capture_output=True).stdout.strip()
            try:
                parts = tuple(int(x) for x in raw.lstrip('v').split('.')[:2])
            except ValueError:
                parts = (0, 0)
            if parts >= MIN_NODE or parts[0] >= 22:
                _say(OK, 'Node.js >= 20.19', raw)
            else:
                _say(BAD, 'Node.js >= 20.19', f'found {raw}')
                problems.append(f'Upgrade Node.js — Vite 7 needs 20.19+ '
                                f'(found {raw}).')

        if os.path.isdir(os.path.join(FRONTEND, 'node_modules')):
            _say(OK, 'frontend dependencies', 'node_modules present')
        else:
            _say(BAD, 'frontend dependencies', 'node_modules missing')
            problems.append('Install them:\n'
                            '     cd ../GBL_MASTER_FRONTEND\n'
                            '     npm install')
    else:
        _say(BAD, 'frontend repo alongside', f'not found at {FRONTEND}')
        problems.append(
            'Clone the frontend NEXT TO this repo — the two must be siblings:\n'
            '       GBL_MASTER/\n'
            '         GBL_MASTER_API/        <- you are here\n'
            '         GBL_MASTER_FRONTEND/\n'
            '     git clone git@github.com:MinhLeGBL/GBL_MASTER_FRONTEND.git')

    found = _bundles()
    if found:
        size = os.path.getsize(found[0]) / 1e6
        _say(OK, 'seed bundle', f'{os.path.basename(found[0])} ({size:.0f} MB)')
    else:
        _say(BAD, 'seed bundle', 'none in dev/seed/')
        problems.append(
            'Get a seed bundle. THIS IS THE ONE THING `git pull` CANNOT GIVE\n'
            '     YOU — bundles are gitignored on purpose, because even\n'
            '     pseudonymised they are real sales, inventory and payroll\n'
            '     figures. Either:\n'
            '       - on the company network / VPN:\n'
            '           python scripts/dev/devdb.py extract\n'
            '       - or copy a .tar.gz from your other machine into dev/seed/\n'
            '         (USB, cloud drive, anything — it is self-contained)')

    print()
    if problems:
        print(f'{len(problems)} thing(s) to do:\n')
        for i, p in enumerate(problems, 1):
            print(f'  {i}. {p}\n')
        return 1
    print('  Ready. Next: python scripts/dev/devdb.py up\n')
    return 0


# ── containers ───────────────────────────────────────────────────────────────
def cmd_up(args):
    if not _docker():
        print('Docker is not installed. Run `doctor` first.')
        return 1
    print('Starting containers...')
    if _compose('up', '-d').returncode != 0:
        return 1
    print('\nOracle takes several minutes the FIRST time — it is creating the\n'
          'database. Later starts are quick. Following the log until ready;\n'
          'Ctrl-C is safe, it keeps running.\n')
    return _compose('logs', '-f', 'oracle').returncode


def cmd_down(args):
    return _compose('down').returncode


def cmd_reset(args):
    print('This DELETES both local databases. The seed bundle is untouched,\n'
          'so `up` then `load` rebuilds them.')
    if input('Type "delete" to confirm: ').strip() != 'delete':
        print('Cancelled.')
        return 1
    return _compose('down', '-v').returncode


def cmd_status(args):
    if not _docker():
        print('Docker is not installed.')
        return 1
    _compose('ps')
    print()
    for name, port in PORTS.items():
        print(f'  {name:10} 127.0.0.1:{port}  '
              f'{"reachable" if _port_open(port) else "not reachable"}')
    return 0


# ── data ─────────────────────────────────────────────────────────────────────
def _child(script, extra, env_name):
    """Run one of the sibling scripts with FLASK_ENV already set.

    This is the bit that makes one instruction work on both platforms.
    """
    env = {**os.environ, 'FLASK_ENV': env_name, 'PYTHONPATH': ROOT}
    return subprocess.run([sys.executable, os.path.join(HERE, script), *extra],
                          cwd=ROOT, env=env).returncode


def cmd_load(args):
    bundle = args.bundle
    if not bundle:
        found = _bundles()
        if not found:
            print('No seed bundle in dev/seed/. Run `doctor` for how to get one.')
            return 1
        bundle = found[0]
        if len(found) > 1:
            print(f'Using the newest of {len(found)} bundles: '
                  f'{os.path.basename(bundle)}\n')
    return _child('load_seed.py', [bundle], 'development')


def cmd_verify(args):
    return _child('verify_seed.py', args.rest, 'development')


def cmd_app(args):
    """Run the API against the local databases. THE daily command."""
    port = args.port
    print(f'GBL Master API on http://127.0.0.1:{port}  (local databases)')
    print('Sign in with any local account and the password "devpassword".')
    print('Ctrl-C to stop.\n')
    env = {**os.environ, 'FLASK_ENV': 'development', 'PYTHONPATH': ROOT}
    code = ('from app.main import app; '
            f"app.run(host='127.0.0.1', port={port}, debug=True)")
    return subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                          env=env).returncode


def cmd_extract(args):
    print('Extracting from PRODUCTION (read-only) — needs the company '
          'network or VPN.')
    print('This is the ONLY command here that leaves this machine.\n')
    return _child('extract_seed.py', args.rest, 'remote')


def main():
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Every command works identically on macOS and Windows.')
    sub = p.add_subparsers(dest='cmd', required=True)

    sub.add_parser('doctor', help='check this machine and say what is missing')
    sub.add_parser('up', help='start the containers')
    sub.add_parser('down', help='stop the containers, keeping the data')
    sub.add_parser('reset', help='delete both databases')
    sub.add_parser('status', help='what is running')

    load = sub.add_parser('load', help='load a seed bundle')
    load.add_argument('bundle', nargs='?', help='default: the newest in dev/seed/')

    app_ = sub.add_parser('app', help='run the API against the local databases')
    app_.add_argument('--port', type=int, default=5200)

    sub.add_parser('verify', help='prove local matches production')
    sub.add_parser('extract', help='make a bundle from production')

    # parse_known_args, not REMAINDER: argparse's REMAINDER does not capture a
    # leading option, so `verify --weeks 3` failed with "unrecognized
    # arguments". Anything not consumed here is handed to the child script.
    args, extra = p.parse_known_args()
    args.rest = extra
    return globals()[f'cmd_{args.cmd}'](args)


if __name__ == '__main__':
    sys.exit(main())
