# October 2026 baseline: NVIDIA Nemotron models

The same 45 held-out tasks as the September baseline (worlds 1000-1004), 3 tries each, temperature 0.6 and
top_p 0.95 (NVIDIA's recommended settings for tool calling), graded by the fixed grader.

| Run | Model | Served by | Tool calls | Reasoning |
|---|---|---|---|---|
| `main-nemotron4b-nothink` | Nemotron 3 Nano 4B (BF16) | vLLM on a Colab A100 | JSON (picked by the smoke test) | off |
| `main-nemotron4b-think` | Nemotron 3 Nano 4B (BF16) | vLLM on a Colab A100 | JSON | on |
| `tf-nvidia-nemotron-3-nano-30b-a3b` | Nemotron 3 Nano 30B-A3B | Nebius Token Factory | native | default (on) |
| `tf-nemotron-3-super-120b-a12b` | Nemotron 3 Super 120B-A12B | Nebius Token Factory | native | default (on) |

`report.md` here compares these with the re-graded September runs in `../2026-09-baseline/`.

What stood out, from reading the failed trajectories:

- **The 4B model fails ordinary tasks too, not only judgment ones.** Most failures are tool-use mistakes it
  doesn't recover from: putting a contact ID where an email address goes (or into `reply_to_id`), then giving up
  with "I need her email address" although the search result showed it; resolving "this Wednesday" to the wrong
  date; running out of steps.
- **Reasoning on helps tool use but hurts judgment.** With reasoning on, the 4B model made far fewer invalid calls
  but asked on only 13% of ambiguous requests (53% with reasoning off) and claimed "done" falsely 16% of the time.
- **Nemotron 3 Super is an excellent teacher for everything except ambiguity.** 94% clean success and 100% on
  impossible requests, but it guessed first on 7 of 15 ambiguous requests and asked only after the user rejected
  the wrong recipient. Distilling from it needs filtering: keep only episodes that asked before drafting.
- A few episodes asked in plain text instead of calling `ask_user` (3 of 15 ambiguous episodes for the 4B model
  with reasoning on, 2 for Nano 30B), which the grader counts as not asking.
