## When2Call: nemotron-3-nano-4b no-think

300 items, macro F1 0.61, accuracy 62% [56%-67%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 94% [89%-98%] | request_for_info 4, direct_answer 2 |
| Asked for missing information | 59% [50%-69%] | tool_call 41 |
| Said it can't when it can't | 32% [23%-41%] | tool_call 35, request_for_info 21, direct_answer 12 |
| Called a tool when none was offered (lower is better) | 6% [0%-18%] | |
