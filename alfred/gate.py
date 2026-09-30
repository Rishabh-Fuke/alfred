"""The approval gate: the only code path that changes the world.

Draft tools submit a pending action. The action is frozen (deep-copied) at submission, so
what gets committed is exactly what the user reviewed. Approving twice is a no-op. The model
never calls approve(); only the user (real or simulated) does.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass


@dataclass
class PendingAction:
    id: str
    kind: str
    action: dict
    preview: str
    call_index: int
    status: str = "pending"            # pending | approved | rejected
    classification: str | None = None  # approved | content | wrong_target | unrequested | leak | ...
    feedback: str | None = None
    result: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


class Gate:
    def __init__(self, world):
        self.world = world
        self.actions: list[PendingAction] = []

    def submit(self, action: dict, preview: str, call_index: int) -> PendingAction:
        pa = PendingAction(id=f"act_{len(self.actions) + 1}", kind=action["kind"], action=copy.deepcopy(action),
                           preview=preview, call_index=call_index)
        self.actions.append(pa)
        return pa

    def approve(self, pa: PendingAction) -> str:
        if pa.status == "approved":
            return pa.result  # idempotent: never commit twice
        if pa.status != "pending":
            raise ValueError(f"{pa.id} is {pa.status}; only pending actions can be approved")
        pa.result = self._commit(pa.action)
        pa.status, pa.classification = "approved", "approved"
        return pa.result

    def reject(self, pa: PendingAction, feedback: str, classification: str) -> None:
        if pa.status != "pending":
            raise ValueError(f"{pa.id} is {pa.status}; only pending actions can be rejected")
        pa.status, pa.feedback, pa.classification = "rejected", feedback, classification

    def _commit(self, a: dict) -> str:
        w = self.world
        kind = a["kind"]
        if kind == "email":
            w.add_email(to=a["to"], cc=a["cc"], subject=a["subject"], body=a["body"])
            return f"Sent to {', '.join(a['to'] + a['cc'])}."
        if kind == "forward":
            orig = w.get_email(a["email_id"])
            quoted = (f"---------- Forwarded message ----------\nFrom: {orig['sender_name']} <{orig['sender']}>\n"
                      f"Date: {orig['date'].replace('T', ' ')}\nSubject: {orig['subject']}\n\n{orig['body']}")
            note = (a.get("note") or "").strip()
            w.add_email(to=a["to"], cc=a["cc"], subject=f"Fwd: {orig['subject']}",
                        body=f"{note}\n\n{quoted}" if note else quoted)
            return f"Forwarded to {', '.join(a['to'])}."
        if kind == "event_change":
            after = a["after"]
            w.update_event(a["event_id"], **after)
            return (f"Updated \"{after['title']}\": now {after['start_at'].replace('T', ' ')} to "
                    f"{after['end_at'][11:]}.")
        if kind == "new_event":
            eid = w.add_event(title=a["title"], start_at=a["start_at"], end_at=a["end_at"],
                              attendees=a["attendees"], location=a["location"])
            for addr in a["attendees"]:  # side effect, covered by the same approval
                w.add_email(to=[addr], cc=[], subject=f"Invitation: {a['title']}",
                            body=f"{w.user['name']} invited you to \"{a['title']}\" on "
                                 f"{a['start_at'].replace('T', ' ')}.")
            invited = f" Invitations sent to {', '.join(a['attendees'])}." if a["attendees"] else ""
            return f"Added \"{a['title']}\" ({eid}).{invited}"
        raise ValueError(f"unknown action kind: {kind}")
