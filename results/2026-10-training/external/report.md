# Outside benchmarks

Benchmarks Alfred never trains on. Rates have 95% bootstrap intervals over items in brackets.

## When2Call: alfred-rl2 no-think

300 items, macro F1 0.51, accuracy 54% [48%-59%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 99% [97%-100%] | direct_answer 1 |
| Asked for missing information | 27% [18%-36%] | tool_call 73 |
| Said it can't when it can't | 36% [27%-45%] | tool_call 52, request_for_info 7, direct_answer 5 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## When2Call: alfred-rl3 no-think

300 items, macro F1 0.70, accuracy 70% [64%-75%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 97% [93%-100%] | request_for_info 2, direct_answer 1 |
| Asked for missing information | 57% [47%-67%] | tool_call 43 |
| Said it can't when it can't | 55% [45%-65%] | tool_call 21, request_for_info 18, direct_answer 6 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## When2Call: alfred-rl4 no-think

300 items, macro F1 0.70, accuracy 70% [65%-75%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 96% [92%-99%] | request_for_info 3, direct_answer 1 |
| Asked for missing information | 65% [55%-74%] | tool_call 34, cannot_answer 1 |
| Said it can't when it can't | 50% [40%-60%] | request_for_info 28, tool_call 17, direct_answer 5 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## When2Call: alfred-rl no-think

300 items, macro F1 0.25, accuracy 37% [32%-43%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 100% [100%-100%] | - |
| Asked for missing information | 3% [0%-7%] | tool_call 97 |
| Said it can't when it can't | 9% [4%-15%] | tool_call 80, direct_answer 6, request_for_info 5 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## When2Call: alfred-rl no-think

300 items, macro F1 0.26, accuracy 38% [32%-43%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 100% [100%-100%] | - |
| Asked for missing information | 3% [0%-7%] | tool_call 97 |
| Said it can't when it can't | 10% [4%-16%] | tool_call 80, direct_answer 6, request_for_info 4 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## When2Call: alfred-sft2 no-think

300 items, macro F1 0.44, accuracy 48% [42%-54%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 99% [97%-100%] | direct_answer 1 |
| Asked for missing information | 21% [13%-28%] | tool_call 79 |
| Said it can't when it can't | 25% [16%-35%] | tool_call 61, direct_answer 7, request_for_info 7 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## When2Call: alfred-sft3 no-think

300 items, macro F1 0.70, accuracy 70% [65%-75%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 97% [93%-100%] | request_for_info 2, direct_answer 1 |
| Asked for missing information | 58% [48%-68%] | tool_call 42 |
| Said it can't when it can't | 54% [45%-64%] | tool_call 22, request_for_info 20, direct_answer 4 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## When2Call: alfred-sft4 no-think

300 items, macro F1 0.69, accuracy 69% [64%-74%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 96% [91%-99%] | request_for_info 3, direct_answer 1 |
| Asked for missing information | 65% [55%-74%] | tool_call 35 |
| Said it can't when it can't | 47% [37%-57%] | request_for_info 28, tool_call 20, direct_answer 5 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## When2Call: alfred-sft no-think

300 items, macro F1 0.26, accuracy 38% [32%-43%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 100% [100%-100%] | - |
| Asked for missing information | 3% [0%-7%] | tool_call 97 |
| Said it can't when it can't | 11% [5%-18%] | tool_call 81, direct_answer 4, request_for_info 4 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## When2Call: alfred-sft no-think

300 items, macro F1 0.26, accuracy 38% [32%-44%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 100% [100%-100%] | - |
| Asked for missing information | 3% [0%-7%] | tool_call 97 |
| Said it can't when it can't | 11% [5%-17%] | tool_call 81, direct_answer 4, request_for_info 4 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## When2Call: nemotron-3-nano-4b

300 items, macro F1 0.65, accuracy 66% [60%-71%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 95% [91%-99%] | request_for_info 4, direct_answer 1 |
| Asked for missing information | 69% [60%-78%] | tool_call 31 |
| Said it can't when it can't | 33% [24%-42%] | request_for_info 34, tool_call 17, direct_answer 16 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## When2Call: nemotron-3-nano-4b no-think

300 items, macro F1 0.61, accuracy 62% [56%-67%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 94% [89%-98%] | request_for_info 4, direct_answer 2 |
| Asked for missing information | 59% [50%-69%] | tool_call 41 |
| Said it can't when it can't | 32% [23%-41%] | tool_call 35, request_for_info 21, direct_answer 12 |
| Called a tool when none was offered (lower is better) | 6% [0%-18%] | |

## When2Call: nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B

300 items, macro F1 0.66, accuracy 67% [61%-71%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 93% [88%-98%] | request_for_info 4, cannot_answer 2, direct_answer 1 |
| Asked for missing information | 69% [60%-78%] | tool_call 26, cannot_answer 4, direct_answer 1 |
| Said it can't when it can't | 38% [29%-47%] | request_for_info 32, tool_call 18, direct_answer 12 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## When2Call: nvidia/nemotron-3-super-120b-a12b

300 items, macro F1 0.60, accuracy 61% [55%-67%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 96% [92%-99%] | request_for_info 3, direct_answer 1 |
| Asked for missing information | 57% [47%-67%] | tool_call 43 |
| Said it can't when it can't | 30% [21%-39%] | request_for_info 31, tool_call 22, direct_answer 17 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |

## BFCL multi-turn: alfred-rl2 no-think

| Category | Accuracy |
|---|---|
| Multi-turn tasks with everything available (control) | 33% (66/200) |
| Missing detail: asked instead of guessing | 12% (24/200) |
| Missing tool: said so instead of forcing a call | 6% (13/200) |

## BFCL multi-turn: alfred-rl3 no-think

| Category | Accuracy |
|---|---|
| Multi-turn tasks with everything available (control) | 38% (76/200) |
| Missing detail: asked instead of guessing | 22% (44/200) |
| Missing tool: said so instead of forcing a call | 14% (27/200) |

## BFCL multi-turn: alfred-rl4 no-think

| Category | Accuracy |
|---|---|
| Multi-turn tasks with everything available (control) | 39% (78/200) |
| Missing detail: asked instead of guessing | 28% (57/200) |
| Missing tool: said so instead of forcing a call | 18% (37/200) |

## BFCL multi-turn: alfred-rl no-think

| Category | Accuracy |
|---|---|
| Multi-turn tasks with everything available (control) | 0% (1/200) |
| Missing detail: asked instead of guessing | 0% (0/200) |
| Missing tool: said so instead of forcing a call | 0% (0/200) |

## BFCL multi-turn: alfred-rl no-think

| Category | Accuracy |
|---|---|
| Multi-turn tasks with everything available (control) | 0% (1/200) |
| Missing detail: asked instead of guessing | 0% (0/200) |
| Missing tool: said so instead of forcing a call | 0% (0/200) |

## BFCL multi-turn: nemotron-3-nano-4b no-think

| Category | Accuracy |
|---|---|
| Multi-turn tasks with everything available (control) | 34% (68/200) |
| Missing detail: asked instead of guessing | 23% (46/200) |
| Missing tool: said so instead of forcing a call | 20% (41/200) |

## AgentDojo workspace: alfred-rl2 no-think

| Metric | Rate |
|---|---|
| Did the user's task (no attack) | 52% [38%-68%] |
| Did the user's task while under attack | 40% [36%-44%] |
| Carried out the attacker's goal (`important_instructions_no_names`; lower is better) | 6% [4%-8%] |
Note: 9 episodes ran past the context window and count as failures.

## AgentDojo workspace: alfred-rl3 no-think

| Metric | Rate |
|---|---|
| Did the user's task (no attack) | 50% [35%-65%] |
| Did the user's task while under attack | 45% [41%-49%] |
| Carried out the attacker's goal (`important_instructions_no_names`; lower is better) | 3% [1%-4%] |
Note: 2 episodes ran past the context window and count as failures.

## AgentDojo workspace: alfred-rl4 no-think

| Metric | Rate |
|---|---|
| Did the user's task (no attack) | 55% [40%-70%] |
| Did the user's task while under attack | 41% [38%-46%] |
| Carried out the attacker's goal (`important_instructions_no_names`; lower is better) | 4% [3%-6%] |
Note: 4 episodes ran past the context window and count as failures.

## AgentDojo workspace: nemotron-3-nano-4b no-think

| Metric | Rate |
|---|---|
| Did the user's task (no attack) | 45% [30%-60%] |
| Did the user's task while under attack | 42% [38%-47%] |
| Carried out the attacker's goal (`important_instructions_no_names`; lower is better) | 4% [3%-6%] |
