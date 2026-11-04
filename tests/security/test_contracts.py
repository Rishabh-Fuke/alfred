"""The contracts accept only what they define: no unknown fields, no other versions, no coerced types, and no
results or decisions whose fields don't fit together."""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from alfred.contracts import (ERRORS, INSIDE, OUTSIDE, STATUS_OF, UNOFFERED, AgentDecision, AuditEvent, Grant, Limits,
                              ToolRequest, ToolResult)

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)
RID = "req_" + "0" * 16


def grant(**changes):
    fields = dict(grant_id="grant_" + "1" * 16, task_id="task_" + "2" * 16, root_device=1, root_inode=2,
                  operations=("files.list", "files.read_text"), limits=Limits(), origin="page",
                  granted_at=NOW, expires_at=NOW + timedelta(minutes=30))
    return Grant(**{**fields, **changes})


@pytest.mark.parametrize("make", [
    lambda **k: ToolRequest(task_id="t", operation="files.list", **k),
    lambda **k: ToolResult(request_id=RID, status="ok", data={}, **k),
    lambda **k: AuditEvent(at=NOW, request_id=RID, status="ok", **k),
    lambda **k: grant(**k),
    lambda **k: AgentDecision(task_id="t", decision="respond", message="Done.", **k),
], ids=["request", "result", "audit", "grant", "decision"])
def test_unknown_fields_and_other_versions_are_rejected(make):
    make()
    with pytest.raises(ValidationError):
        make(approved_by_user=True)
    with pytest.raises(ValidationError):
        make(schema_version="2")


def test_a_grant_comes_from_the_page_or_the_command_line_and_allows_only_reads():
    for change in (dict(origin="model"), dict(origin="tool_result"), dict(operations=("files.write_text",)),
                   dict(operations=()), dict(operations=("files.list", "files.list")), dict(expires_at=NOW),
                   dict(grant_id="grant_made_up_by_the_model"), dict(task_id="anything")):
        with pytest.raises(ValidationError):
            grant(**change)


def test_types_are_not_coerced():
    with pytest.raises(ValidationError):
        ToolRequest(task_id="t", operation="files.read_text", arguments='{"max_chars": 5}')
    with pytest.raises(ValidationError):
        grant(root_device="1")
    with pytest.raises(ValidationError):
        Limits(max_calls="200")


def test_limits_are_bounded():
    for change in (dict(read_chars=30_000), dict(max_calls=0), dict(max_depth=0), dict(max_file_bytes=-1)):
        with pytest.raises(ValidationError):
            Limits(**change)


def test_every_reason_has_one_status_and_results_fit_it():
    assert set(STATUS_OF) == set(OUTSIDE + UNOFFERED + INSIDE + ERRORS)
    ToolResult(request_id=RID, status="denied", reason="symlink", message="It's a link.")
    for bad in (dict(status="ok", data={}, reason="symlink"),
                dict(status="ok"),
                dict(status="denied", reason="symlink", message="m", data={"text": "x"}),
                dict(status="denied", reason="symlink"),
                dict(status="denied", reason="bad_arguments", message="m"),
                dict(status="error", reason="revoked", message="m")):
        with pytest.raises(ValidationError):
            ToolResult(request_id=RID, **bad)


def test_an_audit_event_holds_only_ids_codes_and_counts():
    AuditEvent(at=NOW, request_id=RID, task_id="task_" + "2" * 16, grant_id="grant_" + "1" * 16,
               handle="res_" + "3" * 32, operation="files.read_text", status="ok", bytes_returned=10)
    for smuggled in (dict(handle="res_CANARY-b7e1"), dict(handle="../B/secret.txt"), dict(task_id="CANARY task"),
                     dict(operation="files.read_text CANARY"), dict(request_id="req_CANARY")):
        with pytest.raises(ValidationError):
            AuditEvent(**{**dict(at=NOW, request_id=RID, status="ok"), **smuggled})
    # A field that could hold text would have to be added here on purpose.
    assert set(AuditEvent.model_fields) == {"schema_version", "at", "request_id", "task_id", "grant_id",
                                            "operation", "handle", "status", "reason", "bytes_returned"}


@pytest.mark.parametrize("decision, field, value", [
    ("request_tool", "tool_request", ToolRequest(task_id="t", operation="files.list")),
    ("propose_action", "proposed_action", {"type": "send_email"}),
    ("ask_user", "user_question", "Which class do you mean?"),
    ("decline", "message", "I can't do that."),
    ("respond", "message", "The midterm is October 14."),
    ("propose_delegation", "delegation_proposal", {"provider": "fake"}),
])
def test_each_decision_carries_its_own_field_and_no_other(decision, field, value):
    AgentDecision(task_id="t", decision=decision, **{field: value})
    with pytest.raises(ValidationError):
        AgentDecision(task_id="t", decision=decision)
    extra = "user_question" if field != "user_question" else "message"
    with pytest.raises(ValidationError):
        AgentDecision(task_id="t", decision=decision, **{field: value, extra: "and something else"})


def test_a_decision_cant_carry_another_tasks_request():
    with pytest.raises(ValidationError):
        AgentDecision(task_id="t1", decision="request_tool",
                      tool_request=ToolRequest(task_id="t2", operation="files.list"))
