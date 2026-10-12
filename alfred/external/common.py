"""Shared helpers for the outside benchmarks."""
from __future__ import annotations

import json
import random
import re
import threading
from pathlib import Path

NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}


def client_for(base_url: str, api_key: str | None = None, timeout: float = 600.0):
    """An OpenAI client for a URL or provider shortcut (see alfred.eval.PROVIDERS), plus the resolved URL."""
    from openai import OpenAI

    from ..eval import resolve_endpoint
    url, key = resolve_endpoint(base_url, api_key)
    return OpenAI(base_url=url, api_key=key, timeout=timeout, max_retries=3), url


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")


def bootstrap(values: list[float], n_boot: int = 1000, seed: int = 0) -> dict | None:
    """Mean with a 95% bootstrap interval."""
    if not values:
        return None
    rng = random.Random(seed)
    k = len(values)
    boots = sorted(sum(values[rng.randrange(k)] for _ in range(k)) / k for _ in range(n_boot))
    return {"value": sum(values) / k, "lo": boots[int(0.025 * n_boot)], "hi": boots[int(0.975 * n_boot) - 1], "n": k}


def pct(x: dict | None) -> str:
    return "n/a" if not x else f"{x['value']:.0%} [{x['lo']:.0%}-{x['hi']:.0%}]"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ----------------------------------------------------------------------------- Alfred's finish tool, elsewhere
# Alfred ends every task with a finish(status, message) tool call. Other benchmarks have no such tool: an agent's
# final answer is a plain reply. A model trained in Alfred may still call finish there, and the benchmark then
# answers "invalid tool" until its step limit runs out, which scores a done task as a failure. These helpers read
# such a call as what it means, the final reply, and count how often that happened so reports can say so.

# Results go in folders tagged with the harness version, so a rerun after a harness change never reuses replies
# cached by the old one. h2: Alfred's finish tool is read as the final reply; context overflows end the episode.
HARNESS = "h2"
FINISH_STATS = {"replies": 0, "finish_read_as_reply": 0, "context_overflow": 0}
_STATS_LOCK = threading.Lock()


def count(key: str) -> None:
    with _STATS_LOCK:
        FINISH_STATS[key] += 1
_FINISH_XML = re.compile(r"<tool_call>\s*<function=finish>.*?</function>\s*</tool_call>", re.S)


def _finish_text(args) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return args
    if not isinstance(args, dict):
        return str(args)
    return str(args.get("message") or args.get("answer") or args.get("text") or "")


def read_finish_as_reply(message, offered: set[str]) -> bool:
    """If `message` (an OpenAI ChatCompletionMessage) calls a `finish` tool the benchmark didn't offer, turn that
    call into the reply text, in place. Returns True if it did."""
    if "finish" in offered:
        return False
    count("replies")
    calls = list(getattr(message, "tool_calls", None) or [])
    finishes = [c for c in calls if c.function.name == "finish"]
    text = message.content or ""
    if finishes:
        rest = [c for c in calls if c.function.name != "finish"]
        if rest:  # other calls still to run: keep those, the finish can wait for the next turn
            message.tool_calls = rest
            return False
        message.tool_calls = None
        message.content = "\n\n".join(x for x in [text.strip(), _finish_text(finishes[0].function.arguments)] if x)
        count("finish_read_as_reply")
        return True
    if _FINISH_XML.search(text):  # a server that left the call as text
        from ..agents.llm import parse_xml_tool_calls
        parsed = [args for name, args in parse_xml_tool_calls(text) if name == "finish"]
        message.content = "\n\n".join(x for x in [_FINISH_XML.sub("", text).strip(),
                                                    _finish_text(parsed[0] if parsed else "")] if x)
        count("finish_read_as_reply")
        return True
    return False


def offered_tools(tools) -> set[str]:
    out = set()
    for t in tools or []:
        f = t.get("function", t) if isinstance(t, dict) else getattr(t, "function", t)
        name = f.get("name") if isinstance(f, dict) else getattr(f, "name", None)
        if name:
            out.add(name)
    return out


def finish_note() -> str:
    s = FINISH_STATS
    if not s["finish_read_as_reply"] and not s["context_overflow"]:
        return ""
    parts = []
    if s["finish_read_as_reply"]:
        parts.append(f"{s['finish_read_as_reply']} of {s['replies']} model replies called Alfred's `finish` tool, "
                     f"which this benchmark doesn't offer; they were read as the final answer")
    if s["context_overflow"]:
        parts.append(f"{s['context_overflow']} episodes ran past the context window and count as failures")
    return "Note: " + "; ".join(parts) + ".\n"
