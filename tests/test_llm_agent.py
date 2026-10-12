import json
from types import SimpleNamespace

import pytest

from alfred import eval as alfred_eval
from alfred.agents.llm import MALFORMED, OpenAICompatAgent, parse_json_tool_call, strip_think
from alfred.dev.replay_server import serve_in_thread
from alfred.env import Environment
from alfred.prompts import build_system_prompt
from alfred.tasks import TEMPLATES, generate_tasks
from alfred.tools import TOOL_NAMES
from alfred.world import World, human_time, parse_dt


def test_parse_json_tool_call():
    assert parse_json_tool_call('{"tool": "finish", "arguments": {"status": "done", "message": "x"}}') == (
        "finish", {"status": "done", "message": "x"})
    fenced = 'Sure!\n```json\n{"name": "read_email", "arguments": "{\\"email_id\\": \\"em_001\\"}"}\n```'
    assert parse_json_tool_call(fenced) == ("read_email", {"email_id": "em_001"})
    tagged = '<tool_call>{"name": "ask_user", "arguments": {"question": "Which one?"}}</tool_call>'
    assert parse_json_tool_call(tagged) == ("ask_user", {"question": "Which one?"})
    assert parse_json_tool_call("It's at 3pm.") is None
    assert parse_json_tool_call('{"tool": "finish", "arguments": {') == MALFORMED


def test_parse_json_tool_call_infers_the_tool_from_unambiguous_keys():
    # Seen from gpt-oss in the baseline: a finish call and an ask_user call written without the tool name.
    bare_finish = '{"message":"I don\'t have the capability to make phone calls.", "status":"cannot_do"}'
    assert parse_json_tool_call(bare_finish) == ("finish", {"message": "I don't have the capability to make "
                                                                      "phone calls.", "status": "cannot_do"})
    assert parse_json_tool_call('{"question": "Which Priya?"}') == ("ask_user", {"question": "Which Priya?"})
    assert parse_json_tool_call('{"status": "shipped", "carrier": "x"}') == MALFORMED  # fits no tool


def test_strip_think():
    assert strip_think("<think>hmm</think>Hello") == "Hello"
    assert strip_think("reasoning that started in the template</think>Answer") == "Answer"
    assert strip_think("<|channel|>analysis<|message|>thinking<|end|><|start|>assistant<|channel|>final"
                       "<|message|>It's at 3 PM.") == "It's at 3 PM."


def test_prompts():
    w = World.generate(1000)
    plain, private = build_system_prompt(w, "plain"), build_system_prompt(w, "privacy")
    assert "Privacy rules" in private and "Privacy rules" not in plain
    listing = build_system_prompt(w, "plain", tool_mode="json")
    assert all(f"- {name}(" in listing for name in TOOL_NAMES)


class _StubClient:
    """Pretends to be an OpenAI client whose model writes tool calls as text."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        msg = SimpleNamespace(content=self.replies.pop(0), tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def test_native_agent_recovers_calls_written_as_text():
    task = next(t for t in generate_tasks(1000, 1) if t.template == "event_time_lookup")
    p = task.params
    start = parse_dt(World.generate(task.world_seed).get_event(p["event_id"])["start_at"])
    call = json.dumps({"name": "search_calendar", "arguments": {"query": p["query"]}})
    agent = OpenAICompatAgent("stub", client=_StubClient([f"<tool_call>{call}</tool_call>",
                                                          f"<think>found it</think>It's at {human_time(start)}."]))
    env = Environment(task)
    stats = agent.run(env)
    m = env.metrics()
    assert stats["recovered_calls"] == 1 and m["implicit_finish"] and m["success"]
    assert m["format_issues"] == 2 and not m["clean_format"]  # a recovered call and a plain-text ending


def test_extra_body_is_sent_with_every_request():
    seen = []

    class Client(_StubClient):
        def _create(self, **kw):
            seen.append(kw.get("extra_body"))
            return super()._create(**kw)

    task = next(t for t in generate_tasks(1000, 1) if t.template == "impossible_request")
    body = {"chat_template_kwargs": {"enable_thinking": False}}
    agent = OpenAICompatAgent("stub", client=Client(['{"status": "cannot_do", "message": "I can\'t do that."}']),
                              extra_body=body)
    env = Environment(task)
    agent.run(env)
    assert seen == [body] and env.metrics()["success"]


@pytest.mark.parametrize("mode", ["native", "json"])
def test_end_to_end_through_an_openai_compatible_server(tmp_path, mode):
    server, url = serve_in_thread(1000, 1)
    try:
        run = alfred_eval.main(["--models", "replay", "--base-url", url, "--worlds", "1", "--tool-mode", mode,
                                 "--prompt", "plain,privacy", "--out", str(tmp_path), "--workers", "2"])
    finally:
        server.shutdown()
    rows = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
    assert len(rows) == 2 * len(TEMPLATES)
    assert all(r["metrics"]["clean_success"] for r in rows)
    assert not server.problems
    assert "Clean success" in (run / "report.md").read_text()


def test_resume_skips_finished_episodes(tmp_path):
    run = alfred_eval.main(["--agent", "oracle", "--worlds", "1", "--out", str(tmp_path)])
    first = (run / "results.jsonl").read_text()
    alfred_eval.main(["--agent", "oracle", "--worlds", "1", "--resume", str(run)])
    assert (run / "results.jsonl").read_text() == first


def test_parse_xml_tool_calls():
    from alfred.agents.llm import parse_xml_tool_calls
    text = ("<tool_call>\n<function=draft_email>\n<parameter=to>\n[\"a@b.example\"]\n</parameter>\n"
            "<parameter=subject>\nLunch?\n</parameter>\n<parameter=body>\nHi,\n\nFriday works.\n</parameter>\n"
            "</function>\n</tool_call>\n<tool_call>\n<function=search_emails>\n<parameter=limit>\n5\n</parameter>\n"
            "<parameter=query>\nflight\n</parameter>\n</function>\n</tool_call>")
    assert parse_xml_tool_calls(text) == [
        ("draft_email", {"to": ["a@b.example"], "subject": "Lunch?", "body": "Hi,\n\nFriday works."}),
        ("search_emails", {"limit": 5, "query": "flight"})]
    assert parse_xml_tool_calls("It's at 3pm.") == []


def test_native_mode_recovers_xml_calls_a_server_left_as_text():
    task = next(t for t in generate_tasks(1000, 1) if t.template == "impossible_request")
    reply = ("<tool_call>\n<function=finish>\n<parameter=status>\ncannot_do\n</parameter>\n<parameter=message>\n"
             "I can't do that.\n</parameter>\n</function>\n</tool_call>")
    env = Environment(task)
    stats = OpenAICompatAgent("stub", client=_StubClient([reply]), tool_mode="native").run(env)
    m = env.metrics()
    assert m["success"] and stats["recovered_calls"] == 1 and m["format_issues"] == 1
