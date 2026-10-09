# Sparky — context for Claude Code

Sparky is a voice AI companion for a 4-year-old. The project is "Sparky"; the character the child talks to is **Speel** (wake word "speel"; build it with `./firmware/build.sh "speel"`). Kid safety and low cost are the top two constraints; prefer boring, reliable choices.

## Hardware
- **Face/voice device:** Waveshare ESP32-S3-Touch-AMOLED-2.16 (480×480 AMOLED, mic with AEC, speaker, IMU, button). Runs upstream xiaozhi-esp32 firmware, checked out as `~/esp/ivee-esp32` (board `waveshare/esp32-s3-touch-amoled-2.16`, ESP-IDF ≥ 6.0.1).
- **Server (phase 1):** HP Stream Mini 200-010: Celeron 2957U (no AVX), 2 GB DDR3L, 32 GB SSD, Debian headless.
- **NAS:** QNAP TS-251 (Celeron J1800, 1 GB RAM). Too little RAM to host the server. Use it for backups only. Keep it LAN-only.
- **MacBook (Ollama):** optional fallback brain, only when it's awake.

## Architecture (phase 1)
ESP32 ⇄ WebSocket/Opus ⇄ ivee-esp32-server (lean, no Docker, on HP; systemd `ivee-server`)
  → STT: Groq whisper-large-v3 (English, child-context prompt, volume-normalized; VAD waits 1 s of silence)
  → LLM: Mac Ollama (`OLLAMA_MODEL`) while reachable, else Groq (`GROQ_LLM_MODEL`, then `GROQ_FALLBACK_MODEL`)
  → TTS: local Kokoro HTTP (`server/tts/speel_tts.py`, 127.0.0.1:$TTS_PORT/synthesize), voice `af_heart` pitched up 2 semitones

The upstream server (github.com/xinnan-tech/xiaozhi-esp32-server) is **not vendored**. The installer clones it at the pinned `IVEE_SERVER_REF` into /opt/sparky/ivee-esp32-server. Upstream names that are protocol or code (`/xiaozhi/ota/`, `/xiaozhi/v1/`, `main/xiaozhi-server`, the clone URLs) stay as they are. Our only touchpoint is `data/.config.yaml` (an overlay merged over upstream `config.yaml`) plus `data/sparky-base-prompt.txt`, both rendered by `server/scripts/render_config.py`. The one addition: `server/providers/` is copied into upstream's `core/providers/` (new files only, never edits to upstream files).

## Layout
- `persona/identity.md` — who Sparky is (becomes `prompt:`)
- `persona/base-prompt-template.txt` — Jinja system-prompt wrapper with the safety rules (`{{base_prompt}}`, `emoji_enabled`, `{{emojiList}}` come from upstream)
- `server/config/config.template.yaml` — overlay template, `{{PLACEHOLDERS}}` from `server/.env`
- `server/install-hp.sh` — idempotent installer (uv, Python 3.10, CPU-only torch, Kokoro TTS, systemd)
- `server/tts/` — Speel's voice service (Kokoro + Piper's espeak phonemizer) and its checksum-verified model download
- `server/systemd/*.service` — templated units (`@PREFIX@` etc.)
- `server/providers/asr/sparky_groq.py` — Groq Whisper with a fixed `language`, a child-context `prompt` and volume normalization
- `server/providers/sparky_patches.py` — runtime fixes to upstream (0.6 s audio head start, also rebuilt after a mid-reply pause; with firmware patch 0003's ~1 s playback buffer, keeps face animation from starving the speaker); sends a random face variant (`happy_02`) instead of the plain emotion, and the hi/bye/talking faces
- `server/providers/llm/sparky_groq/` — Groq LLM: `reasoning_effort: low`; upstream's Chinese few-shot examples translated to English (otherwise Speel answers in Chinese); only 3 tools (direct_answer, exit, volume) and the last 6 exchanges sent; on a 429, falls back to `GROQ_FALLBACK_MODEL`, and when every model is rate-limited Speel says he's sleepy and hangs up; bye phrases get one of 10 fixed goodbyes (`GOODBYES`) with no Groq call
- `server/scripts/backup-to-nas.sh` — config + logs to the NAS
- `firmware/README.md` — building and flashing with our OTA URL
- `firmware/faces/` — Speel's animated face GIFs, 3 variants per emotion (and hi, bye, talking) picked at random by the server, plus a single `neutral` (420×420 max; see its README)
- `firmware/patches/` — minimal patches `build.sh` applies to the pinned firmware checkout
- `docs/decisions.md` — why things are the way they are. Read it before changing direction.

## Free-tier budgets (checked Oct 2026)
- Groq free plan: Whisper 20 RPM / 2K RPD / 28.8K audio sec per day; gpt-oss / qwen ≈ 30 RPM, 1K RPD, 8K TPM, 200K TPD. The real cap is 200K tokens/day per model: at ~1.2–1.4K tokens per reply that's ~150 replies on `gpt-oss-20b`, plus as many again on the fallback `gpt-oss-120b` (measured Oct 2026; was ~1.9K/reply before trimming tools and history). Keep prompts small.
- Groq TTS (Orpheus) is only 100 RPD, which is why TTS is local.

## Known risks / open items
- [ ] Verify torch 2.2.2 CPU wheel runs on the no-AVX Celeron (the installer smoke-tests it). If not, switch Silero VAD to ONNX or try a different torch build.
- [x] Groq → Groq failover on 429 (`sparky_groq`, `GROQ_FALLBACK_MODEL`). Switching to Mac Ollama is still manual (`LLM_MODULE`).
- [ ] English wake word: pick a model via `scripts/build.py --list-wake-words` and test it with the child's voice.
- [ ] Time Kokoro TTS on the Celeron (`curl` the /synthesize endpoint). If a sentence takes more than ~1.5 s, go back to Piper there (the Piper setup is in git history before the Kokoro switch).
- [ ] Turn on `server.auth` before anything is exposed beyond the LAN.
- [ ] Phase 2: port the server protocol to Cloudflare Workers + Durable Objects + Workers AI (free 10K neurons/day ≈ 400–500 conversations), which removes the hardware dependency.

## Conventions
- Never commit `server/.env` or rendered configs (they contain keys).
- Any persona or prompt change must keep every rule in `<safety_rules>`.
- Shell: `set -euo pipefail`, run shellcheck. Python: stdlib-only for scripts that run before a venv exists.
