# Sparky

A small, kid-safe voice companion with an animated face, built to run for about $0 a month.

```
┌──────────────────────┐   WebSocket + Opus   ┌────────────────────────────────┐
│ ESP32-S3 AMOLED 2.16 │ ───────────────────▶ │ HP Stream Mini (Debian)        │
│ wake word, mic/AEC,  │ ◀─────────────────── │ xiaozhi-esp32-server (lean)    │
│ speaker, emoji face  │                      │  ├─ STT  → Groq Whisper (free) │
└──────────────────────┘                      │  ├─ LLM  → Groq (free) / Ollama│
                                              │  └─ TTS  → Piper (local)       │
                                              └───────────────┬────────────────┘
                                                              │ nightly rsync
                                                       QNAP TS-251 (backups)
```

## Quick start

**1. Server (HP Stream Mini)**
1. Install Debian (netinst, no desktop, SSH server on). Reserve its IP in your router.
2. Run:
   ```bash
   sudo git clone <this repo> /opt/sparky/repo && cd /opt/sparky/repo
   cp server/.env.example server/.env && nano server/.env   # GROQ_API_KEY, SERVER_LAN_IP
   sudo ./server/install-hp.sh
   ```
3. The installer prints the **OTA URL** (`http://<ip>:8003/xiaozhi/ota/`).

**2. Device**
Follow [firmware/README.md](firmware/README.md) to build the firmware with that OTA URL and flash it.

**3. Talk to it.** Logs: `journalctl -u sparky-server -f`

## Changing Sparky
- Personality → `persona/identity.md`
- Safety rules → `persona/base-prompt-template.txt`
- Models, voice, keys → `server/.env`

Then re-run `sudo ./server/install-hp.sh`. It's idempotent.

## Backups
`server/scripts/backup-to-nas.sh` copies the config and logs to the NAS. Cron it nightly.

## Docs
- [docs/decisions.md](docs/decisions.md): why it's built this way
- [CLAUDE.md](CLAUDE.md): project context and open items

## Troubleshooting
- **`Illegal instruction` during the torch smoke test:** the Celeron lacks AVX. See the open item in CLAUDE.md.
- **Device can't connect:** check that `websocket:` in the rendered `data/.config.yaml` uses the LAN IP, and that the device and server share a network.
- **Groq 429s:** you hit a free-tier limit. Shorten history or replies, or switch `LLM_MODULE=MacOllamaLLM` temporarily.
