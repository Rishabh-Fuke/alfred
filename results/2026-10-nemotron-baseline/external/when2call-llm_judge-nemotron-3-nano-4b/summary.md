## When2Call: nemotron-3-nano-4b

300 items, macro F1 0.65, accuracy 66% [60%-71%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 95% [91%-99%] | request_for_info 4, direct_answer 1 |
| Asked for missing information | 69% [60%-78%] | tool_call 31 |
| Said it can't when it can't | 33% [24%-42%] | request_for_info 34, tool_call 17, direct_answer 16 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |
