"""Writes colab_train.ipynb. Edit the cells here and rerun: python notebooks/make_train_notebook.py"""
import json
from pathlib import Path

INTRO = """# Alfred: train Nemotron 3 Nano 4B for judgment

Teaches NVIDIA's Nemotron 3 Nano 4B to ask when a request is ambiguous, say "I can't" when it can't, do and share no more than it was asked, and ignore instructions hidden in emails and invites. Then measures whether that carried over to request types it never trained on and to outside benchmarks.

**Runtime:** Runtime → Change runtime type → **A100**. Run the cells in order. Everything is saved to Google Drive (`alfred-results/`), and every step resumes where it stopped, so after a disconnect just run the cells again from the top (cells that already finished skip their work).

**Round 4.** Round 3's judgment carried over to apps it never trained on, but asking for missing information only got back to the untrained level, and BFCL's multi-turn categories stayed weak (see RESULTS.md). Round 4 adds three task types in the procedural apps: requests that sound complete but whose tool requires a field nobody mentioned (round 3's commonest When2Call miss was filling such a field with a plausible default), requests that match two records, where the right move is to ask which one, and conversations of two or three requests over the same tools, where a later one is missing a detail or needs a tool that isn't offered (BFCL is multi-turn; round 3's tasks were single requests). It also fixes a bug that made about 13% of round 3's app eval tasks differ between eval runs, so those suites are re-run for the earlier models. Run every cell from the top: work finished in earlier rounds is skipped, and their results stay on Drive for comparison.

| Cell | What | Time | Cost |
|---|---|---|---|
| 3 | Realistic worlds: loads the reviewed content pack from the repo | 2 min | |
| 4 | Teacher episodes in the background (Nemotron 3 Nano 30B on Token Factory, no GPU): round 4 adds conversations and two-match tasks | ~30 min, in the background | under $1 of credits |
| 5 | Meanwhile, a smoke test on the GPU: 2 training steps, play a task in each interface with that adapter, one RL step | ~15 min | |
| 6 | Baselines for the untrained 4B model on the app evals (its Alfred evals are round 2's) | ~20 min | |
| 7 | Wait for the teacher, then build the training set | ~5 min | |
| 8 | Supervised fine-tuning (LoRA), from the base model | ~2-4 h | |
| 9 | Evaluate the fine-tuned model | ~1 h | |
| 10 | Reinforcement learning (GRPO) against the environment | ~3-6 h | |
| 11 | Final evaluation (rounds 2 and 3 side by side), including outside benchmarks | ~3 h | ~$1 |
| 12 | Package results to send back | 1 min | |
| 13 | Export for laptops: merge the adapter, convert to a 4-bit GGUF, check it still works, publish it (optional) | ~40 min | |

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


# Always start from the latest code: from GitHub (public, or private with the GITHUB_TOKEN secret), else the newest
# alfred*.zip on Drive.
subprocess.run(['rm', '-rf', '/content/alfred', '/content/unzipped'], check=True)
token = secret('GITHUB_TOKEN')
public = not token and subprocess.run(['git', 'clone', '-q', '--depth', '1', 'https://github.com/Rishabh-Fuke/alfred.git',
                                       '/content/alfred'], capture_output=True).returncode == 0
if public:
    print(subprocess.run(['git', '-C', '/content/alfred', 'log', '--oneline', '-1'], capture_output=True,
                         text=True).stdout)
elif token:
    r = subprocess.run(['git', 'clone', '-q', '--depth', '1',
                        f'https://x-access-token:{token}@github.com/Rishabh-Fuke/alfred.git', '/content/alfred'],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError('git clone failed: ' + r.stderr.replace(token, '***'))
    subprocess.run(['git', '-C', '/content/alfred', 'remote', 'set-url', 'origin',
                    'https://github.com/Rishabh-Fuke/alfred.git'], check=True)  # don't leave the token on disk
    print(subprocess.run(['git', '-C', '/content/alfred', 'log', '--oneline', '-1'], capture_output=True,
                         text=True).stdout)
else:
    print('No GITHUB_TOKEN secret is visible to this notebook (add it and switch on "Notebook access"), '
          'so using a copy of the code on Drive.')
    zips = sorted(glob.glob('/content/drive/MyDrive/alfred*.zip'), key=os.path.getmtime)  # also "alfred (1).zip"
    if zips:
        src = zips[-1]
        print(f'Using {src}, uploaded {time.ctime(os.path.getmtime(src))}')
        subprocess.run(['unzip', '-q', '-o', src, '-d', '/content/unzipped'], check=True)
        found = glob.glob('/content/unzipped/**/alfred/train/grpo.py', recursive=True)
        assert found, f'{src} does not contain the Alfred code.'
        root = os.path.dirname(os.path.dirname(os.path.dirname(found[0])))
    elif os.path.isdir('/content/drive/MyDrive/alfred'):
        root = '/content/drive/MyDrive/alfred'
        print(f'Using the folder {root}')
    else:
        raise RuntimeError('Add a GITHUB_TOKEN secret, or upload the code to My Drive as alfred.zip.')
    shutil.copytree(root, '/content/alfred')
assert os.path.exists('/content/alfred/alfred/realism/build.py'), (
    'This copy of the code is out of date. Upload the latest alfred.zip to My Drive (or delete the old "alfred" '
    'folder there), then run this cell again.')

# vLLM first (it pins torch and transformers), then the training extras on top of those versions.
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'vllm'], check=True)
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-e', '/content/alfred[train]'], check=True)
# The Mamba layers' fast GPU kernels: this transformers wants kernels 0.16.x (newer ones are refused, and training
# silently falls back to slow plain-PyTorch code). Colab's preinstalled torchao is too old for PEFT, which then
# refuses to add LoRA at all; nothing here uses torchao, so remove it.
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'kernels>=0.16,<0.17'], check=True)
subprocess.run([sys.executable, '-m', 'pip', 'uninstall', '-q', '-y', 'torchao'], check=True)
print(subprocess.run([sys.executable, '-c', 'import torch, transformers, peft, vllm, kernels; print("torch", '
                      'torch.__version__, "| transformers", transformers.__version__, "| peft", peft.__version__, '
                      '"| vllm", vllm.__version__, "| kernels", kernels.__version__)'],
                     capture_output=True, text=True).stdout)
PARSER = '/content/nano_v3_reasoning_parser.py'
if not os.path.exists(PARSER):
    urllib.request.urlretrieve('https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16/resolve/main/'
                               'nano_v3_reasoning_parser.py', PARSER)

MODEL, SERVED = 'nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16', 'nemotron-3-nano-4b'
# Round 4 (this one) adds conversations and two-match tasks (see RESULTS.md for why). Round 3's adapters stay on
# Drive (train/sft-v3, train/grpo-v3) and are evaluated next to round 4's for comparison.
DATA_FILE, SFT_DIR, GRPO_DIR = f'{TRAIN}/data-v4/sft.jsonl', f'{TRAIN}/sft-v4', f'{TRAIN}/grpo-v4'
V3_GRPO_DIR = f'{TRAIN}/grpo-v3'
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
    # Done, and (for realistic runs) made with the content pack in use now; otherwise it runs again in a new folder.
    done = sorted(glob.glob(f'{OUT}/runs/*-{label}/config.json'))
    if done and globals().get('PACK_ID'):
        used = json.load(open(done[-1])).get('pack_id') or ''
        if used and used != PACK_ID:
            return False
    return progress(label) >= episodes


def running(label):
    # Is a background run with exactly this label alive? ("teacher-x" must not match "teacher-x-real".)
    return subprocess.run(['pgrep', '-f', f'{label}( |$)'], capture_output=True).returncode == 0


def background(label, *args):
    # Starts `python -m alfred.<args>` in the background (for runs that only call Token Factory) and returns.
    if running(label):
        print(f'{label} is already running')
        return
    subprocess.Popen([sys.executable, '-m', f'alfred.{args[0]}', *map(str, args[1:])], cwd='/content/alfred',
                     stdout=open(f'/content/{label}.log', 'w'), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     start_new_session=True)
    print(f'{label} started in the background; its log is /content/{label}.log')


def wait_for(label, episodes):
    while not finished(label, episodes):
        if not running(label):
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
alfred('eval', '--agent', 'oracle', '--worlds', 2, '--templates', 'apps,newapps', '--out', '/content/oracle-check')
REAL = ['--style', 'realistic']  # add to an eval for realistic worlds (text from the content pack, cell 3)
TEXT = ['--interface', 'text']  # no ask_user or finish tool: the model asks, declines and answers by replying
sys.path.insert(0, '/content/alfred')
from alfred.tasks import APP_HELDOUT_TEMPLATES, APP_TEMPLATES, HELDOUT_TEMPLATES, TEMPLATES
from alfred.train.data import TRAINING_TEMPLATES
N_TRAIN, N_HELD, N_APPS = len(TEMPLATES), len(HELDOUT_TEMPLATES), len(APP_TEMPLATES)
ROUND1_TEMPLATES = ','.join(list(TEMPLATES)[:9])  # the nine task types round 1 trained on
# The four Alfred evals: trained and held-out task types, each in Alfred's own tool interface and in the plain-text
# interface (how most apps and the outside benchmarks work), all on the same realistic worlds so the interface is the
# only difference. Re-grading round 2's runs with round 3's grader changed no clean-success rate, so the untrained
# and round-2 models keep their r2- runs here.
ALFRED_SUITE = [('tools-real', [*REAL], N_TRAIN), ('tools-heldout-real', [*REAL, '--templates', 'heldout'], N_HELD),
                ('text-real', [*TEXT, *REAL], N_TRAIN),
                ('text-heldout-real', [*TEXT, *REAL, '--templates', 'heldout'], N_HELD)]
# The app evals. apps-text is an in-distribution check: the training apps on new worlds, with the same request
# templates and values as training, so some requests repeat training ones word for word. Transfer is measured on
# newapps: the four apps never trained on, in each interface.
APP_SUITE = [('apps-text', [*TEXT, '--templates', 'apps'], N_APPS),
             ('newapps-text', [*TEXT, '--templates', 'newapps'], N_APPS),
             ('newapps-tools', ['--templates', 'newapps'], N_APPS)]
# Round 4's task types (two-match, a field the tool requires that wasn't said, conversations) on training apps
# (in-distribution) and on the apps never trained on. Conversations are always plain text; the others run in plain
# text here too.
N_APPS4 = 3
APP4_SUITE = [('apps4-text', [*TEXT, '--templates', 'apps4'], N_APPS4),
              ('newapps4-text', [*TEXT, '--templates', 'newapps4'], N_APPS4)]
SUITE = ALFRED_SUITE + APP_SUITE + APP4_SUITE


def eval_suite(served, prefix, suite=SUITE):
    for suffix, args, n in suite:
        label = f'{prefix}-{suffix}'
        if not finished(label, n * EPISODES):
            alfred('eval', '--models', served, *NATIVE, *args, '--worlds', 5, '--samples', 3, *resume(label))


def suite_reports(name, *prefixes):
    for suffix, _, _ in SUITE:
        alfred('report', *runs(*[f'{p}-{suffix}' for p in prefixes]), '--out', f'{TRAIN}/report-{name}-{suffix}.md')
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

PACK = r"""# 3. Realistic worlds: build the content pack (about 1 hour, roughly $3-5 of Token Factory credits)
# Real inboxes are messy, so the training worlds should be too. This samples real emails from the Enron corpus as
# style references (they stay on your Drive and never enter the repo), has Nemotron 3 Super write ~2,000 templates
# in that style (every fact is a slot the world fills, so answers stay verifiable), and checks every one in code.
# Resumes after a disconnect. Once a reviewed pack is committed to the repo, this cell just uses that.
REALISM = f'{OUT}/realism'
os.makedirs(REALISM, exist_ok=True)
REFS, PACK_FILE = f'{REALISM}/enron_refs.jsonl.gz', f'{REALISM}/main.jsonl.gz'
committed = '/content/alfred/alfred/realism/packs/main.jsonl.gz'
if os.path.exists(committed):
    PACK_FILE = committed
    print('Using the reviewed pack from the repo.')
