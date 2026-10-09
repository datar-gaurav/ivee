"""Groq Whisper ASR tuned for a young child's voice.

Over upstream's `openai` ASR (which sends only the model):
- `language` (ISO-639-1, default "en"): otherwise Whisper guesses per clip and
  sometimes heard the child's English as Japanese.
- `prompt`: a short context hint (a young child talking to Speel), which helps
  Whisper with children's speech and spells the name consistently.
- Volume normalization: quiet clips (a child across the room) are scaled up to
  a fixed peak before upload; Whisper is more accurate on well-levelled audio.
The installers copy this file into upstream's core/providers/asr/, and the
config selects it with `type: sparky_groq`.
"""
import asyncio
import io
import wave
from typing import List, Optional, Tuple

import numpy as np
import requests

from config.logger import setup_logging
from core.providers.asr.openai import ASRProvider as OpenAIASRProvider

TAG = __name__
logger = setup_logging()

TARGET_PEAK = 0.9
MAX_GAIN = 8.0  # don't blow up near-silence into loud noise


def normalized_wav(file_path: str) -> bytes:
    """The clip as WAV bytes, peak-normalized (16-bit PCM only; else unchanged)."""
    with open(file_path, "rb") as f:
        data = f.read()
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() != 2:
                return data
            params = w.getparams()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    except wave.Error:
        return data
    peak = float(np.max(np.abs(pcm))) if pcm.size else 0.0
    if peak <= 0:
        return data
    gain = min(TARGET_PEAK / peak, MAX_GAIN)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes((np.clip(pcm * gain, -1, 1) * 32767).astype("<i2").tobytes())
    return out.getvalue()


class ASRProvider(OpenAIASRProvider):
    def __init__(self, config: dict, delete_audio_file: bool):
        super().__init__(config, delete_audio_file)
        self.language = config.get("language", "en")
        self.prompt = config.get("prompt") or ""

    def _transcribe(self, file_path: str) -> str:
        data = {"model": self.model, "language": self.language}
        if self.prompt:
            data["prompt"] = self.prompt
        response = requests.post(
            self.api_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            data=data,
            files={"file": ("speech.wav", normalized_wav(file_path), "audio/wav")},
            timeout=30,
        )
        if response.status_code != 200:
            raise Exception(f"Groq ASR HTTP {response.status_code} - {response.text}")
        return response.json().get("text", "")

    async def speech_to_text(
        self, opus_data: List[bytes], session_id: str, artifacts=None
    ) -> Tuple[Optional[str], Optional[str]]:
        if artifacts is None:
            return "", None
        try:
            text = await asyncio.to_thread(self._transcribe, artifacts.file_path)
            return text, artifacts.file_path
        except Exception as e:
            logger.bind(tag=TAG).error(f"Speech recognition failed: {e}")
            return "", None
