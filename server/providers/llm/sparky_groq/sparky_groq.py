"""Groq LLM tuned for an English-speaking child.

Two fixes over upstream's `openai` LLM, which the installers copy into
upstream's core/providers/llm/ and the config selects with `type: sparky_groq`:

1. `reasoning_effort`. gpt-oss is a reasoning model and upstream can't pass
   this, so at default effort it spends max_tokens thinking and returns empty
   replies. "low" keeps replies short and reliable.
2. English few-shot examples. Upstream injects Chinese example turns into
   every session (core/connection.py, "few-shot 示例"), and the model copies
   their language: Speel answered in Chinese, which Piper reads as
   "Chinese letter". We translate those lines before sending.
"""
import copy
import re

from config.logger import setup_logging
from core.providers.llm.openai.openai import LLMProvider as OpenAILLMProvider

TAG = __name__
logger = setup_logging()

# Upstream's few-shot strings (pinned XIAOZHI_SERVER_REF) -> English.
FEW_SHOT_ENGLISH = {
    "给我讲个故事吧": "Tell me a story!",
    "好呀，你想听什么类型的呀？童话、冒险还是搞笑的？选一个我给你开讲~":
        "🙂 Okay! What kind of story? A fairy tale, an adventure or a funny one?",
    "已直接回复": "Replied directly.",
    "拜拜": "Bye bye!",
    "再见，下次再聊~": "Bye bye! Talk to you soon!",
    "退出意图已处理": "Exit handled.",
}
CJK = re.compile(r"[㐀-鿿]")


def _english(text):
    if not isinstance(text, str):
        return text
    for chinese, english in FEW_SHOT_ENGLISH.items():
        text = text.replace(chinese, english)
    return text


def _englishify(dialogue):
    dialogue = copy.deepcopy(dialogue)
    for msg in dialogue:
        if msg.get("role") == "system":
            continue
        msg["content"] = _english(msg.get("content"))
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", {})
            fn["arguments"] = _english(fn.get("arguments"))
        if CJK.search(str(msg.get("content") or "") + str(msg.get("tool_calls") or "")):
            # Upstream changed its examples, or a tool returned Chinese.
            logger.bind(tag=TAG).warning(f"Chinese text still sent to the LLM: {str(msg)[:120]}")
    return dialogue


class LLMProvider(OpenAILLMProvider):
    def __init__(self, config):
        super().__init__(config)
        self.reasoning_effort = config.get("reasoning_effort", "low")

    # Upstream calls this on every request (streaming and function calling).
    def _apply_thinking_disabled(self, request_params: dict):
        super()._apply_thinking_disabled(request_params)
        if self.reasoning_effort:
            request_params.setdefault("extra_body", {})["reasoning_effort"] = self.reasoning_effort

    def response(self, session_id, dialogue, **kwargs):
        return super().response(session_id, _englishify(dialogue), **kwargs)

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        return super().response_with_functions(session_id, _englishify(dialogue), functions, **kwargs)
