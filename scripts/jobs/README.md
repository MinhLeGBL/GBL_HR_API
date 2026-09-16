# Background Jobs

## CRM RFM Recompute

Daily job that pulls customer transaction data from Oracle, scores via the
luxury hybrid RFM model, and writes results to PostgreSQL.

### Manual run (dev)

```bash
PYTHONPATH=. FLASK_ENV=development python scripts/jobs/crm_recompute.py
```

### Production setup (systemd timer — one-time)

```bash
# Copy service + timer files
sudo cp scripts/jobs/systemd/gbl-crm-recompute.* /etc/systemd/system/

# Reload, enable, and start
sudo systemctl daemon-reload
sudo systemctl enable --now gbl-crm-recompute.timer

# Verify timer is scheduled
systemctl list-timers | grep crm

# Manual trigger (test without waiting for 3am)
sudo systemctl start gbl-crm-recompute.service

# View logs
journalctl -u gbl-crm-recompute.service -n 50
cat /var/log/gbl-crm-recompute.log
```

After initial install, future deploys (git pull) refresh the script
automatically. Only re-install the systemd files if the `.service` or
`.timer` files themselves change.

## Weekly Periodic Sales Report

Two jobs. The first freezes the week that just closed into a snapshot awaiting
approval; the second sends whatever a human has approved. Nothing is ever
emailed without an explicit approval in the app.

### Manual run (dev)

```bash
PYTHONPATH=. FLASK_ENV=development python scripts/jobs/periodic_report_generate.py
PYTHONPATH=. FLASK_ENV=development python scripts/jobs/periodic_report_dispatch.py
```

Outside `FLASK_ENV=production` the mailer defaults to **dry run** — it logs the
message instead of sending it, so running the dispatcher against a copy of the
database cannot email real recipients. Set `MAIL_BACKEND=smtp` to override.

### Production setup (systemd timers — one-time)

```bash
sudo cp scripts/jobs/systemd/gbl-periodic-report*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gbl-periodic-report.timer
sudo systemctl enable --now gbl-periodic-report-dispatch.timer

systemctl list-timers | grep periodic-report
journalctl -u gbl-periodic-report.service -n 50
cat /var/log/gbl-periodic-report.log
```

**The tables must exist first.** `deploy.yml` does not run `init_db.py`, so
after the first release carrying this module run it once on the server or every
endpoint 500s on the missing schema:

```bash
cd /home/gbladmin/GBL_HR_API && FLASK_ENV=production .venv/bin/python scripts/database/init_db.py
```

### Timing

`gbl-periodic-report.timer` fires **Monday 03:00**. The week closes at Sunday
midnight, but sales posted late on Sunday keep arriving in Oracle afterwards and
the figures are frozen the moment the job runs — hence the three-hour margin.
MTD and YTD end on the closing **Sunday**, not the Monday the job fires on, so
they compare complete days against complete days.

`gbl-periodic-report-dispatch.timer` fires every 15 minutes; that interval is the
worst-case delay between pressing Approve with send mode "immediate" and the
email going out.
