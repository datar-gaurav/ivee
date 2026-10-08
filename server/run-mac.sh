#!/usr/bin/env bash
# Run Sparky's server on a MacBook (Apple Silicon) in the foreground, for testing.
#
# Same stack as the HP install: upstream xiaozhi-esp32-server at the pinned ref
# + local Piper TTS, configured by our overlay. No sudo, no launchd; Ctrl-C stops
# everything. Everything lives under ~/.sparky (delete it to start over).
#
# Prereqs: Homebrew, server/.env filled in (SERVER_LAN_IP = this Mac's Wi-Fi IP).
# Usage:   ./server/run-mac.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/server/.env"
PREFIX="${SPARKY_PREFIX:-$HOME/.sparky}"
SERVER_DIR="$PREFIX/xiaozhi-esp32-server"
APP_DIR="$SERVER_DIR/main/xiaozhi-server"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == Darwin ]] || die "This script is for macOS. On Linux use server/install-hp.sh."
[[ -f "$ENV_FILE" ]] || die "Missing $ENV_FILE (copy server/.env.example and fill it in)."
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
: "${XIAOZHI_SERVER_REPO:?}" "${XIAOZHI_SERVER_REF:?}" "${PIPER_VOICE:?}" "${SERVER_LAN_IP:?}"

wifi_ip="$(ipconfig getifaddr en0 || true)"
if [[ -n "$wifi_ip" && "$wifi_ip" != "$SERVER_LAN_IP" ]]; then
  die "SERVER_LAN_IP=$SERVER_LAN_IP in server/.env, but this Mac's Wi-Fi IP is $wifi_ip. Update .env (the device is told this address)."
fi

log "Installing system packages (Homebrew)"
command -v brew >/dev/null || die "Install Homebrew first: https://brew.sh"
for pkg in opus ffmpeg; do
  brew list --formula "$pkg" >/dev/null 2>&1 || brew install "$pkg"
done
command -v uv >/dev/null || brew install uv
# opuslib finds libopus via ctypes, which doesn't search Homebrew's lib dir by default.
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"

mkdir -p "$PREFIX/voices"

log "Fetching xiaozhi-esp32-server @ ${XIAOZHI_SERVER_REF:0:12}"
if [[ ! -d "$SERVER_DIR/.git" ]]; then
  git clone --filter=blob:none "$XIAOZHI_SERVER_REPO" "$SERVER_DIR"
fi
git -C "$SERVER_DIR" fetch --quiet origin
git -C "$SERVER_DIR" checkout --quiet "$XIAOZHI_SERVER_REF"

log "Creating server venv (Python 3.10, as upstream recommends)"
[[ -d "$PREFIX/venv-server" ]] || uv venv --python 3.10 "$PREFIX/venv-server"
# macOS torch wheels are CPU/MPS only, so plain PyPI is fine here.
uv pip install --quiet --python "$PREFIX/venv-server/bin/python" -r "$APP_DIR/requirements.txt"

log "Creating Piper TTS venv and downloading voice ${PIPER_VOICE}"
[[ -d "$PREFIX/venv-tts" ]] || uv venv --python 3.10 "$PREFIX/venv-tts"
uv pip install --quiet --python "$PREFIX/venv-tts/bin/python" "piper-tts[http]"
if [[ ! -f "$PREFIX/voices/${PIPER_VOICE}.onnx" ]]; then
  "$PREFIX/venv-tts/bin/python" -m piper.download_voices --data-dir "$PREFIX/voices" "$PIPER_VOICE"
fi

log "Rendering Sparky config into the server's data/ dir"
python3 "$REPO_DIR/server/scripts/render_config.py" --env "$ENV_FILE" --out "$APP_DIR/data"

log "Starting Piper TTS on 127.0.0.1:5000"
"$PREFIX/venv-tts/bin/python" -m piper.http_server --host 127.0.0.1 --port 5000 \
  --data-dir "$PREFIX/voices" -m "$PIPER_VOICE" &
tts_pid=$!
trap 'kill "$tts_pid" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  curl -fsS -o "$PREFIX/tts-test.wav" -X POST -H 'Content-Type: application/json' \
    -d '{"text":"Hi! I am Sparky."}' http://127.0.0.1:5000/synthesize 2>/dev/null && break
  sleep 1
done
[[ -s "$PREFIX/tts-test.wav" ]] || die "Piper didn't answer on :5000."
echo "TTS OK. Hear it: afplay $PREFIX/tts-test.wav"

cat <<EOF

Starting Sparky server. Ctrl-C to stop.
  OTA URL for the device : http://${SERVER_LAN_IP}:8003/xiaozhi/ota/
  WebSocket              : ws://${SERVER_LAN_IP}:8000/xiaozhi/v1/
  If macOS asks whether Python may accept incoming connections, click Allow.
EOF

# caffeinate keeps the Mac awake (lid open) while the server runs.
cd "$APP_DIR"
caffeinate -i env PYTHONUNBUFFERED=1 "$PREFIX/venv-server/bin/python" app.py
