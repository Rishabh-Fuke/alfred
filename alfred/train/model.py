"""Loading the model with LoRA, and computing the log-probabilities training needs.

Kept separate from the trainers so SFT and RL share one definition of "the model's log-probability of a reply".
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path

import torch

# Linear layers LoRA can adapt. Attention and MLP projections, plus the Mamba output projection in hybrid models
# like Nemotron-H. The Mamba input projection is left out on purpose: vLLM serves it as one packed layer, so an
# adapter on it may not load there, and every adapter has to load into vLLM for RL and serving.
LORA_TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj", "out_proj")


def device() -> torch.device:
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def load_tokenizer(name: str, trust_remote_code: bool = False):
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(name, trust_remote_code=trust_remote_code)
    if not getattr(tok, "is_fast", False):
        raise ValueError(f"{name}: need a fast tokenizer")
    return tok


def load_model(name: str, dtype: str = "auto", use_kernels: bool = True, trust_remote_code: bool = False,
               gradient_checkpointing: bool = True):
    """The base model on the GPU (or CPU for tests). Hub kernels for Mamba layers are used when the `kernels`
    package is installed; otherwise transformers falls back to the mamba-ssm package or plain PyTorch."""
    from transformers import AutoModelForCausalLM
    dev = device()
    if dtype == "auto":
        dtype = "bfloat16" if dev.type == "cuda" else "float32"
    kw = {"trust_remote_code": trust_remote_code}
    if dev.type == "cuda":
        kw["device_map"] = {"": 0}  # load straight onto the GPU
    if use_kernels and dev.type == "cuda" and importlib.util.find_spec("kernels"):
        kw["use_kernels"] = True
    torch_dtype = getattr(torch, dtype)
    attempts = [kw]
    if kw.get("use_kernels"):  # hub kernels can fail to resolve for a new torch; the model still loads without them
        attempts.append({k: v for k, v in kw.items() if k != "use_kernels"})
    if not trust_remote_code:  # a checkpoint newer than this transformers' built-in class: use the repo's code
        attempts.append({**attempts[-1], "trust_remote_code": True})
    for i, attempt in enumerate(attempts):
        try:
            model = AutoModelForCausalLM.from_pretrained(name, dtype=torch_dtype, **attempt)
            break
        except Exception as e:  # noqa: BLE001 - try the next way of loading, and say why
            if i == len(attempts) - 1:
                raise
            print(f"Loading {name} with {attempt} failed ({type(e).__name__}: {str(e)[:300]}); trying another way.")
    print(f"Loaded {name}: {sum(p.numel() for p in model.parameters()) / 1e9:.2f}B parameters, {torch_dtype}, "
          f"hub kernels {'on' if attempt.get('use_kernels') else 'off'}, "
          f"{'custom' if attempt.get('trust_remote_code') else 'built-in'} model code")
    model.to(dev)
    model.config.use_cache = False
    if gradient_checkpointing:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    return model


def lora_target_names(model, wanted=LORA_TARGETS) -> list[str]:
    """The target names from `wanted` that exist in this model as nn.Linear layers."""
    found = {n.rsplit(".", 1)[-1] for n, mod in model.named_modules() if isinstance(mod, torch.nn.Linear)}
    names = [t for t in wanted if t in found]
    if not names:
        raise ValueError(f"none of {wanted} are linear layers in this model")
    return names


def add_lora(model, r: int = 32, alpha: int = 64, dropout: float = 0.0, targets=LORA_TARGETS):
    from peft import LoraConfig, get_peft_model
    cfg = LoraConfig(r=r, lora_alpha=alpha, lora_dropout=dropout, target_modules=lora_target_names(model, targets),
                     bias="none", task_type="CAUSAL_LM")
    return get_peft_model(model, cfg)


def load_lora(model, path: str, trainable: bool = True, adapter_name: str = "default"):
    """Attach a saved adapter. With trainable=True it keeps training from there."""
    from peft import PeftModel
    if isinstance(model, PeftModel):
        model.load_adapter(path, adapter_name=adapter_name, is_trainable=trainable)
        return model
    return PeftModel.from_pretrained(model, path, adapter_name=adapter_name, is_trainable=trainable)


def trainable_parameters(model) -> list[torch.nn.Parameter]:
    return [p for p in model.parameters() if p.requires_grad]


def token_logprobs(model, input_ids: list[int], labels: list[int]) -> torch.Tensor:
    """log p(token) for every labeled token, in order. Computes logits only where there's a label, so a 10k-token
    episode with 500 trained tokens doesn't materialize a 10k x vocabulary matrix."""
    dev = next(model.parameters()).device
    targets = [j for j in range(1, len(input_ids)) if labels[j] != -100]
    ids = torch.tensor([input_ids], device=dev)
    keep = torch.tensor([j - 1 for j in targets], device=dev)
    logits = None
    try:
        out = model(input_ids=ids, logits_to_keep=keep, use_cache=False)
        if out.logits.shape[1] == len(targets):
            logits = out.logits[0]
    except TypeError:
        pass
    if logits is None:  # a model that doesn't take logits_to_keep as positions
        logits = model(input_ids=ids, use_cache=False).logits[0, keep]
    logits = logits.float()
    tgt = torch.tensor([input_ids[j] for j in targets], device=dev)
    return logits.gather(-1, tgt[:, None]).squeeze(-1) - torch.logsumexp(logits, dim=-1)


class Schedule:
    """Linear warmup, then cosine decay to `floor` x the peak learning rate."""

    def __init__(self, optimizer, total_steps: int, warmup_steps: int, floor: float = 0.1):
        self.opt, self.total, self.warmup, self.floor = optimizer, max(1, total_steps), warmup_steps, floor
        self.peak = [g["lr"] for g in optimizer.param_groups]
        self.step_num = 0
        self._apply()

    def factor(self) -> float:
        s = self.step_num
        if s < self.warmup:
            return (s + 1) / (self.warmup + 1)
        t = min(1.0, (s - self.warmup) / max(1, self.total - self.warmup))
        return self.floor + (1 - self.floor) * 0.5 * (1 + math.cos(math.pi * t))

    def _apply(self):
        for g, peak in zip(self.opt.param_groups, self.peak):
            g["lr"] = peak * self.factor()

    def step(self):
        self.step_num += 1
        self._apply()

    def state_dict(self):
        return {"step_num": self.step_num}

    def load_state_dict(self, d):
        self.step_num = d["step_num"]
        self._apply()


def save_checkpoint(model, optimizer, schedule, out: Path, state: dict) -> None:
    """Adapter weights plus everything needed to resume. Written to a temp dir, then renamed, so an interrupted
    save never leaves a half-written checkpoint."""
    tmp = out.with_name(out.name + ".tmp")
    if tmp.exists():
        _rmtree(tmp)
    tmp.mkdir(parents=True)
    model.save_pretrained(tmp, selected_adapters=["default"])
    torch.save({"optimizer": optimizer.state_dict(), "schedule": schedule.state_dict()}, tmp / "trainer.pt")
    (tmp / "state.json").write_text(json.dumps(state, indent=1))
    if out.exists():
        _rmtree(out)
    os.replace(tmp, out)


def load_checkpoint(model, optimizer, schedule, path: Path) -> dict:
    from peft import set_peft_model_state_dict
    from safetensors.torch import load_file
    weights = load_file(str(path / "adapter_model.safetensors"))
    set_peft_model_state_dict(model, weights, adapter_name="default")
    blob = torch.load(path / "trainer.pt", map_location="cpu", weights_only=False)
    optimizer.load_state_dict(blob["optimizer"])
    schedule.load_state_dict(blob["schedule"])
    return json.loads((path / "state.json").read_text())


def _rmtree(p: Path) -> None:
    import shutil
    shutil.rmtree(p, ignore_errors=True)
