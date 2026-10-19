## When2Call: alfred-sft no-think

300 items, macro F1 0.26, accuracy 38% [32%-44%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 100% [100%-100%] | - |
| Asked for missing information | 3% [0%-7%] | tool_call 97 |
| Said it can't when it can't | 11% [5%-17%] | tool_call 81, direct_answer 4, request_for_info 4 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |
