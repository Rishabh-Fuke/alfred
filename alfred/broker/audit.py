"""The broker's audit log: every call, as metadata only.

contracts.AuditEvent has no field that can hold file contents, names, paths, prompts or model output, so the log
can't become a copy of what Alfred read. Events are kept in memory for the page and, given a path, appended to a
JSON Lines file that only its owner can read.
"""
from __future__ import annotations

import os
import threading
from collections import deque
from pathlib import Path
from typing import List, Optional, Union

from ..contracts import AuditEvent


class AuditLog:
    def __init__(self, path: Optional[Union[str, os.PathLike]] = None, keep: int = 10_000):
        self.path = Path(path).expanduser() if path else None
        self._events = deque(maxlen=keep)
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        line = (event.model_dump_json() + "\n").encode()
        with self._lock:
            self._events.append(event)
            if self.path is not None:
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                try:
                    os.write(fd, line)
                finally:
                    os.close(fd)

    def events(self, task_id: Optional[str] = None) -> List[AuditEvent]:
        with self._lock:
            return [e for e in self._events if task_id is None or e.task_id == task_id]
