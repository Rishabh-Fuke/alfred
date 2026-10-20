"""Recompute the headline results from the files committed in results/, and fail if any differs from what
RESULTS.md and the README say.

    python scripts/verify_results.py

No model, GPU or network needed: every number comes from per-episode results (results.jsonl) and per-item
When2Call replies (responses.jsonl) in this repo. Comparisons against another model on the same items use a paired
bootstrap (2,000 resamples of the items, seed 0).
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "results/2026-10-training/runs"
EXT = ROOT / "results/2026-10-training/external"
BASELINE_EXT = ROOT / "results/2026-10-nemotron-baseline/external"
LABELS = ("tool_call", "request_for_info", "cannot_answer")


def episodes(run: str) -> list[dict]:
    return [json.loads(x)["metrics"] for x in (RUNS / run / "results.jsonl").read_text().splitlines()]


def share(rows: list[dict], test) -> float:
    return sum(1 for m in rows if test(m)) / len(rows)


def when2call(path: Path) -> dict:
    return {r["uuid"]: r for r in map(json.loads, path.read_text().splitlines())}


def macro_f1(d: dict, items: list) -> float:
    total = 0.0
    for lab in LABELS:
        tp = sum(d[u]["predicted"] == lab and d[u]["correct_answer"] == lab for u in items)
        fp = sum(d[u]["predicted"] == lab and d[u]["correct_answer"] != lab for u in items)
        fn = sum(d[u]["predicted"] != lab and d[u]["correct_answer"] == lab for u in items)
        total += 0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn)
    return total / len(LABELS)


def rate(d: dict, items: list, lab: str) -> float:
    xs = [u for u in items if d[u]["correct_answer"] == lab]
    return sum(d[u]["predicted"] == lab for u in xs) / len(xs)


def paired(a: dict, b: dict, fn, n: int = 2000) -> tuple[float, float, float]:
    items = sorted(set(a) & set(b))
    rng = random.Random(0)
    diffs = sorted(fn(a, s) - fn(b, s) for s in ([rng.choice(items) for _ in items] for _ in range(n)))
    return fn(a, items) - fn(b, items), diffs[int(0.025 * n)], diffs[int(0.975 * n) - 1]


def main() -> int:
    checks = []

    def check(claim: str, got: float, want: float, tol: float = 0.005):
        checks.append((claim, got, want, abs(got - want) <= tol))

    # 1. Apps never trained on (4 held-out apps), clean success, the same 25 tasks x 3 tries for every model.
    for suite, base, ours in (("newapps-tools", 0.68, 0.99), ("newapps-text", 0.75, 1.00)):
        check(f"Unseen apps ({suite}), untrained 4B clean success",
              share(episodes(f"r4-base-{suite}"), lambda m: m["clean_success"]), base, 0.006)
        check(f"Unseen apps ({suite}), round 4 clean success",
              share(episodes(f"r4-rl4-{suite}"), lambda m: m["clean_success"]), ours, 0.006)

    # 2. Made-up tool arguments on new tasks in the training apps.
    check("Episodes with a made-up tool argument, untrained 4B",
          share(episodes("r4-base-apps-text"), lambda m: m["fabricated"] > 0), 0.23, 0.006)
    check("Episodes with a made-up tool argument, round 4",
          share(episodes("r4-rl4-apps-text"), lambda m: m["fabricated"] > 0), 0.04, 0.006)

    # 2b. The 4-bit GGUF under llama.cpp, on the same tasks as the full model.
    for suite, full, q4 in (("tools-real", 0.99, 1.00), ("text-real", 0.98, 0.97), ("newapps-text", 1.00, 0.97)):
        check(f"4-bit check ({suite}), round 4 BF16 clean success",
              share(episodes(f"r4-rl4-{suite}"), lambda m: m["clean_success"]), full, 0.006)
        check(f"4-bit check ({suite}), round 4 4-bit clean success",
              share(episodes(f"r4-rl4-q4-{suite}"), lambda m: m["clean_success"]), q4, 0.006)

    # 3. When2Call against Nemotron 3 Super 120B, on the same 300 items, same judge.
    ours = when2call(EXT / "when2call-llm_judge-alfred-rl4_no-think-h2/responses.jsonl")
    sup = when2call(BASELINE_EXT / "when2call-llm_judge-nvidia_nemotron-3-super-120b-a12b/responses.jsonl")
    base = when2call(BASELINE_EXT / "when2call-llm_judge-nemotron-3-nano-4b_no-think/responses.jsonl")
    items = sorted(ours)
    check("When2Call macro F1, round 4", macro_f1(ours, items), 0.70)
    check("When2Call macro F1, Nemotron 3 Super", macro_f1(sup, sorted(sup)), 0.60)
    for lab, mine, theirs in (("tool_call", 0.96, 0.96), ("request_for_info", 0.65, 0.57), ("cannot_answer", 0.50, 0.30)):
        check(f"When2Call {lab}, round 4", rate(ours, items, lab), mine)
        check(f"When2Call {lab}, Nemotron 3 Super", rate(sup, sorted(sup), lab), theirs)
    for name, fn in (("macro F1", macro_f1), ("asked for missing info", lambda d, s: rate(d, s, "request_for_info")),
                     ("said it can't", lambda d, s: rate(d, s, "cannot_answer"))):
        diff, lo, hi = paired(ours, sup, fn)
        checks.append((f"Round 4 beats Super on {name}: {diff:+.3f}, 95% CI [{lo:+.3f}, {hi:+.3f}]", lo, 0.0, lo > 0))

    # 4. The collapse and recovery of asking (When2Call, asked for missing information).
    round1 = json.loads((EXT / "when2call-llm_judge-alfred-rl_no-think-h2/summary.json").read_text())
    check("Asked for missing info, untrained 4B", rate(base, sorted(base), "request_for_info"), 0.59)
    check("Asked for missing info, round 1", round1["by_label"]["request_for_info"]["value"], 0.03)
    check("Asked for missing info, round 4", rate(ours, items, "request_for_info"), 0.65)

    width = max(len(c[0]) for c in checks)
    for claim, got, want, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {claim:<{width}}  {got:.3f} (stated {want:.2f})")
    failed = [c for c in checks if not c[3]]
    print(f"\n{len(checks) - len(failed)} of {len(checks)} checks pass.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
