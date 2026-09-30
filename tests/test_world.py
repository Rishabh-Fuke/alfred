from datetime import timedelta

from alfred.tasks import generate_tasks
from alfred.world import World, parse_dt


def test_same_seed_builds_the_same_world():
    assert World.generate(7).snapshot() == World.generate(7).snapshot()


def test_different_seeds_build_different_worlds():
    assert World.generate(7).snapshot() != World.generate(8).snapshot()


def test_tasks_are_deterministic():
    assert [t.to_json() for t in generate_tasks(1000, 3)] == [t.to_json() for t in generate_tasks(1000, 3)]


def test_hooks_are_consistent_across_many_worlds():
    for seed in range(60):
        w = World.generate(seed)
        h = w.hooks
        people = h["people"]
        assert people["dup_work"]["first"] == people["dup_friend"]["first"]
        assert people["dup_work"]["email"] != people["dup_friend"]["email"]
        assert h["injection"]["attacker"] not in {c["email"] for c in w.contacts()}

        # The slot the meeting moves to is free, and the hook is the only meeting with that person that day.
        rs = h["reschedule"]
        ev = rs["event"]
        shift = timedelta(days=rs["new_d"] - rs["orig_d"])
        new_start, new_end = parse_dt(ev["start_at"]) + shift, parse_dt(ev["end_at"]) + shift
        assert all(parse_dt(e["end_at"]) <= new_start or parse_dt(e["start_at"]) >= new_end for e in w.events())
        same_day = [e["id"] for e in w.events()
                    if e["start_at"][:10] == ev["start_at"][:10] and rs["person"]["email"] in e["attendees"]]
        assert same_day == [ev["id"]]

        # The requested new-meeting slot is free.
        start = parse_dt(h["schedule"]["start_at"])
        end = start + timedelta(minutes=h["schedule"]["minutes"])
        assert all(parse_dt(e["end_at"]) <= start or parse_dt(e["start_at"]) >= end for e in w.events())


def test_search_ignores_filler_words_and_possessives():
    w = World.generate(1000)
    h = w.hooks["reply"]
    hits = w.search_emails(f"email from {h['person']['first']}'s about the {h['topic']}")
    assert h["email"]["id"] in [e["id"] for e in hits]


def test_every_sensitive_record_has_canaries():
    w = World.generate(1000)
    canaries = w.canaries()
    assert set(canaries) == w.sensitive_ids()
    assert all(canaries.values())
