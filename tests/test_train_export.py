"""Merging an adapter into the base checkpoint gives the same outputs as base + adapter."""
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("peft")

from alfred.train.export import match_key, merge  # noqa: E402


def test_merged_model_matches_base_plus_adapter(tiny_model_dir, tmp_path):
    from peft import PeftModel
    from transformers import AutoModelForCausalLM

    from alfred.train.model import add_lora
    torch.manual_seed(1)
    model = add_lora(AutoModelForCausalLM.from_pretrained(tiny_model_dir), r=4, alpha=8)
    with torch.no_grad():
        for n, p in model.named_parameters():
            if "lora_B" in n:
                p.normal_(0, 0.05)  # B starts at zero; make the adapter do something
    model.save_pretrained(tmp_path / "adapter")
    ids = torch.tensor([[1, 5, 9, 200, 7, 31]])
    with torch.no_grad():
        want = PeftModel.from_pretrained(AutoModelForCausalLM.from_pretrained(tiny_model_dir),
                                         tmp_path / "adapter")(ids).logits
        base = AutoModelForCausalLM.from_pretrained(tiny_model_dir)(ids).logits
    info = merge(tiny_model_dir, tmp_path / "adapter", tmp_path / "merged")
    assert info["merged_weights"] == 2 * 7  # 2 layers x 7 adapted projections
    with torch.no_grad():
        got = AutoModelForCausalLM.from_pretrained(tmp_path / "merged")(ids).logits
    assert torch.allclose(got, want, atol=1e-4) and not torch.allclose(got, base, atol=1e-3)
    assert (tmp_path / "merged" / "tokenizer_config.json").exists()


def test_checkpoint_names_can_differ_from_module_names():
    keys = {"backbone.layers.3.mixer.q_proj.weight", "backbone.layers.3.mixer.out_proj.weight",
            "mtp.layers.3.mixer.q_proj.weight"}
    assert match_key("model.layers.3.mixer.q_proj", keys) == "backbone.layers.3.mixer.q_proj.weight"
    with pytest.raises(KeyError):
        match_key("model.layers.4.mixer.q_proj", keys)
