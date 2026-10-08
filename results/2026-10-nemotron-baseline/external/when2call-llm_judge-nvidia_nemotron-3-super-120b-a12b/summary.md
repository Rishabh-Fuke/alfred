## When2Call: nvidia/nemotron-3-super-120b-a12b

300 items, macro F1 0.60, accuracy 61% [55%-67%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 96% [92%-99%] | request_for_info 3, direct_answer 1 |
| Asked for missing information | 57% [47%-67%] | tool_call 43 |
| Said it can't when it can't | 30% [21%-39%] | request_for_info 31, tool_call 22, direct_answer 17 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |
