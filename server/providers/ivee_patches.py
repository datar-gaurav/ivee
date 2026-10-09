"""Runtime fixes to the upstream server (ivee-esp32-server), applied when Ivee's providers load.

Restore the audio head start after a pause (AudioRateController).
  Upstream sends the first 5 Opus frames (0.3 s) of a reply at once, then paces
  the rest in real time, so the device always has ~0.3 s queued. But when the
  send queue runs dry mid-reply (the next sentence isn't voiced yet, typical with
  a slowly streaming local LLM), `add_audio` resets its clock so the next frame
  goes out exactly "now" and every later frame arrives just in time: measured
  median 2 ms queued on the device instead of 296 ms. Any device hiccup (e.g.
  decoding a face GIF) then becomes an audible break. We move the clock back by
  HEAD_START_MS on such a resume, so the head start is rebuilt each time.
  After a pause the device has played out its queue, so this stays well under
  its 1.2 s buffer (firmware MAX_DECODE_PACKETS_IN_QUEUE).

Bigger head start at the start of a reply (sendAudioHandle.PRE_BUFFER_COUNT).
  Upstream sends 5 frames (0.3 s) up front. The firmware patch
  0003-bigger-playback-buffer.patch lets the device hold ~1 s of decoded audio,
  so we send HEAD_START_MS up front too, giving it audio to fill that with while
  it's busy drawing a face. Kept under the device's 1.2 s network buffer.

Speel's faces (textUtils.get_emotion, sendAudioHandle.send_tts_message).
  firmware/faces/ has FACE_VARIANTS GIFs per face (happy_01.gif ...), and the
  device shows the GIF whose name matches the emotion it's sent. Upstream sends
  the plain emotion ("happy"), so we send "happy_02" etc. instead, picked at
  random. Faces in NO_VARIANTS keep their plain name: the firmware itself
  sets "neutral" for idle and listening, so neutral has a single GIF.
  Besides the 21 emotions, three faces of our own:
  - "hi": the reply to the wake word (upstream sends the LLM WAKE_GREETING).
  - "bye": a reply starting with GOODBYE_EMOJI (ivee_groq's goodbyes), and any
    sentence spoken once the exit tool has run (conn.close_after_chat).
  - "talking": from the 2nd sentence on, once the reply has TALKING_SENTENCES
    sentences (stories, explanations). Short replies keep their emotion face.

The installers copy this into upstream's core/providers/; ivee_groq imports it.
"""
import json
import random
import re
import time

from config.logger import setup_logging
from core.handle import sendAudioHandle
from core.utils import textUtils
from core.utils.audioRateController import AudioRateController

TAG = __name__
logger = setup_logging()

HEAD_START_MS = 600
FRAME_MS = 60
FACE_VARIANTS = 3
NO_VARIANTS = {"neutral"}
GOODBYE_EMOJI = "👋"  # ivee_groq starts its goodbyes with it; TTS drops emoji
WAKE_GREETING = "嘿，你好呀"  # core/handle/textHandler/listenMessageHandler.py
TALKING_SENTENCES = 3
SENTENCE_END = re.compile(r"[.!?。！？]+(?=\s|$)")


def _resuming(controller):
    """Same test upstream uses to decide it is resuming after the queue ran dry."""
    if controller.queue or controller.play_position <= 0:
        return False
    idle_ms = (time.monotonic() - controller._last_queue_empty_time) * 1000
    return idle_ms >= controller.interval_ms


def _with_head_start(method):
    def wrapper(self, *args, **kwargs):
        resuming = _resuming(self)
        result = method(self, *args, **kwargs)
        if resuming and self.start_timestamp is not None:
            self.start_timestamp -= HEAD_START_MS / 1000
        return result
    wrapper.__wrapped__ = method
    return wrapper


async def _send_face(conn, emoji, face):
    if face not in NO_VARIANTS:
        face = f"{face}_{random.randint(1, FACE_VARIANTS):02d}"
    try:
        await conn.websocket.send(json.dumps(
            {"type": "llm", "text": emoji, "emotion": face, "session_id": conn.session_id}))
    except Exception as e:
        conn.logger.bind(tag=TAG).warning(f"Failed to send the face: {e}")


def _reply_to_wake_word(conn):
    last_user = next((m for m in reversed(conn.dialogue.dialogue) if m.role == "user"), None)
    return last_user is not None and WAKE_GREETING in str(last_user.content)


async def get_emotion(conn, text):
    """Upstream's get_emotion (pinned IVEE_SERVER_REF), plus hi/bye and variants.
    Called once per reply, with its first text."""
    emoji, face = "🙂", "happy"
    for char in text:
        if char in textUtils.EMOJI_MAP:
            emoji, face = char, textUtils.EMOJI_MAP[char]
            break
    if GOODBYE_EMOJI in text:
        emoji, face = GOODBYE_EMOJI, "bye"
    elif _reply_to_wake_word(conn):
        face = "hi"
    conn.speel_face = (conn.sentence_id, face)
    await _send_face(conn, emoji, face)


def _sentences(conn):
    text = "".join(getattr(conn.tts, "tts_text_buff", []) or []).strip()
    ends = len(SENTENCE_END.findall(text))
    return ends + (1 if text and not SENTENCE_END.search(text[-1]) else 0)


def _with_speaking_faces(send_tts_message):
    """Switch to the bye or talking face as each sentence starts playing."""
    async def wrapper(conn, state, text=None):
        if state == "sentence_start" and text:
            try:
                await _speaking_face(conn)
            except Exception as e:
                conn.logger.bind(tag=TAG).warning(f"Failed to pick the speaking face: {e}")
        return await send_tts_message(conn, state, text)
    wrapper.__wrapped__ = send_tts_message
    return wrapper


async def _speaking_face(conn):
    turn, face = getattr(conn, "speel_face", (None, None))
    if turn != conn.sentence_id:
        turn, face = conn.sentence_id, None
    starts = getattr(conn, "speel_sentence_starts", (None, 0))
    starts = starts[1] + 1 if starts[0] == turn else 1
    conn.speel_sentence_starts = (turn, starts)
    if conn.close_after_chat and face not in ("bye", "sleepy"):
        new_face, emoji = "bye", GOODBYE_EMOJI
    elif starts >= 2 and face not in ("hi", "bye", "sleepy", "talking") \
            and _sentences(conn) >= TALKING_SENTENCES:
        new_face, emoji = "talking", "🙂"
    else:
        return
    conn.speel_face = (turn, new_face)
    await _send_face(conn, emoji, new_face)


def apply():
    for name in ("add_audio", "add_message"):
        method = getattr(AudioRateController, name)
        if not hasattr(method, "__wrapped__"):
            setattr(AudioRateController, name, _with_head_start(method))
    sendAudioHandle.PRE_BUFFER_COUNT = HEAD_START_MS // FRAME_MS
    textUtils.get_emotion = get_emotion
    if not hasattr(sendAudioHandle.send_tts_message, "__wrapped__"):
        sendAudioHandle.send_tts_message = _with_speaking_faces(sendAudioHandle.send_tts_message)
    logger.bind(tag=TAG).info(f"Ivee patch: audio head start {HEAD_START_MS} ms (reply start and after pauses)")
