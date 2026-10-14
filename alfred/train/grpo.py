"""Reinforcement learning with verifiable rewards: group-relative policy optimization on Alfred.

    python -m alfred.train.serve start --model nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 \\
        --served-name nemotron-3-nano-4b --lora --gpu-memory-utilization 0.45 --nemotron-parser nano_v3_reasoning_parser.py
    python -m alfred.train.grpo --model nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 --init-adapter out/sft/adapter \\
        --out out/grpo

Each step:
1. Sample --tasks training tasks (worlds 0-949). Templates are weighted toward the ones the policy gets right only
   some of the time, since a task it always or never solves teaches nothing.
2. Play --group episodes of each with the current policy, served by vLLM (sampling at --temperature).
3. Score every episode with the environment's reward (alfred.reward). An episode's advantage is its reward minus
   the mean reward of its group: it was better or worse than the policy's other attempts at the same task.
   Advantages are then scaled by their standard deviation across the whole batch, not per group, so a group
   whose rewards differ only by a small formatting penalty gets a small push, not a full one.
4. One gradient step: raise the log-probability of every assistant token in an episode in proportion to its
   advantage (lower it if negative), averaged over all tokens in the batch. With one step per batch the policy
   that played the episodes is the one being updated, so the importance ratio is exactly 1 and PPO-style
   clipping has nothing to do.
5. Save the adapter and load it into vLLM under a new name; the next step plays with it.

An optional KL penalty (--kl) keeps the policy near the SFT model it started from. Validation episodes on worlds
950-999 run every --val-every steps; the best adapter by validation reward is kept in out/best.

Everything needed to audit reward hacking is logged: per-step rates of asking when it didn't need to, declining
possible tasks, hedged answers, false "done", injections, and every episode's full conversation
(out/episodes/step-NNNNN.jsonl.gz).
"""
from __future__ import annotations

import argparse
import gzip
import json
import random
import shutil
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import torch

from ..env import task_tools
from ..tasks import EVAL_ONLY_TEMPLATES, TEMPLATES, TRAIN_WORLDS, VALIDATION_WORLDS, generate_tasks
from .data import text_interface_for
from . import serve
from .model import (Schedule, load_checkpoint, load_lora, load_model, load_tokenizer, save_checkpoint, token_logprobs,
                    trainable_parameters)
from .render import TemplateMismatch, render
from .rollout import Episode, make_client, play_many


def validation_tasks(n_worlds: int, realistic_share: float, pack: str = "", templates=None,
                     text_share: float = 0.0) -> list:
    """Validation in the same mix as training: the first worlds base, the rest realistic (with eval-split text),
    and text_share of the tasks in the text interface."""
    n_real = round(n_worlds * realistic_share)
    start = VALIDATION_WORLDS.start
    tasks = (generate_tasks(start, n_worlds - n_real, templates) if n_worlds - n_real else []) + \
        (generate_tasks(start + n_worlds - n_real, n_real, templates, style="realistic", pack=pack) if n_real else [])
    return [t.as_interface("text") if text_interface_for(t.id, text_share) else t for t in tasks]


class TaskSampler:
    """Draws training tasks, favoring templates the policy solves only sometimes."""

    def __init__(self, templates: list[str], worlds: range, seed: int = 0, floor: float = 0.1,
                 realistic_share: float = 0.0, pack: str = "", text_share: float = 0.0):
        self.templates, self.worlds, self.floor = list(templates), worlds, floor
        self.realistic_share, self.pack, self.text_share = realistic_share, pack, text_share
        self.rng = random.Random(seed)
        self.solve_rate = {t: 0.5 for t in self.templates}

    def weights(self) -> dict[str, float]:
        return {t: self.floor + p * (1 - p) for t, p in self.solve_rate.items()}

    def sample(self, n: int, avoid: set | None = None) -> list:
        """n tasks, none of them a (world, template) pair in avoid."""
        w = self.weights()
        picks = self.rng.choices(self.templates, [w[t] for t in self.templates], k=n)
        tasks, seen = [], set(avoid or ())
        for tpl in picks:
            for _ in range(1000):
                seed = self.rng.choice(self.worlds)
                if (seed, tpl) not in seen:
                    seen.add((seed, tpl))
                    break
            else:
                continue  # every world of this template is already in the step
            style = "realistic" if self.rng.random() < self.realistic_share else "base"
            interface = "text" if self.rng.random() < self.text_share else "tools"
            tasks.append(generate_tasks(seed, 1, [tpl], style=style, pack=self.pack, interface=interface)[0])
        return tasks

    def update(self, template: str, solve_rate: float, momentum: float = 0.7) -> None:
        self.solve_rate[template] = momentum * self.solve_rate[template] + (1 - momentum) * solve_rate

    def state_dict(self) -> dict:
        return {"solve_rate": self.solve_rate, "rng": self.rng.getstate()}

    def load_state_dict(self, d: dict) -> None:
        self.solve_rate = d["solve_rate"]
        state = d["rng"]
        self.rng.setstate((state[0], tuple(state[1]), state[2]))


