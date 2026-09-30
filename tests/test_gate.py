import json

from alfred.env import Environment
from alfred.tasks import generate_tasks
from alfred.tools import TIERS, TOOL_NAMES


def make_env(template="reschedule_and_notify", seed=1000, **kw):
    task = next(t for t in generate_tasks(seed, 1) if t.template == template)
    env = Environment(task, **kw)
    env.reset()
    return env


def call(env, name, **args):
    return json.loads(env.call_tool(name, args))


def test_no_tool_lets_the_model_commit_anything():
    assert {TIERS[n] for n in TOOL_NAMES} == {"read", "draft", "control"}
    assert not any(n.startswith(("send", "commit", "approve", "delete", "pay")) for n in TOOL_NAMES)


def test_reads_do_not_change_the_world():
    env = make_env()
    before = env.world.snapshot()
    call(env, "search_emails", query="")
    call(env, "read_email", email_id="em_001")
    call(env, "search_calendar", query="")
    call(env, "get_event", event_id="ev_001")
    call(env, "search_contacts", query="")
    call(env, "get_contact", contact_id="ct_001")
    assert env.world.snapshot() == before


def test_rejected_drafts_change_nothing():
    env = make_env()
    before = env.world.snapshot()
    assert call(env, "draft_email", to=["stranger@else.example"], subject="hi", body="hello")["status"] == "rejected"
    assert env.world.snapshot() == before


def test_approval_commits_exactly_the_draft_and_only_once():
    env = make_env()
    p = env.task.params
    n_sent = len(env.world.emails("sent"))
    body = f"Hi {p['first']}, I'm moving our meeting to {p['new_day']}."
    assert call(env, "draft_email", to=[p["contact_email"]], subject="Moving", body=body)["status"] == "approved"
    sent = env.world.emails("sent")
    assert len(sent) == n_sent + 1
    newest = max(sent, key=lambda e: e["id"])
    assert newest["body"] == body and newest["recipients"] == [p["contact_email"]]
    env.gate.approve(env.gate.actions[-1])  # approving again must not send again
    assert len(env.world.emails("sent")) == n_sent + 1


def test_actions_are_frozen_when_submitted():
    env = make_env()
    action = {"kind": "email", "to": ["a@b.example"], "cc": [], "subject": "s", "body": "what the user saw"}
    pa = env.gate.submit(action, "preview", 0)
    action["body"] = "changed after review"
    env.gate.approve(pa)
    assert max(env.world.emails("sent"), key=lambda e: e["id"])["body"] == "what the user saw"


def test_invitations_go_out_only_after_approval():
    env = make_env("schedule_meeting")
    p = env.task.params
    n_sent = len(env.world.emails("sent"))
    wrong = call(env, "propose_new_event", title=p["title"], start=p["start"], end=p["end"], attendees=["x@y.example"])
    assert wrong["status"] == "rejected" and len(env.world.emails("sent")) == n_sent
    right = call(env, "propose_new_event", title=p["title"], start=p["start"], end=p["end"],
                 attendees=[p["contact_email"]])
    assert right["status"] == "approved" and len(env.world.emails("sent")) == n_sent + 1


def test_moving_only_the_start_keeps_the_duration():
    env = make_env()
    p = env.task.params
    res = call(env, "propose_event_change", event_id=p["event_id"], new_start=p["new_start"])
    assert res["status"] == "approved"
    before = env.gate.actions[-1].action["before"]
    after = env.world.get_event(p["event_id"])
    assert after["start_at"] == p["new_start"]
    orig_minutes = int(before["end_at"][11:13]) * 60 + int(before["end_at"][14:]) - (
        int(before["start_at"][11:13]) * 60 + int(before["start_at"][14:]))
    new_minutes = int(after["end_at"][11:13]) * 60 + int(after["end_at"][14:]) - (
        int(after["start_at"][11:13]) * 60 + int(after["start_at"][14:]))
    assert orig_minutes == new_minutes


def test_enforce_policy_blocks_unneeded_reads():
    env = make_env(read_policy="enforce")
    secret = sorted(i for i in env.world.sensitive_ids() - env.task.allowed_reads if i.startswith("em_"))[0]
    assert "Access denied" in call(env, "read_email", email_id=secret)["error"]
    assert env.blocked_reads == 1 and env.opened == []


def test_bad_calls_are_counted_not_raised():
    env = make_env()
    assert "error" in json.loads(env.call_tool("send_money", {}))
    assert "error" in json.loads(env.call_tool("read_email", "{not json"))
    assert "error" in json.loads(env.call_tool("draft_email", {"to": ["a@b.example"]}))
    assert env.invalid_calls == 3
    assert "error" in json.loads(env.call_tool("read_email", {"email_id": "em_999"}))
    assert env.tool_errors == 1


def test_common_argument_slips_are_accepted():
    env = make_env()
    p = env.task.params
    res = call(env, "draft_email", to=f"{p['first']} <{p['contact_email']}>", subject="Moving",
               body=f"Moving it to {p['new_day']}.")
    assert res["status"] == "approved"
    call(env, "finish", status="Completed", message="ok")
    assert env.finish_info["status"] == "done"
