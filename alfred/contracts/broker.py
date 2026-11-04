"""What crosses the permission broker's boundary.

Grants are held by the broker: nothing outside it creates one from data, and a grant never comes from model output,
a tool result or file content. Requests are only requests: the broker checks every one. Results go back to the
model through ToolResult.for_model(). Audit events keep metadata only, and their fields can't hold anything else:
ids, operation names, reason codes and counts.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1"

Operation = Literal["files.list", "files.read_text"]
OPERATIONS: Tuple[str, ...] = ("files.list", "files.read_text")

# Why a call was refused or failed. The model gets one answer for every OUTSIDE reason, so it can't tell a guessed
# handle from another task's, or a grant that never existed from one that ended. UNOFFERED: an operation the
# broker doesn't know, or one the grant doesn't include. INSIDE: something about an entry inside the grant.
OUTSIDE = ("no_grant", "unknown_handle", "expired", "revoked")
UNOFFERED = ("unknown_operation", "not_granted")
INSIDE = ("symlink", "hard_link", "other_device", "changed", "too_deep", "too_large", "not_text", "budget")
ERRORS = ("wrong_kind", "bad_arguments", "io_error")  # the call reached the grant but couldn't be carried out
Reason = Literal["unknown_operation", "no_grant", "not_granted", "unknown_handle", "expired", "revoked",
                 "symlink", "hard_link", "other_device", "changed", "too_deep", "too_large", "not_text", "budget",
                 "wrong_kind", "bad_arguments", "io_error"]
Status = Literal["ok", "denied", "error"]
STATUS_OF = {**{r: "denied" for r in OUTSIDE + UNOFFERED + INSIDE}, **{r: "error" for r in ERRORS}}
Origin = Literal["page", "cli"]

TASK_ID = r"^task_[0-9a-f]{16}$"
GRANT_ID = r"^grant_[0-9a-f]{16}$"
REQUEST_ID = r"^req_[0-9a-f]{16}$"
HANDLE = r"^res_[0-9a-f]{32}$"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _check_outcome(status: str, reason: Optional[str]) -> None:
    if status == "ok" and reason is not None:
        raise ValueError("an ok outcome has no reason")
    if status != "ok" and (reason is None or STATUS_OF[reason] != status):
        raise ValueError(f"status {status!r} doesn't go with reason {reason!r}")


class Limits(Contract):
    """How much one grant allows. The defaults are Milestone 1's starting values."""
    max_depth: int = Field(5, ge=1, le=64)                 # levels below the granted folder that can be listed
    max_entries: int = Field(200, ge=1, le=10_000)         # entries returned per listing
    max_file_bytes: int = Field(2_000_000, ge=1)           # larger files are refused unread
    read_chars: int = Field(6_000, ge=1)                   # characters per read by default
    max_read_chars: int = Field(20_000, ge=1)              # most characters one read can ask for
    max_bytes: int = Field(10_000_000, ge=1)               # file text returned under the grant, in UTF-8 bytes
    max_calls: int = Field(200, ge=1)
    ttl_seconds: int = Field(1_800, ge=1)

    @model_validator(mode="after")
    def _default_fits(self):
        if self.read_chars > self.max_read_chars:
            raise ValueError("read_chars can't be more than max_read_chars")
        return self


class Grant(Contract):
    """Read access to one folder, for one task, until it expires or is revoked."""
    schema_version: Literal["1"] = SCHEMA_VERSION
    grant_id: str = Field(pattern=GRANT_ID)
    task_id: str = Field(pattern=TASK_ID)
    root_device: int                                       # the granted folder, which the broker holds open
    root_inode: int
    operations: Tuple[Operation, ...] = Field(min_length=1)
    limits: Limits
    origin: Origin
    granted_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _consistent(self):
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("operations repeat")
        if self.expires_at <= self.granted_at:
            raise ValueError("a grant has to expire after it's granted")
        return self


class ToolRequest(Contract):
    """One operation that Alfred's environment asks for on the model's behalf. A request isn't permission."""
    schema_version: Literal["1"] = SCHEMA_VERSION
    task_id: str
    operation: str
    handle: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolResult(Contract):
    """What the broker did. The reason is for Alfred's own code and the audit log; the model sees for_model()."""
    schema_version: Literal["1"] = SCHEMA_VERSION
    request_id: str = Field(pattern=REQUEST_ID)
    status: Status
    reason: Optional[Reason] = None
    data: Optional[Dict[str, Any]] = None
    message: str = ""
    bytes_returned: int = Field(0, ge=0)                   # file text in the result, in UTF-8 bytes

    @model_validator(mode="after")
    def _consistent(self):
        _check_outcome(self.status, self.reason)
        if (self.status == "ok") != (self.data is not None):
            raise ValueError("data comes with an ok result, and only with one")
        if self.status != "ok" and not self.message:
            raise ValueError("a refused or failed result says why")
        return self

    def for_model(self) -> str:
        return json.dumps(self.data, ensure_ascii=False) if self.status == "ok" else self.message


class AuditEvent(Contract):
    """One broker call, as stored. No field can hold file contents, names, paths, prompts or model output."""
    schema_version: Literal["1"] = SCHEMA_VERSION
    at: datetime
    request_id: str = Field(pattern=REQUEST_ID)
    task_id: Optional[str] = Field(None, pattern=TASK_ID)      # None for a task the broker didn't start
    grant_id: Optional[str] = Field(None, pattern=GRANT_ID)
    operation: Optional[Operation] = None                      # None for an operation the broker doesn't know
    handle: Optional[str] = Field(None, pattern=HANDLE)        # only handles the broker issued
    status: Status
    reason: Optional[Reason] = None
    bytes_returned: int = Field(0, ge=0)

    @model_validator(mode="after")
    def _consistent(self):
        _check_outcome(self.status, self.reason)
        return self
