## When2Call: alfred-rl3 no-think

300 items, macro F1 0.70, accuracy 70% [64%-75%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 97% [93%-100%] | request_for_info 2, direct_answer 1 |
| Asked for missing information | 57% [47%-67%] | tool_call 43 |
| Said it can't when it can't | 55% [45%-65%] | tool_call 21, request_for_info 18, direct_answer 6 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |
