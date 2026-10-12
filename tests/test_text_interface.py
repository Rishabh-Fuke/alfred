"""The text interface: no ask_user or finish tool; the agent asks, declines and answers by replying."""
import json
from types import SimpleNamespace

import pytest

from alfred.agents.llm import OpenAICompatAgent
from alfred.agents.scripted import OracleAgent, TeachingOracle
from alfred.env import Environment, is_question
from alfred.tasks import generate_tasks
from alfred.tools import TALK_TOOLS, schemas_for
from alfred.train.data import conversation_from_env, conversation_from_trajectory


@pytest.mark.parametrize("text, asks", [
    ("Which Andre do you mean, Andre Reyes or Andre Nair?", True),
    ("What would you like the email to say?", True),
    ("It's at 3:00 PM on Tuesday.", False),
    ("It's at 3:00 PM on Tuesday. Want me to set a reminder?", False),       # a closing offer, not a question
    ("I can't make phone calls. Want me to email her instead?", False),     # a decline
    ("Done! Anything else?", False),
    ("Should I send it to Andre Reyes or Andre Nair?", True),
])
def test_is_question(text, asks):
    assert is_question(text) == asks


def test_text_interface_offers_no_talk_tools_and_rejects_them():
    task = generate_tasks(1000, 1, ["impossible_request"], interface="text")[0]
    assert task.id.endswith("-t") and task.interface == "text"
    env = Environment(task)
    obs = env.reset()
    assert {t["function"]["name"] for t in obs.tools}.isdisjoint(TALK_TOOLS)
    assert "ask in your reply" in obs.system and "call finish" not in obs.system
    assert "error" in json.loads(env.call_tool("finish", {"status": "cannot_do", "message": "Can't."}))
    assert env.invalid_calls == 1 and not env.done


def test_same_tasks_same_grading_in_both_interfaces():
    tools = generate_tasks(1000, 2, "all")
    text = generate_tasks(1000, 2, "all", interface="text")
    for a, b in zip(tools, text):
        assert b.id == a.id + "-t" and a.as_interface("text").id == b.id and b.as_interface("tools").id == a.id
        assert (a.expected_actions, a.answer, a.should_ask, a.expected_status) == \
               (b.expected_actions, b.answer, b.should_ask, b.expected_status)


class _Client:
    """A fake model: each reply is plain text or (name, args) for a native tool call."""

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.seen.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, tuple):
            calls = [SimpleNamespace(id="c1", function=SimpleNamespace(name=r[0], arguments=json.dumps(r[1])))]
            msg = SimpleNamespace(content="", tool_calls=calls)
        else:
            msg = SimpleNamespace(content=r, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def test_model_asks_in_plain_text_then_acts():
    task = generate_tasks(1000, 1, ["ambiguous_recipient"], interface="text")[0]
    p = task.params
    client = _Client([
        ("search_contacts", {"query": p["first"]}),
        f"I found two people named {p['first']}. Which one do you mean?",
        ("draft_email", {"to": [p["target_email"]], "subject": "Lunch Friday?",
                         "body": f"Hi {p['first']}, are you free for lunch on Friday?"}),
        "Sent your lunch invite.",
    ])
    env = Environment(task)
    agent = OpenAICompatAgent("m", client=client)
    agent.run(env)
    m = env.metrics()
    assert m["asked_before_draft"] and m["clean_success"] and m["format_issues"] == 0
    assert {t["function"]["name"] for t in client.seen[0]["tools"]}.isdisjoint(TALK_TOOLS)
    roles = [x["role"] for x in agent.messages]
    assert roles[4:7] == ["assistant", "user", "assistant"]  # the question, the user's answer, the next step
    assert agent.messages[5]["content"] == task.clarification


def test_model_that_declines_in_plain_text_is_scored_as_declining():
    task = generate_tasks(1000, 1, ["impossible_request"], interface="text")[0]
    env = Environment(task)
    OpenAICompatAgent("m", client=_Client(["Sorry, I'm not able to do that with the tools I have."])).run(env)
    m = env.metrics()
    assert m["declined"] and m["clean_success"] and not m["implicit_finish"] and m["format_issues"] == 0


def test_tools_interface_still_treats_plain_text_as_a_format_problem():
    task = generate_tasks(1000, 1, ["impossible_request"])[0]
    env = Environment(task)
    OpenAICompatAgent("m", client=_Client(["Sorry, I'm not able to do that."])).run(env)
    m = env.metrics()
    assert m["implicit_finish"] and m["format_issues"] == 1


def test_training_conversations_from_text_interface_episodes():
    for task in generate_tasks(10, 1, "all", interface="text"):
        env = Environment(task)
        TeachingOracle().run(env)
        msgs = conversation_from_env(env)
        names = {c["function"]["name"] for m in msgs for c in m.get("tool_calls") or []}
        assert names.isdisjoint(TALK_TOOLS), task.id
        assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"] and not msgs[-1].get("tool_calls")
        if task.should_ask:
            i = next(i for i, m in enumerate(msgs) if m["role"] == "assistant" and m["content"].endswith("?"))
            assert msgs[i + 1] == {"role": "user", "content": task.clarification}
        # A teacher trajectory with the same messages converts to the same conversation.
        traj = {"task": task.to_json(), "messages": msgs, "metrics": env.metrics()}
        assert conversation_from_trajectory(traj) == msgs


def test_text_interface_conversations_render_for_training(tiny_model_dir):
    pytest.importorskip("transformers")
    from transformers import AutoTokenizer
    from alfred.train.render import IGNORE, check_prompt_tokens, render, reply_spans
    tok = AutoTokenizer.from_pretrained(tiny_model_dir)
    for task in generate_tasks(11, 1, ["ambiguous_recipient", "impossible_request"], interface="text"):
        env = Environment(task)
        OracleAgent().run(env)
        msgs, tools = conversation_from_env(env), schemas_for("text")
        full, spans = reply_spans(tok, msgs, tools)
        assert len(spans) == sum(m["role"] == "assistant" for m in msgs)
        trained = tok.decode([x for x in render(tok, msgs, tools).labels if x != IGNORE])
        assert msgs[-1]["content"] in trained and task.instruction not in trained
        assert check_prompt_tokens(tok, msgs, tools) == []
