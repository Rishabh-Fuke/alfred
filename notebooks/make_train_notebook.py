"""Writes colab_train.ipynb. Edit the cells here and rerun: python notebooks/make_train_notebook.py"""
import json
from pathlib import Path

INTRO = """# Alfred: train Nemotron 3 Nano 4B for judgment

Teaches NVIDIA's Nemotron 3 Nano 4B to ask when a request is ambiguous, say "I can't" when it can't, do and share no more than it was asked, and ignore instructions hidden in emails and invites. Then measures whether that carried over to request types it never trained on and to outside benchmarks.

**Runtime:** Runtime → Change runtime type → **A100**. Run the cells in order. Everything is saved to Google Drive (`alfred-results/`), and every step resumes where it stopped, so after a disconnect just run the cells again from the top (cells that already finished skip their work).

| Cell | What | Time | Cost |
|---|---|---|---|
| 3 | Start the teacher episodes in the background (Nemotron 3 Nano 30B on Token Factory; no GPU needed) | ~1 h, in the background | ~$5 of credits |
| 4 | Meanwhile, a smoke test on the GPU: 2 training steps, load that adapter into vLLM, one RL step. Catches setup problems in minutes, not hours | ~15 min | |
| 5 | Baselines for the untrained 4B model in native tool-calling mode, and on the new held-out task types | ~40 min | <$1 |
| 6 | Wait for the teacher, then build the training set | | |
| 7 | Supervised fine-tuning (LoRA) | ~1-3 h | |
| 8 | Evaluate the fine-tuned model | ~30 min | |
| 9 | Reinforcement learning (GRPO) against the environment | ~3-6 h | |
| 10 | Final evaluation, including outside benchmarks | ~2 h | |
| 11 | Package results to send back | 1 min | |
| 12 | Export for laptops: merge the adapter, convert to a 4-bit GGUF, check it still works, publish it (optional) | ~40 min | |

**Secrets** (key icon in the left sidebar; turn on notebook access for each):
- `NEBIUS_API_KEY`: your Token Factory key.
- `HF_TOKEN` (only for cell 12's upload): a Hugging Face token with write access.
- `GITHUB_TOKEN`: a fine-grained GitHub token with read access to the alfred repo, so this notebook always runs the latest code. Without it, upload the repo to Drive as `alfred.zip` (or a folder named `alfred`).

Never paste keys into cells."""

