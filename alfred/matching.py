"""Deterministic text checks used to grade answers and drafts."""
from __future__ import annotations

import re
from datetime import date

from .world import MONTHS, WEEKDAYS


def date_patterns(d: date) -> list[str]:
    """Regexes that recognize a date written the usual ways: 'Friday', 'Fri', 'Oct 9', '10/9', ..."""
    wd, mon, day = WEEKDAYS[d.weekday()].lower(), MONTHS[d.month - 1].lower(), d.day
    suf = r"(?:st|nd|rd|th)?"
    return [
        rf"\b{wd}\b", rf"\b{wd[:3]}\b",
        rf"\b{mon}\.?\s+{day}{suf}\b", rf"\b{mon[:3]}\.?\s+{day}{suf}\b",
        rf"\b{day}{suf}\s+(?:of\s+)?{mon}\b", rf"\b{day}{suf}\s+(?:of\s+)?{mon[:3]}\b",
        rf"\b{d.month}/{day}\b", rf"\b{d.isoformat()}\b",
    ]


def any_match(patterns, text: str) -> bool:
    return any(re.search(p, text or "", re.I) for p in patterns)


_TIME_12H = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?", re.I)
_TIME_24H = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def extract_times(text: str) -> set[int]:
    """All times of day mentioned in `text`, as minutes after midnight.

    '3:30 PM' -> {930}. '15:30' -> {930}. A bare '3:30' could mean either, so it yields {210, 930}.
    """
    text = re.sub(r"(\d{4}-\d{2}-\d{2})T(\d{1,2}:\d{2})", r"\1 \2", text or "")
    found: set[int] = set()
    spans = []
    for m in _TIME_12H.finditer(text):
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if hour > 12 or minute > 59:
            continue
        hour %= 12
        if m.group(3).lower() == "p":
            hour += 12
        found.add(hour * 60 + minute)
        spans.append(m.span())
    for m in _TIME_24H.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour < 24 and minute < 60:
            found.add(hour * 60 + minute)
            if 1 <= hour <= 11:
                found.add((hour + 12) * 60 + minute)
    if re.search(r"\bnoon\b", text, re.I):
        found.add(12 * 60)
    return found


# Used only when an agent ends with plain text instead of calling finish.
INABILITY = re.compile(
    r"\b(can['’]?t|cannot|can not|unable|not able|no way to|don['’]?t have (?:a |the |any )?"
    r"(?:way|ability|tool|access)|not something i can|isn['’]?t something i can)\b", re.I)
