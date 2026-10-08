"""Alfred with a person in the loop: the same environment and agent, but every draft waits for your approval and
every question waits for your answer, instead of going to the simulated user.

A Session holds one simulated person's world, which persists across requests (an email you approved shows up in
Sent; a meeting you moved stays moved), plus the event log the web page shows and what Alfred remembers from
earlier answers.
"""
from __future__ import annotations

import copy
import itertools
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..agents.llm import OpenAICompatAgent, chat_create, strip_think
from ..env import Environment, Observation
from ..gate import Gate
from ..matching import WARNING
from ..prompts import build_system_prompt
from ..tasks import Task, generate_tasks
from ..world import EXTRAS, World, human_date, human_time, parse_dt

POLISH_MODEL = "nvidia/nemotron-3-super-120b-a12b"
POLISH_PROMPT = ("You improve emails a personal assistant drafted. Rewrite the email so it's clear, warm and "
                 "concise. Keep every fact, name, date and time exactly as written, and add nothing new. Reply with "
                 "only a JSON object: {\"subject\": \"...\", \"body\": \"...\"}.")
EDITABLE = {"email": ("subject", "body"), "forward": ("note",), "new_event": ("title", "location"),
            "event_change": ()}
# Requests to try, one per kind of judgment, generated for this person's world so they work.
SUGGESTIONS = [("ambiguous_recipient", "Two of your contacts share this name."),
               ("impossible_request", "There's no tool for this."),
               ("injection_lookup", "The delivery email hides instructions meant for AI assistants."),
               ("third_party_request", "A stranger asked for your home address and phone number."),
               ("ambiguous_event", "You have two meetings with this person this week."),
               ("reschedule_and_notify", "Two steps across calendar and email.")]


@dataclass
class Pending:
    id: str
    kind: str                      # "approval" or "question"
    payload: dict
    done: threading.Event = field(default_factory=threading.Event)
    response: dict | None = None


class Memory:
    """Answers to Alfred's earlier questions, so it can use them instead of asking again. Stored as JSON."""

    def __init__(self, path: Path | None):
        self.path = path
        self.items: list[dict] = json.loads(path.read_text()) if path and path.exists() else []

    def add(self, question: str, answer: str) -> None:
        self.items = [m for m in self.items if (m["question"], m["answer"]) != (question, answer)]
        self.items.append({"question": question, "answer": answer, "when": time.strftime("%Y-%m-%d %H:%M")})
        self.items = self.items[-20:]
        if self.path:
            self.path.write_text(json.dumps(self.items, indent=1))

    def clear(self) -> None:
        self.items = []
        if self.path and self.path.exists():
            self.path.unlink()

    def prompt_notes(self, first: str) -> str:
        if not self.items:
            return ""
        lines = [f"- You asked: {m['question']} {first} said: {m['answer']}" for m in self.items[-10:]]
        return (f"\n\nWhat {first} told you in earlier conversations (use it instead of asking the same thing "
                f"again):\n" + "\n".join(lines))


def _summary(tool: str, args: dict, result: dict, world: World, private: set[str]) -> dict:
    """What the page shows for a tool result: a line of text and the records involved."""
    if "error" in result:
        return {"text": f"Error: {result['error']}", "records": []}
    if tool.startswith("search_"):
        rows = result.get("results", [])
        return {"text": f"{len(rows)} result{'s' if len(rows) != 1 else ''}",
                "records": [{"id": r["id"], "label": r.get("subject") or r.get("title") or r.get("name"),
                             "opened": False, "private": r["id"] in private} for r in rows[:8]]}
    if tool in ("read_email", "get_event", "get_contact"):
        rid = result.get("id")
        label = result.get("subject") or result.get("title") or result.get("name")
        return {"text": f"Opened {label}", "records": [{"id": rid, "label": label, "opened": True,
                                                         "private": rid in private}]}
    if tool == "ask_user":
        return {"text": result.get("answer", ""), "records": []}
    if "status" in result and tool != "finish":
        return {"text": result["status"].capitalize() + (f": {result.get('feedback')}" if result.get("feedback") else ""),
                "records": []}
    return {"text": "", "records": []}


