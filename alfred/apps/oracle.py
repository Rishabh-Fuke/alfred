"""The scripted solver for procedural app tasks, used by OracleAgent (one fixed wording), TeachingOracle (varied
wording, for training data) and GuessAgent (fills in missing details instead of asking, to show that fails)."""
from __future__ import annotations

import json

from .catalog import HELDOUT, TRAIN

DOMAINS = {**TRAIN, **HELDOUT}

ASK_ONE = ("{Q}?", "Sure. {Q}?", "Happy to help. {Q}?", "Before I do that, {q}?", "I can do that. {Q}?")
DECLINE_NEARBY = (
    "I can't {label} with the tools I have. I can only {siblings}.",
    "Sorry, I'm not able to {label} here. The tools I have only let me {siblings}.",
    "There's no tool to {label} here, so I can't do that. I could {siblings} if that would help.",
    "I can't do that: none of my tools can {label}. The closest I have would {siblings}, which isn't the same thing.",
)
DECLINE_NONE = (
    "I can't {label} with the tools I have.",
    "Sorry, I don't have a tool that can {label}.",
    "That's not something I can do here: none of my tools can {label}.",
    "I'm not able to {label}. The tools I'm connected to are for other things.",
)
DONE_WRITE = ("Done.", "All set.", "That's done.", "Done, it went through.")
DONE_READ = ("Here's what I found: {found}.", "I found {n}: {found}.", "{Found}.")  # found: every result


def _typed(schema_type: str, value):
    if schema_type == "integer":
        return int(float(value))
    if schema_type == "number":
        return float(value)
    if schema_type == "boolean":
        return bool(value)
    return str(value)


def _props(env, tool: str) -> dict:
    fn = next(t["function"] for t in env.spec["tools"] if t["function"]["name"] == tool)
    return fn["parameters"]["properties"]


def _summary(record: dict) -> str:
    return next((str(v) for k, v in record.items() if k != "id"), record["id"])


def _join(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " or " + xs[-1]


def _call(env, tool: str, args: dict) -> dict:
    return json.loads(env.call_tool(tool, args))  # not scripted.call: a tool may have a parameter called "name"


def solve(agent, env, _params=None) -> None:
    from ..agents.scripted import ask, finish

    sp = env.spec
    d = DOMAINS[sp["domain"]]
    op = d.ops[sp["op"]]
    situation = sp["situation"]
    if situation in ("nearby", "none"):
        if situation == "nearby":
            labels = [d.ops[sp["tool_ops"][t]["op"]].label for t in sp["tempting"]]
            msg = agent.choose(DECLINE_NEARBY).format(label=op.label, siblings=_join(labels))
        else:
            msg = agent.choose(DECLINE_NONE).format(label=op.label)
        finish(env, "cannot_do", msg)
        return

    target = sp["target"]
    props = _props(env, target)
    args = dict(sp["expected"])
    if situation == "ask":
        made_up = agent.fill_missing(env, sp)
        if made_up is None:
            params = [d.params[k] for k in sp["missing"]]
            qs = [agent.choose(p.q or (f"what {p.label} should I use",)) for p in params]
            if len(qs) == 1:
                question = agent.choose(ASK_ONE).format(Q=qs[0][0].upper() + qs[0][1:], q=qs[0])
            else:
                question = qs[0][0].upper() + qs[0][1:] + ", and " + " and ".join(qs[1:]) + "?"
            ask(env, question)
        else:
            args.update(made_up)
    if situation == "lookup":
        lister = sp["lookup"]
        key = sp["lookup_param"]
        entity = d.entities[d.params[key].entity]
        pname = next(p for p, k in sp["tool_ops"][target]["params"].items() if k == key)
        record = next(r for r in sp["records"][entity.key] if r["id"] == sp["expected"][pname])
        largs = {pname: record[entity.ref_field] for pname, k in sp["tool_ops"][lister]["params"].items()
                 if k == "query" and pname in _props(env, lister)}
        found = _call(env, lister, largs)
        assert any(r["id"] == record["id"] for r in found.get("results", [])), \
            f"{record['id']} isn't in what {lister} returns, so the task isn't solvable as written"
    res = _call(env, target, {k: _typed(props[k]["type"], v) for k, v in args.items()})
    if "results" in res:
        found = [_summary(r) for r in res["results"]]
        listed = found[0] if len(found) == 1 else ", ".join(found[:-1]) + " and " + found[-1]
        msg = agent.choose(DONE_READ).format(found=listed, Found=listed[0].upper() + listed[1:], n=len(found))
    else:
        msg = agent.choose(DONE_WRITE)
    if not env.done:
        finish(env, "done", msg)


def made_up_values(env, sp) -> dict:
    """What a guessing agent fills in for the missing details: the defaults When2Call failures used."""
    d = DOMAINS[sp["domain"]]
    inverse = {k: p for p, k in sp["tool_ops"][sp["target"]]["params"].items()}
    out = {}
    for key in sp["missing"]:
        p = d.params[key]
        out[inverse[key]] = {"date": env.today.isoformat(), "time": "12:00", "integer": 1, "amount": 10,
                             "boolean": True, "enum": p.values[0] if p.values else "default",
                             "text": "as discussed"}.get(p.kind, "Berkeley" if key == "city" else "default")
    return out
