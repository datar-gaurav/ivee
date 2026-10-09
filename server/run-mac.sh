#!/usr/bin/env bash
# Run Ivee's server on a Mac (Apple Silicon) in the foreground: for trying Ivee
# out or developing it. For an always-on server use server/install.sh on Linux.
#
# Same stack as the Linux install: the upstream server at the pinned ref (checked
# out as ivee-esp32-server), local Kokoro TTS and the config page. No sudo, no
# launchd; Ctrl-C stops everything. Everything lives under ~/.ivee (delete it to
# start over); settings are in ~/.ivee/config/ivee.env.
#
# Prereqs: Homebrew.
# Usage:   ./server/run-mac.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${IVEE_PREFIX:-$HOME/.ivee}"
CONFIG="$PREFIX/config"
ENV_FILE="$CONFIG/ivee.env"
SERVER_DIR="$PREFIX/ivee-esp32-server"
APP_DIR="$SERVER_DIR/main/xiaozhi-server"
UI_PORT="${IVEE_UI_PORT:-8088}"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
render() { python3 "$REPO_DIR/server/scripts/render_config.py" --env "$ENV_FILE" --out "$APP_DIR/data"; }

[[ "$(uname -s)" == Darwin ]] || die "This script is for macOS. On Linux use server/install.sh."
wifi_ip="$(ipconfig getifaddr en0 || true)"

mkdir -p "$PREFIX/voices" "$CONFIG"
chmod 700 "$CONFIG"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$REPO_DIR/server/ivee.env.example" "$ENV_FILE"
  offset="$(python3 -c 'import time; print(f"{time.localtime().tm_gmtoff / 3600:g}")')"
  sed -i '' -e "s/^SERVER_LAN_IP=.*/SERVER_LAN_IP=${wifi_ip}/" -e "s/^TIMEZONE_OFFSET=.*/TIMEZONE_OFFSET=${offset}/" \
    -e "s/^TTS_PORT=.*/TTS_PORT=5055/" "$ENV_FILE"  # 5000 is AirPlay Receiver on a Mac
  chmod 600 "$ENV_FILE"
fi
password=""
if [[ ! -f "$CONFIG/ui-password" ]]; then
  password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(9))')"
  printf '%s\n' "$password" | python3 "$REPO_DIR/server/config_ui/ivee_config.py" set-password --env "$ENV_FILE" >/dev/null
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
SERVER_WS_PORT="${SERVER_WS_PORT:-8000}"
SERVER_HTTP_PORT="${SERVER_HTTP_PORT:-8003}"
TTS_PORT="${TTS_PORT:-5000}"
for port in "$SERVER_WS_PORT" "$SERVER_HTTP_PORT" "$TTS_PORT" "$UI_PORT"; do
  if holder="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1}')" && [[ -n "$holder" ]]; then
    die "Port $port is already in use by $holder. Pick other ports in $ENV_FILE (or IVEE_UI_PORT for the config page)."
  fi
done
if [[ -n "$wifi_ip" && -n "${SERVER_LAN_IP:-}" && "$wifi_ip" != "$SERVER_LAN_IP" ]]; then
  echo "Note: SERVER_LAN_IP is $SERVER_LAN_IP, but this Mac's Wi-Fi IP is $wifi_ip. Fix it on the config page."
fi

log "Installing system packages (Homebrew)"
command -v brew >/dev/null || die "Install Homebrew first: https://brew.sh"
for pkg in opus ffmpeg; do
  brew list --formula "$pkg" >/dev/null 2>&1 || brew install "$pkg"
done
command -v uv >/dev/null || brew install uv
# opuslib finds libopus via ctypes, which doesn't search Homebrew's lib dir by default.
brew_prefix="$(brew --prefix)"
export DYLD_FALLBACK_LIBRARY_PATH="$brew_prefix/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"

