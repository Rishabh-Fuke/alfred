#!/usr/bin/env bash
# Baseline sweep of laptop-class models through Ollama.
#
#   OLLAMA_CONTEXT_LENGTH=16384 ollama serve      # first, in another terminal
#   bash scripts/baseline_laptop.sh               # full sweep: several hours
#   QUICK=1 bash scripts/baseline_laptop.sh       # 1 sample, 27 tasks: roughly an hour
#   MODELS="qwen3:8b,gpt-oss:20b" bash scripts/baseline_laptop.sh
set -euo pipefail

MODELS="${MODELS:-qwen3:8b,gpt-oss:20b,gemma4:26b,qwen3:30b}"
if [[ "${QUICK:-0}" == "1" ]]; then
  WORLDS=3; SAMPLES=1; TEMP=0
else
  WORLDS=5; SAMPLES=3; TEMP=0.7
fi

for m in ${MODELS//,/ }; do
  ollama pull "$m" || echo "Couldn't pull $m. The eval will skip it."
done

python -m alfred.eval --models "$MODELS" --prompt plain,privacy \
  --worlds "$WORLDS" --samples "$SAMPLES" --temperature "$TEMP" --label laptop-baseline "$@"
