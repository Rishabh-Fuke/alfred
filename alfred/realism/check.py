"""Check a content pack before training on it:

    python -m alfred.realism.check --pack alfred-results/realism/main.jsonl.gz --out check.md

Reports templates per kind and split, anything the validators drop, near-duplicates, whether the scripted oracle
is still 100% clean on realistic worlds built from it (train and eval worlds, every task type), and a sample of
filled-in templates to read.
"""
from __future__ import annotations

import argparse
import random
import re
from collections import Counter
from pathlib import Path

from .kinds import KINDS, SLOTS
from .pack import fill, load, read_entries, resolve


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def check(pack_path: str, worlds: int = 10, samples: int = 12, seed: int = 0) -> tuple[str, bool]:
    from ..agents.scripted import OracleAgent, TeachingOracle
    from ..env import Environment
    from ..tasks import generate_tasks
    path = resolve(pack_path)
    pack = load(str(path))
    raw, meta = read_entries(path)
    ok = True
    lines = [f"# Content pack check: {pack.id}", "", f"{len(raw)} templates in the file, {len(pack)} pass the "
             f"validators ({pack.dropped} dropped).", ""]
    if pack.dropped:
        ok = False
    counts = pack.counts()
    lines += ["| Kind | Train | Eval | Both | Planned | Exact duplicates |", "|---|---:|---:|---:|---:|---:|"]
    short = []
    for name, kind in KINDS.items():
        c = counts.get(name, {})
        texts = Counter(_norm(" ".join(e["fields"].values())) for e in pack.entries if e["kind"] == name)
        dups = sum(n - 1 for n in texts.values() if n > 1)
        lines.append(f"| {name} | {c.get('train', 0)} | {c.get('eval', 0)} | {c.get('both', 0)} | {kind.count} | "
                     f"{dups} |")
        if not (c.get("train", 0) + c.get("both", 0)) or not (c.get("eval", 0) + c.get("both", 0)):
            short.append(name)
    if short:
        lines += ["", f"Kinds missing from a split (worlds there fall back to base wording): {', '.join(short)}"]

    lines += ["", "## Oracle on realistic worlds", ""]
    fails, n = [], 0
    for start in (0, 1000):
        for task in generate_tasks(start, worlds, "all", style="realistic", pack=str(path)):
            for agent in (OracleAgent(), TeachingOracle()):
                env = Environment(task)
                try:
                    agent.run(env)
                    good = env.metrics()["clean_success"]
                except AssertionError as e:
                    good = False
                    fails.append(f"{task.id} ({agent.name}): {e}")
                n += 1
                if not good and not any(task.id in f for f in fails):
                    fails.append(f"{task.id} ({agent.name})")
    ok &= not fails
    lines.append(f"{n - len(fails)}/{n} clean." + ("" if not fails else " Failures:"))
    lines += [f"- {f}" for f in fails[:20]]

    rng = random.Random(seed)
    examples = {s: v[1] for s, v in SLOTS.items()}
    lines += ["", "## Sample (slots filled with example values)", ""]
    for e in rng.sample(pack.entries, min(samples, len(pack.entries))):
        lines += [f"**{e['kind']}** ({e['id']}, {e.get('split')})", "", "```"]
        lines += [f"{k}: {fill(v, examples)}" for k, v in e["fields"].items()] + ["```", ""]
    return "\n".join(lines), ok


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.realism.check", description=__doc__.split("\n\n")[0])
    p.add_argument("--pack", default="", help="Pack name or path (default: the one worlds would use).")
    p.add_argument("--worlds", type=int, default=10, help="Train and eval worlds to run the oracle on.")
    p.add_argument("--samples", type=int, default=12)
    p.add_argument("--out", default="")
    a = p.parse_args(argv)
    text, ok = check(a.pack, a.worlds, a.samples)
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    if not ok:
        raise SystemExit("The pack has problems (see above).")


if __name__ == "__main__":
    main()
