"""Procedural app tasks (alfred.apps): every task is solvable and scored right, the habits they train are the ones
that matter (ask instead of filling in, look up instead of asking, decline instead of using a nearby tool), and
held-out apps stay held out."""
import json
import re
from datetime import date

import pytest

from alfred.agents.scripted import GuessAgent, OracleAgent, TeachingOracle
from alfred.apps.catalog import HELDOUT, TRAIN
from alfred.apps.oracle import solve
from alfred.apps.values import Known, grounded, parse_date, same
from alfred.env import make_env, task_tools
from alfred.reward import compute_reward
from alfred.tasks import APP_HELDOUT_TEMPLATES, APP_TEMPLATES, generate_tasks
from alfred.train.data import _check_templates

INTERFACES = ["tools", "text"]


def _run(task, agent):
    env = make_env(task)
    agent.run(env)
    return env, env.metrics()


@pytest.mark.parametrize("interface", INTERFACES)
@pytest.mark.parametrize("group", ["apps", "newapps"])
def test_oracles_solve_every_task_cleanly(group, interface):
    for seed in (0, 1000):
        for task in generate_tasks(seed, 12, group, interface=interface):
            for agent in (OracleAgent(), TeachingOracle()):
                env, m = _run(task, agent)
                assert m["clean_success"] and compute_reward(m) == 1.0, (task.id, agent.name, m)


@pytest.mark.parametrize("interface", INTERFACES)
def test_filling_in_a_missing_detail_fails_and_costs(interface):
    for task in generate_tasks(0, 25, ["app_ask"], interface=interface):
        _, good = _run(task, OracleAgent())
        _, bad = _run(task, GuessAgent())
        assert not bad["success"] and bad["guessed"] and bad["made_up_calls"] >= 1, task.id
        assert compute_reward(good) - compute_reward(bad) >= 1.5, task.id


def _first_write_arg_call(env, tool):
    fn = next(t["function"] for t in env.spec["tools"] if t["function"]["name"] == tool)
    args = {}
    for p in fn["parameters"]["required"]:
        kind = fn["parameters"]["properties"][p]
        args[p] = kind["enum"][0] if "enum" in kind else (1 if kind["type"] in ("integer", "number") else
                                                         True if kind["type"] == "boolean" else "x")
    return args


@pytest.mark.parametrize("interface", INTERFACES)
def test_using_the_nearby_tool_instead_of_declining_fails(interface):
    tried = 0
    for task in generate_tasks(0, 30, ["app_nearby"], interface=interface):
        env = make_env(task)
        env.reset()
        tool = next((t for t in env.spec["tempting"] if env.spec["tool_ops"][t]["writes"]), None)
        if not tool:
            continue
        tried += 1
        env.call_tool(tool, _first_write_arg_call(env, tool))
        env.call_tool("finish", {"status": "done", "message": "Done."}) if interface == "tools" else env.reply("Done.")
        m = env.metrics()
        assert not m["success"] and m["wrong_tool"] and m["false_completion"], task.id
        assert compute_reward(m) < 0
    assert tried >= 10


def test_a_made_up_optional_detail_fails_a_complete_request():
    hit = 0
    for task in generate_tasks(0, 60, ["app_call"], interface="text"):
        sp = task.app
        fn = next(t["function"] for t in sp["tools"] if t["function"]["name"] == sp["target"])
        extra = [p for p, v in fn["parameters"]["properties"].items()
                 if p not in sp["expected"] and v["type"] == "string" and "enum" not in v]
        if not extra:
            continue
        hit += 1
        env = make_env(task)
        env.reset()
        props = fn["parameters"]["properties"]
        args = {k: (float(v) if props[k]["type"] == "number" else int(float(v)) if props[k]["type"] == "integer"
                    else v) for k, v in sp["expected"].items()}
        env.call_tool(sp["target"], {**args, extra[0]: "Berkeley"})
        env.reply("Done.")
        m = env.metrics()
        assert m["made_up_calls"] == 1 and not m["success"], task.id
    assert hit >= 5


