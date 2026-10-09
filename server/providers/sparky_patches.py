"""Runtime fixes to upstream xiaozhi-esp32-server, applied when Sparky's providers load.

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

The installers copy this into upstream's core/providers/; sparky_groq imports it.
"""
import time

from config.logger import setup_logging
from core.handle import sendAudioHandle
from core.utils.audioRateController import AudioRateController

TAG = __name__
logger = setup_logging()

HEAD_START_MS = 600
FRAME_MS = 60


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


def apply():
    for name in ("add_audio", "add_message"):
        method = getattr(AudioRateController, name)
        if not hasattr(method, "__wrapped__"):
            setattr(AudioRateController, name, _with_head_start(method))
    sendAudioHandle.PRE_BUFFER_COUNT = HEAD_START_MS // FRAME_MS
    logger.bind(tag=TAG).info(f"Sparky patch: audio head start {HEAD_START_MS} ms (reply start and after pauses)")
