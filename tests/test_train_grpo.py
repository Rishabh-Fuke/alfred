"""GRPO end to end against a fake vLLM server: episodes are played, scored, trained on, and each new adapter is
loaded into the server before the next step plays with it."""
import gzip
import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("peft")

from alfred.dev.replay_server import _key, build_scripts, check_conversation, completion  # noqa: E402
from alfred.tasks import generate_tasks  # noqa: E402
from alfred.train import grpo  # noqa: E402
from alfred.train.rollout import Episode  # noqa: E402


class FakeVLLM(ThreadingHTTPServer):
    """Speaks the bits of vLLM's API the trainer uses. Each episode either replays the oracle or, half the time,
    gives up with an unhelpful finish on its first turn, so groups have rewards to compare."""
    daemon_threads = True

    def __init__(self, scripts, lazy: float = 0.5):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.scripts, self.loaded, self.events, self.problems, self.lazy = scripts, {}, [], [], lazy
        self.rng, self.lock = random.Random(0), threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._send(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in ["base", *self.server.loaded]]})

    def do_POST(self):  # noqa: N802
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        srv = self.server
        if self.path.endswith("/load_lora_adapter"):
            if req["lora_name"] in srv.loaded:
                return self._send(400, {"error": "already loaded"})
            srv.loaded[req["lora_name"]] = req["lora_path"]
            srv.events.append(("load", req["lora_name"]))
            return self._send(200, {})
        if self.path.endswith("/unload_lora_adapter"):
            srv.loaded.pop(req["lora_name"], None)
            srv.events.append(("unload", req["lora_name"]))
            return self._send(200, {})
        if req.get("model") not in srv.loaded:
            srv.problems.append(f"request for {req.get('model')}, loaded: {list(srv.loaded)}")
            return self._send(404, {"error": {"message": "model not found"}})
        msgs = req["messages"]
        problem = check_conversation(msgs)
        if problem:
            srv.problems.append(problem)
            return self._send(400, {"error": {"message": problem}})
        system = next(m["content"] for m in msgs if m["role"] == "system")
        instruction = next(m["content"] for m in msgs if m["role"] == "user")
        script = srv.scripts.get(_key(system, instruction), [])
        turn = sum(m["role"] == "assistant" for m in msgs)
        with srv.lock:
            lazy = turn == 0 and srv.rng.random() < srv.lazy
        if lazy or turn >= len(script):
            name, args = "finish", {"status": "done", "message": "Done."}
        else:
            name, args = script[turn]
        self._send(200, completion(tool_call=(f"call_{turn}", name, json.dumps(args))))


@pytest.fixture
def fake_vllm():
    scripts = {**build_scripts(0, 6), **build_scripts(950, 1)}
    srv = FakeVLLM(scripts)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


@pytest.fixture
def init_adapter(tiny_model_dir, tmp_path):
    from alfred.train.model import add_lora, load_model
    model = add_lora(load_model(str(tiny_model_dir), gradient_checkpointing=False), r=8, alpha=16)
    model.save_pretrained(tmp_path / "sft-adapter")
    return tmp_path / "sft-adapter"


def test_advantages_are_relative_to_the_group():
    t1, t2 = generate_tasks(0, 1)[:2]
    eps = [Episode(t1, 0, metrics={}, reward=1.0), Episode(t1, 1, metrics={}, reward=0.0),
           Episode(t2, 0, metrics={}, reward=0.5), Episode(t2, 1, metrics={}, reward=0.5)]
    grpo.advantages(eps, "none")
    assert [e.advantage for e in eps] == [0.5, -0.5, 0.0, 0.0]  # a group with equal rewards teaches nothing
    grpo.advantages(eps, "batch")
    assert eps[0].advantage == pytest.approx(1.0) and eps[1].advantage == pytest.approx(-1.0)


def test_sampler_prefers_templates_with_something_to_learn():
    s = grpo.TaskSampler(["a", "b", "c"], range(10))
    s.solve_rate = {"a": 1.0, "b": 0.5, "c": 0.0}
    w = s.weights()
    assert w["b"] > w["a"] and w["b"] > w["c"] and w["a"] > 0