else:
    if not os.path.exists(REFS):
        alfred('realism.enron', '--download', '/content/enron')
        alfred('realism.enron', '--tar', '/content/enron/enron_mail_20150507.tar.gz', '--out', REFS)
    if not os.path.exists(PACK_FILE):
        alfred('realism.build', '--refs', REFS, '--out', PACK_FILE, '--workers', 16)
    print(open(f'{REALISM}/main.build.md').read())
os.environ['ALFRED_PACK'] = PACK_FILE  # every later cell (and background run) builds realistic worlds from it
sys.path.insert(0, '/content/alfred')
from alfred.realism.pack import load as load_pack
PACK_ID = load_pack(PACK_FILE).id
print('Content pack:', PACK_ID)
if not os.path.exists(f'{REALISM}/check.md') or os.path.getmtime(f'{REALISM}/check.md') < os.path.getmtime(PACK_FILE):
    alfred('realism.check', '--pack', PACK_FILE, '--out', f'{REALISM}/check.md')
print('Pack checked. Send alfred-results/realism/main.jsonl.gz and check.md for review before cell 4.')
"""

TEACHER = r"""# 4. Teacher episodes in the background (round 4: about 30 minutes, under $1 of Token Factory credits)
# Nemotron 3 Nano 30B plays training tasks on Token Factory (no GPU needed). Only its clean successes become training
# data (cell 7). Already done: round 1's 9 task types in Alfred's tool interface (worlds 0-249) and round 2's 12 in
# the plain-text interface (worlds 550-709). Round 3: the 5 app task types, plain text in worlds 710-829 and Alfred's
# tool interface in worlds 830-929.
TEACHER = 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B'
TEACHER_RUNS = {  # label: (first world, worlds, extra arguments, task types per world)
    'teacher-nano30b': (0, 90, ['--templates', ROUND1_TEMPLATES], 9),
    'teacher-nano30b-real': (90, 160, [*REAL, '--templates', ROUND1_TEMPLATES], 9),
    'teacher-nano30b-text': (550, 160, [*REAL, *TEXT], N_TRAIN),
    'teacher-nano30b-apps-text': (710, 120, [*TEXT, '--templates', 'apps'], N_APPS),
    'teacher-nano30b-apps': (830, 100, ['--templates', 'apps'], N_APPS),
    # Round 4: conversations (always plain text) and two-match tasks, and two-match tasks in the tool interface.
    'teacher-nano30b-apps4-text': (0, 150, [*TEXT, '--templates', 'apps4'], N_APPS4),
    'teacher-nano30b-pick': (150, 100, ['--templates', 'app_pick,app_require'], 2),
}
for label, (start, n, extra, per_world) in TEACHER_RUNS.items():
    if not finished(label, n * per_world):
        background(label, 'eval', '--models', TEACHER, '--base-url', 'tokenfactory', '--world-start', start,
                   '--worlds', n, *extra, '--temperature', 0.6, '--max-tokens', 8192, '--workers', 8, *resume(label))
