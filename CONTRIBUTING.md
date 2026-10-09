# Contributing

Thanks for helping. Ivee is for young children, so two rules come before everything else:

1. **Kid safety.** Any change to `persona/` must keep every rule in `<safety_rules>`. Changes that send more data off the home network, or keep data about the child, need a strong reason and a note in `docs/decisions.md`.
2. **Low cost, boring choices.** Ivee runs on a 2 GB box and Groq's free tier. Keep prompts small and avoid new services or heavy dependencies.

## Before you start
- Read [docs/decisions.md](docs/decisions.md): it explains why things are the way they are.
- The upstream server and firmware are **not forked**. Change them through new provider files (`server/providers/`), runtime patches (`server/providers/ivee_patches.py`) or small firmware patches (`firmware/patches/`). Never edit upstream files in place.
- For a bigger change, open an issue first.

## Checks
```bash
shellcheck install.sh server/*.sh server/scripts/*.sh firmware/build.sh
python3 -m unittest discover -s tests
```
CI runs both. Scripts that run before a venv exists (`render_config.py`, the config page, `fetch_models.py`) must use the Python standard library only. Shell scripts use `set -euo pipefail`.

## Testing on real hardware
`./server/run-mac.sh` runs the whole server on a Mac. `./firmware/build.sh "speel"` then `./firmware/build.sh --flash` builds and flashes the firmware (ESP-IDF 6.0.1 or newer). When you touch faces, audio or the TTS, listen for choppy audio while the face animates.

## Releases
Pushing a `v*` tag builds the firmware, attaches it to a GitHub release and updates the browser installer on GitHub Pages (`.github/workflows/release.yml`). Pages must be set to "GitHub Actions" in the repository settings.

Never commit `ivee.env` or a rendered `.config.yaml`: they contain API keys.
