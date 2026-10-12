"""Run agents on tasks. Writes every trajectory, one results line per episode, and a report.

Check the environment with the scripted oracle (no model needed):
    python -m alfred.eval --agent oracle --worlds 20

Baseline local models served by Ollama:
    python -m alfred.eval --models qwen3:8b,gpt-oss:20b --prompt plain,privacy --worlds 5 --samples 3 --temperature 0.7
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .agents.llm import OpenAICompatAgent, chat_create
from .agents.scripted import SCRIPTED_AGENTS
from .env import Environment
from .report import render_markdown, summarize
from .reward import compute_reward
from .tasks import ALL_TEMPLATES, generate_tasks

OLLAMA_NOTE = ("Note: Ollama's default context window is small, and it silently cuts off long prompts. "
               "Start it with OLLAMA_CONTEXT_LENGTH=16384 (see the README) or results will look worse than they are.")

# Shortcuts for --base-url, and the environment variable each one reads its API key from.
PROVIDERS = {
    "ollama": ("http://localhost:11434/v1", None),
    "vllm": ("http://localhost:8000/v1", None),
    "tokenfactory": ("https://api.tokenfactory.nebius.com/v1/", "NEBIUS_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
}

_clients: dict = {}
_clients_lock = threading.Lock()


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")


def resolve_endpoint(base_url: str, api_key: str | None) -> tuple[str, str]:
    """A provider shortcut or URL, plus the key to use: --api-key, else that provider's own variable, else a dummy.

    A key from the environment only ever goes to its own provider, never to another URL.
    """
    url = PROVIDERS.get(base_url, (base_url, None))[0]
    env_var = next((var for u, var in PROVIDERS.values() if var and u.split("/")[2] in url), None)
    key = api_key or (os.environ.get(env_var) if env_var else None) or "local"
    return url, key


def get_client(a):
    url, key = resolve_endpoint(a.base_url, a.api_key)
    with _clients_lock:
        if (url, key) not in _clients:
            from openai import OpenAI
            _clients[(url, key)] = OpenAI(base_url=url, api_key=key, timeout=a.timeout, max_retries=3)
        return _clients[(url, key)]


def extra_body(a) -> dict | None:
    body = json.loads(a.extra_body) if a.extra_body else {}
    if a.no_think:
        body.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
    return body or None


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.eval", description="Run agents on Alfred tasks.")
    p.add_argument("--agent", default="llm", choices=["llm", *SCRIPTED_AGENTS],
                   help="llm (default) or a scripted agent for testing the environment.")
    p.add_argument("--models", default="", help="Comma-separated model names, e.g. qwen3:8b,gpt-oss:20b")
    p.add_argument("--base-url", default=os.environ.get("ALFRED_BASE_URL", "ollama"),
                   help="OpenAI-compatible endpoint URL, or a shortcut: " + ", ".join(PROVIDERS)
                        + ". Default: ollama. tokenfactory reads its key from NEBIUS_API_KEY.")
    p.add_argument("--api-key", default=None, help="Usually unnecessary: keys are read from the environment.")
    p.add_argument("--no-think", action="store_true",
                   help="Turn off reasoning (sends chat_template_kwargs enable_thinking=false; vLLM and most "
                        "vLLM-based hosts understand it).")
    p.add_argument("--extra-body", default="", help="JSON object sent with every request, for server-specific options.")
    p.add_argument("--prompt", default="plain", help="plain, privacy, or both: plain,privacy")
    p.add_argument("--tool-mode", default="native", choices=["native", "json"])
    p.add_argument("--worlds", type=int, default=5, help="Number of worlds. Each world gives one task per template.")
    p.add_argument("--world-start", type=int, default=1000, help="First world seed. Use 1000+ for evaluation.")
    p.add_argument("--templates", default="",
                   help="Comma-separated templates (default: the 9 training templates). 'heldout' adds the "
                        f"evaluation-only ones, 'all' every one. Names: {', '.join(ALL_TEMPLATES)}")
    p.add_argument("--style", default="base", choices=["base", "realistic"],
                   help="World style: base (templated text) or realistic (lived-in worlds, text from a content pack).")
    p.add_argument("--interface", default="tools", choices=["tools", "text"],
                   help="tools: ask_user and finish are tools (Alfred's own). text: no such tools; the model asks, "
                        "declines and answers by replying, as in most apps and outside benchmarks.")
    p.add_argument("--pack", default="", help="Content pack for --style realistic: a name in alfred/realism/packs or "
                                              "a path. Default: $ALFRED_PACK, else packs/main, else packs/seed.")
    p.add_argument("--limit", type=int, default=0, help="Only run the first N tasks, for a quick check.")
    p.add_argument("--samples", type=int, default=1, help="Episodes per task (use >1 with temperature > 0).")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=4096, help="Per reply. Thinking models need room to think.")
    p.add_argument("--max-steps", type=int, default=20, help="Model turns per episode.")
    p.add_argument("--timeout", type=float, default=600.0, help="Seconds per model call.")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--workers", type=int, default=1, help="Episodes in parallel. Match OLLAMA_NUM_PARALLEL.")
    p.add_argument("--read-policy", default="log", choices=["log", "enforce"])
    p.add_argument("--feedback", default="reason", choices=["reason", "none"])
    p.add_argument("--out", default="runs", help="Parent folder for run directories.")
    p.add_argument("--label", default="", help="Name for this run's directory.")
    p.add_argument("--resume", default="", help="Continue an existing run directory; finished episodes are skipped.")
    return p.parse_args(argv)


def preflight(a, model: str) -> str | None:
    kw = {"extra_body": extra_body(a)} if extra_body(a) else {}
    try:
        chat_create(get_client(a), model, messages=[{"role": "user", "content": "Reply with: ok"}],
                    max_tokens=256, temperature=0, **kw)
        return None
    except Exception as e:  # noqa: BLE001 - report whatever the server said
        msg = f"{type(e).__name__}: {e}"[:300]
        if "11434" in resolve_endpoint(a.base_url, a.api_key)[0] and "not found" in msg.lower():
            msg += f"  (try: ollama pull {model})"
        return msg


def run_one(a, run_dir: Path, model: str, prompt: str, task, sample: int):
    path = run_dir / "trajectories" / _slug(model) / prompt / f"{task.id}__s{sample}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())["row"], True
        except (json.JSONDecodeError, KeyError):
            pass
    env = Environment(task, prompt=prompt, max_steps=a.max_steps, read_policy=a.read_policy,
                      feedback=a.feedback, tool_mode=a.tool_mode)
    if a.agent == "llm":
        agent = OpenAICompatAgent(model, temperature=a.temperature, max_tokens=a.max_tokens, seed=a.seed + sample,
                                  tool_mode=a.tool_mode, client=get_client(a), extra_body=extra_body(a))
    else:
        agent = SCRIPTED_AGENTS[a.agent]()
    t0 = time.perf_counter()
    error, stats = None, {}
    try:
        stats = agent.run(env) or {}
    except Exception as e:  # noqa: BLE001 - keep going; infrastructure errors are reported, not scored
        error = f"{type(e).__name__}: {e}"[:500]
    seconds = round(time.perf_counter() - t0, 3)
    metrics = env.metrics() if (env.world is not None and error is None) else None
    tags = [prompt] + (["no thinking"] if a.no_think else []) + (["text interface"] if a.interface == "text" else [])
    row = {"config": f"{model} [{', '.join(tags)}]", "model": model, "agent": a.agent,
           "prompt": prompt, "no_think": a.no_think,
           "tool_mode": a.tool_mode, "task_id": task.id, "template": task.template, "category": task.category,
           "sample": sample, "metrics": metrics, "reward": compute_reward(metrics) if metrics else None,
           "stats": stats, "seconds": seconds, "error": error}
    traj = env.trajectory(extra={"row": row, "stats": stats, "messages": getattr(agent, "messages", None)})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(traj, indent=1, ensure_ascii=False, default=str))
    return row, False


def _status(row) -> str:
    if row["error"]:
        return "ERROR " + row["error"][:80]
    m = row["metrics"]
    if m["clean_success"]:
        return "clean"
    if m["success"]:
        issues = [k for k, bad in (("snooped", m["overreach_reads"]), ("unrequested", m["unrequested_actions"]),
                                   ("leak", m["leaks"]), ("injection", m["injection_followed"])) if bad]
        return "done, but " + ", ".join(issues)
    why = [k for k, bad in (("guessed", m.get("guessed")), ("false claim", m["false_completion"]),
                            ("acted on injection", m["injection_followed"] or m.get("injection_partial")),
                            ("ran out of steps", m["truncated"])) if bad]
    return "failed" + (f" ({', '.join(why)})" if why else "")


def main(argv=None):
    a = parse_args(argv)
    templates = [t.strip() for t in a.templates.split(",") if t.strip()] or None
    tasks = generate_tasks(a.world_start, a.worlds, templates, style=a.style, pack=a.pack, interface=a.interface)
    if a.limit:
        tasks = tasks[:a.limit]
    prompts = [p.strip() for p in a.prompt.split(",") if p.strip()]
    if a.agent == "llm":
        models = [m.strip() for m in a.models.split(",") if m.strip()]
        if not models:
            sys.exit("Pass --models (for example --models qwen3:8b), or --agent oracle to test the environment.")
    else:
        models = [a.agent]

    name = a.label or ("-".join(models) if len(models) <= 2 else f"{len(models)}-models")
    pack_id = tasks[0].pack_id if tasks else ""
    if a.resume and (Path(a.resume) / "config.json").exists():
        old = json.loads((Path(a.resume) / "config.json").read_text())
        if old.get("pack_id", "") != pack_id:
            # Realistic tasks built from another content pack aren't the same tasks: start a fresh run beside it.
            print(f"{a.resume} used content pack {old.get('pack_id') or 'none'}; this run uses {pack_id or 'none'}, "
                  f"so it starts a new run instead of resuming.")
            name = a.label or Path(a.resume).name.split("-", 2)[-1]
            a.out, a.resume = str(Path(a.resume).parent), ""
    run_dir = Path(a.resume) if a.resume else Path(a.out) / f"{datetime.now():%Y%m%d-%H%M%S}-{_slug(name)}"
    while not a.resume and run_dir.exists():  # two runs started within the same second
        run_dir = run_dir.with_name(run_dir.name.replace("-", "x-", 1))
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {**vars(a), "api_key": "***" if a.api_key else None, "base_url": resolve_endpoint(a.base_url, None)[0],
            "n_tasks": len(tasks), "pack_id": pack_id, "started": datetime.now().isoformat(timespec="seconds")}
    (run_dir / "config.json").write_text(json.dumps(meta, indent=2))

    if a.agent == "llm":
        if "11434" in meta["base_url"]:
            print(OLLAMA_NOTE)
        reachable = []
        for m in models:
            err = preflight(a, m)
            if err:
                print(f"! Skipping {m}: {err}")
            else:
                reachable.append(m)
        models = reachable
        if not models:
            sys.exit(f"No model answered at {meta['base_url']}. Is the server running, and is its API key set?")

    jobs = [(m, p, t, s) for m in models for p in prompts for t in tasks for s in range(a.samples)]
    print(f"{len(jobs)} episodes ({len(models)} model(s) x {len(prompts)} prompt(s) x {len(tasks)} tasks x "
          f"{a.samples} sample(s)) -> {run_dir}")
    rows, lock = [], threading.Lock()
    results = run_dir / "results.jsonl"
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        futures = [pool.submit(run_one, a, run_dir, *job) for job in jobs]
        for i, fut in enumerate(as_completed(futures), 1):
            row, cached = fut.result()
            with lock:
                rows.append(row)
                if not cached:
                    with results.open("a") as f:
                        f.write(json.dumps(row) + "\n")
                calls = row["metrics"]["calls"] if row["metrics"] else "-"
                print(f"[{i:>4}/{len(jobs)}] {row['config']:<28} {row['task_id']:<30} s{row['sample']}  "
                      f"{_status(row):<32} {calls} calls  {row['seconds']:.1f}s", flush=True)

    rows.sort(key=lambda r: (r["config"], r["task_id"], r["sample"]))
    results.write_text("".join(json.dumps(r) + "\n" for r in rows))
    summary = summarize(rows)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    report = render_markdown(summary, meta)
    (run_dir / "report.md").write_text(report)
    print()
    print(report)
    print(f"Trajectories, results and report: {run_dir}")
    return run_dir


if __name__ == "__main__":
    main()
