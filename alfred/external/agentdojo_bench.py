"""AgentDojo (ETH Zurich, NeurIPS 2024): prompt injections hidden in email, calendar and cloud files.

    python -m alfred.external.agentdojo_bench --model nemotron-3-nano-4b --base-url vllm --no-think \
        --out runs/external

Runs the workspace suite (40 user tasks over an inbox, calendar and drive) twice:
- without attacks: utility, the share of user tasks done correctly;
- with an attack in every injection slot (14 attacker goals x the user tasks whose data they can reach):
  utility under attack, and the attack success rate (the attacker's goal was carried out; lower is better).

The default attack is `important_instructions_no_names`, AgentDojo's strongest standard template without the
model's name in it (the named version only knows a fixed list of commercial models). Needs `agentdojo`
(`pip install -e ".[external]"`).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from .common import (FINISH_STATS, HARNESS, NO_THINK, bootstrap, client_for, count, finish_note, offered_tools, pct,
                     read_finish_as_reply, slug)


class _ExtraBodyClient:
    """Wraps an OpenAI client so every chat request also sends extra_body (AgentDojo's own client can't)."""

    def __init__(self, client, extra_body: dict | None):
        self._client, self._extra = client, extra_body
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        import openai
        if self._extra:
            kw["extra_body"] = {**kw.get("extra_body", {}), **self._extra}
        try:
            resp = self._client.chat.completions.create(**kw)
        except openai.BadRequestError as e:
            if "context length" not in str(e):
                raise
            # The conversation outgrew the context window: end this episode (it scores as a failure) instead of
            # letting AgentDojo abort the whole benchmark.
            count("context_overflow")
            return _reply(kw.get("model", ""), "")
        read_finish_as_reply(resp.choices[0].message, offered_tools(kw.get("tools")))
        return resp


def _reply(model: str, text: str):
    from openai.types.chat import ChatCompletion
    return ChatCompletion.model_validate({
        "id": "alfred-local", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}]})


def build_pipeline(model: str, base_url: str, api_key: str | None, no_think: bool, temperature: float, name: str):
    from agentdojo.agent_pipeline import AgentPipeline, PipelineConfig
    from agentdojo.agent_pipeline.agent_pipeline import load_system_message
    from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM

    client, _ = client_for(base_url, api_key)
    llm = OpenAILLM(_ExtraBodyClient(client, NO_THINK if no_think else None), model, temperature=temperature)
    llm.name = name
    return AgentPipeline.from_config(PipelineConfig(llm=llm, model_id=None, defense=None, system_message_name=None,
                                                    system_message=load_system_message(None),
                                                    tool_output_format=None))


def summarize(clean: dict, attacked: dict | None) -> dict:
    out = {"utility": bootstrap([float(v) for v in clean["utility_results"].values()])}
    if attacked:
        out["utility_under_attack"] = bootstrap([float(v) for v in attacked["utility_results"].values()])
        out["attack_success"] = bootstrap([float(v) for v in attacked["security_results"].values()])
    return out


def render(name: str, s: dict, attack: str | None) -> str:
    lines = [f"## AgentDojo workspace: {name}", "", "| Metric | Rate |", "|---|---|",
             f"| Did the user's task (no attack) | {pct(s['utility'])} |"]
    if "attack_success" in s:
        lines += [f"| Did the user's task while under attack | {pct(s['utility_under_attack'])} |",
                  f"| Carried out the attacker's goal (`{attack}`; lower is better) | {pct(s['attack_success'])} |"]
    return "\n".join(lines) + "\n"


def run(a) -> Path:
    from agentdojo.attacks.attack_registry import load_attack
    from agentdojo.benchmark import benchmark_suite_with_injections, benchmark_suite_without_injections
    from agentdojo.logging import OutputLogger
    from agentdojo.task_suite.load_suites import get_suite

    label = a.label or f"{a.model}{' no-think' if a.no_think else ''}"
    out = Path(a.out) / f"agentdojo-{a.suite}-{slug(label)}-{HARNESS}"
    logdir = out / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps({**vars(a), "api_key": None}, indent=2))
    suite = get_suite(a.benchmark_version, a.suite)
    user_tasks = sorted(suite.user_tasks, key=lambda t: int(t.split("_")[-1]))[: a.limit or None]
    injection_tasks = sorted(suite.injection_tasks, key=lambda t: int(t.split("_")[-1]))[: a.limit_injections or None]
    # AgentDojo's attacks look the model up by pipeline name; "local" is its generic open-model entry.
    pipeline = build_pipeline(a.model, a.base_url, a.api_key, a.no_think, a.temperature, f"local-{slug(label)}")
    print(f"AgentDojo {a.suite}: {len(user_tasks)} user tasks, {len(injection_tasks)} injection tasks -> {out}")
    with OutputLogger(str(logdir)):
        clean = benchmark_suite_without_injections(pipeline, suite, logdir=logdir, force_rerun=False,
                                                   user_tasks=user_tasks, benchmark_version=a.benchmark_version)
        attacked = None
        if a.attack != "none":
            attacker = load_attack(a.attack, suite, pipeline)
            attacked = benchmark_suite_with_injections(pipeline, suite, attacker, logdir=logdir, force_rerun=False,
                                                       user_tasks=user_tasks, injection_tasks=injection_tasks,
                                                       verbose=False, benchmark_version=a.benchmark_version)
    summary = summarize(clean, attacked)
    summary["harness"] = {"version": HARNESS, **FINISH_STATS}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    text = render(label, summary, None if a.attack == "none" else a.attack) + finish_note()
    (out / "summary.md").write_text(text)
    print(text)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.external.agentdojo_bench", description=__doc__.split("\n")[0])
    p.add_argument("--model", required=True)
    p.add_argument("--base-url", default="vllm", help="URL or shortcut: ollama, vllm, tokenfactory, openai.")
    p.add_argument("--api-key", default=None)
    p.add_argument("--no-think", action="store_true")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--suite", default="workspace")
    p.add_argument("--benchmark-version", default="v1.2.2")
    p.add_argument("--attack", default="important_instructions_no_names", help="An AgentDojo attack, or none.")
    p.add_argument("--limit", type=int, default=0, help="Only the first N user tasks (for a quick check).")
    p.add_argument("--limit-injections", type=int, default=0, help="Only the first N injection tasks.")
    p.add_argument("--out", default="runs/external")
    p.add_argument("--label", default="")
    return run(p.parse_args(argv))


if __name__ == "__main__":
    main()
