"""Build one procedural app task: the tools on offer, what the user says, and what the right move is.

Five situations, each a habit that should carry over to any app:
- call:    everything needed was said. Call the right tool with exactly what was said; leave unsaid optional
           details out (or at their documented default).
- ask:     a required detail is missing and nothing on offer can find it. Ask; don't fill it in.
- lookup:  a required id wasn't given, but a tool on offer lists it ("my lisinopril prescription"). Look it up;
           asking the user is unneeded.
- nearby:  the request needs a tool that isn't offered, and a related one is ("pay the bill" with only
           get_bill and set_autopay). Say it can't be done; don't use the related tool instead.
- none:    nothing on offer fits at all (the tools are another app's). Say it can't be done.

Tool names, parameter names and descriptions are drawn per task in one of several naming styles, so nothing about
a particular schema can be memorized.
"""
from __future__ import annotations

import random
import re
from datetime import date, timedelta

from .catalog import HELDOUT, TRAIN, Domain, Entity, Op, Param
from .values import say_date, say_number, say_time

SITUATIONS = ("call", "ask", "lookup", "nearby", "none")
NAME_STYLES = ("snake", "camel", "prefixed", "pascal", "double")
_SLOT = re.compile(r"\{(\w+)\}")


# ----------------------------------------------------------------------------- names and schemas

def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(w.capitalize() for w in rest)


def _pascal(snake: str) -> str:
    return "".join(w.capitalize() for w in snake.split("_"))


def tool_name(d: Domain, op: Op, style: str, rng: random.Random) -> str:
    stem, prefix = rng.choice(op.names), rng.choice(d.prefixes)
    return {"snake": stem, "camel": _camel(stem), "prefixed": f"{prefix}_{stem}",
            "pascal": _pascal(prefix) + _pascal(stem), "double": f"{prefix}__{stem}"}[style]


def param_name(p: Param, style: str, rng: random.Random) -> str:
    n = rng.choice(p.names)
    return _camel(n) if style in ("camel", "pascal") else n


_JSON_TYPES = {"integer": "integer", "amount": "number", "boolean": "boolean"}


def schema(d: Domain, op: Op, name: str, style: str, rng: random.Random) -> tuple[dict, dict]:
    """The tool's schema and its parameter names (schema name -> catalog key)."""
    props, required, names = {}, [], {}
    for key in op.req + op.opt:
        p = d.params[key]
        pname = param_name(p, style, rng)
        while pname in names:
            pname = param_name(p, style, rng) + "_"
        names[pname] = key
        desc = rng.choice(p.desc)
        if p.kind == "date" and "YYYY" not in desc and rng.random() < 0.6:
            desc += " Format: YYYY-MM-DD."
        if p.kind == "time" and "HH" not in desc and rng.random() < 0.6:
            desc += " Format: HH:MM."
        if p.default is not None and key in op.opt:
            shown = str(p.default).lower() if isinstance(p.default, bool) else p.default
            desc += f" Defaults to {shown}."
        if key in op.req and rng.random() < 0.3:
            desc = "Required. " + desc
        prop = {"type": _JSON_TYPES.get(p.kind, "string"), "description": desc}
        if p.kind == "enum":
            prop["enum"] = list(p.values)
        props[pname] = prop
        if key in op.req:
            required.append(pname)
    fn = {"name": name, "description": rng.choice(op.desc),
          "parameters": {"type": "object", "properties": props, "required": required}}
    return {"type": "function", "function": fn}, names


# ----------------------------------------------------------------------------- records

def _fill_id(fmt: str, rng: random.Random) -> str:
    def rep(m):
        kind, n = m.group(1), int(m.group(2))
        pool = "0123456789" if kind == "d" else "ABCDEFGHJKLMNPQRSTUVWXYZ"
        first = pool[1:] if kind == "d" else pool
        return rng.choice(first) + "".join(rng.choice(pool) for _ in range(n - 1))
    return re.sub(r"\{([dA])(\d)\}", rep, fmt)


