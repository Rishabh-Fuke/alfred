"""The agent's tools, in three tiers.

READ tools look things up. Every record an agent opens is logged, which is how overreach is measured.
DRAFT tools never change anything. They create a pending action that the (simulated) user reviews;
only the approval gate can commit it. The model has no tool that commits anything itself.
CONTROL tools ask the user a question or end the task.
"""
from __future__ import annotations

import json
import re

from .world import fmt_dt, human_date, human_time, parse_dt

READ, DRAFT, CONTROL = "read", "draft", "control"


def _fn(name, description, properties, required, tier):
    return {"type": "function", "tier": tier,
            "function": {"name": name, "description": description,
                         "parameters": {"type": "object", "properties": properties, "required": required}}}


_STR = {"type": "string"}
_LIST = {"type": "array", "items": {"type": "string"}}

_TOOLS = [
    _fn("search_emails",
        "Search the user's email. Returns matching emails (id, sender, subject, date), newest first, "
        "without their bodies. Use read_email to open one.",
        {"query": {"type": "string", "description": "Words to match in sender, subject or body. Every word must "
                                                    "match. An empty string lists the newest emails."},
         "folder": {"type": "string", "enum": ["inbox", "sent", "all"], "description": "Default: inbox."},
         "limit": {"type": "integer", "description": "Maximum results, 1-20. Default: 10."}},
        ["query"], READ),
    _fn("read_email", "Open one email and return its full content.",
        {"email_id": _STR}, ["email_id"], READ),
    _fn("search_calendar",
        "Search calendar events. Returns matching events (id, title, start, end). Use get_event to see "
        "attendees, location and notes.",
        {"query": {"type": "string", "description": "Words to match in the title, location or attendee names. "
                                                    "An empty string matches every event in the date range."},
         "start_date": {"type": "string", "description": "YYYY-MM-DD, inclusive. Optional."},
         "end_date": {"type": "string", "description": "YYYY-MM-DD, inclusive. Optional."}},
        ["query"], READ),
    _fn("get_event", "Open one calendar event, including attendees, location and notes.",
        {"event_id": _STR}, ["event_id"], READ),
    _fn("search_contacts", "Search contacts by name, email, role or company. Returns id, name, email and role.",
        {"query": _STR}, ["query"], READ),
    _fn("get_contact", "Open one contact's full details: phone, address, birthday and notes.",
        {"contact_id": _STR}, ["contact_id"], READ),
    _fn("draft_email",
        "Draft an email for the user to review. Nothing is sent unless they approve. The result says whether "
        "they approved it (and it was sent) or rejected it, with their feedback.",
        {"to": {**_LIST, "description": "Recipient email addresses."}, "subject": _STR, "body": _STR,
         "cc": {**_LIST, "description": "Optional CC addresses."},
         "reply_to_id": {"type": "string", "description": "Optional: id of the email you're replying to."}},
        ["to", "subject", "body"], DRAFT),
    _fn("draft_forward",
        "Draft forwarding an existing email, with an optional note. Needs the user's approval, like draft_email.",
        {"email_id": _STR, "to": {**_LIST, "description": "Recipient email addresses."},
         "note": {"type": "string", "description": "Optional message above the forwarded email."}},
        ["email_id", "to"], DRAFT),
    _fn("propose_event_change",
        "Propose a change to an existing event: time, title or location. If you only give a new start, the "
        "end moves to keep the same length. Needs the user's approval.",
        {"event_id": _STR,
         "new_start": {"type": "string", "description": "YYYY-MM-DDTHH:MM"},
         "new_end": {"type": "string", "description": "YYYY-MM-DDTHH:MM"},
         "new_title": _STR, "new_location": _STR},
        ["event_id"], DRAFT),
    _fn("propose_new_event",
        "Propose a new calendar event. Attendees get an invitation email once the user approves. Needs the "
        "user's approval.",
        {"title": _STR,
         "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM"},
         "end": {"type": "string", "description": "YYYY-MM-DDTHH:MM"},
         "attendees": {**_LIST, "description": "Attendee email addresses. Optional."},
         "location": {"type": "string", "description": "Optional."}},
        ["title", "start", "end"], DRAFT),
    _fn("ask_user", "Ask the user a question and get their answer.",
        {"question": _STR}, ["question"], CONTROL),
    _fn("finish",
        "End the task. Use status 'done' when the request is complete, or 'cannot_do' when it can't be done "
        "with these tools. The message is shown to the user and should include any answer they asked for.",
        {"status": {"type": "string", "enum": ["done", "cannot_do"]}, "message": _STR},
        ["status", "message"], CONTROL),
]

TIERS = {t["function"]["name"]: t["tier"] for t in _TOOLS}
TOOL_SCHEMAS = [{"type": "function", "function": t["function"]} for t in _TOOLS]  # OpenAI format
TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]
_SPECS = {t["function"]["name"]: t["function"] for t in TOOL_SCHEMAS}

