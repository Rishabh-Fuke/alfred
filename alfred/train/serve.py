"""Start and stop the vLLM server, and load LoRA adapters into it while it runs.

    python -m alfred.train.serve start --model nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 --served-name nemotron-3-nano-4b
    python -m alfred.train.serve start ... --lora-module alfred-sft=out/sft/adapter   # serve an adapter too
    python -m alfred.train.serve stop

The server runs in the background (its log goes to --log) and keeps running after this command returns.
With --lora (implied by --lora-module) it can load and unload adapters at runtime, which the RL trainer uses
after every step: no restart, so a new policy is live within seconds.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

NANO_V3_PARSER_URL = ("https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16/resolve/main/"
                      "nano_v3_reasoning_parser.py")


def nemotron_flags(parser_path: str) -> list[str]:
    """NVIDIA's recommended vLLM flags for Nemotron 3 Nano: native tool calls in the Qwen3-Coder format, and the
    reasoning parser that separates thinking from the answer."""
    return ["--trust-remote-code", "--mamba_ssm_cache_dtype", "float32", "--enable-auto-tool-choice",
            "--tool-call-parser", "qwen3_coder", "--reasoning-parser-plugin", parser_path,
            "--reasoning-parser", "nano_v3"]


def _pid_file(port: int) -> Path:
    return Path.home() / f".alfred-vllm-{port}.pid"


def _request(url: str, payload: dict | None = None, timeout: float = 30.0) -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def base_url(port: int = 8000) -> str:
    return f"http://localhost:{port}/v1"


def is_up(url: str) -> bool:
    try:
        return _request(f"{url}/models", timeout=5)[0] == 200
    except (urllib.error.URLError, OSError):
        return False


def served_models(url: str) -> list[str]:
    status, body = _request(f"{url}/models")
    return [m["id"] for m in json.loads(body)["data"]] if status == 200 else []


def load_adapter(url: str, name: str, path: str) -> None:
    """Load a LoRA adapter into a running server (needs --lora at start). Requests can then use `name` as the
    model. vLLM's endpoints sit at the server root, not under /v1."""
    root = url.rsplit("/v1", 1)[0]
    status, body = _request(f"{root}/v1/load_lora_adapter", {"lora_name": name, "lora_path": str(path)}, 300)
    if status != 200:
        raise RuntimeError(f"vLLM couldn't load adapter {name} from {path}: HTTP {status}: {body[:500]}")


def unload_adapter(url: str, name: str) -> None:
    root = url.rsplit("/v1", 1)[0]
    status, body = _request(f"{root}/v1/unload_lora_adapter", {"lora_name": name}, 60)
    if status != 200 and "not found" not in body.lower():
        raise RuntimeError(f"vLLM couldn't unload adapter {name}: HTTP {status}: {body[:300]}")


def start(model: str, served_name: str, port: int = 8000, lora: bool = False, lora_modules: dict | None = None,
          max_lora_rank: int = 64, max_loras: int = 4, gpu_memory_utilization: float = 0.9,
          max_model_len: int = 32768, max_num_seqs: int = 64, preset_flags: list[str] | None = None,
          extra: list[str] | None = None, log_path: str = "vllm.log", timeout: float = 1800) -> int:
    url = base_url(port)
    if is_up(url):
        raise RuntimeError(f"Something is already serving on port {port}. Stop it first (serve stop).")
    lora = lora or bool(lora_modules)
    cmd = ["vllm", "serve", model, "--served-model-name", served_name, "--port", str(port),
           "--max-model-len", str(max_model_len), "--max-num-seqs", str(max_num_seqs),
           "--gpu-memory-utilization", str(gpu_memory_utilization), *(preset_flags or [])]
    env = dict(os.environ)
    if lora:
        cmd += ["--enable-lora", "--max-lora-rank", str(max_lora_rank), "--max-loras", str(max_loras)]
        if lora_modules:
            cmd += ["--lora-modules", *[f"{k}={v}" for k, v in lora_modules.items()]]
        env["VLLM_ALLOW_RUNTIME_LORA_UPDATING"] = "True"
    cmd += list(extra or [])
    if not shutil.which("vllm"):
        raise RuntimeError("vLLM isn't installed here (pip install vllm).")
    log = open(log_path, "w")
    # stdin too: an inherited terminal (Colab's cell output is one) would stay open while vLLM runs, so whatever ran
    # this command would wait for vLLM to exit.
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env,
                            start_new_session=True)
    _pid_file(port).write_text(str(proc.pid))
    print("Starting:", " ".join(cmd), flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if is_up(url):
            print(f"vLLM is serving {', '.join(served_models(url))} at {url}")
            return proc.pid
        time.sleep(5)
    tail = Path(log_path).read_text(errors="replace")[-4000:]
    stop(port)
    raise RuntimeError(f"vLLM didn't start. The end of its log ({log_path}):\n{tail}")


def stop(port: int = 8000, wait: float = 90) -> bool:
    """Stop the server started by `start` on this port. Returns True if one was running."""
    pid_file = _pid_file(port)
    if not pid_file.exists():
        return False
    pid = int(pid_file.read_text())
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        return False
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            if os.waitpid(pid, os.WNOHANG)[0] == pid:  # our own child: reap it, or it lingers as a zombie
                break
        except ChildProcessError:
            pass
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.5)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    pid_file.unlink(missing_ok=True)
    time.sleep(3)  # let the GPU memory go
    return True


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.train.serve", description="Manage the vLLM server.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--model", required=True)
    s.add_argument("--served-name", required=True)
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--lora", action="store_true", help="Allow loading LoRA adapters at runtime.")
    s.add_argument("--lora-module", action="append", default=[], help="name=path of an adapter to serve.")
    s.add_argument("--max-lora-rank", type=int, default=64)
    s.add_argument("--gpu-memory-utilization", type=float, default=0.9)
    s.add_argument("--max-model-len", type=int, default=32768)
    s.add_argument("--max-num-seqs", type=int, default=64)
    s.add_argument("--nemotron-parser", default="",
                   help="Path to NVIDIA's nano_v3_reasoning_parser.py. Adds the Nemotron 3 Nano flags.")
    s.add_argument("--log", default="vllm.log")
    s.add_argument("extra", nargs=argparse.REMAINDER, help="Anything after -- goes to vllm serve as is.")
    t = sub.add_parser("stop")
    t.add_argument("--port", type=int, default=8000)
    a = p.parse_args(argv)
    if a.cmd == "stop":
        print("Stopped." if stop(a.port) else "Nothing was running.")
        return
    modules = dict(m.split("=", 1) for m in a.lora_module)
    extra = [x for x in a.extra if x != "--"]
    try:
        start(a.model, a.served_name, a.port, a.lora, modules, a.max_lora_rank,
              gpu_memory_utilization=a.gpu_memory_utilization, max_model_len=a.max_model_len,
              max_num_seqs=a.max_num_seqs, preset_flags=nemotron_flags(a.nemotron_parser) if a.nemotron_parser else [],
              extra=extra, log_path=a.log)
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
