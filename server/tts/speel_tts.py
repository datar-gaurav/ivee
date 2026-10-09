"""Speel's voice: Kokoro TTS over a tiny local HTTP API.

POST /synthesize {"text": "..."} -> audio/wav. Same interface the server's
`custom` TTS used with Piper, so only the URL's service changed.

- Voice: Kokoro (kokoro-onnx), e.g. af_heart.
- Phonemes come from Piper's bundled espeak-ng (piper.phonemize_espeak):
  kokoro-onnx's own espeak loader is broken on macOS wheels.
- Playful pitch (1.12 = +2 semitones; +4 hurt clarity): synthesize at speed/pitch, then label the WAV with
  sample_rate*pitch. Playback is pitch x higher and back at `speed`, with no
  resampling CPU cost (the server resamples to 16 kHz for Opus anyway).
- Small-speaker EQ: cut below --highpass Hz (the speaker can't play it; it
  only wastes amplifier headroom and buzzes) and lift consonants around 3 kHz
  by --presence dB. Done with one FFT per clip, numpy only.
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


def small_speaker_eq(audio, rate, highpass, presence_db):
    """4th-order high-pass at `highpass` Hz plus a one-octave bell at 3 kHz."""
    if not highpass and not presence_db:
        return audio
    spectrum = np.fft.rfft(audio)
    f = np.maximum(np.fft.rfftfreq(len(audio), 1 / rate), 1.0)
    gain = np.ones_like(f)
    if highpass:
        gain /= np.sqrt(1 + (highpass / f) ** 8)
    if presence_db:
        gain *= 10 ** (presence_db * np.exp(-np.log2(f / 3000) ** 2 / 0.5) / 20)
    return np.fft.irfft(spectrum * gain, len(audio)).astype(np.float32)


class SpeelVoice:
    def __init__(self, model, voices, voice, speed, pitch, peak, boost, highpass, presence):
        self.kokoro = Kokoro(model, voices)
        self.phonemizer = EspeakPhonemizer()
        self.voice, self.speed, self.pitch, self.peak, self.boost = voice, speed, pitch, peak, boost
        self.highpass, self.presence = highpass, presence
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
        # EQ in playback frequencies: the pitch trick plays everything pitch x higher.
        audio = small_speaker_eq(audio, sr * self.pitch, self.highpass, self.presence)
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
    p.add_argument("--voice", default="af_heart")
    p.add_argument("--speed", type=float, default=0.9)
    p.add_argument("--pitch", type=float, default=1.12, help="1 = natural; 1.12 = +2 semitones")
    p.add_argument("--peak", type=float, default=0.95, help="normalize each clip to this peak")
    p.add_argument("--boost", type=float, default=2.0, help="tanh loudness drive; 1 = off")
    p.add_argument("--highpass", type=float, default=300, help="cut below this many Hz; 0 = off")
    p.add_argument("--presence", type=float, default=5, help="dB lift around 3 kHz; 0 = off")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    voice = SpeelVoice(args.model, args.voices, args.voice, args.speed, args.pitch, args.peak,
                       args.boost, args.highpass, args.presence)

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

    log.info("Speel TTS on %s:%d (voice %s, speed %.2f, pitch %.2f, boost %.1f, highpass %g Hz, presence %g dB)",
             args.host, args.port, args.voice, args.speed, args.pitch, args.boost, args.highpass, args.presence)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
