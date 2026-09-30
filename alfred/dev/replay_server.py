"""A fake OpenAI-compatible server that replays the oracle. Tests the LLM harness without a model.

    python -m alfred.dev.replay_server --port 8765 --worlds 2 &
    python -m alfred.eval --models replay --base-url http://127.0.0.1:8765/v1 --worlds 2

Each request is matched to its task (by the system prompt's first line plus the user's instruction)
and answered with the oracle's next tool call. Like a real server, it rejects malformed
conversations, such as a tool result whose id doesn't match an earlier tool call.
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..agents.scripted import OracleAgent
from ..env import Environment
from ..tasks import generate_tasks


def _key(system: str, instruction: str) -> str:
    return (system.splitlines()[0] if system else "") + "\n" + instruction


def build_scripts(world_start: int, worlds: int) -> dict:
    scripts = {}
    for task in generate_tasks(world_start, worlds):
        env = Environment(task)
        OracleAgent().run(env)
        scripts[_key(env.system_prompt, task.instruction)] = [(e["tool"], e["args"]) for e in env.log if e.get("tool")]
    return scripts


def check_conversation(messages: list[dict]) -> str | None:
    pending = set()
    for i, m in enumerate(messages):
        role = m.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            return f"message {i}: unknown role {role!r}"
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                if tc.get("type") != "function" or not isinstance(tc.get("function", {}).get("arguments"), str):
                    return f"message {i}: malformed tool call"
                pending.add(tc.get("id"))
        elif role == "tool":
            if m.get("tool_call_id") not in pending:
                return f"message {i}: tool result for unknown call {m.get('tool_call_id')!r}"
            pending.discard(m["tool_call_id"])
            if not isinstance(m.get("content"), str):
                return f"message {i}: tool content must be a string"
    return None


def completion(content=None, tool_call=None) -> dict:
    msg = {"role": "assistant", "content": content}
    if tool_call:
        cid, name, args = tool_call
        msg["tool_calls"] = [{"id": cid, "type": "function", "function": {"name": name, "arguments": args}}]
    return {"id": "chatcmpl-replay", "object": "chat.completion", "created": 0, "model": "replay",
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_call else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._send(200, {"object": "list", "data": [{"id": "replay", "object": "model", "created": 0,
                                                                "owned_by": "alfred"}]})
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, {"error": {"message": "not found"}})
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        messages = req.get("messages", [])
        problem = check_conversation(messages)
        if problem:
            self.server.problems.append(problem)
            return self._send(400, {"error": {"message": problem, "type": "invalid_request_error"}})
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        instruction = next((m["content"] for m in messages if m["role"] == "user"), "")
        script = self.server.scripts.get(_key(system, instruction))
        if script is None:
            return self._send(200, completion(content="ok"))
        turn = sum(1 for m in messages if m["role"] == "assistant")
        name, args = script[turn] if turn < len(script) else ("finish", {"status": "done", "message": "done"})
        self.server.n_requests += 1
        if req.get("tools"):
            return self._send(200, completion(tool_call=(f"call_{turn}", name, json.dumps(args))))
        return self._send(200, completion(content=json.dumps({"tool": name, "arguments": args})))


class ReplayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, scripts: dict):
        super().__init__(address, _Handler)
        self.scripts, self.n_requests, self.problems = scripts, 0, []


def serve_in_thread(world_start: int = 1000, worlds: int = 1, port: int = 0):
    server = ReplayServer(("127.0.0.1", port), build_scripts(world_start, worlds))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.dev.replay_server")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--world-start", type=int, default=1000)
    p.add_argument("--worlds", type=int, default=2)
    a = p.parse_args(argv)
    server = ReplayServer(("127.0.0.1", a.port), build_scripts(a.world_start, a.worlds))
    print(f"Replaying the oracle at http://127.0.0.1:{a.port}/v1 (worlds {a.world_start}-{a.world_start + a.worlds - 1})")
    server.serve_forever()


if __name__ == "__main__":
    main()
