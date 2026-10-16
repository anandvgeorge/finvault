#!/usr/bin/env bash
# Run as the same user who owns the FinVault systemd user services.
set -euo pipefail
umask 077

repo="$HOME/finvault"
state="$HOME/.local/share/finvault"
current="$state/current"
uv="$HOME/.local/bin/uv"
timer=finvault-ingest.timer
ingest=finvault-ingest.service
ui=finvault-ui.service
temporary_link="$state/.current-$$"
resume_timer=0
rollback_needed=0
previous=

log() { printf '%s\n' "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

switch_release() {
    if [[ -L "$temporary_link" ]]; then
        unlink -- "$temporary_link" || return
    fi
    ln -s -- "$1" "$temporary_link" || return
    mv -Tf -- "$temporary_link" "$current"
}

healthy() {
    # Probe locally; a server bound to 0.0.0.0 also accepts loopback connections.
    "$current/.venv/bin/python" - <<'PY'
import json
import time
from urllib.request import ProxyHandler, build_opener

opener = build_opener(ProxyHandler({}))
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        with opener.open("http://127.0.0.1:8000/", timeout=2) as response:
            if response.status != 200:
                raise ValueError("Dashboard is unavailable")
        with opener.open("http://127.0.0.1:8000/overview?period=all", timeout=2) as response:
            overview = json.load(response)
            if response.status != 200 or "total_credits" not in overview:
                raise ValueError("Overview is unavailable")
        raise SystemExit(0)
    except (OSError, ValueError):
        time.sleep(1)
raise SystemExit("UI health check failed")
PY
}

finish() {
    local status=$?
    trap - EXIT
    # Attempt all recovery steps even if a restart itself fails.
    set +e
    if (( rollback_needed )); then
        log "Deployment failed; restoring $previous."
        if switch_release "$previous" && systemctl --user restart "$ui" && healthy; then
            log "Previous release restored."
        else
            log "ERROR: recovery failed; inspect finvault-ui.service." >&2
        fi
        status=1
    fi
    if (( resume_timer )); then
        if ! systemctl --user start "$timer"; then
            log "ERROR: could not resume $timer." >&2
            status=1
        fi
    fi
    if [[ -L "$temporary_link" ]]; then
        unlink -- "$temporary_link"
    fi
    exit "$status"
}

[[ -d "$state" ]] || fail "Missing runtime directory: $state"
exec 9>"$state/deploy.lock"
if ! flock -n 9; then
    log "Another deployment is running; skipping."
    exit 0
fi
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

[[ -x "$uv" ]] || fail "uv is missing at $uv"
[[ -L "$current" && -d "$current" ]] || fail "Configure the current release symlink first."
previous=$(readlink -f -- "$current")
for unit in "$ui" "$ingest"; do
    directory=$(systemctl --user show --property=WorkingDirectory --value "$unit")
    command=$(systemctl --user show --property=ExecStart --value "$unit")
    [[ "$directory" == "$state" ]] || fail "$unit must use $state as WorkingDirectory."
    [[ "$command" == *"$current/.venv/bin/python"* ]] || fail "$unit must start Python through $current."
done

# Fetch code with the clone's existing read-only deploy key; do not change its checkout.
git -C "$repo" fetch --no-tags origin refs/heads/production:refs/remotes/origin/production
commit=$(git -C "$repo" rev-parse --verify 'refs/remotes/origin/production^{commit}')
release="$state/releases/$commit"
if [[ "$previous" == "$release" ]]; then
    log "Already running production commit $commit."
    exit 0
fi

log "Preparing production commit $commit."
mkdir -p -- "$release"
git -C "$repo" archive "$commit" | tar -x -C "$release"
# Build at the final path: virtual environments cannot safely be moved afterward.
# Ignore an inherited laptop venv or uv project-environment override.
(
    unset VIRTUAL_ENV UV_PROJECT_ENVIRONMENT
    cd "$release"
    "$uv" sync --locked --project "$release"
    "$release/.venv/bin/python" -c 'import finvault, fastapi, uvicorn'
)

# Stopping a timer does not terminate ingestion that has already started.
if systemctl --user is-active --quiet "$timer"; then
    resume_timer=1
    systemctl --user stop "$timer"
fi
deadline=$((SECONDS + 120))
while :; do
    activity=$(systemctl --user show --property=ActiveState --value "$ingest")
    case "$activity" in
        inactive|failed) break ;;
        active|activating|deactivating|reloading)
            (( SECONDS < deadline )) || fail "Ingestion is still running; retry deployment later."
            sleep 2
            ;;
        *) fail "Unexpected ingestion state: $activity" ;;
    esac
done

rollback_needed=1
switch_release "$release"
systemctl --user restart "$ui"
healthy
rollback_needed=0
log "Deployed production commit $commit."
