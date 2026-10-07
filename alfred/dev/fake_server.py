"""A tiny OpenAI-compatible chat server driven by a Python function. For testing harness plumbing.

    server, url = serve_fake(lambda request: {"content": "Which one do you mean?"})
    ...
    server.shutdown()

The policy gets the parsed request body and returns {"content": str} and/or {"tool_call": (name, arguments)}.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def serve_fake(policy, model: str = "fake"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep test output quiet
            pass

        def _send(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802 - http.server API
            self._send({"object": "list", "data": [{"id": model, "object": "model", "created": 0, "owned_by": "fake"}]})

        def do_POST(self):  # noqa: N802 - http.server API
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            server.requests.append(body)
            out = policy(body)
            msg = {"role": "assistant", "content": out.get("content")}
            if out.get("tool_call"):
                name, args = out["tool_call"]
                msg["tool_calls"] = [{"id": f"call_{len(server.requests)}", "type": "function",
                                      "function": {"name": name, "arguments": json.dumps(args)}}]
            self._send({"id": "chatcmpl-fake", "object": "chat.completion", "created": 0, "model": body.get("model"),
                        "choices": [{"index": 0, "message": msg,
                                     "finish_reason": "tool_calls" if out.get("tool_call") else "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"
