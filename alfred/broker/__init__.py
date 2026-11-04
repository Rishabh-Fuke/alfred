"""The permission broker (Milestone 1): read-only access to one folder the person grants, for one task.

An access-control prototype inside Alfred's own process, not a sandbox: see broker.py and docs/ARCHITECTURE.md.
"""
from .audit import AuditLog
from .broker import NOT_GRANTED, NOT_OFFERED, Broker, GrantError
from .catalog import descriptors, operation_for, tool_name

__all__ = ["AuditLog", "Broker", "GrantError", "NOT_GRANTED", "NOT_OFFERED", "descriptors", "operation_for",
           "tool_name"]
