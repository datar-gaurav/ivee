#!/usr/bin/env bash
# Build Ivee's firmware: upstream xiaozhi-esp32 (checked out as ~/esp/ivee-esp32)
# for the Waveshare ESP32-S3-Touch-AMOLED-2.16, with our patches and faces.
# Most people don't need this: flash a prebuilt release from the browser (README).
#
# Checks out upstream firmware at FIRMWARE_REF, writes an `ivee.json` board
# config (upstream config.json + our OTA URL and wake word) next to upstream's,
# and builds with it, so the upstream checkout's own files stay untouched.
#
# Usage:
#   ./firmware/build.sh --list-wake-words
#   ./firmware/build.sh <wake-word> [ota-url]   then: ./firmware/build.sh --flash [port]
#   ./firmware/build.sh --release <out-dir>     (what CI publishes: wake word "speel",
#                                                default OTA URL, files for the web flasher)
# <wake-word> is either a pre-trained WakeNet model from the list (e.g.
# wn9_hijolly_tts2) or any English phrase (e.g. "speel"). A phrase uses
# ESP-SR's MultiNet7 English command recognizer, which converts the text to
# phonemes on the device, so it needs no training but is less accurate.
# The OTA URL is only the device's default: the Wi-Fi setup page's "Custom OTA
# URL" overrides it. It defaults to SERVER_LAN_IP/SERVER_HTTP_PORT from your
# ivee.env (IVEE_ENV, else ~/.ivee or /opt/ivee), else DEFAULT_OTA_URL.
# Speel's faces (firmware/faces/*.gif) replace upstream's emoji in the assets partition.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIRMWARE_REPO="${FIRMWARE_REPO:-https://github.com/78/xiaozhi-esp32.git}"
FIRMWARE_REF="${FIRMWARE_REF:-4f7c00fb8432e92b08732f5a3cd6c58f9f7ca0cd}"  # main, v2.5.1
FIRMWARE_DIR="${FIRMWARE_DIR:-$HOME/esp/ivee-esp32}"
IDF_PATH="${IDF_PATH:-$HOME/esp/esp-idf}"
BOARD=waveshare/esp32-s3-touch-amoled-2.16
LANGUAGE=en-US
# Never upstream's cloud: a device that wasn't given our server's URL should fail, not phone out.
DEFAULT_OTA_URL="http://ivee.local:8003/xiaozhi/ota/"

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

if [[ ! -d "$FIRMWARE_DIR/.git" ]]; then
  git clone "$FIRMWARE_REPO" "$FIRMWARE_DIR"
