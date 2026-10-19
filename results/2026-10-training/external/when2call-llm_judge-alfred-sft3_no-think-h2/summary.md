## When2Call: alfred-sft3 no-think

300 items, macro F1 0.70, accuracy 70% [65%-75%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 97% [93%-100%] | request_for_info 2, direct_answer 1 |
| Asked for missing information | 58% [48%-68%] | tool_call 42 |
| Said it can't when it can't | 54% [45%-64%] | tool_call 22, request_for_info 20, direct_answer 4 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |
