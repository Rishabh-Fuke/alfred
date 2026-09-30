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
from dataclasses import dataclass

from . import tools as T
from .checker import score
from .gate import Gate
from .prompts import build_system_prompt
from .tasks import Task
from .user_sim import SimulatedUser
from .world import World


@dataclass
class Observation:
    system: str
    instruction: str
    tools: list[dict]


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


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
        self.world = World.generate(self.task.world_seed)
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
        self.system_prompt = build_system_prompt(self.world, self.prompt, self.tool_mode)
        return Observation(self.system_prompt, self.task.instruction, T.TOOL_SCHEMAS)

    def call_tool(self, name: str, arguments) -> str:
        """Run one tool call. Never raises for agent mistakes; errors come back as {"error": ...}."""
        if self.world is None:
            raise RuntimeError("call reset() first")
        if self.done:
            return _dump({"error": "The task has already ended."})
        self.calls += 1
        entry = {"i": self.calls, "tool": name}
        try:
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
