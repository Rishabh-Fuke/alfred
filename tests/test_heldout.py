"""Held-out templates: solvable, graded right, kept out of training, and they don't disturb the base worlds."""
import pytest

from alfred.agents.scripted import (FalseClaimAgent, GuessAgent, InjectionFollowerAgent, InjectionRelayAgent,
                                     InjectionWarnAgent, LeakAgent, OracleAgent)
from alfred.env import Environment
from alfred.reward import compute_reward
from alfred.tasks import HELDOUT_TEMPLATES, TEMPLATES, generate_tasks, template_names
from alfred.world import EXTRAS, World

TASKS = generate_tasks(1000, 10, "heldout")


def run(agent_cls, task):
    env = Environment(task)
    agent_cls().run(env)
    return env.metrics()


def test_names():
    assert template_names("") == list(TEMPLATES)
    assert template_names("heldout") == list(HELDOUT_TEMPLATES)
    assert template_names("all") == list(TEMPLATES) + list(HELDOUT_TEMPLATES)
    assert not set(TEMPLATES) & set(HELDOUT_TEMPLATES)


def test_extras_leave_the_base_world_alone():
    for seed in (0, 1000, 1004):
        base = World.generate(seed).snapshot()
        full = World.generate(seed, tuple(EXTRAS)).snapshot()
        for table, rows in base.items():
            assert full[table][:len(rows)] == rows, (seed, table)
    # and the original tasks are exactly what they were
    assert [t.to_json() for t in generate_tasks(1000, 2)] == [t.to_json() for t in generate_tasks(1000, 2, "")]


def test_oracle_is_perfect_on_held_out_tasks():
    for t in TASKS:
        m = run(OracleAgent, t)
        assert m["clean_success"] and compute_reward(m) == 1.0, (t.id, m)
        assert m["asked"] == t.should_ask


def test_guessing_is_caught_on_new_kinds_of_ambiguity():
    for t in [t for t in TASKS if t.should_ask]:
        m = run(GuessAgent, t)
        assert m["guessed"] and not m["success"], t.id


def test_sharing_the_home_address_is_a_leak():
    for t in [t for t in TASKS if t.template == "third_party_request"]:
        m = run(LeakAgent, t)
        assert m["leaks"] and not m["clean_success"], t.id


def test_injection_in_a_calendar_invite():
    for t in [t for t in TASKS if t.template == "injection_event"]:
        assert not run(InjectionFollowerAgent, t)["success"]
        relay = run(InjectionRelayAgent, t)
        assert relay["injection_partial"] and not relay["success"]
        warn = run(InjectionWarnAgent, t)
        assert warn["injection_flagged"] and warn["clean_success"]


def test_false_claims_on_impossible_variants():
    for t in [t for t in TASKS if t.template == "impossible_variant"]:
        m = run(FalseClaimAgent, t)
        assert m["false_completion"] and not m["success"]
    variants = {t.params["variant"] for t in generate_tasks(1000, 40, ["impossible_variant"])}
    assert variants == {"text", "reorder", "reservation", "alarm", "cancel_delivery"}


def test_held_out_templates_cannot_become_training_data():
    from alfred.train import data
    with pytest.raises(ValueError):
        data.oracle_examples(range(0, 1), ["ambiguous_event"])
