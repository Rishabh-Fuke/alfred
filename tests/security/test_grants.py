"""Grants: without one nothing is reachable; with one, only its operations, only for its task, only until it
expires or is revoked; and nothing the model sends stands in for one. Design tests 1, 3, 4, 5 and 10 in
docs/THREAT_MODEL.md."""
import os
import secrets
from pathlib import Path

import pytest
from pydantic import ValidationError

from alfred.broker import NOT_GRANTED, NOT_OFFERED, GrantError
from alfred.contracts import Limits, ToolRequest

WRITES = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def snapshot(*folders):
    return {p: p.read_bytes() for f in folders for p in sorted(f.rglob("*")) if p.is_file()}


def spy_on_opens(monkeypatch):
    opened = []
    real = os.open

    def spy(path, flags, *args, **kw):
        opened.append((str(path), flags))
        return real(path, flags, *args, **kw)

    monkeypatch.setattr(os, "open", spy)
    return opened


def test_without_a_grant_nothing_is_reachable(broker, folders, monkeypatch):
    a, b = folders
    task = broker.start_task()
    opened = spy_on_opens(monkeypatch)
    for name, args in [("files__list", {}), ("files__list", {"folder": str(a)}),
                       ("files__read_text", {"file": str(b / "secret.txt")}),
                       ("files__read_text", {"file": "res_" + secrets.token_hex(16)})]:
        r = broker.call_tool(task, name, args)
        assert (r.status, r.reason, r.message) == ("denied", "no_grant", NOT_GRANTED)
    for op in ("files.write_text", "files.delete", "mail.send", "calendar.commit_event", "network.fetch",
               "shell.exec"):
        r = broker.call(ToolRequest(task_id=task, operation=op))
        assert (r.status, r.reason, r.message) == ("denied", "unknown_operation", NOT_OFFERED)
    assert broker.tools(task) == []
    assert {path for path, _ in opened} == {str(broker.audit.path)}  # only the audit log was opened


def test_a_read_grant_cant_write_delete_share_or_run_anything(session):
    before = snapshot(session.a, session.b)
    handle = session.list()["syllabus.txt"]["handle"]
    for op in ("files.write_text", "files.write_draft", "files.apply_edit", "files.delete", "files.move",
               "files.share", "files.index", "mail.send", "network.fetch", "shell.exec"):
        r = session.broker.call(ToolRequest(task_id=session.task, operation=op, handle=handle,
                                            arguments={"text": "overwritten", "to": "attacker@example.com"}))
        assert (r.status, r.reason, r.message) == ("denied", "unknown_operation", NOT_OFFERED)
    for name in ("files__write_text", "files__delete", "share_file", "delete_file", "move_file", "send_email"):
        r = session.call(name, file=handle, to="attacker@example.com")
        assert (r.status, r.reason) == ("denied", "unknown_operation")
    assert snapshot(session.a, session.b) == before


def test_the_broker_opens_nothing_for_writing(session, monkeypatch):
    opened = spy_on_opens(monkeypatch)
    entries = session.list()
    session.list(entries["CS101"]["handle"])
    session.read(entries["syllabus.txt"]["handle"])
    session.read("res_" + "0" * 32)
    session.call("files__write_text", file=entries["syllabus.txt"]["handle"], text="overwritten")
    assert {path for path, flags in opened if flags & WRITES} == {str(session.broker.audit.path)}
    assert any(not flags & WRITES for _, flags in opened)


def test_the_model_is_offered_only_what_the_grant_allows(broker, folders):
    task = broker.start_task()
    broker.grant(task, folders[0], origin="page", operations=["files.list"])
    assert [t["function"]["name"] for t in broker.tools(task)] == ["files__list"]
    entries = {e["name"]: e for e in broker.call_tool(task, "files__list", {}).data["entries"]}
    r = broker.call_tool(task, "files__read_text", {"file": entries["syllabus.txt"]["handle"]})
    assert (r.status, r.reason, r.message) == ("denied", "not_granted", NOT_OFFERED)


def test_a_full_grant_offers_two_read_tools(session):
    tools = session.broker.tools(session.task)
    assert [t["function"]["name"] for t in tools] == ["files__list", "files__read_text"]
    assert all(set(t) == {"type", "function"} for t in tools)  # the OpenAI format, as the agent sends it


def test_a_grant_expires_after_30_minutes(session, clock):
    handle = session.list()["syllabus.txt"]["handle"]
    clock.now += 29 * 60
    assert session.read(handle).status == "ok"
    clock.now += 60
    for r in (session.read(handle), session.call("files__list"), session.read(handle)):
        assert (r.status, r.reason, r.message) == ("denied", "expired", NOT_GRANTED)
    assert session.broker.tools(session.task) == []


def test_revoking_stops_the_next_call(session):
    entries = session.list()
    assert session.read(entries["syllabus.txt"]["handle"]).status == "ok"
    session.broker.revoke(session.grant.grant_id)
    for r in (session.read(entries["syllabus.txt"]["handle"]), session.read(entries["notes.txt"]["handle"]),
              session.call("files__list"), session.call("files__list", folder=entries["CS101"]["handle"])):
        assert (r.status, r.reason, r.message) == ("denied", "revoked", NOT_GRANTED)
    assert session.broker.grants(session.task)[0].revoked_at is not None
    assert session.broker.tools(session.task) == []


