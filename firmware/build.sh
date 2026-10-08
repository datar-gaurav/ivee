#!/usr/bin/env bash
# Build xiaozhi-esp32 firmware for Sparky's board, pointed at Sparky's OTA URL.
#
# Checks out upstream firmware at FIRMWARE_REF, writes a `sparky.json` board
# config (upstream config.json + our OTA URL) next to upstream's, and builds
# with it, so the upstream checkout's own files stay untouched.
#
# Usage:
#   ./firmware/build.sh --list-wake-words
#   ./firmware/build.sh <wake-word> [ota-url]
# <wake-word> is either a pre-trained WakeNet model from the list (e.g.
# wn9_hijolly_tts2) or any English phrase (e.g. "hi speel"). A phrase uses
# ESP-SR's MultiNet7 English command recognizer, which converts the text to
# phonemes on the device, so it needs no training but is less accurate.
# The OTA URL defaults to http://$SERVER_LAN_IP:$SERVER_HTTP_PORT/xiaozhi/ota/ from server/.env.
# Then flash: ./firmware/build.sh --flash [port]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIRMWARE_REPO="${FIRMWARE_REPO:-https://github.com/78/xiaozhi-esp32.git}"
FIRMWARE_REF="${FIRMWARE_REF:-4f7c00fb8432e92b08732f5a3cd6c58f9f7ca0cd}"  # main, v2.5.1
FIRMWARE_DIR="${FIRMWARE_DIR:-$HOME/esp/xiaozhi-esp32}"
IDF_PATH="${IDF_PATH:-$HOME/esp/esp-idf}"
BOARD=waveshare/esp32-s3-touch-amoled-2.16
LANGUAGE=en-US

die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ -f "$IDF_PATH/export.sh" ]] || die "ESP-IDF not found at $IDF_PATH (set IDF_PATH)."
# shellcheck disable=SC1091
. "$IDF_PATH/export.sh" >/dev/null

if [[ ! -d "$FIRMWARE_DIR/.git" ]]; then
  git clone "$FIRMWARE_REPO" "$FIRMWARE_DIR"
fi
git -C "$FIRMWARE_DIR" fetch --quiet origin
git -C "$FIRMWARE_DIR" checkout --quiet "$FIRMWARE_REF"
cd "$FIRMWARE_DIR"

case "${1:-}" in
  "" | -h | --help)
    sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 0 ;;
  --list-wake-words)
    python scripts/build.py --list-wake-words; exit 0 ;;
  --flash)
    port="${2:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)}"
    [[ -n "$port" ]] || die "No /dev/cu.usbmodem* found. Plug the board in with a data cable."
    idf.py -p "$port" flash monitor; exit 0 ;;
esac

wake_word="$1"
ota_url="${2:-}"
if [[ -z "$ota_url" ]]; then
  [[ -f "$REPO_DIR/server/.env" ]] || die "Pass an OTA URL or create server/.env."
  SERVER_LAN_IP="$(sed -n 's/^SERVER_LAN_IP=//p' "$REPO_DIR/server/.env" | tr -d "\"'")"
  [[ -n "$SERVER_LAN_IP" ]] || die "SERVER_LAN_IP is empty in server/.env."
  SERVER_HTTP_PORT="$(sed -n 's/^SERVER_HTTP_PORT=//p' "$REPO_DIR/server/.env" | tr -d "\"'")"
  ota_url="http://$SERVER_LAN_IP:${SERVER_HTTP_PORT:-8003}/xiaozhi/ota/"
fi
[[ "$ota_url" == */xiaozhi/ota/ ]] || die "OTA URL should end in /xiaozhi/ota/ (got $ota_url)."

wake_word_args=()
custom_phrase=""
if [[ "$wake_word" =~ ^wn9[sl]?_[a-z0-9_]+$ || "$wake_word" == disabled ]]; then
  wake_word_args=(--wake-word "$wake_word")
else
  custom_phrase="$(tr '[:upper:]' '[:lower:]' <<<"$wake_word")"
  [[ "$custom_phrase" =~ ^[a-z\ ]+$ ]] || die "Wake phrase must be English letters and spaces (got '$wake_word')."
fi

python3 - "main/boards/$BOARD" "$ota_url" "$custom_phrase" <<'PY'
import json, sys
from pathlib import Path
board_dir, ota_url, phrase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
ours = [f'CONFIG_OTA_URL="{ota_url}"']
if phrase:
    ours += [
        "CONFIG_USE_AFE_WAKE_WORD=n",
        "CONFIG_USE_CUSTOM_WAKE_WORD=y",
        f'CONFIG_CUSTOM_WAKE_WORD="{phrase}"',
        f'CONFIG_CUSTOM_WAKE_WORD_DISPLAY="{phrase}"',
        "CONFIG_SR_MN_EN_MULTINET7_QUANT=y",
        "CONFIG_SR_MN_CN_NONE=y",
        "CONFIG_SR_WN_WN9_NIHAOXIAOZHI_TTS=n",  # default WakeNet model; unused, saves flash
    ]
keys = {line.split("=", 1)[0] for line in ours}
cfg = json.loads((board_dir / "config.json").read_text())
for build in cfg["builds"]:
    build["sdkconfig_append"] = [
        line for line in build.get("sdkconfig_append", []) if line.split("=", 1)[0] not in keys
    ] + ours
(board_dir / "sparky.json").write_text(json.dumps(cfg, indent=4) + "\n")
PY

echo "Building $BOARD ($LANGUAGE, wake word '$wake_word') with OTA URL $ota_url"
python scripts/build.py "$BOARD" -c sparky.json --language "$LANGUAGE" ${wake_word_args[@]+"${wake_word_args[@]}"}
if [[ -n "$custom_phrase" ]]; then
  grep -q "CONFIG_CUSTOM_WAKE_WORD=\"$custom_phrase\"" sdkconfig || die "sdkconfig doesn't have the custom wake phrase."
fi

grep -q "CONFIG_OTA_URL=\"$ota_url\"" sdkconfig || die "sdkconfig doesn't have our OTA URL; don't flash this build."
echo "Built. OTA URL confirmed in sdkconfig. Flash with: ./firmware/build.sh --flash"