def test_grpo_runs_publishes_and_resumes(tiny_model_dir, init_adapter, fake_vllm, tmp_path):
    srv, url = fake_vllm
    out = tmp_path / "grpo"
    common = ["--model", str(tiny_model_dir), "--init-adapter", str(init_adapter), "--out", str(out), "--vllm", url,
              "--tasks", "3", "--group", "4", "--train-worlds", "0-5", "--val-every", "2", "--val-worlds", "1",
              "--save-every", "1", "--workers", "6", "--lr", "1e-3", "--max-len", "8192"]
    grpo.main(common + ["--steps", "2"])
    assert not srv.problems, srv.problems[:3]
    log = [json.loads(x) for x in (out / "log.jsonl").read_text().splitlines()]
    assert [r["step"] for r in log] == [1, 2]
    assert all(r["errors"] == 0 for r in log) and sum(r["trained_episodes"] for r in log) > 0
    assert ("load", "policy-0") in srv.events and ("load", "policy-2") in srv.events
    assert ("unload", "policy-0") in srv.events  # old policies don't pile up in the server
    with gzip.open(out / "episodes" / "step-00001.jsonl.gz", "rt") as f:
        eps = [json.loads(x) for x in f]
    assert len(eps) == 12 and all(e["messages"] and e["metrics"] for e in eps)
    assert {e["reward"] for e in eps} != {eps[0]["reward"]}
    assert (out / "best" / "adapter_model.safetensors").exists() and (out / "val" / "step-00000.json").exists()

    grpo.main(common + ["--steps", "3", "--resume"])
    log = [json.loads(x) for x in (out / "log.jsonl").read_text().splitlines()]
    assert [r["step"] for r in log] == [1, 2, 3]
    assert (out / "final" / "adapter_model.safetensors").exists()


def test_without_signal_finds_groups_whose_rewards_all_match():
    t1, t2 = generate_tasks(0, 1)[:2]
    eps = [Episode(t1, 0, metrics={}, reward=1.0), Episode(t1, 1, metrics={}, reward=1.0),
           Episode(t2, 0, metrics={}, reward=0.5), Episode(t2, 1, metrics={}, reward=-0.25)]
    assert grpo.without_signal([t1, t2], eps) == [t1]


def test_sampler_never_repeats_a_task_within_a_step():
    s = grpo.TaskSampler(["app_ask", "event_time_lookup"], range(10))
    first = s.sample(4)
    more = s.sample(4, avoid={(t.world_seed, t.template) for t in first})
    assert not {(t.world_seed, t.template) for t in first} & {(t.world_seed, t.template) for t in more}
    s = grpo.TaskSampler(["app_ask"], range(2))
    assert len(s.sample(5)) == 2  # only two distinct tasks exist; it doesn't spin looking for more


def _serve(scripts, lazy):
    srv = FakeVLLM(scripts, lazy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


APPS = "app_call,app_nearby,app_lookup"


@pytest.mark.parametrize("lazy", [0.5, 0.0])
def test_grpo_trains_on_app_tasks_and_refills_groups_without_signal(tiny_model_dir, init_adapter, tmp_path, lazy):
    """lazy 0.5: groups differ, so it trains on app tasks (their tools rendered into the prompt). lazy 0: every
    attempt replays the oracle, every group gets the same reward, and each step refills them all."""
    srv, url = _serve({**build_scripts(0, 6, APPS), **build_scripts(950, 1, APPS)}, lazy)
    out = tmp_path / "grpo"
    try:
        grpo.main(["--model", str(tiny_model_dir), "--init-adapter", str(init_adapter), "--out", str(out), "--vllm",
                   url, "--tasks", "2", "--group", "4", "--train-worlds", "0-5", "--templates", APPS,
                   "--text-share", "0", "--realistic-share", "0", "--refill-rounds", "2", "--val-every", "5",
                   "--val-worlds", "1", "--workers", "6", "--lr", "1e-3", "--max-len", "8192", "--steps", "1"])
    finally:
        srv.shutdown()
    assert not srv.problems, srv.problems[:3]
    rec = json.loads((out / "log.jsonl").read_text().splitlines()[0])
    with gzip.open(out / "episodes" / "step-00001.jsonl.gz", "rt") as f:
        eps = [json.loads(x) for x in f]
    assert {e["template"] for e in eps} <= set(APPS.split(","))
    if lazy:
        assert rec["trained_episodes"] > 0 and rec["groups_with_signal"] >= 1
    else:
        assert all(e["metrics"]["clean_success"] for e in eps)
        assert rec["groups_without_signal"] == 2 and rec["groups_refilled"] == 4 and rec["groups_with_signal"] == 0
        assert len(eps) == (2 + 4) * 4 and rec["trained_episodes"] == 0
        # The step's rates come from the drawn groups only; refills are marked, and the audit leaves them out.
        assert rec["episodes"] == 2 * 4 and sum(e["refill"] for e in eps) == 4 * 4
        from alfred.train.audit import load_episodes
        assert len(load_episodes(out)[1]) == 2 * 4
