# Raspberry Pi deployment

`scripts/deploy-pi.sh` deploys the tested `production` branch using the existing
read-only GitHub deploy key. It runs as the user who owns the systemd user
services. GitHub does not connect to the Pi.

## Expected layout

```text
~/finvault/                              Git clone used to fetch code
~/.local/bin/uv                         uv executable
~/.local/bin/finvault-deploy             Installed copy of the deployment script
~/.local/share/finvault/
  credentials.json                     Existing Google OAuth client credentials
  token.json                           Existing OAuth token
  data/                                Existing database and logs
  releases/<commit>/                   Code and .venv for each deployed commit
  current -> <active release>           Used by both services
```

The script fetches `production`, builds that exact commit using `uv sync --locked`,
then pauses the ingestion timer and waits up to two minutes for any running
ingestion service to finish. It switches `current`, restarts the UI, and checks
the dashboard and overview API on `127.0.0.1:8000`. If startup fails, it restores
the previous code and tries to restart it. The ingestion timer resumes if it
was active before deployment. A file lock prevents overlapping deployments.
An unchanged commit does not cause a rebuild or restart.

The database, credentials, token, and logs stay in the runtime directory. Code
rollback does not reverse database changes. Review future schema migrations for
compatibility before enabling automatic deployment. Old releases are retained;
monitor disk usage and remove unused releases when needed, keeping the active
release and one working previous release.

## One-time service setup

Run on the Pi. These commands assume the existing services currently use
`~/finvault/.venv/bin/python` and work in `~/.local/share/finvault`.

First install the reviewed script from a commit already promoted to `production`:

```bash
cd "$HOME/finvault"
git fetch origin
mkdir -p "$HOME/.local/bin"
git show origin/production:scripts/deploy-pi.sh > "$HOME/.local/bin/finvault-deploy"
chmod 700 "$HOME/.local/bin/finvault-deploy"
```

Create the initial symlink only if it does not already exist. It points at the
existing installation so that the first deployment can roll back to it:

```bash
ln -s "$HOME/finvault" "$HOME/.local/share/finvault/current"
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

Repeat the script installation when deliberately adopting changes to the
deployment script itself. Updating application releases does not overwrite the
installed deployment script. Keep the Git clone on the original version until
the first release succeeds, so the initial rollback target remains available.

## Automatic checks

After a successful manual deployment, create these user units on the Pi:

```bash
cat > "$HOME/.config/systemd/user/finvault-deploy.service" <<'EOF'
[Unit]
Description=Deploy tested FinVault production code

[Service]
Type=oneshot
ExecStart=%h/.local/bin/finvault-deploy
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
systemctl --user enable --now finvault-deploy.timer
systemctl --user list-timers --all finvault-deploy.timer
```

The timer checks five minutes after the previous deployment check finishes; it
does not restart an unchanged version. Ingestion retains its separate 15-minute
schedule. Existing `loginctl enable-linger` configuration keeps these user units
running after logout and starts enabled units at boot.

Inspect a deployment with:

```bash
journalctl --user -u finvault-deploy.service -n 60 --no-pager
```