SETUP = r"""# 1. Code, packages and helpers (5-10 minutes the first time)
import glob, json, os, shlex, shutil, subprocess, sys, time, urllib.request
from google.colab import drive, userdata

drive.mount('/content/drive')
OUT = '/content/drive/MyDrive/alfred-results'
TRAIN = f'{OUT}/train'
for d in (f'{OUT}/runs', TRAIN):
    os.makedirs(d, exist_ok=True)


def secret(name):
    try:
        return userdata.get(name)
    except Exception:
        return None


# Always start from the latest code.
subprocess.run(['rm', '-rf', '/content/alfred'], check=True)
token = secret('GITHUB_TOKEN')
if token:
    r = subprocess.run(['git', 'clone', '-q', '--depth', '1',
                        f'https://x-access-token:{token}@github.com/Rishabh-Fuke/alfred.git', '/content/alfred'],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError('git clone failed: ' + r.stderr.replace(token, '***'))
    subprocess.run(['git', '-C', '/content/alfred', 'remote', 'set-url', 'origin',
                    'https://github.com/Rishabh-Fuke/alfred.git'], check=True)  # don't leave the token on disk
else:
    src = next((p for p in ['/content/drive/MyDrive/alfred.zip', '/content/drive/MyDrive/alfred'] if os.path.exists(p)), None)
    assert src, 'Add a GITHUB_TOKEN secret, or upload the repo to Drive as alfred.zip or a folder named alfred.'
    if src.endswith('.zip'):
        subprocess.run(['unzip', '-q', '-o', src, '-d', '/content/unzipped'], check=True)
        root = os.path.dirname(glob.glob('/content/unzipped/**/pyproject.toml', recursive=True)[0])
    else:
        root = src
    shutil.copytree(root, '/content/alfred')
assert os.path.exists('/content/alfred/alfred/train/grpo.py'), 'This copy of the code is out of date.'
print(subprocess.run(['git', '-C', '/content/alfred', 'log', '--oneline', '-1'], capture_output=True, text=True).stdout)

# vLLM first (it pins torch and transformers), then the training extras on top of those versions.
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'vllm'], check=True)
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-e', '/content/alfred[train]'], check=True)
PARSER = '/content/nano_v3_reasoning_parser.py'
if not os.path.exists(PARSER):
    urllib.request.urlretrieve('https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16/resolve/main/'
                               'nano_v3_reasoning_parser.py', PARSER)

MODEL, SERVED = 'nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16', 'nemotron-3-nano-4b'
DATA_FILE, SFT_DIR, GRPO_DIR = f'{TRAIN}/data/sft.jsonl', f'{TRAIN}/sft', f'{TRAIN}/grpo'
EPISODES = 5 * 3  # per task type in every eval: 5 held-out worlds x 3 tries
# How every Alfred eval in this notebook calls the 4B model: native tool calls, reasoning off, NVIDIA's
# recommended sampling for tool calling. Training uses the same format.
NATIVE = ['--base-url', 'vllm', '--tool-mode', 'native', '--no-think', '--temperature', 0.6,
          '--extra-body', '{"top_p": 0.95}', '--workers', 16]
JSON_MODE = ['json' if x == 'native' else x for x in NATIVE]


def sh(cmd):
    # Runs a shell command, streams its output here, and stops the cell if it fails.
    ip = get_ipython()
    ip.system(cmd)
    code = ip.user_ns.get('_exit_code', 0)
    if code:
        raise RuntimeError(f'Failed (exit status {code}): {cmd[:200]}. The output above says why.')


def alfred(*args):
    # Runs `python -m alfred.<args>`.
    sh('cd /content/alfred && python -m alfred.' + ' '.join(shlex.quote(str(a)) for a in args))


def runs(*labels):
    # The latest run directory for each label that exists.
    return [sorted(glob.glob(f'{OUT}/runs/*-{l}'))[-1] for l in labels if glob.glob(f'{OUT}/runs/*-{l}')]


def resume(label):
    # Continue the latest run with this label instead of starting over.
    done = sorted(d for d in glob.glob(f'{OUT}/runs/*-{label}') if os.path.exists(f'{d}/config.json'))
    return ['--resume', done[-1]] if done else ['--out', f'{OUT}/runs', '--label', label]


def progress(label):
    done = sorted(glob.glob(f'{OUT}/runs/*-{label}/results.jsonl'))
    return sum(1 for _ in open(done[-1])) if done else 0


def finished(label, episodes):
    return progress(label) >= episodes


def background(label, *args):
    # Starts `python -m alfred.<args>` in the background (for runs that only call Token Factory) and returns.
    if subprocess.run(['pgrep', '-f', label], capture_output=True).returncode == 0:
        print(f'{label} is already running')
        return
    subprocess.Popen([sys.executable, '-m', f'alfred.{args[0]}', *map(str, args[1:])], cwd='/content/alfred',
                     stdout=open(f'/content/{label}.log', 'w'), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     start_new_session=True)
    print(f'{label} started in the background; its log is /content/{label}.log')


def wait_for(label, episodes):
    while not finished(label, episodes):
        if subprocess.run(['pgrep', '-f', label], capture_output=True).returncode != 0:
            time.sleep(5)
            if finished(label, episodes):
                break
            print(open(f'/content/{label}.log').read()[-3000:] if os.path.exists(f'/content/{label}.log') else '')
            raise RuntimeError(f'{label} stopped at {progress(label)}/{episodes} episodes. Run its cell again to resume.')
        print(f'{label}: {progress(label)}/{episodes} episodes', flush=True)
        time.sleep(60)
    print(f'{label}: done')


def serve(*adapters, gpu=0.9):
    # (Re)start vLLM with the base model and any adapters as name=path. LoRA stays enabled so adapters can be
    # loaded later without a restart.
    alfred('train.serve', 'stop')
    args = ['train.serve', 'start', '--model', MODEL, '--served-name', SERVED, '--lora', '--nemotron-parser', PARSER,
            '--gpu-memory-utilization', gpu, '--log', '/content/vllm.log']
    for a in adapters:
        args += ['--lora-module', a]
    alfred(*args)


def stop_serving():
    alfred('train.serve', 'stop')


alfred('eval', '--agent', 'oracle', '--worlds', 1, '--out', '/content/oracle-check')
"""

