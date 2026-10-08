"""Groq Whisper ASR that pins the spoken language.

Upstream's `openai` ASR sends only the model, so Whisper guesses the language
per clip and sometimes hears a 4-year-old's English as Japanese. This sends
`language` (ISO-639-1, default "en") too. The installers copy this file into
upstream's core/providers/asr/, and the config selects it with `type: sparky_groq`.
"""
import asyncio
from typing import List, Optional, Tuple

import requests

from config.logger import setup_logging
from core.providers.asr.openai import ASRProvider as OpenAIASRProvider

TAG = __name__
logger = setup_logging()


class ASRProvider(OpenAIASRProvider):
    def __init__(self, config: dict, delete_audio_file: bool):
        super().__init__(config, delete_audio_file)
        self.language = config.get("language", "en")

    def _transcribe(self, file_path: str) -> str:
        with open(file_path, "rb") as audio_file:
            response = requests.post(
                self.api_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                data={"model": self.model, "language": self.language},
                files={"file": audio_file},
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
