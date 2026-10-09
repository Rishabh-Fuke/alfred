"""The lived-in layer of a realistic world: people the user knows but no task is about, months of background mail,
and a calendar with recurring holds, vague titles and last week's meetings.

It runs after the base world and its extras, with its own random generator, and it never touches a record a task
depends on: background people have their own names (content.NOISE_FIRST/LAST), background text can't contain
anything a task searches for, and background events stay out of every slot a task needs free.
"""
from __future__ import annotations

import bisect
import random
import re
from datetime import timedelta

from .. import content as C
from ..world import CALENDAR_STOP, EMAIL_STOP, _has, parse_dt, query_terms, slug
from .pack import fill

WORK_ROLES = ["Program Manager", "Analyst", "Operations Lead", "Office Manager", "Legal Counsel", "Sales Director",
              "IT Support", "Engineer", "Facilities", "Executive Assistant"]
PERSONAL_ROLES = ["Neighbor", "College friend", "Old roommate", "Book club", "Soccer coach", "Cousin"]
EXTERNAL_ROLES = ["Account Manager", "Consultant", "Recruiter (agency)", "Partner team", "Vendor contact"]
MAIL_MIX = [("noise_work", 0.38), ("noise_notification", 0.30), ("noise_personal", 0.17), ("noise_calendar", 0.15)]
LOCATIONS = ["", "", "Video call", "Room 5A", "Room 1B", "4th floor kitchen", "Phone"]


def _reserved(g) -> list[list[str]]:
    """Every search a task (or a sensible agent doing it) might run, as term lists. Background text that matches
    all terms of one would show up in that search, so it's left out."""
    names = (C.FIRST_NAMES + C.LAST_NAMES + C.MOM_FIRST_NAMES + C.STRANGER_FIRST + C.STRANGER_LAST + C.STORES
             + [c for c, _ in C.CARRIERS] + [u for u, _ in C.UTILITIES] + [a for a, _, _ in C.AIRLINES]
             + [c for c, _ in C.CITIES] + C.CLINICS + C.BANKS + C.RESTAURANTS + C.VENDORS
             + [p for _, p, _ in C.PERSONAL_EVENTS] + [pl for _, _, pl in C.PERSONAL_EVENTS]
             + [g.therapist["name"], g.doctor, g.hooks["reply"]["topic"]])
    out = [query_terms(n, EMAIL_STOP) for n in names] + [query_terms(n, CALENDAR_STOP) for n in names]
    return [t for t in out if t]


def _clashes(text: str, reserved: list[list[str]]) -> bool:
    """Would a search for any reserved term list find this text? (Same rule as search: each term starts a word.)"""
    hay = text.lower()
    words = sorted(set(re.findall(r"[a-z0-9]+", hay)))

    def has(term: str) -> bool:
        if not term.isalnum():
            return _has(term, hay)
        i = bisect.bisect_left(words, term)
        return i < len(words) and words[i].startswith(term)
    return any(all(has(t) for t in terms) for terms in reserved)


def _reserve_task_slots(g) -> None:
    """Slots tasks move things into, so nothing background lands there."""
    ev = g.hooks["lookup_event"]["event"]
    s, e = parse_dt(ev["start_at"]), parse_dt(ev["end_at"])
    g.busy.append((s + timedelta(days=7), e + timedelta(days=7)))  # "move it to the same time next week"
    for ev in g.hooks.get("two_meetings", {}).get("events", []):  # "move it to Friday, same time"
        s = parse_dt(ev["start_at"])
        fri = g.at(4, s.hour, s.minute)
        g.busy.append((fri, fri + timedelta(minutes=30)))


def _people(g, r: random.Random) -> list[dict]:
    n = r.randint(10, 16)
    firsts, lasts = r.sample(C.NOISE_FIRST, n), r.sample(C.NOISE_LAST, n)
    people = []
    for f, l in zip(firsts, lasts):
        kind = r.choices(["internal", "external", "personal"], [0.45, 0.25, 0.30])[0]
        if kind == "internal":
            company, domain, role, rel = g.company, g.domain, r.choice(WORK_ROLES), "work"
        elif kind == "external":
            company = r.choice(C.NOISE_COMPANIES)
            domain, role, rel = f"{slug(company)}.example", r.choice(EXTERNAL_ROLES), "work"
        else:
            company, domain, role, rel = "", r.choice(C.PERSONAL_DOMAINS), r.choice(PERSONAL_ROLES), "friend"
        people.append({"name": f"{f} {l}", "first": f, "last": l, "email": f"{f}.{l}@{domain}".lower(),
                       "company": company, "role": role, "relationship": rel, "kind": kind})
    for p in people:
        if r.random() < 0.7:  # the rest write to the user but aren't in the address book
            c = g.contact(p["name"], p["email"], p["role"] if r.random() > 0.2 else "", p["relationship"],
                          first=p["first"], last=p["last"], company=p["company"],
                          phone=f"({r.randint(201, 989)}) 555-01{r.randint(0, 99):02d}",
                          notes=r.choice(["", "", "", "Met at the conference.", "Prefers text.", "Spouse: ask about kids."]))
            if r.random() < 0.3:
                c["phone"] = ""
    stale = r.choice(people)  # someone who changed jobs: the old entry is still there
    old_co = r.choice([c for c in C.NOISE_COMPANIES if c != stale["company"]])
    c = g.contact(stale["name"], f"{stale['first']}.{stale['last']}@{slug(old_co)}.example", "", "work",
                  first=stale["first"], last=stale["last"], company=old_co, notes="Old work email.",
                  phone=f"({r.randint(201, 989)}) 555-01{r.randint(0, 99):02d}")
    return people


