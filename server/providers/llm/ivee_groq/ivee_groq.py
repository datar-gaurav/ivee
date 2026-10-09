"""Groq LLM tuned for an English-speaking child on Groq's free tier.

Changes over upstream's `openai` LLM, which the installers copy into
upstream's core/providers/llm/ and the config selects with `type: ivee_groq`:

1. `reasoning_effort`. gpt-oss is a reasoning model and upstream can't pass
   this, so at default effort it spends max_tokens thinking and returns empty
   replies. "low" keeps replies short and reliable.
2. English few-shot examples. Upstream injects Chinese example turns into
   every session (core/connection.py, "few-shot 示例"), and the model copies
   their language: Speel answered in Chinese, which TTS reads as
   "Chinese letter". We translate those lines before sending.
3. Fewer tokens per reply (the free tier's 200K tokens/day is the real cap):
   only the tools Speel needs, with short English descriptions (upstream
   sends ~815 tokens of tools, including a Chinese lunar calendar and a
   Wi-Fi reset a child could trigger), and only the last
   `max_history_turns` exchanges (upstream keeps the whole session).
4. `fallback_model`. Groq rate-limits each model separately, so when the
   main model returns 429 we answer with the fallback and stay on it until
   the 429's retry-after passes.
5. Sleepy when out of quota. If every model is rate-limited, Speel shows the
   sleepy face, says SLEEPY_LINE and ends the session (via the exit tool, like
   "bye"), without calling Groq, until a retry-after passes.
6. Goodbyes from GOODBYES. Bye phrases (BYE) and the silence-timeout marker
   (GOODBYE_MARKER, the config's end_prompt) get a random one with no Groq
   call; when the LLM itself calls the exit tool, its goodbye is replaced by
   one too. Upstream's exit_commands is empty so bye phrases reach us.
7. Local model first. If `local_model` is set (Ollama on a computer nearby, via its
   OpenAI-compatible /v1 API), it answers while reachable. If it's off or
   asleep, the 1 s connect timeout fails over to Groq and the local model is
   skipped for LOCAL_RETRY seconds before it's tried again.
8. An extra provider before Groq. If `extra_model` and `extra_base_url` are set
   (any OpenAI-compatible API, e.g. NVIDIA NIM or OpenRouter), it answers after
   the local model and before Groq. On a rate limit, an outage or a rejected
   request it's skipped (for the retry-after, else EXTRA_RETRY seconds) and
   Groq answers.

Order: local model, extra provider, Groq `model_name`, Groq `fallback_model`.
"""
import copy
import json
import random
import re
import time

import httpx
import openai
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall, ChoiceDeltaToolCallFunction

from config.logger import setup_logging
from core.providers import ivee_patches
from core.providers.llm.openai.openai import LLMProvider as OpenAILLMProvider

TAG = __name__
logger = setup_logging()
ivee_patches.apply()  # runtime fixes to upstream; see core/providers/ivee_patches.py

# Upstream's hardcoded Chinese strings (pinned IVEE_SERVER_REF) -> English.
FEW_SHOT_ENGLISH = {
    "给我讲个故事吧": "Tell me a story!",
    "好呀，你想听什么类型的呀？童话、冒险还是搞笑的？选一个我给你开讲~":
        "🙂 Okay! What kind of story? A fairy tale, an adventure or a funny one?",
    "已直接回复": "Replied directly.",
    "拜拜": "Bye bye!",
    "再见，下次再聊~": "Bye bye! Talk to you soon!",
    "退出意图已处理": "Exit handled.",
    # Sent as the child's first message when the wake word fires
    # (core/handle/textHandler/listenMessageHandler.py).
    "嘿，你好呀": "Hi Speel!",
}
CJK = re.compile(r"[㐀-鿿]")

# The only tools Speel gets, with short English descriptions. Anything else
# upstream or the device offers is dropped.
SPEEL_TOOLS = {
    "direct_answer": "Reply to the child. Put the full spoken reply in `response`.",
    "handle_exit_intent": "End the conversation when the child says bye. Put a short warm English goodbye in `say_goodbye`.",
    "self_audio_speaker_set_volume": "Set the speaker volume (0-100) when the child asks for louder or quieter.",
}
SPEEL_PARAM_DESCRIPTIONS = {
    "response": "The full spoken reply, in simple English.",
    "say_goodbye": "A short, warm goodbye in simple English.",
}
SLEEPY_LINE = "I am sooo sleepy! Let's catch up later."
GOODBYES = [
    "Bye bye! That was so much fun. See you soon!",
    "See you later, alligator! In a while, crocodile!",
    "Bye! Give your family a big hug from me!",
    "Goodbye, my friend! I'll be right here when you want to play again.",
    "Bye bye! Keep being awesome!",
    "See you soon! Tell your family one fun thing we talked about!",
    "Bye! I had the best time playing with you!",
    "Toodle-oo! You're the best!",
    "Bye bye! I'll be dreaming about dinosaurs until next time!",
    "Catch you later! Wave bye-bye to me!",
]
# The whole utterance is a goodbye: "bye", "Bye bye, Speel!", "good night", "stop".
# Speech recognition spells the name several ways (see persona/identity.md).
BYE = re.compile(
    r"^(ok(ay)? )?(bye( bye)?|bye-bye|goodbye|good ?night|night night|see you( later| soon)?|stop)"
    r"( (speel|spiel|speal|spill|steel))?$")
