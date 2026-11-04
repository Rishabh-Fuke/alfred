"""Fixtures for the permission broker's tests: folder A, which a task is granted; folder B beside it, which no task
ever is; a broker with a clock the tests control and its audit log on disk."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from alfred.broker import AuditLog, Broker
from alfred.contracts import Grant, ToolResult


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Session:
    """A task with folder A granted, calling tools the way the model does."""
    broker: Broker
    task: str
    grant: Grant
    a: Path
    b: Path

    def call(self, name, **arguments) -> ToolResult:
        return self.broker.call_tool(self.task, name, arguments)

    def list(self, folder=None) -> dict:
        result = self.call("files__list", **({"folder": folder} if folder else {}))
        assert result.status == "ok", result
        return {e["name"]: e for e in result.data["entries"]}

    def read(self, handle, **arguments) -> ToolResult:
        return self.call("files__read_text", file=handle, **arguments)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def broker(tmp_path, clock):
    with Broker(AuditLog(tmp_path / "audit.jsonl"), clock=clock) as b:
        yield b


@pytest.fixture
def folders(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    (a / "CS101").mkdir(parents=True)
    b.mkdir()
    (a / "syllabus.txt").write_text("Midterm: October 14. Final: December 9.\n")
    (a / "CS101" / "hw1.txt").write_text("Homework 1 is due September 30.\n")
    (a / "notes.txt").write_text("Reminder: CANARY-41d9 salary 182,000\n")      # private, but inside the grant
    (b / "secret.txt").write_text("CANARY-b7e1 the lab results came back\n")   # outside the grant
    (b / "hw1.txt").write_text("CANARY-b7e1 the lab results came back\n")
    return a, b


@pytest.fixture
def session(broker, folders):
    a, b = folders
    task = broker.start_task()
    return Session(broker, task, broker.grant(task, a, origin="cli"), a, b)


@pytest.fixture
def secrets_of(folders):
    """The private text inside the granted folder, and the secret only folder B holds."""
    a, b = folders
    return (a / "notes.txt").read_text().strip(), (b / "secret.txt").read_text().strip()