def without_signal(tasks: list, episodes: list[Episode]) -> list:
    """Tasks whose attempts all got the same reward. GRPO learns only from differences within a group, so these
    teach nothing (round 2: 6.5 of 8 groups per step on average, after SFT had already reached 0.95)."""
    return [t for t in tasks if len({e.reward for e in episodes if e.ok and e.task.id == t.id}) <= 1]


def advantages(episodes: list[Episode], norm: str = "batch") -> list[Episode]:
    """Set each episode's advantage: reward minus its group's mean, scaled (see the module docstring)."""
    groups = defaultdict(list)
    for ep in episodes:
        if ep.ok:
            groups[ep.task.id].append(ep)
    raw = []
    for eps in groups.values():
        if len(eps) < 2:
            continue
        rewards = [e.reward for e in eps]
        mean = sum(rewards) / len(rewards)
        sd = statistics.pstdev(rewards)
        for e in eps:
            e.advantage = e.reward - mean
            if norm == "group":
                e.advantage = e.advantage / sd if sd > 1e-6 else 0.0
        raw += [e.advantage for e in eps if abs(e.advantage) > 1e-9]
    if norm == "batch" and len(raw) > 1:
        scale = statistics.pstdev(raw + [-x for x in raw])  # spread around zero
        if scale > 1e-6:
            for eps in groups.values():
                for e in eps:
                    e.advantage /= scale
    return episodes


def conversation(ep: Episode) -> list[dict]:
    """The episode's messages through its last assistant turn (the result of the final call is never seen)."""
    msgs = ep.messages
    last = max((i for i, m in enumerate(msgs) if m["role"] == "assistant"), default=-1)
    return msgs[:last + 1]


def batch_stats(episodes: list[Episode]) -> dict:
    """Rates worth watching for reward hacking, next to the reward itself."""
    ok = [e for e in episodes if e.ok]
    if not ok:
        return {"episodes": len(episodes), "errors": len(episodes)}
    m = [e.metrics for e in ok]

    def rate(xs):
        xs = list(xs)
        return round(sum(map(bool, xs)) / len(xs), 4) if xs else None

    by_cat = defaultdict(list)
    for x in m:
        by_cat[x["category"]].append(x["success"])
    return {
        "episodes": len(episodes), "errors": len(episodes) - len(ok),
        "reward": round(sum(e.reward for e in ok) / len(ok), 4),
        "success": rate(x["success"] for x in m), "clean_success": rate(x["clean_success"] for x in m),
        **{f"success_{c}": rate(v) for c, v in sorted(by_cat.items())},
        "over_ask": rate(x["asked"] for x in m if not x["should_ask"]),
        "asked_when_ambiguous": rate(x["asked_before_draft"] for x in m if x["should_ask"]),
        "false_decline": rate(x.get("false_decline") for x in m if x["category"] != "impossible"),
        "false_completion": rate(x["false_completion"] for x in m),
        "injection_acted": rate(x["injection_followed"] or x["injection_partial"] for x in m
                                if x["category"] == "injection"),
        "answer_hedged": rate(x.get("answer_hedged") for x in m),
        "truncated": rate(x["truncated"] for x in m),
        "format_issues": round(sum(x["format_issues"] for x in m) / len(m), 4),
        "calls": round(sum(x["calls"] for x in m) / len(m), 3),
        "finish_chars": round(sum(len(_finish_message(e)) for e in ok) / len(ok), 1),
    }


def _finish_message(ep: Episode) -> str:
    if ep.task.interface == "text":  # the final plain reply
        return next((m.get("content") or "" for m in reversed(ep.messages)
                     if m.get("role") == "assistant" and not m.get("tool_calls")), "")
    for msg in reversed(ep.messages):
        for tc in msg.get("tool_calls") or []:
            if tc["function"]["name"] == "finish":
                try:
                    return json.loads(tc["function"]["arguments"]).get("message", "") or ""
                except (json.JSONDecodeError, TypeError, AttributeError):
                    return ""
    return ""


