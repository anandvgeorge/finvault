# Raspberry Pi deployment

`scripts/deploy-pi.sh` deploys the tested `production` branch using the existing
read-only GitHub deploy key. It runs as the user who owns the systemd user
services. GitHub does not connect to the Pi.

For an existing installation receiving transaction comments, follow
[Prepare an existing database for comments](#prepare-an-existing-database-for-comments)
before the feature reaches `production`. For service configuration changes, see
[Switch an existing service to the active release](#switch-an-existing-service-to-the-active-release).

## Expected layout

```text
~/finvault/                              Git clone used to fetch code
~/.local/bin/uv                         uv executable
~/.local/share/finvault/
  credentials.json                     Existing Google OAuth client credentials
  token.json                           Existing OAuth token
  data/                                Existing database and logs
  releases/<commit>/                   Code and .venv for each deployed commit
  current -> <active release>           Used by UI, ingestion, and deployment
    scripts/deploy-pi.sh                Deployment script used for the next check
```

The script fetches `production`, builds that exact commit using `uv sync --locked`,
then pauses the ingestion timer and waits up to two minutes for any running
ingestion service to finish. It switches `current`, restarts the UI, and checks
the dashboard, overview API, and transactions API on `127.0.0.1:8000`. If startup fails, it restores
the previous code and tries to restart it. The ingestion timer resumes if it
was active before deployment. A file lock prevents overlapping deployments.
An unchanged commit does not cause a rebuild or restart.

The database, credentials, token, and logs stay in the runtime directory. Code
rollback does not reverse database changes. Review future schema migrations for
compatibility before enabling automatic deployment. Old releases are retained;
monitor disk usage and remove unused releases when needed, keeping the active
release and one working previous release.

## Prepare an existing database for comments

Application startup creates new tables with a nullable `comment TEXT` column.
Existing tables require the one-time update below. Existing transactions receive
`NULL`, and repeating this procedure safely skips an already present column.

Run as the service owner on the Pi before merging the feature into the branch
that CI promotes to `production`. Pause automatic checks first.

```bash
systemctl --user stop finvault-deploy.timer
systemctl --user show finvault-deploy.service -p ActiveState -p SubState
```

Wait for any running deployment to finish. Stopping the timer leaves an already
running service active.

The following command creates a consistent SQLite backup while the application
is running, then adds the column. It opens the existing runtime database with
`mode=rw`, so an incorrect path fails instead of creating an empty database.
The schema update waits up to 30 seconds for a write lock.

```bash
"$HOME/.local/share/finvault/current/.venv/bin/python" - <<'PYTHON'
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

path = Path.home() / ".local/share/finvault/data/finvault.db"
connection = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=30)
try:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"finvault-before-comments-{stamp}.db")
    backup.touch(mode=0o600, exist_ok=False)
    target = sqlite3.connect(backup)
    try:
        connection.backup(target)
    finally:
        target.close()
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(transactions)")}
        if "comment" not in columns:
            connection.execute("ALTER TABLE transactions ADD COLUMN comment TEXT")
    print("Comment column ready.")
    print("Backup:", backup)
finally:
    connection.close()
PYTHON
```

Continue after the command reports `Comment column ready.`. If it reports a
locked database, let the active writer finish and rerun it before deployment.
For bootstrap installations whose `current` symlink has not been created yet,
use `$HOME/finvault/.venv/bin/python` for this command.

After the tested feature reaches `production`, run the deployment and verify it.

```bash
systemctl --user start finvault-deploy.service
systemctl --user show finvault-deploy.service -p Result -p ExecMainStatus
systemctl --user is-active finvault-ui.service finvault-ingest.timer
readlink -f "$HOME/.local/share/finvault/current"
curl --fail --silent --show-error 'http://127.0.0.1:8000/transactions?period=all' > /dev/null
```

Expect `Result=success`, `ExecMainStatus=0`, and an active UI. The ingestion
timer should be active if it was active before deployment. The final command
also checks transaction loading explicitly because the first deployment of
this release runs the previous release's deployment script and health checks.

Open the dashboard, click a merchant, save a comment, and refresh. Confirm the
comment appears below the merchant. Edit and clear it, and check a manual entry
with an optional comment. On mobile, verify the same actions in the transaction
card. Resume deployment checks after verification.

```bash
systemctl --user start finvault-deploy.timer
systemctl --user list-timers --all finvault-deploy.timer
```

If deployment or verification fails, keep the deployment timer paused and inspect
`journalctl --user -u finvault-deploy.service -u finvault-ui.service -n 100 --no-pager`.
A `no such column: comment` error means the runtime database still needs the
schema update. Apply it to the path above before retrying. If the new release
is already active, restart `finvault-ui.service` and repeat verification, since
the deploy script skips unchanged commits.

Earlier application code can use the expanded database. Code rollback retains
the column and saved comments. Restoring the backup also discards all database
writes since it was taken, including newly ingested transactions.

## Local verification

For an existing local database, use the same Python body above with
`path = Path("data/finvault.db")` and run it using `.venv/bin/python` from the
project directory. Newly created databases include the column automatically.

```bash
uv run --locked uvicorn finvault.api.main:app --reload
```

Open `http://127.0.0.1:8000` and verify save, refresh, edit, cancel, and clear.
Run the database and API checks with temporary fixture databases.

```bash
.venv/bin/python -m unittest tests.test_database tests.test_api
```

## One-time service setup

Run on the Pi. These commands assume the existing services currently use
`~/finvault/.venv/bin/python` and work in `~/.local/share/finvault`.
The clone, its working Python environment, OAuth files, database at
`~/.local/share/finvault/data/finvault.db`, and ingestion timer must already exist.

First install the reviewed script from a commit already promoted to `production`:

```bash
cd "$HOME/finvault"
git fetch origin
mkdir -p "$HOME/.local/bin"
git show origin/production:scripts/deploy-pi.sh > "$HOME/.local/bin/finvault-deploy.new"
/bin/bash -n "$HOME/.local/bin/finvault-deploy.new"
```

Continue only if fetching, extracting, and syntax validation succeeded:

```bash
install -m 700 "$HOME/.local/bin/finvault-deploy.new" "$HOME/.local/bin/finvault-deploy"
```

Create the initial symlink only if it does not already exist. It points at the
existing installation so that the first deployment can roll back to it:

```bash
if [ ! -e "$HOME/.local/share/finvault/current" ] && [ ! -L "$HOME/.local/share/finvault/current" ]; then
    ln -sT "$HOME/finvault" "$HOME/.local/share/finvault/current"
fi
mkdir -p "$HOME/.config/systemd/user/finvault-ui.service.d"
mkdir -p "$HOME/.config/systemd/user/finvault-ingest.service.d"

cat > "$HOME/.config/systemd/user/finvault-ui.service.d/release.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=%h/.local/share/finvault/current/.venv/bin/python -m uvicorn finvault.api.main:app --host 0.0.0.0 --port 8000
EOF

cat > "$HOME/.config/systemd/user/finvault-ingest.service.d/release.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=%h/.local/share/finvault/current/.venv/bin/python -m finvault
EOF

systemctl --user daemon-reload
"$HOME/.local/bin/finvault-deploy"
```

Do not manually launch ingestion while a deployment is activating. Deployments
coordinate with `finvault-ingest.timer` and `finvault-ingest.service`; a separately
started Python process is outside that coordination.

After the first successful deployment, verify the services and release:

```bash
readlink -f "$HOME/.local/share/finvault/current"
systemctl --user is-active finvault-ui.service finvault-ingest.timer
```

From your laptop, open `http://<pi-tailscale-ip>:8000/` using the Pi's Tailscale
IPv4 address. Binding to `0.0.0.0` also listens on the Pi's LAN interfaces;
network access depends on the host firewall and Tailscale access rules. The
deployment health checks still connect locally to `127.0.0.1:8000`.

The installed `~/.local/bin/finvault-deploy` copy is only needed to bootstrap the
first release. The automatic service below runs the script from `current`, so
future script changes arrive with application releases. The running check
finishes with its original script; the next check uses the new release's script.
Rolling back `current` also restores the previous deployment script. Keep the Git
clone on the original version until the first release succeeds, so the initial
rollback target remains available.

## Automatic checks

After a successful manual deployment, create these user units on the Pi:

```bash
cat > "$HOME/.config/systemd/user/finvault-deploy.service" <<'EOF'
[Unit]
Description=Deploy tested FinVault production code

[Service]
Type=oneshot
ExecStart=/bin/bash %h/.local/share/finvault/current/scripts/deploy-pi.sh
Environment=GIT_TERMINAL_PROMPT=0
UMask=0077
TimeoutStartSec=20min
EOF

cat > "$HOME/.config/systemd/user/finvault-deploy.timer" <<'EOF'
[Unit]
Description=Check FinVault production code every five minutes

[Timer]
OnBootSec=2min
OnUnitInactiveSec=5min

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user start finvault-deploy.service
systemctl --user show finvault-deploy.service -p ExecStart -p Result -p ExecMainStatus
```

The same service definition is provided in [`finvault-deploy.service`](finvault-deploy.service).
Confirm the command uses `current/scripts/deploy-pi.sh`, `Result=success`, and
`ExecMainStatus=0` before enabling the timer and removing the bootstrap copies:

```bash
systemctl --user enable --now finvault-deploy.timer
systemctl --user list-timers --all finvault-deploy.timer
rm -f "$HOME/.local/bin/finvault-deploy" "$HOME/.local/bin/finvault-deploy.new"
```

The timer checks five minutes after the previous deployment check finishes; it
does not restart an unchanged version. Ingestion retains its separate 15-minute
schedule. Existing `loginctl enable-linger` configuration keeps these user units
running after logout and starts enabled units at boot.

Inspect a deployment with:

```bash
journalctl --user -u finvault-deploy.service -n 60 --no-pager
```

If the user journal reports no entries, query the system journal for your user:

```bash
sudo journalctl _SYSTEMD_USER_UNIT=finvault-deploy.service "_UID=$(id -u)" -n 60 --no-pager
```

## Switch an existing service to the active release

Run on the Pi after a working release containing `scripts/deploy-pi.sh` is active.
Pause new checks, then confirm the script exists and has valid Bash syntax:

```bash
systemctl --user stop finvault-deploy.timer
test -r "$HOME/.local/share/finvault/current/scripts/deploy-pi.sh"
/bin/bash -n "$HOME/.local/share/finvault/current/scripts/deploy-pi.sh"
systemctl --user show finvault-deploy.service -p ActiveState -p SubState
```

Continue only if the script exists and syntax validation succeeded. Wait for any
running check to finish; stopping the timer leaves it running.
Once the service is inactive, replace its definition with:

```bash
cat > "$HOME/.config/systemd/user/finvault-deploy.service" <<'EOF'
[Unit]
Description=Deploy tested FinVault production code

[Service]
Type=oneshot
ExecStart=/bin/bash %h/.local/share/finvault/current/scripts/deploy-pi.sh
Environment=GIT_TERMINAL_PROMPT=0
UMask=0077
TimeoutStartSec=20min
EOF

systemctl --user daemon-reload
systemctl --user start finvault-deploy.service
systemctl --user show finvault-deploy.service -p ExecStart -p Result -p ExecMainStatus
```

Confirm `ExecStart` uses `current/scripts/deploy-pi.sh`, `Result=success`, and
`ExecMainStatus=0`. Refresh Settings and check the last update-check time. Resume
automatic checks and remove the obsolete installed copies only after success:

```bash
systemctl --user enable --now finvault-deploy.timer
systemctl --user list-timers --all finvault-deploy.timer
rm -f "$HOME/.local/bin/finvault-deploy" "$HOME/.local/bin/finvault-deploy.new"
```

Keep `~/finvault` (the fetch clone), `current`, release directories, database,
OAuth files, category settings, and the UI/ingestion service overrides. Those
remain part of the deployment. There are no additional services or timers to
remove. Manage old releases separately, retaining the active and a working
previous release.
