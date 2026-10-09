#!/usr/bin/env python3
"""Ivee's config page: edit ivee.env and the character from a browser.

    ivee_config.py serve --env /opt/ivee/config/ivee.env [--port 8088] [--apply systemd|manual]
    ivee_config.py set-password --env /opt/ivee/config/ivee.env   (reads the password from stdin)

Serves a small password-protected page on the LAN. Saving writes ivee.env and
identity.md (next to it) and, with --apply systemd, asks ivee-apply.path to
re-render the server config and restart Ivee's services. With --apply manual
(run-mac.sh), restarting is left to the person.

The API key is never sent back to the browser. Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.cookies
import json
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import render_config as rc  # noqa: E402

STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/app.css": ("app.css", "text/css")}
# The settings the page edits, in ivee.env order. GROQ_API_KEY is write-only.
FIELDS = ["GROQ_API_KEY", "SERVER_LAN_IP", "TIMEZONE_OFFSET", "GROQ_LLM_MODEL", "GROQ_FALLBACK_MODEL",
          "EXTRA_LLM_BASE_URL", "EXTRA_LLM_MODEL", "EXTRA_LLM_API_KEY",
          "OLLAMA_BASE_URL", "OLLAMA_MODEL", "LLM_MODULE", "TTS_VOICE", "TTS_SPEED", "TTS_PITCH",
          "TTS_BOOST", "TTS_HIGHPASS", "TTS_PRESENCE", "SERVER_WS_PORT", "SERVER_HTTP_PORT", "TTS_PORT",
          "BACKUP_TARGET"]
SECRETS = ("GROQ_API_KEY", "EXTRA_LLM_API_KEY")  # write-only: the page sees only whether they're set
EXTRA = ("EXTRA_LLM_BASE_URL", "EXTRA_LLM_MODEL", "EXTRA_LLM_API_KEY")
SESSION_SECONDS = 12 * 3600
PBKDF2_ROUNDS = 300_000
MAX_BODY = 64 * 1024
MAX_IDENTITY = 8000
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"


# --- settings files -------------------------------------------------------

def update_env(path: Path, updates: dict[str, str]) -> None:
    """Set KEY=value for each update, keeping the file's comments and order."""
    lines = path.read_text().splitlines() if path.exists() else []
    left = dict(updates)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if not line.lstrip().startswith("#") and "=" in line and key in left:
            lines[i] = f"{key}={left.pop(key)}"
    lines += [f"{key}={value}" for key, value in left.items()]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)


