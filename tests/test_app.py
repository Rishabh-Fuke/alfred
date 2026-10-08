"""The local app with a person in the loop, driven over HTTP the way the page drives it."""
import json
import time
import urllib.request

import pytest

from alfred.app import server as app_server
from alfred.dev.replay_server import serve_in_thread
from alfred.tasks import generate_tasks


def _call(base, path, payload=None):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _wait(base, since, want, timeout=20):
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        _, out = _call(base, f"/api/events?since={since}")
        seen += out["events"]
        since = max([e["id"] for e in out["events"]] + [since])
        hit = [e for e in seen if e["type"] == want]
        if hit:
            return hit[-1], since, seen
        time.sleep(0.05)
    raise AssertionError(f"no {want} event; saw {[e['type'] for e in seen]}")


@pytest.fixture
def app(tmp_path):
    model, url = serve_in_thread(1000, 1)  # replays the careful agent's tool calls
    args = app_server.parse_args(["--model", "replay", "--base-url", url, "--world", "1000", "--port", "0",
                                  "--memory", str(tmp_path / "memory.json"), "--no-browser", "--no-cloud"])
    srv = app_server.serve(args)
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", srv.app, tmp_path
    srv.shutdown()
    model.shutdown()


def test_ask_then_approve_changes_the_world_and_is_remembered(app):
    base, application, tmp = app
    task = next(t for t in generate_tasks(1000, 1) if t.template == "ambiguous_recipient")
    sent_before = len(application.session.world.emails("sent"))
    assert _call(base, "/api/request", {"text": task.instruction})[0] == 200
    assert _call(base, "/api/request", {"text": "another"})[0] == 409  # one request at a time
    q, since, _ = _wait(base, 0, "question")
    assert task.params["first"] in q["question"]
    _call(base, "/api/respond", {"pending_id": q["pending_id"], "response": {"text": task.clarification}})
    draft, since, _ = _wait(base, since, "approval")
    assert draft["kind"] == "email" and draft["action"]["to"] == [task.params["target_email"]]
    assert "body" in draft["editable"]
    _call(base, "/api/respond", {"pending_id": draft["pending_id"],
                                 "response": {"approve": True, "edits": {"body": "Lunch Friday? (edited)"}}})
    final, since, seen = _wait(base, since, "done")
    assert any(e["type"] == "final" and e["status"] == "done" for e in seen)
    sent = application.session.world.emails("sent")
    assert len(sent) == sent_before + 1 and sent[-1]["body"] == "Lunch Friday? (edited)"
    memory = json.loads((tmp / "memory.json").read_text())
    assert memory[0]["answer"] == task.clarification
    _, world = _call(base, "/api/world")
    assert world["memory"] and any(e["subject"] == "Lunch on Friday?" for e in world["emails"] if e["folder"] == "sent")


def test_rejecting_sends_the_feedback_back(app):
    base, application, _ = app
    task = next(t for t in generate_tasks(1000, 1) if t.template == "reply_with_info")
    _call(base, "/api/request", {"text": task.instruction})
    draft, since, seen = _wait(base, 0, "approval")
    assert any(e["type"] == "result" and e["records"] and e["records"][0]["opened"] for e in seen)
    _call(base, "/api/respond", {"pending_id": draft["pending_id"],
                                 "response": {"approve": False, "feedback": "Not now."}})
    decision, since, _ = _wait(base, since, "decision")
    assert decision["approved"] is False and decision["feedback"] == "Not now."
    _wait(base, since, "done")
    assert not application.session.world.emails("sent")[-1]["body"].startswith("Hi")  # nothing new was sent


def test_polish_needs_a_key_and_stop_works(app):
    base, application, _ = app
    task = next(t for t in generate_tasks(1000, 1) if t.template == "reply_with_info")
    _call(base, "/api/request", {"text": task.instruction})
    draft, since, _ = _wait(base, 0, "approval")
    code, out = _call(base, "/api/polish", {"pending_id": draft["pending_id"]})
    assert code == 409 and "NEBIUS_API_KEY" in out["error"]
    _call(base, "/api/stop", {})
    final, _, _ = _wait(base, since, "done")
    assert not application.session.running