log "Fetching the upstream server @ ${IVEE_SERVER_REF:0:12}"
if [[ ! -d "$SERVER_DIR/.git" ]]; then
  git clone --filter=blob:none "$IVEE_SERVER_REPO" "$SERVER_DIR"
fi
git -C "$SERVER_DIR" fetch --quiet origin
git -C "$SERVER_DIR" checkout --quiet "$IVEE_SERVER_REF"

log "Creating the server venv (Python 3.10, as upstream recommends)"
[[ -d "$PREFIX/venv-server" ]] || uv venv --python 3.10 "$PREFIX/venv-server"
# macOS torch wheels are CPU/MPS only, so plain PyPI is fine here.
# Skip packages with no macOS arm64 wheels. They back optional providers that
# upstream imports lazily, and Ivee doesn't select them.
MAC_SKIP='^(vosk)[=<> ]'
grep -Ev "$MAC_SKIP" "$APP_DIR/requirements.txt" > "$PREFIX/requirements-mac.txt"
uv pip install --quiet --python "$PREFIX/venv-server/bin/python" -r "$PREFIX/requirements-mac.txt"

log "Creating the voice venv and downloading Kokoro models"
[[ -d "$PREFIX/venv-tts" ]] || uv venv --python 3.10 "$PREFIX/venv-tts"
# piper-tts only for its bundled espeak-ng phonemizer; Kokoro does the speaking.
uv pip install --quiet --python "$PREFIX/venv-tts/bin/python" piper-tts kokoro-onnx
python3 "$REPO_DIR/server/tts/fetch_models.py" "$PREFIX/voices"
cp -R "$REPO_DIR/server/providers/." "$APP_DIR/core/providers/"

log "Starting the voice (Kokoro) on 127.0.0.1:$TTS_PORT and the config page on :$UI_PORT"
"$PREFIX/venv-tts/bin/python" "$REPO_DIR/server/tts/speel_tts.py" --host 127.0.0.1 --env "$ENV_FILE" \
  --model "$PREFIX/voices/kokoro-v1.0.onnx" --voices "$PREFIX/voices/voices-v1.0.bin" &
tts_pid=$!
python3 "$REPO_DIR/server/config_ui/ivee_config.py" serve --env "$ENV_FILE" --port "$UI_PORT" --apply manual &
ui_pid=$!
trap 'kill "$tts_pid" "$ui_pid" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  curl -fsS -o "$PREFIX/tts-test.wav" -X POST -H 'Content-Type: application/json' \
    -d '{"text":"Hi! I am Speel."}' "http://127.0.0.1:$TTS_PORT/synthesize" 2>/dev/null && break
  sleep 1
done
[[ -s "$PREFIX/tts-test.wav" ]] || die "The voice didn't answer on :$TTS_PORT."
echo "Voice OK. Hear it: afplay $PREFIX/tts-test.wav"

cat <<EOF

  Config page : http://${wifi_ip:-127.0.0.1}:${UI_PORT}
EOF
[[ -n "$password" ]] && echo "  Password    : $password   (shown once; to reset, delete $CONFIG/ui-password and re-run)"
if ! render >/dev/null 2>&1; then
  echo
  echo "Finish setup on the config page (Groq API key, server address). Waiting for it..."
  until render >/dev/null 2>&1; do sleep 3; done
fi
render
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"  # again: the config page may have changed it
set +a

cat <<EOF

Starting Ivee's server. Ctrl-C to stop. After saving on the config page, re-run this script.
  Device OTA URL : http://${SERVER_LAN_IP}:${SERVER_HTTP_PORT}/xiaozhi/ota/
  WebSocket      : ws://${SERVER_LAN_IP}:${SERVER_WS_PORT}/xiaozhi/v1/
  If macOS asks whether Python may accept incoming connections, click Allow.
EOF

# caffeinate keeps the Mac awake (lid open) while the server runs.
cd "$APP_DIR"
caffeinate -i env PYTHONUNBUFFERED=1 "$PREFIX/venv-server/bin/python" app.py
