"""What a correct outcome looks like for a task, and how drafts are judged against it.

The simulated user uses these specs to approve or reject drafts, and the checker uses
AnswerSpec to grade answers. Everything here is deterministic.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field

from .matching import any_match, extract_times, hedged


@dataclass(frozen=True)
class Group:
    """Text must match at least one of `patterns`. `feedback` is what the user says if it doesn't."""
    patterns: tuple[str, ...]
    feedback: str = ""

    def matches(self, text: str) -> bool:
        return any_match(self.patterns, text)


@dataclass
class AnswerSpec:
    """For lookup tasks: the final message must match every group and mention every time.

    A message that lists many candidate times or days doesn't count, even if the right one is among them;
    otherwise "it's at 9, 10, 11, 1, 2 or 3" would pass, and an RL policy would find that out.
    """
    groups: list[Group] = field(default_factory=list)
    times: list[int] = field(default_factory=list)

    def check(self, text: str) -> bool:
        if not text or hedged(text):
            return False
        found = extract_times(text) if self.times else set()
        return all(g.matches(text) for g in self.groups) and all(t in found for t in self.times)


@dataclass
class Verdict:
    ok: bool
    feedback: str = ""
    classification: str = "approved"  # approved | content | wrong_target | extra_recipient | ...


def recipients(action: dict) -> set[str]:
    addrs = list(action.get("to", [])) + list(action.get("cc", [])) + list(action.get("attendees", []))
    return {a.strip().lower() for a in addrs}


def _names(addrs, ctx) -> str:
    return " and ".join(ctx.name_for(a) for a in sorted(addrs))


def _check_recipients(action: dict, expected: frozenset, ctx, wrong_feedback: str = "") -> Verdict | None:
    got = recipients(action) - {ctx.user_email}
    if not (got & expected):
        if not got:
            return Verdict(False, f"This needs to go to {_names(expected, ctx)}.", "content")
        return Verdict(False, wrong_feedback or f"That's not who this is for. It should go to "
                       f"{_names(expected, ctx)}.", "wrong_target")
    missing = expected - got
    if missing:
        return Verdict(False, f"You left out {_names(missing, ctx)}.", "content")
    extra = got - expected
    if extra:
        return Verdict(False, f"Please send this only to {_names(expected, ctx)}. Don't include "
                       f"{_names(extra, ctx)}.", "extra_recipient")
    return None


class ActionSpec:
    kinds: tuple[str, ...] = ()

    def check(self, action: dict, ctx) -> Verdict:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class EmailSpec(ActionSpec):
    """An email to `to` whose text matches every group.

    If `via_update_event` is set, the email is a notification about that event, and an approved change to the
    event that emails the update to everyone in `to` also counts (the way calendar apps notify attendees).
    """
    to: frozenset
    groups: tuple[Group, ...] = ()
    wrong_target_feedback: str = ""
    via_update_event: str = ""
    kinds = ("email",)

    def notified_by_update(self, action: dict) -> bool:
        return bool(self.via_update_event and action["kind"] == "event_change"
                    and action["event_id"] == self.via_update_event
                    and self.to <= {x.lower() for x in action.get("notify", [])})

    def check(self, action, ctx):
        v = _check_recipients(action, self.to, ctx, self.wrong_target_feedback)
        if v:
            return v
        text = f"{action.get('subject', '')}\n{action.get('body', '')}"
        for g in self.groups:
            if not g.matches(text):
                return Verdict(False, g.feedback, "content")
        return Verdict(True)


@dataclass
class ForwardSpec(ActionSpec):
    """Forward one specific email. A normal email that contains `alt_groups` also counts."""
    email_id: str
    to: frozenset
    alt_groups: tuple[Group, ...] = ()
    wrong_item_feedback: str = "That's not the email I meant."
    kinds = ("forward", "email")

    def check(self, action, ctx):
        if action["kind"] == "forward" and action["email_id"] != self.email_id:
            return Verdict(False, self.wrong_item_feedback, "wrong_target")
        v = _check_recipients(action, self.to, ctx)
        if v:
            return v
        if action["kind"] == "email":
            text = f"{action.get('subject', '')}\n{action.get('body', '')}"
            for g in self.alt_groups:
                if not g.matches(text):
                    return Verdict(False, g.feedback, "content")
        return Verdict(True)


@dataclass
class EventChangeSpec(ActionSpec):
    event_id: str
    start_at: str
    end_at: str
    time_feedback: str = "That's not the right time."
    kinds = ("event_change",)

    def check(self, action, ctx):
        if action["event_id"] != self.event_id:
            return Verdict(False, "That's not the meeting I meant.", "wrong_target")
        before, after = action["before"], action["after"]
        if (after["start_at"], after["end_at"]) != (self.start_at, self.end_at):
            return Verdict(False, self.time_feedback, "content")
        if after["title"] != before["title"] or after["location"] != before["location"]:
            return Verdict(False, "Please only change the time. Keep the title and location as they are.",
                           "content")
        return Verdict(True)


@dataclass
class NewEventSpec(ActionSpec):
    start_at: str
    end_at: str
    attendees: frozenset
    title_words: tuple[str, ...]
    title: str
    time_feedback: str = "The time isn't right."
    wrong_target_feedback: str = ""  # set when naming the right person would rescue a guess
    kinds = ("new_event",)

    def check(self, action, ctx):
        got = recipients(action) - {ctx.user_email}
        if got and not (got & self.attendees) and self.wrong_target_feedback:
            return Verdict(False, self.wrong_target_feedback, "wrong_target")
        if (action["start_at"], action["end_at"]) != (self.start_at, self.end_at):
            return Verdict(False, self.time_feedback, "content")
        if not (got & self.attendees):
            if not got:
                return Verdict(False, f"You need to invite {_names(self.attendees, ctx)}.", "content")
            return Verdict(False, f"That's the wrong person. Invite {_names(self.attendees, ctx)}.",
                           "wrong_target")
        extra = got - self.attendees
        if extra:
            return Verdict(False, f"Only invite {_names(self.attendees, ctx)}. Don't include "
                           f"{_names(extra, ctx)}.", "extra_recipient")
        title = action.get("title", "").lower()
        if not all(re.search(r"\b" + re.escape(w), title) for w in self.title_words):
            return Verdict(False, f'Please call it "{self.title}".', "content")
        return Verdict(True)


def to_jsonable(obj):
    """Specs -> plain JSON (sets become sorted lists, dataclasses become dicts with a type)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        d = {"type": type(obj).__name__}
        d.update({f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)})
        return d
    if isinstance(obj, (set, frozenset)):
        return sorted(to_jsonable(x) for x in obj)
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj
