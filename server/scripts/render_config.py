#!/usr/bin/env python3
"""Render Sparky's xiaozhi-server overlay config from template + .env + persona.

Usage:
    render_config.py --env server/.env --out /opt/sparky/xiaozhi-server/data

Writes:
    <out>/.config.yaml              (merged over upstream config.yaml by the server)
    <out>/sparky-base-prompt.txt    (system prompt template referenced by the config)

Stdlib only, so it runs before any venv exists.
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

REQUIRED = [
    "SERVER_LAN_IP",
    "GROQ_API_KEY",
    "GROQ_LLM_MODEL",
    "LLM_MODULE",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
]


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            sys.exit(f"{path}:{lineno}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else "" for line in text.strip().splitlines())


def render(env: dict[str, str]) -> str:
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        sys.exit(f"Missing values in .env: {', '.join(missing)}")
    if env["GROQ_API_KEY"].endswith("replace_me"):
        sys.exit("Set a real GROQ_API_KEY in server/.env")
    if env["LLM_MODULE"] not in {"GroqLLM", "MacOllamaLLM"}:
        sys.exit("LLM_MODULE must be GroqLLM or MacOllamaLLM")

    values = dict(env)
    values["PERSONA_IDENTITY"] = indent(IDENTITY.read_text(), 2)

    def sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            sys.exit(f"Template placeholder {{{{{key}}}}} has no value")
        return values[key]

    out = re.sub(r"\{\{([A-Z_]+)\}\}", sub, TEMPLATE.read_text())
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", out)
    if leftover:
        sys.exit(f"Unrendered placeholders: {leftover}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=REPO / "server" / ".env")
    parser.add_argument("--out", type=Path, required=True, help="xiaozhi-server data/ directory")
    args = parser.parse_args()

    if not args.env.exists():
        sys.exit(f"{args.env} not found. Copy server/.env.example to server/.env first.")

    args.out.mkdir(parents=True, exist_ok=True)
    config_path = args.out / ".config.yaml"
    config_path.write_text(render(load_env(args.env)))
    config_path.chmod(0o600)  # contains the API key
    (args.out / "sparky-base-prompt.txt").write_text(BASE_PROMPT.read_text())
    print(f"Wrote {config_path} and {args.out / 'sparky-base-prompt.txt'}")


if __name__ == "__main__":
    main()
