"""Re-grade finished runs with the current grader, without calling any model.

    python -m alfred.rescore runs/<run-dir> [runs/<another-run-dir> ...] --out runs/rescored

Each episode's tool calls are replayed, in order, into a fresh environment and scored again. Use it after a
grading fix to see how earlier results change.

A replay can't show what a model would have done if the new grader had answered it differently (say, a draft that
the simulated user now rejects), so read the numbers as a re-grade of the same behavior. Rerun the models when
you need exact figures.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .env import Environment
from .report import render_markdown, summarize
from .reward import compute_reward
from .tasks import generate_tasks

# Parts of the old metrics that the grading fixes didn't touch. A replay must reproduce them exactly.
UNCHANGED = ("calls", "invalid_calls", "overreach_reads", "leaks", "injection_followed", "asked", "truncated")


def task_for(task_id: str, template: str, style: str = "base", pack: str = "", interface: str = "tools"):
    seed = int(task_id[1:].split("-", 1)[0])
    return generate_tasks(seed, 1, [template], style=style, pack=pack, interface=interface)[0]


def replay(traj: dict, row: dict, config: dict) -> Environment:
    task = task_for(row["task_id"], row["template"], config.get("style", "base"), config.get("pack") or "",
                    config.get("interface", "tools"))
    env = Environment(task, prompt=row["prompt"], max_steps=config.get("max_steps", 20),
                      read_policy=config.get("read_policy", "log"), feedback=config.get("feedback", "reason"),
                      tool_mode=row.get("tool_mode", "native"))
    env.reset()
    for e in traj.get("log", []):
        if env.done:
            break
        if e.get("implicit_finish"):
            env.implicit_finish(e.get("text", ""))
        elif e.get("reply") is not None:
            env.reply(e["reply"])
        elif e.get("tool") is None:
            env.record_invalid((e.get("result") or {}).get("error", "invalid"), e.get("raw_arguments", ""))
        elif e.get("invalid"):
            env.call_tool(e["tool"], e.get("raw_arguments"))
        else:
            env.call_tool(e["tool"], e.get("args", {}))
    for _ in range((row.get("stats") or {}).get("recovered_calls", 0)):
        env.note_format("recovered_call")
    if (traj.get("metrics") or {}).get("truncated"):
        env.truncate()
    return env


def rescore_run(run_dir: Path, out_root: Path) -> tuple[Path, list[str]]:
    config = json.loads((run_dir / "config.json").read_text())
    out = out_root / f"{run_dir.name}-rescored"
    rows, mismatches = [], []
    for path in sorted((run_dir / "trajectories").glob("*/*/*.json")):
        traj = json.loads(path.read_text())
        row = dict(traj["row"])
        if row.get("error") or not row.get("metrics"):
            rows.append(row)
            continue
        env = replay(traj, row, config)
        metrics = env.metrics()
        old = row["metrics"]
        diff = [k for k in UNCHANGED if k in old and old[k] != metrics[k]]
        if diff:
            mismatches.append(f"{path.relative_to(run_dir)}: {', '.join(diff)}")
        row.update(metrics=metrics, reward=compute_reward(metrics), rescored=True)
        rows.append(row)
        new_traj = env.trajectory(extra={"row": row, "stats": traj.get("stats"), "messages": traj.get("messages")})
        dest = out / path.relative_to(run_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(new_traj, indent=1, ensure_ascii=False, default=str))
    rows.sort(key=lambda r: (r["config"], r["task_id"], r["sample"]))
    out.mkdir(parents=True, exist_ok=True)
    meta = {**config, "rescored_from": str(run_dir)}
    (out / "config.json").write_text(json.dumps(meta, indent=2))
    (out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    summary = summarize(rows)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "report.md").write_text(render_markdown(summary, meta))
    return out, mismatches


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.rescore", description="Re-grade runs with the current grader.")
    p.add_argument("runs", nargs="+", help="Run directories written by alfred.eval.")
    p.add_argument("--out", default="runs/rescored", help="Where to write the re-graded runs.")
    a = p.parse_args(argv)
    outs = []
    for r in a.runs:
        out, mismatches = rescore_run(Path(r), Path(a.out))
        outs.append(out)
        print(f"{r} -> {out}" + (f"  ({len(mismatches)} episodes didn't replay identically)" if mismatches else ""))
        for m in mismatches[:10]:
            print(f"   {m}")
    return outs


if __name__ == "__main__":
    main()