# The bigger Nemotron models on the held-out task types, for reference (about $1; done in round 1).
for m in ['nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B', 'nvidia/nemotron-3-super-120b-a12b']:
    for suffix, style in (('', []), ('-real', REAL)):
        label = 'tf-heldout-' + m.split('/')[-1].lower() + suffix
        if not finished(label, N_HELD * EPISODES):
            background(label, 'eval', '--models', m, '--base-url', 'tokenfactory', '--templates', 'heldout', *style,
                       '--worlds', 5, '--samples', 3, '--temperature', 0.6, '--max-tokens', 8192, '--workers', 4,
                       *resume(label))
"""

DATA = r"""# 7. Wait for the teacher, then build the training set (about 5 minutes once it's done)
# For each of the 17 training task types (Alfred's 12 and the 5 app types) in each interface: the teacher's clean
# successes (at most one per task), topped up with scripted episodes (worlds 300-549, 60% realistic, half in each
# interface) to 100 examples, about 3,400 in all. Asking or declining is the right move in 8 of the 17 types.
# Validation tasks are held out by task, not episode.
for label, (start, n, extra, per_world) in TEACHER_RUNS.items():
    wait_for(label, n * per_world)
teacher_runs = sorted(d for d in glob.glob(f'{OUT}/runs/*-teacher-*') if os.path.exists(f'{d}/results.jsonl'))
print('Teacher runs:', *teacher_runs, sep='\n  ')
if not os.path.exists(DATA_FILE):
    alfred('train.data', '--runs', *teacher_runs, '--oracle-worlds', '300-549', '--realistic-share', 0.6,
           '--text-share', 0.5, '--per-template', 100, '--out', DATA_FILE)