GOODBYE_MARKER = "[[speel:goodbye]]"  # end_prompt in config.template.yaml
DEFAULT_RETRY_AFTER = 60  # seconds, when a 429 has no retry-after header
MAX_RETRY_AFTER = 3600
LOCAL_RETRY = 60  # seconds to skip the local model after it failed
EXTRA_RETRY = 60  # seconds to skip the extra provider after an error without retry-after
LOCAL_DOWN = (openai.APIConnectionError, openai.APITimeoutError, openai.NotFoundError,
              openai.InternalServerError, openai.RateLimitError)
# The extra provider is optional, so any API failure falls through to Groq.
EXTRA_DOWN = LOCAL_DOWN + (openai.APIStatusError,)


def _english(text):
    if not isinstance(text, str):
        return text
    for chinese, english in FEW_SHOT_ENGLISH.items():
        text = text.replace(chinese, english)
    return text


def _englishify(dialogue):
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


def _is_few_shot(msg):
    ids = [c.get("id", "") for c in msg.get("tool_calls") or []] + [msg.get("tool_call_id") or ""]
    return any(i.startswith("fewshot_") for i in ids)


def _trim_history(dialogue, max_turns):
    """Keep system messages, upstream's few-shot block and the last `max_turns`
    exchanges. Cuts only at a user message, so tool calls stay paired."""
    if not max_turns:
        return dialogue
    head_end = 0
    while head_end < len(dialogue) and dialogue[head_end].get("role") == "system":
        head_end += 1
    last_few_shot = max((i for i, m in enumerate(dialogue) if _is_few_shot(m)), default=-1)
    if last_few_shot >= head_end:
        head_end = last_few_shot + 1
        # Upstream ends the block with a plain assistant goodbye.
        if head_end < len(dialogue) and dialogue[head_end].get("role") == "assistant" \
                and not dialogue[head_end].get("tool_calls"):
            head_end += 1
    rest = dialogue[head_end:]
    user_turns = [i for i, m in enumerate(rest) if m.get("role") == "user"]
    if len(user_turns) <= max_turns:
        return dialogue
    return dialogue[:head_end] + rest[user_turns[-max_turns]:]


def _speel_tools(functions):
    tools = []
    for tool in functions or []:
        fn = tool.get("function", {})
        if fn.get("name") not in SPEEL_TOOLS:
            continue
        tool = copy.deepcopy(tool)
        fn = tool["function"]
        fn["description"] = SPEEL_TOOLS[fn["name"]]
        for name, prop in (fn.get("parameters", {}).get("properties") or {}).items():
            if name in SPEEL_PARAM_DESCRIPTIONS:
                prop["description"] = SPEEL_PARAM_DESCRIPTIONS[name]
        tools.append(tool)
    # An empty tools list is rejected by the API; keep upstream's if nothing matched.
    return tools or functions


def _retry_after(error):
    try:
        seconds = float(error.response.headers.get("retry-after"))
    except (AttributeError, TypeError, ValueError):
        seconds = DEFAULT_RETRY_AFTER
    return min(max(seconds, 1), MAX_RETRY_AFTER)


