"""Starting, stopping and loading adapters into the server, with a stand-in `vllm` command."""
import os
import stat
import sys
import textwrap

import pytest

from alfred.train import serve

FAKE_VLLM = textwrap.dedent('''\
    #!{python}
    import json, os, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1])
    name = args[args.index("--served-model-name") + 1]
    loaded = [m.split("=")[0] for i, m in enumerate(args) if i and args[i - 1] == "--lora-modules"]
    with open(os.environ["FAKE_VLLM_ARGS"], "w") as f:
        json.dump({{"args": args, "lora_env": os.environ.get("VLLM_ALLOW_RUNTIME_LORA_UPDATING")}}, f)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def reply(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            self.reply(200, {{"data": [{{"id": m}} for m in [name] + loaded]}})
        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/v1/load_lora_adapter":
                loaded.append(req["lora_name"])
            elif self.path == "/v1/unload_lora_adapter":
                loaded.remove(req["lora_name"])
            self.reply(200, {{}})

    HTTPServer(("127.0.0.1", port), H).serve_forever()
''')


@pytest.fixture
def fake_vllm(tmp_path, monkeypatch):
    exe = tmp_path / "bin" / "vllm"
    exe.parent.mkdir()
    exe.write_text(FAKE_VLLM.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{exe.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_VLLM_ARGS", str(tmp_path / "args.json"))
    monkeypatch.setenv("HOME", str(tmp_path))  # the pid file goes in the home directory
    return tmp_path


def test_start_load_unload_stop(fake_vllm):
    import json
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = serve.base_url(port)
    serve.start("org/base", "base", port=port, lora_modules={"alfred-sft": "/adapters/sft"},
                preset_flags=serve.nemotron_flags("/p.py"), log_path=str(fake_vllm / "vllm.log"), timeout=30)
    try:
        sent = json.loads((fake_vllm / "args.json").read_text())
        assert sent["lora_env"] == "True" and "--enable-lora" in sent["args"]
        assert sent["args"][sent["args"].index("--tool-call-parser") + 1] == "qwen3_coder"
        assert serve.served_models(url) == ["base", "alfred-sft"]
        serve.load_adapter(url, "policy-1", "/adapters/p1")
        assert "policy-1" in serve.served_models(url)
        serve.unload_adapter(url, "policy-1")
        assert "policy-1" not in serve.served_models(url)
        with pytest.raises(RuntimeError):
            serve.start("org/base", "base", port=port)  # port already taken
    finally:
        assert serve.stop(port)
    assert not serve.is_up(url)
    assert not serve.stop(port)  # nothing left to stop


def test_start_returns_inside_a_terminal(fake_vllm):
    """Colab runs shell commands in a terminal and waits until nothing holds it open. A server that kept the
    terminal as its stdin made `serve start` (and the notebook cell) hang for as long as vLLM ran."""
    import pty
    import select
    import socket
    import subprocess
    import time
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    master, slave = pty.openpty()
    proc = subprocess.Popen([sys.executable, "-m", "alfred.train.serve", "start", "--model", "org/base",
                             "--served-name", "base", "--port", str(port), "--log", str(fake_vllm / "v.log")],
                            stdin=slave, stdout=slave, stderr=slave, cwd=os.path.dirname(os.path.dirname(__file__)))
    os.close(slave)
    try:
        deadline, closed = time.time() + 60, False
        while time.time() < deadline and not closed:
            if select.select([master], [], [], 1)[0]:
                try:
                    closed = not os.read(master, 4096)
                except OSError:  # every holder of the terminal closed it
                    closed = True
        assert closed, "the terminal stayed open after `serve start` finished"
        assert proc.wait(5) == 0 and serve.is_up(serve.base_url(port))
    finally:
        os.close(master)
        serve.stop(port)