print(open(DATA_FILE.replace('.jsonl', '.md')).read())
"""

BASELINE = r"""# 6. Baselines (about 20 minutes): the untrained 4B model on the app evals. Its Alfred evals are round 2's (r2-base).
serve()
eval_suite(SERVED, 'r2-base', ALFRED_SUITE)  # already done in round 2: skipped
eval_suite(SERVED, 'r3-base', APP_SUITE)  # done in round 3
# The app suites again under the fixed task generator (round 3's varied between runs), plus round 4's new ones.
eval_suite(SERVED, 'r4-base', APP_SUITE + APP4_SUITE)
"""

SMOKE = r"""# 5. Smoke test of the training stack while the teacher runs (about 15 minutes)
# Two SFT steps on 8 scripted conversations, load that adapter into vLLM and play one task with it in each interface,
# then one tiny GRPO step. If anything in the setup is wrong (model loading, Mamba kernels, LoRA in vLLM, GPU memory
# while both run), it shows up here instead of hours into training.
SMOKE = '/content/smoke-v4'
if not os.path.exists(f'{SMOKE}/done'):
    stop_serving()
    alfred('train.data', '--oracle-worlds', '600-601', '--realistic-share', 0.5, '--text-share', 0.5,
           '--out', f'{SMOKE}/data.jsonl')
    alfred('train.sft', '--model', MODEL, '--data', f'{SMOKE}/data.jsonl', '--out', f'{SMOKE}/sft', '--limit', 8,
           '--batch', 4, '--epochs', 1, '--eval-every', 1000, '--save-every', 1000)
    serve(f'alfred-smoke={SMOKE}/sft/adapter', gpu=0.42)
    for interface in ([], TEXT):
        alfred('eval', '--models', 'alfred-smoke', *NATIVE, *REAL, *interface, '--worlds', 1,
               '--templates', 'event_time_lookup,missing_content,app_ask,app_pick,app_multi', '--out', f'{SMOKE}/eval')
    alfred('train.grpo', '--model', MODEL, '--init-adapter', f'{SMOKE}/sft/adapter', '--out', f'{SMOKE}/grpo',
           '--steps', 1, '--tasks', 2, '--group', 4, '--val-every', 1000, '--val-worlds', 1,
           '--templates', 'missing_content,app_ask,app_pick,app_multi', '--refill-rounds', 1)
    open(f'{SMOKE}/done', 'w').write('ok')
