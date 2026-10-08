"""LoRA supervised fine-tuning on conversations built by alfred.train.data.

    python -m alfred.train.sft --model nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 --data data/sft.jsonl --out out/sft

The loss is the mean negative log-likelihood of the assistant's tokens over a batch, so every token counts the
same and a long episode isn't down-weighted against a short one. Each conversation is one forward pass (no
padding or packing, which hybrid Mamba layers don't handle cleanly), and gradients accumulate over --batch
conversations per optimizer step.

Writes out/adapter (the LoRA adapter, loadable by vLLM with --lora-modules), out/log.jsonl, and out/checkpoint
for --resume after a disconnect.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import torch

from .model import (LORA_TARGETS, Schedule, add_lora, load_checkpoint, load_model, load_tokenizer, save_checkpoint,
                    token_logprobs, trainable_parameters)
from .render import TemplateMismatch, check_prompt_tokens, render


def load_examples(path: Path, limit: int = 0) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows[:limit] if limit else rows


def render_examples(tok, examples: list[dict], max_len: int) -> tuple[list, dict]:
    out, skipped = [], {"template mismatch": 0, "nothing to train": 0, "truncated": 0}
    for ex in examples:
        try:
            r = render(tok, ex["messages"], ex.get("tools"), ex.get("train"), max_len=max_len)
        except TemplateMismatch:
            skipped["template mismatch"] += 1
            continue
        if r is None:
            skipped["nothing to train"] += 1
            continue
        skipped["truncated"] += r.truncated
        out.append(r)
    return out, skipped


@torch.no_grad()
def evaluate(model, data) -> float:
    model.eval()
    total, n = 0.0, 0
    for r in data:
        lp = token_logprobs(model, r.input_ids, r.labels)
        total += -lp.sum().item()
        n += lp.numel()
    model.train()
    return total / max(1, n)


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.train.sft", description="LoRA SFT on Alfred conversations.")
    p.add_argument("--model", required=True, help="Base model (Hugging Face id or local path).")
    p.add_argument("--data", required=True, help="Training .jsonl from alfred.train.data.")
    p.add_argument("--val", default="", help="Validation .jsonl (default: the .val.jsonl next to --data).")
    p.add_argument("--out", required=True)
    p.add_argument("--epochs", type=float, default=2.0)
    p.add_argument("--batch", type=int, default=16, help="Conversations per optimizer step.")
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--warmup", type=float, default=0.05, help="Share of steps spent warming up.")
    p.add_argument("--lora-r", type=int, default=32)
    p.add_argument("--lora-alpha", type=int, default=64)
    p.add_argument("--lora-dropout", type=float, default=0.0)
    p.add_argument("--lora-targets", default=",".join(LORA_TARGETS),
                   help="Linear layer names to adapt (those missing from the model are skipped).")
    p.add_argument("--max-len", type=int, default=12288, help="Tokens per conversation; longer ones are cut.")
    p.add_argument("--eval-every", type=int, default=25)
    p.add_argument("--save-every", type=int, default=25)
    p.add_argument("--limit", type=int, default=0, help="Use only the first N examples (for a quick check).")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--trust-remote-code", action="store_true")
    p.add_argument("--no-kernels", action="store_true", help="Don't use Hugging Face hub kernels for Mamba layers.")
    p.add_argument("--no-gradient-checkpointing", action="store_true")
    p.add_argument("--resume", action="store_true", help="Continue from out/checkpoint if it exists.")
    return p.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(a.seed)

    tok = load_tokenizer(a.model, a.trust_remote_code)
    data_path = Path(a.data)
    val_path = Path(a.val) if a.val else data_path.with_suffix(".val.jsonl")
    t0 = time.time()
    train, skipped = render_examples(tok, load_examples(data_path, a.limit), a.max_len)
    val, _ = render_examples(tok, load_examples(val_path, a.limit) if val_path.exists() else [], a.max_len)
    if not train:
        sys.exit(f"No usable training examples ({skipped}).")
    for ex in load_examples(data_path, 3):  # tokenizing a prompt in one go must give the same tokens vLLM sees
        bad = check_prompt_tokens(tok, ex["messages"], ex.get("tools"))
        if bad:
            print(f"WARNING: {ex['id']}: prompt tokens differ from the training sequence at messages {bad}")
    n_tokens = sum(len(r.input_ids) for r in train)
    print(f"{len(train)} training conversations ({n_tokens:,} tokens, {sum(r.n_train for r in train):,} trained), "
          f"{len(val)} validation; skipped {skipped}; rendered in {time.time() - t0:.0f}s")

    model = add_lora(load_model(a.model, use_kernels=not a.no_kernels, trust_remote_code=a.trust_remote_code,
                                gradient_checkpointing=not a.no_gradient_checkpointing),
                     r=a.lora_r, alpha=a.lora_alpha, dropout=a.lora_dropout,
                     targets=tuple(t.strip() for t in a.lora_targets.split(",") if t.strip()))
    params = trainable_parameters(model)
    print(f"LoRA: {sum(p.numel() for p in params):,} trainable parameters on "
          f"{', '.join(model.peft_config['default'].target_modules)}")
    opt = torch.optim.AdamW(params, lr=a.lr, betas=(0.9, 0.999), weight_decay=0.0)
    per_epoch = -(-len(train) // a.batch)
    total = max(1, int(round(a.epochs * per_epoch)))
    sched = Schedule(opt, total, int(a.warmup * total))

    step = 0
    if a.resume and (out / "checkpoint" / "state.json").exists():
        step = load_checkpoint(model, opt, sched, out / "checkpoint")["step"]
        print(f"Resumed at step {step} of {total}")
    (out / "args.json").write_text(json.dumps({**vars(a), "train_examples": len(train), "steps": total}, indent=1))

    model.train()
    with (out / "log.jsonl").open("a") as log:
        if step == 0 and val:
            rec = {"step": 0, "val_loss": evaluate(model, val)}
            log.write(json.dumps(rec) + "\n")
            print(f"step 0: val loss {rec['val_loss']:.4f}")
        while step < total:
            epoch, k = divmod(step, per_epoch)
            order = list(range(len(train)))
            random.Random(a.seed + epoch).shuffle(order)
            batch = [train[i] for i in order[k * a.batch:(k + 1) * a.batch]]
            n_train = sum(r.n_train for r in batch)
            t0, loss_sum = time.time(), 0.0
            for r in batch:
                loss = -token_logprobs(model, r.input_ids, r.labels).sum() / n_train
                loss.backward()
                loss_sum += loss.item()
            grad_norm = torch.nn.utils.clip_grad_norm_(params, 1.0).item()
            opt.step()
            opt.zero_grad(set_to_none=True)
            sched.step()
            step += 1
            dt = time.time() - t0
            rec = {"step": step, "epoch": round(step / per_epoch, 3), "loss": round(loss_sum, 5),
                   "lr": opt.param_groups[0]["lr"], "grad_norm": round(grad_norm, 4), "trained_tokens": n_train,
                   "tokens_per_s": round(sum(len(r.input_ids) for r in batch) / dt), "seconds": round(dt, 2)}
            if val and (step % a.eval_every == 0 or step == total):
                rec["val_loss"] = round(evaluate(model, val), 5)
            log.write(json.dumps(rec) + "\n")
            log.flush()
            print(f"step {step}/{total}  loss {rec['loss']:.4f}" + (f"  val {rec['val_loss']:.4f}" if "val_loss" in rec
                                                                     else "") +
                  f"  lr {rec['lr']:.2e}  {rec['tokens_per_s']:,} tok/s  {dt:.1f}s", flush=True)
            if step % a.save_every == 0 or step == total:
                save_checkpoint(model, opt, sched, out / "checkpoint", {"step": step})

    model.save_pretrained(out / "adapter", selected_adapters=["default"])
    (out / "adapter" / "alfred_training.json").write_text(json.dumps(
        {"stage": "sft", "base_model": a.model, "data": str(data_path), "steps": total, "args": vars(a)}, indent=1))
    print(f"Adapter saved to {out / 'adapter'}")
    return out / "adapter"


if __name__ == "__main__":
    main()
