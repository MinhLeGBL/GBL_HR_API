---
name: start
description: Start or restart the GBL HR API locally (kill stale processes, start server, verify health + SSH tunnel)
---

Start or restart the GBL HR API locally. Run each step sequentially and report results.

## Steps

### 1. Kill stale processes on ports 5200 (API) and 6543 (SSH tunnel)

```bash
lsof -ti:5200 | xargs kill -9 2>/dev/null; lsof -ti:6543 | xargs kill -9 2>/dev/null; echo "Killed stale processes"
```

### 2. Verify ports are free

```bash
lsof -i:5200 -i:6543 2>/dev/null
```

- If output is empty: ports are free, proceed.
- If output shows listeners: warn the user that ports are still in use and suggest manual intervention.

### 3. Start the API

```bash
nohup .venv/bin/python -c "from app.main import app; app.run(host='0.0.0.0', port=5200, debug=False, use_reloader=False)" > /tmp/gbl_api.log 2>&1 &
echo "PID: $!"
```

Wait 2 seconds after starting, then proceed to health checks.

### 4. Health check (no DB needed)

```bash
curl -s http://127.0.0.1:5200/api/v1/health/
```

- Expected: `{"status":"healthy"}`
- If it fails or times out: read `/tmp/gbl_api.log` for startup errors and show them to the user.

### 5. DB + SSH tunnel check

```bash
curl -s -X POST http://127.0.0.1:5200/api/v1/auth/login -H "Content-Type: application/json" -d '{"email":"test@test.com","password":"test"}'
```

- Expected: `{"error":"Invalid email or password"}` — this means DB connection and SSH tunnel are working.
- If you see tunnel errors like `"Server is not started"` or `"Couldn't open tunnel"`:
  1. Tell the user the SSH tunnel failed to start
  2. Suggest: kill ports 5200 + 6543 and retry `/start`

### 6. Report

Print a summary:
- API status (running / failed)
- PID
- Health check result
- DB/tunnel status
- If anything failed, show the relevant error and suggest a fix
