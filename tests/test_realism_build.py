"""The pack builder and the Enron reader, with a fake model and a tiny fake corpus (no network)."""
import io
import json
import tarfile
from types import SimpleNamespace

from alfred.realism import build as B
from alfred.realism.enron import categorize, mask, read, sample, write
from alfred.realism.kinds import KINDS
from alfred.realism.pack import load, problems, read_entries, resolve
from alfred.tasks import generate_tasks
from alfred.env import Environment
from alfred.agents.scripted import OracleAgent


class FakeLLM:
    """Writes a seed-pack template for the asked kind, with a fresh word so it isn't a duplicate. Its first try for
    every job breaks a rule (a year), so the retry-with-feedback path runs."""

    def __init__(self):
        entries, _ = read_entries(resolve("seed"))
        self.by_kind = {}
        for e in entries:
            self.by_kind.setdefault(e["kind"], []).append(e["fields"])
        self.calls, self.n = 0, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, **kw):
        self.calls += 1
        kind = messages[1]["content"].split("'")[1]
        fields = dict(self.by_kind[kind][0])
        if len(messages) == 2:
            key = next(iter(fields))
            fields[key] = fields[key] + " 2001"
        else:
            assert "doesn't pass these checks" in messages[-1]["content"] and "no years" in messages[-1]["content"]
        text = "<think>ok</think>Here you go:\n" + json.dumps(fields)
        usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=100)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=usage)


def test_builder_retries_with_feedback_validates_and_resumes(tmp_path):
    out = tmp_path / "pack.jsonl.gz"
    llm = FakeLLM()
    kinds = ["receipt", "noise_work", "event", "request:forward_receipt", "injection_text"]
    refs = [{"id": "r1", "category": "work", "subject": "hi", "body": "status update on the deal", "names": ["Tana"]},
            {"id": "r2", "category": "notification", "subject": "notice", "body": "your account was updated",
             "names": []}]
    done, text = B.build(llm, out, refs, kinds=kinds, per_kind=1, workers=2, log=lambda *_: None)
    assert len(done) == len(kinds) and llm.calls == 2 * len(kinds)
    for e in done:
        assert not problems(e["kind"], e["fields"]) and e["split"] in ("train", "eval")
    pack = load(str(out))
    assert len(pack) == len(kinds) and pack.dropped == 0 and pack.meta["model"] == B.DEFAULT_MODEL
    assert "Kept 5 of 5" in text and (tmp_path / "pack.build.md").exists()
    # Resume: nothing left to do, so no more calls.
    B.build(llm, out, refs, kinds=kinds, per_kind=1, workers=2, log=lambda *_: None)
    assert llm.calls == 2 * len(kinds)


def test_prompt_states_slots_and_rules():
    text = B.prompt(KINDS["shipped"], [], {"tone": "terse"}, None)
    assert "{tracking}" in text and "No weekday names" in text and "tone: terse" in text
    text = B.prompt(KINDS["noise_calendar"], [], {}, None)
    assert "No weekday names" not in text and "No clock times" not in text


def test_parse_takes_the_json_and_only_the_kind_fields():
    assert B.parse('noise {"subject": "a", "body": "b", "extra": 1} trailing', KINDS["noise_work"]) == \
           {"subject": "a", "body": "b"}
    assert B.parse("no json here", KINDS["noise_work"]) is None


def _fake_corpus(tmp_path):
    msgs = {
        "maildir/doe-j/inbox/1.": "From: ann.lee@enron.com\nX-From: Ann Lee\nX-To: John Doe\nSubject: RE: curve\n\n"
                                  "-----Original Message-----\nCan you send the curve? Call 713-555-0100 or "
                                  "ann.lee@enron.com. Thanks for the help on this one.",
        "maildir/doe-j/all_documents/1.": "From: ann.lee@enron.com\nX-From: Ann Lee\nSubject: RE: curve\n\n"
                                          "-----Original Message-----\nCan you send the curve? Call 713-555-0100 or "
                                          "ann.lee@enron.com. Thanks for the help on this one.",
        "maildir/doe-j/personal/2.": "From: pal@aol.com\nX-From: Sam Pal\nSubject: weekend\n\nhey are we still on for "
                                     "the lake this weekend? let me know soon so I can plan the food and the drive up.",
        "maildir/doe-j/sent/3.": "From: john.doe@enron.com\nSubject: mine\n\nthis is the user's own sent mail and "
                                 "should be skipped by the reader entirely.",
        "maildir/doe-j/inbox/4.": "From: no.reply@travel.com\nSubject: Your itinerary\n\nYour itinerary is attached. "
                                  "To unsubscribe from these messages, visit our site and change your settings.",
    }
    path = tmp_path / "enron.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, text in msgs.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_enron_reader_samples_dedupes_masks_and_skips_sent(tmp_path):
    rows, seen = sample(_fake_corpus(tmp_path), per_category=10)
    assert sum(seen.values()) == 3  # the copy in all_documents and the sent mail are skipped
    by = {r["category"]: r for r in rows}
    assert set(by) == {"thread", "personal", "notification"}
    assert "[phone]" in by["thread"]["body"] and "[address]" in by["thread"]["body"]
    assert "@" not in by["thread"]["body"] and {"Ann", "Lee", "John", "Doe"} <= set(by["thread"]["names"])
    write(rows, tmp_path / "refs.jsonl.gz")
    assert read(tmp_path / "refs.jsonl.gz") == rows
    assert mask("x a.b@c.com y") == "x [address] y"
    assert categorize("calendar", "", "", "x") == "calendar"


def test_a_built_pack_drives_realistic_worlds(tmp_path, monkeypatch):
    """A pack built for every kind loads, and the oracle stays clean on worlds that use it."""
    out = tmp_path / "full.jsonl.gz"
    B.build(FakeLLM(), out, [], per_kind=1, workers=4, log=lambda *_: None)
    pack = load(str(out))
    assert {e["kind"] for e in pack.entries} == set(KINDS)
    monkeypatch.setenv("ALFRED_PACK", str(out))
    tasks = generate_tasks(0, 2, "all", style="realistic") + generate_tasks(1000, 2, "all", style="realistic")
    for t in tasks:
        assert t.pack_id == pack.id
        env = Environment(t)
        OracleAgent().run(env)
        assert env.metrics()["clean_success"], t.id
