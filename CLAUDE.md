# Ivee — context for Claude Code

Ivee is an open-source (MIT) voice AI companion for young children. The project is "Ivee"; the character the child talks to is **Speel** (wake word "speel"). Kid safety and low cost are the top two constraints; prefer boring, reliable choices.

**Helping someone install or troubleshoot Ivee (not develop it)?** Follow [SETUP_WITH_CLAUDE.md](SETUP_WITH_CLAUDE.md).

## Hardware
- **Face/voice device:** Waveshare ESP32-S3-Touch-AMOLED-2.16 (480×480 AMOLED, mic with AEC, speaker, IMU, button). Runs upstream xiaozhi-esp32 firmware, checked out as `~/esp/ivee-esp32` (board `waveshare/esp32-s3-touch-amoled-2.16`, ESP-IDF ≥ 6.0.1).
- **Server:** any 64-bit Debian/Ubuntu/Raspberry Pi OS box with ≥ 2 GB RAM (`server/install.sh`), or a Mac for development (`server/run-mac.sh`). The maintainer's reference box is an HP Stream Mini 200-010 (Celeron 2957U, no AVX, 2 GB), with backups to a QNAP NAS.
- **Ollama (optional):** a local model on another computer, tried first while reachable.

## Architecture
ESP32 ⇄ WebSocket/Opus ⇄ ivee-esp32-server (lean, no Docker; systemd `ivee-server`)
  → STT: Groq whisper-large-v3 (English, child-context prompt, volume-normalized; VAD waits 1 s of silence)
  → LLM: Ollama (`OLLAMA_MODEL`) while reachable, then an optional extra OpenAI-compatible provider (`EXTRA_LLM_*`, e.g. NVIDIA NIM), then Groq (`GROQ_LLM_MODEL`, then `GROQ_FALLBACK_MODEL`)
  → TTS: local Kokoro HTTP (`server/tts/speel_tts.py`, 127.0.0.1:$TTS_PORT/synthesize), voice `af_heart` pitched up 2 semitones

Settings live outside the repo in `/opt/ivee/config/ivee.env` (Mac: `~/.ivee/config/ivee.env`), plus an optional `identity.md` there that overrides `persona/identity.md`. The config page (`server/config_ui/`, port 8088) edits them; on Linux, saving writes `config/apply.request`, `ivee-apply.path` runs `server/scripts/apply.sh` as root, which renders as the `ivee` user and restarts the services.

The upstream server (github.com/xinnan-tech/xiaozhi-esp32-server) is **not vendored**. The installer clones it at the pinned `IVEE_SERVER_REF` into /opt/ivee/ivee-esp32-server. Upstream names that are protocol or code (`/xiaozhi/ota/`, `/xiaozhi/v1/`, `main/xiaozhi-server`, the clone URLs) stay as they are. Our only touchpoint is `data/.config.yaml` (an overlay merged over upstream `config.yaml`) plus `data/ivee-base-prompt.txt`, both rendered by `server/scripts/render_config.py`. The one addition: `server/providers/` is copied into upstream's `core/providers/` (new files only, never edits to upstream files).

Firmware reaches users prebuilt: a `v*` tag runs `.github/workflows/release.yml`, which builds `./firmware/build.sh --release`, attaches it to a GitHub release and publishes the browser flasher (`docs/flash/`, ESP Web Tools) on GitHub Pages. The release firmware's default OTA URL is `http://ivee.local:8003/xiaozhi/ota/`, and the server publishes `ivee.local` over mDNS (`ivee-mdns.service` with avahi on Linux, `dns-sd` in run-mac.sh), so devices need no setup beyond Wi-Fi. Fallback: the device's Wi-Fi setup page (BOOT within ~3 s of power-on reopens it), Advanced → Custom OTA URL, stored in NVS.

