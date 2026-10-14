"""Build the supervised training set: filtered teacher episodes plus scripted ones, as chat conversations.

    # 1. Run a teacher on training worlds (the eval harness does this; worlds 0-949 only):
    python -m alfred.eval --models nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B --base-url tokenfactory \\
        --world-start 0 --worlds 200 --temperature 0.6 --max-tokens 8192 --workers 16 --label teacher-nano30b

    # 2. Keep its clean successes, top up with scripted episodes, write JSONL:
    python -m alfred.train.data --runs runs/<teacher-run> --oracle-worlds 200-449 --out data/sft.jsonl

What gets kept from a teacher, and why:
- Only clean successes: the task was done, nothing unneeded was opened or drafted, nothing private leaked, no
  injected instruction was acted on. For ambiguous requests that means it asked before drafting anything.
- Only episodes with no formatting problems, except one that's repaired: a teacher that ended with a plain-text
  answer instead of calling finish gets that answer wrapped in a finish call, with the status the grader inferred.
- Reasoning is dropped (the student runs with reasoning off), and so is any text before a tool call.
- A turn whose draft was rejected or whose call failed stays in the conversation, so the student sees how the
  teacher recovered, but it gets no loss, so the student doesn't learn the mistake.

Scripted episodes come from TeachingOracle: always correct, worded in varied ways, and usually warning the user
when an email contains a prompt injection. They fill in templates where the teacher's clean episodes are scarce
(Nemotron teachers guess on many ambiguous requests).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from ..agents.llm import strip_think
from ..agents.scripted import TeachingOracle
from ..env import make_env, task_tools
from ..tasks import APP_TEMPLATES, EVAL_ONLY_TEMPLATES, TEMPLATES, TRAIN_WORLDS, generate_tasks

# Evaluation-only templates (tasks.EVAL_ONLY_TEMPLATES: held-out email tasks and apps never trained on) never are.
TRAINING_TEMPLATES = list(TEMPLATES) + list(APP_TEMPLATES)


def _check_templates(templates) -> None:
    held = set(templates) & set(EVAL_ONLY_TEMPLATES)
    if held:
        raise ValueError(f"{sorted(held)} are held out for evaluation and can't be training data")


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _parse_world_range(text: str) -> range:
    a, _, b = text.partition("-")
    return range(int(a), int(b or a) + 1)


def _failed(tool_content: str) -> bool:
    """A tool result that means the call before it went wrong: an error, or a draft the user rejected."""
    try:
        obj = json.loads(tool_content)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(obj, dict) and ("error" in obj or obj.get("status") == "rejected")


def train_flags(messages: list[dict]) -> list[bool]:
    """For each assistant message: False if any of its calls failed or was rejected."""
    flags = []
    for i, m in enumerate(messages):
        if m["role"] != "assistant":
            continue
        results = []
        for later in messages[i + 1:]:
            if later["role"] != "tool":
                break
            results.append(later.get("content", ""))
        flags.append(not any(_failed(c) for c in results))
    return flags


def keep_teacher_episode(traj: dict, max_calls: int = 15) -> tuple[bool, str]:
    """Whether a teacher episode is good enough to learn from, and if not, why."""
    row, m = traj.get("row") or {}, traj.get("metrics") or {}
    if row.get("error") or not m:
        return False, "error"
    if row.get("tool_mode", "native") != "native" or not traj.get("messages"):
        return False, "not native"
    if traj["task"]["world_seed"] not in TRAIN_WORLDS:
        return False, "not a training world"
    if traj["task"]["template"] in EVAL_ONLY_TEMPLATES:
        return False, "held-out template"
    if not m.get("clean_success"):
        return False, "not a clean success"
    if m.get("truncated") or m.get("calls", 0) > max_calls:
        return False, "too long"
    other_format = (m.get("invalid_calls", 0) + len(traj.get("format_notes") or []) + int(m.get("leaked_reasoning", 0))
                    + int(m.get("status_conflict", 0)) + m.get("classifications", []).count("format"))
    if other_format:
        return False, "formatting problems"
    return True, "kept"


def conversation_from_trajectory(traj: dict) -> list[dict]:
    """A teacher's messages, cleaned for training (see the module docstring)."""
    out = []
    msgs = traj["messages"]
    text_interface = traj["task"].get("interface", "tools") == "text"
    last_assistant = max(i for i, m in enumerate(msgs) if m["role"] == "assistant")
    for i, m in enumerate(msgs[:last_assistant + 1]):
        role = m["role"]
        if role in ("system", "user"):
            out.append({"role": role, "content": m.get("content") or ""})
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id"), "content": m.get("content") or ""})
        elif m.get("tool_calls"):
            calls = []
            for k, tc in enumerate(m["tool_calls"]):
                fn = tc["function"]
                args = fn.get("arguments")
                args = json.loads(args) if isinstance(args, str) and args.strip() else (args or {})
                calls.append({"id": tc.get("id") or f"call_{i}_{k}", "type": "function",
                              "function": {"name": fn["name"], "arguments": args}})
            out.append({"role": "assistant", "content": "", "tool_calls": calls})
        elif text_interface:
            # In the text interface a plain reply is how the assistant asks, declines or answers.
            out.append({"role": "assistant", "content": strip_think(m.get("content"))})
        else:
            if i != last_assistant:
                raise ValueError("plain-text assistant message before the end")
            # Ended in plain text: make it the finish call it should have been.
            status = (traj.get("metrics") or {}).get("status") or "done"
            out.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"call_{i}_0", "type": "function",
                 "function": {"name": "finish", "arguments": {"status": status,
                                                              "message": strip_think(m.get("content"))}}}]})
    return out


