## When2Call: alfred-sft4 no-think

300 items, macro F1 0.69, accuracy 69% [64%-74%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 96% [91%-99%] | request_for_info 3, direct_answer 1 |
| Asked for missing information | 65% [55%-74%] | tool_call 35 |
| Said it can't when it can't | 47% [37%-57%] | request_for_info 28, tool_call 20, direct_answer 5 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |
