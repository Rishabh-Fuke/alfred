import json
from types import SimpleNamespace

import pytest

from alfred import eval as alfred_eval
from alfred.agents.llm import _PARAM_FIXES, chat_create
from alfred.capability import choose_openai_model, pick_mode
from alfred.report import load_rows, reliability, render_markdown, summarize


class _PickyClient:
    """Rejects max_tokens and non-default temperature, like some hosted reasoning models."""

    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if "max_tokens" in kw:
            raise Exception("Unsupported parameter: 'max_tokens' is not supported with this model. "
                            "Use 'max_completion_tokens' instead.")
        if "temperature" in kw:
            raise Exception("Unsupported value: 'temperature' does not support 0.7 with this model.")
        return "ok"


def test_chat_create_adapts_and_remembers():
    _PARAM_FIXES.pop("picky", None)
    client = _PickyClient()
    assert chat_create(client, "picky", messages=[], max_tokens=10, temperature=0.7) == "ok"
    assert client.calls[-1] == {"model": "picky", "messages": [], "max_completion_tokens": 10}
    n = len(client.calls)
    assert chat_create(client, "picky", messages=[], max_tokens=10, temperature=0.7) == "ok"
    assert len(client.calls) == n + 1  # learned: no extra failed calls the second time


def test_chat_create_drops_template_options_a_host_rejects():
    _PARAM_FIXES.pop("no-kwargs", None)
    calls = []

    def create(**kw):
        calls.append(kw)
        if "extra_body" in kw:
            raise Exception("400: unknown field 'chat_template_kwargs'")
        return "ok"

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    body = {"chat_template_kwargs": {"enable_thinking": False}}
    assert chat_create(client, "no-kwargs", messages=[], max_tokens=5, extra_body=body) == "ok"
    assert "extra_body" not in calls[-1]


def test_provider_shortcuts_and_keys(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "nb-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert alfred_eval.resolve_endpoint("tokenfactory", None) == ("https://api.tokenfactory.nebius.com/v1/", "nb-test")
    assert alfred_eval.resolve_endpoint("openai", None) == ("https://api.openai.com/v1", "sk-test")
    # Keys never go to a different host.
    assert alfred_eval.resolve_endpoint("ollama", None) == ("http://localhost:11434/v1", "local")
    assert alfred_eval.resolve_endpoint("http://x:1/v1", None) == ("http://x:1/v1", "local")
    assert alfred_eval.resolve_endpoint("http://x:1/v1", "k") == ("http://x:1/v1", "k")
    a = alfred_eval.parse_args(["--no-think", "--extra-body", '{"top_k": 20}'])
    assert alfred_eval.extra_body(a) == {"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}}


def test_chat_create_reraises_other_errors():
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: (_ for _ in ()).throw(RuntimeError("connection refused")))))
    with pytest.raises(RuntimeError):
        chat_create(client, "down", messages=[], max_tokens=5)


def test_choose_openai_model():
    assert choose_openai_model({"gpt-4o", "gpt-5.4", "gpt-5.5"}) == "gpt-5.4"
    assert choose_openai_model({"gpt-4o", "gpt-5.4-2026-03-05", "gpt-5.4-mini"}) == "gpt-5.4-2026-03-05"
    assert choose_openai_model({"whisper-1"}) is None


def test_pick_mode_and_reliability(tmp_path):
    runs = tmp_path / "runs"
    alfred_eval.main(["--agent", "oracle", "--worlds", "1", "--out", str(runs), "--label", "smoke-m-native"])
    alfred_eval.main(["--agent", "false-claim", "--worlds", "1", "--out", str(runs), "--label", "smoke-m-json"])
    assert pick_mode(runs, "smoke-m")[0] == "native"
    alfred_eval.main(["--agent", "oracle", "--worlds", "1", "--out", str(runs), "--label", "smoke-n-json"])
    alfred_eval.main(["--agent", "false-claim", "--worlds", "1", "--out", str(runs), "--label", "smoke-n-native"])
    assert pick_mode(runs, "smoke-n")[0] == "json"
    assert pick_mode(runs, "smoke-missing")[0] == "native"

    run = alfred_eval.main(["--agent", "oracle", "--worlds", "2", "--samples", "2", "--out", str(runs)])
    rel = reliability(load_rows(run))
    assert rel["lookup"]["all_tries"] == 1.0 and rel["lookup"]["meets_bar"] is True
    assert rel["ambiguous"]["meets_bar"] is None
    assert "## Reliability by category" in render_markdown(summarize(load_rows(run)))


def test_all_tries_is_stricter_than_the_average():
    rows = [{"task_id": "t1", "category": "action", "metrics": {"success": s}} for s in (True, True, False)]
    rows += [{"task_id": "t2", "category": "action", "metrics": {"success": True}} for _ in range(3)]
    rel = reliability(rows)["action"]
    assert rel["all_tries"] == 0.5 and round(rel["average"], 2) == 0.83 and rel["meets_bar"] is False