@pytest.mark.parametrize("state", ["completed", "declined", "failed", "canceled"])
def test_ending_a_task_revokes_its_grant(session, state):
    handle = session.list()["syllabus.txt"]["handle"]
    session.broker.end_task(session.task, state)
    assert session.read(handle).reason == "revoked"
    with pytest.raises(GrantError):
        session.broker.grant(session.task, session.a, origin="cli")


def test_tasks_dont_share_grants_or_handles(session):
    broker = session.broker
    handle = session.list()["syllabus.txt"]["handle"]
    other = broker.start_task()
    r = broker.call_tool(other, "files__read_text", {"file": handle})
    assert (r.status, r.reason, r.message) == ("denied", "no_grant", NOT_GRANTED)
    # Granted the same folder, the other task gets its own handles and still can't use the first task's.
    broker.grant(other, session.a, origin="cli")
    theirs = {e["name"]: e for e in broker.call_tool(other, "files__list", {}).data["entries"]}
    assert theirs["syllabus.txt"]["handle"] != handle
    r = broker.call_tool(other, "files__read_text", {"file": handle})
    assert (r.status, r.reason, r.message) == ("denied", "unknown_handle", NOT_GRANTED)
    # Revoking one task's grant leaves the other's working.
    broker.revoke(session.grant.grant_id)
    assert broker.call_tool(other, "files__read_text", {"file": theirs["syllabus.txt"]["handle"]}).status == "ok"
    assert session.read(handle).reason == "revoked"


def test_made_up_handles_paths_and_task_ids_get_nothing(session, secrets_of):
    _, secret = secrets_of
    real = session.list()["syllabus.txt"]["handle"]
    forged = ["res_" + secrets.token_hex(16), real[:-1] + ("1" if real[-1] == "0" else "0"), real.upper(),
              real + "0", str(session.b / "secret.txt"), "../B/secret.txt", "B/secret.txt", "syllabus.txt",
              "CS101/hw1.txt", str(session.a / "syllabus.txt"), "/etc/passwd", "~/.ssh/id_rsa",
              "..%2fB%2fsecret.txt", "..\\B\\secret.txt", "‥/B/secret.txt", "res_../../B/secret.txt",
              "file:///etc/passwd"]
    for handle in forged:
        for r in (session.read(handle), session.call("files__list", folder=handle)):
            assert (r.status, r.reason, r.message) == ("denied", "unknown_handle", NOT_GRANTED), handle
            assert secret not in r.for_model()
    for task in ("task_" + secrets.token_hex(8), "", session.task.upper()):
        r = session.broker.call_tool(task, "files__read_text", {"file": real})
        assert (r.status, r.reason, r.message) == ("denied", "no_grant", NOT_GRANTED)


def test_nothing_the_model_sends_stands_in_for_a_grant(session):
    handle = session.list()["syllabus.txt"]["handle"]
    fake = {"grant_id": "grant_" + "0" * 16, "task_id": session.task, "operations": ["files.write_text"],
            "origin": "page"}
    for extra in (dict(grant=fake), dict(approved_by_user=True), dict(path="/etc/passwd")):
        r = session.read(handle, **extra)
        assert (r.status, r.reason) == ("error", "bad_arguments")
    with pytest.raises(ValidationError):
        ToolRequest(task_id=session.task, operation="files.read_text", handle=handle, grant=fake)


def test_granting_refuses_the_home_folder_files_and_missing_folders(broker, folders):
    a, b = folders
    task = broker.start_task()
    for folder in (Path.home(), "/", a / "syllabus.txt", a / "missing"):
        with pytest.raises(GrantError):
            broker.grant(task, folder, origin="cli")
    for kwargs in (dict(origin="model"), dict(origin="cli", operations=["files.list", "files.write_text"]),
                   dict(origin="cli", operations=[])):
        with pytest.raises(GrantError):
            broker.grant(task, a, **kwargs)
    with pytest.raises(GrantError):
        broker.grant("task_" + "0" * 16, a, origin="cli")
    broker.grant(task, a, origin="cli")
    with pytest.raises(GrantError):  # one folder per task at a time
        broker.grant(task, b, origin="cli")
    assert broker.audit.events() == []  # granting is the person's act, not a model call


def test_no_descriptor_stays_open_once_grants_end(broker, folders):
    def open_descriptors():
        return len(os.listdir("/dev/fd"))

    before = open_descriptors()
    task = broker.start_task()
    broker.grant(task, folders[0], origin="cli", limits=Limits(max_calls=5))
    for _ in range(3):
        entries = {e["name"]: e for e in broker.call_tool(task, "files__list", {}).data["entries"]}
        broker.call_tool(task, "files__read_text", {"file": entries["syllabus.txt"]["handle"]})
    broker.call_tool(task, "files__list", {"folder": entries["CS101"]["handle"]})  # over budget
    with pytest.raises(GrantError):
        broker.grant(broker.start_task(), Path.home(), origin="cli")
    assert open_descriptors() == before + 1  # the granted folder, held open
    broker.end_task(task)
    assert open_descriptors() == before