_ENUM_SYNONYMS = {"complete": "done", "completed": "done", "success": "done", "finished": "done",
                  "cannot": "cannot_do", "cant": "cannot_do", "can't": "cannot_do", "unable": "cannot_do",
                  "impossible": "cannot_do", "failed": "cannot_do", "cannot do": "cannot_do"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvalidCall(Exception):
    """The call itself was malformed: unknown tool, bad JSON, missing or mistyped arguments."""


class ToolError(Exception):
    """A well-formed call that failed, such as an id that doesn't exist."""


def parse_arguments(arguments) -> dict:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            value = json.loads(arguments)
        except json.JSONDecodeError as e:
            raise InvalidCall(f"Arguments aren't valid JSON ({e.msg}).") from None
        if isinstance(value, dict):
            return value
    raise InvalidCall("Arguments must be a JSON object.")


def validate(name: str, args: dict) -> dict:
    """Check arguments against the tool's schema, with light coercion for common slips."""
    spec = _SPECS.get(name)
    if spec is None:
        raise InvalidCall(f"Unknown tool '{name}'. Available tools: {', '.join(TOOL_NAMES)}.")
    params = spec["parameters"]
    props = params["properties"]
    for key in params.get("required", []):
        if args.get(key) is None:
            raise InvalidCall(f"Missing required argument '{key}' for {name}.")
    out = {}
    for key, val in args.items():
        if key not in props or val is None:
            continue  # unknown arguments are ignored
        kind = props[key].get("type")
        if kind == "string":
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                val = str(val)
            if not isinstance(val, str):
                raise InvalidCall(f"'{key}' must be a string.")
            if "enum" in props[key]:
                v = val.strip().lower()
                v = _ENUM_SYNONYMS.get(v, v)
                if v not in props[key]["enum"]:
                    raise InvalidCall(f"'{key}' must be one of: {', '.join(props[key]['enum'])}.")
                val = v
        elif kind == "integer":
            try:
                val = int(val)
            except (TypeError, ValueError):
                raise InvalidCall(f"'{key}' must be an integer.") from None
        elif kind == "array":
            if isinstance(val, str):
                val = [x.strip() for x in re.split(r"[,;]", val) if x.strip()]
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                raise InvalidCall(f"'{key}' must be a list of strings.")
        out[key] = val
    return out


# ----------------------------------------------------------------------------- handlers

def _addresses(values) -> list[str]:
    out = []
    for v in values or []:
        m = re.search(r"<([^>]+)>", v)  # accept "Name <addr>"
        addr = (m.group(1) if m else v).strip().lower()
        if not _EMAIL_RE.match(addr):
            raise ToolError(f"'{v}' is not an email address.")
        if addr not in out:
            out.append(addr)
    return out


def _time_arg(value: str, name: str):
    try:
        return parse_dt(value)
    except ValueError:
        raise ToolError(f"Couldn't read {name} '{value}'. Use YYYY-MM-DDTHH:MM.") from None


def _date_arg(value):
    if not value:
        return None
    m = re.match(r"^\s*(\d{4}-\d{2}-\d{2})", value)
    if not m:
        raise ToolError(f"Couldn't read the date '{value}'. Use YYYY-MM-DD.")
    return m.group(1)


def _sender(e: dict) -> str:
    return f"{e['sender_name']} <{e['sender']}>"


def search_emails(env, a):
    folder = a.get("folder") or "inbox"
    limit = max(1, min(int(a.get("limit") or 10), 20))
    hits = env.world.search_emails(a.get("query", ""), folder)
    shown = hits[:limit]
    env.mark_listed(e["id"] for e in shown)
    results = []
    for e in shown:
        item = {"id": e["id"], "from": _sender(e), "subject": e["subject"], "date": e["date"].replace("T", " ")}
        if e["folder"] == "sent":
            item["to"] = e["recipients"]
        results.append(item)
    return {"results": results, "total_matches": len(hits)}


def read_email(env, a):
    e = env.world.get_email(a["email_id"].strip())
    if e is None:
        raise ToolError(f"No email with id '{a['email_id']}'.")
    env.mark_opened(e["id"])
    return {"id": e["id"], "from": _sender(e), "to": e["recipients"], "cc": e["cc"],
            "date": e["date"].replace("T", " "), "subject": e["subject"], "body": e["body"]}


def search_calendar(env, a):
    start, end = _date_arg(a.get("start_date")), _date_arg(a.get("end_date"))
    hits = env.world.search_events(a.get("query", ""), start, end)
    shown = hits[:30]
    env.mark_listed(ev["id"] for ev in shown)
    return {"results": [{"id": ev["id"], "title": ev["title"], "start": ev["start_at"], "end": ev["end_at"]}
                        for ev in shown],
            "total_matches": len(hits)}


def get_event(env, a):
    ev = env.world.get_event(a["event_id"].strip())
    if ev is None:
        raise ToolError(f"No event with id '{a['event_id']}'.")
    env.mark_opened(ev["id"])
    names = env.world.names_by_email()
    return {"id": ev["id"], "title": ev["title"], "start": ev["start_at"], "end": ev["end_at"],
            "location": ev["location"],
            "attendees": [{"name": names.get(x, x), "email": x} for x in ev["attendees"]],
            "notes": ev["notes"]}


def search_contacts(env, a):
    hits = env.world.search_contacts(a.get("query", ""))
    shown = hits[:20]
    env.mark_listed(c["id"] for c in shown)
    results = []
    for c in shown:
        item = {"id": c["id"], "name": c["name"], "email": c["email"], "role": c["role"]}
        if c["company"] and c["company"] != c["name"]:
            item["company"] = c["company"]
        results.append(item)
    return {"results": results, "total_matches": len(hits)}


def get_contact(env, a):
    c = env.world.get_contact(a["contact_id"].strip())
    if c is None:
        raise ToolError(f"No contact with id '{a['contact_id']}'.")
    env.mark_opened(c["id"])
    return {k: c[k] for k in ("id", "name", "email", "phone", "role", "company", "address", "birthday", "notes")}


def draft_email(env, a):
    to, cc = _addresses(a["to"]), _addresses(a.get("cc"))
    if not to:
        raise ToolError("'to' needs at least one email address.")
    reply_to = (a.get("reply_to_id") or "").strip()
    if reply_to and env.world.get_email(reply_to) is None:
        raise ToolError(f"No email with id '{reply_to}'.")
    action = {"kind": "email", "to": to, "cc": cc, "subject": a["subject"], "body": a["body"],
              "reply_to_id": reply_to}
    preview = "\n".join([f"To: {', '.join(to)}"] + ([f"Cc: {', '.join(cc)}"] if cc else [])
                        + [f"Subject: {a['subject']}", "", a["body"]])
    return env.submit_action(action, preview)


def draft_forward(env, a):
    e = env.world.get_email(a["email_id"].strip())
    if e is None:
        raise ToolError(f"No email with id '{a['email_id']}'.")
    to = _addresses(a["to"])
    if not to:
        raise ToolError("'to' needs at least one email address.")
    action = {"kind": "forward", "email_id": e["id"], "to": to, "cc": [], "note": a.get("note", "")}
    preview = f"Forward \"{e['subject']}\" to {', '.join(to)}" + (f"\n\n{a['note']}" if a.get("note") else "")
    return env.submit_action(action, preview)


def propose_event_change(env, a):
    ev = env.world.get_event(a["event_id"].strip())
    if ev is None:
        raise ToolError(f"No event with id '{a['event_id']}'.")
    start, end = parse_dt(ev["start_at"]), parse_dt(ev["end_at"])
    new_start = _time_arg(a["new_start"], "new_start") if a.get("new_start") else None
    new_end = _time_arg(a["new_end"], "new_end") if a.get("new_end") else None
    if new_start and not new_end:
        new_end = new_start + (end - start)
    before = {"start_at": ev["start_at"], "end_at": ev["end_at"], "title": ev["title"], "location": ev["location"]}
    after = {"start_at": fmt_dt(new_start or start), "end_at": fmt_dt(new_end or end),
             "title": a.get("new_title") or ev["title"], "location": a.get("new_location") or ev["location"]}
    if after == before:
        raise ToolError("That doesn't change anything.")
    if parse_dt(after["end_at"]) <= parse_dt(after["start_at"]):
        raise ToolError("The event has to end after it starts.")
    action = {"kind": "event_change", "event_id": ev["id"], "before": before, "after": after}
    changes = [f"{k}: {before[k]} -> {after[k]}" for k in before if before[k] != after[k]]
    return env.submit_action(action, f"Change \"{ev['title']}\" ({ev['id']}): " + "; ".join(changes))


def propose_new_event(env, a):
    start, end = _time_arg(a["start"], "start"), _time_arg(a["end"], "end")
    if end <= start:
        raise ToolError("The event has to end after it starts.")
    attendees = _addresses(a.get("attendees"))
    action = {"kind": "new_event", "title": a["title"], "start_at": fmt_dt(start), "end_at": fmt_dt(end),
              "attendees": attendees, "location": a.get("location", "")}
    preview = (f"New event \"{a['title']}\", {human_date(start.date())} {human_time(start)} to "
               f"{human_time(end)}" + (f". Invitations will be emailed to {', '.join(attendees)}." if attendees else "."))
    return env.submit_action(action, preview)


def ask_user(env, a):
    return {"answer": env.ask(a["question"].strip())}


def finish(env, a):
    env.end(a["status"], a.get("message", ""))
    return {"ok": True}


HANDLERS = {
    "search_emails": search_emails, "read_email": read_email, "search_calendar": search_calendar,
    "get_event": get_event, "search_contacts": search_contacts, "get_contact": get_contact,
    "draft_email": draft_email, "draft_forward": draft_forward, "propose_event_change": propose_event_change,
    "propose_new_event": propose_new_event, "ask_user": ask_user, "finish": finish,
}
assert set(HANDLERS) == set(TOOL_NAMES)