KEYS = r"""# 2. Token Factory key, from the NEBIUS_API_KEY secret
os.environ['NEBIUS_API_KEY'] = secret('NEBIUS_API_KEY') or ''
if not os.environ['NEBIUS_API_KEY']:
    import getpass
    os.environ['NEBIUS_API_KEY'] = getpass.getpass('Nebius Token Factory API key (input is hidden): ')
from openai import OpenAI
tf = OpenAI(base_url='https://api.tokenfactory.nebius.com/v1/', api_key=os.environ['NEBIUS_API_KEY'])
print('Token Factory OK:', sorted(m.id for m in tf.models.list() if 'nemotron' in m.id.lower()))
"""

TEACHER = r"""# 3. Start the teacher episodes in the background (about 1 hour, roughly $5 of Token Factory credits)
# Nemotron 3 Nano 30B scored 95% clean on the baseline, as well as Super, at a fifth of the price. Worlds 0-219,
# all 9 training task types, one episode each. Only clean successes become training data (cell 6). This runs on
# Token Factory, so the GPU is free for the next cells meanwhile.
TEACHER = 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B'
TEACHER_WORLDS = 220
if not finished('teacher-nano30b', TEACHER_WORLDS * 9):
    background('teacher-nano30b', 'eval', '--models', TEACHER, '--base-url', 'tokenfactory', '--world-start', 0,
               '--worlds', TEACHER_WORLDS, '--temperature', 0.6, '--max-tokens', 8192, '--workers', 12,
               *resume('teacher-nano30b'))
# The bigger Nemotron models on the held-out task types, for reference (well under $1).
for m in ['nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B', 'nvidia/nemotron-3-super-120b-a12b']:
    label = 'tf-heldout-' + m.split('/')[-1].lower()
    if not finished(label, 7 * EPISODES):
        background(label, 'eval', '--models', m, '--base-url', 'tokenfactory', '--templates', 'heldout',
                   '--worlds', 5, '--samples', 3, '--temperature', 0.6, '--max-tokens', 8192, '--workers', 4,
                   *resume(label))
"""

DATA = r"""# 6. Wait for the teacher, then build the training set (about 1 minute once it's done)
# The teacher's clean successes, at most one per task, topped up with scripted episodes (worlds 300-549) so every
# task type has the same count. Validation tasks are held out by task, not by episode.
wait_for('teacher-nano30b', TEACHER_WORLDS * 9)
teacher_runs = sorted(d for d in glob.glob(f'{OUT}/runs/*-teacher-*') if os.path.exists(f'{d}/results.jsonl'))
print('Teacher runs:', *teacher_runs, sep='\n  ')
alfred('train.data', '--runs', *teacher_runs, '--oracle-worlds', '300-549', '--out', DATA_FILE)
"""

BASELINE = r"""# 5. Baselines for the comparison (about 40 minutes)
# The October baseline ran the 4B model with JSON tool calls, its better mode before training. Training uses
# native tool calls (what vLLM, agent frameworks and the outside benchmarks use), so measure the untrained model
# that way too, and on the held-out task types the model never trains on (both tool modes, to be fair to it).
serve()
if not finished('base-native', 9 * EPISODES):
    alfred('eval', '--models', SERVED, *NATIVE, '--worlds', 5, '--samples', 3, *resume('base-native'))
if not finished('base-native-heldout', 7 * EPISODES):
    alfred('eval', '--models', SERVED, *NATIVE, '--templates', 'heldout', '--worlds', 5, '--samples', 3,
           *resume('base-native-heldout'))
if not finished('base-json-heldout', 7 * EPISODES):
    alfred('eval', '--models', SERVED, *JSON_MODE, '--templates', 'heldout', '--worlds', 5, '--samples', 3,
           *resume('base-json-heldout'))
alfred('report', *runs('main-nemotron4b-nothink', 'base-native'), '--out', f'{TRAIN}/report-base.md')
"""