print('Smoke test passed.')
"""

SFT = r"""# 8. Supervised fine-tuning with LoRA (about 2-4 hours; resumes after a disconnect)
# Starts again from the base model, not round 1's adapter, so none of round 1's act-first habit carries over.
# One conversation per forward pass, 16 per optimizer step, loss only on the assistant's turns.
stop_serving()  # training needs the GPU memory
if not os.path.exists(f'{SFT_DIR}/adapter/adapter_model.safetensors'):
    alfred('train.sft', '--model', MODEL, '--data', DATA_FILE, '--out', SFT_DIR, '--epochs', 2, '--lr', '1e-4',
           '--resume')
print(open(f'{SFT_DIR}/log.jsonl').read().splitlines()[-1])
"""

EVAL_SFT = r"""# 9. Evaluate the fine-tuned model (about 1 hour)
serve(f'alfred-sft4={SFT_DIR}/adapter')
eval_suite('alfred-sft4', 'r4-sft4')
suite_reports('sft4', 'r2-base', 'r4-base', 'r4-sft4')
for suffix in ('text-heldout-real', 'newapps-text', 'newapps4-text'):
    print(open(f'{TRAIN}/report-sft4-{suffix}.md').read())
"""

GRPO = r"""# 10. Reinforcement learning (GRPO) against the environment (about 3-6 hours; resumes after a disconnect)
# vLLM plays the episodes with the current policy while the trainer shares the GPU, so vLLM gets less memory here.
# Every step: 8 training tasks x 8 attempts over all 17 training task types (70% in realistic worlds, half in the
# plain-text interface), rewards from the environment's checks, one policy-gradient step, and the new adapter goes
# straight into vLLM. A group whose 8 attempts all score the same teaches nothing, so up to 3 times a step it's
# replaced with a fresh task (round 2 trained on nothing in 17 of 60 steps). Validation on worlds 950-955 every 10
# steps, in the same mix; the best is kept.
STEPS = 60
if not os.path.exists(f'{GRPO_DIR}/final/adapter_model.safetensors'):
    serve(gpu=0.42)
    alfred('train.grpo', '--model', MODEL, '--init-adapter', f'{SFT_DIR}/adapter', '--out', GRPO_DIR,
           '--steps', STEPS, '--templates', ','.join(TRAINING_TEMPLATES), '--refill-rounds', 3, '--resume')