def conversation_from_env(env) -> list[dict]:
    """An episode driven by a scripted agent (which calls the environment directly), as a conversation."""
    out = [{"role": "system", "content": env.system_prompt}, {"role": "user", "content": env.task.instruction}]
    for n, e in enumerate(env.log, 1):
        if e.get("reply") is not None:  # text interface: a question for the user, or the final answer
            out.append({"role": "assistant", "content": e["reply"]})
            if e["answer"] is None:
                break
            out.append({"role": "user", "content": e["answer"]})
            continue
        if not e.get("tool") or e.get("invalid"):
            raise ValueError("scripted episodes should only make valid calls")
        cid = f"call_{n}"
        out.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": e["tool"], "arguments": e["args"]}}]})
        if e["tool"] == "finish":
            break
        out.append({"role": "tool", "tool_call_id": cid, "content": _json(e["result"])})
    return out


def example(source: str, task: dict, messages: list[dict], metrics: dict, sample: int = 0) -> dict:
    return {"id": f"{source}:{task['id']}:s{sample}", "source": source, "task_id": task["id"],
            "template": task["template"], "category": task["category"], "world_seed": task["world_seed"],
            "world_style": task.get("world_style", "base"),
            "interface": task.get("interface", "tools"),
            "messages": messages, "train": train_flags(messages), "tools": task_tools(task),
            "metrics": {k: metrics.get(k) for k in ("clean_success", "calls", "first_try_approval", "asked")}}


def load_teacher(run_dirs: list[Path], max_per_task: int = 1) -> tuple[list[dict], Counter]:
    """Kept teacher episodes (at most max_per_task per task, preferring first-try approvals and fewer calls)."""
    why = Counter()
    by_task = defaultdict(list)
    for run in run_dirs:
        for path in sorted(Path(run).glob("trajectories/*/*/*.json")):
            traj = json.loads(path.read_text())
            ok, reason = keep_teacher_episode(traj)
            why[reason] += 1
            if not ok:
                continue
            try:
                msgs = conversation_from_trajectory(traj)
            except (ValueError, KeyError, json.JSONDecodeError):
                why["couldn't convert"] += 1
                why["kept"] -= 1
                continue
            row = traj["row"]
            by_task[traj["task"]["id"]].append((traj, msgs, row))
    out = []
    for task_id, eps in sorted(by_task.items()):
        eps.sort(key=lambda e: (not e[0]["metrics"].get("first_try_approval", True), e[0]["metrics"]["calls"],
                                e[2].get("sample", 0)))
        for traj, msgs, row in eps[:max_per_task]:
            out.append(example(f"teacher:{row.get('model', '?')}", traj["task"], msgs, traj["metrics"],
                               row.get("sample", 0)))
    return out, why


def text_interface_for(task_id: str, share: float) -> bool:
    """Whether a training task is played in the text interface: a fixed share, decided by the task id."""
    return share > 0 and random.Random(f"interface:{task_id}").random() < share


def oracle_examples(worlds: range, templates: list[str], prompt: str = "plain", realistic_share: float = 0.0,
                    pack: str = "", text_share: float = 0.0) -> list[dict]:
    """Scripted episodes. realistic_share of the worlds (the last ones in the range) are realistic; text_share of
    the tasks use the text interface (no ask_user or finish tool)."""
    _check_templates(templates)
    out = []
    n_real = round(len(worlds) * realistic_share)
    split = worlds.start + len(worlds) - n_real
    tasks = generate_tasks(worlds.start, split - worlds.start, templates) if split > worlds.start else []
    tasks += generate_tasks(split, n_real, templates, style="realistic", pack=pack) if n_real else []
    tasks = [t.as_interface("text") if text_interface_for(t.id, text_share) else t for t in tasks]
    for task in tasks:
        if task.world_seed not in TRAIN_WORLDS:
            raise ValueError(f"world {task.world_seed} isn't a training world")
        env = make_env(task, prompt=prompt, tool_mode="native")
        TeachingOracle().run(env)
        m = env.metrics()
        if not m["clean_success"]:
            raise AssertionError(f"scripted episode for {task.id} isn't a clean success")
        out.append(example("oracle", task.to_json(), conversation_from_env(env), m))
    return out


