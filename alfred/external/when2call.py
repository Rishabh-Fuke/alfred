"""NVIDIA When2Call: does the model call a tool, ask a follow-up, or say it can't, when it should?

    python -m alfred.external.when2call --model nemotron-3-nano-4b --base-url vllm --no-think \
        --judge-model nvidia/nemotron-3-super-120b-a12b --judge-base-url tokenfactory --out runs/external

Follows the benchmark's LLM-as-judge protocol: the model answers each question with that item's tools
available. A reply with a tool call counts as a tool call; any other reply is classified by a judge model using
When2Call's own judge prompt. `--subset llm_judge` (default) is the official 300-item set, balanced across
tool_call, request_for_info and cannot_answer; `--subset mcq` runs the 3,652 questions of the multiple-choice
set the same way, for tighter intervals. `--judge rules` skips the judge model and uses keyword rules (rougher,
free, useful for smoke tests).

Data and judge prompt: https://github.com/NVIDIA/When2Call (Apache-2.0; data CC BY 4.0).
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

from ..agents.llm import chat_create, strip_think
from ..matching import INABILITY
from .common import (FINISH_STATS, HARNESS, NO_THINK, bootstrap, client_for, finish_note, offered_tools, pct, read_jsonl,
                     read_finish_as_reply, slug)

DATA_URL = "https://raw.githubusercontent.com/NVIDIA/When2Call/main/data/test/when2call_test_{subset}.jsonl"
LABELS = ("tool_call", "request_for_info", "cannot_answer")
PREDICTIONS = ("direct_answer", *LABELS)

# Verbatim from When2Call's evaluation/llm_as_a_judge/run_openai_judge.py (Apache-2.0), so scores are comparable.
JUDGE_PROMPT = """You are an expert at classifying responses from AI models.

Your task is to classify AI model's response into one of the following four categories:
(1) direct_answer: The AI model responded to the User's questions based on it's existing knowledge, without requesting any additional information or using external tools.
(2) tool_call: The AI model decided to use a tool from the provided one's to help answer the question.
(3) request_for_info: The AI model requested for some additional information from the User.
(4) cannot_answer: The AI model refused to answer the User's questions by acknowledging the lack of required capabilities.

*You should not judge whether the AI model's response is accurate or not. Only provide the classification of the response into one of these four categories: [direct_answer, tool_call, request_for_info, cannot_answer]*

- The tools available to the AI model are given in <AVAILABLE_TOOLS> </AVAILABLE_TOOLS>
- The User's question is provided in <USER_QUESTION> </USER_QUESTION>
- The AI model's response is provided in <AI_MODEL_RESPONSE> </AI_MODEL_RESPONSE> which may or may not invlove a tool call

<AVAILABLE_TOOLS>
{}
</AVAILABLE_TOOLS>

<USER_QUESTION>
{}
</USER_QUESTION>

<AI_MODEL_RESPONSE>
{}
</AI_MODEL_RESPONSE>

Please provide the classification in the following json format by filling in the placeholders in < >:
{{"classification": "<one of `direct_answer`, `tool_call`, `request_for_info`, `cannot_answer`>"}}

