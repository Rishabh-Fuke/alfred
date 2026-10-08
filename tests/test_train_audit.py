"""The reward-hacking audit flags a shortcut that grows during RL, and stays quiet when nothing does."""
import gzip
import json

from alfred.agents.scripted import OracleAgent, call
from alfred.env import Environment
from alfred.reward import compute_reward
from alfred.tasks import generate_tasks
from alfred.train.audit import audit
from alfred.train.data import conversation_from_env


class AlwaysAsks(OracleAgent):
    """Correct, but asks a question first on every task: the over-asking shortcut."""

    def before(self, env):
        call(env, "ask_user", question="Before I start, is there anything I should know?")


def write_steps(run, agents_by_step):
    (run / "episodes").mkdir(parents=True)
    tasks = generate_tasks(0, 2)
    for step, agent in agents_by_step.items():
        rows = []
        for t in tasks:
            env = Environment(t)
            agent().run(env)
            m = env.metrics()
            rows.append({"task_id": t.id, "template": t.template, "category": t.category, "reward": compute_reward(m),
                         "metrics": m, "messages": conversation_from_env(env)})
        with gzip.open(run / "episodes" / f"step-{step:05d}.jsonl.gz", "wt") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))


def test_flags_over_asking_that_grew(tmp_path):
    write_steps(tmp_path, {1: OracleAgent, 2: OracleAgent, 3: AlwaysAsks, 4: AlwaysAsks})
    text, flagged = audit(tmp_path, window=2)
    assert set(flagged) == {"over_ask"}
    assert "Asked when the request was clear: 0% to 100%" in text
    assert "anything I should know" in text  # quotes the question it kept asking


def test_quiet_when_nothing_changes(tmp_path):
    write_steps(tmp_path, {1: OracleAgent, 2: OracleAgent})
    text, flagged = audit(tmp_path, window=1)
    assert flagged == {} and "No shortcut grew" in text
