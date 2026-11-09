"""The web app: a local page where you talk to Alfred, approve its drafts and answer its questions.

    python -m alfred.app --model alfred-4b --base-url ollama          # a model served by Ollama
    python -m alfred.app --model alfred-rl --base-url vllm            # or by vLLM
    python -m alfred.app --model alfred-4b --grant ~/School           # on a real folder, through the permission broker

With a model served on this computer, everything runs here except one optional button: with --cloud and
NEBIUS_API_KEY set, "Polish with Nemotron 3 Super" sends a pending email's subject and body (nothing else) to Token
Factory. A hosted model (--base-url tokenfactory, openai or any other host) gets every request and everything Alfred
reads; the terminal and the page say so.

The page is where drafts get approved, so the API answers only that page. It listens on this computer only, refuses
requests addressed to any other host name (DNS rebinding) or sent from any other page (cross-site requests), and wants
the key from the link the app prints, which no other page or user can read.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..broker import AuditLog, Broker, GrantError
from .folder import FolderSession, check_folder
from .live import Session

STATIC = Path(__file__).parent / "static"
TOKEN_HEADER = "X-Alfred-Token"
MAX_BODY = 1_000_000
SIMULATED_ONLY = ("/api/polish", "/api/memory", "/api/reset")  # the simulated inbox has these; a real folder doesn't
_SCRIPT = re.compile(rb"<script\b[^>]*>(.*?)</script>", re.S)


def page_policy(page: bytes) -> str:
    """The page's Content-Security-Policy: only its own inline script runs (by hash), it talks only to this server,
    and no other site can frame it. Text from emails or files that slipped into the page as markup still couldn't run
    script, send anything anywhere, or be clicked through from inside another site's page."""
    hashes = " ".join("'sha256-" + base64.b64encode(hashlib.sha256(s.replace(b"\r\n", b"\n")).digest()).decode() + "'"
                      for s in _SCRIPT.findall(page))
    return (f"default-src 'none'; script-src {hashes}; style-src 'self' 'unsafe-inline'; font-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def make_handler(app: "App"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, kind: str = "application/json", policy: str | None = None):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            if policy:
                self.send_header("Content-Security-Policy", policy)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            # "replace": a model can write lone surrogates, which UTF-8 can't encode, and the page has to keep working
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8", "replace"))

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _refused(self, api: bool) -> bool:
            """Answers a request that might not come from Alfred's own page with 403, and says so (True)."""
            if self.headers.get("Host") not in app.hosts:
                self._json({"error": "Alfred only answers requests addressed to this computer."}, 403)
                return True
            origin = self.headers.get("Origin")
            if origin is not None and origin not in app.origins:
                self._json({"error": "Alfred only answers its own page."}, 403)
                return True
            if api and not hmac.compare_digest(self.headers.get(TOKEN_HEADER, "").encode(), app.token.encode()):
                self._json({"error": "Open Alfred with the link it printed in your terminal. That link carries the "
                                     "key for this run."}, 403)
                return True
            return False

        def do_GET(self):  # noqa: N802 - http.server API
            url = urlparse(self.path)
            if self._refused(api=url.path.startswith("/api/")):
                return
            if url.path in ("/", "/index.html"):
                page = (STATIC / "index.html").read_bytes()
                return self._send(200, page, "text/html; charset=utf-8", policy=page_policy(page))
            if url.path.startswith("/static/"):
                f = (STATIC / url.path[len("/static/"):]).resolve()
                if STATIC.resolve() in f.parents and f.is_file():
                    kind = {".woff2": "font/woff2", ".css": "text/css", ".js": "text/javascript",
                            ".svg": "image/svg+xml"}.get(f.suffix, "application/octet-stream")
                    return self._send(200, f.read_bytes(), kind)
            if url.path == "/api/world":
                return self._json({**app.session.world_view(), "model_host": app.model_host})
            if url.path == "/api/events":
                since = int(parse_qs(url.query).get("since", ["0"])[0])
                s = app.session
                p = s.pending
                return self._json({"events": s.events_since(since), "running": s.running,
                                   "pending": {"id": p.id, "kind": p.kind} if p else None})
            self._json({"error": "not found"}, 404)

        def do_POST(self):  # noqa: N802 - http.server API
            if self._refused(api=True):
                return
            if self.headers.get_content_type() != "application/json":
                return self._json({"error": "Send JSON."}, 415)
            if int(self.headers.get("Content-Length") or 0) > MAX_BODY:
                return self._json({"error": "That's too long."}, 413)
            path, s = urlparse(self.path).path, app.session
            if app.folder is not None and path in SIMULATED_ONLY:
                return self._json({"error": "That's only for the simulated inbox."}, 409)
            try:
                body = self._body()
                if path == "/api/request":
                    text = (body.get("text") or "").strip()
                    if not text:
                        return self._json({"error": "Say what you'd like Alfred to do."}, 400)
                    s.start(text)
                elif path == "/api/respond":
                    s.respond(body["pending_id"], body.get("response") or {})
                elif path == "/api/polish":
                    return self._json(s.polish(body["pending_id"]))
                elif path == "/api/stop":
                    s.stop()
                elif path == "/api/revoke" and app.folder is not None:
                    s.revoke()
                elif path == "/api/memory":
                    if body.get("clear"):
                        s.memory.clear()
                    if "use" in body:
                        s.use_memory = bool(body["use"])
                elif path == "/api/reset":
                    app.reset(int(body.get("seed", s.seed)))
                else:
                    return self._json({"error": "not found"}, 404)
            except (KeyError, RuntimeError, ValueError) as e:
                return self._json({"error": str(e).strip("'\"")}, 409)
            except Exception as e:  # noqa: BLE001 - report it to the page
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            self._json({"ok": True})

    return Handler


class App:
    def __init__(self, a):
        from openai import OpenAI

        from ..eval import resolve_endpoint
        self.a = a
        self.token = secrets.token_urlsafe(32)
        self.hosts: set[str] = set()
        self.origins: set[str] = set()
        url, key = resolve_endpoint(a.base_url, a.api_key)
        self.client = OpenAI(base_url=url, api_key=key, timeout=300, max_retries=2)
        host = urlparse(url).hostname or ""
        self.model_host = None if is_loopback(host) else (host or url)  # where the model runs, if not on this computer
        tf_key = os.environ.get("NEBIUS_API_KEY")
        self.polish_client = (OpenAI(base_url="https://api.tokenfactory.nebius.com/v1/", api_key=tf_key, timeout=120)
                              if a.cloud and tf_key and not a.no_cloud else None)
        self.memory_path = Path(a.memory).expanduser() if a.memory else None
        self.folder = Path(os.path.abspath(os.path.expanduser(a.grant))) if a.grant else None
        self.broker = Broker(AuditLog(a.audit or None)) if a.grant else None
        if self.folder is not None:
            check_folder(self.folder, self.broker)
        self.reset(a.world)

    def listen_on(self, host: str, port: int) -> None:
        """The host names and page origins the server answers, once the port is known."""
        self.hosts = {f"{h}:{port}" for h in (host, "127.0.0.1", "localhost")}
        self.origins = {f"http://{h}" for h in self.hosts}

    def reset(self, seed: int) -> None:
        if self.folder is not None:
            self.session = FolderSession(self.folder, self.a.model, self.client, self.broker,
                                         temperature=self.a.temperature, no_think=not self.a.thinking,
                                         model_host=self.model_host)
            return
        old = getattr(self, "session", None)
        if old and old.running:
            old.stop()
        self.session = Session(seed, self.a.model, self.client, tool_mode=self.a.tool_mode,
                               temperature=self.a.temperature, no_think=not self.a.thinking,
                               memory_path=self.memory_path, use_memory=not self.a.no_memory,
                               polish_client=self.polish_client)


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.app", description="Alfred, with you approving every draft.")
    p.add_argument("--model", required=True, help="Model name on the server, e.g. alfred-4b.")
    p.add_argument("--base-url", default="ollama",
                   help="Server URL or shortcut: ollama or vllm (on this computer), tokenfactory or openai (hosted, so "
                        "everything Alfred reads is sent there).")
    p.add_argument("--api-key", default=None)
    p.add_argument("--tool-mode", default="native", choices=["native", "json"])
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--thinking", action="store_true", help="Leave the model's reasoning on.")
    p.add_argument("--world", type=int, default=1000, help="Which simulated person's inbox to use.")
    p.add_argument("--memory", default="~/.alfred-memory.json", help="Where to keep answers to earlier questions.")
    p.add_argument("--no-memory", action="store_true")
    p.add_argument("--cloud", action="store_true",
                   help="Turn on the Polish button, which sends one email draft to Token Factory (needs NEBIUS_API_KEY). "
                        "Off by default.")
    p.add_argument("--no-cloud", action="store_true", help=argparse.SUPPRESS)  # the default now; kept for old scripts
    p.add_argument("--host", default="127.0.0.1", help="A loopback address: the page approves drafts, so it never "
                                                       "listens on the network.")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--grant", metavar="FOLDER", default=None,
                   help="Work on this folder instead of the simulated inbox, through the permission broker. Alfred can "
                        "only read it, and the page asks you before each request.")
    p.add_argument("--audit", default="~/.alfred-audit.jsonl",
                   help="With --grant: the file that logs every call the broker checks (ids, operations, reasons and "
                        "sizes, never names or contents). Empty keeps the log in memory.")
    a = p.parse_args(argv)
    if a.grant and a.cloud:
        p.error("--grant can't be used with --cloud: nothing goes to the cloud when Alfred works on a real folder.")
    if a.grant and a.tool_mode != "native":
        p.error("--grant needs native tool calling.")
    return a


