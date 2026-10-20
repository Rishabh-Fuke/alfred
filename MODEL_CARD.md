---
license: other
license_name: alfred-4b-evaluation-license
license_link: LICENSE
base_model: nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16
extra_gated_heading: Access Alfred 4B for non-commercial evaluation
extra_gated_prompt: >-
  Alfred 4B is shared as evidence for the results in https://github.com/Rishabh-Fuke/alfred. Under the Alfred 4B
  Evaluation License (LICENSE in this repository) you may download and run it privately to evaluate it and reproduce
  its results, non-commercially, and publish your evaluation results with credit. You may not use it commercially,
  modify it or make derivatives (fine-tuning, merging, distilling, training on its outputs), or share, publish or
  host it or any derivative. It is also governed by the NVIDIA Nemotron Open Model License (LICENSE-NVIDIA).
extra_gated_fields:
  I will use Alfred 4B only for non-commercial evaluation: checkbox
  I will not redistribute it or make or share derivatives of it: checkbox
extra_gated_button_content: Agree and access
language:
- en
pipeline_tag: text-generation
library_name: transformers
tags:
- tool-use
- function-calling
- agents
- reinforcement-learning
- grpo
---

# Alfred 4B

A 4B-parameter assistant model trained for **judgment in tool use**: call a tool when everything it needs was said,
ask when a required detail is missing, look things up instead of guessing, and say "I can't" when no tool fits.
Fine-tuned from NVIDIA Nemotron 3 Nano 4B with teacher distillation and reinforcement learning with verifiable
rewards (GRPO) in [Alfred](https://github.com/Rishabh-Fuke/alfred), a sandboxed environment that simulates a person's
email, calendar and contacts, plus twelve procedurally generated apps.

Small enough to run on a laptop: a 4-bit GGUF is in [alfred-4b-GGUF](__GGUF_REPO__). Shared for **non-commercial
evaluation only** (see [License](#license)); access is granted automatically once you accept the terms.

## Results

Our own runs, reasoning off. Everything below recomputes from the evidence in the
[GitHub repo](https://github.com/Rishabh-Fuke/alfred) (`python scripts/verify_results.py`), and the full write-up is
[RESULTS.md](https://github.com/Rishabh-Fuke/alfred/blob/main/RESULTS.md).

| Measure | Nemotron 3 Nano 4B (base) | **Alfred 4B** | Nemotron 3 Super 120B |
|---|---|---|---|
| When2Call macro F1 | 0.61 | **0.70** | 0.60 |
| When2Call: asked for missing information | 59% | **65%** | 57% |
| When2Call: said "I can't" when no tool fits | 32% | **50%** | 30% |
| When2Call: called a tool when it should | 94% | 96% | 96% |
| BFCL multi-turn: asked for a missing detail | 23% | **28%** | |
| BFCL multi-turn: said a tool was missing | 20% | 18% | |
| Apps never trained on, clean success (tools) | 68% | **99%** | |
| Made-up tool arguments on new tasks | 23% | **4%** | |

On When2Call's 300 items, against Nemotron 3 Super on the same items and the same judge (paired bootstrap, 95%):
macro F1 +0.10 [+0.06, +0.15], asking +8 points [+1, +15], saying "I can't" +20 points [+12, +29]. The base model
and Super were run with their own settings (Super with reasoning on); Alfred 4B with reasoning off.

## Use

Turn reasoning off, as in training and every evaluation: pass `chat_template_kwargs: {"enable_thinking": false}`.
NVIDIA's recommended sampling for tool calling is temperature 0.6, top-p 0.95.

With vLLM, using NVIDIA's recommended flags for Nemotron 3 Nano (the reasoning parser plugin is
`nano_v3_reasoning_parser.py` from the base model's repository):

```bash
vllm serve __MODEL_REPO__ --trust-remote-code --mamba_ssm_cache_dtype float32 \
    --enable-auto-tool-choice --tool-call-parser qwen3_coder \
    --reasoning-parser-plugin nano_v3_reasoning_parser.py --reasoning-parser nano_v3
```

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="local")
reply = client.chat.completions.create(
    model="__MODEL_REPO__", messages=[{"role": "user", "content": "Find me a pharmacy."}], tools=[...],
    temperature=0.6, extra_body={"top_p": 0.95, "chat_template_kwargs": {"enable_thinking": False}})
```

With llama.cpp: `llama-server -m alfred-4b-Q4_K_M.gguf --jinja`, and the same `chat_template_kwargs` in each request.
The 4-bit GGUF scores within the margin of the full model on the same tasks: 100% vs 99% clean success on trained tasks with tools, 97% vs 98% with plain replies, and 97% vs 100% on apps never trained on.

## Training

- **Base:** `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16`, LoRA rank 32 on the attention and MLP projections, merged.
- **Distillation:** 3,900 conversations across 20 task types, in a tool interface and a plain-text one: clean
  successes of Nemotron 3 Nano 30B as a teacher (71%), topped up with scripted correct episodes.
- **RL:** GRPO, 8 attempts per task, rewards computed by code from what happened (no judge model): a task done earns
  reward; made-up values, unneeded or missing questions, unrequested actions, leaks and acting on prompt injections
  cost it. 60 steps; the best checkpoint by validation reward was kept.
- **Data:** synthetic. Email text was written by Nemotron 3 Super with real emails from the Enron corpus as style
  references only. No data from When2Call, BFCL or AgentDojo, or their tool sets, was used in training.

## Limitations

- Asked for a two-part answer (a meeting's time and place), it can answer from a search result and never open the
  record that has the second part.
- It recovers poorly after a user rejects a draft, sometimes repeating the same search.
- Turning a weekday into a date ("Friday") is unreliable.
- It declines and then offers alternatives, which some benchmarks count as asking.
- English only; evaluated on synthetic tasks and three public benchmarks. It is not a substitute for a human
  approving actions: in Alfred, every action is a draft the user approves.

## License

**Evaluation only.** The weights are licensed under the Alfred 4B Evaluation License (`LICENSE` in this repository):
you may download and run them privately to evaluate the model and reproduce its results, non-commercially, and
publish your evaluation results with credit. You may not use them commercially, make derivatives (fine-tuning,
merging, distilling, training on outputs), or redistribute, publish or host them or any derivative. As a derivative of
NVIDIA Nemotron 3 Nano 4B they are also governed by the
[NVIDIA Nemotron Open Model License](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/)
(`LICENSE-NVIDIA`). NVIDIA and Nemotron are trademarks of NVIDIA Corporation. The training and evaluation code is
separate, under AGPL-3.0, at [github.com/Rishabh-Fuke/alfred](https://github.com/Rishabh-Fuke/alfred).
