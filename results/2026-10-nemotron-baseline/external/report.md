# Outside benchmarks

Benchmarks Alfred never trains on. Rates have 95% bootstrap intervals over items in brackets.

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

## BFCL multi-turn: nemotron-3-nano-4b no-think

| Category | Accuracy |
|---|---|
| Multi-turn tasks with everything available (control) | 34% (68/200) |
| Missing detail: asked instead of guessing | 23% (46/200) |
| Missing tool: said so instead of forcing a call | 20% (41/200) |

## AgentDojo workspace: nemotron-3-nano-4b no-think

| Metric | Rate |
|---|---|
| Did the user's task (no attack) | 45% [30%-60%] |
| Did the user's task while under attack | 42% [38%-47%] |
| Carried out the attacker's goal (`important_instructions_no_names`; lower is better) | 4% [3%-6%] |
