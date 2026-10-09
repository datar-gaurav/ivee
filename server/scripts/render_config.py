#!/usr/bin/env python3
"""Render Ivee's overlay config for the upstream server from ivee.env + persona.

Usage:
    render_config.py --env /opt/ivee/config/ivee.env --out <upstream server>/data

Writes:
    <out>/.config.yaml            (merged over upstream config.yaml by the server)
    <out>/ivee-base-prompt.txt    (system prompt template referenced by the config)

The character's identity comes from identity.md next to the env file if there is
one (the config page writes it there), else persona/identity.md.
The safety rules (persona/base-prompt-template.txt) always come from the repo.

Stdlib only, so it runs before any venv exists. The config page imports it too.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "server" / "config" / "config.template.yaml"
IDENTITY = REPO / "persona" / "identity.md"
BASE_PROMPT = REPO / "persona" / "base-prompt-template.txt"

# Everything but the API key and the LAN IP has a default. The device learns the
# WebSocket port from OTA; SERVER_HTTP_PORT is part of the OTA URL the device
# stores, so changing it means re-entering that URL on the device.
DEFAULTS = {
    "SERVER_WS_PORT": "8000",
    "SERVER_HTTP_PORT": "8003",
    "TTS_PORT": "5000",
    "TIMEZONE_OFFSET": "0",
    "GROQ_LLM_MODEL": "openai/gpt-oss-20b",
    "GROQ_FALLBACK_MODEL": "openai/gpt-oss-120b",
    "LLM_MODULE": "GroqLLM",
    "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
    "OLLAMA_MODEL": "none",
    "EXTRA_LLM_BASE_URL": "",
    "EXTRA_LLM_MODEL": "",
    "EXTRA_LLM_API_KEY": "",
    "TTS_VOICE": "af_heart",
    "TTS_SPEED": "0.9",
    "TTS_PITCH": "1.12",
    "TTS_BOOST": "2.0",
    "TTS_HIGHPASS": "300",
    "TTS_PRESENCE": "5",
}
PORTS = ["SERVER_WS_PORT", "SERVER_HTTP_PORT", "TTS_PORT"]
REQUIRED = ["SERVER_LAN_IP", "GROQ_API_KEY"]
LLM_MODULES = {"GroqLLM", "OllamaLLM"}
PLACEHOLDER_KEY = "gsk_replace_me"
# Same limits as server/tts/speel_tts.py OVERRIDES.
TTS_RANGES = {"TTS_SPEED": (0.5, 1.5), "TTS_PITCH": (0.8, 1.5), "TTS_BOOST": (1.0, 5.0),
              "TTS_HIGHPASS": (0.0, 1000.0), "TTS_PRESENCE": (0.0, 12.0)}


class ConfigError(ValueError):
    pass


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"{path}:{lineno}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def settings(env: dict[str, str]) -> dict[str, str]:
    """`env` over DEFAULTS; empty values fall back to the default."""
    return {**DEFAULTS, **{k: v for k, v in env.items() if v}}


def identity_path(env_path: Path) -> Path:
    custom = env_path.parent / "identity.md"
    return custom if custom.exists() else IDENTITY


def problems(env: dict[str, str]) -> list[str]:
    """Everything wrong with `env`, as messages for a person. Empty means it renders."""
    values = settings(env)
    found = [f"{key} is not set" for key in REQUIRED if not values.get(key)]
    if values.get("GROQ_API_KEY") == PLACEHOLDER_KEY:
        found.append("GROQ_API_KEY is still the example value")
    if values["LLM_MODULE"] not in LLM_MODULES:
        found.append(f"LLM_MODULE must be one of {', '.join(sorted(LLM_MODULES))}")
    if values["LLM_MODULE"] == "OllamaLLM" and values["OLLAMA_MODEL"].lower() == "none":
        found.append("LLM_MODULE=OllamaLLM needs an OLLAMA_MODEL")
    for key in PORTS:
        if not values[key].isdigit() or not 1 <= int(values[key]) <= 65535:
            found.append(f"{key} must be a port number")
    if not re.fullmatch(r"[+-]?\d{1,2}(\.5|\.75)?", values["TIMEZONE_OFFSET"]):
        found.append("TIMEZONE_OFFSET must be hours from UTC, like -7 or 5.5")
    for key, (low, high) in TTS_RANGES.items():
        try:
            ok = low <= float(values[key]) <= high
        except ValueError:
            ok = False
        if not ok:
            found.append(f"{key} must be a number from {low:g} to {high:g}")
    for key in ("GROQ_LLM_MODEL", "GROQ_FALLBACK_MODEL", "OLLAMA_MODEL"):
        if not re.fullmatch(r"[A-Za-z0-9._:/-]+", values[key]):
            found.append(f"{key} doesn't look like a model name")
    extra_url, extra_model = values["EXTRA_LLM_BASE_URL"], values["EXTRA_LLM_MODEL"]
    if extra_url or extra_model or values["EXTRA_LLM_API_KEY"]:
        if not re.fullmatch(r"https?://[A-Za-z0-9.:/_-]+", extra_url):
            found.append("EXTRA_LLM_BASE_URL must be an http(s) URL, like https://integrate.api.nvidia.com/v1")
        if not re.fullmatch(r"[A-Za-z0-9._:/@-]+", extra_model):
            found.append("EXTRA_LLM_MODEL must be set to a model name")
    if not re.fullmatch(r"[a-z]{2}_[a-z]+", values["TTS_VOICE"]):
        found.append("TTS_VOICE must be a Kokoro voice name, like af_heart")
    for key, value in env.items():
        if any(c in value for c in "\r\n\0"):
            found.append(f"{key} has a line break in it")
    if values.get("SERVER_LAN_IP") and not re.fullmatch(r"[A-Za-z0-9.:-]+", values["SERVER_LAN_IP"]):
        found.append("SERVER_LAN_IP must be an IP address or host name")
    return found


def render(env: dict[str, str], identity: str) -> str:
    found = problems(env)
    if found:
        raise ConfigError("; ".join(found))
    values = settings(env)
    values["PERSONA_IDENTITY"] = indent(identity, 2)
    for key in ("EXTRA_LLM_BASE_URL", "EXTRA_LLM_MODEL", "EXTRA_LLM_API_KEY"):
        values[f"{key}_OR_NONE"] = values[key] or "none"

    def sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ConfigError(f"Template placeholder {{{{{key}}}}} has no value")
        return values[key]

    out = re.sub(r"\{\{([A-Z_]+)\}\}", sub, TEMPLATE.read_text())
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", out)
    if leftover:
        raise ConfigError(f"Unrendered placeholders: {leftover}")
    return out


def indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else "" for line in text.strip().splitlines())


def write(env_path: Path, out: Path) -> Path:
    config = render(load_env(env_path), identity_path(env_path).read_text())
    out.mkdir(parents=True, exist_ok=True)
    config_path = out / ".config.yaml"
    config_path.write_text(config)
    config_path.chmod(0o600)  # contains the API key
    (out / "ivee-base-prompt.txt").write_text(BASE_PROMPT.read_text())
    return config_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", type=Path, required=True, help="ivee.env")
    parser.add_argument("--out", type=Path, required=True, help="the upstream server's data/ directory")
    args = parser.parse_args()
    if not args.env.exists():
        sys.exit(f"{args.env} not found. Run the installer, or copy server/ivee.env.example there.")
    try:
        config_path = write(args.env, args.out)
    except ConfigError as e:
        sys.exit(f"Can't render the config: {e}")
    print(f"Wrote {config_path} and {args.out / 'ivee-base-prompt.txt'}")


if __name__ == "__main__":
    main()
