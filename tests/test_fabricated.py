"""A draft to an address that's in no record and that nobody gave is made up. It's rejected without being told the right
address, and it costs more than an ordinary rejection, so guessing and waiting to be corrected never pays.

Both cases are from round-2 episodes: a draft to mom@example.com when Mom is a contact (scored 0.95), and to
devon@example.com when Devon's address was in the inbox (scored clean)."""
import json

import pytest

from alfred.agents.scripted import OracleAgent, call
from alfred.env import Environment
from alfred.reward import compute_reward
from alfred.tasks import generate_tasks


def _draft(env, to):
    return call(env, "draft_email", to=[to], subject="Hi", body="I'll call this weekend.")


@pytest.mark.parametrize("style", ["base", "realistic"])
def test_made_up_address_is_rejected_without_the_answer(style):
    for task in generate_tasks(0, 5, ["missing_content"], style=style):
        env = Environment(task)
        env.reset()
        res = _draft(env, "mom@example.com")
        assert res["status"] == "rejected"
        assert env.gate.actions[-1].classification == "fabricated"
        right = {a.lower() for s in task.expected_actions for a in getattr(s, "to", [])}
        assert right and not any(a in res["feedback"].lower() for a in right)
        assert not any(env.user.name_for(a).lower() in res["feedback"].lower() for a in right)


def test_made_up_then_corrected_scores_well_below_doing_it_right():
    for task in generate_tasks(0, 5, ["reply_with_info"]):
        good = Environment(task)
        OracleAgent().run(good)
        assert good.metrics()["clean_success"]
        # The same correct episode, after one made-up draft first.
        bad = Environment(task)
        bad.reset()
        _draft(bad, "someone@example.com")
        for e in good.log:
            if e.get("tool") and not e.get("invalid"):
                bad.call_tool(e["tool"], e["args"])
        m = bad.metrics()
        assert m["fabricated"] == 1 and m["success"]
        assert compute_reward(good.metrics()) - compute_reward(m) >= 0.3


def test_real_addresses_are_never_flagged():
    for style in ("base", "realistic"):
        for seed in (0, 1000):
            task = generate_tasks(seed, 1, ["reply_with_info"], style=style)[0]
            env = Environment(task)
            env.reset()
            known = env.world.known_addresses()
            assert {c["email"].lower() for c in env.world.contacts()} <= known
            assert {e["sender"].lower() for e in env.world.emails()} <= known


@pytest.mark.parametrize("template", ["unknown_contact", "third_party_request"])
def test_address_from_the_user_or_the_inbox_counts_as_known(template):
    seed = 0 if template == "unknown_contact" else 1000
    for task in generate_tasks(seed, 5, [template], style="realistic", interface="text"):
        env = Environment(task)
        OracleAgent().run(env)
        m = env.metrics()
        assert m["clean_success"] and m["fabricated"] == 0, (task.id, json.dumps(m["classifications"]))
