"""Print trajectories in a readable form. Reading rollouts by hand is how you find new failure modes.

    python -m alfred.show runs/<run>/trajectories/<model>/<prompt>/<task>__s0.json
    python -m alfred.show runs/<run-a> runs/<run-b>                  # the same tasks, run A then run B
    python -m alfred.show runs/<run-a> runs/<run-b> --task newapp_none --sample 0

Given two run directories, it pairs episodes by task and try, prints a tally of clean successes per task, then each
pair. Works for email tasks and app tasks, in either interface.
"""
from __future__ import annotations

import argparse
import json
import textwrap
from collections import defaultdict
from pathlib import Path


def _short(obj, width: int) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return text if len(text) <= width else text[: width - 3] + "..."


def render(tr: dict, width: int = 160) -> str:
    t, out = tr["task"], []
    allowed = t.get("allowed_reads") or []
    out.append(f"TASK {t['id']}  [{t['category']}]  prompt={tr.get('prompt')}")
    out.append(f"User: {t['instruction']}")
    if not t.get("app"):
        out.append(f"Allowed to open: {', '.join(allowed) or '(nothing)'}")
    out.append("")
    # Email episodes key a draft by "call_index"; app episodes by "i".
    actions = {a.get("call_index", a.get("i")): a for a in tr.get("actions", [])}
    for e in tr["log"]:
        if e.get("implicit_finish"):
            out.append(f"  (ended in plain text) {_short(e.get('text', ''), width)}")
            continue
        if not e.get("tool") and "reply" in e:  # text interface: the model answers by replying
            for label, text in (("replied", e["reply"]), ("user", e.get("answer"))):
                if text:
                    lines = _short(text, width * 4).splitlines() or [""]
                    out.append(f"  {label:>7}: {lines[0]}")
                    out += [f"{'':11}{line}" for line in lines[1:] if line.strip()]
            continue
        tag = " INVALID" if e.get("invalid") else ""
        args = e.get("args", e.get("raw_arguments"))
        out.append(f"{e['i']:>3}. {e.get('tool') or '(no tool)'}({_short(args, width - 20)}){tag}")
        opened = ""
        if e.get("tool") in ("read_email", "get_event", "get_contact") and "id" in (e.get("result") or {}):
            opened = "   <- not needed" if e["result"]["id"] not in allowed else ""
        out.append(textwrap.indent(_short(e.get("result"), width), "       -> ") + opened)
        if e.get("made_up"):
            out.append(f"       [made up: {', '.join(e['made_up'])}]")
        if e["i"] in actions:
            a = actions[e["i"]]
            status = a.get("status") or ("approved" if a.get("classification") == "approved" else "rejected")
            out.append(f"       [{status}: {a.get('classification')}] {a.get('feedback') or a.get('result') or ''}")
    out.append("")
    m = tr.get("metrics") or {}
    keys = ["success", "clean_success", "overreach_reads", "sensitive_reads", "unrequested_actions", "leaks",
            "injection_followed", "asked", "should_ask", "false_completion", "fabricated", "calls", "invalid_calls"]
    out.append("METRICS " + "  ".join(f"{k}={m.get(k)}" for k in keys if k in m))
    return "\n".join(out)


def _episodes(run: Path) -> dict[str, Path]:
    """A run's trajectory files by name (<task>__s<try>.json)."""
    return {p.name: p for p in sorted(run.glob("trajectories/*/*/*.json"))}


def compare(run_a: Path, run_b: Path, task: str = "", sample: int | None = None, width: int = 160) -> str:
    a, b = _episodes(run_a), _episodes(run_b)
    names = [n for n in a if n in b and task in n.split("__")[0]
             and (sample is None or n.endswith(f"__s{sample}.json"))]
    if not names:
        return f"No episodes in common between {run_a} and {run_b}" + (f" for '{task}'" if task else "") + "."
    clean = defaultdict(lambda: [0, 0, 0])  # task -> [clean in A, clean in B, tries]
    loaded = {}
    for n in names:
        ta, tb = json.loads(a[n].read_text()), json.loads(b[n].read_text())
        loaded[n] = (ta, tb)
        c = clean[n.split("__")[0]]
        c[0] += bool((ta.get("metrics") or {}).get("clean_success"))
        c[1] += bool((tb.get("metrics") or {}).get("clean_success"))
        c[2] += 1
    out = [f"A = {run_a.name}", f"B = {run_b.name}", "", "Clean successes per task (A vs B, out of the tries shown):"]
    out += [f"  {t:<40} {ca}/{n}  vs  {cb}/{n}" for t, (ca, cb, n) in sorted(clean.items())]
    tot = [sum(v[i] for v in clean.values()) for i in range(3)]
    out += [f"  {'total':<40} {tot[0]}/{tot[2]}  vs  {tot[1]}/{tot[2]}", ""]
    for n in names:
        ta, tb = loaded[n]
        out += ["=" * width, f"{n}", "-" * 20 + " A " + "-" * 20, render(ta, width),
                "-" * 20 + " B " + "-" * 20, render(tb, width), ""]
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.show", description=__doc__.split("\n")[0])
    p.add_argument("paths", nargs="+", help="A trajectory file, or two run directories to compare.")
    p.add_argument("--task", default="", help="With two runs: only tasks whose id contains this.")
    p.add_argument("--sample", type=int, default=None, help="With two runs: only this try (0, 1, 2...).")
    p.add_argument("--width", type=int, default=160)
    a = p.parse_args(argv)
    if len(a.paths) == 2 and all(Path(x).is_dir() for x in a.paths):
        print(compare(Path(a.paths[0]), Path(a.paths[1]), a.task, a.sample, a.width))
    elif len(a.paths) == 1:
        print(render(json.loads(Path(a.paths[0]).read_text()), a.width))
    else:
        p.error("give one trajectory file, or two run directories")


if __name__ == "__main__":
    main()
