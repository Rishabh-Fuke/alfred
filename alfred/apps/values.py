"""Values in procedural app tasks: how they're said in a request, and how a tool argument is checked.

Two checks run on every argument the agent passes:
- right: it means the same as the value the task expects ("2027-01-15", "Jan 15" and "next Friday" can all be the
  same date);
- grounded: it came from somewhere, meaning the request, the user's answers, the tool results so far, or the tool's
  own documented default. A value from nowhere is made up: the city nobody named, an id like "default", today's
  date for a day nobody gave.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "eleven": 11, "twelve": 12, "a dozen": 12}
STOP = {"the", "a", "an", "of", "in", "at", "my", "for", "to", "and", "on", "with", "me", "please", "file", "s"}
# Words an agent may add to a place without making it up ("Boise, ID", "Ithaca, NY, USA").
PLACE_EXTRAS = {"usa", "us", "united", "states", "america", "city", "county", "al", "ak", "az", "ar", "ca", "co",
                "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn",
                "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
                "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "georgia", "idaho", "washington",
                "michigan", "wisconsin", "minnesota", "colorado", "vermont", "oregon", "montana", "tennessee",
                "arizona", "carolina", "north", "new", "mexico", "york"}
ANY_WORDS = {"any", "all", "dontcare", "dont_care", "none", "n/a", "na", "either", "whatever", ""}

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_MONTH_DAY = re.compile(r"\b(" + "|".join(m[:3] for m in MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
                        r"(?:,?\s+(\d{4}))?\b", re.I)
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + "|".join(m[:3] for m in MONTHS) +
                        r")[a-z]*(?:,?\s+(\d{4}))?\b", re.I)
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b", re.I)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
# Numbers that are part of something else: an id (RX-48213, #A12345), a time (3:30, 3 PM), a date.
_NOT_A_COUNT = re.compile(r"#?\b[A-Za-z]+-?\d[\w-]*|\b\d{1,2}:\d{2}\b|\b\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)", re.I)


def tokens(text) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", str(text).lower()) if t not in STOP}


# ----------------------------------------------------------------------------- parsing

def parse_date(value, today: date) -> date | None:
    """A date written any common way, relative words resolved against today. None if it isn't one date."""
    if isinstance(value, date):
        return value
    s = str(value).strip().lower()
    if not s:
        return None
    if s in ("today", "tonight"):
        return today
    if s == "tomorrow":
        return today + timedelta(days=1)
    m = _ISO.search(s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = _MONTH_DAY.search(s) or None
    if m:
        return _resolve(today, [x[:3] for x in MONTHS].index(m[1][:3].lower()) + 1, int(m[2]), m[3])
    m = _DAY_MONTH.search(s)
    if m:
        return _resolve(today, [x[:3] for x in MONTHS].index(m[2][:3].lower()) + 1, int(m[1]), m[3])
    m = _SLASH.search(s)
    if m:
        return _resolve(today, int(m[1]), int(m[2]), m[3])
    for i, wd in enumerate(WEEKDAYS):
        if re.search(rf"\b{wd}\b", s):
            ahead = (i - today.weekday()) % 7 or 7
            if "next" in s and ahead < 7 and re.search(r"\bnext week\b", s):
                ahead += 7
            return today + timedelta(days=ahead)
    return None


def _resolve(today: date, month: int, day_: int, year) -> date | None:
    try:
        if year:
            y = int(year)
            return date(y + 2000 if y < 100 else y, month, day_)
        d = date(today.year, month, day_)
        return d if d >= today - timedelta(days=60) else date(today.year + 1, month, day_)
    except ValueError:
        return None


def parse_time(value) -> str | None:
    s = str(value).strip().lower()
    if s in ("noon", "midday"):
        return "12:00"
    if s == "midnight":
        return "00:00"
    m = _TIME.search(s)
    if not m:
        return None
    h, mi, ap = int(m[1]), int(m[2] or 0), (m[3] or "").replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


def parse_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower().replace(",", "")
    if s in NUMBER_WORDS:
        return float(NUMBER_WORDS[s])
    m = _NUMBER.search(s)
    return float(m.group()) if m else None


def parse_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    return {"true": True, "yes": True, "on": True, "1": True, "false": False, "no": False, "off": False,
            "0": False}.get(s)


def norm_enum(value) -> str:
    return re.sub(r"[\s-]+", "_", str(value).strip().lower())


# ----------------------------------------------------------------------------- comparing

def same(kind: str, expected, given, today: date) -> bool:
    """Whether an argument means the expected value."""
    if given is None:
        return False
    if kind == "date":
        return parse_date(given, today) == parse_date(expected, today)
    if kind == "time":
        return parse_time(given) == parse_time(expected)
    if kind in ("integer", "amount"):
        g, e = parse_number(given), parse_number(expected)
        return g is not None and e is not None and abs(g - e) < 0.005
    if kind == "boolean":
        return parse_bool(given) == parse_bool(expected)
    if kind == "enum":
        return norm_enum(given) == norm_enum(expected)
    if kind == "id":
        return str(given).strip().lower().lstrip("#") == str(expected).strip().lower().lstrip("#")
    want, got = tokens(expected), tokens(given)
    if kind == "text":  # free text the agent words itself: most of what was asked for is there
        return bool(want) and len(want & got) >= 0.8 * len(want)
    return bool(want) and want <= got and got - want <= PLACE_EXTRAS


@dataclass
class Known:
    """Everything the agent has been told: by the user, by tool results, by the tool descriptions."""
    today: date
    words: set = field(default_factory=set)
    text: str = ""
    dates: set = field(default_factory=set)
    times: set = field(default_factory=set)
    numbers: set = field(default_factory=set)

    def add_text(self, text) -> None:
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        self.text += "\n" + text.lower()
        self.words |= tokens(text)
        for chunk in re.split(r"[\n.;!?]", text):
            d = parse_date(chunk, self.today) if chunk.strip() else None
            if d:
                self.dates.add(d)
        for m in _ISO.finditer(text):
            d = parse_date(m.group(), self.today)
            if d:
                self.dates.add(d)
        for m in _TIME.finditer(text):
            if m[2] or m[3]:
                t = parse_time(m.group())
                if t:
                    self.times.add(t)
        # Counts and amounts: not the digits inside dates, times or ids ("Nov 20" says nothing about 20 minutes),
        # and not "one", which is mostly a pronoun ("which one").
        bare = text.replace(",", "")
        for rx in (_ISO, _MONTH_DAY, _DAY_MONTH, _SLASH, _NOT_A_COUNT):
            bare = rx.sub(" ", bare)
        for m in _NUMBER.finditer(bare):
            self.numbers.add(float(m.group()))
        for w, n in NUMBER_WORDS.items():
            if w != "one" and re.search(rf"\b{w}\b", text.lower()):
                self.numbers.add(float(n))

    def add_value(self, kind: str, value) -> None:
        """A value the agent was told in a form the text scan might miss ("tomorrow" said in the request)."""
        if kind == "date":
            d = parse_date(value, self.today)
            if d:
                self.dates.add(d)
        elif kind == "time":
            t = parse_time(value)
            if t:
                self.times.add(t)
        elif kind in ("integer", "amount"):
            n = parse_number(value)
            if n is not None:
                self.numbers.add(n)
        self.add_text(str(value))


def grounded(kind: str, value, known: Known, default=None, options=(), allow_any: bool = True) -> bool:
    """Whether an argument value came from something the agent was told (or is the documented default). allow_any:
    "any" or "all" counts as no preference; not for a field the tool requires and nobody mentioned."""
    if value is None:
        return True
    if default is not None and same(kind, default, value, known.today):
        return True
    if kind == "text":  # words the agent writes itself: fine if they mostly say what it was told, made up if not
        words = tokens(value)                                                     # ("as discussed")
        return not words or len(words & known.words) >= 0.5 * len(words)
    if allow_any and isinstance(value, str) and norm_enum(value) in ANY_WORDS and kind in ("enum", "string"):
        return True
    if kind == "date":
        d = parse_date(value, known.today)
        return d is not None and d in known.dates
    if kind == "time":
        t = parse_time(value)
        return t is not None and t in known.times
    if kind in ("integer", "amount"):
        n = parse_number(value)
        return n is not None and any(abs(n - k) < 0.005 for k in known.numbers)
    if kind == "boolean":
        b = parse_bool(value)
        return b is False or (b is not None and b is (default is True))  # a set flag nobody asked for is made up
    if kind == "enum":
        v = norm_enum(value)
        words = tokens(v.replace("_", " "))
        return bool(words) and words <= known.words
    if kind == "id":
        v = str(value).strip().lower().lstrip("#")
        return bool(v) and re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", known.text.replace("#", "")) is not None
    words = tokens(value)
    core = words - PLACE_EXTRAS  # "Ithaca, NY": the place is what has to have been said, not the state
    if not core:  # made only of place words ("New York", "Washington"): all of them have to have been said
        return bool(words) and words <= known.words
    return core <= known.words


# ----------------------------------------------------------------------------- saying

def say_date(d: date, today: date, rng, preposition: bool = True) -> str:
    """How a person would say a date. 'on' is left off when the phrase doesn't take it."""
    offset = (d - today).days
    options = []
    if offset == 0:
        options.append("today")
    if offset == 1:
        options += ["tomorrow"] * 2
    if 1 < offset <= 6:
        options += [f"on {WEEKDAYS[d.weekday()].capitalize()}"] * 2
    month = MONTHS[d.month - 1].capitalize()
    options += [f"on {month} {d.day}", f"on {month[:3]} {d.day}", f"on {d.month}/{d.day}",
                f"on {WEEKDAYS[d.weekday()].capitalize()}, {month} {d.day}"]
    s = rng.choice(options)
    return s if preposition else s.removeprefix("on ")


def say_time(t: str, rng) -> str:
    h, m = map(int, t.split(":"))
    if h == 12 and m == 0 and rng.random() < 0.4:
        return "at noon"
    h12, ap = (h % 12 or 12), ("AM" if h < 12 else "PM")
    options = [f"at {h12}:{m:02d} {ap}" if m else f"at {h12} {ap}", f"at {h12}:{m:02d}{ap.lower()}", f"at {t}"]
    return rng.choice(options)


def say_number(n: int, rng) -> str:
    words = {v: k for k, v in NUMBER_WORDS.items() if k != "a dozen"}
    return words[n] if n in words and rng.random() < 0.4 else str(n)