SMOKE = r"""# 4. Smoke test of the training stack while the teacher runs (about 15 minutes)
# Two SFT steps on 8 scripted conversations, load that adapter into vLLM and play one task with it, then one tiny
# GRPO step. If anything in the setup is wrong (model loading, Mamba kernels, LoRA in vLLM, GPU memory while both
# run), it shows up here instead of hours into training.
SMOKE = '/content/smoke'
if not os.path.exists(f'{SMOKE}/done'):
    stop_serving()
    alfred('train.data', '--oracle-worlds', '600-601', '--out', f'{SMOKE}/data.jsonl')
    alfred('train.sft', '--model', MODEL, '--data', f'{SMOKE}/data.jsonl', '--out', f'{SMOKE}/sft', '--limit', 8,
           '--batch', 4, '--epochs', 1, '--eval-every', 1000, '--save-every', 1000)
    serve(f'alfred-smoke={SMOKE}/sft/adapter', gpu=0.42)
    alfred('eval', '--models', 'alfred-smoke', *NATIVE, '--worlds', 1, '--templates', 'event_time_lookup',
           '--out', f'{SMOKE}/eval')
    alfred('train.grpo', '--model', MODEL, '--init-adapter', f'{SMOKE}/sft/adapter', '--out', f'{SMOKE}/grpo',
           '--steps', 1, '--tasks', 2, '--group', 4, '--val-every', 1000, '--val-worlds', 1)
    open(f'{SMOKE}/done', 'w').write('ok')
print('Smoke test passed.')
"""

SFT = r"""# 7. Supervised fine-tuning with LoRA (about 1-3 hours; resumes after a disconnect)
# One conversation per forward pass, 16 per optimizer step, loss only on the assistant's turns.
stop_serving()  # training needs the GPU memory
if not os.path.exists(f'{SFT_DIR}/adapter/adapter_model.safetensors'):
    alfred('train.sft', '--model', MODEL, '--data', DATA_FILE, '--out', SFT_DIR, '--epochs', 2, '--lr', '1e-4',
           '--resume')
print(open(f'{SFT_DIR}/log.jsonl').read().splitlines()[-1])
"""

EVAL_SFT = r"""# 8. Evaluate the fine-tuned model on the same tasks (about 30 minutes)
serve(f'alfred-sft={SFT_DIR}/adapter')
if not finished('sft-native', 9 * EPISODES):
    alfred('eval', '--models', 'alfred-sft', *NATIVE, '--worlds', 5, '--samples', 3, *resume('sft-native'))
if not finished('sft-native-heldout', 7 * EPISODES):
    alfred('eval', '--models', 'alfred-sft', *NATIVE, '--templates', 'heldout', '--worlds', 5, '--samples', 3,
           *resume('sft-native-heldout'))
alfred('report', *runs('base-native', 'main-nemotron4b-nothink', 'sft-native'), '--out', f'{TRAIN}/report-sft.md')
alfred('report', *runs('base-native-heldout', 'base-json-heldout', 'sft-native-heldout'),
       '--out', f'{TRAIN}/report-sft-heldout.md')
"""

GRPO = r"""# 9. Reinforcement learning (GRPO) against the environment (about 3-6 hours; resumes after a disconnect)
# vLLM plays the episodes with the current policy while the trainer shares the GPU, so vLLM gets less memory here.
# Every step: 8 training tasks x 8 attempts, rewards from the environment's checks, one policy-gradient step,
# and the new adapter goes straight into vLLM. Validation on worlds 950-955 every 10 steps; the best is kept.
STEPS = 60
if not os.path.exists(f'{GRPO_DIR}/final/adapter_model.safetensors'):
    serve(gpu=0.42)
    alfred('train.grpo', '--model', MODEL, '--init-adapter', f'{SFT_DIR}/adapter', '--out', GRPO_DIR,
           '--steps', STEPS, '--resume')
for line in open(f'{GRPO_DIR}/log.jsonl').read().splitlines()[-3:]:
    r = json.loads(line)
    print({k: r.get(k) for k in ('step', 'reward', 'clean_success', 'over_ask', 'false_decline', 'trained_episodes')})
"""

