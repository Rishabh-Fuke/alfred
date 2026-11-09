"""Turning reasoning off: each server gets the option it honors. Requests stop at a fake transport, so no model runs.

Ollama ignores chat_template_kwargs and honors reasoning_effort. vLLM and llama.cpp honor the template option.
"""
import json
import sys
import time
from types import SimpleNamespace

import httpx
import openai
import pytest

from alfred import eval as alfred_eval
from alfred.app import server as app_server
from alfred.app.live import Pending
from alfred.external import agentdojo_bench, bfcl_bench, when2call
from alfred.realism import build as realism_build
from alfred.realism.kinds import KINDS

EFFORT = {"reasoning_effort": "none"}
TEMPLATE = {"chat_template_kwargs": {"enable_thinking": False}}
LLAMA_CPP = "http://localhost:8090/v1"  # where the training notebook runs llama-server
ENDPOINTS = [("ollama", EFFORT), ("http://localhost:11434/v1", EFFORT), ("vllm", TEMPLATE), (LLAMA_CPP, TEMPLATE)]
REPLY = {"id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "m",
         "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def sent_options(body: dict) -> dict:
    return {k: body[k] for k in (*EFFORT, *TEMPLATE) if k in body}


@pytest.fixture
def wire(monkeypatch):
    """Every OpenAI client made during the test answers here, whatever its URL, so port 11434 needs no Ollama (and a
    real Ollama on this computer can't get involved). Returns the requests sent, as (url, JSON body)."""
    real, sent = openai.OpenAI, []

    def answer(request):
        sent.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json=REPLY)

    monkeypatch.setattr(openai, "OpenAI",
                        lambda **kw: real(http_client=httpx.Client(transport=httpx.MockTransport(answer)), **kw))
    monkeypatch.setattr(alfred_eval, "_clients", {})  # get_client keeps clients between runs
    return sent


# --- The choice itself

@pytest.mark.parametrize("base_url", ["ollama", "http://localhost:11434/v1", "http://127.0.0.1:11434/v1/",
                                      "http://[::1]:11434/v1", "http://192.168.1.20:11434/v1",
                                      "http://localhost:11434"])
def test_ollama_gets_reasoning_effort(base_url):
    assert alfred_eval.no_think_options(base_url) == EFFORT


@pytest.mark.parametrize("base_url", [
    "vllm", "tokenfactory", "openai", "http://localhost:8000/v1", "http://localhost:8090/v1",  # vLLM, hosted, llama.cpp
    "http://localhost:1234/v1", "https://api.tokenfactory.nebius.com/v1/",
    "http://ollama.example.com:8000/v1", "http://example.com/11434/v1", "http://x:114340/v1",  # only look like Ollama
    "http://localhost:not-a-port/v1", "http://[::1/v1"])  # malformed: the request will say so, not this choice
def test_everything_else_gets_the_template_option(base_url):
    assert alfred_eval.no_think_options(base_url) == TEMPLATE


def test_callers_cannot_change_each_others_options():
    alfred_eval.no_think_options("vllm")["chat_template_kwargs"]["enable_thinking"] = True
    assert alfred_eval.no_think_options("vllm") == TEMPLATE


# --- Every caller, as the server sees it

def _run_eval(base_url, off, tmp):
    alfred_eval.main(["--models", "m", "--base-url", base_url, "--worlds", "1", "--limit", "1", "--out", str(tmp)]
                     + (["--no-think"] if off else []))


def _run_app(base_url, off, tmp):
    session = app_server.App(app_server.parse_args(
        ["--model", "m", "--base-url", base_url, "--memory", str(tmp / "memory.json"), "--no-browser"]
        + ([] if off else ["--thinking"]))).session
    session.start("What's on my calendar on Friday?")
    deadline = time.time() + 20
    while session.running and time.time() < deadline:
        time.sleep(0.01)
    assert not session.running


def _run_realism(base_url, off, tmp):
    realism_build.main(["--base-url", base_url, "--kinds", next(iter(KINDS)), "--per-kind", "1", "--workers", "1",
                        "--out", str(tmp / "pack.jsonl.gz")] + ([] if off else ["--think"]))


def _run_when2call(base_url, off, tmp):
    item = {"uuid": "a", "question": "Book me a hotel.", "correct_answer": "cannot_answer", "tools": []}
    (tmp / "when2call_test_llm_judge.jsonl").write_text(json.dumps(item) + "\n")
    when2call.main(["--model", "m", "--base-url", base_url, "--judge", "rules", "--data-dir", str(tmp),
                    "--out", str(tmp / "out"), "--workers", "1"] + (["--no-think"] if off else []))


