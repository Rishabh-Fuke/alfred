"""Content packs: load them, pick a template, fill its slots, and check a template against its kind's rules.

A pack is a .jsonl (or .jsonl.gz) file, one template per line:

    {"id": "receipt-0007", "kind": "receipt", "split": "train", "fields": {"subject": "...", "body": "..."},
     "source": "nemotron-3-super"}

`split` is "train" (used by worlds below 950), "eval" (worlds 950 and up, so validation and evaluation see
wording training never did) or "both" (the small hand-written seed pack, so tests work on either side). An
optional first line {"_meta": {...}} describes how the pack was built.

`problems()` is the one gate every template passes: the builder rejects what it flags and asks for a rewrite,
and `load()` drops anything that slipped through (say, a hand-edited pack), so a world never uses a template
that could break a task.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path

from .. import content as C
from ..matching import REASONING_LEAK, mentioned_times
from .kinds import BRACKETS_OK, KINDS, MONTHS_OK, SLOTS, TIMES_OK, WEEKDAYS_OK

PACK_DIR = Path(__file__).parent / "packs"
EVAL_SPLIT_START = 950  # validation worlds use eval wording too
SLOT_RE = re.compile(r"\{([a-z_0-9]+)\}")


def split_for_seed(seed: int) -> str:
    return "train" if seed < EVAL_SPLIT_START else "eval"


def split_for_id(entry_id: str) -> str:
    """About one template in five goes to eval, decided by the id alone so it never changes."""
    return "eval" if int(hashlib.sha1(entry_id.encode()).hexdigest(), 16) % 5 == 0 else "train"


def fill(text: str, slots: dict) -> str:
    """Put slot values in. Only {known_slot} patterns are touched, so stray braces can't raise."""
    return SLOT_RE.sub(lambda m: str(slots[m.group(1)]) if m.group(1) in slots else m.group(0), text)


def used_slots(fields: dict) -> set[str]:
    return {s for v in fields.values() for s in SLOT_RE.findall(v or "")}


# ----------------------------------------------------------------------------- validation

WEEKDAY_WORDS = re.compile(r"\b(?:Mon|Tues?|Wed|Thu|Thurs?|Fri|Sat|Sun)(?:day|nesday|rsday|urday)?s?\b\.?")
MONTH_WORDS = re.compile(r"\b(?:January|February|March|April|June|July|August|September|October|November|December|"
                         r"(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.?\s+\d|May\s+\d)")
NUMERIC_DATE = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
YEAR = re.compile(r"\b(?:19|20)\d\d\b")
EMAIL_ADDR = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(r"(?:https?://|www\.)([\w.-]+)", re.I)
PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]?\d{3}[-. ]\d{4}\b|\b\d{3}[-. ]\d{4}\b")
FICTIONAL_PHONE = re.compile(r"555[-. ]01\d\d")
CODE_LIKE = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9][A-Z0-9-]{4,12}[A-Z0-9]\b|\b\d{6,}\b")
PLACEHOLDER = re.compile(r"\[[^\]\n]{1,40}\]|<[A-Za-z][^>\n]{0,40}>|\bX{3,}\b|\blorem ipsum\b|\{|\}", re.I)
CHATTER = re.compile(r"\A\s*(?:here(?:'s| is) (?:a|an|the|your) (?:new |rewritten |revised )?"
                     r"(?:email|message|version|draft|template|request)\b|sure[,!.]|certainly[,!.])|"
                     r"\bas an ai\b|\bre-?written\b|\bplaceholders?\b", re.I)
# Slots whose names aren't labels a real message would use ("User First: Nora", "Weekday: Friday").
LEAKY_SLOTS = {"user_first", "user_full", "sender_first", "sender_full", "recipient_first", "friend_first",
               "sibling_first", "stranger_first", "person_a", "person_b", "delivery_weekday", "dx_text", "hcode",
               "ccode", "last4"}
SLOT_LABEL = re.compile(r"^[ \t]*[*\-\u2022]?[ \t]*([A-Za-z][A-Za-z _]{0,30}):[ \t]*\{([a-z_0-9]+)\}", re.M)
CLOCK_WORDS = re.compile(r"\b(?:noon|midnight|o'clock)\b", re.I)