class LiveEnvironment(Environment):
    """An episode against the session's persistent world, with the person reviewing drafts and answering."""

    def __init__(self, session: "Session", instruction: str, n: int):
        task = Task(id=f"live-{n}", template="live", category="live", world_seed=session.seed, instruction=instruction)
        super().__init__(task, prompt="plain", max_steps=20, tool_mode=session.tool_mode)
        self.session = session

    def reset(self) -> Observation:
        obs = super().reset()  # sets up logs and counters; the world is then swapped for the session's
        self.world = self.session.world
        self.gate = Gate(self.world)
        self.user = None
        notes = self.session.memory.prompt_notes(self.world.user["first"]) if self.session.use_memory else ""
        self.system_prompt = build_system_prompt(self.world, "plain", self.tool_mode) + notes
        return Observation(self.system_prompt, self.task.instruction, obs.tools)

    def call_tool(self, name: str, arguments) -> str:
        s = self.session
        args = arguments if isinstance(arguments, dict) else _loads(arguments)
        if name not in ("ask_user", "finish") and not self.done:
            s.emit("call", tool=name, args=args)
        out = super().call_tool(name, arguments)
        result = _loads(out)
        if name not in ("ask_user", "finish") and not name.startswith(("draft_", "propose_")):
            s.emit("result", tool=name, **_summary(name, args, result, self.world, s.private_ids()))
        return out

    def submit_action(self, action: dict, preview: str) -> dict:
        pa = self.gate.submit(action, preview, self.calls)
        decision = self.session.wait_for("approval", {"action_id": pa.id, "kind": pa.kind, "preview": preview,
                                                      "action": action, "editable": EDITABLE.get(pa.kind, ())})
        if decision.get("approve"):
            edits = {k: v for k, v in (decision.get("edits") or {}).items() if k in EDITABLE.get(pa.kind, ())}
            if edits:
                pa.action.update(edits)
            result = self.gate.approve(pa)
            out = {"status": "approved", "action_id": pa.id, "result": result}
            if edits:
                out["note"] = "The user edited the draft before approving it."
            self.session.emit("decision", pending_id=decision["pending_id"], action_id=pa.id, approved=True,
                              edited=bool(edits), result=result)
            return out
        feedback = (decision.get("feedback") or "").strip() or "The user rejected this draft."
        self.gate.reject(pa, feedback, "user")
        self.session.emit("decision", pending_id=decision["pending_id"], action_id=pa.id, approved=False,
                          feedback=feedback)
        return {"status": "rejected", "action_id": pa.id, "feedback": feedback}

    def ask(self, question: str) -> str:
        answer = self.session.wait_for("question", {"question": question}).get("text", "").strip()
        answer = answer or "Nothing to add. Please go ahead with what I asked."
        self.asks.append({"i": self.calls, "question": question, "answer": answer})
        self.session.emit("answer", text=answer)
        if self.session.use_memory:
            self.session.memory.add(question, answer)
        return answer

    def end(self, status: str, message: str) -> None:
        super().end(status, message)
        self.session.emit("final", status=status, message=message or "", warned=bool(WARNING.search(message or "")))

    def implicit_finish(self, text: str) -> None:
        if not self.done:
            self.session.emit("final", status="reply", message=strip_think(text), warned=bool(WARNING.search(text)))
        super().implicit_finish(text)


def _loads(text):
    try:
        return json.loads(text) if isinstance(text, str) else (text or {})
    except json.JSONDecodeError:
        return {}


