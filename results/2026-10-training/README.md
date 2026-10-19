# October 2026: training rounds 1-3

The evidence behind [`RESULTS.md`](../../RESULTS.md): every evaluation run, training log and outside-benchmark summary
from the three rounds of training Nemotron 3 Nano 4B, run on one Colab A100 between Oct 10 and Oct 15.

| Folder | What's in it |
|---|---|
| `runs/<label>/` | Each eval run: `report.md`, `config.json`, `results.jsonl` (one line per episode, every metric) |
| `reports/round1`, `round2`, `round3` | Side-by-side reports, one per suite |
| `training/roundN-sft` | Fine-tuning arguments and per-step loss |
| `training/roundN-grpo` | GRPO arguments, per-step log (rewards, shortcut rates, groups with signal), validation, reward-hacking audit |
| `training/roundN-data` | How the training set was built: teacher episodes kept, scripted top-ups, per task type and interface |
| `external/` | When2Call, BFCL and AgentDojo summaries for every model; `external/report.md` has them all |

Run labels:

| Label | Model | Suites |
|---|---|---|
| `base-native*`, `base-json-heldout` | Untrained 4B (round 1's baseline) | Round 1's |
| `sft-native*`, `rl-native*` | Round 1 SFT and RL | Round 1's |
| `r2-base-*`, `r2-sft2-*`, `r2-rl2-*` | Untrained 4B, round 2 SFT and RL | The four Alfred suites |
| `r2-rl1-*` | Round 1 RL, graded on round 2's suites | The four Alfred suites |
| `r3-base-*`, `r3-rl2-*` | Untrained 4B and round 2 RL | The three app suites (their Alfred results are the `r2-` runs) |
| `r3-sft3-*`, `r3-rl3-*` | Round 3 SFT and RL | All seven suites |
| `tf-heldout-*` | Nemotron 3 Nano 30B and Super, on Token Factory | Held-out email tasks |
| `teacher-nano30b-apps*` | Round 3's teacher data (training worlds only) | App tasks |

Re-grading the round-2 runs with round 3's grader (made-up addresses rejected) changed no clean-success rate and moved
mean reward by at most 0.012, so they're kept as graded at the time. Runs from before round 3 show "n/a" for the
made-up-value metric, which wasn't measured yet.

Full conversations are in the GitHub release `results-2026-10-training`. To compare any runs side by side:

```bash
python -m alfred.report results/2026-10-training/runs/r2-rl2-text-heldout-real results/2026-10-training/runs/r3-rl3-text-heldout-real
```