def serve(a) -> ThreadingHTTPServer:
    if not is_loopback(a.host):
        raise SystemExit(f"--host {a.host}: Alfred's page approves drafts, so it only listens on this computer "
                         f"(127.0.0.1).")
    try:
        app = App(a)
    except GrantError as e:
        raise SystemExit(f"--grant: {e}") from None
    server = ThreadingHTTPServer((a.host, a.port), make_handler(app))
    app.listen_on(a.host, server.server_address[1])
    server.app = app
    return server


def cloud_status(a, app: App) -> list[str]:
    """What leaves this computer, as the terminal says it. The page says the same."""
    lines = []
    if app.model_host:
        lines.append(f"Not on this computer: the model runs at {app.model_host}, so your requests and everything "
                     f"Alfred reads are sent there.")
    if app.polish_client:
        lines.append("Cloud: on for the Polish button (one email draft at a time, to Nemotron 3 Super on Token "
                     "Factory).")
    elif a.cloud and not a.no_cloud:
        lines.append("The Polish button is off: --cloud needs NEBIUS_API_KEY set.")
    if not app.model_host and not app.polish_client:
        lines.append("Cloud: off. Nothing leaves this computer.")
    return lines


def main(argv=None):
    a = parse_args(argv)
    server = serve(a)
    app = server.app
    url = f"http://{a.host}:{server.server_address[1]}/#k={app.token}"
    print(f"Alfred is running with {a.model}. Open it with this link, which carries the key for this run:\n  {url}\n"
          f"Press Ctrl+C to stop.")
    if app.folder is not None:
        print(f"Folder: {app.folder}. Alfred can only read it, and the page asks you before each request.")
    for line in cloud_status(a, app):
        print(line)
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
