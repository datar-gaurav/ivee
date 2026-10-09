#!/usr/bin/env bash
# Sparky server installer for a small always-on Debian box (HP Stream Mini 200-010).
#
# Lean by design: no Docker, no web console, no database. Runs:
#   - sparky-server.service : xiaozhi-esp32-server core (WebSocket :8000, OTA :8003)
#   - sparky-tts.service    : Speel's voice, Kokoro TTS on 127.0.0.1:$TTS_PORT (server/tts/speel_tts.py)
#
# Prereqs: Debian 12/13 (no desktop), this repo cloned to /opt/sparky/repo,
#          server/.env filled in (cp server/.env.example server/.env).
# Usage:   sudo ./server/install-hp.sh
# Re-run safely after changing .env, the persona, or the pinned upstream ref.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/server/.env"
PREFIX=/opt/sparky
SERVER_DIR="$PREFIX/xiaozhi-esp32-server"
APP_DIR="$SERVER_DIR/main/xiaozhi-server"
SERVICE_USER=sparky

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run with sudo."
[[ -f "$ENV_FILE" ]] || die "Missing $ENV_FILE (copy server/.env.example and fill it in)."
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
: "${XIAOZHI_SERVER_REPO:?}" "${XIAOZHI_SERVER_REF:?}"

log "Checking hardware"
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
echo "RAM: ${mem_mb} MB"
(( mem_mb >= 1800 )) || die "Needs ~2 GB RAM."
if ! grep -qw avx /proc/cpuinfo; then
  echo "Note: CPU has no AVX (expected on the Celeron 2957U). PyTorch/ONNX will use slower code paths."
fi
free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
(( free_gb >= 6 )) || die "Need ~6 GB free disk, have ${free_gb} GB."

log "Installing system packages"
apt-get update -qq
apt-get install -y -qq git curl ca-certificates ffmpeg libopus0 rsync >/dev/null

if (( mem_mb < 3000 )) && ! swapon --show | grep -q .; then
  log "Adding 2 GB swapfile (low-RAM box)"
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "Creating service user and directories"
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$PREFIX/home" --shell /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$PREFIX/voices"

log "Installing uv (Python manager)"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi
export UV_PYTHON_INSTALL_DIR="$PREFIX/python"

log "Fetching xiaozhi-esp32-server @ ${XIAOZHI_SERVER_REF:0:12}"
if [[ ! -d "$SERVER_DIR/.git" ]]; then
  git clone --filter=blob:none "$XIAOZHI_SERVER_REPO" "$SERVER_DIR"
fi
git -C "$SERVER_DIR" fetch --quiet origin
git -C "$SERVER_DIR" checkout --quiet "$XIAOZHI_SERVER_REF"

log "Creating server venv (Python 3.10, as upstream recommends)"
uv python install 3.10
[[ -d "$PREFIX/venv-server" ]] || uv venv --python 3.10 "$PREFIX/venv-server"
# CPU-only torch first, so requirements.txt doesn't pull the multi-GB CUDA build.
uv pip install --python "$PREFIX/venv-server/bin/python" \
  --index-url https://download.pytorch.org/whl/cpu torch==2.2.2 torchaudio==2.2.2
uv pip install --python "$PREFIX/venv-server/bin/python" \
  --extra-index-url https://download.pytorch.org/whl/cpu -r "$APP_DIR/requirements.txt"

log "Smoke-testing torch + VAD on this CPU"
if ! "$PREFIX/venv-server/bin/python" -c "import torch; torch.zeros(1); import silero_vad" ; then
  die "torch/silero failed to load (possibly 'Illegal instruction' on this CPU). See docs/troubleshooting in README."
fi

log "Creating TTS venv and downloading Kokoro models"
[[ -d "$PREFIX/venv-tts" ]] || uv venv --python 3.10 "$PREFIX/venv-tts"
# piper-tts only for its bundled espeak-ng phonemizer; Kokoro does the speaking.
uv pip install --python "$PREFIX/venv-tts/bin/python" piper-tts kokoro-onnx
python3 "$REPO_DIR/server/tts/fetch_models.py" "$PREFIX/voices"

log "Rendering Sparky config and copying Sparky providers into the server"
python3 "$REPO_DIR/server/scripts/render_config.py" --env "$ENV_FILE" --out "$APP_DIR/data"
cp -R "$REPO_DIR/server/providers/." "$APP_DIR/core/providers/"

chown -R "$SERVICE_USER:$SERVICE_USER" "$PREFIX"

log "Installing systemd units"
for unit in sparky-tts.service sparky-server.service; do
  sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@USER@|$SERVICE_USER|g" \
      -e "s|@REPO_DIR@|$REPO_DIR|g" -e "s|@TTS_PORT@|${TTS_PORT:-5000}|g" \
      -e "s|@TTS_VOICE@|${TTS_VOICE:-af_heart}|g" -e "s|@TTS_SPEED@|${TTS_SPEED:-0.9}|g" \
      -e "s|@TTS_PITCH@|${TTS_PITCH:-1.12}|g" -e "s|@TTS_BOOST@|${TTS_BOOST:-2.0}|g" \
      -e "s|@TTS_HIGHPASS@|${TTS_HIGHPASS:-300}|g" -e "s|@TTS_PRESENCE@|${TTS_PRESENCE:-5}|g" \
      "$REPO_DIR/server/systemd/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now sparky-tts.service
systemctl restart sparky-server.service || true
systemctl enable sparky-server.service

log "Waiting for services"
for _ in $(seq 1 60); do
  curl -fsS -o /dev/null "http://127.0.0.1:8003/xiaozhi/ota/" && break
  sleep 2
done

if curl -fsS -o /tmp/sparky-tts-test.wav -X POST -H 'Content-Type: application/json' \
     -d '{"text":"Hi! I am Speel."}' "http://127.0.0.1:${TTS_PORT:-5000}/synthesize"; then
  echo "TTS OK ($(stat -c%s /tmp/sparky-tts-test.wav) bytes)"
else
  echo "TTS check failed: journalctl -u sparky-tts -n 50"
fi

if curl -fsS -o /dev/null "http://127.0.0.1:8003/xiaozhi/ota/"; then
  cat <<EOF

Sparky server is up.
  OTA URL for the device : http://${SERVER_LAN_IP}:8003/xiaozhi/ota/
  WebSocket              : ws://${SERVER_LAN_IP}:8000/xiaozhi/v1/
  Logs                   : journalctl -u sparky-server -f
EOF
else
  echo "Server not answering yet. Check: journalctl -u sparky-server -n 100"
  exit 1
fi
