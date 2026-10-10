#!/usr/bin/env bash
# Ivee server installer for an always-on Linux box on your home network:
# Debian, Ubuntu or Raspberry Pi OS (64-bit), x86_64 or arm64, 2 GB RAM or more.
#
# Lean by design: no Docker, no database. Installs these systemd units:
#   - ivee-server.service : the voice server (upstream xiaozhi-esp32-server, as ivee-esp32-server)
#   - ivee-tts.service    : the character's voice, Kokoro TTS on 127.0.0.1
#   - ivee-config.service : the config page on :8088 (API key, voice, character)
#   - ivee-apply.path     : applies what the config page saves (server/scripts/apply.sh)
#   - ivee-mdns.service   : publishes this server as ivee.local, so devices find it without setup
#
# Usage:   sudo ./server/install.sh                    (or the one-line install.sh in the repo root)
#          sudo ./server/install.sh --reset-password   (new config page password)
# Settings live in /opt/ivee/config/ivee.env; edit them on the config page.
# Re-run safely after `git pull` or after changing IVEE_SERVER_REF.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/ivee
CONFIG="$PREFIX/config"
ENV_FILE="$CONFIG/ivee.env"
SERVER_DIR="$PREFIX/ivee-esp32-server"
APP_DIR="$SERVER_DIR/main/xiaozhi-server"
SERVICE_USER=ivee
UI_PORT="${IVEE_UI_PORT:-8088}"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1 | tr -d "\"'"; }
lan_ip() { ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p'; }

new_password() {
  password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(9))')"
  printf '%s\n' "$password" | runuser -u "$SERVICE_USER" -- \
    python3 "$REPO_DIR/server/config_ui/ivee_config.py" set-password --env "$ENV_FILE" >/dev/null
}
password=""

[[ $EUID -eq 0 ]] || die "Run with sudo."
if [[ "${1:-}" == "--reset-password" ]]; then
  [[ -f "$ENV_FILE" ]] || die "Ivee isn't installed yet: run this script without --reset-password."
  new_password
  echo "New config page password: $password"
  exit 0
fi

log "Checking this machine"
# shellcheck disable=SC1091
[[ -r /etc/os-release ]] && . /etc/os-release
[[ " ${ID:-} ${ID_LIKE:-} " == *" debian "* ]] || die "Needs Debian, Ubuntu or Raspberry Pi OS (found ${PRETTY_NAME:-unknown})."
arch="$(uname -m)"
[[ "$arch" == x86_64 || "$arch" == aarch64 ]] || die "Needs a 64-bit x86_64 or arm64 system (found $arch)."
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
echo "${PRETTY_NAME:-Linux} on $arch, ${mem_mb} MB RAM"
(( mem_mb >= 1400 )) || die "Needs about 2 GB of RAM."
if [[ "$arch" == x86_64 ]] && ! grep -qw avx /proc/cpuinfo; then
  echo "Note: this CPU has no AVX. It works, but speech and voice run slower."
fi
free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
(( free_gb >= 6 )) || die "Needs about 6 GB of free disk space (have ${free_gb} GB)."

log "Installing system packages"
apt-get update -qq
apt-get install -y -qq git curl ca-certificates ffmpeg libopus0 rsync python3 avahi-daemon avahi-utils >/dev/null

if (( mem_mb < 3000 )) && ! swapon --show | grep -q .; then
  log "Adding a 2 GB swap file (low-RAM machine)"
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "Creating the service user and settings"
# The ivee user runs scripts from the repo; a root umask of 077 would hide them.
chmod -R a+rX "$REPO_DIR"
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$PREFIX/home" --shell /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$PREFIX/voices" "$CONFIG"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$REPO_DIR/server/ivee.env.example" "$ENV_FILE"
  ip_now="$(lan_ip)"
  offset="$(python3 -c 'import time; print(f"{time.localtime().tm_gmtoff / 3600:g}")')"
  sed -i -e "s/^SERVER_LAN_IP=.*/SERVER_LAN_IP=${ip_now}/" -e "s/^TIMEZONE_OFFSET=.*/TIMEZONE_OFFSET=${offset}/" "$ENV_FILE"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$CONFIG"
chmod 700 "$CONFIG"
chmod 600 "$ENV_FILE"


log "Installing uv (Python manager)"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi
export UV_PYTHON_INSTALL_DIR="$PREFIX/python"

server_repo="$(env_value IVEE_SERVER_REPO)"
server_ref="$(env_value IVEE_SERVER_REF)"
[[ -n "$server_repo" && -n "$server_ref" ]] || die "IVEE_SERVER_REPO and IVEE_SERVER_REF must be set in $ENV_FILE."
log "Fetching the upstream server @ ${server_ref:0:12}"
if [[ ! -d "$SERVER_DIR/.git" ]]; then
  git clone --filter=blob:none "$server_repo" "$SERVER_DIR"
