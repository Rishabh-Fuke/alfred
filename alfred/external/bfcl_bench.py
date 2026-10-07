"""BFCL multi-turn (Berkeley Function Calling Leaderboard): does the model ask or decline when it should?

    python -m alfred.external.bfcl_bench --model nemotron-3-nano-4b --base-url vllm --no-think --out runs/external

Three 200-task categories of multi-turn tool use over stateful apps (file system, messaging, travel, trading):
- multi_turn_base: everything needed is available (the control);
- multi_turn_miss_param: a needed detail is missing and can't be looked up, so the model should ask;
- multi_turn_miss_func: no available tool can do it, so the model should say so instead of forcing a call.

A task only counts if the model also finishes it once the user supplies the detail or the tool appears in a later
turn, so these scores combine judgment with ordinary tool use. Compare them with multi_turn_base.

Uses BFCL's own runner and checker; this module only registers the model as an OpenAI-compatible
function-calling model (with optional extra_body) and summarizes the scores. Needs `bfcl-eval`
(`pip install -e ".[external]"`).
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from .common import NO_THINK, pct, slug

CATEGORIES = ("multi_turn_base", "multi_turn_miss_param", "multi_turn_miss_func")
NAMES = {"multi_turn_base": "Multi-turn tasks with everything available (control)",
         "multi_turn_miss_param": "Missing detail: asked instead of guessing",
         "multi_turn_miss_func": "Missing tool: said so instead of forcing a call"}


def register(name: str, model: str, extra_body: dict | None) -> None:
    from bfcl_eval.constants.model_config import MODEL_CONFIG_MAPPING, ModelConfig
    from bfcl_eval.model_handler.api_inference.openai_completion import OpenAICompletionsHandler

    class Handler(OpenAICompletionsHandler):
        def _query_FC(self, inference_data: dict):  # noqa: N802 - BFCL's method name
            # The parent sends store=False, which open-model servers don't all accept.
            message, tools = inference_data["message"], inference_data["tools"]
            inference_data["inference_input_log"] = {"message": repr(message), "tools": tools}
            kwargs = {"messages": message, "model": self.model_name, "temperature": self.temperature}
            if tools:
                kwargs["tools"] = tools
            if extra_body:
                kwargs["extra_body"] = extra_body
            return self.generate_with_backoff(**kwargs)

    MODEL_CONFIG_MAPPING[name] = ModelConfig(
        model_name=model, display_name=name, url="", org="", license="", model_handler=Handler,
        input_price=None, output_price=None, is_fc_model=True, underscore_to_dot=True)


def read_scores(score_dir: Path, name: str) -> dict:
    out = {}
    for cat in CATEGORIES:
        hits = sorted(score_dir.rglob(f"*{cat}_score.json"))
        hits = [h for h in hits if name.replace("/", "_") in str(h)]
        if hits:
            head = json.loads(hits[-1].read_text().splitlines()[0])
            out[cat] = {"value": head.get("accuracy"), "correct": head.get("correct_count"),
                        "total": head.get("total_count")}
    return out


def render(label: str, scores: dict) -> str:
    lines = [f"## BFCL multi-turn: {label}", "", "| Category | Accuracy |", "|---|---|"]
    for cat in CATEGORIES:
        s = scores.get(cat)
        lines.append(f"| {NAMES[cat]} | " + ("n/a" if not s else f"{s['value']:.0%} ({s['correct']}/{s['total']})")
                     + " |")
    return "\n".join(lines) + "\n"


def run(a) -> Path:
    label = a.label or f"{a.model}{' no-think' if a.no_think else ''}"
    # BFCL stores results under the model name with "/" turned into "_" and reverses that when scoring, so the
    # registered name must contain neither.
    name = "alfred-" + re.sub(r"[^A-Za-z0-9.-]+", "-", label).strip("-")
    out = (Path(a.out) / f"bfcl-{slug(label)}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps({**vars(a), "api_key": None}, indent=2))

    from ..eval import resolve_endpoint
    url, key = resolve_endpoint(a.base_url, a.api_key)
    os.environ["BFCL_PROJECT_ROOT"] = str(out)  # must be set before bfcl_eval is imported
    os.environ["OPENAI_BASE_URL"], os.environ["OPENAI_API_KEY"] = url, key  # read by BFCL's OpenAI handler
    register(name, a.model, NO_THINK if a.no_think else None)
    from bfcl_eval.__main__ import cli

    categories = ",".join(a.categories.split(","))
    if not a.skip_generate:
        cli(["generate", "--model", name, "--test-category", categories, "--num-threads", str(a.workers),
             "--temperature", str(a.temperature), "--result-dir", "result"], standalone_mode=False)
    cli(["evaluate", "--model", name, "--test-category", categories, "--result-dir", "result",
         "--score-dir", "score"], standalone_mode=False)
    scores = read_scores(out / "score", name)
    (out / "summary.json").write_text(json.dumps(scores, indent=2))
    text = render(label, scores)
    (out / "summary.md").write_text(text)
    print(text)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.external.bfcl_bench", description=__doc__.split("\n")[0])
    p.add_argument("--model", required=True)
    p.add_argument("--base-url", default="vllm", help="URL or shortcut: ollama, vllm, tokenfactory, openai.")
    p.add_argument("--api-key", default=None)
    p.add_argument("--no-think", action="store_true")
    p.add_argument("--temperature", type=float, default=0.001, help="BFCL's default.")
    p.add_argument("--categories", default=",".join(CATEGORIES))
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--skip-generate", action="store_true", help="Only re-score existing results.")
    p.add_argument("--out", default="runs/external")
    p.add_argument("--label", default="")
    return run(p.parse_args(argv))


if __name__ == "__main__":
    main()
