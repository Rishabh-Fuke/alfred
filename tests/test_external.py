"""The outside-benchmark harnesses, run against a fake model server (no GPU, no network)."""
import json

import pytest

from alfred.dev.fake_server import serve_fake
from alfred.external import summary as ext_summary
from alfred.external import when2call

TOOL = json.dumps({"name": "weather.get", "description": "Weather for a city.",
                   "parameters": {"type": "dict", "required": ["city"],
                                  "properties": {"city": {"type": "string", "description": "City"}}}})
ITEMS = [
    {"uuid": "a", "question": "Weather in Paris?", "correct_answer": "tool_call", "tools": [TOOL]},
    {"uuid": "b", "question": "What's the weather?", "correct_answer": "request_for_info", "tools": [TOOL]},
    {"uuid": "c", "question": "Book me a flight.", "correct_answer": "cannot_answer", "tools": [TOOL]},
    {"uuid": "d", "question": "Book me a hotel.", "correct_answer": "cannot_answer", "tools": []},
]


def policy(req):
    text = req["messages"][-1]["content"]
    if text.startswith("You are an expert at classifying"):  # the judge
        if "Which city" in text:
            return {"content": '{"classification": "request_for_info"}'}
        return {"content": 'Sure: {"classification": "cannot_answer"}'}
    if text == "Weather in Paris?":
        return {"tool_call": ("weather_get", {"city": "Paris"})}
    if text == "What's the weather?":
        return {"content": "Which city do you mean?"}
    return {"content": "<think>no tool for that</think>Sorry, I can't book travel."}


@pytest.mark.parametrize("judge", ["model", "rules"])
def test_when2call_scores_each_behavior(tmp_path, judge):
    data = tmp_path / "data"
    data.mkdir()
    (data / "when2call_test_llm_judge.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ITEMS))
    server, url = serve_fake(policy)
    try:
        out = when2call.main(["--model", "m", "--base-url", url, "--judge", judge, "--judge-model", "j",
                              "--judge-base-url", url, "--data-dir", str(data), "--out", str(tmp_path / "out"),
                              "--no-think", "--workers", "2"])
    finally:
        server.shutdown()
    s = json.loads((out / "summary.json").read_text())
    assert s["accuracy"]["value"] == 1.0 and s["macro_f1"] == 1.0
    assert s["tool_hallucination"]["value"] == 0.0
    model_calls = [r for r in server.requests if not r["messages"][-1]["content"].startswith("You are an expert")]
    assert all(r["chat_template_kwargs"] == {"enable_thinking": False} for r in model_calls)
    assert model_calls[0]["tools"][0]["function"]["name"] == "weather_get"  # dots replaced, like the official script
    # Re-running skips finished items.
    server2, url2 = serve_fake(policy)
    try:
        when2call.main(["--model", "m", "--base-url", url2, "--judge", judge, "--judge-model", "j",
                        "--judge-base-url", url2, "--data-dir", str(data), "--out", str(tmp_path / "out"), "--no-think"])
    finally:
        server2.shutdown()
    assert server2.requests == []
    assert "When2Call" in ext_summary.collect(tmp_path / "out")


def test_agentdojo_harness_runs_end_to_end(tmp_path):
    pytest.importorskip("agentdojo")
    from alfred.external import agentdojo_bench

    server, url = serve_fake(lambda req: {"content": "Done."})
    try:
        out = agentdojo_bench.main(["--model", "m", "--base-url", url, "--limit", "1", "--limit-injections", "1",
                                    "--out", str(tmp_path)])
    finally:
        server.shutdown()
    s = json.loads((out / "summary.json").read_text())
    assert {"utility", "utility_under_attack", "attack_success"} <= set(s)


# --- Alfred's finish tool on benchmarks that don't offer it

def _msg(content=None, calls=()):
    from openai.types.chat import ChatCompletionMessage
    return ChatCompletionMessage.model_validate({
        "role": "assistant", "content": content,
        "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                       for i, (n, a) in enumerate(calls)] or None})


def test_finish_call_is_read_as_the_final_reply():
    from alfred.external.common import read_finish_as_reply
    m = _msg(calls=[("finish", {"status": "cannot_do", "message": "I can't book flights."})])
    assert read_finish_as_reply(m, {"search_flights"}) and m.tool_calls is None
    assert m.content == "I can't book flights."
    m = _msg("ok", calls=[("search", {"q": "x"}), ("finish", {"message": "done"})])
    assert not read_finish_as_reply(m, {"search"}) and [c.function.name for c in m.tool_calls] == ["search"]
    m = _msg(calls=[("finish", {"message": "done"})])
    assert not read_finish_as_reply(m, {"finish"}) and m.tool_calls  # a benchmark that has finish keeps it
    m = _msg("Sure.\n<tool_call>\n<function=finish>\n<parameter=message>\nIt's Friday.\n</parameter>\n</function>\n"
             "</tool_call>")
    assert read_finish_as_reply(m, set()) and m.content == "Sure.\n\nIt's Friday."
    m = _msg("Plain answer.")
    assert not read_finish_as_reply(m, {"x"}) and m.content == "Plain answer."


def test_agentdojo_client_ends_an_episode_that_outgrows_the_context():
    import httpx
    import openai
    from types import SimpleNamespace
    from alfred.external.agentdojo_bench import _ExtraBodyClient
    from alfred.external.common import FINISH_STATS

    def create(**kw):
        if kw["messages"][0]["content"] == "long":
            raise openai.BadRequestError("This model's maximum context length is 32768 tokens.",
                                         response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
                                         body=None)
        from openai.types.chat import ChatCompletion
        return ChatCompletion.model_validate({"id": "1", "object": "chat.completion", "created": 0, "model": "m",
                                              "choices": [{"index": 0, "finish_reason": "tool_calls", "message":
                                                           _msg(calls=[("finish", {"message": "All set."})])
                                                           .model_dump()}]})
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    c = _ExtraBodyClient(fake, None)
    before = dict(FINISH_STATS)
    r = c.chat.completions.create(model="m", messages=[{"role": "user", "content": "long"}], tools=[])
    assert r.choices[0].message.content == "" and not r.choices[0].message.tool_calls
    r = c.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}],
                                  tools=[{"type": "function", "function": {"name": "send_email"}}])
    assert r.choices[0].message.content == "All set." and not r.choices[0].message.tool_calls
    assert FINISH_STATS["context_overflow"] == before["context_overflow"] + 1
    assert FINISH_STATS["finish_read_as_reply"] == before["finish_read_as_reply"] + 1