for line in open(f'{GRPO_DIR}/log.jsonl').read().splitlines()[-3:]:
    r = json.loads(line)
    print({k: r.get(k) for k in ('step', 'reward', 'clean_success', 'over_ask', 'false_decline', 'trained_episodes',
                                 'groups_without_signal', 'groups_refilled', 'groups_with_signal')})
# Did it learn the judgment, or a way around the checks? Compares the first and last steps on every known shortcut.
alfred('train.audit', GRPO_DIR, '--out', f'{GRPO_DIR}/audit.md')
print(open(f'{GRPO_DIR}/audit.md').read())
"""

EVAL_FINAL = r"""# 11. Final evaluation: Alfred tasks, held-out task types, and the outside benchmarks (about 3 hours)
# Round 4's models next to round 3's. Round 3's email-task evals are reused (those tasks never varied); its app evals
# run again here on the fixed generator, so every model is compared on exactly the same app tasks.
serve(f'alfred-sft4={SFT_DIR}/adapter', f'alfred-rl4={GRPO_DIR}/best', f'alfred-rl3={V3_GRPO_DIR}/best')
eval_suite('alfred-rl4', 'r4-rl4')
eval_suite('alfred-rl3', 'r4-rl3', APP_SUITE + APP4_SUITE)
suite_reports('final4', 'r2-base', 'r4-base', 'r3-rl3', 'r4-rl3', 'r4-sft4', 'r4-rl4')

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


