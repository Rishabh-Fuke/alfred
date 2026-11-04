"""What Alfred decided at each step, and where a task stands.

The model keeps its trained interface, native tool calls (docs/ARCHITECTURE.md, D2). An adapter derives one
AgentDecision per step from what the model did. Decisions feed the audit panel and the task's state; nothing runs
because of one, and a request_tool decision is still only a request that the broker checks.
"""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import Field, model_validator

from .broker import SCHEMA_VERSION, Contract, ToolRequest

TaskState = Literal["running", "awaiting_clarification", "calling_tool",
                    "completed", "declined", "failed", "canceled"]
FINAL_STATES = ("completed", "declined", "failed", "canceled")

Decision = Literal["request_tool", "propose_action", "ask_user", "decline", "respond", "propose_delegation"]
# The one field each decision carries. The others have to be empty.
CARRIES = {"request_tool": "tool_request", "propose_action": "proposed_action", "ask_user": "user_question",
           "decline": "message", "respond": "message", "propose_delegation": "delegation_proposal"}


class AgentDecision(Contract):
    schema_version: Literal["1"] = SCHEMA_VERSION
    task_id: str = Field(min_length=1)
    decision: Decision
    tool_request: Optional[ToolRequest] = None
    proposed_action: Optional[Dict[str, Any]] = None
    user_question: Optional[str] = None
    message: Optional[str] = None                     # the answer, or why Alfred declined
    delegation_proposal: Optional[Dict[str, Any]] = None  # Milestone 4

    @model_validator(mode="after")
    def _carries_its_field(self):
        wanted = CARRIES[self.decision]
        for name in set(CARRIES.values()):
            if (getattr(self, name) is not None) != (name == wanted):
                raise ValueError(f"a {self.decision} decision carries {wanted} and nothing else")
        if self.tool_request is not None and self.tool_request.task_id != self.task_id:
            raise ValueError("the tool request is for another task")
        return self
