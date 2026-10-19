## When2Call: alfred-sft2 no-think

300 items, macro F1 0.44, accuracy 48% [42%-54%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 99% [97%-100%] | direct_answer 1 |
| Asked for missing information | 21% [13%-28%] | tool_call 79 |
| Said it can't when it can't | 25% [16%-35%] | tool_call 61, direct_answer 7, request_for_info 7 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |
