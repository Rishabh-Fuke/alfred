## When2Call: alfred-rl2 no-think

300 items, macro F1 0.51, accuracy 54% [48%-59%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 99% [97%-100%] | direct_answer 1 |
| Asked for missing information | 27% [18%-36%] | tool_call 73 |
| Said it can't when it can't | 36% [27%-45%] | tool_call 52, request_for_info 7, direct_answer 5 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |
