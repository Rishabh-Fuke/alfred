"""Build a content pack: an LLM writes each template, code checks it, and rejected drafts go back with the reasons.

    export NEBIUS_API_KEY=...        # or a Colab secret; the key only ever goes to its own provider
    python -m alfred.realism.build --refs enron_refs.jsonl.gz --out packs/main.jsonl.gz

The writer only supplies wording. Every fact (names, dates, amounts, codes) is a {slot} the world fills, and every
template must pass realism.pack.problems() — the same checks the loader applies — plus two more here: no 10-word
span copied from the Enron references it was shown, no person named in them, and not a near-copy of a template
already accepted. Each job is deterministic (its references and style angle come from its id) and the output is
appended as it goes, so an interrupted build resumes where it stopped.
"""
from __future__ import annotations

import argparse
import gzip
import json
import random
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .kinds import (BRACKETS_OK, KINDS, MONTHS_OK, SLOTS, TEXT, TIMES_OK, WEEKDAYS_OK, Kind)
from .pack import PACK_DIR, problems, read_entries, resolve, split_for_id

DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
PRICES = {"nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90), "nvidia/nemotron-3-nano-30b-a3b": (0.06, 0.24)}

SYSTEM = ("You write text templates for a simulated person's inbox, calendar and assistant requests, used to train "
          "and test AI assistants. Real mail is uneven: some messages are one line, some ramble, some have typos, "
          "quoted replies or long signatures. Match that. You only write wording: every name, date, time, amount, "
          "number or code comes from a {slot}, written exactly as given with its braces. Return one JSON object and "
          "nothing else.")

EMAIL_ANGLES = {
    "length": ["one or two lines", "a short paragraph", "two or three short paragraphs", "a few lines plus a "
               "quoted earlier message below"],
    "tone": ["terse and busy", "friendly", "formal", "rushed", "apologetic", "dry and matter-of-fact", "chatty"],
    "quirk": ["all lowercase, little punctuation", "one small typo", "a signature block with a job title",
              "a forwarded message header above the quoted text", "a reply quoted with '>' lines",
              "a short bulleted list", "no greeting at all", "a sign-off with just an initial", "nothing unusual",
              "nothing unusual"],
}
NOTICE_ANGLES = {
    "length": ["very short", "a few lines", "a structured notice with labeled lines"],
    "tone": ["plain transactional", "friendly brand voice", "stiff and formal", "cluttered with boilerplate"],
    "quirk": ["a footer about notification settings", "labeled fields like 'Amount:'", "a short legal-ish footer",
              "nothing unusual"],
}
REQUEST_ANGLES = {
    "tone": ["terse, like a text message", "polite and complete", "casual, all lowercase", "with a short reason or "
             "context first", "a quick question", "a little rambling", "with one small typo"],
}


def _rules(kind: Kind) -> list[str]:
    out = ["Never invent names of people, companies, stores, products or places; use a slot or leave it out. "
           "Never use real brands.",
           "No email addresses, phone numbers, links, years, order or tracking numbers, or codes unless they come "
           "from a slot."]
    if WEEKDAYS_OK not in kind.allow:
        out.append("No weekday names except through slots.")
    if MONTHS_OK not in kind.allow:
        out.append("No month names or dates except through slots.")
    if TIMES_OK not in kind.allow:
        out.append("No clock times (like 3 PM, 10:30, noon) except through slots.")
    if BRACKETS_OK not in kind.allow:
        out.append("No placeholders or brackets like [Name].")
    if kind.fields == TEXT and kind.name.startswith("request:"):
        out.append("One line, in the user's own voice, addressed to their assistant.")
    return out


def prompt(kind: Kind, refs: list[dict], angle: dict, example: dict | None) -> str:
    def slot_line(s):
        return f"  {{{s}}}: {SLOTS[s][0]}"
    lines = [f"Write one template of kind '{kind.name}'.", "", f"What it is: {kind.describe}"]
    if kind.sender:
        lines.append(f"Who writes it: {kind.sender}.")
    lines += ["", f"Return a JSON object with exactly these keys: {', '.join(kind.fields)}."]
    if kind.required:
        lines += ["", "Slots it must use:"] + [slot_line(s) for s in kind.required]
    groups = [g for g in kind.any_of]
    for g in groups:
        lines += ["", "It must use at least one of:"] + [slot_line(s) for s in g]
    optional = [s for s in kind.optional if s not in kind.required]
    if optional:
        lines += ["", "Slots it may use:"] + [slot_line(s) for s in optional]
    if not kind.slots:
        lines += ["", "This kind has no slots."]
    lines += ["", "Rules:"] + [f"- {r}" for r in _rules(kind)]
    if angle:
        lines += ["", "This one should be: " + "; ".join(f"{k}: {v}" for k, v in angle.items()) + "."]
    if refs:
        lines += ["", "Real emails to match in style: tone, length, structure, how people actually write. Don't copy "
                      "their sentences, names or details; write something new about something else."]
        for i, r in enumerate(refs, 1):
            lines += ["", f"--- reference {i}", f"Subject: {r['subject']}", r["body"][:1500]]
        lines.append("--- end of references")
    if example:
        lines += ["", "Format example (shows the slot style only; don't reuse its wording):",
                  json.dumps(example, ensure_ascii=False)]
    return "\n".join(lines)


def parse(text: str, kind: Kind) -> dict | None:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return {f: obj[f] for f in kind.fields if f in obj}


def _grams(text: str, n: int = 5) -> set:
    w = re.findall(r"[a-z0-9{}_']+", text.lower())
    return {tuple(w[i:i + n]) for i in range(max(1, len(w) - n + 1))}


class Builder:
    def __init__(self, client, model: str, refs: list[dict], seed: int = 0, max_tries: int = 4,
                 temperature: float = 0.9, examples: dict | None = None, extra_body: dict | None = None):
        self.client, self.model, self.seed = client, model, seed
        self.max_tries, self.temperature, self.extra_body = max_tries, temperature, extra_body
        self.refs_by = defaultdict(list)
        for r in refs:
            self.refs_by[r["category"]].append(r)
        self.examples = examples or {}
        self.lock = threading.Lock()
        self.accepted: dict[str, list[set]] = defaultdict(list)
        self.usage = Counter()
        self.reasons: dict[str, Counter] = defaultdict(Counter)
        self.tries: Counter = Counter()

    def remember(self, kind: str, fields: dict) -> None:
        self.accepted[kind].append(_grams(" ".join(fields.values())))

    def too_close(self, kind: str, fields: dict) -> bool:
        mine = _grams(" ".join(fields.values()))
        with self.lock:
            return any(len(mine & g) / max(1, len(mine | g)) > 0.5 for g in self.accepted[kind])

    def job(self, kind: Kind, entry_id: str) -> dict | None:
        rng = random.Random(f"build:{self.seed}:{entry_id}")
        pool = [r for c in kind.refs for r in self.refs_by.get(c, [])]
        refs = rng.sample(pool, min(3, len(pool))) if pool else []
        angles = (REQUEST_ANGLES if kind.name.startswith("request:") else
                  NOTICE_ANGLES if "notification" in kind.refs[:1] else EMAIL_ANGLES)
        if kind.fields != ("subject", "body") and not kind.name.startswith("request:"):
            angles = {"tone": EMAIL_ANGLES["tone"]}
        angle = {k: rng.choice(v) for k, v in angles.items()}
        ex = self.examples.get(kind.name)
        example = rng.choice(ex) if ex else None
        names = {n for r in refs for n in r.get("names", [])}
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt(kind, refs, angle, example)}]
        for attempt in range(self.max_tries):
            text = self._call(messages, rng)
            with self.lock:
                self.tries[kind.name] += 1
            fields = parse(text, kind)
            if fields is None:
                found = ["the reply wasn't one JSON object with the keys " + ", ".join(kind.fields)]
            else:
                fields = {k: str(v).strip() for k, v in fields.items()}
                found = problems(kind.name, fields, [r["body"] for r in refs] + [r["subject"] for r in refs], names)
                if not found and self.too_close(kind.name, fields):
                    found = ["it's too close to a template already written; change the situation and wording"]
            if not found:
                with self.lock:
                    self.remember(kind.name, fields)
                return {"id": entry_id, "kind": kind.name, "split": split_for_id(entry_id), "fields": fields,
                        "source": self.model, "refs": [r["id"] for r in refs], "angle": angle}
            with self.lock:
                for f in found:
                    self.reasons[kind.name][f.split(" (")[0][:90]] += 1
            messages += [{"role": "assistant", "content": text or ""},
                         {"role": "user", "content": "That doesn't pass these checks:\n" +
                          "\n".join(f"- {f}" for f in found) + "\nWrite it again as one JSON object."}]
        return None

    def _call(self, messages, rng) -> str:
        for attempt in range(5):
            try:
                kw = dict(model=self.model, messages=messages, temperature=self.temperature, max_tokens=1500)
                if self.extra_body:
                    kw["extra_body"] = self.extra_body
                resp = self.client.chat.completions.create(**kw)
                u = getattr(resp, "usage", None)
                if u is not None:
                    with self.lock:
                        self.usage["prompt"] += u.prompt_tokens or 0
                        self.usage["completion"] += u.completion_tokens or 0
                return resp.choices[0].message.content or ""
            except Exception as e:  # rate limits and timeouts: back off and retry
                if attempt == 4:
                    raise
                wait = 2 ** attempt + rng.random()
                print(f"  ({type(e).__name__}: {str(e)[:100]}; retrying in {wait:.0f}s)", flush=True)
                time.sleep(wait)
        return ""


