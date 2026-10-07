"""Shared helpers for the outside benchmarks."""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}


def client_for(base_url: str, api_key: str | None = None, timeout: float = 600.0):
    """An OpenAI client for a URL or provider shortcut (see alfred.eval.PROVIDERS), plus the resolved URL."""
    from openai import OpenAI

    from ..eval import resolve_endpoint
    url, key = resolve_endpoint(base_url, api_key)
    return OpenAI(base_url=url, api_key=key, timeout=timeout, max_retries=3), url


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")


def bootstrap(values: list[float], n_boot: int = 1000, seed: int = 0) -> dict | None:
    """Mean with a 95% bootstrap interval."""
    if not values:
        return None
    rng = random.Random(seed)
    k = len(values)
    boots = sorted(sum(values[rng.randrange(k)] for _ in range(k)) / k for _ in range(n_boot))
    return {"value": sum(values) / k, "lo": boots[int(0.025 * n_boot)], "hi": boots[int(0.975 * n_boot) - 1], "n": k}


def pct(x: dict | None) -> str:
    return "n/a" if not x else f"{x['value']:.0%} [{x['lo']:.0%}-{x['hi']:.0%}]"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