EVAL_FINAL = r"""# 10. Final evaluation: Alfred tasks, held-out task types, and the outside benchmarks (about 2 hours)
serve(f'alfred-sft={SFT_DIR}/adapter', f'alfred-rl={GRPO_DIR}/best')
if not finished('rl-native', 9 * EPISODES):
    alfred('eval', '--models', 'alfred-rl', *NATIVE, '--worlds', 5, '--samples', 3, *resume('rl-native'))
if not finished('rl-native-heldout', 7 * EPISODES):
    alfred('eval', '--models', 'alfred-rl', *NATIVE, '--templates', 'heldout', '--worlds', 5, '--samples', 3,
           *resume('rl-native-heldout'))
alfred('report', *runs('base-native', 'main-nemotron4b-nothink', 'sft-native', 'rl-native'),
       '--out', f'{TRAIN}/report-final.md')
alfred('report', *runs('base-native-heldout', 'base-json-heldout', 'sft-native-heldout', 'rl-native-heldout'),
       '--out', f'{TRAIN}/report-final-heldout.md')

# Outside benchmarks the model never trained on, in their own environment (their packages pin other versions).
if not os.path.exists('/content/benchenv/bin/python'):
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'uv'], check=True)
    subprocess.run(['uv', 'venv', '-q', '/content/benchenv'], check=True)
subprocess.run(['uv', 'pip', 'install', '-q', '--python', '/content/benchenv/bin/python', '-e',
                '/content/alfred[external]'], check=True)
EXT = f'{OUT}/external'
JUDGE = ['--judge-model', 'nvidia/nemotron-3-super-120b-a12b', '--judge-base-url', 'tokenfactory']


def external(module, *args):
    sh('cd /content/alfred && /content/benchenv/bin/python -m alfred.external.' + module + ' '
       + ' '.join(shlex.quote(str(a)) for a in args))


# When2Call is quick, so both trained models; the two agent benchmarks take longer, so the final model only.
for name in ('alfred-sft', 'alfred-rl'):
    external('when2call', '--model', name, '--base-url', 'vllm', '--no-think', *JUDGE, '--out', EXT)
external('bfcl_bench', '--model', 'alfred-rl', '--base-url', 'vllm', '--no-think', '--workers', 32, '--out', EXT)
external('agentdojo_bench', '--model', 'alfred-rl', '--base-url', 'vllm', '--no-think', '--out', EXT)
external('summary', EXT, '--out', f'{EXT}/report.md')
print(open(f'{EXT}/report.md').read())
"""

PACKAGE = r"""# 11. Package everything to send back (about 1 minute)
# Logs, reports, data stats, validation curves and episodes, and every eval run's trajectories. Adapters and
# the training data itself stay on Drive.
os.chdir(OUT)
patterns = ['train/*.md', 'train/data/*.md', 'train/sft/log.jsonl', 'train/sft/args.json', 'train/grpo/log.jsonl',
            'train/grpo/args.json', 'train/grpo/val/*', 'train/grpo/episodes/*', 'train/grpo/best/val.json',
            'external/report.md', 'external/*/summary.*']
files = [f for p in patterns for f in glob.glob(p)]
new_runs = [d for d in glob.glob('runs/*') if any(k in d for k in ('-base-', '-sft-', '-rl-', '-tf-heldout-'))]
files += glob.glob('train/report-*.md')
subprocess.run(['rm', '-f', 'train-results.zip'])
subprocess.run(['zip', '-qr', 'train-results.zip', *files, *new_runs], check=True)
print(f'Download alfred-results/train-results.zip from your Drive ({os.path.getsize("train-results.zip") / 1e6:.0f} MB) '
      'and send it over.')
"""


