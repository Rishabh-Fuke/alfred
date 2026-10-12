import json
import re

from alfred import eval as alfred_eval
from alfred import viewer
from alfred.tasks import TEMPLATES


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
