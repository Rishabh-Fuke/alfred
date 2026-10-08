"""Turn a conversation into token ids and a loss mask, with the model's own chat template.

Training has to see exactly what the model sees at inference. vLLM renders the conversation with the chat
template, adds the generation prompt, and the model writes one assistant turn; the next turn's prompt is the
template's rendering of everything so far. So for every assistant message we render the conversation up to it
with the generation prompt (what the model saw) and up to and including it (what the template keeps), and train
on the difference: the assistant's reply and its end-of-turn token.

This only works if the template renders a conversation as a sequence of prefixes: turn t's prompt, then turn t's
reply, then whatever comes next, without rewriting earlier text. Nemotron's template does, as long as reasoning
is off (it strips old reasoning otherwise). `render` checks this for every example and raises TemplateMismatch
if it doesn't hold, rather than training on a sequence the model never sees.

Pieces are tokenized one at a time and concatenated, so each reply is tokenized on its own, the way the model
produced it, rather than merged with the text around it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

TEMPLATE_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content")
# Nemotron and Qwen templates read enable_thinking; templates that don't simply ignore it.
DEFAULT_TEMPLATE_KWARGS = {"enable_thinking": False}
IGNORE = -100


class TemplateMismatch(ValueError):
    """The chat template doesn't render this conversation as a sequence of prefixes."""


@dataclass
class Rendered:
    input_ids: list[int]
    labels: list[int]              # IGNORE where there's no loss
    n_train: int = 0               # tokens with a loss
    turns: int = 0                 # assistant turns with a loss
    truncated: bool = False
    spans: list = field(default_factory=list)  # (start, end) token ranges of trained replies


def _tool_call(tc: dict) -> dict:
    fn = tc.get("function", tc)
    args = fn.get("arguments")
    if isinstance(args, str):
        args = json.loads(args) if args.strip() else {}
    return {"id": tc.get("id") or "call", "type": "function",
            "function": {"name": fn["name"], "arguments": args if isinstance(args, dict) else {}}}


def template_messages(messages: list[dict]) -> list[dict]:
    """Messages as chat templates expect them: tool-call arguments as objects (vLLM converts the JSON strings
    of the OpenAI format the same way before rendering), content never None, and no extra keys."""
    out = []
    for m in messages:
        m2 = {k: m[k] for k in TEMPLATE_KEYS if k in m}
        if m2.get("content") is None:
            m2["content"] = ""
        if m2.get("tool_calls"):
            m2["tool_calls"] = [_tool_call(tc) for tc in m2["tool_calls"]]
        else:
            m2.pop("tool_calls", None)
        out.append(m2)
    return out


def chat_text(tokenizer, messages: list[dict], tools, add_generation_prompt: bool, template_kwargs: dict) -> str:
    return tokenizer.apply_chat_template(messages, tools=tools or None, tokenize=False,
                                         add_generation_prompt=add_generation_prompt, **template_kwargs)


def reply_spans(tokenizer, messages: list[dict], tools=None, template_kwargs: dict | None = None):
    """The full rendered text and the character span of every assistant reply in it."""
    kw = DEFAULT_TEMPLATE_KWARGS if template_kwargs is None else template_kwargs
    msgs = template_messages(messages)
    full = chat_text(tokenizer, msgs, tools, False, kw)
    spans = []
    for i, m in enumerate(msgs):
        if m["role"] != "assistant":
            continue
        prompt = chat_text(tokenizer, msgs[:i], tools, True, kw)
        upto = chat_text(tokenizer, msgs[:i + 1], tools, False, kw)
        if not (upto.startswith(prompt) and full.startswith(upto)):
            raise TemplateMismatch(f"message {i}: rendering the conversation up to this reply doesn't extend the "
                                   f"rendering before it")
        reply = upto[len(prompt):]
        if not reply.strip():
            raise TemplateMismatch(f"message {i}: the template rendered this reply as nothing")
        spans.append((len(prompt), len(prompt) + len(reply.rstrip())))  # through end-of-turn, not the newline after
    return full, spans


def render(tokenizer, messages: list[dict], tools=None, train: list[bool] | None = None,
           template_kwargs: dict | None = None, max_len: int | None = None) -> Rendered | None:
    """Token ids and labels for one conversation.

    train: for each assistant message in order, whether it gets a loss (default: all of them). Turns that led to
    a rejected draft or an error can be left out, so the model learns the recovery but not the mistake.
    max_len: cut the sequence there; returns None if nothing trainable is left.
    """
    full, spans = reply_spans(tokenizer, messages, tools, template_kwargs)
    if train is not None and len(train) != len(spans):
        raise ValueError(f"train has {len(train)} flags for {len(spans)} assistant messages")
    ids, labels, token_spans = [], [], []
    pos = 0
    for k, (start, end) in enumerate(spans):
        if start > pos:
            piece = tokenizer(full[pos:start], add_special_tokens=False)["input_ids"]
            ids += piece
            labels += [IGNORE] * len(piece)
        piece = tokenizer(full[start:end], add_special_tokens=False)["input_ids"]
        on = True if train is None else bool(train[k])
        token_spans.append((len(ids), len(ids) + len(piece), on))
        ids += piece
        labels += piece if on else [IGNORE] * len(piece)
        pos = end
    if pos < len(full) and full[pos:].strip():
        piece = tokenizer(full[pos:], add_special_tokens=False)["input_ids"]
        ids += piece
        labels += [IGNORE] * len(piece)
    truncated = bool(max_len and len(ids) > max_len)
    if truncated:
        ids, labels = ids[:max_len], labels[:max_len]
    n_train = sum(x != IGNORE for x in labels)
    if n_train == 0:
        return None
    kept = [(a, min(b, len(ids))) for a, b, on in token_spans if on and a < len(ids)]
    return Rendered(ids, labels, n_train, len(kept), truncated, kept)


def check_prompt_tokens(tokenizer, messages: list[dict], tools=None, template_kwargs: dict | None = None) -> list[int]:
    """Assistant turns whose prompt, tokenized in one go (as vLLM does), differs from the start of the training
    sequence. Should be empty; a mismatch means the model would be trained on token boundaries it never sees."""
    kw = DEFAULT_TEMPLATE_KWARGS if template_kwargs is None else template_kwargs
    r = render(tokenizer, messages, tools, template_kwargs=kw)
    msgs = template_messages(messages)
    bad, k = [], 0
    for i, m in enumerate(msgs):
        if m["role"] != "assistant":
            continue
        prompt_ids = tokenizer(chat_text(tokenizer, msgs[:i], tools, True, kw), add_special_tokens=False)["input_ids"]
        start = r.spans[k][0] if r and k < len(r.spans) else None
        if start is None or r.input_ids[:start] != prompt_ids:
            bad.append(i)
        k += 1
    return bad