BRANDS = ["Google", "Gmail", "Microsoft", "Outlook", "Teams", "Amazon", "Apple", "iPhone", "Zoom", "Slack", "FedEx",
          "UPS", "USPS", "DHL", "Visa", "Mastercard", "PayPal", "Venmo", "Facebook", "Instagram", "LinkedIn",
          "Twitter", "Uber", "Lyft", "Netflix", "Spotify", "Starbucks", "Walmart", "Costco", "Delta", "United",
          "American Airlines", "Southwest", "Expedia", "Airbnb", "Yahoo", "AOL", "Hotmail", "Dropbox", "Salesforce",
          "Excel", "PowerPoint", "Lotus", "Blackberry", "AT&T", "Verizon", "Comcast", "Chase", "Citibank",
          "Wells Fargo", "Bank of America", "WebMD", "Kaiser"]
# The corpus the style references come from, and its best-known names and places. Names from the actual
# references are added at build time.
ENRON_TERMS = ["Enron", "ENA", "EES", "EBS", "EOTT", "Houston", "Skilling", "Fastow", "Lay", "Kaminski", "Lavorato",
               "Dasovich", "Shackleton", "Kitchen", "Whalley", "Delainey", "Kean", "Steffes", "Mann", "Dynegy",
               "FERC", "PG&E", "CPUC", "Calpine", "Andersen", "Portland General"]


def _world_names() -> set[str]:
    names = set(C.FIRST_NAMES + C.LAST_NAMES + C.MOM_FIRST_NAMES + C.STRANGER_FIRST + C.STRANGER_LAST)
    names |= set(getattr(C, "NOISE_FIRST", [])) | set(getattr(C, "NOISE_LAST", []))
    names |= {n for pair in getattr(C, "UNKNOWN_PEOPLE", []) for n in pair}
    names |= {c for c, _ in C.COMPANIES} | set(C.PROJECTS) | set(C.STORES) | {c for c, _ in C.CARRIERS}
    names |= {u for u, _ in C.UTILITIES} | {a for a, _, _ in C.AIRLINES} | {c for c, _ in C.CITIES}
    names |= set(C.CLINICS + C.BANKS + C.RESTAURANTS + C.VENDORS) | {n for n, _, _ in C.NEWSLETTERS}
    names |= {place for _, _, place in C.PERSONAL_EVENTS} | {"Ironwood Fitness", "Mindful Counseling"}
    names |= set(getattr(C, "NOISE_COMPANIES", [])) | {n for n, _ in getattr(C, "NOISE_SERVICES", [])}
    return names


@lru_cache(maxsize=1)
def _banned_static() -> re.Pattern:
    terms = sorted(_world_names() | set(BRANDS) | set(ENRON_TERMS), key=len, reverse=True)
    return re.compile(r"(?<![\w&])(?:" + "|".join(re.escape(t) for t in terms) + r")(?![\w&])")


def _shingles(text: str, n: int = 10) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _greets(line: str, slots: str) -> bool:
    """Is this line a greeting (or chat-style 'Name:' prefix) addressed to one of these slots?"""
    return bool(re.match(rf"^\s*(?:(?:hi|hey|hello|dear|morning|yo|ok|so)\s+)?\{{(?:{slots})\}}\s*(?:[,:!.\-—–]|$)",
                         line, re.I))


def _signs(line: str, slots: str) -> bool:
    return bool(re.match(rf"^\s*[-—–~]*\s*\{{(?:{slots})\}}\s*[.!]?\s*$", line))