fi
# The checkout belongs to the service user; git refuses to work in it as root otherwise.
server_git() { git -c safe.directory="$SERVER_DIR" -C "$SERVER_DIR" "$@"; }
server_git fetch --quiet origin
server_git checkout --quiet "$server_ref"

log "Creating the server venv (Python 3.10, as upstream recommends)"
uv python install 3.10
[[ -d "$PREFIX/venv-server" ]] || uv venv --python 3.10 "$PREFIX/venv-server"
# CPU-only torch first, so requirements.txt doesn't pull the multi-GB CUDA build.
uv pip install --python "$PREFIX/venv-server/bin/python" \
  --index-url https://download.pytorch.org/whl/cpu torch==2.2.2 torchaudio==2.2.2
# The rest from PyPI, without the torch pins (already satisfied by the CPU build).
grep -Ev '^(torch|torchaudio)[=<> ]' "$APP_DIR/requirements.txt" > "$PREFIX/requirements-linux.txt"
uv pip install --python "$PREFIX/venv-server/bin/python" -r "$PREFIX/requirements-linux.txt"

log "Smoke-testing torch + VAD on this CPU"
if ! "$PREFIX/venv-server/bin/python" -c "import torch; torch.zeros(1); import silero_vad"; then
  die "torch/silero failed to load (on an old CPU this can be 'Illegal instruction'). See README troubleshooting."
fi

log "Creating the voice venv and downloading Kokoro models"
[[ -d "$PREFIX/venv-tts" ]] || uv venv --python 3.10 "$PREFIX/venv-tts"
# piper-tts only for its bundled espeak-ng phonemizer; Kokoro does the speaking.
uv pip install --python "$PREFIX/venv-tts/bin/python" piper-tts kokoro-onnx
python3 "$REPO_DIR/server/tts/fetch_models.py" "$PREFIX/voices"

log "Adding Ivee's providers to the server"
cp -R "$REPO_DIR/server/providers/." "$APP_DIR/core/providers/"
# The service user owns what it writes; the repo stays root's, because
# ivee-apply runs server/scripts/apply.sh from it as root.
chown -R "$SERVICE_USER:$SERVICE_USER" "$SERVER_DIR" "$PREFIX/venv-server" "$PREFIX/venv-tts" \
  "$PREFIX/voices" "$PREFIX/home" "$PREFIX/python"

configured=false
if output="$(runuser -u "$SERVICE_USER" -- python3 "$REPO_DIR/server/scripts/render_config.py" \
               --env "$ENV_FILE" --out "$APP_DIR/data" 2>&1)"; then
  configured=true
else
  echo "${output}"
  echo "(Fine on a first install: finish setup on the config page.)"
fi

log "Installing systemd units"
for unit in ivee-tts.service ivee-server.service ivee-config.service ivee-apply.service ivee-apply.path \
            ivee-mdns.service; do
  sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@USER@|$SERVICE_USER|g" \
      -e "s|@REPO_DIR@|$REPO_DIR|g" -e "s|@UI_PORT@|$UI_PORT|g" \
      "$REPO_DIR/server/systemd/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now ivee-tts.service ivee-config.service ivee-apply.path avahi-daemon.service ivee-mdns.service
systemctl enable ivee-server.service
systemctl restart ivee-tts.service ivee-config.service ivee-mdns.service
$configured && systemctl restart ivee-server.service

# Only now, so a failed install never loses a password it didn't print.
[[ -f "$CONFIG/ui-password" ]] || new_password
ip_now="$(env_value SERVER_LAN_IP)"
http_port="$(env_value SERVER_HTTP_PORT)"
log "Done"
if $configured; then
  for _ in $(seq 1 60); do
    curl -fsS -o /dev/null "http://127.0.0.1:${http_port:-8003}/xiaozhi/ota/" && break
    sleep 2
  done
  echo "Ivee's server is running."
else
  echo "Ivee is installed. Open the config page to add your Groq API key and start it."
fi
cat <<EOF

  Config page      : http://${ip_now:-$(lan_ip)}:${UI_PORT}
EOF
[[ -n "$password" ]] && echo "  Password         : $password   (shown once; reset with: sudo $REPO_DIR/server/install.sh --reset-password)"
cat <<EOF
  Device           : finds this server as ivee.local by itself. If it doesn't, enter
                     http://${ip_now}:${http_port:-8003}/xiaozhi/ota/ as the Custom OTA URL on its Wi-Fi setup page
  Logs             : journalctl -u ivee-server -f
EOF
