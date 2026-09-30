#!/bin/bash
# Capability test: can laptop-sized open models do Alfred's tasks reliably?
#
# Everything runs on this Mac:
#   1. checks memory, disk, Ollama and Python, and asks for an OpenAI key (optional, for the reference run)
#   2. installs Alfred in its own virtual environment
#   3. starts a private Ollama server with a 16k context window on port 11435 (the Ollama app is left alone)
#   4. downloads the local models that fit in this Mac's memory
#   5. reference run: 45 tasks x 1 try with an OpenAI model, so there's a "what good looks like" line
#   6. smoke test: 9 tasks per local model, with native tool calling and with JSON tool calls
#   7. main run: 45 tasks x 3 tries per local model, in whichever tool mode worked better
#   8. writes report.md, explorer.html and results.zip into ../results
#
# Safe to stop (Ctrl-C) and rerun: finished episodes are kept and skipped.
#
# Usage:   bash scripts/run_capability_test.sh
# Options: MODELS="qwen3:8b gpt-oss:20b"  WORLDS=5  SAMPLES=3  SKIP_REFERENCE=1  REF_MODEL=gpt-5.4
#          REF_BASE_URL (any OpenAI-compatible API; default https://api.openai.com/v1)
set -euo pipefail

ALFRED_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$(dirname "$ALFRED_DIR")/results}"
PORT="${PORT:-11435}"
WORLDS="${WORLDS:-5}"
SAMPLES="${SAMPLES:-3}"
REF_BASE_URL="${REF_BASE_URL:-https://api.openai.com/v1}"
TASKS=$((WORLDS * 9))
export PYTHONUNBUFFERED=1
mkdir -p "$OUT/runs"
exec > >(tee -a "$OUT/progress.log") 2>&1