def make_records(e: Entity, rng: random.Random, n: int, keep: dict | None = None) -> list[dict]:
    """n records of an entity. keep, if given, is one of them (the one a task is about); the others never share
    its ref_field, so "my lisinopril prescription" picks out exactly one."""
    out = [dict(keep)] if keep else []
    used = {keep[e.ref_field]} if keep and e.ref_field else set()
    ids = {r["id"] for r in out}
    tries = 0
    while len(out) < n and tries < 200:
        tries += 1
        rec = {"id": _fill_id(e.id_fmt, rng), **{f: rng.choice(v) for f, v in e.fields.items()}}
        if rec["id"] in ids or (e.ref_field and rec[e.ref_field] in used):
            continue
        ids.add(rec["id"])
        if e.ref_field:
            used.add(rec[e.ref_field])
        out.append(rec)
    rng.shuffle(out)
    return out


# ----------------------------------------------------------------------------- saying values

def draw(p: Param, today: date, rng: random.Random):
    """A value for a parameter, in its canonical form."""
    if p.kind == "date":
        return (today + timedelta(days=rng.randint(1, 20))).isoformat()
    if p.kind == "boolean":
        return rng.choice([True, False]) if False in p.say else True
    if p.kind == "id":
        return ""  # filled in from the record the task is about
    return rng.choice(p.values)


def say(p: Param, value, today: date, rng: random.Random, ref: str = "") -> str:
    """How a person would say a value in a request."""
    if ref:
        return ref
    if p.kind == "date":
        return say_date(date.fromisoformat(value), today, rng)
    if p.kind == "time":
        return say_time(value, rng)
    if p.kind == "boolean":
        return p.say.get(bool(value), "")
    if p.kind == "enum":
        v = p.say.get(value, value.replace("_", " "))
    elif p.kind == "integer":
        v = say_number(int(value), rng)
    else:
        v = value
    return p.fmt.format(v=v)


def _answer(said: list[tuple[str, str]], rng: random.Random) -> str:
    """What the user says when asked for the missing details: (label, phrase) pairs."""
    if len(said) > 1:  # name each one, so two dates can't be mixed up
        return "; ".join(f"{label[0].upper() + label[1:]}: {re.sub(r'^(on|at|in|for|to) ', '', x)}"
                         for label, x in said) + "."
    x = said[0][1]
    if "@" in x:
        return rng.choice([f"It's {x}", f"Use {x}", x])
    options = [x[0].upper() + x[1:] + ".", f"Oh, {x}.", x[0].upper() + x[1:] + ", please."]
    if re.match(r"(on|in|at) ", x):
        options.append(f"Sorry, {x}.")
    return rng.choice(options)


# ----------------------------------------------------------------------------- requests

def _parts(template: str) -> list[tuple[bool, str]]:
    """(optional, text) pieces of a template."""
    out = []
    for piece in re.split(r"(\[[^\]]*\])", template):
        if piece.startswith("["):
            out.append((True, piece[1:-1]))
        elif piece:
            out.append((False, piece))
    return out


def _render(template: str, want: set, rng: random.Random) -> tuple[str, set]:
    """Say the template, keeping each optional part only if every slot in it is wanted. Returns the text and the
    slots it mentions."""
    text, said = "", set()
    for optional, piece in _parts(template):
        slots = set(_SLOT.findall(piece))
        if optional and (not slots <= want or (not slots and rng.random() < 0.5)):
            continue
        text += piece
        said |= slots
    return text, said


def _tidy(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\b(until|from|to|by|starting|through|since|for) on\b", r"\1", s)  # "until on Friday"
    s = re.sub(r"\s+([,.?!)])", r"\1", s)
    s = re.sub(r"(?<!\.)\.\.(?!\.)", ".", s)  # a name ending in a period at the end of a sentence
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r",\s*([.?!])", r"\1", s)
    return s[0].upper() + s[1:] if s else s


