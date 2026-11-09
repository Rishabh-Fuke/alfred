"""Alfred on a real folder, through the permission broker.

    python -m alfred.app --model MODEL --base-url ollama --grant ~/School

The person names the folder on the command line and, before each request, allows or refuses Alfred's access on the
page. Only then does the broker make a grant, for that request alone. It's the same broker and environment as
alfred.files, with the person in the simulated user's place. Everything else about the page's API (the key for the
run, the Host and Origin checks) applies here too.

An access-control prototype inside Alfred's own process, not a sandbox: docs/ARCHITECTURE.md, D4.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from ..agents.llm import OpenAICompatAgent
from ..broker import Broker, GrantError
from ..contracts import Limits
from ..eval import no_think_options
from ..files.course import Course
from ..files.env import FilesEnvironment, system_prompt
from ..files.tasks import FileTask
from ..matching import WARNING
from .live import Conversation, _Stopped

MAX_TURNS, MAX_ASKS = 20, 5
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def printable(text, limit: int = 200) -> str:
    """A file name, or a name the model made up, made safe to show on one line. Control and direction-changing
    characters could disguise a name in the very panel that's meant to show what was touched."""
    clean = _UNSAFE.sub("\ufffd", str(text).encode("utf-8", "replace").decode("utf-8"))
    return clean if len(clean) <= limit else clean[:limit - 1] + "…"


def check_folder(folder: Path, broker: Broker) -> None:
    """Try the grant once, at startup, so a folder Alfred can't have is reported in the terminal. Raises GrantError."""
    task = broker.start_task()
    try:
        broker.grant(task, folder, origin="cli")
    finally:
        broker.end_task(task, "canceled")


class LiveFolderEnvironment(FilesEnvironment):
    """One request, with the person answering questions and the page following every call the broker checks."""

    def __init__(self, session: "FolderSession", instruction: str, n: int):
        super().__init__(FileTask(id=f"live-{n}", instruction=instruction, user_answer=""),
                         Course(session.folder.parent, session.folder), session.broker, max_steps=MAX_TURNS,
                         origin="page", max_asks=MAX_ASKS)
        self.session = session

    def reset(self):
        obs = super().reset()
        self.session.emit("access")  # the grant exists now; the page shows it, with the button that revokes it
        return obs

    def _prompt_text(self) -> str:
        return system_prompt(self.interface, self.prompt, folder=self.course.school.name, person=None,
                             first="the user", today=date.today())

    def _answer(self, question: str) -> str:
        answer = str(self.session.wait_for("question", {"question": question}).get("text", "")).strip()
        answer = answer or "Nothing to add. Please go ahead with what I asked."
        self.session.emit("answer", text=answer)
        return answer

    def _through_broker(self, name, arguments) -> str:
        session = self.session
        if session.stop_requested:
            raise _Stopped()
        tool = printable(name)
        session.emit("file_call", tool=tool)
        out = super()._through_broker(name, arguments)
        entry = self.log[-1]
        view = {"tool": tool, "status": entry["status"], "reason": entry["reason"], "bytes": entry["bytes"]}
        if entry["status"] == "ok":  # what the page shows about a result: never the file's text
            data = json.loads(entry["result"])
            view["path"] = printable(data["path"])
            if "entries" in data:
                view.update(kind="list", entries=len(data["entries"]), skipped=data["skipped"], more=data["more"])
            else:
                view.update(kind="read", truncated=data["truncated"], size=data["size"])
        else:
            view["target"] = printable(entry["args"].get("folder") or entry["args"].get("file") or "")
        session.emit("file_result", **view)
        return out

    def _end(self, state: str, message: Optional[str]) -> None:
        super()._end(state, message)
        if message is not None:
            self.session.emit("final", status="reply", message=message, warned=bool(WARNING.search(message)))


class FolderSession(Conversation):
    def __init__(self, folder: Path, model: str, client, broker: Broker, temperature: float = 0.3,
                 no_think: bool = True, model_host: Optional[str] = None):
        super().__init__()
        self.folder, self.model, self.client, self.broker = folder, model, client, broker
        self.temperature, self.no_think, self.model_host = temperature, no_think, model_host
        self.env: Optional[LiveFolderEnvironment] = None

    def access_terms(self) -> dict:
        """What access means, in the words the person sees before allowing it and in the panel afterwards."""
        limits = Limits()
        return {"can": "List this folder and read its text files.",
                "cant": "Change, delete or send anything, follow links out of the folder, or see hidden files.",
                "limits": f"Files up to {limits.max_file_bytes / 1_000_000:g} MB, "
                          f"{limits.max_bytes / 1_000_000:g} MB of text in all, {limits.max_calls} calls.",
                "until": f"This request ends, or {limits.ttl_seconds // 60} minutes at most.",
                "where": (f"Alfred's model runs at {self.model_host}, so what it reads is sent there."
                          if self.model_host else "What it reads stays on this computer.")}

    def _run(self, text: str, n: int) -> None:
        env = self.env = LiveFolderEnvironment(self, text, n)
        extra = {**no_think_options(str(self.client.base_url)), "top_p": 0.95} if self.no_think else None
        agent = OpenAICompatAgent(self.model, client=self.client, temperature=self.temperature, max_tokens=2048,
                                  extra_body=extra)
        ended = "failed"
        try:
            # Nothing is granted, and the model isn't called, until the person says yes on the page.
            decision = self.wait_for("consent", {"name": printable(self.folder.name), "path": str(self.folder),
                                                 **self.access_terms()})
            allowed = decision.get("allow") is True
            self.emit("consent_decision", pending_id=decision["pending_id"], allowed=allowed)
            if not allowed:
                self.emit("final", status="stopped", message="You didn't allow it, so Alfred read nothing.",
                          warned=False)
                return
            agent.run(env)
            if env.truncated:
                self.emit("final", status="stopped", message="Alfred stopped without finishing.", warned=False)
        except _Stopped:
            ended = "canceled"
            self.emit("final", status="stopped", message="Stopped.", warned=False)
        except GrantError as e:
            self.emit("final", status="stopped", message=str(e), warned=False)
        except Exception as e:  # noqa: BLE001 - show the problem on the page, keep the app running
            self.emit("error", message=f"{type(e).__name__}: {e}"[:500])
        finally:
            env.abort(ended)
            self.emit("done")
            with self.lock:
                self.running, self.pending = False, None

    def _live_grant(self):
        env = self.env
        if env is None or env.task_id is None or env.done:
            return None
        grants = self.broker.grants(env.task_id)
        if not grants or grants[-1].revoked_at is not None or grants[-1].expires_at <= datetime.now(timezone.utc):
            return None
        return grants[-1]

    def revoke(self) -> None:
        """Take Alfred's access away now. What it already read can't be unread, and the page says so."""
        grant = self._live_grant()
        if grant is None:
            raise KeyError("Alfred has no access to revoke right now.")
        self.broker.revoke(grant.grant_id)
        self.emit("revoked")

    def world_view(self) -> dict:
        grant = self._live_grant()
        audit = self.broker.audit.path
        return {"mode": "folder", "model": self.model, "polish": False, "allows": self.access_terms(),
                "folder": {"name": printable(self.folder.name), "path": str(self.folder)},
                "grant": {"until": grant.expires_at.astimezone().strftime("%H:%M")} if grant else None,
                "audit_path": str(audit) if audit else None}