def plan(kinds: list[str], scale: float, per_kind: int) -> list[tuple[Kind, str]]:
    jobs = []
    for name in kinds:
        k = KINDS[name]
        n = per_kind or max(1, round(k.count * scale))
        jobs += [(k, f"{name.replace(':', '.')}-{i:04d}") for i in range(n)]
    return jobs


def report(builder: Builder, done: list[dict], planned: list[tuple[Kind, str]], model: str, seconds: float) -> str:
    want = Counter(k.name for k, _ in planned)
    got = Counter(e["kind"] for e in done)
    pin, pout = PRICES.get(model, (0.0, 0.0))
    cost = builder.usage["prompt"] / 1e6 * pin + builder.usage["completion"] / 1e6 * pout
    lines = [f"# Content pack build", "", f"Model: {model}. Time: {seconds / 60:.1f} min. Tokens: "
             f"{builder.usage['prompt']:,} in, {builder.usage['completion']:,} out (about ${cost:.2f} this run).", "",
             "| Kind | Kept | Planned | Model calls | Most common rejection |", "|---|---:|---:|---:|---|"]
    for name in sorted(want):
        top = builder.reasons[name].most_common(1)
        lines.append(f"| {name} | {got[name]} | {want[name]} | {builder.tries[name]} | "
                     f"{(top[0][0] + f' ({top[0][1]})') if top else ''} |")
    lines += ["", f"Kept {sum(got.values())} of {sum(want.values())} planned templates."]
    return "\n".join(lines)


