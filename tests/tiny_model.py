"""A tiny random model and tokenizer with Nemotron 3 Nano's chat template, for testing training code on a CPU.

The template is NVIDIA's (fixtures/nemotron3_nano_chat_template.jinja, as published with Nemotron 3 Nano and
vendored by llama.cpp). The tokenizer is a small byte-level BPE trained on Alfred conversations, with the
template's special tokens, so token boundaries behave like the real tokenizer's around them.
"""
from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
SPECIAL = ["<|im_start|>", "<|im_end|>", "<think>", "</think>", "<tool_call>", "</tool_call>", "<tool_response>",
           "</tool_response>", "<pad>"]


def corpus() -> list[str]:
    from alfred.agents.scripted import TeachingOracle
    from alfred.env import Environment
    from alfred.tasks import generate_tasks
    from alfred.tools import TOOL_SCHEMAS
    from alfred.train.data import conversation_from_env
    texts = [str(TOOL_SCHEMAS)]
    for t in generate_tasks(0, 3):
        env = Environment(t)
        TeachingOracle().run(env)
        texts += [str(m) for m in conversation_from_env(env)]
    return texts


def build(out: Path, vocab_size: int = 1200, hidden: int = 64, layers: int = 2) -> Path:
    import torch
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    from transformers import LlamaConfig, LlamaForCausalLM, PreTrainedTokenizerFast

    out.mkdir(parents=True, exist_ok=True)
    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(vocab_size=vocab_size, special_tokens=SPECIAL,
                                  initial_alphabet=pre_tokenizers.ByteLevel.alphabet())
    tok.train_from_iterator(corpus(), trainer)
    fast = PreTrainedTokenizerFast(tokenizer_object=tok, eos_token="<|im_end|>", pad_token="<pad>",
                                   bos_token=None, unk_token=None)
    fast.chat_template = (FIXTURES / "nemotron3_nano_chat_template.jinja").read_text()
    fast.save_pretrained(out)
    torch.manual_seed(0)
    cfg = LlamaConfig(vocab_size=len(fast), hidden_size=hidden, intermediate_size=hidden * 2,
                      num_hidden_layers=layers, num_attention_heads=4, num_key_value_heads=2,
                      max_position_embeddings=16384, tie_word_embeddings=False,
                      eos_token_id=fast.eos_token_id, pad_token_id=fast.pad_token_id)
    LlamaForCausalLM(cfg).save_pretrained(out)
    return out
