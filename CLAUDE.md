# Sparky — context for Claude Code

Sparky is a voice AI companion for a 4-year-old. The project is "Sparky"; the character the child talks to is **Speel** (wake phrase "hi speel"). Kid safety and low cost are the top two constraints; prefer boring, reliable choices.

## Hardware
- **Face/voice device:** Waveshare ESP32-S3-Touch-AMOLED-2.16 (480×480 AMOLED, mic with AEC, speaker, IMU, button). Runs upstream xiaozhi-esp32 firmware (board `waveshare/esp32-s3-touch-amoled-2.16`, ESP-IDF ≥ 6.0.1).
- **Server (phase 1):** HP Stream Mini 200-010: Celeron 2957U (no AVX), 2 GB DDR3L, 32 GB SSD, Debian headless.
- **NAS:** QNAP TS-251 (Celeron J1800, 1 GB RAM). Too little RAM to host the server. Use it for backups only. Keep it LAN-only.
- **MacBook (Ollama):** optional fallback brain, only when it's awake.

## Architecture (phase 1)
ESP32 ⇄ WebSocket/Opus ⇄ xiaozhi-esp32-server (lean, no Docker, on HP)
  → STT: Groq whisper-large-v3-turbo
  → LLM: Groq (`GROQ_LLM_MODEL`), with a manual switch to Mac Ollama
  → TTS: local Kokoro HTTP (`server/tts/speel_tts.py`, 127.0.0.1:$TTS_PORT/synthesize), voice `af_heart` pitched up 2 semitones

Upstream xiaozhi-esp32-server is **not vendored**. The installer clones it at the pinned `XIAOZHI_SERVER_REF` into /opt/sparky. Our only touchpoint is `data/.config.yaml` (an overlay merged over upstream `config.yaml`) plus `data/sparky-base-prompt.txt`, both rendered by `server/scripts/render_config.py`. The one addition: `server/providers/` is copied into upstream's `core/providers/` (new files only, never edits to upstream files).

## Layout
- `persona/identity.md` — who Sparky is (becomes `prompt:`)
- `persona/base-prompt-template.txt` — Jinja system-prompt wrapper with the safety rules (`{{base_prompt}}`, `emoji_enabled`, `{{emojiList}}` come from upstream)
- `server/config/config.template.yaml` — overlay template, `{{PLACEHOLDERS}}` from `server/.env`
- `server/install-hp.sh` — idempotent installer (uv, Python 3.10, CPU-only torch, Kokoro TTS, systemd)
- `server/tts/` — Speel's voice service (Kokoro + Piper's espeak phonemizer) and its checksum-verified model download
- `server/systemd/*.service` — templated units (`@PREFIX@` etc.)
- `server/providers/asr/sparky_groq.py` — Groq Whisper with a fixed `language` (upstream's sends none)
- `server/providers/llm/sparky_groq/` — Groq LLM with `reasoning_effort: low`, and upstream's Chinese few-shot examples translated to English (otherwise Speel answers in Chinese)
- `server/scripts/backup-to-nas.sh` — config + logs to the NAS
- `firmware/README.md` — building and flashing with our OTA URL
- `firmware/faces/` — Speel's animated face GIFs (420×420 max; see its README)
- `firmware/patches/` — minimal patches `build.sh` applies to the pinned firmware checkout
- `docs/decisions.md` — why things are the way they are. Read it before changing direction.

## Free-tier budgets (checked Oct 2026)
- Groq free plan: Whisper 20 RPM / 2K RPD / 28.8K audio sec per day; gpt-oss / qwen ≈ 30 RPM, 1K RPD, 8K TPM, 200K TPD. Keep prompts small, because the 8K TPM is the real cap.
- Groq TTS (Orpheus) is only 100 RPD, which is why TTS is local.

## Known risks / open items
- [ ] Verify torch 2.2.2 CPU wheel runs on the no-AVX Celeron (the installer smoke-tests it). If not, switch Silero VAD to ONNX or try a different torch build.
- [ ] Upstream has no automatic LLM fallback. Switching to Mac Ollama is manual (`LLM_MODULE`). An automatic Groq → Ollama failover on 429 would be a small provider wrapper.
- [ ] English wake word: pick a model via `scripts/build.py --list-wake-words` and test it with the child's voice.
- [ ] Time Kokoro TTS on the Celeron (`curl` the /synthesize endpoint). If a sentence takes more than ~1.5 s, go back to Piper there (the Piper setup is in git history before the Kokoro switch).
- [ ] Turn on `server.auth` before anything is exposed beyond the LAN.
- [ ] Phase 2: port the server protocol to Cloudflare Workers + Durable Objects + Workers AI (free 10K neurons/day ≈ 400–500 conversations), which removes the hardware dependency.

## Conventions
- Never commit `server/.env` or rendered configs (they contain keys).
- Any persona or prompt change must keep every rule in `<safety_rules>`.
- Shell: `set -euo pipefail`, run shellcheck. Python: stdlib-only for scripts that run before a venv exists.