fi
git -C "$FIRMWARE_DIR" fetch --quiet origin
git -C "$FIRMWARE_DIR" checkout --quiet "$FIRMWARE_REF"
cd "$FIRMWARE_DIR"
# Our small upstream patches (firmware/patches/). Reset tracked files first so
# re-running applies them cleanly; untracked files like ivee.json are kept.
git checkout --quiet -- .
for patch in "$REPO_DIR"/firmware/patches/*.patch; do
  git apply "$patch" || die "Patch $(basename "$patch") no longer applies to $FIRMWARE_REF."
done

case "${1:-}" in
  "" | -h | --help)
    sed -n '2,23p' "${BASH_SOURCE[0]}"; exit 0 ;;
  --list-wake-words)
    python scripts/build.py --list-wake-words; exit 0 ;;
  --flash)
    ports=(/dev/cu.usbmodem* /dev/ttyACM*)
    port="${2:-}"
    for candidate in "${ports[@]}"; do
      [[ -z "$port" && -e "$candidate" ]] && port="$candidate"
    done
    [[ -n "$port" ]] || die "No USB serial port found. Plug the board in with a data cable."
    # Build first so flash has nothing left to regenerate, then re-pack the faces.
    idf.py build >/dev/null
    pack_faces
    idf.py -p "$port" flash monitor; exit 0 ;;
esac

release_dir=""
if [[ "$1" == --release ]]; then
  [[ -n "${2:-}" ]] || die "Usage: ./firmware/build.sh --release <out-dir>"
  release_dir="$(mkdir -p "$2" && cd "$2" && pwd)"
  wake_word="${IVEE_WAKE_WORD:-speel}"
  ota_url="$DEFAULT_OTA_URL"
else
  wake_word="$1"
  ota_url="${2:-}"
fi
if [[ -z "$ota_url" ]]; then
  env_file="${IVEE_ENV:-}"
  for candidate in "$HOME/.ivee/config/ivee.env" /opt/ivee/config/ivee.env; do
    [[ -z "$env_file" && -r "$candidate" ]] && env_file="$candidate"
  done
  lan_ip="" http_port=""
  if [[ -n "$env_file" ]]; then
    lan_ip="$(sed -n 's/^SERVER_LAN_IP=//p' "$env_file" | tail -1 | tr -d "\"'")"
    http_port="$(sed -n 's/^SERVER_HTTP_PORT=//p' "$env_file" | tail -1 | tr -d "\"'")"
  fi
  ota_url="$DEFAULT_OTA_URL"
  [[ -n "$lan_ip" ]] && ota_url="http://$lan_ip:${http_port:-8003}/xiaozhi/ota/"
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
(board_dir / "ivee.json").write_text(json.dumps(cfg, indent=4) + "\n")
PY

echo "Building $BOARD ($LANGUAGE, wake word '$wake_word') with OTA URL $ota_url"
python scripts/build.py "$BOARD" -c ivee.json --language "$LANGUAGE" ${wake_word_args[@]+"${wake_word_args[@]}"}
if [[ -n "$custom_phrase" ]]; then
  grep -q "CONFIG_CUSTOM_WAKE_WORD=\"$custom_phrase\"" sdkconfig || die "sdkconfig doesn't have the custom wake phrase."
fi

grep -q "CONFIG_OTA_URL=\"$ota_url\"" sdkconfig || die "sdkconfig doesn't have our OTA URL; don't flash this build."
pack_faces
if [[ -n "$release_dir" ]]; then
  idf.py merge-bin >/dev/null  # build/merged-binary.bin, with the re-packed faces
  python3 - "$release_dir" "${IVEE_VERSION:-$(git -C "$REPO_DIR" describe --tags --always)}" <<'PY'
import json, shutil, sys
from pathlib import Path
out, version = Path(sys.argv[1]), sys.argv[2]
args = json.loads(Path("build/flasher_args.json").read_text())
parts = []
for offset, file in sorted(args["flash_files"].items(), key=lambda item: int(item[0], 16)):
    name = Path(file).name
    shutil.copy(Path("build") / file, out / name)
    parts.append({"path": name, "offset": int(offset, 16)})
shutil.copy("build/merged-binary.bin", out / "ivee-merged.bin")
# ESP Web Tools manifest (docs/flash/). Erasing clears an old server URL from the device.
manifest = {"name": "Ivee", "version": version, "new_install_prompt_erase": True,
            "builds": [{"chipFamily": "ESP32-S3", "parts": parts}]}
(out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
# The binary includes upstream's code, ESP-IDF and many components: ship their licenses with it.
import os, zipfile
with zipfile.ZipFile(out / "firmware-licenses.zip", "w", zipfile.ZIP_DEFLATED) as z:
    sources = [("xiaozhi-esp32", Path("."))] + [(d.name, d) for d in sorted(Path("managed_components").iterdir())]
    sources.append(("esp-idf", Path(os.environ["IDF_PATH"])))
    for name, folder in sources:
        for f in sorted(folder.glob("*")):
            if f.is_file() and f.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                z.write(f, f"{name}/{f.name}")
print(f"Release files in {out}: {', '.join(p['path'] for p in parts)}, ivee-merged.bin, "
      "manifest.json, firmware-licenses.zip")
PY
  exit 0
fi
echo "Built. OTA URL confirmed in sdkconfig. Flash with: ./firmware/build.sh --flash"