EXPORT = r"""# 12. Export for laptops (about 40 minutes): merge, convert to GGUF, quantize to 4 bits, check, publish
# The merged model goes to Drive; the 4-bit GGUF is what Ollama and llama.cpp run on a laptop. The check plays the
# 45 Alfred tasks once with the 4-bit model under llama.cpp, so "it still works after quantization" is measured.
FINAL = f'{TRAIN}/alfred-nemotron-3-nano-4b'
stop_serving()
if not os.path.exists(f'{FINAL}/alfred_merge.json'):
    alfred('train.export', '--model', MODEL, '--adapter', f'{GRPO_DIR}/best', '--out', FINAL)
LLAMA = '/content/llama.cpp'
if not os.path.exists(f'{LLAMA}/build/bin/llama-server'):
    sh(f'git clone -q --depth 1 https://github.com/ggml-org/llama.cpp {LLAMA}')
    sh(f'cmake -S {LLAMA} -B {LLAMA}/build -DGGML_CUDA=ON -DLLAMA_CURL=OFF > /content/llama-build.log 2>&1')
    sh(f'cmake --build {LLAMA}/build -j 12 --target llama-server llama-quantize >> /content/llama-build.log 2>&1')
sh(f'{sys.executable} -m pip install -q --no-deps {LLAMA}/gguf-py')
GGUF = f'{TRAIN}/gguf'
os.makedirs(GGUF, exist_ok=True)
BF16, Q4 = f'{GGUF}/alfred-nemotron-3-nano-4b-BF16.gguf', f'{GGUF}/alfred-nemotron-3-nano-4b-Q4_K_M.gguf'
if not os.path.exists(BF16):
    sh(f'{sys.executable} {LLAMA}/convert_hf_to_gguf.py {shlex.quote(FINAL)} --outtype bf16 --outfile {shlex.quote(BF16)}')
if not os.path.exists(Q4):
    sh(f'{LLAMA}/build/bin/llama-quantize {shlex.quote(BF16)} {shlex.quote(Q4)} Q4_K_M')
print(f'4-bit model: {os.path.getsize(Q4) / 1e9:.2f} GB')

# Play the Alfred tasks with the 4-bit model under llama.cpp, the way a laptop would run it.
llama = subprocess.Popen([f'{LLAMA}/build/bin/llama-server', '-m', Q4, '--jinja', '-c', '131072', '-ngl', '99',
                          '--port', '8090', '--parallel', '8', '--alias', 'alfred-q4'],
                         stdout=open('/content/llama-server.log', 'w'), stderr=subprocess.STDOUT)
for _ in range(120):
    try:
        urllib.request.urlopen('http://localhost:8090/v1/models', timeout=3)
        break
    except Exception:
        time.sleep(2)
try:
    Q4_RUN = ['--base-url', 'http://localhost:8090/v1', '--tool-mode', 'native', '--no-think', '--temperature', 0.6,
              '--extra-body', '{"top_p": 0.95}', '--workers', 8]
    if not finished('rl-q4-native', 9 * EPISODES):
        alfred('eval', '--models', 'alfred-q4', *Q4_RUN, '--worlds', 5, '--samples', 3, *resume('rl-q4-native'))
finally:
    llama.terminate()
alfred('report', *runs('rl-native', 'rl-q4-native'), '--out', f'{TRAIN}/report-q4.md')

# Publish to Hugging Face (optional): the merged model and the GGUF, so anyone can run it.
hf_token = secret('HF_TOKEN')
if hf_token:
    from huggingface_hub import HfApi
    api = HfApi(token=hf_token)
    user = api.whoami()['name']
    for repo, folder, pattern in [(f'{user}/alfred-nemotron-3-nano-4b', FINAL, None),
                                  (f'{user}/alfred-nemotron-3-nano-4b-GGUF', GGUF, '*Q4_K_M.gguf')]:
        api.create_repo(repo, exist_ok=True)
        api.upload_folder(repo_id=repo, folder_path=folder, allow_patterns=pattern)
        print('Uploaded', f'https://huggingface.co/{repo}')
else:
    print('Add an HF_TOKEN secret to publish the model; the files are on your Drive in', TRAIN)
"""


def cell(source: str, kind: str = "code") -> dict:
    lines = source.strip("\n").split("\n")
    src = [line + "\n" for line in lines[:-1]] + [lines[-1]]
    c = {"cell_type": kind, "metadata": {}, "source": src}
    if kind == "code":
        c.update(execution_count=None, outputs=[])
    return c


def main():
    nb = {"cells": [cell(INTRO, "markdown"), cell(SETUP), cell(KEYS), cell(TEACHER), cell(SMOKE), cell(BASELINE),
                    cell(DATA), cell(SFT), cell(EVAL_SFT), cell(GRPO), cell(EVAL_FINAL), cell(PACKAGE),
                    cell(EXPORT)],
          "metadata": {"accelerator": "GPU", "colab": {"gpuType": "A100", "provenance": []},
                       "kernelspec": {"display_name": "Python 3", "name": "python3"},
                       "language_info": {"name": "python"}},
          "nbformat": 4, "nbformat_minor": 0}
    out = Path(__file__).with_name("colab_train.ipynb")
    out.write_text(json.dumps(nb, indent=1) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