def _role_problems(kind, body: str) -> list[str]:
    """Mail is from the sender to the user (sent_reply: from the user). Greeting the sender, or signing as the
    user, swaps who wrote it, and keeping track of who said what is part of the job."""
    lines = [ln for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith(">")]
    if not lines:
        return []
    first, last = lines[0], lines[-1]
    if kind.sender == "the user":
        me, them = "user_first|user_full", "recipient_first"
    else:
        me, them = "sender_first|sender_full", "user_first|user_full"
    out = []
    if _greets(first, me):
        out.append(f"it's written by {kind.sender or 'the sender'} to {'a colleague' if kind.sender == 'the user' else 'the user'}"
                   f": greet {{{them.split('|')[0]}}}, not {{{me.split('|')[0]}}}")
    if _signs(last, them):
        out.append(f"sign it as {{{me.split('|')[0]}}}, the writer, not {{{them.split('|')[0]}}}")
    return out


def problems(kind_name: str, fields: dict, refs: list[str] = (), names: set[str] = frozenset()) -> list[str]:
    """Everything wrong with a template, in words a writer can act on. Empty means it's usable.

    refs: reference texts it was written from (no 10-word span may be copied). names: extra names to ban (people
    named in those references)."""
    kind = KINDS.get(kind_name)
    if kind is None:
        return [f"unknown kind {kind_name!r}"]
    out: list[str] = []
    if not isinstance(fields, dict) or set(fields) != set(kind.fields):
        return [f"it must have exactly these fields: {', '.join(kind.fields)}"]
    if not all(isinstance(v, str) for v in fields.values()):
        return ["every field must be a string"]
    used = used_slots(fields)
    unknown = used - kind.slots
    if unknown:
        allowed = ", ".join("{" + s + "}" for s in sorted(kind.slots)) or "none"
        out.append(f"it uses slots this kind doesn't have: {', '.join('{' + s + '}' for s in sorted(unknown))} "
                   f"(allowed: {allowed})")
    missing = [s for s in kind.required if s not in used]
    if missing:
        out.append(f"it must use {', '.join('{' + s + '}' for s in missing)}")
    for group in kind.any_of:
        if not set(group) & used:
            out.append(f"it must use at least one of {', '.join('{' + s + '}' for s in group)}")

    examples = {s: SLOTS[s][1] for s in SLOTS}
    for f, (lo, hi) in kind.lengths.items():
        n = len(fill(fields.get(f, ""), examples).strip())
        if not lo <= n <= hi:
            out.append(f"{f} is {n} characters; it should be {lo} to {hi}")
    for f in ("subject", "title"):
        if "\n" in fields.get(f, ""):
            out.append(f"the {f} must be one line")

    raw = "\n".join(fields[f] for f in kind.fields)
    for pattern, why in kind.need:
        # Slots the pattern doesn't name become a neutral mark, so "{depart_date}" can't pass for the word "depart".
        text = SLOT_RE.sub(lambda m: m.group(0) if f"\\{{{m.group(1)}\\}}" in pattern else "\u25ca", raw)
        if not re.search(pattern, text, re.I):
            out.append(why)
    for pattern, why in kind.forbid:
        if re.search(pattern, SLOT_RE.sub(" ", raw), re.I):
            out.append(why)

    if kind.fields == ("subject", "body"):
        out += _role_problems(kind, fields["body"])
    for m in SLOT_LABEL.finditer(raw):
        label, slot = re.sub(r"[\s_]+", "", m.group(1).lower()), m.group(2)
        if slot in LEAKY_SLOTS and label and label in slot.replace("_", ""):
            out.append(f"don't label a slot with its own name ('{m.group(0).strip()}'); write it the way a real "
                       f"message would")
            break

    bare = SLOT_RE.sub(" ", raw)  # only the template's own words
    allow = kind.allow
    if EMAIL_ADDR.search(bare):
        out.append("no email addresses except through slots")
    if any(not u.lower().rstrip(".").endswith(".example") for u in URL.findall(bare)):
        out.append("links must use a made-up domain ending in .example")
    if any(not FICTIONAL_PHONE.search(m.group(0)) for m in PHONE.finditer(bare)):
        out.append("no phone numbers")
    if YEAR.search(bare):
        out.append("no years")
    if WEEKDAYS_OK not in allow and WEEKDAY_WORDS.search(bare):
        out.append("no weekday names except through slots")
    if MONTHS_OK not in allow and (MONTH_WORDS.search(bare) or NUMERIC_DATE.search(bare)):
        out.append("no month names or dates except through slots")
    if TIMES_OK not in allow and (mentioned_times(bare) or CLOCK_WORDS.search(bare)):
        out.append("no clock times except through slots")
    if CODE_LIKE.search(bare):
        out.append("no codes, order numbers or IDs except through slots (they look like the facts tasks ask for)")
    if BRACKETS_OK not in allow and PLACEHOLDER.search(bare):
        out.append("no placeholders, brackets or braces")
    if REASONING_LEAK.search(raw) or (BRACKETS_OK not in allow and any(CHATTER.search(v) for v in fields.values())):
        out.append("write only the message itself, with no comments about writing it")
    hit = _banned_static().search(bare)
    if hit:
        out.append(f"don't use real or reserved names like {hit.group(0)!r}; use the slots or invent nothing")
    if names:
        found = [n for n in names if len(n) > 2 and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", bare)]
        if found:
            out.append(f"don't use names from the reference emails ({', '.join(sorted(found)[:3])})")
    if refs:
        mine = _shingles(raw)
        if any(mine & _shingles(r) for r in refs):
            out.append("it copies a passage from a reference email; write new sentences")
    return list(dict.fromkeys(out))


# ----------------------------------------------------------------------------- loading

class Pack:
    def __init__(self, entries: list[dict], name: str, meta: dict | None = None, dropped: int = 0):
        self.name, self.meta, self.dropped = name, meta or {}, dropped
        digest = hashlib.sha1(json.dumps(entries, sort_keys=True).encode()).hexdigest()[:10]
        self.id = f"{name}@{digest}"
        self.entries = sorted(entries, key=lambda e: e["id"])
        self._by: dict[tuple[str, str], list[dict]] = {}
        for e in self.entries:
            for split in (("train", "eval") if e.get("split", "both") == "both" else (e["split"],)):
                self._by.setdefault((e["kind"], split), []).append(e)

    def __len__(self):
        return len(self.entries)

    def has(self, kind: str, split: str) -> bool:
        return bool(self._by.get((kind, split)))

    def options(self, kind: str, split: str, slots: dict | None = None) -> list[dict]:
        out = self._by.get((kind, split), [])
        if slots is not None:
            out = [e for e in out if used_slots(e["fields"]) <= set(slots)]
        return out

    def choose(self, kind: str, rng, split: str, slots: dict | None = None) -> dict | None:
        """A template of this kind for this split whose slots `slots` can fill, or None."""
        opts = self.options(kind, split, slots)
        return opts[rng.randrange(len(opts))] if opts else None

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for e in self.entries:
            out.setdefault(e["kind"], {}).setdefault(e.get("split", "both"), 0)
            out[e["kind"]][e.get("split", "both")] += 1
        return out


def read_entries(path: Path) -> tuple[list[dict], dict]:
    opener = gzip.open if str(path).endswith(".gz") else open
    entries, meta = [], {}
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if "_meta" in row:
                meta = row["_meta"]
            else:
                entries.append(row)
    return entries, meta


def resolve(name: str | None = None) -> Path:
    """A pack by path or by name in packs/. Default: $ALFRED_PACK, else packs/main if it exists, else the seed."""
    name = name or os.environ.get("ALFRED_PACK") or ""
    if name and Path(name).exists():
        return Path(name)
    for candidate in ([name] if name else ["main", "seed"]):
        for ext in (".jsonl.gz", ".jsonl"):
            p = PACK_DIR / f"{candidate}{ext}"
            if p.exists():
                return p
    raise FileNotFoundError(f"no content pack named {name!r} (looked in {PACK_DIR})")


@lru_cache(maxsize=8)
def _load(path: str, mtime: float) -> Pack:
    entries, meta = read_entries(Path(path))
    good = [e for e in entries if e.get("kind") in KINDS and not problems(e["kind"], e.get("fields"))]
    stem = Path(path).name.split(".")[0]
    return Pack(good, stem, meta, dropped=len(entries) - len(good))


def load(name: str | None = None) -> Pack:
    p = resolve(name).resolve()
    return _load(str(p), p.stat().st_mtime)