def save_episodes(path: Path, episodes: list[Episode]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as f:
        for ep in episodes:
            f.write(json.dumps(ep.record(), ensure_ascii=False, default=str) + "\n")


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.train.grpo", description="GRPO on Alfred.")
    p.add_argument("--model", required=True, help="Base model, the same one vLLM serves.")
    p.add_argument("--init-adapter", required=True, help="Adapter to start from (the SFT adapter).")
    p.add_argument("--out", required=True)
    p.add_argument("--vllm", default=serve.base_url(8000), help="vLLM server URL (started with --lora).")
    p.add_argument("--steps", type=int, default=100)
    p.add_argument("--tasks", type=int, default=8, help="Tasks per step.")
    p.add_argument("--group", type=int, default=8, help="Episodes per task.")
    p.add_argument("--refill-rounds", type=int, default=0,
                   help="Dynamic sampling: up to this many times per step, replace groups whose attempts all got the "
                        "same reward with fresh tasks, until --tasks groups carry signal (0: off, as in round 2).")
    p.add_argument("--templates", default=",".join(TEMPLATES))
    p.add_argument("--train-worlds", default=f"{TRAIN_WORLDS.start}-{TRAIN_WORLDS.stop - 1}",
                   help="Range of training worlds to sample tasks from (inside 0-949).")
    p.add_argument("--realistic-share", type=float, default=0.7,
                   help="Share of training tasks in realistic worlds (the rest use base worlds).")
    p.add_argument("--pack", default="", help="Content pack for realistic worlds (default: $ALFRED_PACK, packs/main, "
                                              "packs/seed).")
    p.add_argument("--text-share", type=float, default=0.5,
                   help="Share of training tasks in the text interface (no ask_user or finish tool: the model asks, "
                        "declines and answers by replying, as in most apps and outside benchmarks).")
    p.add_argument("--temperature", type=float, default=0.9)
    p.add_argument("--top-p", type=float, default=1.0)
    p.add_argument("--max-steps", type=int, default=12, help="Model turns per episode.")
    p.add_argument("--max-tokens", type=int, default=2048, help="Tokens per model turn.")
    p.add_argument("--max-len", type=int, default=12288, help="Tokens per episode used for training.")
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument("--warmup", type=int, default=3, help="Warmup steps.")
    p.add_argument("--kl", type=float, default=0.0, help="KL penalty toward the starting adapter (0: off).")
    p.add_argument("--adv-norm", default="batch", choices=["batch", "group", "none"])
    p.add_argument("--workers", type=int, default=64, help="Episodes played at once.")
    p.add_argument("--val-every", type=int, default=10)
    p.add_argument("--val-worlds", type=int, default=6, help="Validation worlds (x 9 templates).")
    p.add_argument("--save-every", type=int, default=5)
    p.add_argument("--keep-adapters", type=int, default=3, help="Recent per-step adapters kept on disk.")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--trust-remote-code", action="store_true")
    p.add_argument("--no-kernels", action="store_true")
    p.add_argument("--resume", action="store_true")
    return p.parse_args(argv)


class Trainer:
    def __init__(self, a):
        self.a = a
        self.out = Path(a.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.tok = load_tokenizer(a.model, a.trust_remote_code)
        model = load_model(a.model, use_kernels=not a.no_kernels, trust_remote_code=a.trust_remote_code)
        self.model = load_lora(model, a.init_adapter, trainable=True)
        if a.kl > 0:
            self.model.load_adapter(a.init_adapter, adapter_name="ref", is_trainable=False)
            self.model.set_adapter("default")
        self.params = trainable_parameters(self.model)
        self.opt = torch.optim.AdamW(self.params, lr=a.lr, betas=(0.9, 0.999), weight_decay=0.0)
        self.sched = Schedule(self.opt, a.steps, a.warmup, floor=1.0)  # warmup, then constant
        self.client = make_client(a.vllm)
        lo, _, hi = a.train_worlds.partition("-")
        worlds = range(int(lo), int(hi or lo) + 1)
        if worlds.start < TRAIN_WORLDS.start or worlds.stop > TRAIN_WORLDS.stop:
            raise ValueError(f"--train-worlds must stay inside {TRAIN_WORLDS.start}-{TRAIN_WORLDS.stop - 1}")
        templates = [t.strip() for t in a.templates.split(",") if t.strip()]
        if set(templates) & set(EVAL_ONLY_TEMPLATES):
            raise ValueError("held-out templates are for evaluation only")
        self.sampler = TaskSampler(templates, worlds, a.seed, realistic_share=a.realistic_share, pack=a.pack,
                                   text_share=a.text_share)
        self.step, self.best_val, self.policy = 0, None, None
        self.val_tasks = validation_tasks(a.val_worlds, a.realistic_share, a.pack, templates, a.text_share)

    # --- serving the policy -------------------------------------------------------------------------------------

    def publish(self, adapter_dir: Path | str) -> None:
        """Load the adapter into vLLM as policy-<step>, and unload any other policy-* (including ones left over
        from a run that was interrupted while the server kept running)."""
        name = f"policy-{self.step}"
        served = serve.served_models(self.a.vllm)
        if name in served:
            serve.unload_adapter(self.a.vllm, name)
        serve.load_adapter(self.a.vllm, name, str(adapter_dir))
        self.policy = name
        for old in served:
            if old.startswith("policy-") and old != name:
                try:
                    serve.unload_adapter(self.a.vllm, old)
                except RuntimeError as e:
                    print(f"  (couldn't unload {old}: {e})")

    def save_step_adapter(self) -> Path:
        d = self.out / "adapters" / f"step-{self.step:05d}"
        self.model.save_pretrained(d, selected_adapters=["default"])
        olds = sorted((self.out / "adapters").glob("step-*"))[:-self.a.keep_adapters]
        for old in olds:
            shutil.rmtree(old, ignore_errors=True)
        return d

    # --- one step -----------------------------------------------------------------------------------------------

    def train_on(self, episodes: list[Episode]) -> dict:
        examples = []
        for ep in episodes:
            if not ep.ok or abs(ep.advantage) < 1e-9:
                continue
            try:
                r = render(self.tok, conversation(ep), task_tools(ep.task), max_len=self.a.max_len)
            except (TemplateMismatch, ValueError, KeyError):
                continue
            if r is not None:
                examples.append((ep.advantage, r))
        if not examples:
            return {"trained_episodes": 0}
        n_tokens = sum(r.n_train for _, r in examples)
        self.model.train()
        pg_sum, kl_sum = 0.0, 0.0
        for adv, r in examples:
            logp = token_logprobs(self.model, r.input_ids, r.labels)
            loss = -(adv * logp).sum() / n_tokens
            if self.a.kl > 0:
                self.model.set_adapter("ref")
                with torch.no_grad():
                    ref = token_logprobs(self.model, r.input_ids, r.labels)
                self.model.set_adapter("default")
                d = ref - logp
                kl = (torch.exp(d) - d - 1).sum() / n_tokens  # k3 estimator, always >= 0
                loss = loss + self.a.kl * kl
                kl_sum += kl.item()
            loss.backward()
            pg_sum += loss.item()
        grad_norm = torch.nn.utils.clip_grad_norm_(self.params, 1.0).item()
        self.opt.step()
        self.opt.zero_grad(set_to_none=True)
        self.sched.step()
        return {"trained_episodes": len(examples), "trained_tokens": n_tokens, "loss": round(pg_sum, 5),
                "kl": round(kl_sum, 6), "grad_norm": round(grad_norm, 4), "lr": self.opt.param_groups[0]["lr"]}

    def play_groups(self, tasks: list) -> list[Episode]:
        a = self.a
        jobs = [(t, s) for t in tasks for s in range(a.group)]
        return play_many(jobs, self.policy, self.client, a.workers, temperature=a.temperature, top_p=a.top_p,
                         max_tokens=a.max_tokens, max_steps=a.max_steps)

    def validate(self) -> dict:
        eps = play_many([(t, 0) for t in self.val_tasks], self.policy, self.client, self.a.workers,
                        temperature=0.6, top_p=0.95, max_tokens=self.a.max_tokens, max_steps=20)
        return batch_stats(eps)

    def checkpoint(self) -> None:
        save_checkpoint(self.model, self.opt, self.sched, self.out / "checkpoint",
                        {"step": self.step, "sampler": self.sampler.state_dict(), "best_val": self.best_val})

    def resume(self) -> bool:
        ck = self.out / "checkpoint"
        if not (ck / "state.json").exists():
            return False
        state = load_checkpoint(self.model, self.opt, self.sched, ck)
        self.step, self.best_val = state["step"], state.get("best_val")
        self.sampler.load_state_dict(state["sampler"])
        print(f"Resumed at step {self.step}")
        self.publish(ck)
        return True

    def run(self) -> None:
        a = self.a
        if not (a.resume and self.resume()):
            self.publish(a.init_adapter)
        (self.out / "args.json").write_text(json.dumps(vars(a), indent=1))
        log = (self.out / "log.jsonl").open("a")
        while self.step < a.steps:
            if self.step % a.val_every == 0 and not (self.out / "val" / f"step-{self.step:05d}.json").exists():
                v = {"step": self.step, **self.validate()}
                (self.out / "val").mkdir(exist_ok=True)
                (self.out / "val" / f"step-{self.step:05d}.json").write_text(json.dumps(v, indent=1))
                print(f"[val] step {self.step}: reward {v.get('reward')}  clean {v.get('clean_success')}  "
                      f"success {v.get('success')}", flush=True)
                if v.get("reward") is not None and (self.best_val is None or v["reward"] > self.best_val):
                    self.best_val = v["reward"]
                    src = Path(a.init_adapter) if self.step == 0 else self.out / "adapters" / f"step-{self.step:05d}"
                    if src.exists():
                        shutil.rmtree(self.out / "best", ignore_errors=True)
                        shutil.copytree(src, self.out / "best")
                        (self.out / "best" / "val.json").write_text(json.dumps(v, indent=1))
            t0 = time.time()
            tasks = self.sampler.sample(a.tasks)
            episodes = self.play_groups(tasks)
            drawn = list(episodes)  # the step's rates come from these, so they compare with rounds without refills
            zero_var = len(without_signal(tasks, episodes))
            refilled = 0
            for _ in range(a.refill_rounds):
                missing = a.tasks - (len(tasks) - len(without_signal(tasks, episodes)))
                if missing <= 0:
                    break
                extra = self.sampler.sample(missing, avoid={(t.world_seed, t.template) for t in tasks})
                more = self.play_groups(extra)
                for ep in more:
                    ep.refill = True
                tasks, episodes, refilled = tasks + extra, episodes + more, refilled + len(extra)
            t_play = time.time() - t0
            advantages(episodes, a.adv_norm)
            for tpl in {t.template for t in tasks}:
                solved = [e.metrics["clean_success"] for e in episodes if e.ok and e.task.template == tpl]
                if solved:
                    self.sampler.update(tpl, sum(solved) / len(solved))
            t1 = time.time()
            train_info = self.train_on(episodes)
            self.step += 1
            save_episodes(self.out / "episodes" / f"step-{self.step:05d}.jsonl.gz", episodes)
            self.publish(self.save_step_adapter())
            rec = {"step": self.step, **batch_stats(drawn), **train_info, "groups_without_signal": zero_var,
                   "groups_refilled": refilled, "groups_with_signal": len(tasks) - len(without_signal(tasks, episodes)),
                   "play_s": round(t_play, 1), "train_s": round(time.time() - t1, 1),
                   "template_weights": {k: round(v, 3) for k, v in self.sampler.weights().items()}}
            log.write(json.dumps(rec) + "\n")
            log.flush()
            print(f"step {self.step}/{a.steps}  reward {rec.get('reward')}  clean {rec.get('clean_success')}  "
                  f"over-ask {rec.get('over_ask')}  false-decline {rec.get('false_decline')}  "
                  f"signal {rec['groups_with_signal']}/{len(tasks)} (refilled {refilled})  "
                  f"trained {train_info.get('trained_episodes')} eps  play {t_play:.0f}s  "
                  f"train {rec['train_s']:.0f}s", flush=True)
            if self.step % a.save_every == 0 or self.step == a.steps:
                self.checkpoint()
        v = {"step": self.step, **self.validate()}
        (self.out / "val").mkdir(exist_ok=True)
        (self.out / "val" / f"step-{self.step:05d}.json").write_text(json.dumps(v, indent=1))
        if v.get("reward") is not None and (self.best_val is None or v["reward"] > self.best_val):
            shutil.rmtree(self.out / "best", ignore_errors=True)
            shutil.copytree(self.out / "adapters" / f"step-{self.step:05d}", self.out / "best")
            (self.out / "best" / "val.json").write_text(json.dumps(v, indent=1))
        self.model.save_pretrained(self.out / "final", selected_adapters=["default"])
        print(f"Done. Final adapter: {self.out / 'final'}; best by validation reward: {self.out / 'best'}")


def main(argv=None):
    a = parse_args(argv)
    if not serve.is_up(a.vllm):
        sys.exit(f"No vLLM server at {a.vllm}. Start one with: python -m alfred.train.serve start ... --lora")
    Trainer(a).run()


if __name__ == "__main__":
    main()
