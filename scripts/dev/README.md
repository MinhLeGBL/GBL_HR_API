# Local development database

Until now there was one database and it was production: `.env.development`
pointed at the live server, so "developing" meant running experimental code
against the company's real data, over an SSH tunnel, on the company network.

Now `development` **is** the local containers. No tunnel, no VPN, no network.
Production is reachable only by asking for it by name.

| FLASK_ENV | database | needs the network |
|---|---|---|
| *(unset)* → `development` | local containers | no |
| `remote` | production, read-only Oracle + live Postgres | yes |
| `testing` | mocked; no database | no |

Tested shape: a work Mac and a home Windows PC, same repo, same commands.

---

## One command to find out what you need

```
python scripts/dev/devdb.py doctor
```

Run it first, on any machine, before installing anything. It uses **only the
standard library**, so it works on a fresh machine with no virtualenv and no
packages — being told what to install is useless if you must install something
to be told.

It prints one line per prerequisite and then a numbered to-do list with the
exact commands for **your** platform:

```
  [ok  ] Python >= 3.12                     3.12.4
  [ok  ] running in a virtualenv            .../.venv
  [ok  ] Python packages                    oracledb, psycopg2, bcrypt, dotenv
  [FAIL] Docker                             not on PATH
  [ok  ] dev/docker-compose.yml             present
  [FAIL] .env.development                   missing
  [warn] postgres not reachable             127.0.0.1:55432 — run `up`
  [ok  ] seed bundle                        gbl-dev-seed-2026-09-23.tar.gz (29 MB)
```

---

## Setting up a Windows 11 PC

The short version: clone both repos **side by side**, install four things, copy
two files, get a seed bundle, run `doctor`.

```powershell
# 1. Clone them as SIBLINGS. The tooling resolves ../GBL_MASTER_FRONTEND.
mkdir GBL_MASTER; cd GBL_MASTER
git clone git@github.com:MinhLeGBL/GBL_HR_API.git GBL_MASTER_API
git clone git@github.com:MinhLeGBL/GBL_MASTER_FRONTEND.git

# 2. Backend
cd GBL_MASTER_API
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.development.example .env.development

# 3. Frontend
cd ..\GBL_MASTER_FRONTEND
npm install
copy .env.example .env
cd ..\GBL_MASTER_API

# 4. Tell me what is still missing
python scripts\dev\devdb.py doctor
```

`doctor` checks **both** repos — Python, packages, Docker, Node, the frontend's
`node_modules`, the containers and the seed bundle — and prints the exact
command for anything absent. Then:

```powershell
python scripts\dev\devdb.py up       # first Oracle start takes a few minutes
python scripts\dev\devdb.py load
python scripts\dev\devdb.py verify
```

### The four Windows-specific traps

