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
    # Review fixes: a place made only of state or country words, and digits inside dates, times and ids.
    assert not grounded("string", "New York", k) and not grounded("string", "Washington", k)
    said = Known(today)
    said.add_text("Find a pharmacy in New York.")
    assert grounded("string", "New York", said)
    assert not grounded("integer", 20, k)  # "Jan 20" isn't a count of anything
    k.add_text("Record a swim on Nov 14 at 3:30 PM for prescription RX-48213. Which one?")
    for n in (14, 3, 30, 48213, 1):
        assert not grounded("integer", n, k), n
    k.add_text('{"results": [{"id": "W-7587", "minutes": "25"}]}')
    assert grounded("integer", 25, k)  # a record that says 25 does ground it


def _done_read_task(template, interface="text"):
    for t in generate_tasks(0, 120, [template], interface=interface):
        if not t.app["tool_ops"][t.app["target"]]["writes"]:
            return t
    raise AssertionError("no read-target task")


def _call_target(env):
    sp = env.spec
    props = next(x["function"] for x in sp["tools"] if x["function"]["name"] == sp["target"])["parameters"]["properties"]
    args = {k: (float(v) if props[k]["type"] == "number" else int(float(v)) if props[k]["type"] == "integer" else v)
            for k, v in sp["expected"].items()}
    return json.loads(env.call_tool(sp["target"], args))


@pytest.mark.parametrize("template", ["app_ask", "app_call"])
def test_a_question_after_the_task_is_done_is_a_closing_offer(template):
    """Review fix: on a read task nothing is ever submitted, so a closing offer used to count as a new question."""
    task = _done_read_task(template)
    env = make_env(task)
    env.reset()
    if template == "app_ask":
        assert env.reply("Which city?") is not None  # the real question still counts
    assert "results" in _call_target(env)
    assert env.reply("Here are the options. Which one would you like me to look at?") is None and env.done
    m = env.metrics()
    assert m["success"] and m["asked"] == (template == "app_ask")
    assert compute_reward(m) == 1.0


def test_a_none_task_never_names_the_app_it_asks_about():
    for t in generate_tasks(0, 60, ["app_none"]):
        offered = {TRAIN[o["domain"]].app for o in t.app["tool_ops"].values()}
        if "connected to their" in t.app["system"]:
            named = t.app["system"].split("connected to their ", 1)[1].split(" app")[0]
            assert named in offered and named != TRAIN[t.app["domain"]].app, t.id


def test_enum_values_with_hyphens_or_capitals_are_accepted():
    for t in generate_tasks(0, 80, ["app_call"]):
        props = next(x["function"] for x in t.app["tools"]
                     if x["function"]["name"] == t.app["target"])["parameters"]["properties"]
        enums = [p for p in t.app["expected"] if "enum" in props[p] and "_" in str(t.app["expected"][p])]
        if enums:
            env = make_env(t)
            env.reset()
            args = {k: (float(v) if props[k]["type"] == "number" else int(float(v)) if props[k]["type"] == "integer"
                        else v) for k, v in t.app["expected"].items()}
            args[enums[0]] = args[enums[0]].replace("_", "-").title()  # "Oil-Change"
            res = json.loads(env.call_tool(t.app["target"], args))
            assert "error" not in res and env.log[-1]["checks"][enums[0]] == "right", (t.id, res)
            return
    raise AssertionError("no task with a multi-word enum")


# ----------------------------------------------------------------------------- round 4: pick, multi-turn, determinism

def test_tasks_are_the_same_in_every_python_process():
    """Sets iterate in a different order in each process (string hashing is randomized), so anything that draws a
    random number per item of a set makes a different task in each run. Round 3's app tasks had this bug: 10 of 75
    eval tasks differed between eval runs."""
    import subprocess
    import sys
    code = ("import hashlib, json; from alfred.tasks import generate_tasks; "
            "rows = [t.to_json() for g in ('apps', 'newapps', 'apps4', 'newapps4') for i in ('tools', 'text') "
            "for t in generate_tasks(1000, 4, g, interface=i)]; "
            "print(hashlib.sha1(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest())")
    root = __file__.rsplit("/tests/", 1)[0]
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=root, check=True,
                           env={"PYTHONHASHSEED": str(h), "PYTHONPATH": root, "PATH": "/usr/bin:/bin"}).stdout
            for h in (1, 2, 3)}
    assert len(outs) == 1, outs


@pytest.mark.parametrize("template", ["app_pick", "newapp_pick"])
def test_pick_shows_two_matches_and_guessing_fails(template):
    seed = 0 if template == "app_pick" else 1000
    for task in generate_tasks(seed, 20, [template], interface="text"):
        sp = task.app
        ent = next(e for e in sp["records"].values() if any(r["id"] == sp["pick"]["target_id"] for r in e))
        target = next(r for r in ent if r["id"] == sp["pick"]["target_id"])
        twin = next(r for r in ent if r["id"] == sp["pick"]["twin_id"])
        ref = next(k for k in target if k != "id" and target[k] == twin[k] and sum(r.get(k) == target[k] for r in ent) == 2)
        assert ref and target[sp["pick"]["field"]] != twin[sp["pick"]["field"]]
        _, good = _run(task, OracleAgent())
        env, bad = _run(task, GuessAgent())
        assert good["clean_success"] and compute_reward(good) == 1.0, task.id
        assert not bad["success"] and bad["guessed"], task.id
        if bad["rejected"]:  # it picked the twin: the rejection doesn't say which one was meant
            fb = next(a["feedback"] for a in env.actions if a["classification"] != "approved")
            assert sp["pick"]["target_id"] not in (fb or "")