CALLERS = {"eval": _run_eval, "app": _run_app, "realism": _run_realism, "when2call": _run_when2call}


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize("base_url, want", ENDPOINTS)
def test_every_request_carries_the_option_its_server_honors(wire, tmp_path, caller, base_url, want):
    CALLERS[caller](base_url, True, tmp_path)
    assert wire
    assert [sent_options(body) for _, body in wire] == [want] * len(wire)


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize("base_url", ["ollama", "vllm"])
def test_reasoning_left_on_sends_neither_option(wire, tmp_path, caller, base_url):
    CALLERS[caller](base_url, False, tmp_path)
    assert wire
    assert [sent_options(body) for _, body in wire] == [{}] * len(wire)


def test_eval_checks_reachability_with_the_same_option(wire, tmp_path):
    _run_eval("ollama", True, tmp_path)
    assert wire[0][1]["messages"] == [{"role": "user", "content": "Reply with: ok"}]  # the preflight, then an episode
    assert len(wire) > 1


@pytest.mark.parametrize("base_url", ["ollama", "vllm"])
def test_app_keeps_its_sampling_beside_the_option(wire, tmp_path, base_url):
    _run_app(base_url, True, tmp_path)
    assert wire and all(body["top_p"] == 0.95 for _, body in wire)


def test_polish_goes_to_token_factory_even_when_the_agent_runs_on_ollama(wire, tmp_path, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "nb-test")
    session = app_server.App(app_server.parse_args(["--model", "m", "--base-url", "ollama", "--cloud", "--no-browser",
                                                    "--memory", str(tmp_path / "memory.json")])).session
    email = {"kind": "email", "action": {"subject": "s", "body": "b"}}
    session.pending = Pending(id="p", kind="approval", payload=email)
    session.polish("p")
    url, body = wire[-1]
    assert url.startswith("https://api.tokenfactory.nebius.com/") and sent_options(body) == TEMPLATE


@pytest.mark.parametrize("base_url, want", ENDPOINTS)
def test_agentdojo_client_sends_it(wire, monkeypatch, base_url, want):
    """AgentDojo itself isn't needed: only the client this harness hands it."""
    monkeypatch.setitem(sys.modules, "agentdojo.agent_pipeline", SimpleNamespace(
        AgentPipeline=SimpleNamespace(from_config=lambda config: config), PipelineConfig=SimpleNamespace))
    monkeypatch.setitem(sys.modules, "agentdojo.agent_pipeline.agent_pipeline",
                        SimpleNamespace(load_system_message=lambda name: "system"))
    monkeypatch.setitem(sys.modules, "agentdojo.agent_pipeline.llms.openai_llm", SimpleNamespace(
        OpenAILLM=lambda client, model, temperature: SimpleNamespace(client=client)))
    config = agentdojo_bench.build_pipeline("m", base_url, None, True, 0.0, "local-test")
    config.llm.client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    assert [sent_options(body) for _, body in wire] == [want]


@pytest.mark.parametrize("base_url, want", ENDPOINTS)
def test_bfcl_model_is_registered_with_it(monkeypatch, tmp_path, base_url, want):
    """BFCL itself isn't needed: only what the harness registers with it."""
    for var in ("BFCL_PROJECT_ROOT", "OPENAI_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.setenv(var, "")  # the run sets these for BFCL; this puts them back afterwards
    registered = []
    monkeypatch.setattr(bfcl_bench, "register", lambda name, model, extra: registered.append(extra))
    monkeypatch.setitem(sys.modules, "bfcl_eval.__main__", SimpleNamespace(cli=lambda *args, **kw: None))
    bfcl_bench.main(["--model", "m", "--base-url", base_url, "--no-think", "--out", str(tmp_path)])
    assert registered == [want]


# --- --no-think together with --extra-body

@pytest.mark.parametrize("given, base_url, want", [
    ({"top_k": 20}, "ollama", {"top_k": 20, **EFFORT}),
    ({"top_k": 20}, "vllm", {"top_k": 20, **TEMPLATE}),
    # Another template option given by hand survives, as it always has.
    ({"chat_template_kwargs": {"foo": 1}}, "vllm", {"chat_template_kwargs": {"foo": 1, "enable_thinking": False}}),
    # --no-think wins over a setting that contradicts it, on either kind of server.
    ({"chat_template_kwargs": {"enable_thinking": True}}, "vllm", TEMPLATE),
    ({"reasoning_effort": "high"}, "ollama", EFFORT),
])
def test_no_think_joins_extra_body(given, base_url, want):
    a = alfred_eval.parse_args(["--no-think", "--base-url", base_url, "--extra-body", json.dumps(given)])
    assert alfred_eval.extra_body(a) == want


@pytest.mark.parametrize("base_url", ["ollama", "vllm"])
def test_without_no_think_extra_body_is_left_alone(base_url):
    assert alfred_eval.extra_body(alfred_eval.parse_args(["--base-url", base_url])) is None
    a = alfred_eval.parse_args(["--base-url", base_url, "--extra-body", '{"top_k": 20}'])
    assert alfred_eval.extra_body(a) == {"top_k": 20}