1. **PowerShell refuses to run the activate script.** `.venv\Scripts\Activate.ps1`
   fails with *"running scripts is disabled on this system"* on a default
   Windows 11. Once, per user:

   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   ```

2. **Docker Desktop must use the WSL2 backend.** The Oracle and Postgres images
   are Linux containers. Choose WSL2 in the installer, or Settings → General →
   *Use the WSL 2 based engine*.

3. **Node must be 20.19+ (or 22.12+).** Vite 7 refuses to start on anything
   older, with an error that does not obviously say why. `doctor` checks it.

4. **Clone the two repos as siblings.** Not nested, not in separate folders —
   the dev tooling and the CR skills both resolve `../GBL_MASTER_FRONTEND`.
   `doctor` checks this too.

**No Oracle Instant Client, no VPN, and no SSH key for the database.** Oracle
runs in thin mode against a container.

### The one thing `git pull` cannot give you

**The seed bundle.** `dev/seed/*.tar.gz` is gitignored on purpose: even
pseudonymised it is real sales, inventory and payroll figures, and that does
not belong in a repository. So on the new PC, either

- run `python scripts\dev\devdb.py extract` **on the company network or VPN**, or
- copy the `.tar.gz` from your Mac into `dev\seed\` by USB or cloud drive.

It is self-contained — the machine loading it needs no production access at all.

---

## Setting up a new machine

**Everything below is identical on macOS and Windows.** That is deliberate —
see *Why there are no `FLASK_ENV=` prefixes* at the bottom.

### 1. Python 3.12

The server runs 3.12 and CI is pinned to 3.12. A version gap between them has
taken production down before, so `doctor` treats anything older as a failure
rather than a warning.

- **macOS** — `brew install python@3.12`
- **Windows** — [python.org](https://www.python.org/downloads/), and tick
  **"Add python.exe to PATH"** in the installer.

### 2. The repo and its packages

```
python -m venv .venv
```

| | activate |
|---|---|
| macOS | `source .venv/bin/activate` |
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |

```
pip install -r requirements.txt
```

No Oracle Instant Client is needed. `.env.development` sets `ORACLE_CLIENT_MODE=thin`,
which talks to Oracle with no Oracle libraries installed at all.

### 3. Docker Desktop

One installer covering both platforms, same images, same commands.

- **macOS** — `brew install --cask docker-desktop`
  (the cask is `docker-desktop`, not `docker`), or the .dmg from
  <https://docker.com/products/docker-desktop>.

  **Run it in your own terminal.** It calls `sudo` to install a privileged
  helper, and sudo needs a terminal to read the password from — so it cannot be
  run for you by a tool or a script. It fails at the very last step with
  `sudo: a terminal is required to read the password`, after downloading all
  644 MB, which looks like a broken install and is not.

- **Windows** — the installer from the same page. **Pick the WSL2 backend**;
  the Oracle image is a Linux container.

Start it and wait for *Engine running*.

> On Intel Macs, Homebrew now warns that the platform is unsupported (Tier 3,
> no prebuilt bottles, as of September 2026). Casks like this one still install
> — they ship Apple's own binaries — but expect source builds for anything
> installed as a formula on this machine from now on.

### 4. Configuration

| | |
|---|---|
| macOS | `cp .env.development.example .env.development` |
| Windows | `copy .env.development.example .env.development` |

### 5. Start the databases

```
python scripts/dev/devdb.py up
```

**The first Oracle start takes several minutes** — it is creating a database
from scratch. `up` follows the log until it says *DATABASE IS READY TO USE*.
Ctrl-C is safe; the container keeps running. Later starts take seconds.

### 6. Get a seed bundle

A bundle is one `.tar.gz` holding the schema and the data.

- **On a machine that can reach production** (company network / VPN) — this
  is the only command in the whole setup that leaves your machine:
  ```
  python scripts/dev/devdb.py extract
  ```
  ~29 MB, a few minutes.

- **On a machine that cannot** — copy the `.tar.gz` from your other machine
  into `dev/seed/`. USB, cloud drive, whatever you like; it is self-contained
  and needs no network. It is **gitignored on purpose**: pseudonymised or not,
  it is real sales, inventory and payroll figures.

### 7. Load and check

```
python scripts/dev/devdb.py load
python scripts/dev/devdb.py verify
```

`load` picks the newest bundle in `dev/seed/` unless you name one.

### 8. Run the app

However you normally do:

```
python -c "from app.main import app; app.run(port=5200)"
```

**No `FLASK_ENV` needed.** `development` is the default and it now means the
local containers, so the app, the tests and every script use them without
being told. The penalty for forgetting an environment variable used to be
running experimental code against the live database; now there is no variable
to forget.

`python scripts/dev/devdb.py app` does the same with a friendlier message. It
is a convenience, not a requirement.

To reach production, ask for it by name: `FLASK_ENV=remote ...`.

Every local account signs in with the password **`devpassword`**.

---

## How often you actually run any of this

**Only `app` is daily.** Steps 1-7 are once per machine. The containers are
`unless-stopped` and their data lives in named volumes, so they come back by
themselves and keep everything — verified by restarting them and re-counting:
12,605 documents, timezones intact.

| command | when |
|---|---|
| *(none)* | day to day — just run the app, it is local by default |
| `up` | after a reboot, and only if Docker Desktop was not already running |
| `load` | when you want fresher data — weekly, monthly, or never |
| `verify` | after a load, or when a number looks wrong |
| `doctor` | on a new machine, or when something is broken |

Docker Desktop does **not** start at login by default on this Mac. Either turn
that on in its settings, or just launch it after a reboot — the containers
follow on their own.

---

## Everyday commands

| | |
|---|---|
| `devdb.py app` | run the API against the local databases — **the daily one** |
| `devdb.py doctor` | what is missing on this machine |
| `devdb.py up` | start the containers |
| `devdb.py status` | what is running |
| `devdb.py load` | load the newest bundle |
| `devdb.py verify` | prove local matches production |
| `devdb.py extract` | make a bundle (needs production) |
| `devdb.py down` | stop, keeping the data |
| `devdb.py reset` | delete both databases and start over |

---

## What is in a bundle

| | |
|---|---|
| Oracle | 19 `rps` tables, ~582,000 rows |
| Postgres | 46 tables, ~50,000 rows |
| Window | reference tables whole; transactional tables from 1 Jan last year, so year-on-year comparisons reconcile |

**Personal data is replaced with deterministic pseudonyms** — the same person
becomes the same pseudonym everywhere they appear, so joins and CRM segment
counts still behave. Photographs and note CLOBs are dropped.
`users.password_hash` is replaced rather than copied.

**Employee names are NOT pseudonymised**, on purpose: commission joins sales to
staff by name, and the HR module exists to manage named people.
`extract --anonymise-staff` turns it on.

Before writing anything, the extractor greps the finished bundle for real
contact data and **refuses to write** if it finds any. On the first real run
that caught four leaks the spec had missed, including the management board's
addresses in `periodic_report_sends.recipients`.

---

## Why `verify` matters

It does not recompute something twice and hope. The weekly report stores
**frozen** snapshots — figures aggregated on the server and never recalculated
— and those travel in the bundle. Production has therefore already published
its answer for 38 weeks.

`verify` re-runs the aggregation against local Oracle and compares metric by
metric, exactly, with no tolerance. Pass means the local database reproduces
the server. Fail names the week, the period, the metric and both numbers.

---

## Why there are no `FLASK_ENV=` prefixes

The obvious instruction is `FLASK_ENV=local python scripts/dev/load_seed.py`.
That is bash syntax. In PowerShell it is not a variable assignment, it is an
error — so a README written on the Mac hands the Windows machine a set of
commands that all fail, and the first half hour on a new machine goes on
translating them.

`devdb.py` sets the environment in Python instead, so one instruction is
literally identical on both. The two places above where the platforms differ
are marked in a table, because there the difference is real.

---

## Things worth knowing

- **`rps` is a schema in BOTH databases and they are unrelated.**
  `rps.document` is Oracle (RetailPro); `rps.carrier_item` is Postgres (ours).

- **The ports are deliberately not the defaults** — 55432 and 51521. Nothing
  can connect here thinking it is somewhere else, and nothing pointed here can
  reach production.

- **`load` refuses to run against a non-local host.** It truncates every table
  it loads. Tested: with `FLASK_ENV=remote` it exits 1 without connecting. The
  check is kept even though `development` is now local, because a machine set
  up before the rename still has the old file.

- **Production credentials live in `.env.remote` alone.** It is named `remote`
  rather than `production` on purpose: several scripts in `scripts/jobs/`
  default to `FLASK_ENV=production`, so that name would let a bare
  `python scripts/jobs/<anything>.py` on a laptop quietly reach the live
  database. Nothing defaults to `remote`.

- **No bind mounts.** The Oracle container uses a named volume only. Mounting a
  repo directory is a reliable source of grief on Windows, and the two GRANT
  statements it used to carry are issued over SQL by `load_seed.py` instead.

- **Mail is `dryrun`.** Recipient rows are also pseudonymised onto
  `example.com`, so there are two independent reasons a dev box cannot email
  the board.

- **`.gitattributes` pins line endings** so a Windows checkout does not rewrite
  LF to CRLF in files that Linux containers and CI execute.
