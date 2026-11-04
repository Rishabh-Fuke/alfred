"""The permission broker: the only code that decides what Alfred may read.

Milestone 1: read-only access to one folder the person grants (from the page or the command line) for one task.
The model names files by handles the broker issued; it never sends a path. Every call is checked against the task's
grant (its operations, expiry, revocation and budgets) and recorded in the audit log, allowed or not.

This is an access-control prototype, not a sandbox (docs/ARCHITECTURE.md, D4). It runs in the same Python process as
the agent loop: it keeps the model away from anything it wasn't granted, but a bug in Alfred's own code could still
go around it. OS isolation is Milestone 2.
"""
from __future__ import annotations

import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Container, Dict, Iterable, List, Optional, Tuple

from ..contracts import FINAL_STATES, OPERATIONS, STATUS_OF, AuditEvent, Grant, Limits, ToolRequest, ToolResult
from . import catalog, fs
from .audit import AuditLog

NOT_GRANTED = "Not found, or not granted for this task. Open files and folders by the handles files__list gives you."
NOT_OFFERED = "That tool isn't available for this task."


class GrantError(ValueError):
    """A grant the broker won't make. The message is for the person."""


class _Refusal(Exception):
    def __init__(self, reason: str, message: str = NOT_GRANTED):
        super().__init__(reason)
        self.reason, self.message = reason, message


@dataclass(frozen=True)
class _Entry:
    grant_id: str
    parts: Tuple[str, ...]  # names from the granted folder down; () is the folder itself
    kind: str               # "file" or "folder"
    device: int
    inode: int


@dataclass
class _Held:
    grant: Grant
    fd: Optional[int]       # the granted folder, held open until the grant ends
    root: str = ""          # the granted folder's handle
    calls: int = 0
    bytes: int = 0
    handles: Dict[Tuple[Tuple[str, ...], int, int], str] = field(default_factory=dict)  # (parts, dev, ino) -> handle


def _utc(t: float) -> datetime:
    return datetime.fromtimestamp(t, tz=timezone.utc)


def _shown(name: str) -> str:
    """A name as text the model and the page can show, even if it isn't valid UTF-8 on disk."""
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _path(parts: Tuple[str, ...]) -> str:
    return "/".join(_shown(p) for p in parts) or "."


def _size(n: int) -> str:
    return f"{n / 1_000_000:g} MB" if n >= 1_000_000 else f"{n:,} bytes"


def _holds_home(st: os.stat_result) -> bool:
    """Whether a folder is the home folder or one of the folders above it."""
    home = Path.home()
    for p in (home, *home.parents):
        try:
            s = os.stat(p)
        except OSError:
            continue
        if (s.st_dev, s.st_ino) == (st.st_dev, st.st_ino):
            return True
    return False


def _no_permission(path: str) -> str:
    hint = (" macOS decides which folders an app may read: check System Settings > Privacy & Security > Files and "
            "Folders for the app you started Alfred from." if sys.platform == "darwin" else "")
    return f"Alfred isn't allowed to read {path}.{hint}"


def _inside(reason: str, limits: Limits) -> str:
    return {"symlink": "That entry is a link now, and Alfred doesn't follow links.",
            "hard_link": "That file has another name somewhere on this computer (a hard link), so Alfred doesn't "
                         "read it.",
            "other_device": "That entry is on another disk, which the grant doesn't cover.",
            "changed": "That entry changed after it was listed. List its folder again.",
            "too_large": f"That file is over {_size(limits.max_file_bytes)}, so Alfred doesn't read it.",
            "not_text": "That file isn't plain text, so Alfred can't read it. Only text files can be read for now.",
            }[reason]


