"""One episode: a fresh world, a task, a simulated user and a log of everything the agent does.

    env = Environment(task)
    obs = env.reset()                      # system prompt, instruction, tool schemas
    env.call_tool("search_emails", {...})  # returns the result as a JSON string
    ...
    env.metrics()                          # scored once the agent calls finish (or runs out of steps)

Any agent loop can drive it: an LLM (agents/llm.py), a script (agents/scripted.py), or an RL trainer.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import tools as T
from .checker import score
from .gate import Gate
from .matching import INABILITY
from .prompts import build_system_prompt
from .tasks import Task
from .user_sim import SimulatedUser
from .world import World


@dataclass
class Observation:
    system: str
    instruction: str
    tools: list[dict]


# A reply ending in a question counts as one, unless it's a closing
# offer after an answer or a decline ("It's at 3 PM. Want me to set a reminder?"). A question that starts with
# which/what/who/... is a real question even when phrased as an offer ("What would you like me to say?").
_OFFER = re.compile(r"\b(?:anything else|something else|what else|want me to|would you like me to|shall i|"
                    r"should i (?:also|add|set|create|send|put|schedule|book|draft|make)|do you want me to|"
                    r"let me know if|happy to help|need anything|would you like|want a|do you need|can i help)\b",
                    re.I)
# A request for information that isn't phrased as a question ("Please tell me what you'd like it to say.").
ASKS_FOR_INFO = re.compile(r"\b(?:please (?:tell|let) me|let me know (?:what|which|who|when|where|how)|"
                           r"(?:could|can) you (?:tell|give|send|share) me|i(?:'ll| will)? need (?:to know|the|your))\b",
                           re.I)
_INTERROGATIVE = re.compile(r"^\W*(?:which|what|who|whom|whose|when|where|how)\b", re.I)


def is_question(text: str) -> bool:
    t = (text or "").strip()
    if not t.endswith("?"):
        return False
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", t) if x.strip()]
    last = sentences[-1]
    if (len(sentences) > 1 and any(not x.endswith("?") for x in sentences[:-1]) and _OFFER.search(last)
            and not _INTERROGATIVE.match(last)):
        return False
    return True


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def make_env(task: Task, **kw):
    """The environment for a task: Alfred's email and calendar, or a procedural app (alfred.apps)."""
    if getattr(task, "app", None):
        from .apps.env import AppEnvironment
        return AppEnvironment(task, **kw)
    return Environment(task, **kw)


def task_tools(task) -> list[dict]:
    """The tool schemas a task offers (a Task or its to_json() dict)."""
    get = task.get if isinstance(task, dict) else (lambda k, d=None: getattr(task, k, d))
    interface = get("interface", "tools") or "tools"
    app = get("app")
    if app:
        from .apps.env import TALK_SCHEMAS
        return list(app["tools"]) + (TALK_SCHEMAS if interface == "tools" else [])
    return T.schemas_for(interface)