def balance(teacher: list[dict], oracle: list[dict], per_template: int | None, oracle_min: float,
            seed: int = 0) -> list[dict]:
    """Per template and interface: up to per_template teacher examples, topped up with scripted ones to
    per_template, and at least oracle_min of the group's examples scripted. per_template defaults to the largest
    teacher count. Each (template, interface) pair gets the same share, so neither way of asking and finishing
    dominates."""
    rng = random.Random(seed)
    t_by, o_by = defaultdict(list), defaultdict(list)
    for ex in teacher:
        t_by[(ex["template"], ex.get("interface", "tools"))].append(ex)
    for ex in oracle:
        o_by[(ex["template"], ex.get("interface", "tools"))].append(ex)
    target = (per_template or max((len(v) for v in t_by.values()), default=0)
              or max((len(v) for v in o_by.values()), default=0))
    out = []
    for tpl in sorted(set(t_by) | set(o_by)):
        t, o = t_by[tpl][:], o_by[tpl][:]
        rng.shuffle(t)
        rng.shuffle(o)
        n_oracle = max(int(round(oracle_min * target)), target - len(t))
        n_oracle = min(n_oracle, len(o))
        n_teacher = min(len(t), target - n_oracle)
        out += t[:n_teacher] + o[:n_oracle]
    rng.shuffle(out)
    return out


def stats(examples: list[dict]) -> str:
    by = defaultdict(Counter)
    turns, chars = Counter(), Counter()
    for ex in examples:
        src = "teacher" if ex["source"].startswith("teacher") else "oracle"
        key = (ex["template"], ex.get("interface", "tools"))
        by[key][src] += 1
        turns[key] += sum(m["role"] == "assistant" for m in ex["messages"])
        chars[key] += sum(len(_json(m)) for m in ex["messages"])
    lines = ["| Template | Interface | Teacher | Scripted | Total | Turns per example | ~Tokens per example |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for key in sorted(by):
        n = sum(by[key].values())
        lines.append(f"| {key[0]} | {key[1]} | {by[key]['teacher']} | {by[key]['oracle']} | {n} | "
                     f"{turns[key] / n:.1f} | {chars[key] / n / 3.5 + 2500:,.0f} |")
    total = len(examples)
    lines.append(f"| **all** | | {sum(v['teacher'] for v in by.values())} | {sum(v['oracle'] for v in by.values())} | "
                 f"{total} | | |")
    return "\n".join(lines)


def split_validation(examples: list[dict], frac: float) -> tuple[list[dict], list[dict]]:
    """Hold out whole tasks (by a hash of the task id), so validation loss isn't measured on a task seen in
    training through another episode."""
    def held(ex):
        h = int(hashlib.sha1(ex["task_id"].encode()).hexdigest()[:8], 16)
        return (h % 10_000) < frac * 10_000
    return [e for e in examples if not held(e)], [e for e in examples if held(e)]


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.train.data", description=__doc__.split("\n\n")[0])
    p.add_argument("--runs", nargs="*", default=[], help="Teacher run directories from alfred.eval.")
    p.add_argument("--max-per-task", type=int, default=1, help="Teacher episodes kept per task.")
    p.add_argument("--oracle-worlds", default="", help="Training worlds for scripted episodes, e.g. 200-449.")
    p.add_argument("--templates", default=",".join(TRAINING_TEMPLATES))
    p.add_argument("--realistic-share", type=float, default=0.0,
                   help="Share of the scripted worlds that are realistic (teacher episodes keep their own style).")
    p.add_argument("--pack", default="", help="Content pack for realistic worlds.")
    p.add_argument("--text-share", type=float, default=0.0,
                   help="Share of the scripted tasks in the text interface (no ask_user or finish tool).")
    p.add_argument("--per-template", type=int, default=0,
                   help="Examples per template (default: the largest teacher count).")
    p.add_argument("--oracle-min", type=float, default=0.15,
                   help="Minimum share of scripted examples per template.")
    p.add_argument("--val-frac", type=float, default=0.03, help="Share of tasks held out for validation loss.")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", required=True, help="Output .jsonl (validation goes next to it as *.val.jsonl).")
    a = p.parse_args(argv)

    templates = [t.strip() for t in a.templates.split(",") if t.strip()]
    _check_templates(templates)
    teacher, why = load_teacher([Path(r) for r in a.runs], a.max_per_task)
    teacher = [ex for ex in teacher if ex["template"] in templates]
    oracle = (oracle_examples(_parse_world_range(a.oracle_worlds), templates, realistic_share=a.realistic_share,
                              pack=a.pack, text_share=a.text_share) if a.oracle_worlds else [])
    examples = balance(teacher, oracle, a.per_template or None, a.oracle_min, a.seed)
    train, val = split_validation(examples, a.val_frac)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(_json(ex) + "\n" for ex in train))
    out.with_suffix(".val.jsonl").write_text("".join(_json(ex) + "\n" for ex in val))
    report = (f"# Training data\n\nTeacher episodes read: {sum(why.values())}. "
              + ", ".join(f"{k}: {v}" for k, v in why.most_common()) + ".\n\n"
              + stats(examples) + f"\n\n{len(train)} training examples, {len(val)} validation examples.\n")
    out.with_suffix(".md").write_text(report)
    print(report)
    print(f"Wrote {out} and {out.with_suffix('.val.jsonl')}")
    return out


if __name__ == "__main__":
    main()
