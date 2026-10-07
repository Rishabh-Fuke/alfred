"""Outside benchmarks, run against the same OpenAI-compatible endpoints as alfred.eval.

These are tests only: never train on them, so they stay independent evidence that training taught judgment and
not the quirks of Alfred's own environment.

    when2call        NVIDIA When2Call: call a tool, ask a follow-up, or say it can't (LLM-as-judge protocol)
    agentdojo_bench  AgentDojo workspace suite: prompt injections in email, calendar and files
    bfcl_bench       BFCL multi-turn: base, missing parameters (should ask), missing functions (should decline)
    summary          one report from every summary.md under a folder

when2call needs only this package. The other two need `pip install -e ".[external]"`, ideally in a separate
virtual environment from vLLM, since they pin their own dependencies.
"""
