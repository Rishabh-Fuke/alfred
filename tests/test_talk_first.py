"""Training tasks where the right first move is to talk: ask for what only the user knows, or decline."""
import pytest

from alfred.agents.scripted import GuessAgent, OracleAgent, _out_of_scope, call
from alfred.env import Environment
from alfred.reward import compute_reward
from alfred.tasks import HELDOUT_TEMPLATES, TEMPLATES, generate_tasks

TALK_FIRST = ["missing_content", "unknown_contact", "out_of_scope"]


def test_they_are_training_templates_and_distinct_from_held_out_ones():
    assert set(TALK_FIRST) <= set(TEMPLATES) and not set(TALK_FIRST) & set(HELDOUT_TEMPLATES)
    asks = [t for t in generate_tasks(0, 1) if t.should_ask]
    declines = [t for t in generate_tasks(0, 1) if t.expected_status == "cannot_do"]
    assert len(asks) == 3 and len(declines) == 2  # asking or declining is right on 5 of 12 training types


@pytest.mark.parametrize("interface", ["tools", "text"])
@pytest.mark.parametrize("template", ["missing_content", "unknown_contact"])
def test_asking_succeeds_and_guessing_fails(template, interface):
    for task in generate_tasks(0, 6, [template], interface=interface):
        good, guess = Environment(task), Environment(task)
        OracleAgent().run(good)
        GuessAgent().run(guess)
        g, b = good.metrics(), guess.metrics()
        assert g["clean_success"] and g["asked_before_draft"], task.id
        assert not b["success"] and b["guessed"], task.id
        assert compute_reward(g) > compute_reward(b)


def test_unknown_contact_is_in_no_record():
    for task in generate_tasks(0, 10, ["unknown_contact"], style="realistic"):
        env = Environment(task)
        env.reset()
        name = task.params["name"]
        assert not env.world.search_contacts(name) and not env.world.search_emails(name, "all")


@pytest.mark.parametrize("interface", ["tools", "text"])
def test_reaching_for_tools_on_an_out_of_scope_request_costs_reward(interface):
    for task in generate_tasks(0, 6, ["out_of_scope"], interface=interface):
        direct, reaching = Environment(task), Environment(task)
        OracleAgent().run(direct)
        reaching.reset()
        call(reaching, "search_emails", query="weather")
        call(reaching, "search_calendar", query="")
        _out_of_scope(OracleAgent(), reaching, task.params)  # then the same decline
        d, r = direct.metrics(), reaching.metrics()
        assert d["needless_calls"] == 0 and r["needless_calls"] == 2
        assert d["success"] and r["success"] and compute_reward(d) > compute_reward(r)