class Session:
    def __init__(self, seed: int, model: str, client, tool_mode: str = "native", temperature: float = 0.3,
                 no_think: bool = True, memory_path: Path | None = None, use_memory: bool = True,
                 polish_client=None):
        self.seed, self.model, self.client, self.tool_mode = seed, model, client, tool_mode
        self.temperature, self.no_think, self.use_memory = temperature, no_think, use_memory
        self.polish_client = polish_client
        self.world = World.generate(seed, tuple(EXTRAS))  # extras add a few records; the base world is unchanged
        self.memory = Memory(memory_path)
        self.events: list[dict] = []
        self.pending: Pending | None = None
        self.running = False
        self.lock = threading.Lock()
        self._ids = itertools.count(1)
        self._requests = itertools.count(1)
        self._private: set[str] | None = None
        self.stop_requested = False

    # --- events the page polls --------------------------------------------------------------------------------

    def emit(self, type_: str, **data) -> dict:
        with self.lock:
            ev = {"id": next(self._ids), "type": type_, "time": time.strftime("%H:%M:%S"), **data}
            self.events.append(ev)
            return ev

    def events_since(self, since: int) -> list[dict]:
        with self.lock:
            return [e for e in self.events if e["id"] > since]

    def private_ids(self) -> set[str]:
        if self._private is None:
            self._private = self.world.sensitive_ids()
        return self._private

    # --- running a request ------------------------------------------------------------------------------------

    def start(self, text: str) -> None:
        with self.lock:
            if self.running:
                raise RuntimeError("Alfred is still working on the last request.")
            self.running = True
            self.stop_requested = False
        n = next(self._requests)
        self.emit("request", text=text, n=n)
        threading.Thread(target=self._run, args=(text, n), daemon=True).start()

    def _run(self, text: str, n: int) -> None:
        env = LiveEnvironment(self, text, n)
        extra = {"chat_template_kwargs": {"enable_thinking": False}, "top_p": 0.95} if self.no_think else None
        agent = OpenAICompatAgent(self.model, client=self.client, temperature=self.temperature, max_tokens=2048,
                                  tool_mode=self.tool_mode, extra_body=extra)
        try:
            agent.run(env)
            if not env.done or env.truncated:
                self.emit("final", status="stopped", message="Alfred stopped without finishing.", warned=False)
        except _Stopped:
            self.emit("final", status="stopped", message="Stopped.", warned=False)
        except Exception as e:  # noqa: BLE001 - show the problem on the page, keep the app running
            self.emit("error", message=f"{type(e).__name__}: {e}"[:500])
        finally:
            touched = {"opened": list(env.opened), "private_opened": [r for r in env.opened if r in self.private_ids()],
                       "drafts": len(env.gate.actions) if env.world else 0,
                       "approved": sum(a.status == "approved" for a in env.gate.actions) if env.world else 0}
            self.emit("done", **touched)
            with self.lock:
                self.running, self.pending = False, None

    def wait_for(self, kind: str, payload: dict) -> dict:
        """Called from the agent's thread: show the page a draft or a question and wait for the person."""
        p = Pending(id=f"p{next(self._ids)}", kind=kind, payload=payload)
        with self.lock:
            self.pending = p
        self.emit(kind, pending_id=p.id, **payload)
        while not p.done.wait(0.5):
            if self.stop_requested:
                raise _Stopped()
        with self.lock:
            self.pending = None
        return {**(p.response or {}), "pending_id": p.id}

    def respond(self, pending_id: str, response: dict) -> None:
        with self.lock:
            p = self.pending
        if p is None or p.id != pending_id:
            raise KeyError("That's no longer waiting for an answer.")
        p.response = response
        p.done.set()

    def stop(self) -> None:
        self.stop_requested = True

    # --- the cloud handoff --------------------------------------------------------------------------------------

    def polish(self, pending_id: str) -> dict:
        """Ask Nemotron 3 Super on Token Factory to improve a pending email. Only that draft's subject and body
        leave this machine; nothing from the inbox, calendar or contacts does."""
        with self.lock:
            p = self.pending
        if p is None or p.id != pending_id or p.kind != "approval" or p.payload["kind"] != "email":
            raise KeyError("Only a pending email can be polished.")
        if self.polish_client is None:
            raise RuntimeError("Set NEBIUS_API_KEY to polish drafts with Nemotron 3 Super on Token Factory.")
        a = p.payload["action"]
        sent = {"subject": a.get("subject", ""), "body": a.get("body", "")}
        resp = chat_create(self.polish_client, POLISH_MODEL, max_tokens=2048, temperature=0.3,
                           messages=[{"role": "system", "content": POLISH_PROMPT},
                                     {"role": "user", "content": json.dumps(sent, ensure_ascii=False)}],
                           extra_body={"chat_template_kwargs": {"enable_thinking": False}})
        text = strip_think(resp.choices[0].message.content)
        start, end = text.find("{"), text.rfind("}")
        try:
            out = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            out = {"subject": sent["subject"], "body": text}
        out = {"subject": str(out.get("subject") or sent["subject"]), "body": str(out.get("body") or sent["body"])}
        self.emit("polish", pending_id=pending_id, model=POLISH_MODEL, sent=sent, suggestion=out)
        return out

    # --- the world, for the page ------------------------------------------------------------------------------

    def world_view(self) -> dict:
        w, private = self.world, self.private_ids()
        names = w.names_by_email()

        def when(v):
            d = parse_dt(v)
            return f"{human_date(d.date())}, {human_time(d)}"

        emails = [{"id": e["id"], "folder": e["folder"], "from": e["sender_name"], "to": [names.get(x, x) for x in
                                                                                         e["recipients"]],
                   "subject": e["subject"], "body": e["body"], "date": when(e["date"]), "private": e["id"] in private}
                  for e in sorted(w.emails(), key=lambda e: e["date"], reverse=True)]
        events = [{"id": ev["id"], "title": ev["title"], "start": when(ev["start_at"]), "end": human_time(
            parse_dt(ev["end_at"])), "location": ev["location"], "attendees": [names.get(x, x) for x in ev["attendees"]],
                   "notes": ev["notes"], "private": ev["id"] in private}
                  for ev in sorted(w.events(), key=lambda ev: ev["start_at"])]
        contacts = [{k: c[k] for k in ("id", "name", "email", "role", "phone", "address", "notes")} | {
            "private": c["id"] in private} for c in w.contacts()]
        tasks = {t.template: t for t in generate_tasks(self.seed, 1, [name for name, _ in SUGGESTIONS])}
        suggestions = [{"text": tasks[name].instruction, "why": why} for name, why in SUGGESTIONS]
        return {"user": w.user, "now": when(w.now.isoformat()), "emails": emails, "events": events,
                "contacts": contacts, "model": self.model, "memory": copy.deepcopy(self.memory.items),
                "use_memory": self.use_memory, "polish": self.polish_client is not None, "seed": self.seed,
                "suggestions": suggestions}


class _Stopped(Exception):
    pass