def _mail(g, r: random.Random, people: list[dict], reserved) -> None:
    U = g.user
    work = [p for p in people if p["kind"] != "personal"]
    personal = [p for p in people if p["kind"] == "personal"] or people
    kinds, weights = zip(*MAIL_MIX)
    for _ in range(r.randint(60, 150)):
        kind = r.choices(kinds, weights)[0]
        days = min(89, int(r.expovariate(1 / 22)))
        when = (g.now - timedelta(days=days)).replace(hour=r.randint(6, 8) if days == 0 else r.randint(7, 22),
                                                       minute=r.randint(0, 59))
        if kind == "noise_notification":
            service, addr = r.choice(C.NOISE_SERVICES)
            sender, sender_name, first, full = addr, service, "", service
        else:
            p = r.choice(personal if kind == "noise_personal" else work)
            sender, sender_name, first, full = p["email"], p["name"], p["first"], p["name"]
        others = [q["name"] for q in people if q["name"] != sender_name]
        a, b = r.sample(others, 2)
        slots = dict(user_first=U["first"], user_full=U["name"], sender_first=first, sender_full=full, person_a=a,
                     person_b=b, company=r.choice(C.NOISE_COMPANIES), project=r.choice(C.PROJECTS),
                     service=sender_name)
        for _try in range(3):
            entry = g.pack.choose(kind, r, g.split, slots)
            if entry is None:
                break
            subject, body = (fill(entry["fields"][f], slots).strip() for f in ("subject", "body"))
            if not _clashes(f"{subject}\n{body}", reserved):
                to, cc = [U["email"]], []
                if kind in ("noise_work", "noise_calendar") and r.random() < 0.35:
                    to += [q["email"] for q in r.sample(work, min(len(work), r.randint(1, 3))) if q["email"] != sender]
                if kind == "noise_work" and r.random() < 0.2:
                    cc = [g.team]
                g.email(when, sender_name, sender, subject, body, to=to, cc=cc)
                break


def _free(g, start, minutes) -> bool:
    return g.free(start, start + timedelta(minutes=minutes))


def _calendar(g, r: random.Random, people: list[dict], reserved) -> None:
    attendees = [p["email"] for p in people if p["kind"] != "personal"]

    def make(slots):
        for _try in range(3):
            entry = g.pack.choose("event", r, g.split, slots)
            if entry is None:
                return None
            title, notes = (fill(entry["fields"][f], slots).strip() for f in ("title", "notes"))
            if not _clashes(f"{title}\n{notes}", reserved):
                return title, notes
        return None

    def slots():
        return dict(person_a=r.choice(people)["name"], project=r.choice(C.PROJECTS))

    # Recurring holds over last week, this week and next.
    for _ in range(r.randint(2, 3)):
        made = make(slots())
        if not made:
            break
        h, m = r.choice([(8, 0), (8, 30), (12, 0), (13, 0), (16, 0), (17, 0)])
        mins = r.choice([30, 60])
        days = r.choice([[0, 2, 4], [1, 3], [0, 1, 2, 3, 4], [2]])
        who = r.sample(attendees, min(len(attendees), r.randint(0, 2)))
        for week in (-1, 0, 1):
            for d in days:
                start = g.at(d + 7 * week, h, m)
                if _free(g, start, mins):
                    g.event(made[0], start, mins, who, r.choice(LOCATIONS), notes=made[1])
    # One-offs, including some already past.
    for _ in range(r.randint(8, 16)):
        made = make(slots())
        if not made:
            break
        d = r.choice([x for x in range(-7, 14) if x % 7 < 5])
        start = g.at(d, r.randint(8, 17), r.choice([0, 30]))
        mins = r.choice([15, 30, 30, 45, 60])
        if _free(g, start, mins):
            who = r.sample(attendees, min(len(attendees), r.randint(0, 3)))
            g.event(made[0], start, mins, who, r.choice(LOCATIONS), notes=made[1])


def add_background(g, r: random.Random) -> None:
    reserved = _reserved(g)
    _reserve_task_slots(g)
    people = _people(g, r)
    _mail(g, r, people, reserved)
    _calendar(g, r, people, reserved)
