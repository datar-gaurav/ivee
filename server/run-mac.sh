#!/usr/bin/env bash
# Run Sparky's server on a MacBook (Apple Silicon) in the foreground, for testing.
#
# Same stack as the HP install: upstream xiaozhi-esp32-server at the pinned ref
# + local Kokoro TTS (server/tts/speel_tts.py), configured by our overlay. No sudo, no launchd; Ctrl-C stops
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
: "${XIAOZHI_SERVER_REPO:?}" "${XIAOZHI_SERVER_REF:?}" "${SERVER_LAN_IP:?}"

SERVER_WS_PORT="${SERVER_WS_PORT:-8000}"
SERVER_HTTP_PORT="${SERVER_HTTP_PORT:-8003}"
TTS_PORT="${TTS_PORT:-5000}"
for port in "$SERVER_WS_PORT" "$SERVER_HTTP_PORT" "$TTS_PORT"; do
  if holder="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1}')" && [[ -n "$holder" ]]; then
    die "Port $port is already in use by $holder. Pick another port in server/.env (SERVER_WS_PORT / SERVER_HTTP_PORT / TTS_PORT)."
  fi
done

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
# Skip packages with no macOS arm64 wheels. They back optional providers that
# upstream imports lazily, and Sparky doesn't select them.
MAC_SKIP='^(vosk)[=<> ]'
grep -Ev "$MAC_SKIP" "$APP_DIR/requirements.txt" > "$PREFIX/requirements-mac.txt"
uv pip install --quiet --python "$PREFIX/venv-server/bin/python" -r "$PREFIX/requirements-mac.txt"

log "Creating TTS venv and downloading Kokoro models"
[[ -d "$PREFIX/venv-tts" ]] || uv venv --python 3.10 "$PREFIX/venv-tts"
# piper-tts only for its bundled espeak-ng phonemizer; Kokoro does the speaking.
uv pip install --quiet --python "$PREFIX/venv-tts/bin/python" piper-tts kokoro-onnx
python3 "$REPO_DIR/server/tts/fetch_models.py" "$PREFIX/voices"

log "Rendering Sparky config and copying Sparky providers into the server"
python3 "$REPO_DIR/server/scripts/render_config.py" --env "$ENV_FILE" --out "$APP_DIR/data"
cp -R "$REPO_DIR/server/providers/." "$APP_DIR/core/providers/"

log "Starting Speel's voice (Kokoro ${TTS_VOICE:-af_heart}) on 127.0.0.1:$TTS_PORT"
"$PREFIX/venv-tts/bin/python" "$REPO_DIR/server/tts/speel_tts.py" --host 127.0.0.1 --port "$TTS_PORT" \
  --model "$PREFIX/voices/kokoro-v1.0.onnx" --voices "$PREFIX/voices/voices-v1.0.bin" \
  --voice "${TTS_VOICE:-af_heart}" --speed "${TTS_SPEED:-0.9}" --pitch "${TTS_PITCH:-1.12}" \
  --boost "${TTS_BOOST:-2.0}" --highpass "${TTS_HIGHPASS:-300}" --presence "${TTS_PRESENCE:-5}" &
tts_pid=$!
trap 'kill "$tts_pid" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  curl -fsS -o "$PREFIX/tts-test.wav" -X POST -H 'Content-Type: application/json' \
    -d '{"text":"Hi! I am Speel."}' "http://127.0.0.1:$TTS_PORT/synthesize" 2>/dev/null && break
  sleep 1
done
[[ -s "$PREFIX/tts-test.wav" ]] || die "TTS didn't answer on :$TTS_PORT."
echo "TTS OK. Hear it: afplay $PREFIX/tts-test.wav"

cat <<EOF

Starting Sparky server. Ctrl-C to stop.
  OTA URL for the device : http://${SERVER_LAN_IP}:${SERVER_HTTP_PORT}/xiaozhi/ota/
  WebSocket              : ws://${SERVER_LAN_IP}:${SERVER_WS_PORT}/xiaozhi/v1/
  If macOS asks whether Python may accept incoming connections, click Allow.
EOF

# caffeinate keeps the Mac awake (lid open) while the server runs.
cd "$APP_DIR"
caffeinate -i env PYTHONUNBUFFERED=1 "$PREFIX/venv-server/bin/python" app.py