def write_pack(entries: list[dict], out: Path, meta: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if str(out).endswith(".gz") else open
    rows = [{"_meta": meta}] + sorted(entries, key=lambda e: e["id"])
    with opener(out, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build(client, out: Path, refs: list[dict], model: str = DEFAULT_MODEL, kinds: list[str] | None = None,
          scale: float = 1.0, per_kind: int = 0, workers: int = 16, seed: int = 0, extra_body: dict | None = None,
          log=print) -> tuple[list[dict], str]:
    kinds = kinds or list(KINDS)
    partial = out.with_name(out.name.split(".")[0] + ".partial.jsonl")
    done: list[dict] = []
    if partial.exists():
        done = [json.loads(line) for line in partial.read_text().splitlines() if line.strip()]
    seed_entries, _ = read_entries(resolve("seed"))
    examples = defaultdict(list)
    for e in seed_entries:
        examples[e["kind"]].append(e["fields"])
    b = Builder(client, model, refs, seed, examples=examples, extra_body=extra_body)
    for e in done:
        b.remember(e["kind"], e["fields"])
    planned = plan(kinds, scale, per_kind)
    have = {e["id"] for e in done}
    todo = [(k, i) for k, i in planned if i not in have]
    log(f"{len(planned)} templates planned, {len(planned) - len(todo)} already built, {len(todo)} to write "
        f"with {model} ({workers} at a time).")
    start, lock = time.time(), threading.Lock()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool, partial.open("a") as f:
        futures = {pool.submit(b.job, k, i): (k, i) for k, i in todo}
        for n, fut in enumerate(as_completed(futures), 1):
            entry = fut.result()
            if entry:
                with lock:
                    done.append(entry)
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    f.flush()
            if n % 50 == 0 or n == len(todo):
                log(f"  {n}/{len(todo)} written, {len(done)} kept, {time.time() - start:.0f}s")
    text = report(b, done, planned, model, time.time() - start)
    meta = {"name": out.name.split(".")[0], "model": model, "seed": seed, "scale": scale,
            "built": time.strftime("%Y-%m-%d"), "kinds": len(kinds),
            "refs": "Enron corpus (style references only; no text copied)" if refs else "none"}
    write_pack(done, out, meta)
    out.with_name(out.name.split(".")[0] + ".build.md").write_text(text)
    return done, text


def main(argv=None):
    from ..eval import resolve_endpoint
    from .enron import read as read_refs
    p = argparse.ArgumentParser(prog="python -m alfred.realism.build", description=__doc__.split("\n\n")[0])
    p.add_argument("--refs", default="", help="References from alfred.realism.enron (optional but recommended).")
    p.add_argument("--out", default=str(PACK_DIR / "main.jsonl.gz"))
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--base-url", default="tokenfactory", help="tokenfactory, vllm, or a URL.")
    p.add_argument("--kinds", default="", help="Comma-separated kinds (default: all).")
    p.add_argument("--scale", type=float, default=1.0, help="Multiply every kind's planned count.")
    p.add_argument("--per-kind", type=int, default=0, help="Exactly this many per kind (for a quick test).")
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--think", action="store_true", help="Let the model reason first (slower, rarely needed).")
    a = p.parse_args(argv)
    from openai import OpenAI
    url, key = resolve_endpoint(a.base_url, None)
    client = OpenAI(base_url=url, api_key=key, timeout=180, max_retries=2)
    refs = read_refs(Path(a.refs)) if a.refs else []
    if not refs:
        print("No --refs given: templates will follow the kind descriptions only, without real-mail style.")
    kinds = [k.strip() for k in a.kinds.split(",") if k.strip()] or None
    unknown = set(kinds or []) - set(KINDS)
    if unknown:
        p.error(f"unknown kinds: {sorted(unknown)}")
    extra = None if a.think else {"chat_template_kwargs": {"enable_thinking": False}}
    _, text = build(client, Path(a.out), refs, a.model, kinds, a.scale, a.per_kind, a.workers, a.seed, extra)
    print(text)


if __name__ == "__main__":
    main()
