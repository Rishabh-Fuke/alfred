"""Run the course-folder requests with a model or a scripted agent.

    python -m alfred.files.run --model nemotron-3-nano-4b --base-url ollama --no-think
    python -m alfred.files.run --agent careful     # no model: the requests can be done and the checks work
    python -m alfred.files.run --agent hostile     # no model: every way out of School is refused

Each run builds the course folder in a new temporary folder, gives each request its own broker task with School
granted, and writes each episode, a summary and the broker's audit log to --out. The folder is made-up data; with a
hosted --base-url its text goes to that host, and the run says so.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from ..broker import AuditLog, Broker
from ..eval import no_think_options
from .course import build
from .env import FilesEnvironment
from .scripted import CarefulAgent, HostileAgent
from .tasks import TASKS_BY_ID


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.files.run", description=__doc__.split("\n\n")[0])
    p.add_argument("--agent", choices=["llm", "careful", "hostile"], default="llm")
    p.add_argument("--model", help="the model's name on the server (with --agent llm)")
    p.add_argument("--base-url", default="ollama",
                   help="server URL or shortcut: ollama or vllm (on this computer), tokenfactory or openai (hosted)")
    p.add_argument("--api-key")
    p.add_argument("--interface", choices=["text", "tools"], default="text",
                   help="text: Alfred asks and answers in plain replies; tools: with ask_user and finish")
    p.add_argument("--prompt", choices=["plain", "privacy"], default="plain",
                   help="plain explains the mechanics only; privacy adds rules about files and guessing")
    p.add_argument("--tasks", default=",".join(TASKS_BY_ID), help="comma-separated, from: " + ", ".join(TASKS_BY_ID))
    p.add_argument("--max-steps", type=int, default=12, help="model turns per request")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=2048)
    p.add_argument("--no-think", action="store_true", help="turn reasoning off (Nemotron, Qwen3)")
    p.add_argument("--timeout", type=float, default=600.0, help="seconds per model call")
    p.add_argument("--out", help="where to write episodes and the audit log (default: runs/files/<time>-<name>)")
    a = p.parse_args(argv)
    if a.agent == "llm" and not a.model:
        p.error("--model is required with --agent llm")
    unknown = [t for t in a.tasks.split(",") if t not in TASKS_BY_ID]
    if unknown:
        p.error(f"unknown task: {', '.join(unknown)}")
    return a


def _agent_factory(a):
    if a.agent != "llm":
        return {"careful": CarefulAgent, "hostile": HostileAgent}[a.agent]
    from openai import OpenAI

    from ..agents.llm import OpenAICompatAgent
    from ..app.server import is_loopback
    from ..eval import resolve_endpoint
    url, key = resolve_endpoint(a.base_url, a.api_key)
    host = urlparse(url).hostname or url
    if not is_loopback(host):
        print(f"The model runs at {host}, so the course folder's text goes there (it's made-up data).")
    client = OpenAI(base_url=url, api_key=key, timeout=a.timeout, max_retries=2)
    extra = no_think_options(url) if a.no_think else None
    return lambda: OpenAICompatAgent(a.model, client=client, temperature=a.temperature, max_tokens=a.max_tokens,
                                     extra_body=extra)


def _short(value) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    text = re.sub(r"res_([0-9a-f]{4})[0-9a-f]{28}", r"res_\1…", text)
    return text if len(text) <= 90 else text[:89] + "…"


def describe(entry: dict) -> str:
    """One line for a step: the call, and what the broker did."""
    if entry.get("ask"):
        return f"asked: {_short(entry['ask'])}  →  {_short(entry['answer'])}"
    if entry.get("invalid"):
        return f"invalid call: {_short(entry.get('raw_arguments') or entry.get('tool') or '')}"
    call = f"{entry['tool']}({_short(entry['args'])})"
    if entry["status"] != "ok":
        return f"{call}  →  {entry['status']}: {entry['reason']}"
    data = json.loads(entry["result"])
    if "entries" in data:
        return f"{call}  →  ok: {data['path']}, {len(data['entries'])} entries, {data['skipped']} skipped"
    return f"{call}  →  ok: {data['path']}, {entry['bytes']} bytes"


def main(argv=None) -> int:
    a = parse_args(argv)
    name = a.model if a.agent == "llm" else a.agent
    out = Path(a.out or f"runs/files/{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^A-Za-z0-9._-]+', '-', name)}")
    out.mkdir(parents=True, exist_ok=True)
    make_agent = _agent_factory(a)
    rows = []
    with tempfile.TemporaryDirectory(prefix="alfred-course-") as tmp, Broker(AuditLog(out / "audit.jsonl")) as broker:
        course = build(Path(tmp) / "course")
        for task_id in a.tasks.split(","):
            task = TASKS_BY_ID[task_id]
            env = FilesEnvironment(task, course, broker, interface=a.interface, prompt=a.prompt,
                                   max_steps=a.max_steps)
            print(f"\n{task.id}: {task.instruction}", flush=True)
            started, error, stats = time.perf_counter(), None, {}
            try:
                stats = make_agent().run(env) or {}
            except Exception as e:  # a model server error ends this request; the run goes on
                error = f"{type(e).__name__}: {e}"
                if env.task_id is not None and not env.done:
                    env.truncate()
            if env.task_id is None:
                rows.append({"task": task.id, "ok": False, "error": error})
                print(f"  error: {error}")
                continue
            for entry in env.log:
                print("  " + describe(entry))
            row = {**env.score(), "seconds": round(time.perf_counter() - started, 1)}
            if error:
                row["error"] = error
                print(f"  error: {error}")
            print(f"  answer: {(env.final or '(none)').strip()}")
            print(f"  check: {'ok' if row['ok'] else 'not ok'}  "
                  + ", ".join(f"{k}={v}" for k, v in row.items() if k not in ("task", "ok", "seconds")))
            rows.append(row)
            (out / f"{task.id}.json").write_text(json.dumps({**env.trajectory(), "stats": stats, "error": error},
                                                            indent=2, ensure_ascii=False))
    (out / "summary.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"\n{'task':<12} {'ok':<4} {'asked':>5} {'read':>5} {'refused':>8} {'outside':>8} {'leaked':>7} "
          f"{'seconds':>8}")
    for r in rows:
        print(f"{r['task']:<12} {'yes' if r.get('ok') else 'no':<4} {r.get('asked', '-'):>5} "
              f"{r.get('files_read', '-'):>5} {r.get('refused', '-'):>8} {r.get('outside_attempts', '-'):>8} "
              f"{len(r.get('leaked', [])):>7} {r.get('seconds', '-'):>8}")
    tried = sum(r.get("outside_attempts", 0) for r in rows)
    leaked = sorted({t for r in rows for t in r.get("leaked", [])})
    print("\n" + (f"{tried} calls tried to reach outside School or use a tool that isn't offered; the broker refused "
                  f"each one. " if tried else "No call tried to reach outside School. ")
          + (f"LEAKED: {', '.join(leaked)}" if leaked else "Nothing from Private reached the model."))
    print(f"Episodes, summary and audit log: {out}")
    return 1 if leaked else 0


if __name__ == "__main__":
    sys.exit(main())
