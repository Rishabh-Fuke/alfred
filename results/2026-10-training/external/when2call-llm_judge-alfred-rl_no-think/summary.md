## When2Call: alfred-rl no-think

300 items, macro F1 0.25, accuracy 37% [32%-43%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 100% [100%-100%] | - |
| Asked for missing information | 3% [0%-7%] | tool_call 97 |
| Said it can't when it can't | 9% [4%-15%] | tool_call 80, direct_answer 6, request_for_info 5 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |
