#!/usr/bin/env bash
# Apply ivee.env after the config page saves it (run as root by ivee-apply.service,
# which ivee-apply.path starts when the page writes config/apply.request).
#
# Renders the server config and writes apply.status as the ivee user, so root
# never writes into directories that user controls; root only restarts services.
set -euo pipefail

PREFIX="${IVEE_PREFIX:-/opt/ivee}"
SERVICE_USER="${IVEE_USER:-ivee}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$PREFIX/config"
APP_DIR="$PREFIX/ivee-esp32-server/main/xiaozhi-server"

request="$(head -c 64 "$CONFIG/apply.request" 2>/dev/null | tr -cd '0-9a-f')"

as_user() { runuser -u "$SERVICE_USER" -- "$@"; }
status() {  # status <ok:true|false> <message>
  as_user python3 - "$CONFIG/apply.status" "$request" "$1" "$2" <<'PY'
import json, sys, time
path, request, ok, message = sys.argv[1:]
with open(path, "w") as f:
    json.dump({"request": request, "ok": ok == "true", "message": message, "time": time.time()}, f)
PY
}

if ! output="$(as_user python3 "$REPO_DIR/server/scripts/render_config.py" \
                 --env "$CONFIG/ivee.env" --out "$APP_DIR/data" 2>&1)"; then
  status false "${output##*$'\n'}"
  exit 1
fi
if ! output="$(systemctl restart ivee-tts.service ivee-server.service 2>&1)"; then
  status false "Restart failed: ${output##*$'\n'}"
  exit 1
fi
status true "Applied"
