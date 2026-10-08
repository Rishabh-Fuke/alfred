"""Merge a LoRA adapter into the base model's weights and save a standalone model.

    python -m alfred.train.export --model nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 --adapter out/grpo/best \\
        --out out/alfred-nemotron-4b

The merge works on the base checkpoint's own files: every tensor the adapter touches gets W + scale * B @ A,
everything else is copied byte for byte, and the config, tokenizer, chat template and any custom code come along
unchanged. So the result loads anywhere the base model does (vLLM, transformers, llama.cpp's GGUF converter),
with the same tensor names. Runs on CPU; no GPU needed.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


def _module_of(adapter_key: str) -> tuple[str, str] | None:
    """'base_model.model.model.layers.3.mixer.q_proj.lora_A.weight' -> ('model.layers.3.mixer.q_proj', 'A')."""
    m = re.match(r"^(?:base_model\.model\.)?(.+)\.lora_([AB])(?:\.[^.]+)?\.weight$", adapter_key)
    return (m.group(1), m.group(2)) if m else None


def match_key(module: str, base_keys: set[str]) -> str:
    """The base checkpoint's weight name for an adapter's module, matching on the longest common suffix (the
    checkpoint may say 'backbone.layers.3...' where the loaded model said 'model.layers.3...')."""
    parts = module.split(".")
    # Never drop the layer number: 'mixer.q_proj' alone would match every layer.
    last = next((i for i, x in enumerate(parts) if x.isdigit()), len(parts) - 1)
    for i in range(last + 1):
        cand = ".".join(parts[i:]) + ".weight"
        hits = [k for k in base_keys if k == cand or k.endswith("." + cand)]
        if len(hits) > 1:  # e.g. a multi-token-prediction head with the same layer numbering: keep the trunk
            hits = [k for k in hits if k.split(".", 1)[0] in ("backbone", "model", parts[0])]
        if len(hits) == 1:
            return hits[0]
        if hits:
            break
    raise KeyError(f"no unique weight in the base checkpoint for {module}")


def merge(base_dir: Path, adapter_dir: Path, out: Path) -> dict:
    import torch
    from safetensors import safe_open
    from safetensors.torch import load_file, save_file

    cfg = json.loads((adapter_dir / "adapter_config.json").read_text())
    r, alpha = cfg["r"], cfg["lora_alpha"]
    scale = alpha / (r ** 0.5) if cfg.get("use_rslora") else alpha / r
    lora = load_file(str(adapter_dir / "adapter_model.safetensors"))
    pairs: dict[str, dict] = {}
    for k, v in lora.items():
        mod = _module_of(k)
        if mod:
            pairs.setdefault(mod[0], {})[mod[1]] = v

    shards = sorted(base_dir.glob("*.safetensors"))
    if not shards:
        raise FileNotFoundError(f"no .safetensors files in {base_dir}")
    base_keys = set()
    for s in shards:
        with safe_open(str(s), "pt") as f:
            base_keys |= set(f.keys())
    targets = {match_key(m, base_keys): ab for m, ab in pairs.items()}

    out.mkdir(parents=True, exist_ok=True)
    for f in base_dir.iterdir():  # config, tokenizer, chat template, custom code, index...
        if f.is_file() and not f.name.endswith(".safetensors") and not f.name.startswith("."):
            shutil.copy2(f, out / f.name)
    merged = 0
    for s in shards:
        tensors = load_file(str(s))
        for k in list(tensors):
            if k in targets:
                ab = targets[k]
                w = tensors[k]
                delta = (ab["B"].float() @ ab["A"].float()) * scale
                if delta.shape != w.shape:
                    raise ValueError(f"{k}: adapter delta {tuple(delta.shape)} doesn't match weight {tuple(w.shape)}")
                tensors[k] = (w.float() + delta).to(w.dtype).contiguous()
                merged += 1
        with safe_open(str(s), "pt") as f:
            meta = f.metadata()
        save_file(tensors, str(out / s.name), metadata=meta)
    if merged != len(targets):
        raise RuntimeError(f"merged {merged} of {len(targets)} adapted weights")
    info = {"base": str(base_dir), "adapter": str(adapter_dir), "merged_weights": merged, "scale": scale}
    (out / "alfred_merge.json").write_text(json.dumps(info, indent=1))
    return info


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m alfred.train.export", description=__doc__.split("\n\n")[0])
    p.add_argument("--model", required=True, help="Base model: Hugging Face id or local directory.")
    p.add_argument("--adapter", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    base = Path(a.model)
    if not base.is_dir():
        from huggingface_hub import snapshot_download
        base = Path(snapshot_download(a.model))
    info = merge(base, Path(a.adapter), Path(a.out))
    print(f"Merged {info['merged_weights']} weights into {a.out}")


if __name__ == "__main__":
    main()
