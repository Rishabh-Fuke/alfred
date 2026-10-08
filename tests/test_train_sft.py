"""SFT end to end on a tiny random model (CPU): the loss goes down, the adapter saves, and training resumes."""
import json

import pytest

pytest.importorskip("peft")

from alfred.train import data, sft  # noqa: E402


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("data") / "sft.jsonl"
    data.main(["--oracle-worlds", "0-1", "--val-frac", "0.15", "--out", str(out)])
    return out


def test_sft_learns_and_resumes(tiny_model_dir, dataset, tmp_path):
    out = tmp_path / "sft"
    common = ["--model", str(tiny_model_dir), "--data", str(dataset), "--out", str(out), "--batch", "4",
              "--lr", "3e-2", "--lora-r", "16", "--lora-alpha", "32", "--eval-every", "2", "--save-every", "2",
              "--no-gradient-checkpointing"]
    sft.main(common + ["--epochs", "1"])
    log = [json.loads(x) for x in (out / "log.jsonl").read_text().splitlines()]
    first_steps = [r for r in log if "loss" in r]
    assert (out / "adapter" / "adapter_model.safetensors").exists()
    cfg = json.loads((out / "adapter" / "adapter_config.json").read_text())
    assert set(cfg["target_modules"]) >= {"q_proj", "o_proj", "down_proj"} and cfg["r"] == 16

    # More epochs from the checkpoint: continues where it stopped, and the loss keeps falling.
    sft.main(common + ["--epochs", "4", "--resume"])
    log = [json.loads(x) for x in (out / "log.jsonl").read_text().splitlines()]
    steps = [r["step"] for r in log if "loss" in r]
    assert steps == list(range(1, len(steps) + 1))  # no step repeated or skipped
    losses = [r["loss"] for r in log if "loss" in r]
    assert sum(losses[-4:]) < 0.97 * sum(losses[:4]), losses  # a tiny random model learns slowly, but it learns
    vals = [r["val_loss"] for r in log if "val_loss" in r]
    assert vals[-1] < 0.97 * vals[0], vals
    assert len(first_steps) < len(steps)