def eligible(d: Domain, op: Op, situation: str) -> bool:
    if situation in ("call", "none"):
        return bool(op.say)
    if situation == "nearby":
        return bool(op.siblings) and bool(op.say)
    if situation == "ask":
        return any(_can_drop(op, k) for k in op.req if d.params[k].kind != "id")
    if situation == "lookup":
        return any(d.params[k].kind == "id" and _lister(d, d.params[k].entity, op) for k in op.req)
    raise ValueError(situation)


def _can_drop(op: Op, key: str) -> bool:
    """Some template says this required detail only in an optional part."""
    return any(all(key not in _SLOT.findall(t) for opt, t in _parts(tpl) if not opt)
               and any(key in _SLOT.findall(t) for opt, t in _parts(tpl) if opt) for tpl in op.say)


def _lister(d: Domain, entity: str, besides: Op) -> Op | None:
    """A read op here that lists records of the entity and needs at most a search query, which the way the user
    refers to the record supplies ("the lease agreement" -> search "lease"). One that needs a city or a date
    nobody gave would turn a lookup into an ask."""
    for o in d.ops.values():
        if o is not besides and not o.writes and o.lists == entity and set(o.req) <= {"query"}:
            return o
    return None


def make_spec(key: str, situation: str, held_out: bool, today: date, user: dict) -> dict:
    """Everything about one task, as plain JSON-able data. key seeds every random choice."""
    if situation not in SITUATIONS:
        raise ValueError(f"unknown situation {situation!r}")
    rng = random.Random(key)
    pool = HELDOUT if held_out else TRAIN
    choices = [(d, op) for d in pool.values() for op in d.ops.values() if eligible(d, op, situation)]
    for _ in range(100):
        d, op = rng.choice(choices)
        spec = _try(d, op, situation, held_out, today, user, pool, rng)
        if spec:
            return spec
    raise RuntimeError(f"couldn't build a {situation} task for {key}")


