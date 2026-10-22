import json
import re

import pytest

from alfred import eval as alfred_eval
from alfred import viewer
from alfred.agents.scripted import GuessAgent, OracleAgent
from alfred.env import make_env
from alfred.tasks import TEMPLATES, generate_tasks


def _data(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="alfred-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def test_scripted_page_has_worlds_tasks_and_episodes(tmp_path):
    out = viewer.main(["--worlds", "1000", "--out", str(tmp_path / "x.html")])
    html = out.read_text()
    assert html.startswith("<!doctype html>") and "<title>Alfred Explorer</title>" in html
    data = _data(html)
    assert [w["seed"] for w in data["worlds"]] == [1000]
    assert len(data["worlds"][0]["tasks"]) == len(TEMPLATES)
    refs = [e for e in data["episodes"] if e["kind"] == "reference"]
    assert len(refs) == len(TEMPLATES) and all(e["metrics"]["clean_success"] for e in refs)
    assert any(e["kind"] == "misbehaving" and not e["metrics"]["clean_success"] for e in data["episodes"])


def test_fragment_leaves_out_the_document_wrapper(tmp_path):
    html = viewer.main(["--worlds", "1000", "--fragment", "--out", str(tmp_path / "x.html")]).read_text()
    assert html.startswith("<title>") and "<html" not in html and "<body" not in html


def test_page_from_an_eval_run(tmp_path):
    run = alfred_eval.main(["--agent", "oracle", "--worlds", "1", "--out", str(tmp_path)])
    data = _data(viewer.main([str(run)]).read_text())
    assert (run / "explorer.html").exists()
    assert len(data["episodes"]) == len(TEMPLATES) and {e["kind"] for e in data["episodes"]} == {"reference"}


@pytest.mark.parametrize("templates,interface", [("newapps", "tools"), ("newapps", "text"), ("newapps4", "text")])
def test_page_from_app_episodes(tmp_path, templates, interface):
    tasks = generate_tasks(1000, 2, templates, interface=interface)
    for model, agent in (("careful", OracleAgent()), ("guesser", GuessAgent())):
        folder = tmp_path / "run" / "trajectories" / model / "plain"
        folder.mkdir(parents=True)
        for t in tasks:
            env = make_env(t)
            agent.run(env)
            (folder / f"{t.id}__s0.json").write_text(json.dumps(env.trajectory()))
    data = _data(viewer.main([str(tmp_path / "run")]).read_text())

    assert [w["seed"] for w in data["worlds"]] == [1000, 1001]
    assert not any("emails" in w for w in data["worlds"])  # app tasks have no inbox to show
    shown = {t["id"]: t for w in data["worlds"] for t in w["tasks"]}
    assert set(shown) == {t.id for t in tasks}
    assert all(t["app"]["tools"] and t["app"]["turns"] for t in shown.values())
    assert data["initial"]["key"] in {t["key"] for t in data["worlds"][0]["tasks"]}

    eps = data["episodes"]
    assert len(eps) == 2 * len(tasks) and all(e["app"] for e in eps)
    assert all(e["metrics"]["clean_success"] for e in eps if e["label"] == "careful")
    steps = [s for e in eps for s in e["steps"]]
    assert {"read", "draft"} <= {s.get("tier") for s in steps}  # app tools take their tier from the app
    verdicts = [s["verdict"] for s in steps if "verdict" in s]
    assert any(v["status"] == "approved" and v["result"] for v in verdicts)
    assert any(v["status"] == "rejected" and v["classification"] == "made_up" for v in verdicts)
    assert any("made_up" in (s.get("checks") or {}).values() for s in steps)
    assert any(s.get("items") for s in steps)  # what a search returned
    replies = [s for s in steps if s.get("kind") == "reply"]
    if interface == "text":
        assert {"Asked", "Final reply"} <= {s["label"] for s in replies}
        if templates == "newapps4":  # a conversation: the user's next request comes back after a reply
            assert any(s["label"] == "Replied" and s["next_turn"] for s in replies)
            assert any(len(t["app"]["turns"]) > 1 for t in shown.values())
    else:
        assert not replies and any(s.get("finish") for s in steps)


def test_embedded_data_cannot_close_the_script_tag():
    data = viewer.build_data([1000], [], "test")
    data["worlds"][0]["emails"][0]["body"] = "</script><script>alert(1)</script>"
    html = viewer.render_html(data)
    assert "</script><script>alert(1)" not in html
    assert _data(html)["worlds"][0]["emails"][0]["body"].startswith("</script>")


def test_text_mode(capsys):
    viewer.main(["--worlds", "1000", "--text"])
    out = capsys.readouterr().out
    assert "--- Inbox" in out and "--- Requests" in out and "success:" in out