step() { printf '\n==> [%s] %s\n' "$(date '+%H:%M')" "$*"; }
slug() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }
progress() { grep --line-buffered -E '^\[ *[0-9]+/|episodes \(|^! ' || true; }
latest_run() { ls -d "$OUT"/runs/*-$1 2>/dev/null | sort | tail -1 || true; }  # $1 may be a glob
rows_in() { if [ -n "$1" ] && [ -f "$1/results.jsonl" ]; then wc -l <"$1/results.jsonl" | tr -d ' '; else echo 0; fi; }

# ---------------------------------------------------------------------------- 1. checks
step "Checking this Mac"
RAM_GB=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 17179869184) / 1073741824 ))
FREE_GB=$(df -k "$HOME" | awk 'NR==2 {print int($4 / 1048576)}')
echo "Memory: ${RAM_GB} GB. Free disk: ${FREE_GB} GB."
if [ -z "${MODELS:-}" ]; then
  if [ "$RAM_GB" -ge 30 ]; then MODELS="qwen3:8b gpt-oss:20b qwen3:30b"
  elif [ "$RAM_GB" -ge 15 ]; then MODELS="qwen3:8b gpt-oss:20b"
  else MODELS="qwen3:4b qwen3:8b"; fi
fi
echo "Local models to test: $MODELS"
[ "$FREE_GB" -ge 45 ] || echo "Warning: the models can take up to about 40 GB of disk."

OLLAMA=""
if command -v ollama >/dev/null 2>&1; then
  OLLAMA="ollama"
elif [ -x /Applications/Ollama.app/Contents/Resources/ollama ]; then
  OLLAMA="/Applications/Ollama.app/Contents/Resources/ollama"
elif command -v brew >/dev/null 2>&1; then
  step "Installing Ollama with Homebrew"
  brew install ollama
  OLLAMA="ollama"
else
  echo "Ollama isn't installed. Get it from https://ollama.com/download, open it once, then run this again."
  exit 1
fi
OLLAMA_VERSION=$("$OLLAMA" --version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | tail -1 || true)
echo "Ollama: ${OLLAMA_VERSION:-unknown version}"
case "$OLLAMA_VERSION" in
  0.[0-5].*)
    echo "This Ollama is too old to set the context window, so long episodes would get cut off."
    echo "Update it (brew upgrade ollama, or download the latest from https://ollama.com/download) and run this again."
    exit 1 ;;
esac

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9 or newer is needed. Install it with 'brew install python' or from python.org, then run this again."
  exit 1
fi
echo "Python: $("$PY" --version 2>&1)"

REF_KEY="${OPENAI_API_KEY:-}"
REF_DONE=0
prev_ref=$(latest_run "reference-*")
if [ "$(rows_in "$prev_ref")" -ge "$TASKS" ]; then REF_DONE=1; fi
if [ -z "$REF_KEY" ] && [ "$REF_DONE" = 0 ] && [ "${SKIP_REFERENCE:-0}" != "1" ]; then
  printf '\nPaste your OpenAI API key for the reference run and press Enter.\n'
  printf 'It stays hidden, is kept only in memory, and is sent only to the OpenAI API. Press Enter alone to skip: '
  read -rs REF_KEY || true
  echo
fi

# ---------------------------------------------------------------------------- 2. install
step "Installing Alfred"
cd "$ALFRED_DIR"
if [ -d .venv ] || "$PY" -m venv .venv >/dev/null 2>&1; then
  . .venv/bin/activate
else
  echo "Couldn't create a virtual environment here (common on Colab); installing into this Python instead."
  rm -rf .venv
  python() { "$PY" "$@"; }
  NO_VENV=1
fi
if [ -z "${NO_VENV:-}" ]; then
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -e ".[dev]"
else
  python -m pip install --quiet -e ".[dev]" 2>/dev/null \
    || python -m pip install --quiet --break-system-packages -e ".[dev]"
fi
python -m alfred.eval --agent oracle --worlds 1 --out "$OUT/runs" --label oracle-check \
  | grep -E '^\| (Clean success|Success) \|' || true

# ---------------------------------------------------------------------------- 3. private Ollama server
step "Starting a private Ollama server on port $PORT with a 16k context window"
OLLAMA_HOST="127.0.0.1:$PORT" OLLAMA_CONTEXT_LENGTH=16384 OLLAMA_KEEP_ALIVE=30m \
  "$OLLAMA" serve >"$OUT/ollama-server.log" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
up=0
for _ in $(seq 1 60); do
  if curl -s "http://127.0.0.1:$PORT/api/version" >/dev/null 2>&1; then up=1; break; fi
  sleep 1
done
if [ "$up" != 1 ]; then echo "The Ollama server didn't start. See $OUT/ollama-server.log"; exit 1; fi
BASE="http://127.0.0.1:$PORT/v1"
if command -v caffeinate >/dev/null 2>&1; then caffeinate -i -w $$ >/dev/null 2>&1 & fi  # stay awake while running

# ---------------------------------------------------------------------------- 4. models
for m in $MODELS; do
  step "Downloading $m (skipped if it's already on this Mac)"
  OLLAMA_HOST="127.0.0.1:$PORT" "$OLLAMA" pull "$m"
done

# ---------------------------------------------------------------------------- 5. reference
if [ "$REF_DONE" = 1 ]; then
  echo "Reference run already finished: $prev_ref"
elif [ -n "$REF_KEY" ]; then
  REF_MODEL="${REF_MODEL:-$(OPENAI_API_KEY="$REF_KEY" python -m alfred.capability pick-openai-model)}" || REF_MODEL=""
  if [ -n "$REF_MODEL" ]; then
    step "Reference run: $REF_MODEL, $TASKS tasks x 1 try"
    resume=()
    prev=$(latest_run "reference-$(slug "$REF_MODEL")")
    if [ -n "$prev" ]; then resume=(--resume "$prev"); fi
    OPENAI_API_KEY="$REF_KEY" python -m alfred.eval --models "$REF_MODEL" --base-url "$REF_BASE_URL" \
      --worlds "$WORLDS" --samples 1 --workers 4 --out "$OUT/runs" --label "reference-$(slug "$REF_MODEL")" \
      ${resume[@]+"${resume[@]}"} | progress || true
  else
    echo "Skipping the reference run."
  fi
fi
REF_KEY=""

# ---------------------------------------------------------------------------- 6. smoke test
step "Smoke test: 9 tasks per model, native tool calling vs JSON"
MODE_FILE="$OUT/tool-modes.txt"
touch "$MODE_FILE"
for m in $MODELS; do
  s=$(slug "$m")
  if grep -q "^$m " "$MODE_FILE"; then
    echo "$m: already tested, using $(awk -v m="$m" '$1 == m {print $2}' "$MODE_FILE") tool calls."
    continue
  fi
  for mode in native json; do
    echo "$m, $mode tool calls..."
    env -u OPENAI_API_KEY python -m alfred.eval --models "$m" --base-url "$BASE" --world-start 1100 --worlds 1 \
      --tool-mode "$mode" --out "$OUT/runs" --label "smoke-$s-$mode" >"$OUT/smoke-$s-$mode.log" 2>&1 || true
  done
  mode=$(python -m alfred.capability pick-mode "$OUT/runs" "smoke-$s")
  echo "$m $mode" >>"$MODE_FILE"
  echo "$m will use $mode tool calls."
  OLLAMA_HOST="127.0.0.1:$PORT" "$OLLAMA" stop "$m" >/dev/null 2>&1 || true
done

# ---------------------------------------------------------------------------- 7. main run
for m in $MODELS; do
  s=$(slug "$m")
  mode=$(awk -v m="$m" '$1 == m {print $2}' "$MODE_FILE")
  step "Main run: $m (${mode:-native} tool calls), $TASKS tasks x $SAMPLES tries"
  resume=()
  prev=$(latest_run "main-$s")
  if [ -n "$prev" ]; then resume=(--resume "$prev"); echo "Continuing $prev"; fi
  env -u OPENAI_API_KEY python -m alfred.eval --models "$m" --base-url "$BASE" --tool-mode "${mode:-native}" \
    --worlds "$WORLDS" --samples "$SAMPLES" --temperature 0.7 --out "$OUT/runs" --label "main-$s" \
    ${resume[@]+"${resume[@]}"} | progress || true
  OLLAMA_HOST="127.0.0.1:$PORT" "$OLLAMA" stop "$m" >/dev/null 2>&1 || true
done

# ---------------------------------------------------------------------------- 8. report
step "Writing the report"
RUNS=()
for d in "$OUT"/runs/*-reference-* "$OUT"/runs/*-main-*; do
  if [ -f "$d/results.jsonl" ]; then RUNS+=("$d"); fi
done
if [ "${#RUNS[@]}" -eq 0 ]; then echo "No finished runs to report on. See $OUT/progress.log"; exit 1; fi
python -m alfred.report "${RUNS[@]}" --out "$OUT/report.md" >/dev/null
python -m alfred.viewer "${RUNS[@]}" --out "$OUT/explorer.html" >/dev/null
(cd "$OUT" && rm -f results.zip && zip -qr results.zip runs report.md tool-modes.txt progress.log)
step "Done"
sed -n '/## Reliability by category/,/## All metrics/p' "$OUT/report.md" | sed '$d'
echo "Full report: $OUT/report.md"
echo "Explorer:    $OUT/explorer.html"
echo "To share:    $OUT/results.zip"