def lan_ip() -> str:
    """This machine's address on the LAN (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return ""


# --- password and sessions ------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.strip().split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
    except ValueError:
        return False
    return hmac.compare_digest(got.hex(), digest)


class Sessions:
    def __init__(self):
        self.tokens: dict[str, float] = {}
        self.failures: list[float] = []
        self.lock = threading.Lock()

    def new(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.tokens[token] = time.time() + SESSION_SECONDS
        return token

    def valid(self, token: str | None) -> bool:
        with self.lock:
            return bool(token) and self.tokens.get(token, 0) > time.time()

    def end(self, token: str | None) -> None:
        with self.lock:
            self.tokens.pop(token or "", None)

    def locked_out(self) -> bool:
        """More than 10 wrong passwords in the last 5 minutes."""
        with self.lock:
            now = time.time()
            self.failures = [t for t in self.failures if t > now - 300]
            return len(self.failures) >= 10

    def failed(self) -> None:
        with self.lock:
            self.failures.append(time.time())


# --- the app ----------------------------------------------------------------

class App:
    def __init__(self, env_path: Path, apply_mode: str):
        self.env_path = env_path
        self.identity_path = env_path.parent / "identity.md"
        self.password_path = env_path.parent / "ui-password"
        self.request_path = env_path.parent / "apply.request"
        self.status_path = env_path.parent / "apply.status"
        self.apply_mode = apply_mode
        self.sessions = Sessions()

    def env(self) -> dict[str, str]:
        return rc.load_env(self.env_path) if self.env_path.exists() else {}

    def identity(self) -> str:
        return rc.identity_path(self.env_path).read_text()

    def state(self) -> dict:
        env = self.env()
        values = rc.settings(env)
        keys = {}
        for name in SECRETS:
            key = env.get(name, "")
            is_set = bool(key) and key != rc.PLACEHOLDER_KEY
            keys[name] = {"set": is_set, "hint": f"…{key[-4:]}" if is_set else ""}
        return {
            "values": {k: values.get(k, "") for k in FIELDS if k not in SECRETS},
            "keys": keys,
            "identity": self.identity(),
            "default_identity": rc.IDENTITY.read_text(),
            "problems": rc.problems(env),
            "detected_ip": lan_ip(),
            "ota_url": f"http://{values.get('SERVER_LAN_IP', '')}:{values['SERVER_HTTP_PORT']}/xiaozhi/ota/",
            "server_ref": values.get("IVEE_SERVER_REF", "")[:12],
            "apply_mode": self.apply_mode,
            "apply_status": self.apply_status(),
        }

    def apply_status(self) -> dict | None:
        try:
            return json.loads(self.status_path.read_text())
        except (OSError, ValueError):
            return None

    def save(self, body: dict) -> dict:
        submitted = body.get("values") or {}
        if not isinstance(submitted, dict):
            raise rc.ConfigError("values must be an object")
        updates = {}
        for key in FIELDS:
            if key not in submitted:
                continue
            value = str(submitted[key]).strip()
            if key in SECRETS and not value:
                continue  # blank means keep the saved key
            updates[key] = value
        if body.get("remove_extra"):
            updates.update({key: "" for key in EXTRA})
        env = {**self.env(), **updates}
        found = rc.problems(env)
        if found:
            return {"ok": False, "problems": found}

        identity = body.get("identity")
        if identity is not None:
            identity = str(identity).replace("\r\n", "\n").strip()
            if not identity:
                raise rc.ConfigError("The character description can't be empty")
            if len(identity) > MAX_IDENTITY:
                raise rc.ConfigError(f"The character description is over {MAX_IDENTITY} characters")
            rc.render(env, identity)  # catches anything the template can't take

        update_env(self.env_path, updates)
        if identity is not None:
            if identity == rc.IDENTITY.read_text().strip():
                self.identity_path.unlink(missing_ok=True)  # back to the default
            else:
                self.identity_path.write_text(identity + "\n")
        return {"ok": True, "apply_request": self.request_apply()}

    def request_apply(self) -> str | None:
        """Ask ivee-apply.path to apply; ivee-apply writes this id back into apply.status."""
        if self.apply_mode != "systemd":
            return None
        request = secrets.token_hex(8)
        self.request_path.write_text(request + "\n")
        return request

    def test_groq(self, body: dict) -> dict:
        key = str(body.get("api_key") or "").strip() or self.env().get("GROQ_API_KEY", "")
        if not key or key == rc.PLACEHOLDER_KEY:
            return {"ok": False, "message": "No API key to test."}
        request = urllib.request.Request(GROQ_MODELS_URL, headers={
            "Authorization": f"Bearer {key}", "User-Agent": "ivee-config"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                models = {m["id"] for m in json.load(response).get("data", [])}
        except urllib.error.HTTPError as e:
            return {"ok": False, "message": "Groq rejected this key." if e.code == 401 else f"Groq answered {e.code}."}
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {"ok": False, "message": f"Couldn't reach Groq: {e}"}
        values = rc.settings({**self.env(), **self.submitted(body)})
        missing = [m for m in (values["GROQ_LLM_MODEL"], values["GROQ_FALLBACK_MODEL"], "whisper-large-v3")
                   if m not in models]
        if missing:
            return {"ok": False, "message": f"The key works, but Groq doesn't offer: {', '.join(missing)}."}
        return {"ok": True, "message": "The key works and the models are available."}

    def submitted(self, body: dict) -> dict:
        values = body.get("values") or {}
        return {k: str(v).strip() for k, v in values.items() if k in FIELDS and str(v).strip()}

    def test_extra(self, body: dict) -> dict:
        """Ask the extra provider for one short reply with the page's (or saved) settings."""
        values = rc.settings({**self.env(), **self.submitted(body)})
        found = [p for p in rc.problems({**values, "GROQ_API_KEY": "x"}) if p.startswith("EXTRA_LLM")]
        if found or not values["EXTRA_LLM_BASE_URL"]:
            return {"ok": False, "message": " ".join(found) or "Set a base URL and a model first."}
        request = urllib.request.Request(
            values["EXTRA_LLM_BASE_URL"].rstrip("/") + "/chat/completions",
            data=json.dumps({"model": values["EXTRA_LLM_MODEL"], "max_tokens": 20,
                             "messages": [{"role": "user", "content": "Say hi in three words."}]}).encode(),
            headers={"Authorization": f"Bearer {values['EXTRA_LLM_API_KEY'] or 'none'}",
                     "Content-Type": "application/json", "User-Agent": "ivee-config"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                json.load(response)["choices"][0]
        except urllib.error.HTTPError as e:
            detail = e.read(300).decode(errors="replace")
            return {"ok": False, "message": f"The provider answered {e.code}: {detail}"}
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as e:
            return {"ok": False, "message": f"Couldn't get a reply: {e}"}
        return {"ok": True, "message": "It answered. Speel will use it before Groq."}

    def preview(self, body: dict) -> bytes:
        values = rc.settings(self.env())
        text = str(body.get("text") or "Hi! I'm so happy to talk with you.")[:300]
        payload = {"text": text}
        for key, name in (("TTS_VOICE", "voice"), ("TTS_SPEED", "speed"), ("TTS_PITCH", "pitch"),
                          ("TTS_BOOST", "boost"), ("TTS_HIGHPASS", "highpass"), ("TTS_PRESENCE", "presence")):
            value = (body.get("values") or {}).get(key) or values[key]
            payload[name] = value if name == "voice" else float(value)
        request = urllib.request.Request(
            f"http://127.0.0.1:{values['TTS_PORT']}/synthesize", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()

    def health(self) -> dict:
        values = rc.settings(self.env())
        checks = {"server": f"http://127.0.0.1:{values['SERVER_HTTP_PORT']}/xiaozhi/ota/",
                  "voice": f"http://127.0.0.1:{values['TTS_PORT']}/"}
        result = {}
        for name, url in checks.items():
            try:
                urllib.request.urlopen(url, timeout=2).close()
                result[name] = True
            except urllib.error.HTTPError:
                result[name] = True  # it answered
            except (urllib.error.URLError, OSError):
                result[name] = False
        return result


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ivee-config"

        def log_message(self, fmt, *args):
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

        # helpers
        def token(self) -> str | None:
            cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return cookie["ivee_session"].value if "ivee_session" in cookie else None

        def send(self, code: int, body: bytes, content_type: str, headers: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; media-src 'self' blob:; frame-ancestors 'none'")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def json(self, code: int, data, headers: dict | None = None):
            self.send(code, json.dumps(data).encode(), "application/json", headers)

        def body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise rc.ConfigError("Request too large")
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise rc.ConfigError("Expected a JSON object")
            return data

        def same_origin(self) -> bool:
            """API calls must come from this page's script (custom header forces a
            CORS preflight we never answer) and, if the browser says, this origin."""
            if self.headers.get("X-Ivee") != "1":
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin.split("://", 1)[-1] == self.headers.get("Host")

        # routes
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in STATIC:
                name, content_type = STATIC[path]
                self.send(200, (HERE / "static" / name).read_bytes(), f"{content_type}; charset=utf-8")
            elif path == "/api/state":
                if not app.sessions.valid(self.token()):
                    self.json(401, {"login": True, "has_password": app.password_path.exists()})
                else:
                    self.json(200, app.state())
            elif path == "/api/health":
                if not app.sessions.valid(self.token()):
                    self.json(401, {"login": True})
                else:
                    self.json(200, {"health": app.health(), "apply_status": app.apply_status()})
            else:
                self.send(404, b"Not found", "text/plain")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if not self.same_origin():
                self.json(403, {"message": "Cross-site request refused"})
                return
            try:
                body = self.body()
                if path == "/api/login":
                    self.login(body)
                    return
                if path == "/api/logout":
                    app.sessions.end(self.token())
                    self.json(200, {"ok": True}, {"Set-Cookie": "ivee_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"})
                    return
                if not app.sessions.valid(self.token()):
                    self.json(401, {"login": True})
                    return
                if path == "/api/save":
                    self.json(200, app.save(body))
                elif path == "/api/test-groq":
                    self.json(200, app.test_groq(body))
                elif path == "/api/test-extra":
                    self.json(200, app.test_extra(body))
                elif path == "/api/preview":
                    self.send(200, app.preview(body), "audio/wav")
                else:
                    self.json(404, {"message": "Not found"})
            except rc.ConfigError as e:
                self.json(400, {"ok": False, "problems": [str(e)]})
            except urllib.error.HTTPError as e:
                self.json(400, {"ok": False, "message": f"The voice service refused: {e.reason}"})
            except (urllib.error.URLError, OSError) as e:
                self.json(502, {"ok": False, "message": f"The voice service didn't answer: {e}"})
            except ValueError:
                self.json(400, {"ok": False, "problems": ["Bad request"]})

        def login(self, body: dict):
            if not app.password_path.exists():
                self.json(403, {"message": "No password is set. Run the installer, or: "
                                           "ivee_config.py set-password --env <ivee.env>"})
                return
            if app.sessions.locked_out():
                self.json(429, {"message": "Too many wrong passwords. Wait a few minutes."})
                return
            if not check_password(str(body.get("password", "")), app.password_path.read_text()):
                app.sessions.failed()
                time.sleep(1)
                self.json(401, {"message": "Wrong password."})
                return
            token = app.sessions.new()
            self.json(200, {"ok": True}, {"Set-Cookie": f"ivee_session={token}; Max-Age={SESSION_SECONDS}; "
                                                        "Path=/; HttpOnly; SameSite=Strict"})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the config page")
    serve.add_argument("--env", type=Path, required=True)
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8088)
    serve.add_argument("--apply", choices=["systemd", "manual"], default="manual")
    setpw = sub.add_parser("set-password", help="set the page's password (read from stdin)")
    setpw.add_argument("--env", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "set-password":
        password = sys.stdin.readline().rstrip("\n")
        if len(password) < 8:
            sys.exit("Use at least 8 characters.")
        target = args.env.parent / "ui-password"
        target.write_text(hash_password(password) + "\n")
        target.chmod(0o600)
        print(f"Password saved to {target}")
        return

    app = App(args.env, args.apply)
    print(f"Ivee config page on http://{lan_ip() or args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), make_handler(app)).serve_forever()


if __name__ == "__main__":
    main()
