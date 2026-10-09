"""Sample style references from the Enron email corpus. Run where the corpus is reachable (Colab):

    python -m alfred.realism.enron --download /content/enron            # ~1.7 GB from CMU, once
    python -m alfred.realism.enron --tar /content/enron/enron_mail_20150507.tar.gz --out refs.jsonl.gz

The corpus (https://www.cs.cmu.edu/~enron/) was made public by FERC; CMU distributes it for research and asks
users to be sensitive to the privacy of the people in it. So references are only ever *shown* to the writer
model as examples of how real mail reads (tone, length, threading, sign-offs, mess). They never go into a pack:
validators reject any template that copies a 10-word span from its references or names a person from them, and
this file's output stays on your own drive, out of the repo. Addresses and phone numbers are masked here, before
anything is sent to a model.

The tar is read as a stream (no extraction to disk), duplicates across folders are dropped, and each category
keeps a uniform random sample (reservoir sampling).
"""
from __future__ import annotations

import argparse
import email
import gzip
import hashlib
import json
import random
import re
import tarfile
import urllib.request
from collections import Counter
from email.message import Message
from pathlib import Path

from .kinds import ENRON_CATEGORIES

URL = "https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz"
MASK_ADDR = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
MASK_PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]?\d{3}[-. ]\d{4}\b")
MASK_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
BULK_SENDER = re.compile(r"no.?reply|newsletter|news|alert|announce|admin|mailer|daemon|notify|notification|"
                         r"service|support|info@|promo|subscri", re.I)
CALENDAR_HINT = re.compile(r"\b(?:meeting|invitation|resched\w*|conference call|calendar|available|availability|"
                           r"time slot|accepted|declined)\b", re.I)
REQUEST_HINT = re.compile(r"\b(?:please|could you|can you|would you|need you to|let me know)\b", re.I)
THREAD_HINT = re.compile(r"-----\s*Original Message\s*-----|-{5,}\s*Forwarded by|^>", re.I | re.M)
# Words that are also first names; never banned as names.
COMMON = {"Bill", "Mark", "Will", "May", "Grant", "Rich", "Price", "Hope", "Joy", "Faith", "Dawn", "Rose", "Sue",
          "Ray", "Guy", "Art", "Gene", "Chase", "Frank", "Dean", "Jack", "Pat", "Don", "Max", "Rob", "Bob", "Bud",
          "Sunny", "Summer", "April", "June", "Lane", "Page", "Cash", "Day", "King", "Young", "Long", "Brown",
          "White", "Green", "Black", "Gray", "Hall", "Hill", "Wood", "Woods", "Rice", "Love", "The", "And", "For",
          "From", "To", "Cc", "Re", "Fw", "Fwd", "Inc", "Corp", "Enron"}


