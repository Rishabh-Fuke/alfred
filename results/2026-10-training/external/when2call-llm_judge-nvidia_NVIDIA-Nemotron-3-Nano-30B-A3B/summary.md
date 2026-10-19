## When2Call: nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B

300 items, macro F1 0.66, accuracy 67% [61%-71%].

| Behavior | Rate | What it did instead |
|---|---|---|
| Called a tool when it should | 93% [88%-98%] | request_for_info 4, cannot_answer 2, direct_answer 1 |
| Asked for missing information | 69% [60%-78%] | tool_call 26, cannot_answer 4, direct_answer 1 |
| Said it can't when it can't | 38% [29%-47%] | request_for_info 32, tool_call 18, direct_answer 12 |
| Called a tool when none was offered (lower is better) | 0% [0%-0%] | |
