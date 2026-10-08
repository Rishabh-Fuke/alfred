"""Training Alfred: supervised fine-tuning, then reinforcement learning against the environment.

The pipeline, each step a module you can run with `python -m alfred.train.<module>`:

1. data   Build the supervised dataset: a teacher model's episodes on training worlds, kept only when they were
          clean successes (and, for ambiguous requests, only when the teacher asked before drafting), plus scripted
          episodes. Every example is a conversation in the OpenAI chat format with the tool schemas.
2. sft    LoRA fine-tuning on those conversations. The loss covers only the assistant's turns, rendered with the
          model's own chat template exactly as the server renders them at inference.
3. grpo   Reinforcement learning with verifiable rewards: the policy plays episodes through a vLLM server, the
          environment scores each one (alfred.reward.compute_reward), and a group-relative policy gradient pushes
          up what scored above its group's average. The new LoRA weights are loaded into vLLM after every step.
4. serve  Start and stop the vLLM server, and load adapters into it.
5. export Merge an adapter into the base model, for serving it anywhere (and converting to GGUF for laptops).

World seeds are split three ways (alfred.tasks): training data and RL use worlds 0-949, checkpoints are picked on
950-999, and every reported number comes from worlds 1000 and up.

The training modules need PyTorch, transformers and peft (`pip install -e ".[train]"`); data building doesn't.
"""
