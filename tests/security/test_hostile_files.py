"""Files inside the grant that could hurt: huge, binary, not UTF-8, a pipe, a folder too deep or too full, a task
that keeps reading. Each fails safely within the grant's limits. Design test 8 and threat T8 in
docs/THREAT_MODEL.md."""
import os
import threading

import pytest

from alfred.contracts import Limits


def test_a_huge_file_is_refused_unread(session, monkeypatch):
    with open(session.a / "huge.txt", "wb") as f:
        f.truncate(3_000_000)  # sparse, so it takes no disk space
    handle = session.list()["huge.txt"]["handle"]
    reads = []
    real_read = os.read
    monkeypatch.setattr(os, "read", lambda fd, n: reads.append(n) or real_read(fd, n))
    r = session.read(handle)
    assert (r.status, r.reason, r.bytes_returned) == ("denied", "too_large", 0) and "2 MB" in r.message
    assert reads == []


def test_binary_is_refused_and_bytes_that_arent_utf8_are_replaced(session):
    (session.a / "photo.txt").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    (session.a / "latin1.txt").write_bytes("café au lait".encode("latin-1"))
    entries = session.list()
    r = session.read(entries["photo.txt"]["handle"])
    assert (r.status, r.reason, r.bytes_returned) == ("denied", "not_text", 0)
    r = session.read(entries["latin1.txt"]["handle"])
    assert r.status == "ok" and r.data["text"] == "caf� au lait"


def test_a_pipe_swapped_in_after_listing_doesnt_block(session):
    pipe = session.a / "syllabus.txt"
    handle = session.list()["syllabus.txt"]["handle"]
    pipe.unlink()
    os.mkfifo(pipe)
    results = []
    reader = threading.Thread(target=lambda: results.append(session.read(handle)), daemon=True)
    reader.start()
    reader.join(timeout=10)
    if not results:  # stuck waiting for a writer: give it one so the broker's lock comes free, then fail
        os.close(os.open(pipe, os.O_WRONLY | os.O_NONBLOCK))
        reader.join(timeout=10)
        pytest.fail("reading a pipe blocked")
    assert (results[0].status, results[0].reason) == ("denied", "changed")


def test_reads_stop_at_the_character_limit(session):
    (session.a / "long.txt").write_text("é" * 50_000)
    entries = session.list()
    handle = entries["long.txt"]["handle"]
    r = session.read(handle)
    assert r.data["text"] == "é" * 6_000 and r.data["truncated"] is True and r.data["size"] == 100_000
    assert r.bytes_returned == 12_000
    assert len(session.read(handle, max_chars=20_000).data["text"]) == 20_000
    assert session.read(handle, max_chars="15").data["text"] == "é" * 15
    for bad in (0, 20_001, -5, True, 2.5, "lots", [100]):
        r = session.read(handle, max_chars=bad)
        assert (r.status, r.reason) == ("error", "bad_arguments"), bad
    assert session.read(entries["syllabus.txt"]["handle"]).data["truncated"] is False


def test_folders_more_than_five_levels_down_are_not_listed(session):
    d4 = session.a / "d1" / "d2" / "d3" / "d4"
    (d4 / "d5" / "d6").mkdir(parents=True)
    (d4 / "five levels down.txt").write_text("still readable")
    entries = session.list()
    for name in ("d1", "d2", "d3", "d4"):
        entries = session.list(entries[name]["handle"])
    assert session.read(entries["five levels down.txt"]["handle"]).data["text"] == "still readable"
    r = session.call("files__list", folder=entries["d5"]["handle"])
    assert (r.status, r.reason) == ("denied", "too_deep")


def test_a_listing_returns_at_most_200_entries(session):
    many = session.a / "many"
    many.mkdir()
    for i in range(250):
        (many / f"f{i:03}.txt").write_text("x")
    r = session.call("files__list", folder=session.list()["many"]["handle"])
    names = [e["name"] for e in r.data["entries"]]
    assert len(names) == 200 and r.data["more"] == 50 and names[0] == "f000.txt" and names[-1] == "f199.txt"


def test_a_grant_stops_after_its_calls(broker, folders):
    task = broker.start_task()
    broker.grant(task, folders[0], origin="cli", limits=Limits(max_calls=3))
    results = [broker.call_tool(task, "files__list", {}) for _ in range(4)]
    assert [r.status for r in results] == ["ok", "ok", "ok", "denied"] and results[-1].reason == "budget"


def test_a_grant_stops_after_its_bytes(broker, folders):
    task = broker.start_task()
    broker.grant(task, folders[0], origin="cli", limits=Limits(max_bytes=30))
    entries = {e["name"]: e for e in broker.call_tool(task, "files__list", {}).data["entries"]}
    first = broker.call_tool(task, "files__read_text", {"file": entries["syllabus.txt"]["handle"]})
    size = len((folders[0] / "syllabus.txt").read_bytes())
    assert size > 30 and first.status == "ok" and first.bytes_returned == size  # checked before a read, so one
    # read can go past it
    second = broker.call_tool(task, "files__read_text", {"file": entries["notes.txt"]["handle"]})
    assert (second.status, second.reason, second.bytes_returned) == ("denied", "budget", 0)
