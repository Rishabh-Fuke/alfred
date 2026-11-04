"""The audit log records every call, once, with metadata only: no file contents, names or paths, and nothing the
model wrote, even when the model puts private text into a call. Threat T11 and design test 8 in
docs/THREAT_MODEL.md."""
import os
import stat

import pytest

from alfred.broker import fs
from alfred.contracts import AuditEvent, ToolRequest


def test_the_log_keeps_no_contents_names_paths_or_model_text(session, secrets_of):
    private, secret = secrets_of
    broker = session.broker
    entries = session.list()
    attempts = [
        lambda: session.read(entries["notes.txt"]["handle"]),  # private text, read under the grant
        lambda: session.call("files__list", folder=entries["CS101"]["handle"]),
        lambda: session.read(private),  # the model pastes private text where a handle goes
        lambda: session.read("res_" + private),
        lambda: session.read(entries["notes.txt"]["handle"], max_chars=private),
        lambda: session.call(private, file=private),  # as a tool name
        lambda: session.call("files__read_text", file=str(session.b / "secret.txt")),
        lambda: broker.call_tool(private, "files__list", {}),  # as a task id
        lambda: broker.call(ToolRequest(task_id=session.task, operation="files.read_text " + secret, handle=secret)),
    ]
    for attempt in attempts:
        attempt()
    events = broker.audit.events()
    assert len(events) == 1 + len(attempts)  # the first listing, then one event per call
    stored = broker.audit.path.read_text()
    assert [AuditEvent.model_validate_json(line) for line in stored.splitlines()] == events
    assert events[1].bytes_returned == len(private.encode()) + 1
    for text in (private, secret, "CANARY", "notes", "syllabus", "CS101", "secret.txt", str(session.a.parent)):
        assert text not in stored


def test_each_event_says_what_happened_and_to_which_handle(session):
    handle = session.list()["syllabus.txt"]["handle"]
    session.read(handle)
    session.read("res_" + "0" * 32)
    session.broker.revoke(session.grant.grant_id)
    session.read(handle)
    events = session.broker.audit.events()
    assert [(e.operation, e.status, e.reason) for e in events] == [
        ("files.list", "ok", None), ("files.read_text", "ok", None),
        ("files.read_text", "denied", "unknown_handle"), ("files.read_text", "denied", "revoked")]
    assert all(e.task_id == session.task and e.grant_id in (session.grant.grant_id, None) for e in events)
    assert events[1].handle == handle and events[1].bytes_returned == len((session.a / "syllabus.txt").read_bytes())
    assert events[2].handle is None  # a handle the broker never issued isn't recorded


def test_the_log_file_is_readable_only_by_its_owner(session):
    session.list()
    assert stat.S_IMODE(os.stat(session.broker.audit.path).st_mode) == 0o600


def test_a_failure_inside_the_broker_is_recorded_and_returns_nothing(session, monkeypatch):
    handle = session.list()["syllabus.txt"]["handle"]
    monkeypatch.setattr(fs, "read_text", lambda *args: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        session.read(handle)
    last = session.broker.audit.events()[-1]
    assert (last.operation, last.status, last.reason, last.bytes_returned) == ("files.read_text", "error",
                                                                             "io_error", 0)
