#!/usr/bin/env bash
# Build the upstream ESP32 firmware (checked out as ivee-esp32) for Sparky's board, pointed at Sparky's OTA URL.
#
# Checks out upstream firmware at FIRMWARE_REF, writes a `sparky.json` board
# config (upstream config.json + our OTA URL) next to upstream's, and builds
# with it, so the upstream checkout's own files stay untouched.
#
# Usage:
#   ./firmware/build.sh --list-wake-words
#   ./firmware/build.sh <wake-word> [ota-url]
# <wake-word> is either a pre-trained WakeNet model from the list (e.g.
# wn9_hijolly_tts2) or any English phrase (e.g. "speel"). A phrase uses
# ESP-SR's MultiNet7 English command recognizer, which converts the text to
# phonemes on the device, so it needs no training but is less accurate.
# The OTA URL defaults to http://$SERVER_LAN_IP:$SERVER_HTTP_PORT/xiaozhi/ota/ from server/.env.
# Then flash: ./firmware/build.sh --flash [port]
# Speel's faces (firmware/faces/*.gif) replace upstream's emoji in the assets partition.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIRMWARE_REPO="${FIRMWARE_REPO:-https://github.com/78/xiaozhi-esp32.git}"
FIRMWARE_REF="${FIRMWARE_REF:-4f7c00fb8432e92b08732f5a3cd6c58f9f7ca0cd}"  # main, v2.5.1
FIRMWARE_DIR="${FIRMWARE_DIR:-$HOME/esp/ivee-esp32}"
IDF_PATH="${IDF_PATH:-$HOME/esp/esp-idf}"
BOARD=waveshare/esp32-s3-touch-amoled-2.16
LANGUAGE=en-US

die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# Rebuild upstream's assets.bin with Speel's faces as the emoji collection.
# Reuses upstream's exact asset command from the last build (fonts, wake-word
# models, size cap) and swaps only --emoji_collection, so no upstream file changes.
# Aliases are extra index entries pointing at an existing GIF, so they cost no space.
# Also sets hide_subtitle so no text covers the face.
pack_faces() {
  python - "$REPO_DIR/firmware/faces" <<'PY'
import contextlib, importlib.util, io, json, shlex, subprocess, sys
from pathlib import Path
faces = Path(sys.argv[1]).resolve()
aliases = [line.split() for line in (faces / "aliases.txt").read_text().splitlines()
           if line.strip() and not line.startswith("#")]

commands = subprocess.run(
    ["ninja", "-C", "build", "-t", "commands", "generated_default_assets"],
    capture_output=True, text=True, check=True).stdout
cmd = next(l for l in reversed(commands.splitlines()) if "build_default_assets.py" in l)
tokens = shlex.split(cmd)
start = next(i for i, t in enumerate(tokens) if t.endswith("build_default_assets.py"))
args = tokens[start + 1:]
args[args.index("--emoji_collection") + 1] = str(faces)
output = Path(args[args.index("--output") + 1])

spec = importlib.util.spec_from_file_location("build_default_assets", tokens[start])
upstream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream)
process_faces = upstream.process_emoji_collection
def process_with_aliases(emoji_dir, assets_dir):
    emoji = process_faces(emoji_dir, assets_dir)
    files = {e["name"]: e["file"] for e in emoji}
    return emoji + [{"name": alias, "file": files[face]} for alias, face in aliases]
upstream.process_emoji_collection = process_with_aliases

# Hide the subtitle bar so the face isn't covered by text. Upstream reads
# "hide_subtitle" from the assets index.json at boot.
write_index = upstream.generate_index_json
def write_index_hiding_subtitles(assets_dir, *a, **kw):
    write_index(assets_dir, *a, **kw)
    index = Path(assets_dir) / "index.json"
    data = json.loads(index.read_text())
    data["hide_subtitle"] = True
    index.write_text(json.dumps(data, indent=4, ensure_ascii=False))
upstream.generate_index_json = write_index_hiding_subtitles
sys.argv = [tokens[start], *args]
with contextlib.redirect_stdout(io.StringIO()):
    upstream.main()

if b"neutral.gif" not in output.read_bytes():
    sys.exit(f"{output} doesn't contain Speel's faces")
print(f"Packed {len(list(faces.glob('*.gif')))} faces + {len(aliases)} aliases into {output}")
PY
}

[[ -f "$IDF_PATH/export.sh" ]] || die "ESP-IDF not found at $IDF_PATH (set IDF_PATH)."
# shellcheck disable=SC1091
. "$IDF_PATH/export.sh" >/dev/null

# Earlier builds used the upstream folder name; move that checkout over. Its
# build/ caches the old absolute path, so it is rebuilt from scratch.
if [[ ! -d "$FIRMWARE_DIR" && -d "$HOME/esp/xiaozhi-esp32/.git" ]]; then
  mv "$HOME/esp/xiaozhi-esp32" "$FIRMWARE_DIR"
  rm -rf "$FIRMWARE_DIR/build"
fi
if [[ ! -d "$FIRMWARE_DIR/.git" ]]; then
  git clone "$FIRMWARE_REPO" "$FIRMWARE_DIR"
fi
git -C "$FIRMWARE_DIR" fetch --quiet origin
git -C "$FIRMWARE_DIR" checkout --quiet "$FIRMWARE_REF"
cd "$FIRMWARE_DIR"
# Our small upstream patches (firmware/patches/). Reset tracked files first so
# re-running applies them cleanly; untracked files like sparky.json are kept.
git checkout --quiet -- .
for patch in "$REPO_DIR"/firmware/patches/*.patch; do
  git apply "$patch" || die "Patch $(basename "$patch") no longer applies to $FIRMWARE_REF."
done

case "${1:-}" in
  "" | -h | --help)
    sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
  --list-wake-words)
    python scripts/build.py --list-wake-words; exit 0 ;;
  --flash)
    port="${2:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)}"
    [[ -n "$port" ]] || die "No /dev/cu.usbmodem* found. Plug the board in with a data cable."
    # Build first so flash has nothing left to regenerate, then re-pack the faces.
    idf.py build >/dev/null
    pack_faces
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
pack_faces
echo "Built. OTA URL confirmed in sdkconfig. Flash with: ./firmware/build.sh --flash"