# When2Call is quick, so both of round 4's models; the two agent benchmarks take longer, so the final model only.
# (Round 2's results are already in the same folder, so the summary shows both rounds.)
for name in ('alfred-sft4', 'alfred-rl4'):
    external('when2call', '--model', name, '--base-url', 'vllm', '--no-think', *JUDGE, '--out', EXT)
external('bfcl_bench', '--model', 'alfred-rl4', '--base-url', 'vllm', '--no-think', '--workers', 32, '--out', EXT)
external('agentdojo_bench', '--model', 'alfred-rl4', '--base-url', 'vllm', '--no-think', '--out', EXT)
external('summary', EXT, '--out', f'{EXT}/report.md')
print(open(f'{EXT}/report.md').read())
"""

PACKAGE = r"""# 12. Package everything to send back (15-30 minutes: Google Drive is slow at reading thousands of small files)
# Logs, reports, data stats, validation curves and episodes, and every eval run's trajectories. Adapters and
# the training data itself stay on Drive.
os.chdir(OUT)
patterns = ['train/*.md', 'realism/main.build.md', 'realism/check.md', 'external/report.md', 'external/*/summary.*']
for d in ('', '-v2', '-v3', '-v4'):  # every round
    patterns += [f'train/data{d}/*.md', f'train/sft{d}/log.jsonl', f'train/sft{d}/args.json', f'train/grpo{d}/log.jsonl',
                 f'train/grpo{d}/args.json', f'train/grpo{d}/val/*', f'train/grpo{d}/episodes/*',
                 f'train/grpo{d}/best/val.json', f'train/grpo{d}/audit.md']
files = [f for p in patterns for f in glob.glob(p)]
# A few AgentDojo episodes as they happened (clean runs of the first user tasks), to see how the model ends a task.
files += sorted(glob.glob('external/agentdojo-*/logs/**/user_task_[0-4]/none/none.json', recursive=True))[:20]
new_runs = [d for d in glob.glob('runs/*') if any(k in d for k in ('-r4-', '-teacher-nano30b-apps4', '-teacher-nano30b-pick'))]
# Every When2Call model reply, so each judged item can be checked (round 3 shipped only the summaries).
files += glob.glob('external/when2call-*/responses.jsonl')
files += glob.glob('train/report-*.md')
subprocess.run(['rm', '-f', 'train-results.zip'])
subprocess.run(['zip', '-qr', 'train-results.zip', *files, *new_runs], check=True)
print(f'Download alfred-results/train-results.zip from your Drive ({os.path.getsize("train-results.zip") / 1e6:.0f} MB) '
      'and send it over.')
"""


EXPORT = r"""# 13. Export for laptops (about 40 minutes): merge, convert to GGUF, quantize to 4 bits, check, publish
# The merged model goes to Drive; the 4-bit GGUF is what Ollama and llama.cpp run on a laptop. The check plays the
# trained task types with the 4-bit model under llama.cpp, in both interfaces, so "it still works after
# quantization" is measured.
FINAL = f'{TRAIN}/alfred-4b'
stop_serving()
# The adapter to ship: round 4's by default. If round 4 didn't beat round 3 on the same evals, use V3_GRPO_DIR.
EXPORT_FROM = GRPO_DIR
if not os.path.exists(f'{FINAL}/alfred_merge.json'):
    alfred('train.export', '--model', MODEL, '--adapter', f'{EXPORT_FROM}/best', '--out', FINAL)
LLAMA = '/content/llama.cpp'
if not os.path.exists(f'{LLAMA}/build/bin/llama-server'):
    sh(f'git clone -q --depth 1 https://github.com/ggml-org/llama.cpp {LLAMA}')
    sh(f'cmake -S {LLAMA} -B {LLAMA}/build -DGGML_CUDA=ON -DLLAMA_CURL=OFF > /content/llama-build.log 2>&1')
    sh(f'cmake --build {LLAMA}/build -j 12 --target llama-server llama-quantize >> /content/llama-build.log 2>&1')
