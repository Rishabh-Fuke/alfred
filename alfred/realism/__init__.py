"""Realistic worlds: text written in the style of real mail, checked by code before it's used.

A content pack (pack.py) is a file of text templates, each of one *kind* (kinds.py): a receipt, a colleague
asking to move a meeting, a newsletter, a user's request, background mail. Templates have {slots} that the world
generator fills with its own facts, so the facts every task is graded on still come from code. An LLM writes the
templates once (build.py), in the style of sampled Enron emails (enron.py) without copying them, and every
template passes the same validators before it's kept.

`World.generate(seed, style="realistic")` uses the pack. The default style, "base", ignores it, so every
existing world, task and score stays exactly as it was.
"""
