"""Speel's voice: Kokoro TTS over a tiny local HTTP API.

POST /synthesize {"text": "..."} -> audio/wav. Same interface the server's
`custom` TTS used with Piper, so only the URL's service changed.

- Voice: Kokoro (kokoro-onnx), e.g. am_puck.
- Phonemes come from Piper's bundled espeak-ng (piper.phonemize_espeak):
  kokoro-onnx's own espeak loader is broken on macOS wheels.
- Optional cartoon pitch (off by default; it hurt clarity): synthesize at speed/pitch, then label the WAV with
  sample_rate*pitch. Playback is pitch x higher and back at `speed`, with no
  resampling CPU cost (the server resamples to 16 kHz for Opus anyway).
- Loudness: every clip is peak-normalized, then soft-clipped with tanh
  (--boost). That lifts quiet syllables toward the peaks, so speech sounds
  louder on the small speaker without hard clipping. --boost 1 turns it off.
"""
import argparse
import io
import json
import logging
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from kokoro_onnx import Kokoro
from piper.phonemize_espeak import EspeakPhonemizer

MAX_PHONEMES = 500  # Kokoro's limit is 510 per call
log = logging.getLogger("speel_tts")


class SpeelVoice:
    def __init__(self, model, voices, voice, speed, pitch, peak, boost):
        self.kokoro = Kokoro(model, voices)
        self.phonemizer = EspeakPhonemizer()
        self.voice, self.speed, self.pitch, self.peak, self.boost = voice, speed, pitch, peak, boost
        self.lock = threading.Lock()  # one synthesis at a time; espeak is global state

    def _chunks(self, text):
        """Phoneme strings under Kokoro's limit, split on sentence boundaries."""
        chunk = ""
        for sentence in self.phonemizer.phonemize("en-us", text):
            ph = "".join(sentence)
            if chunk and len(chunk) + len(ph) + 1 > MAX_PHONEMES:
                yield chunk
                chunk = ""
            chunk = f"{chunk} {ph}".strip()[:MAX_PHONEMES]
        if chunk:
            yield chunk

    def wav(self, text):
        with self.lock:
            parts, sr = [], 24000
            for phonemes in self._chunks(text):
                samples, sr = self.kokoro.create(
                    phonemes, voice=self.voice, speed=self.speed / self.pitch, is_phonemes=True)
                parts.append(samples)
        audio = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
        audio = audio / (float(np.max(np.abs(audio))) or 1.0)
        if self.boost > 1:
            audio = np.tanh(self.boost * audio) / np.tanh(self.boost)
        pcm = np.clip(audio * self.peak, -1, 1)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(round(sr * self.pitch))
            w.writeframes((pcm * 32767).astype("<i2").tobytes())
        return buf.getvalue()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--model", required=True, help="kokoro-v1.0.onnx")
    p.add_argument("--voices", required=True, help="voices-v1.0.bin")
    p.add_argument("--voice", default="am_puck")
    p.add_argument("--speed", type=float, default=0.9)
    p.add_argument("--pitch", type=float, default=1.0, help="1 = natural; 1.26 = +4 semitones")
    p.add_argument("--peak", type=float, default=0.95, help="normalize each clip to this peak")
    p.add_argument("--boost", type=float, default=2.5, help="tanh loudness drive; 1 = off")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    voice = SpeelVoice(args.model, args.voices, args.voice, args.speed, args.pitch, args.peak, args.boost)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/synthesize":
                self.send_error(404)
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                text = str(body.get("text", "")).strip()
                if not text:
                    self.send_error(400, "text is required")
                    return
                data = voice.wav(text)
            except Exception as e:  # keep serving; the server retries
                log.exception("synthesis failed")
                self.send_error(500, str(e))
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *a):
            log.info("%s %s", self.address_string(), fmt % a)

    log.info("Speel TTS on %s:%d (voice %s, speed %.2f, pitch %.2f, boost %.1f)",
             args.host, args.port, args.voice, args.speed, args.pitch, args.boost)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