sh(f'{sys.executable} -m pip install -q --no-deps {LLAMA}/gguf-py')
GGUF = f'{TRAIN}/gguf'
os.makedirs(GGUF, exist_ok=True)
BF16, Q4 = f'{GGUF}/alfred-4b-BF16.gguf', f'{GGUF}/alfred-4b-Q4_K_M.gguf'
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
    for suffix, args, n in [*ALFRED_SUITE[::2], APP_SUITE[1]]:  # trained Alfred types in each interface; new apps
        label = f'r4-rl4-q4-{suffix}'
        if not finished(label, n * EPISODES):
            alfred('eval', '--models', 'alfred-q4', *Q4_RUN, *args, '--worlds', 5, '--samples', 3, *resume(label))
finally:
    llama.terminate()
for suffix, _, _ in [*ALFRED_SUITE[::2], APP_SUITE[1]]:
    alfred('report', *runs(f'r4-rl4-{suffix}', f'r4-rl4-q4-{suffix}'), '--out', f'{TRAIN}/report-q4-{suffix}.md')

# Publish to Hugging Face (needs an HF_TOKEN secret with write access), as evidence for the results: the merged model
# with the exact LoRA adapter that was evaluated (lora/), and the 4-bit GGUF. The weights are evaluation-only: LICENSE
# is the Alfred 4B Evaluation License from the repo, and each repo is gated (visitors accept those terms, then get
# access automatically) before any weights are uploaded. They're derived from Nemotron, so NVIDIA's license also goes
# to everyone who gets them: LICENSE-NVIDIA, copied word for word from the base model's repository. The repo names
# leave out NVIDIA's product names, which its license doesn't grant; the card says where the model came from.
hf_token = secret('HF_TOKEN')
if hf_token:
    import re
    from huggingface_hub import HfApi, hf_hub_download
    api = HfApi(token=hf_token)
    user = api.whoami()['name']
    MODEL_REPO, GGUF_REPO = f'{user}/alfred-4b', f'{user}/alfred-4b-GGUF'
    nvidia = [f for f in api.list_repo_files(MODEL) if re.fullmatch(r'(?i)license(\.(md|txt))?', os.path.basename(f))]
    assert nvidia, f'No LICENSE file found in {MODEL}; add NVIDIA\'s license text by hand before uploading.'
    card = (open('/content/alfred/MODEL_CARD.md').read().replace('__MODEL_REPO__', MODEL_REPO)
            .replace('__GGUF_REPO__', f'https://huggingface.co/{GGUF_REPO}'))
    for folder in (FINAL, GGUF):
        shutil.copy(hf_hub_download(MODEL, nvidia[0]), os.path.join(folder, 'LICENSE-NVIDIA'))
        shutil.copy('/content/alfred/MODEL_LICENSE.md', os.path.join(folder, 'LICENSE'))
        open(os.path.join(folder, 'README.md'), 'w').write(card)
    shutil.copy(hf_hub_download(MODEL, 'nano_v3_reasoning_parser.py'), FINAL)  # the card's vLLM command uses it
    shutil.copytree(f'{EXPORT_FROM}/best', f'{FINAL}/lora', dirs_exist_ok=True)
    gguf_files = ['*Q4_K_M.gguf', 'README.md', 'LICENSE', 'LICENSE-NVIDIA']
    for repo, folder, pattern in [(MODEL_REPO, FINAL, None), (GGUF_REPO, GGUF, gguf_files)]:
        api.create_repo(repo, exist_ok=True)
        api.update_repo_settings(repo_id=repo, gated='auto')  # before any weights go up
        api.upload_folder(repo_id=repo, folder_path=folder, allow_patterns=pattern)
        print('Uploaded', f'https://huggingface.co/{repo}', '(gated: accept the terms, then access is automatic)')
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
    nb = {"cells": [cell(INTRO, "markdown"), cell(SETUP), cell(KEYS), cell(PACK), cell(TEACHER), cell(SMOKE), cell(BASELINE),
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
