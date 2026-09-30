"""Print one trajectory in a readable form. Reading rollouts by hand is how you find new failure modes.

    python -m alfred.show runs/<run>/trajectories/<model>/<prompt>/<task>__s0.json
"""
from __future__ import annotations

import argparse
import json
import textwrap


def _short(obj, width: int) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return text if len(text) <= width else text[: width - 3] + "..."


def render(tr: dict, width: int = 160) -> str:
    t, out = tr["task"], []
    out.append(f"TASK {t['id']}  [{t['category']}]  prompt={tr.get('prompt')}")
    out.append(f"User: {t['instruction']}")
    out.append(f"Allowed to open: {', '.join(t['allowed_reads']) or '(nothing)'}")
    out.append("")
    actions = {a["call_index"]: a for a in tr.get("actions", [])}
    for e in tr["log"]:
        if e.get("implicit_finish"):
            out.append(f"  (ended in plain text) {_short(e.get('text', ''), width)}")
            continue
        tag = " INVALID" if e.get("invalid") else ""
        args = e.get("args", e.get("raw_arguments"))
        out.append(f"{e['i']:>3}. {e['tool']}({_short(args, width - 20)}){tag}")
        opened = ""
        if e["tool"] in ("read_email", "get_event", "get_contact") and "result" in e and "id" in e["result"]:
            rid = e["result"]["id"]
            opened = "   <- not needed" if rid not in t["allowed_reads"] else ""
        out.append(textwrap.indent(_short(e.get("result"), width), "       -> ") + opened)
        if e["i"] in actions:
            a = actions[e["i"]]
            out.append(f"       [{a['status']}: {a['classification']}] {a.get('feedback') or a.get('result') or ''}")
    out.append("")
    m = tr.get("metrics") or {}
    keys = ["success", "clean_success", "overreach_reads", "sensitive_reads", "unrequested_actions", "leaks",
            "injection_followed", "asked", "should_ask", "false_completion", "calls", "invalid_calls"]
    out.append("METRICS " + "  ".join(f"{k}={m.get(k)}" for k in keys))
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.show")
    p.add_argument("path")
    p.add_argument("--width", type=int, default=160)
    a = p.parse_args(argv)
    with open(a.path) as f:
        print(render(json.load(f), a.width))


if __name__ == "__main__":
    main()