Respond only in the prescribed json format with the placeholders filled in."""


def load_items(subset: str, data_dir: Path) -> list[dict]:
    path = data_dir / f"when2call_test_{subset}.jsonl"
    if not path.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        with urlopen(DATA_URL.format(subset=subset), timeout=120) as r:  # noqa: S310 - fixed GitHub URL
            path.write_bytes(r.read())
    return read_jsonl(path)


def openai_tools(item: dict) -> list[dict]:
    """The item's tools in OpenAI format, converted exactly like When2Call's run_openai_inference.py."""
    tools = []
    for raw in item["tools"]:
        t = json.loads(raw.replace("float", "string").replace("integer", "string").replace("dict", "object")
                       .replace("tuple", "object"))
        t["name"] = t["name"].replace(".", "_")
        t["parameters"]["type"] = "object"
        for p in t["parameters"]["properties"]:
            t["parameters"]["properties"][p]["type"] = "string"
        tools.append({"type": "function", "function": t})
    return tools


def respond(client, model: str, item: dict, extra_body: dict | None, temperature: float, max_tokens: int) -> dict:
    tools = openai_tools(item)
    kw = dict(messages=[{"role": "user", "content": item["question"]}], temperature=temperature, max_tokens=max_tokens)
    if tools:
        kw["tools"] = tools
    if extra_body:
        kw["extra_body"] = extra_body
    msg = chat_create(client, model, **kw).choices[0].message
    read_finish_as_reply(msg, offered_tools(tools))
    if msg.tool_calls:
        tc = msg.tool_calls[0].function
        return {"text": json.dumps({"name": tc.name, "arguments": tc.arguments}), "tool_call": True}
    return {"text": strip_think(msg.content), "tool_call": False}


_REQUEST = re.compile(r"\?\s*$|\b(could you|can you|please (?:provide|share|tell|specify|confirm)|which|what is the|"
                      r"what's the|do you want|would you like|let me know)\b", re.I | re.M)


def classify_by_rules(text: str) -> str:
    if INABILITY.search(text):
        return "cannot_answer"
    if _REQUEST.search(text):
        return "request_for_info"
    return "direct_answer"


def classify_by_judge(client, judge_model: str, item: dict, text: str) -> str:
    prompt = JUDGE_PROMPT.format([json.loads(t) for t in item["tools"]], item["question"], text)
    out = chat_create(client, judge_model, messages=[{"role": "user", "content": prompt}], temperature=0,
                      max_tokens=2048).choices[0].message.content or ""
    out = strip_think(out)
    m = re.search(r'"classification"\s*:\s*"(\w+)"', out) or re.search(r"\b(" + "|".join(PREDICTIONS) + r")\b", out)
    return m.group(1) if m and m.group(1) in PREDICTIONS else "direct_answer"


def summarize(rows: list[dict]) -> dict:
    out = {"items": len(rows), "by_label": {}, "confusion": {}}
    for label in LABELS:
        mine = [r for r in rows if r["correct_answer"] == label]
        out["by_label"][label] = bootstrap([float(r["predicted"] == label) for r in mine])
        out["confusion"][label] = dict(Counter(r["predicted"] for r in mine))
    f1s = []
    for label in LABELS:
        tp = sum(r["predicted"] == label and r["correct_answer"] == label for r in rows)
        fp = sum(r["predicted"] == label and r["correct_answer"] != label for r in rows)
        fn = sum(r["predicted"] != label and r["correct_answer"] == label for r in rows)
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    out["macro_f1"] = sum(f1s) / len(f1s)
    out["accuracy"] = bootstrap([float(r["predicted"] == r["correct_answer"]) for r in rows])
    no_tools = [r for r in rows if not r["has_tools"]]
    out["tool_hallucination"] = bootstrap([float(r["predicted"] == "tool_call") for r in no_tools])
    return out


def render(name: str, s: dict) -> str:
    names = {"tool_call": "Called a tool when it should", "request_for_info": "Asked for missing information",
             "cannot_answer": "Said it can't when it can't"}
    lines = [f"## When2Call: {name}", "", f"{s['items']} items, macro F1 {s['macro_f1']:.2f}, accuracy {pct(s['accuracy'])}.",
             "", "| Behavior | Rate | What it did instead |", "|---|---|---|"]
    for label in LABELS:
        other = {k: v for k, v in s["confusion"][label].items() if k != label}
        lines.append(f"| {names[label]} | {pct(s['by_label'][label])} | "
                     + (", ".join(f"{k} {v}" for k, v in sorted(other.items(), key=lambda kv: -kv[1])) or "-") + " |")
    lines.append(f"| Called a tool when none was offered (lower is better) | {pct(s['tool_hallucination'])} | |")
    return "\n".join(lines) + "\n"


def run(a) -> Path:
    items = load_items(a.subset, Path(a.data_dir))
    if a.limit:
        items = items[:a.limit]
    client, _ = client_for(a.base_url, a.api_key, timeout=a.timeout)
    judge_client = None if a.judge == "rules" else client_for(a.judge_base_url, None, timeout=a.timeout)[0]
    label = a.label or f"{a.model}{' no-think' if a.no_think else ''}"
    out = Path(a.out) / f"when2call-{a.subset}-{slug(label)}-{HARNESS}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps({**vars(a), "api_key": None}, indent=2))
    path = out / "responses.jsonl"
    done = {r["uuid"] for r in read_jsonl(path)}
    lock = threading.Lock()
    extra = NO_THINK if a.no_think else None

    def one(item):
        try:
            r = respond(client, a.model, item, extra, a.temperature, a.max_tokens)
            if r["tool_call"]:
                predicted, via = "tool_call", "tool call"
            elif judge_client is None:
                predicted, via = classify_by_rules(r["text"]), "rules"
            else:
                predicted, via = classify_by_judge(judge_client, a.judge_model, item, r["text"]), a.judge_model
        except Exception as e:  # noqa: BLE001 - record and keep going
            print(f"! {item['uuid']}: {type(e).__name__}: {str(e)[:200]}")
            return
        row = {"uuid": item["uuid"], "correct_answer": item["correct_answer"], "has_tools": bool(item["tools"]),
               "question": item["question"], "response": r["text"], "predicted": predicted, "classified_by": via}
        with lock:
            with path.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    todo = [i for i in items if i["uuid"] not in done]
    print(f"When2Call {a.subset}: {len(items)} items, {len(todo)} to run -> {out}")
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        list(pool.map(one, todo))
    rows = [r for r in read_jsonl(path) if r["uuid"] in {i["uuid"] for i in items}]
    summary = summarize(rows)
    summary["harness"] = {"version": HARNESS, **FINISH_STATS}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    text = render(label, summary) + finish_note()
    (out / "summary.md").write_text(text)
    print(text)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.external.when2call", description=__doc__.split("\n")[0])
    p.add_argument("--model", required=True)
    p.add_argument("--base-url", default="vllm", help="URL or shortcut: ollama, vllm, tokenfactory, openai.")
    p.add_argument("--api-key", default=None)
    p.add_argument("--no-think", action="store_true")
    p.add_argument("--subset", default="llm_judge", choices=["llm_judge", "mcq"])
    p.add_argument("--judge", default="model", choices=["model", "rules"])
    p.add_argument("--judge-model", default="nvidia/nemotron-3-super-120b-a12b")
    p.add_argument("--judge-base-url", default="tokenfactory")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--data-dir", default=str(Path.home() / ".cache" / "alfred" / "when2call"))
    p.add_argument("--out", default="runs/external")
    p.add_argument("--label", default="", help="Name for this model setting in the output folder and report.")
    return run(p.parse_args(argv))


if __name__ == "__main__":
    main()
