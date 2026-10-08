# Decisions

## D1: Self-host the xiaozhi server; never use the default xiaozhi.me cloud
The firmware's default OTA endpoint is a third-party cloud hosted in China. A child's voice shouldn't go there. We point the firmware at our own server.

## D2: Phase 1 runs on the HP Stream Mini (free), not new hardware
Options considered:
- Pi 5: cost money.
- N150 mini PC: about $200.
- QNAP TS-251 NAS: only 1 GB RAM.
- Oracle Always Free: idle-reclamation risk, and the tier was cut in June 2026.
- Cloudflare: requires a port.

The HP (2 GB) runs the lean server because Groq does the heavy work. The config lives in git and is backed up to the NAS, so if the 12-year-old HP dies, rebuilding on anything else takes about 30 minutes.

## D3: Groq for STT and LLM, local TTS
Groq's free tier is generous for Whisper and chat, but its TTS (Orpheus) allows only 100 requests a day, so TTS is local.

Voice (Oct 2026): Kokoro `am_puck` at natural pitch, speed 0.9, with a tanh loudness boost (`server/tts/speel_tts.py`), chosen by ear over Piper, which sounded robotic. A cartoon pitch-up of `af_bella` (+4 semitones) was tried first and dropped: too hard to understand. The pitch option remains (`TTS_PITCH`). Piper's bundled espeak-ng still does the phonemes (kokoro-onnx's own espeak loader is broken on macOS). The pitch shift, if used, is free: synthesize slower and label the WAV with a higher sample rate. Kokoro is ~7× slower than Piper (0.7 s per 5 s of speech on an M-series Mac); on the Celeron it is untested and may be too slow, in which case restore the Piper setup from git history.

## D4: No long-term memory (`Memory: nomem`)
We don't store a profile of the child by default. Revisit only deliberately, and only with local storage.

## D5: Minimal tools
No web search, news, or role switching. Only exit and local music are enabled. Fewer tools means fewer surprises and smaller prompts (the Groq 8K TPM limit).

## D6: Upstream not vendored; pinned by commit
We only own an overlay config and the prompt files. Bumping `XIAOZHI_SERVER_REF` is a deliberate act.
Exception: extra providers in `server/providers/`, which the installers copy into upstream's tree as new files. Upstream loads a provider by its `type:` name, so this needs no edits to upstream code. `sparky_groq` exists because upstream's `openai` ASR sends no `language` and Whisper heard the child as Japanese. Check it still matches upstream's ASR interface after bumping the ref.

## D7: Phase 2 target is Cloudflare
Workers + Durable Objects (WebSocket sessions) + Workers AI (Whisper, MeloTTS, Llama). At about 20 neurons per exchange, the 10K/day free tier gives roughly 400–500 conversations. This needs a TypeScript port of the device protocol and WASM Opus handling. Benefits: no hardware, and the device works on any Wi-Fi.
