"""Building the supervised training set: what's kept, how it's cleaned, and that nothing leaks from eval worlds."""
import json

import pytest

from alfred import eval as alfred_eval
from alfred.agents.scripted import TeachingOracle
from alfred.dev.replay_server import serve_in_thread
from alfred.env import Environment
from alfred.tasks import EVAL_WORLD_START, TEMPLATES, TRAIN_WORLDS, VALIDATION_WORLDS, generate_tasks
from alfred.train import data


def test_world_splits_dont_overlap():
    assert max(TRAIN_WORLDS) < min(VALIDATION_WORLDS) and max(VALIDATION_WORLDS) < EVAL_WORLD_START


def test_teaching_oracle_is_clean_and_varied():
    tasks = generate_tasks(0, 12)
    finishes = set()
    for t in tasks:
        env = Environment(t)
        TeachingOracle().run(env)
        m = env.metrics()
        assert m["clean_success"] and m["format_issues"] == 0, (t.id, m)
        finishes.add(env.finish_info["message"])
    assert len(finishes) > 40  # not one canned sentence per template


def test_scripted_conversation_shape():
    task = generate_tasks(5, 1, ["reschedule_and_notify"])[0]
    env = Environment(task)
    TeachingOracle().run(env)
    msgs = data.conversation_from_env(env)
    assert [m["role"] for m in msgs[:2]] == ["system", "user"]
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["tool_calls"][0]["function"]["name"] == "finish"
    for a, b in zip(msgs[2::2], msgs[3::2]):  # every call is answered by its own result
        assert a["role"] == "assistant" and b["role"] == "tool" and b["tool_call_id"] == a["tool_calls"][0]["id"]
        assert isinstance(a["tool_calls"][0]["function"]["arguments"], dict)
    assert data.train_flags(msgs) == [True] * sum(m["role"] == "assistant" for m in msgs)


def _teacher_traj(messages, metrics, seed=3, template="email_fact_lookup"):
    task = generate_tasks(seed, 1, [template])[0].to_json()
    return {"task": task, "messages": messages, "metrics": metrics, "format_notes": [],
            "row": {"model": "teacher", "tool_mode": "native", "sample": 0, "error": None}}


CLEAN = {"clean_success": True, "truncated": False, "calls": 3, "invalid_calls": 0, "leaked_reasoning": False,
         "status_conflict": False, "classifications": [], "status": "done", "first_try_approval": None}


def test_plain_text_ending_becomes_a_finish_call_and_reasoning_is_dropped():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "What's my code?"},
            {"role": "assistant", "content": "<think>let me look</think>Searching.",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "search_emails", "arguments": '{"query": "flight"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"results": []}'},
            {"role": "assistant", "content": "<think>found it</think>\nYour code is **ABC123**."}]
    conv = data.conversation_from_trajectory(_teacher_traj(msgs, {**CLEAN, "implicit_finish": True}))
    assert conv[2]["content"] == "" and conv[2]["tool_calls"][0]["function"]["arguments"] == {"query": "flight"}
    last = conv[-1]["tool_calls"][0]["function"]
    assert last["name"] == "finish" and last["arguments"] == {"status": "done", "message": "Your code is **ABC123**."}


def test_rejected_turn_stays_but_gets_no_loss():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "draft_email", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "a", "content": '{"status": "rejected", "feedback": "wrong person"}'},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "b", "type": "function", "function": {"name": "draft_email", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "b", "content": '{"status": "approved"}'},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c", "type": "function", "function": {"name": "finish", "arguments": "{}"}}]}]
    assert data.train_flags(msgs) == [False, True, True]


@pytest.mark.parametrize("change, reason", [
    ({"clean_success": False}, "not a clean success"),
    ({"invalid_calls": 1}, "formatting problems"),
    ({"truncated": True}, "too long"),
])
def test_teacher_filter(change, reason):
    traj = _teacher_traj([{"role": "assistant", "content": "x"}], {**CLEAN, **change})
    assert data.keep_teacher_episode(traj) == (False, reason)


def test_eval_and_validation_worlds_are_never_training_data():
    for seed in (min(VALIDATION_WORLDS), EVAL_WORLD_START):
        traj = _teacher_traj([{"role": "assistant", "content": "x"}], CLEAN, seed=seed)
        assert data.keep_teacher_episode(traj) == (False, "not a training world")
    with pytest.raises(ValueError):
        data.oracle_examples(range(EVAL_WORLD_START, EVAL_WORLD_START + 1), ["event_time_lookup"])


def test_end_to_end_from_a_teacher_run(tmp_path):
    server, url = serve_in_thread(0, 2)  # a "teacher" that replays the oracle through the chat API
    try:
        run = alfred_eval.main(["--models", "replay", "--base-url", url, "--world-start", "0", "--worlds", "2",
                                "--tool-mode", "native", "--out", str(tmp_path / "runs"), "--workers", "4"])
    finally:
        server.shutdown()
    out = data.main(["--runs", str(run), "--oracle-worlds", "2-4", "--per-template", "3", "--val-frac", "0.2",
                     "--out", str(tmp_path / "sft.jsonl")])
    train = [json.loads(x) for x in out.read_text().splitlines()]
    val = [json.loads(x) for x in out.with_suffix(".val.jsonl").read_text().splitlines()]
    rows = train + val
    # 3 per training template: Alfred's own get 2 from the teacher topped up with 1 scripted (the replay teacher only
    # ran Alfred's tasks); the procedural app templates are all scripted.
    assert len(rows) == 3 * len(data.TRAINING_TEMPLATES)
    assert sum(r["source"] == "teacher:replay" for r in rows) == 2 * len(TEMPLATES)
    assert {r["template"] for r in rows} == set(data.TRAINING_TEMPLATES)
    assert {r["task_id"] for r in train}.isdisjoint({r["task_id"] for r in val})
    for r in rows:
        assert r["world_seed"] in TRAIN_WORLDS and r["tools"] and len(r["train"]) >= 1
        assert r["messages"][-1]["tool_calls"][0]["function"]["name"] == "finish"
    assert "| ambiguous_recipient |" in out.with_suffix(".md").read_text()
