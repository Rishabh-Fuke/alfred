## When2Call: alfred-rl4 no-think

300 items, macro F1 0.70, accuracy 70% [65%-75%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 96% [92%-99%] | request_for_info 3, direct_answer 1 |
| Asked for missing information | 65% [55%-74%] | tool_call 34, cannot_answer 1 |
| Said it can't when it can't | 50% [40%-60%] | request_for_info 28, tool_call 17, direct_answer 5 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |
