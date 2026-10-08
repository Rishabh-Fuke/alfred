"""The web app: a local page where you talk to Alfred, approve its drafts and answer its questions.

    python -m alfred.app --model alfred-4b --base-url ollama          # a model served by Ollama
    python -m alfred.app --model alfred-rl --base-url vllm            # or by vLLM

Everything runs on this machine except one optional button: "Polish with Nemotron 3 Super" sends a pending
email's subject and body (nothing else) to Token Factory, if NEBIUS_API_KEY is set.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .live import Session

STATIC = Path(__file__).parent / "static"


def make_handler(app: "App"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, kind: str = "application/json"):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode())

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_GET(self):  # noqa: N802 - http.server API
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if url.path.startswith("/static/"):
                f = (STATIC / url.path[len("/static/"):]).resolve()
                if STATIC.resolve() in f.parents and f.is_file():
                    kind = {".woff2": "font/woff2", ".css": "text/css", ".js": "text/javascript",
                            ".svg": "image/svg+xml"}.get(f.suffix, "application/octet-stream")
                    return self._send(200, f.read_bytes(), kind)
            if url.path == "/api/world":
                return self._json(app.session.world_view())
            if url.path == "/api/events":
                since = int(parse_qs(url.query).get("since", ["0"])[0])
                s = app.session
                p = s.pending
                return self._json({"events": s.events_since(since), "running": s.running,
                                   "pending": {"id": p.id, "kind": p.kind} if p else None})
            self._json({"error": "not found"}, 404)

        def do_POST(self):  # noqa: N802 - http.server API
            path, body, s = urlparse(self.path).path, self._body(), app.session
            try:
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
        url, key = resolve_endpoint(a.base_url, a.api_key)
        self.client = OpenAI(base_url=url, api_key=key, timeout=300, max_retries=2)
        tf_key = os.environ.get("NEBIUS_API_KEY")
        self.polish_client = (OpenAI(base_url="https://api.tokenfactory.nebius.com/v1/", api_key=tf_key, timeout=120)
                              if tf_key and not a.no_cloud else None)
        self.memory_path = Path(a.memory).expanduser() if a.memory else None
        self.reset(a.world)

    def reset(self, seed: int) -> None:
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
    p.add_argument("--base-url", default="ollama", help="Server URL or shortcut: ollama, vllm, tokenfactory, openai.")
    p.add_argument("--api-key", default=None)
    p.add_argument("--tool-mode", default="native", choices=["native", "json"])
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--thinking", action="store_true", help="Leave the model's reasoning on.")
    p.add_argument("--world", type=int, default=1000, help="Which simulated person's inbox to use.")
    p.add_argument("--memory", default="~/.alfred-memory.json", help="Where to keep answers to earlier questions.")
    p.add_argument("--no-memory", action="store_true")
    p.add_argument("--no-cloud", action="store_true", help="Never call Token Factory, even with a key set.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    return p.parse_args(argv)


def serve(a) -> ThreadingHTTPServer:
    app = App(a)
    server = ThreadingHTTPServer((a.host, a.port), make_handler(app))
    server.app = app
    return server


def main(argv=None):
    a = parse_args(argv)
    server = serve(a)
    url = f"http://{a.host}:{server.server_address[1]}/"
    print(f"Alfred is running at {url} with {a.model}. Press Ctrl+C to stop.")
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
