"""Rendering conversations for training with Nemotron's real chat template: the loss covers exactly the replies."""
import pytest

pytest.importorskip("transformers")
pytest.importorskip("tokenizers")

from alfred.agents.scripted import TeachingOracle  # noqa: E402
from alfred.env import Environment  # noqa: E402
from alfred.tasks import generate_tasks  # noqa: E402
from alfred.tools import TOOL_SCHEMAS  # noqa: E402
from alfred.train.data import conversation_from_env  # noqa: E402
from alfred.train.render import IGNORE, TemplateMismatch, check_prompt_tokens, render, reply_spans  # noqa: E402


@pytest.fixture(scope="module")
def tok(tiny_model_dir):
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(tiny_model_dir)


def conversations():
    for t in generate_tasks(10, 2):
        env = Environment(t)
        TeachingOracle().run(env)
        yield t, conversation_from_env(env)


def test_every_reply_is_a_prefix_extension_and_only_replies_are_trained(tok):
    for t, msgs in conversations():
        full, spans = reply_spans(tok, msgs, TOOL_SCHEMAS)
        assert len(spans) == sum(m["role"] == "assistant" for m in msgs)
        for a, b in spans:
            reply = full[a:b]
            assert reply.lstrip().startswith("<tool_call>") and reply.endswith("<|im_end|>"), reply
            # the prompt before it is the generation prompt with reasoning off
            assert full[:a].endswith("<|im_start|>assistant\n<think></think>")
        r = render(tok, msgs, TOOL_SCHEMAS)
        trained = tok.decode([x for x in r.labels if x != IGNORE])
        assert "<tool_response>" not in trained and "You are a personal assistant" not in trained
        assert trained.count("<|im_end|>") == len(spans) and trained.count("<tool_call>") == len(spans)
        assert check_prompt_tokens(tok, msgs, TOOL_SCHEMAS) == [], t.id


def test_untrained_turns_get_no_loss(tok):
    _, msgs = next(conversations())
    n = sum(m["role"] == "assistant" for m in msgs)
    full = render(tok, msgs, TOOL_SCHEMAS)
    part = render(tok, msgs, TOOL_SCHEMAS, train=[False] + [True] * (n - 1))
    assert part.input_ids == full.input_ids and 0 < part.n_train < full.n_train and part.turns == n - 1
    assert render(tok, msgs, TOOL_SCHEMAS, train=[False] * n) is None


def test_truncation_keeps_what_fits(tok):
    msgs = next(m for _, m in conversations() if sum(x["role"] == "assistant" for x in m) >= 3)
    full = render(tok, msgs, TOOL_SCHEMAS)
    cut = render(tok, msgs, TOOL_SCHEMAS, max_len=full.spans[1][1])
    assert cut.truncated and len(cut.input_ids) == full.spans[1][1] and cut.turns == 2


def test_string_arguments_render_like_objects(tok):
    import json
    _, msgs = next(conversations())
    as_strings = [dict(m, tool_calls=[dict(tc, function=dict(tc["function"],
                                                              arguments=json.dumps(tc["function"]["arguments"])))
                                      for tc in m["tool_calls"]]) if m.get("tool_calls") else m for m in msgs]
    assert render(tok, as_strings, TOOL_SCHEMAS).input_ids == render(tok, msgs, TOOL_SCHEMAS).input_ids


def test_a_template_that_rewrites_history_is_refused(tok):
    _, msgs = next(conversations())
    # With reasoning on, Nemotron's template strips earlier reasoning: training on that would teach a sequence
    # the model never sees, so it has to be refused.
    msgs = [dict(m, reasoning_content="thinking...") if m["role"] == "assistant" else m for m in msgs]
    msgs.insert(3, {"role": "user", "content": "a later user turn makes earlier reasoning history"})
    with pytest.raises(TemplateMismatch):
        render(tok, msgs, TOOL_SCHEMAS, template_kwargs={"enable_thinking": True})