def download(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / URL.rsplit("/", 1)[1]
    if not out.exists():
        print(f"Downloading {URL} (about 1.7 GB)...")
        tmp = out.with_suffix(".part")
        urllib.request.urlretrieve(URL, tmp)
        tmp.rename(out)
    return out


def _body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                return (payload or b"").decode("latin-1", "replace")
        return ""
    payload = msg.get_payload(decode=True)
    return (payload or b"").decode("latin-1", "replace") if isinstance(payload, bytes) else str(msg.get_payload())


def mask(text: str) -> str:
    text = MASK_ADDR.sub("[address]", text)
    text = MASK_URL.sub("[link]", text)
    return MASK_PHONE.sub("[phone]", text)


def _header_names(msg: Message) -> list[str]:
    """Capitalized name tokens from the display-name headers (X-From, X-To, X-cc)."""
    out = []
    for h in ("X-From", "X-To", "X-cc"):
        raw = re.sub(r"<[^>]*>", " ", str(msg.get(h) or ""))
        out += [t for t in re.findall(r"\b[A-Z][a-z]{2,}\b", raw) if t not in COMMON]
    return out


def categorize(folder: str, sender: str, subject: str, body: str) -> str | None:
    folder, low = folder.lower(), body.lower()
    if "calendar" in folder or (CALENDAR_HINT.search(subject) and len(body) < 1200):
        return "calendar"
    if BULK_SENDER.search(sender) or "unsubscribe" in low:
        return "notification"
    if "personal" in folder or (sender and not sender.lower().endswith("enron.com")):
        return "personal"
    if THREAD_HINT.search(body) or re.match(r"\s*(?:re|fw|fwd):", subject, re.I):
        return "thread"
    if "?" in body and REQUEST_HINT.search(body):
        return "request"
    return "work"


def sample(tar_path: Path, per_category: int = 400, seed: int = 0, limit: int = 0) -> tuple[list[dict], Counter]:
    rng = random.Random(seed)
    keep: dict[str, list[dict]] = {c: [] for c in ENRON_CATEGORIES}
    seen_cat: Counter = Counter()
    seen_bodies: set[str] = set()
    vocab: Counter = Counter()
    n = 0
    with tarfile.open(tar_path, "r|gz") as tar:
        for member in tar:
            if not member.isfile() or "/maildir/" not in f"/{member.name}":
                continue
            parts = member.name.split("/")
            if len(parts) < 4:
                continue
            folder = "/".join(parts[parts.index("maildir") + 2:-1])
            if folder.lower().startswith(("sent", "_sent", "deleted")):
                continue  # the user's own mail and trash
            f = tar.extractfile(member)
            if f is None:
                continue
            msg = email.message_from_bytes(f.read())
            body = _body(msg).strip()
            if not 80 <= len(body) <= 2500 or body.count("\n") > 80:
                continue
            digest = hashlib.sha1(re.sub(r"\s+", " ", body.lower()).encode()).hexdigest()
            if digest in seen_bodies:
                continue
            seen_bodies.add(digest)
            names = _header_names(msg)
            vocab.update(names)
            subject = str(msg.get("Subject") or "").strip()
            cat = categorize(folder, str(msg.get("From") or ""), subject, body)
            if cat is None:
                continue
            seen_cat[cat] += 1
            row = {"id": digest[:12], "category": cat, "subject": mask(subject)[:150], "body": mask(body),
                   "names": sorted(set(names))}
            bucket = keep[cat]
            if len(bucket) < per_category:
                bucket.append(row)
            else:
                j = rng.randrange(seen_cat[cat])
                if j < per_category:
                    bucket[j] = row
            n += 1
            if limit and n >= limit:
                break
    # Names that recur across the corpus, also banned when they turn up in a sampled body.
    common = {name for name, c in vocab.items() if c >= 5}
    rows = []
    for cat in ENRON_CATEGORIES:
        for row in keep[cat]:
            in_body = {t for t in re.findall(r"\b[A-Z][a-z]{2,}\b", row["body"]) if t in common and t not in COMMON}
            row["names"] = sorted(set(row["names"]) | in_body)
            rows.append(row)
    return rows, seen_cat


def write(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read(path: Path) -> list[dict]:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.realism.enron", description=__doc__.split("\n\n")[0])
    p.add_argument("--download", default="", help="Download the corpus into this folder and exit.")
    p.add_argument("--tar", default="", help="Path to enron_mail_20150507.tar.gz.")
    p.add_argument("--out", default="enron_refs.jsonl.gz")
    p.add_argument("--per-category", type=int, default=400)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--limit", type=int, default=0, help="Stop after this many usable messages (for a quick test).")
    a = p.parse_args(argv)
    if a.download:
        print(download(Path(a.download)))
        return None
    if not a.tar:
        p.error("pass --tar (or --download first)")
    rows, seen = sample(Path(a.tar), a.per_category, a.seed, a.limit)
    write(rows, Path(a.out))
    print(f"Usable messages by category: {dict(seen)}")
    print(f"Wrote {len(rows)} references to {a.out}")
    return Path(a.out)


if __name__ == "__main__":
    main()