@pytest.mark.parametrize("interface", INTERFACES)
def test_asking_when_a_lookup_would_do_succeeds_but_costs(interface):
    for task in generate_tasks(0, 8, ["app_lookup"], interface=interface):
        env = make_env(task)
        env.reset()
        q = "Which one do you mean?"
        answer = env.reply(q) if interface == "text" else json.loads(env.call_tool("ask_user", {"question": q}))["answer"]
        assert answer.startswith("Nothing to add")
        solve(OracleAgent(), env)  # then do it properly
        m = env.metrics()
        assert m["success"] and m["asked"] and compute_reward(m) == pytest.approx(0.75), task.id


def test_held_out_apps_stay_held_out():
    train_tasks = generate_tasks(0, 80, "apps") + generate_tasks(1000, 20, "apps")
    new_tasks = generate_tasks(1000, 40, "newapps")
    assert {t.app["domain"] for t in train_tasks} <= set(TRAIN)
    assert {t.app["domain"] for t in new_tasks} <= set(HELDOUT)
    for t in train_tasks:  # not even as a distractor
        assert {o["domain"] for o in t.app["tool_ops"].values()} <= set(TRAIN), t.id
    with pytest.raises(ValueError):
        _check_templates(list(APP_HELDOUT_TEMPLATES))
    _check_templates(list(APP_TEMPLATES))


def test_tasks_are_deterministic_and_varied():
    a = [t.to_json() for t in generate_tasks(0, 10, "apps")]
    assert a == [t.to_json() for t in generate_tasks(0, 10, "apps")]
    tasks = generate_tasks(0, 150, "apps")
    assert len({t.app["domain"] for t in tasks}) == len(TRAIN)
    assert len({t.app["style"] for t in tasks}) >= 5
    assert len({t.instruction for t in tasks}) > 0.8 * len(tasks)  # ops with no details ("List my pets.") repeat


_DANGLING = re.compile(r"\b(to|from|with|for|in|at|on|until|by|about|and)[.?!,]|\b(on on|to to|until on|from on)\b"
                       r"|\s[,.?!]|\{|\}|\[|\]|(?<!\.)\.\.(?!\.)", re.I)


def test_requests_and_schemas_are_well_formed():
    for group, seeds in (("apps", (0, 300)), ("newapps", (1000,))):
        for seed in seeds:
            for t in generate_tasks(seed, 60, group, interface="tools"):
                said = re.sub(r"\b(am I in|could go to|autopay (on|off))\b", "x", t.instruction)  # endings that are fine
                assert not _DANGLING.search(said), (t.id, t.instruction)
                if t.clarification:
                    assert not _DANGLING.search(t.clarification), (t.id, t.clarification)
                names = [x["function"]["name"] for x in task_tools(t)]
                assert len(names) == len(set(names)), t.id
                for n in names:
                    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", n), n
                for x in t.app["tools"]:
                    ps = x["function"]["parameters"]
                    assert set(ps["required"]) <= set(ps["properties"]), t.id


def test_dates_and_grounding():
    today = date(2027, 1, 8)  # a Friday
    assert parse_date("tomorrow", today) == date(2027, 1, 9)
    assert parse_date("on Friday", today) == date(2027, 1, 15)
    assert parse_date("Jan 20", today) == parse_date("1/20", today) == parse_date("2027-01-20", today)
    assert same("date", "2027-01-20", "January 20th", today)
    k = Known(today)
    k.add_text("Find a pharmacy in Ithaca that's open tomorrow.")
    k.add_value("date", "2027-01-09")
    # the When2Call failures, here: a city nobody named, placeholder ids, today's date for a day nobody gave
    assert not grounded("string", "Berkeley", k)
    assert not grounded("id", "default", k) and not grounded("string", "current_user", k)
    assert not grounded("date", "2027-01-08", k)
    assert grounded("string", "Ithaca, NY", k) and grounded("date", "2027-01-09", k)
    assert grounded("date", "2027-01-08", k, default="today")  # unless it's the tool's documented default
