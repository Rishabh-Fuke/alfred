"""alfred.show prints email and app episodes in either interface, and pairs two runs task by task."""
import json

import pytest

from alfred import show
from alfred.agents.scripted import GuessAgent, OracleAgent
from alfred.env import make_env
from alfred.tasks import TEMPLATES, generate_tasks


def _trajectory(task, agent):
    env = make_env(task)
    agent.run(env)
    return env.trajectory()


@pytest.mark.parametrize("interface", ["tools", "text"])
def test_app_episodes_render(interface):
    for task in generate_tasks(1000, 10, "newapps", interface=interface):
        text = show.render(_trajectory(task, OracleAgent()))
        assert task.instruction in text and "clean_success=True" in text, task.id


def test_email_episodes_still_render():
    task = generate_tasks(0, 1, [next(iter(TEMPLATES))])[0]
    text = show.render(_trajectory(task, OracleAgent()))
    assert "Allowed to open:" in text and "clean_success=True" in text


def test_two_runs_pair_up_task_by_task(tmp_path):
    tasks = generate_tasks(1000, 4, ["app_ask"])
    for run, agent in (("a", GuessAgent()), ("b", OracleAgent())):
        folder = tmp_path / run / "trajectories" / "model" / "plain"
        folder.mkdir(parents=True)
        for t in tasks:
            (folder / f"{t.id}__s0.json").write_text(json.dumps(_trajectory(t, agent)))
    out = show.compare(tmp_path / "a", tmp_path / "b")
    assert f"total{' ' * 35} 0/{len(tasks)}  vs  {len(tasks)}/{len(tasks)}" in out
    assert all(t.instruction in out for t in tasks)
    assert "No episodes in common" in show.compare(tmp_path / "a", tmp_path / "b", task="nothing_like_this")