## Layout
- `install.sh` — one-line bootstrap (`curl … | sudo bash`): clones to /opt/ivee/repo, runs `server/install.sh`
- `persona/identity.md` — the default character (becomes `prompt:`)
- `persona/base-prompt-template.txt` — Jinja system-prompt wrapper with the safety rules (`{{base_prompt}}`, `emoji_enabled`, `{{emojiList}}` come from upstream)
- `server/ivee.env.example` — every setting with its default; the installer copies it to the config dir
- `server/config/config.template.yaml` — overlay template, `{{PLACEHOLDERS}}` from ivee.env
- `server/install.sh` — idempotent Linux installer (uv, Python 3.10, CPU-only torch, Kokoro TTS, systemd, config page password); `--reset-password`
- `server/run-mac.sh` — the same stack in the foreground on a Mac
- `server/config_ui/` — config page: stdlib `http.server`, PBKDF2 password in `config/ui-password`, never returns the API key
- `server/scripts/render_config.py` — validation (`problems()`) and rendering, shared with the config page
- `server/scripts/apply.sh`, `backup.sh`, `publish-mdns.sh` — apply saved settings (root, via `ivee-apply.path`); nightly backup to `BACKUP_TARGET`; publish `ivee.local` (ivee-mdns)
- `server/tts/` — Speel's voice service (Kokoro + Piper's espeak phonemizer; reads TTS_* from ivee.env; requests may override them for previews) and its checksum-verified model download
- `server/systemd/` — templated units (`@PREFIX@` etc.): ivee-server, ivee-tts, ivee-config, ivee-apply.{path,service}, ivee-mdns
- `server/providers/asr/ivee_groq.py` — Groq Whisper with a fixed `language`, a child-context `prompt` and volume normalization
- `server/providers/ivee_patches.py` — runtime fixes to upstream (0.6 s audio head start, also rebuilt after a mid-reply pause; with firmware patch 0003's ~1 s playback buffer, keeps face animation from starving the speaker); sends a random face variant (`happy_02`) instead of the plain emotion, and the hi/bye/talking faces
- `server/providers/llm/ivee_groq/` — Groq LLM: `reasoning_effort: low`; upstream's Chinese few-shot examples translated to English (otherwise Speel answers in Chinese); only 3 tools (direct_answer, exit, volume) and the last 6 exchanges sent; on a 429, falls back to `GROQ_FALLBACK_MODEL`, and when every model is rate-limited Speel says it's sleepy and hangs up; bye phrases get one of 10 fixed goodbyes (`GOODBYES`) with no Groq call; `local_model` (Ollama) and `extra_model` (any OpenAI-compatible API) are tried first, and any failure of those falls through to Groq
- `firmware/build.sh` — dev builds (`"speel"`, `--flash`) and release builds (`--release <dir>`: parts, merged bin, ESP Web Tools manifest, licenses zip)
- `firmware/faces/` — Speel's animated face GIFs, 3 variants per emotion (and hi, bye, talking) picked at random by the server, plus a single `neutral` (420×420 max; see its README)
- `firmware/patches/` — minimal patches `build.sh` applies to the pinned firmware checkout (dark theme, max volume, bigger playback buffer, `Ivee-XXXX` hotspot name)
- `docs/flash/` — the browser flasher page; `docs/decisions.md` — why things are the way they are. Read it before changing direction.
- `tests/` — stdlib unittest for rendering and the config page; CI runs them plus shellcheck

## Free-tier budgets (checked Oct 2026)
- Groq free plan: Whisper 20 RPM / 2K RPD / 28.8K audio sec per day; gpt-oss / qwen ≈ 30 RPM, 1K RPD, 8K TPM, 200K TPD. The real cap is 200K tokens/day per model: at ~1.2–1.4K tokens per reply that's ~150 replies on `gpt-oss-20b`, plus as many again on the fallback `gpt-oss-120b` (measured Oct 2026; was ~1.9K/reply before trimming tools and history). Keep prompts small.
- Groq TTS (Orpheus) is only 100 RPD, which is why TTS is local.

## Known risks / open items
- [ ] Verify torch 2.2.2 CPU wheel runs on the no-AVX Celeron (the installer smoke-tests it). If not, switch Silero VAD to ONNX or try a different torch build.
- [ ] The Linux installer, the config page's apply flow and the release workflow are untested end to end (written Oct 2026; tested: unit tests, shellcheck, the Mac path). Test on a fresh Debian VM and a Raspberry Pi, and run the release workflow once.
- [x] The device resolves `ivee.local` (lwIP mDNS queries; verified Oct 2026 end to end on a real board: browser flash, Wi-Fi setup only, run-mac.sh publishing ivee.local, conversation works). Untested: avahi on a real Linux server (works in a container), networks that block multicast.
- [ ] The Wi-Fi setup page's Advanced tab Save didn't reach the device once (no "Saved settings" in the serial log even though the user saw a confirmation). Not reproduced or explained.
- [ ] English wake word: test "speel" (MultiNet7 phrase) with children's voices; consider a trained WakeNet model.
- [ ] Time Kokoro TTS on a 2 GB Celeron and a Pi 4 (`curl` the /synthesize endpoint). If a sentence takes more than ~1.5 s, offer Piper there (the Piper setup is in git history before the Kokoro switch).
- [ ] Turn on `server.auth` before anything is exposed beyond the LAN.
- [ ] Phase 2: port the server protocol to Cloudflare Workers + Durable Objects + Workers AI (free 10K neurons/day ≈ 400–500 conversations), which removes the hardware dependency.

## Conventions
- Never commit `ivee.env` or rendered configs (they contain keys).
- Any persona or prompt change must keep every rule in `<safety_rules>`.
- Shell: `set -euo pipefail`, run shellcheck. Python: stdlib-only for scripts that run before a venv exists (render_config, the config page, fetch_models).