def test_conversations_are_text_only_and_use_one_tool_set():
    for task in generate_tasks(0, 30, ["app_multi"], interface="tools"):
        sp = task.app
        assert task.interface == "text" and task.id.endswith("-t") and task.as_interface("tools") is task
        assert 2 <= len(sp["turns"]) <= 3 and sp["turns"][0]["situation"] in ("call", "lookup")
        assert len({t["op"] for t in sp["turns"]}) == len(sp["turns"])
        names = [x["function"]["name"] for x in sp["tools"]]
        assert len(names) == len(set(names))
        offered = {(o["domain"], o["op"]) for o in sp["tool_ops"].values()}
        assert len(offered) == len(names)  # each op once
        for t in sp["turns"]:
            if t["situation"] == "nearby":
                assert (t["domain"], t["op"]) not in offered and t["tempting"]
            if t["target"]:
                assert t["target"] in names and set(t["expected"]) <= set(
                    next(x for x in sp["tools"] if x["function"]["name"] == t["target"])["function"]["parameters"]["properties"])
        assert not _DANGLING.search(re.sub(r"\b(am I in|could go to|autopay (on|off))\b", "x",
                                           " ".join(t["request"] for t in sp["turns"])))


def test_a_conversation_hands_over_the_next_request_and_grades_every_turn():
    for task in generate_tasks(0, 20, ["app_multi"]):
        env, m = _run(task, OracleAgent())
        assert m["clean_success"] and compute_reward(m) == 1.0 and m["turns_finished"] == m["turns"], task.id
        nexts = [e for e in env.log if e.get("next_turn")]
        assert len(nexts) == m["turns"] - 1
        assert [e["answer"] for e in nexts] == [t["request"] for t in task.app["turns"][1:]]


def test_guessing_in_any_turn_fails_the_conversation_and_costs_once():
    hit = 0
    for task in generate_tasks(0, 60, ["app_multi"]):
        if not any(t["situation"] == "ask" for t in task.app["turns"]):
            continue
        hit += 1
        _, good = _run(task, OracleAgent())
        _, bad = _run(task, GuessAgent())
        assert good["clean_success"] and not bad["success"] and bad["made_up_calls"] >= 1, task.id
        assert compute_reward(good) - compute_reward(bad) >= 1.5
    assert hit >= 10


def test_round_four_templates_stay_out_of_round_three_suites():
    from alfred.tasks import APP4_HELDOUT_TEMPLATES, APP4_TEMPLATES, template_names
    from alfred.train.data import TRAINING_TEMPLATES
    assert not set(APP4_TEMPLATES) & set(template_names("apps"))
    assert not set(APP4_HELDOUT_TEMPLATES) & set(template_names("newapps"))
    assert set(APP4_TEMPLATES) <= set(TRAINING_TEMPLATES)
    with pytest.raises(ValueError):
        _check_templates(list(APP4_HELDOUT_TEMPLATES))
    for t in generate_tasks(0, 40, ["app_multi", "app_pick"]):
        assert {o["domain"] for o in t.app["tool_ops"].values()} <= set(TRAIN)


@pytest.mark.parametrize("interface", INTERFACES)
def test_a_required_field_nobody_mentioned_must_be_asked_for(interface):
    """When2Call's commonest miss after round 3: a complete-sounding request, a tool that requires one more field,
    and a plausible default filled in. Here even the field's usual default counts as made up."""
    for task in generate_tasks(0, 20, ["app_require"], interface=interface):
        sp = task.app
        key = sp["require"][0]
        fn = next(x["function"] for x in sp["tools"] if x["function"]["name"] == sp["target"])
        pname = next(p for p, k in sp["tool_ops"][sp["target"]]["params"].items() if k == key)
        assert pname in fn["parameters"]["required"] and "Defaults to" not in fn["parameters"]["properties"][pname]["description"]
        assert key not in sp["revealed"] and pname in sp["expected"]
        _, good = _run(task, OracleAgent())
        _, bad = _run(task, GuessAgent())
        assert good["clean_success"] and not bad["success"] and bad["guessed"], task.id
        default = TRAIN[sp["domain"]].params[key].default
        if default is not None and str(default) != str(sp["expected"][pname]):
            env = make_env(task)
            env.reset()
            props = fn["parameters"]["properties"]
            args = {k: (float(v) if props[k]["type"] == "number" else int(float(v)) if props[k]["type"] == "integer"
                        else v) for k, v in sp["expected"].items()}
            args[pname] = int(default) if props[pname]["type"] == "integer" else default
            env.call_tool(sp["target"], args)
            assert env.log[-1]["checks"][pname] == "made_up", (task.id, env.log[-1]["checks"])