def _try(d: Domain, op: Op, situation: str, held_out: bool, today: date, user: dict, pool: dict,
         rng: random.Random) -> dict | None:
    values = {k: draw(d.params[k], today, rng) for k in op.req + op.opt}
    for start, end in (("start_date", "end_date"),):  # a range runs forward
        if start in values and end in values:
            a, b = sorted({values[start], values[end]} | {(date.fromisoformat(values[start])
                                                            + timedelta(days=rng.randint(2, 6))).isoformat()})[:2]
            values[start], values[end] = a, b
    # ids: the record the request is about
    target_records, refs = {}, {}
    for k in op.req + op.opt:
        p = d.params[k]
        if p.kind == "id":
            rec = make_records(d.entities[p.entity], rng, 1)[0]
            target_records[k] = rec
            values[k] = rec["id"]

    missing: list[str] = []
    lookup_key = ""
    if situation == "ask":
        droppable = [k for k in op.req if d.params[k].kind != "id" and _can_drop(op, k)]
        missing = rng.sample(droppable, 2 if len(droppable) > 1 and rng.random() < 0.2 else 1)
    if situation in ("nearby", "none"):  # can't be done anyway: say records the way people do
        for k, rec in target_records.items():
            e = d.entities[d.params[k].entity]
            if e.refs:
                refs[k] = rng.choice(e.refs).format(**rec)
    if situation == "lookup":
        lookup_key = rng.choice([k for k in op.req if d.params[k].kind == "id" and _lister(d, d.params[k].entity, op)])
        e = d.entities[d.params[lookup_key].entity]
        if not e.refs:
            return None
        refs[lookup_key] = rng.choice(e.refs).format(**target_records[lookup_key])

    optional_wanted = {k for k in op.opt if rng.random() < 0.5}
    want = (set(op.req) - set(missing)) | optional_wanted
    template = rng.choice(op.say)
    text, said = _render(template, want, rng)
    if not set(op.req) - set(missing) <= said or said & set(missing):
        return None
    if situation == "ask" and not missing:
        return None
    phrases = {k: say(d.params[k], values[k], today, rng, refs.get(k, "")) for k in said}
    request = _tidy(_SLOT.sub(lambda m: phrases[m.group(1)], text))

    # what's on offer
    style = rng.choice(NAME_STYLES)
    others = [o for o in d.ops.values() if o is not op]
    if situation == "nearby":
        offered = [d.ops[s] for s in op.siblings]
        offered += rng.sample([o for o in others if o not in offered], min(len(others) - len(offered),
                                                                          rng.randint(0, 2)))
    elif situation == "none":
        offered = []
    else:
        offered = [op]
        if situation == "lookup":
            offered.append(_lister(d, d.params[lookup_key].entity, op))
        rest = [o for o in others if o not in offered]
        offered += rng.sample(rest, min(len(rest), rng.randint(0, 2)))
    foreign = [(fd, fo) for fd in pool.values() if fd is not d for fo in fd.ops.values()]
    n_foreign = rng.randint(2, 4) if situation == "none" else rng.randint(0, 3)
    picked = [(d, o) for o in offered] + rng.sample(foreign, n_foreign)
    rng.shuffle(picked)

    tools, tool_ops, by_op = [], {}, {}
    for dd, oo in picked:
        name = tool_name(dd, oo, style, rng)
        while name in tool_ops:
            name = tool_name(dd, oo, "prefixed" if style != "prefixed" else "double", rng)
        sch, pnames = schema(dd, oo, name, style, rng)
        tools.append(sch)
        tool_ops[name] = {"domain": dd.key, "op": oo.key, "params": pnames, "writes": oo.writes}
        by_op[(dd.key, oo.key)] = name

    target = by_op.get((d.key, op.key)) if situation in ("call", "ask", "lookup") else None
    expected = {}
    if target:
        inverse = {v: k for k, v in tool_ops[target]["params"].items()}
        for k in op.req:
            expected[inverse[k]] = values[k]
        for k in op.opt:
            if k in said:
                expected[inverse[k]] = values[k]
    answer = (_answer([(d.params[k].label, say(d.params[k], values[k], today, rng)) for k in missing], rng)
              if missing else None)
    revealed = {k: values[k] for k in said if k not in refs}
    records = {}
    for k, rec in target_records.items():
        e = d.entities[d.params[k].entity]
        records[e.key] = make_records(e, rng, rng.randint(3, 4), keep=rec)
    tempting = [by_op[(d.key, s)] for s in op.siblings if (d.key, s) in by_op] if situation == "nearby" else []

    return {
        "domain": d.key, "app": d.app, "op": op.key, "op_label": op.label, "situation": situation,
        "held_out": held_out, "today": today.isoformat(), "style": style,
        # A "none" task offers only other apps' tools, so its prompt names one of those, never the app asked about.
        "request": request, "system": system_prompt(user, today, picked[0][0].app if situation == "none" else d.app,
                                                    rng),
        "tools": tools, "tool_ops": tool_ops,
        "target": target, "expected": expected,
        "missing": [k for k in missing], "missing_labels": [d.params[k].label for k in missing],
        "answer": answer, "revealed": revealed,
        "lookup": by_op[(d.key, _lister(d, d.params[lookup_key].entity, op).key)] if lookup_key else None,
        "lookup_param": lookup_key or None,
        "records": records, "tempting": tempting,
    }


def system_prompt(user: dict, today: date, app: str, rng: random.Random) -> str:
    """One of a few system prompts, from none at all (as on most benchmarks) to a few lines of guidance."""
    first = user.get("first") or user.get("name", "the user").split()[0]
    when = f"{today.strftime('%A')}, {today.strftime('%B')} {today.day}, {today.year}"
    return rng.choice([
        f"Today is {when}.",
        f"You are a helpful assistant with access to tools. Today's date is {today.isoformat()}.",
        f"You are {first}'s assistant, connected to their {app} app and a few others. Today is {when}. Use the "
        f"tools when they help. If you need something only {first} knows, ask. If the tools can't do what "
        f"{first} wants, say so.",
    ])
