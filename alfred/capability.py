"""Helpers for scripts/run_capability_test.sh.

    python -m alfred.capability pick-mode RUNS_DIR SMOKE_LABEL   # prints native or json
    python -m alfred.capability pick-openai-model                 # prints a model id your OPENAI_API_KEY can use
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .report import load_rows

# Strong general models first; dated snapshots of these names also count.
OPENAI_PREFERENCE = ["gpt-5.4", "gpt-5.5", "gpt-5.6-terra", "gpt-5.1", "gpt-5", "gpt-4.1", "gpt-4o"]


def _latest_run(runs_dir: Path, label: str) -> Path | None:
    hits = sorted(d for d in runs_dir.glob(f"*-{label}") if (d / "results.jsonl").exists())
    return hits[-1] if hits else None


def _smoke_stats(run: Path | None) -> dict:
    if run is None:
        return {"success": 0.0, "invalid_rate": 1.0, "episodes": 0}
    rows = load_rows(run)
    scored = [r for r in rows if r.get("metrics")]
    calls = sum(r["metrics"]["calls"] for r in scored)
    return {"success": sum(r["metrics"]["success"] for r in scored) / max(len(rows), 1),
            "invalid_rate": sum(r["metrics"]["invalid_calls"] for r in scored) / max(calls, 1),
            "episodes": len(rows)}


def pick_mode(runs_dir: Path, label: str) -> tuple[str, str]:
    """Native tool calling unless JSON mode clearly did better in the smoke test."""
    native = _smoke_stats(_latest_run(runs_dir, f"{label}-native"))
    as_json = _smoke_stats(_latest_run(runs_dir, f"{label}-json"))
    mode = "json" if as_json["success"] >= native["success"] + 0.15 else "native"
    why = (f"native: {native['success']:.0%} success, {native['invalid_rate']:.0%} invalid calls; "
           f"json: {as_json['success']:.0%} success, {as_json['invalid_rate']:.0%} invalid calls")
    return mode, why


def choose_openai_model(available: set[str]) -> str | None:
    for name in OPENAI_PREFERENCE:
        if name in available:
            return name
        dated = sorted(i for i in available if re.fullmatch(re.escape(name) + r"-\d{4}-\d{2}-\d{2}", i))
        if dated:
            return dated[-1]
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m alfred.capability")
    sub = p.add_subparsers(dest="cmd", required=True)
    pm = sub.add_parser("pick-mode")
    pm.add_argument("runs_dir")
    pm.add_argument("label")
    sub.add_parser("pick-openai-model")
    a = p.parse_args(argv)

    if a.cmd == "pick-mode":
        mode, why = pick_mode(Path(a.runs_dir), a.label)
        print(why, file=sys.stderr)
        print(mode)
        return 0

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("OPENAI_API_KEY isn't set.", file=sys.stderr)
        return 1
    from openai import OpenAI
    try:
        available = {m.id for m in OpenAI(api_key=key, max_retries=2, timeout=30).models.list()}
    except Exception as e:  # noqa: BLE001 - explain and let the script skip the reference run
        print(f"Couldn't list models with that key ({type(e).__name__}: {str(e)[:200]}).", file=sys.stderr)
        return 1
    model = choose_openai_model(available)
    if model is None:
        print("None of the preferred models is available to this key. Set REF_MODEL to pick one.", file=sys.stderr)
        return 1
    print(model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