class LLMProvider(OpenAILLMProvider):
    def __init__(self, config):
        super().__init__(config)
        self.reasoning_effort = config.get("reasoning_effort", "low")
        self.max_history_turns = int(config.get("max_history_turns") or 0)
        self.fallback = None
        self.local = None
        self.extra = None
        self.is_local = bool(config.get("is_local"))
        self.is_extra = bool(config.get("is_extra"))
        self.blocked_until = 0.0  # skip this model until then (monotonic time)
        fallback_model = config.get("fallback_model")
        if fallback_model and fallback_model != self.model_name:
            self.fallback = LLMProvider({**config, "model_name": fallback_model, "fallback_model": None,
                                         "local_model": None, "extra_model": None})
            # Switch to the fallback at once instead of the client's retry backoff.
            self.client = self.client.with_options(max_retries=0)
        local_model = config.get("local_model")
        if local_model and str(local_model).lower() != "none" and config.get("local_base_url"):
            self.local = LLMProvider({**config, "model_name": local_model, "base_url": config["local_base_url"],
                                      "api_key": "ollama", "reasoning_effort": None, "is_local": True,
                                      "fallback_model": None, "local_model": None, "extra_model": None})
            self.local.client = self.local.client.with_options(
                max_retries=0, timeout=httpx.Timeout(30.0, connect=1.0))
        extra_model = config.get("extra_model")
        if extra_model and str(extra_model).lower() != "none" and config.get("extra_base_url"):
            self.extra = LLMProvider({
                **config, "model_name": extra_model, "base_url": config["extra_base_url"],
                "api_key": config.get("extra_api_key") or "none", "is_extra": True,
                "reasoning_effort": None, "fallback_model": None, "local_model": None, "extra_model": None})
            self.extra.client = self.extra.client.with_options(
                max_retries=0, timeout=httpx.Timeout(30.0, connect=3.0))

    # Upstream calls this on every request (streaming and function calling).
    def _apply_thinking_disabled(self, request_params: dict):
        super()._apply_thinking_disabled(request_params)
        if self.reasoning_effort:
            request_params.setdefault("extra_body", {})["reasoning_effort"] = self.reasoning_effort

    def _prepare(self, dialogue):
        return _englishify(_trim_history(copy.deepcopy(dialogue), self.max_history_turns))

    def _stream(self, method, *args, **kwargs):
        """Stream from the first available model: local (if set), main, fallback.
        Returns False (with nothing yielded) when none can answer."""
        chain = [m for m in (self.local, self.extra, self, self.fallback) if m]
        for llm in chain:
            if time.monotonic() < llm.blocked_until:
                continue
            started = False
            errors = LOCAL_DOWN if llm.is_local else EXTRA_DOWN if llm.is_extra else openai.RateLimitError
            try:
                for item in method(llm, *args, **kwargs):
                    started = True
                    yield item
                return True
            except errors as e:
                if started:
                    raise
                if llm.is_local:
                    wait = LOCAL_RETRY
                elif isinstance(e, openai.RateLimitError):
                    wait = _retry_after(e)
                else:
                    wait = EXTRA_RETRY
                llm.blocked_until = time.monotonic() + wait
                logger.bind(tag=TAG).warning(
                    f"{llm.model_name} unavailable ({type(e).__name__}: {str(e)[:120]}); skipping it for {wait:.0f}s")
        logger.bind(tag=TAG).warning("No model can answer (local down, clouds rate-limited); Speel is sleepy")
        return False

    def response(self, session_id, dialogue, **kwargs):
        if _wants_goodbye(dialogue):
            yield f"{ivee_patches.GOODBYE_EMOJI} {random.choice(GOODBYES)}"
            return
        if not (yield from self._stream(OpenAILLMProvider.response, session_id, self._prepare(dialogue), **kwargs)):
            yield f"😴 {SLEEPY_LINE}"

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        tools = _speel_tools(functions)
        if _wants_goodbye(dialogue):
            yield from _say_and_exit(ivee_patches.GOODBYE_EMOJI, random.choice(GOODBYES), tools)
            return
        stream = self._stream(OpenAILLMProvider.response_with_functions, session_id,
                              self._prepare(dialogue), tools, **kwargs)
        if not (yield from _with_our_goodbyes(stream)):
            yield from _say_and_exit("😴", SLEEPY_LINE, tools)


def _last_user_text(dialogue):
    """The child's latest words, or None if the last message isn't theirs
    (e.g. a follow-up call after a tool result)."""
    if not dialogue or dialogue[-1].get("role") != "user":
        return None
    text = str(dialogue[-1].get("content") or "")
    if text.strip().startswith("{"):  # upstream wraps it as JSON when it knows the speaker
        try:
            text = str(json.loads(text).get("content", text))
        except (ValueError, AttributeError):
            pass
    return text


def _wants_goodbye(dialogue):
    text = _last_user_text(dialogue)
    if text is None:
        return False
    if text.strip() == GOODBYE_MARKER:
        return True
    words = re.sub(r"[^a-z ]+", " ", text.lower().replace("-", " "))
    return bool(BYE.match(" ".join(words.split())))


def _exit_call(goodbye, index=0, name=True):
    """An exit-tool call delta. name=False continues a call the model started."""
    function = ChoiceDeltaToolCallFunction(
        name="handle_exit_intent" if name else None, arguments=json.dumps({"say_goodbye": goodbye}))
    return ChoiceDeltaToolCall(index=index, id="speel_exit" if name else None, type="function", function=function)


def _say_and_exit(emoji, line, tools):
    """Speak `line` with the face for `emoji`, then end the session through the
    exit tool, like "bye". Upstream skips re-speaking text already streamed."""
    yield f"{emoji} {line}", None
    if any(t.get("function", {}).get("name") == "handle_exit_intent" for t in tools or []):
        yield None, [_exit_call(line)]


def _with_our_goodbyes(stream):
    """Pass the stream through, but replace the arguments of any exit-tool call
    with one of our goodbyes. Returns the stream's return value."""
    exit_indexes = set()
    result = None
    try:
        while True:
            content, tool_calls = next(stream)
            if tool_calls:
                kept = []
                for call in tool_calls:
                    if call.function and call.function.name == "handle_exit_intent":
                        exit_indexes.add(call.index)
                    if call.index in exit_indexes and call.function:
                        call = call.model_copy(deep=True)
                        call.function.arguments = ""
                    kept.append(call)
                tool_calls = kept
            yield content, tool_calls
    except StopIteration as stop:
        result = stop.value
    for index in exit_indexes:
        yield None, [_exit_call(random.choice(GOODBYES), index=index, name=False)]
    return result