class Broker:
    def __init__(self, audit: Optional[AuditLog] = None, clock: Callable[[], float] = time.time):
        self.audit = audit if audit is not None else AuditLog()
        self._clock = clock
        self._lock = threading.RLock()  # one call at a time, so a grant can't close its folder under a call
        self._tasks: Dict[str, str] = {}       # task id -> "running" or how it ended
        self._held: Dict[str, _Held] = {}      # grant id -> the grant and what it holds
        self._owner: Dict[str, str] = {}       # handle -> grant id, kept after the grant ends so denials say why
        self._entries: Dict[str, _Entry] = {}  # handle -> what it names, forgotten when the grant ends

    def __enter__(self) -> "Broker":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ tasks and grants

    def start_task(self) -> str:
        with self._lock:
            task_id = self._unique("task_", 8, self._tasks)
            self._tasks[task_id] = "running"
            return task_id

    def end_task(self, task_id: str, state: str = "completed") -> None:
        """End a task as completed, declined, failed or canceled. Its grants are revoked."""
        if state not in FINAL_STATES:
            raise ValueError(f"a task ends as one of {', '.join(FINAL_STATES)}, not {state!r}")
        with self._lock:
            if self._tasks[task_id] == "running":
                self._tasks[task_id] = state
            for held in self._grants_of(task_id):
                self._revoke(held)

    def grant(self, task_id: str, folder, *, origin: str, operations: Iterable[str] = OPERATIONS,
              limits: Optional[Limits] = None) -> Grant:
        """Let a running task read one folder. Only the page and the command line call this, for the person.

        The path is the person's own, so links in it are followed; everything below it is opened without following
        any. A task holds one live grant at a time.
        """
        ops = tuple(sorted(set(operations)))
        if not ops or any(op not in OPERATIONS for op in ops):
            raise GrantError(f"A grant allows some of {', '.join(OPERATIONS)}, and nothing else.")
        if origin not in ("page", "cli"):
            raise GrantError("Grants come from the page or the command line.")
        limits = limits if limits is not None else Limits()
        path = os.path.abspath(os.path.expanduser(os.fspath(folder)))
        with self._lock:
            if self._tasks.get(task_id) != "running":
                raise GrantError("That task isn't running.")
            if any(self._ended(held) is None for held in self._grants_of(task_id)):
                raise GrantError("This task already has a folder. Revoke that grant first.")
            try:
                fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            except FileNotFoundError:
                raise GrantError(f"There's no folder at {path}.") from None
            except NotADirectoryError:
                raise GrantError(f"{path} isn't a folder.") from None
            except PermissionError:
                raise GrantError(_no_permission(path)) from None
            try:
                st = os.fstat(fd)
                if _holds_home(st):
                    raise GrantError(f"{path} holds your whole home folder. Grant only the folder Alfred needs, "
                                     f"such as one class's folder.")
                try:
                    os.listdir(fd)  # macOS can refuse to list a folder that opened fine
                except PermissionError:
                    raise GrantError(_no_permission(path)) from None
                now = self._clock()
                grant = Grant(grant_id=self._unique("grant_", 8, self._held), task_id=task_id,
                              root_device=st.st_dev, root_inode=st.st_ino, operations=ops, limits=limits,
                              origin=origin, granted_at=_utc(now), expires_at=_utc(now + limits.ttl_seconds))
            except BaseException:
                os.close(fd)
                raise
            held = _Held(grant, fd)
            self._held[grant.grant_id] = held
            held.root = self._issue(held, (), "folder", st.st_dev, st.st_ino)
            return grant

    def revoke(self, grant_id: str) -> None:
        """Stop a grant now. A call already running finishes first, and nothing read before can be unread."""
        with self._lock:
            self._revoke(self._held[grant_id])

    def grants(self, task_id: str) -> List[Grant]:
        with self._lock:
            return [held.grant for held in self._grants_of(task_id)]

    def tools(self, task_id: str) -> List[dict]:
        """The tools to offer the model for a task: the operations its live grant allows, and nothing else."""
        with self._lock:
            live = [held for held in self._grants_of(task_id) if self._ended(held) is None]
            if not live:
                return []
            return catalog.descriptors({op for held in live for op in held.grant.operations}, live[0].grant.limits)

    def close(self) -> None:
        """Revoke every grant and close every folder."""
        with self._lock:
            for held in self._held.values():
                self._revoke(held)

    # ------------------------------------------------------------------ calls

    def call_tool(self, task_id: str, name: Any, arguments: Any) -> ToolResult:
        """A tool call as the model made it. Show the model only the result's for_model()."""
        operation = catalog.operation_for(name) if isinstance(name, str) else None
        problem = None
        try:
            args = catalog.parse_arguments(arguments)
        except catalog.BadArguments as e:
            args, problem = {}, str(e)
        handle, rest = catalog.split_handle(operation or "", args)
        request = ToolRequest(task_id=task_id, operation=operation or "?", handle=handle, arguments=rest)
        return self._handle(request, problem)

    def call(self, request: ToolRequest) -> ToolResult:
        return self._handle(request, None)

    def _handle(self, request: ToolRequest, problem: Optional[str]) -> ToolResult:
        with self._lock:
            rid = "req_" + secrets.token_hex(8)
            # What the audit log may say: only ids the broker issued and operations it knows, never what was sent.
            note = {"task_id": request.task_id if request.task_id in self._tasks else None, "grant_id": None,
                    "operation": request.operation if request.operation in OPERATIONS else None,
                    "handle": request.handle if request.handle in self._owner else None}
            result = None
            try:
                data, size = self._run(request, problem, note)
                result = ToolResult(request_id=rid, status="ok", data=data, bytes_returned=size)
            except _Refusal as r:
                result = self._refused(rid, r.reason, r.message)
            except OSError as e:
                result = self._refused(rid, "io_error", f"Alfred couldn't open that ({e.strerror or 'unknown error'}).")
            finally:
                # A bug raises past this point, after it's been recorded as a failed call.
                outcome = result or self._refused(rid, "io_error", "Alfred hit an internal error.")
                self.audit.record(AuditEvent(at=_utc(self._clock()), request_id=rid, status=outcome.status,
                                             reason=outcome.reason, bytes_returned=outcome.bytes_returned, **note))
            return result

    def _run(self, request: ToolRequest, problem: Optional[str], note: dict) -> Tuple[dict, int]:
        op = note["operation"]
        if op is None:
            raise _Refusal("unknown_operation", NOT_OFFERED)
        held = self._grant_for(request)
        note["grant_id"] = held.grant.grant_id
        ended = self._ended(held)
        if ended:
            raise _Refusal(ended)
        if op not in held.grant.operations:
            raise _Refusal("not_granted", NOT_OFFERED)
        limits = held.grant.limits
        if held.calls >= limits.max_calls:
            raise _Refusal("budget", f"This task has used all {limits.max_calls} calls its grant allows.")
        held.calls += 1
        if problem:
            raise _Refusal("bad_arguments", problem)
        if catalog.HANDLE_ARG[op][1] and request.handle is None:
            raise _Refusal("bad_arguments", f"{catalog.tool_name(op)} needs a file handle (res_...) from files__list.")
        try:
            args = catalog.check_arguments(op, request.arguments, limits)
        except catalog.BadArguments as e:
            raise _Refusal("bad_arguments", str(e)) from None
        handle = request.handle or held.root
        note["handle"] = handle
        entry = self._entries.get(handle)
        if entry is None:
            raise _Refusal("unknown_handle")
        try:
            if op == "files.list":
                return self._list(held, handle, entry), 0
            return self._read(held, handle, entry, args["max_chars"])
        except fs.Refused as r:
            raise _Refusal(r.reason, _inside(r.reason, limits)) from None

    def _grant_for(self, request: ToolRequest) -> _Held:
        """The grant a request is under. Its handle names it; without one, the task's grant."""
        mine = self._grants_of(request.task_id) if request.task_id in self._tasks else []
        if not mine:
            raise _Refusal("no_grant")
        if request.handle is not None:
            grant_id = self._owner.get(request.handle)
            if grant_id is None or self._held[grant_id].grant.task_id != request.task_id:
                raise _Refusal("unknown_handle")
            return self._held[grant_id]
        live = [held for held in mine if self._ended(held) is None]
        return live[0] if live else mine[-1]  # with none live, the newest says how it ended

    def _list(self, held: _Held, handle: str, entry: _Entry) -> dict:
        limits = held.grant.limits
        if entry.kind != "folder":
            raise _Refusal("wrong_kind", "That handle is a file. Read it with files__read_text.")
        if len(entry.parts) >= limits.max_depth:
            raise _Refusal("too_deep", f"That folder is too deep to list: Alfred lists up to {limits.max_depth} "
                                       f"levels inside the granted folder.")
        fd = fs.open_entry(held.fd, held.grant.root_device, entry.parts, "folder", entry.device, entry.inode)
        try:
            found, skipped = fs.list_folder(fd, held.grant.root_device)
        finally:
            os.close(fd)
        entries = []
        for f in found[:limits.max_entries]:
            parts = entry.parts + (f.name,)
            item = {"handle": self._issue(held, parts, f.kind, f.device, f.inode), "name": _shown(f.name),
                    "path": _path(parts), "kind": f.kind}
            if f.kind == "file":
                item["size"] = f.size
            item["modified"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.modified))
            entries.append(item)
        return {"folder": handle, "path": _path(entry.parts), "entries": entries,
                "more": max(0, len(found) - limits.max_entries), "skipped": skipped}

    def _read(self, held: _Held, handle: str, entry: _Entry, max_chars: int) -> Tuple[dict, int]:
        limits = held.grant.limits
        if entry.kind != "file":
            raise _Refusal("wrong_kind", "That handle is a folder. List it with files__list.")
        if held.bytes >= limits.max_bytes:
            raise _Refusal("budget", f"This task has read all the text its grant allows ({_size(limits.max_bytes)}).")
        fd = fs.open_entry(held.fd, held.grant.root_device, entry.parts, "file", entry.device, entry.inode)
        try:
            text, truncated, size = fs.read_text(fd, max_chars, limits.max_file_bytes)
        finally:
            os.close(fd)
        n = len(text.encode("utf-8"))
        held.bytes += n
        return {"file": handle, "path": _path(entry.parts), "text": text, "truncated": truncated, "size": size}, n

    # ------------------------------------------------------------------ bookkeeping

    def _grants_of(self, task_id: str) -> List[_Held]:
        return [held for held in self._held.values() if held.grant.task_id == task_id]

    def _ended(self, held: _Held) -> Optional[str]:
        """Why a grant no longer works ("revoked" or "expired"), or None while it does."""
        if held.grant.revoked_at is not None:
            return "revoked"
        if self._clock() >= held.grant.expires_at.timestamp():
            self._release(held)
            return "expired"
        if held.fd is None:
            return "revoked"  # its folder is closed, so nothing can be read under it
        return None

    def _revoke(self, held: _Held) -> None:
        if held.grant.revoked_at is None and self._ended(held) is None:
            held.grant = held.grant.model_copy(update={"revoked_at": _utc(self._clock())})
        self._release(held)

    def _release(self, held: _Held) -> None:
        """Close the granted folder and forget every name the grant's handles pointed to."""
        if held.fd is not None:
            os.close(held.fd)
            held.fd = None
        for handle in held.handles.values():
            self._entries.pop(handle, None)
        held.handles.clear()

    def _issue(self, held: _Held, parts: Tuple[str, ...], kind: str, device: int, inode: int) -> str:
        """The handle for an entry: the same one each time it's listed, and a new one if it's a new file."""
        key = (parts, device, inode)
        handle = held.handles.get(key)
        if handle is None:
            handle = self._unique("res_", 16, self._owner)
            held.handles[key] = handle
            self._owner[handle] = held.grant.grant_id
            self._entries[handle] = _Entry(held.grant.grant_id, parts, kind, device, inode)
        return handle

    @staticmethod
    def _unique(prefix: str, nbytes: int, taken: Container[str]) -> str:
        while True:
            value = prefix + secrets.token_hex(nbytes)
            if value not in taken:
                return value

    @staticmethod
    def _refused(rid: str, reason: str, message: str) -> ToolResult:
        return ToolResult(request_id=rid, status=STATUS_OF[reason], reason=reason, message=message)