class Environment:
    def __init__(self, task: Task, prompt: str = "plain", max_steps: int = 20, read_policy: str = "log",
                 feedback: str = "reason", tool_mode: str = "native"):
        """
        read_policy: "log" records every record opened (default, for measuring overreach).
                     "enforce" blocks opening records outside the task's allowed set.
        feedback:    "reason" returns the user's reason when a draft is rejected; "none" just says rejected.
        """
        self.task, self.prompt, self.max_steps = task, prompt, max_steps
        self.read_policy, self.feedback, self.tool_mode = read_policy, feedback, tool_mode
        self.max_calls = max_steps * 3
        self.world: World | None = None

    # --- lifecycle ---------------------------------------------------------------------------

    def reset(self) -> Observation:
        t = self.task
        self.world = World.generate(t.world_seed, t.world_extras, style=t.world_style, pack=t.pack or None)
        if t.pack_id and self.world.pack_id != t.pack_id:
            raise RuntimeError(f"task {t.id} was made with content pack {t.pack_id}, but {self.world.pack_id} is "
                               f"loaded now; regenerate the tasks or point ALFRED_PACK at the original pack")
        self.gate = Gate(self.world)
        self.user = SimulatedUser(self.task, self.world)
        self.log: list[dict] = []
        self.opened: list[str] = []
        self.listed: list[str] = []
        self.asks: list[dict] = []
        self.finish_info: dict | None = None
        self.done = False
        self.truncated = False
        self.calls = 0
        self.invalid_calls = 0
        self.tool_errors = 0
        self.blocked_reads = 0
        self.format_notes: list[str] = []  # reported by the agent loop, e.g. a call it had to recover from text
        self.interface = getattr(t, "interface", "tools")
        self.tools = T.schemas_for(self.interface)
        self.system_prompt = build_system_prompt(self.world, self.prompt, self.tool_mode, self.interface)
        return Observation(self.system_prompt, self.task.instruction, self.tools)

    def call_tool(self, name: str, arguments) -> str:
        """Run one tool call. Never raises for agent mistakes; errors come back as {"error": ...}."""
        if self.world is None:
            raise RuntimeError("call reset() first")
        if self.done:
            return _dump({"error": "The task has already ended."})
        self.calls += 1
        entry = {"i": self.calls, "tool": name}
        try:
            if self.interface == "text" and name in T.TALK_TOOLS:  # not offered here, like any unknown tool
                raise T.InvalidCall(f"Unknown tool '{name}'. Available tools: "
                                    f"{', '.join(t['function']['name'] for t in self.tools)}.")
            args = T.validate(name, T.parse_arguments(arguments))
        except T.InvalidCall as e:
            self.invalid_calls += 1
            result = {"error": str(e)}
            entry.update(raw_arguments=arguments if isinstance(arguments, (str, dict)) else repr(arguments),
                         result=result, invalid=True)
            self.log.append(entry)
            self._check_budget()
            return _dump(result)
        entry["args"] = args
        self.log.append(entry)
        try:
            result = T.HANDLERS[name](self, args)
        except T.ToolError as e:
            self.tool_errors += 1
            result = {"error": str(e)}
        entry["result"] = result
        self._check_budget()
        return _dump(result)

    def record_invalid(self, message: str, raw: str = "") -> None:
        """For agent loops that fail to produce a parseable call at all."""
        self.calls += 1
        self.invalid_calls += 1
        self.log.append({"i": self.calls, "tool": None, "raw_arguments": raw, "result": {"error": message},
                         "invalid": True})
        self._check_budget()

    def note_format(self, kind: str) -> None:
        """For agent loops: record a formatting problem the environment can't see, like a tool call that had
        to be recovered from plain text."""
        self.format_notes.append(kind)

    def reply(self, text: str) -> str | None:
        """The agent replied in plain text. In the tools interface that ends the episode (it should have called
        finish). In the text interface it's how the agent talks to the user.

        A reply before anything is drafted is a question for the user (it gets their answer back, returned) when
        it asks for something; every other reply is the final answer (returns None). Where the user does have
        something to tell (should_ask), any question mark or request for information counts, so how the question is
        worded can't cost the credit. Elsewhere it has to read as a real question, not an answer with a closing
        offer ("It's at 3 PM. Should I set a reminder?"); the user then says there's nothing to add and the agent
        carries on, exactly as with an unneeded ask_user in the tools interface (it costs the same over_ask)."""
        if self.done:
            return None
        if self.interface != "text":
            self.implicit_finish(text)
            return None
        text = text or ""
        if not self.gate.actions and len(self.asks) < 3:
            asking = (("?" in text or bool(ASKS_FOR_INFO.search(text))) if self.task.should_ask
                      else is_question(text))
            if asking:
                answer = self.ask(text.strip())
                self.log.append({"i": self.calls, "tool": None, "reply": text, "answer": answer})
                return answer
        self.log.append({"i": self.calls, "tool": None, "reply": text, "answer": None})
        self.finish_info = {"status": "unspecified", "message": text, "implicit": False, "via": "reply"}
        self.done = True
        return None

    def implicit_finish(self, text: str) -> None:
        """The agent replied in plain text instead of calling a tool. Treat that as its final answer."""
        if self.done:
            return
        self.log.append({"i": self.calls, "tool": None, "implicit_finish": True, "text": text})
        self.finish_info = {"status": "unspecified", "message": text or "", "implicit": True}
        self.done = True

    def truncate(self) -> None:
        if not self.done:
            self.truncated = True
            self.done = True

    def _check_budget(self) -> None:
        if self.calls >= self.max_calls and not self.done:
            self.truncate()

    # --- called by tool handlers -------------------------------------------------------------

    def mark_listed(self, ids) -> None:
        for rid in ids:
            if rid not in self.listed:
                self.listed.append(rid)

    def mark_opened(self, rid: str) -> None:
        if self.read_policy == "enforce" and rid not in self.task.allowed_reads:
            self.blocked_reads += 1
            raise T.ToolError("Access denied: that record isn't needed for this request.")
        if rid not in self.opened:
            self.opened.append(rid)

    def submit_action(self, action: dict, preview: str) -> dict:
        pa = self.gate.submit(action, preview, self.calls)
        verdict = self.user.review(pa.action)
        if verdict.ok:
            return {"status": "approved", "action_id": pa.id, "result": self.gate.approve(pa)}
        self.gate.reject(pa, verdict.feedback, verdict.classification)
        feedback = verdict.feedback if self.feedback == "reason" else "The user rejected this draft."
        return {"status": "rejected", "action_id": pa.id, "feedback": feedback}

    def ask(self, question: str) -> str:
        answer = self.user.answer(question)
        self.asks.append({"i": self.calls, "question": question, "answer": answer})
        return answer

    def end(self, status: str, message: str) -> None:
        self.finish_info = {"status": status, "message": message or "", "implicit": False}
        self.done = True

    # --- results -----------------------------------------------------------------------------

    def metrics(self) -> dict:
        return score(self)

    def trajectory(self, extra: dict | None = None) -> dict:
        out = {
            "task": self.task.to_json(),
            "prompt": self.prompt,
            "system_prompt": getattr(self, "system_prompt", None),
            "log": getattr(self, "log", []),
            "actions": [a.to_json() for a in self.gate.actions] if self.world else [],
            "opened": getattr(self, "opened", []),
            "listed": getattr(self, "listed", []),
            "asks": getattr(self, "asks", []),
            "format_notes": getattr(self, "format_notes", []),
            "finish": getattr(self, "finish_info", None),
            "metrics": self.metrics() if self.world else None,
        }
        if extra:
            out.update(extra)
        return out
