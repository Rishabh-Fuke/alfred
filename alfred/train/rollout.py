"""Play episodes with a model behind an OpenAI-compatible server, many at once, and score them.

The same agent loop as evaluation (agents/llm.py) in native tool-calling mode with reasoning off, so what RL
optimizes is exactly what gets evaluated and deployed.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..agents.llm import OpenAICompatAgent
from ..env import make_env
from ..reward import compute_reward
from ..tasks import Task


@dataclass
class Episode:
    task: Task
    sample: int
    messages: list = field(default_factory=list)
    metrics: dict | None = None
    reward: float = 0.0
    error: str | None = None
    stats: dict = field(default_factory=dict)
    seconds: float = 0.0
    advantage: float = 0.0
    refill: bool = False  # played to replace a group with no signal (GRPO dynamic sampling)

    @property
    def ok(self) -> bool:
        return self.error is None and self.metrics is not None

    def record(self) -> dict:
        return {"task_id": self.task.id, "template": self.task.template, "category": self.task.category,
                "sample": self.sample, "reward": self.reward, "advantage": self.advantage, "error": self.error,
                "metrics": self.metrics, "stats": self.stats, "seconds": self.seconds, "messages": self.messages,
                "refill": self.refill}


def make_client(base_url: str, timeout: float = 300.0):
    from openai import OpenAI
    return OpenAI(base_url=base_url, api_key="local", timeout=timeout, max_retries=2)


def play(task: Task, model: str, client, sample: int = 0, temperature: float = 0.9, top_p: float = 1.0,
         max_tokens: int = 2048, max_steps: int = 12, reward_weights: dict | None = None) -> Episode:
    env = make_env(task, prompt="plain", max_steps=max_steps, tool_mode="native")
    agent = OpenAICompatAgent(model, client=client, temperature=temperature, max_tokens=max_tokens,
                              tool_mode="native",
                              extra_body={"top_p": top_p, "chat_template_kwargs": {"enable_thinking": False}})
    ep = Episode(task, sample)
    t0 = time.perf_counter()
    try:
        ep.stats = agent.run(env) or {}
        ep.metrics = env.metrics()
        ep.reward = compute_reward(ep.metrics, reward_weights)
    except Exception as e:  # noqa: BLE001 - a server hiccup drops the episode, it doesn't stop training
        ep.error = f"{type(e).__name__}: {e}"[:300]
    ep.messages = agent.messages
    ep.seconds = round(time.perf_counter() - t0, 2)
    return ep


def play_many(jobs: list[tuple[Task, int]], model: str, client, workers: int = 32, **kw) -> list[Episode]:
    """Episodes for (task, sample) pairs, in the same order."""
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(play, task, model, client, sample, **kw) for task, sample in jobs]
        return [f.result() for f in futures]
