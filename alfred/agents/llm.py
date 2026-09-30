"""An agent backed by any OpenAI-compatible chat endpoint.

Works with Ollama (http://localhost:11434/v1), LM Studio (http://localhost:1234/v1), vLLM,
llama.cpp's server, or a hosted API. Two tool modes:
  native: the server's own tool calling (the `tools` parameter). Use this when the model supports it.
  json:   tools are described in the system prompt and the model replies with a JSON object.
"""
from __future__ import annotations

import json
import os
import re
import time

from ..tools import TOOL_NAMES

_THINK = re.compile(r"<think>.*?</think>", re.S)
_HARMONY = re.compile(r"<\|(?:start|end|channel|message|constrain|return|call)\|>")
MALFORMED = "malformed"

# A JSON object with no tool name whose keys only fit one tool, e.g. {"status": ..., "message": ...}.
_KEYS_TO_TOOL = [({"status", "message"}, "finish"), ({"question"}, "ask_user")]


def strip_think(text: str | None) -> str:
    """Remove reasoning blocks that some local models put in their visible output."""
    text = _THINK.sub("", text or "")
    if "</think>" in text:  # the opening tag was part of the chat template
        text = text.split("</think>", 1)[1]
    if "<|message|>" in text:  # harmony-format debris: keep the last message
        text = text.rsplit("<|message|>", 1)[1]
    return _HARMONY.sub("", text).strip()


def _infer_tool(obj: dict):
    keys = set(obj)
    for needed, tool in _KEYS_TO_TOOL:
        if needed <= keys and keys <= needed | {"reason", "note"}:
            return tool
    return None


def parse_json_tool_call(text: str):
    """Find a tool call written as JSON in plain text.

    Returns (name, arguments), MALFORMED if the text looks like an attempted call that doesn't
    parse, or None if there's no attempt at all. A bare {"status": ..., "message": ...} counts as
    finish and a bare {"question": ...} as ask_user, since those keys fit only one tool.
    """
    if not text or "{" not in text:
        return None
    m = re.search(r"<tool_call>(.*?)</tool_call>", text, re.S)
    body = re.sub(r"```(?:json)?", "", m.group(1) if m else text)
    decoder = json.JSONDecoder()
    for i, ch in enumerate(body):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(body, i)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        fn = obj.get("function") if isinstance(obj.get("function"), dict) else obj
        name = fn.get("tool") or fn.get("name") or (obj.get("function") if isinstance(obj.get("function"), str) else None)
        if not isinstance(name, str):
            inferred = _infer_tool(obj)
            if inferred:
                return inferred, {k: v for k, v in obj.items() if k not in ("reason", "note")}
            continue
        args = fn.get("arguments", fn.get("args", fn.get("parameters", fn.get("input", {}))))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return name, args if isinstance(args, dict) else {}
    return MALFORMED


_PARAM_FIXES: dict = {}  # model -> {"token_param": ..., "drop": {...}} learned from server errors


def chat_create(client, model: str, **kw):
    """client.chat.completions.create, adapting to servers that reject some parameters.

    Newer hosted models refuse `max_tokens` (they want `max_completion_tokens`) and some accept only the
    default temperature. The first rejection is remembered for that model, so it costs one extra call.
    """
    fix = _PARAM_FIXES.setdefault(model, {"token_param": "max_tokens", "drop": set()})
    for _ in range(4):
        args = dict(kw)
        if fix["token_param"] == "max_completion_tokens" and "max_tokens" in args:
            args["max_completion_tokens"] = args.pop("max_tokens")
        for p in fix["drop"]:
            args.pop(p, None)
        try:
            return client.chat.completions.create(model=model, **args)
        except Exception as e:  # noqa: BLE001 - inspect the server's message, re-raise anything else
            msg = str(e).lower()
            if "max_tokens" in msg and "max_completion_tokens" in msg and fix["token_param"] == "max_tokens":
                fix["token_param"] = "max_completion_tokens"
                continue
            if "extra_body" in args and "extra_body" not in fix["drop"] and "chat_template_kwargs" in msg:
                fix["drop"].add("extra_body")  # this host doesn't take template options; run without them
                continue
            bad = next((p for p in ("temperature", "seed", "top_p") if p in args and p not in fix["drop"]
                        and p in msg and ("unsupported" in msg or "not supported" in msg)), None)
            if bad is None:
                raise
            fix["drop"].add(bad)
    raise RuntimeError(f"{model}: couldn't find request parameters the server accepts")


class OpenAICompatAgent:
    def __init__(self, model: str, base_url: str = "http://localhost:11434/v1", api_key: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 4096, seed: int | None = None,
                 tool_mode: str = "native", timeout: float = 600.0, client=None, extra_body: dict | None = None):
        """extra_body is sent as-is with every request, e.g. {"chat_template_kwargs": {"enable_thinking": False}}
        to turn off reasoning on vLLM-served Nemotron and Qwen models."""
        if tool_mode not in ("native", "json"):
            raise ValueError("tool_mode must be 'native' or 'json'")
        self.model, self.temperature, self.max_tokens = model, temperature, max_tokens
        self.seed, self.tool_mode, self.extra_body = seed, tool_mode, extra_body
        if client is None:
            from openai import OpenAI
            env_key = os.environ.get("OPENAI_API_KEY") if "api.openai.com" in base_url else None
            client = OpenAI(base_url=base_url, api_key=api_key or env_key or "local", timeout=timeout, max_retries=3)
        self.client = client
        self.messages: list[dict] = []

    def _complete(self, messages, tools):
        kw = dict(messages=messages, temperature=self.temperature, max_tokens=self.max_tokens)
        if tools:
            kw["tools"] = tools
        if self.seed is not None:
            kw["seed"] = self.seed
        if self.extra_body:
            kw["extra_body"] = self.extra_body
        return chat_create(self.client, self.model, **kw)

    def run(self, env) -> dict:
        obs = env.reset()
        native = self.tool_mode == "native"
        messages = [{"role": "system", "content": obs.system}, {"role": "user", "content": obs.instruction}]
        self.messages = messages
        stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "llm_seconds": 0.0,
                 "max_prompt_tokens": 0, "recovered_calls": 0}
        for turn in range(env.max_steps):
            t0 = time.perf_counter()
            resp = self._complete(messages, obs.tools if native else None)
            stats["llm_seconds"] += time.perf_counter() - t0
            stats["llm_calls"] += 1
            if resp.usage:
                stats["prompt_tokens"] += resp.usage.prompt_tokens or 0
                stats["completion_tokens"] += resp.usage.completion_tokens or 0
                stats["max_prompt_tokens"] = max(stats["max_prompt_tokens"], resp.usage.prompt_tokens or 0)
            msg = resp.choices[0].message
            content = msg.content or ""

            if native and msg.tool_calls:
                calls = [(tc.id or f"call_{turn}_{k}", tc.function.name, tc.function.arguments)
                         for k, tc in enumerate(msg.tool_calls)]
                messages.append({"role": "assistant", "content": content, "tool_calls": [
                    {"id": cid, "type": "function",
                     "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}
                    for cid, name, args in calls]})
                for cid, name, args in calls:
                    messages.append({"role": "tool", "tool_call_id": cid, "content": env.call_tool(name, args)})
                    if env.done:
                        break
            else:
                visible = strip_think(content)
                parsed = parse_json_tool_call(visible)
                messages.append({"role": "assistant", "content": content})
                if isinstance(parsed, tuple) and (not native or parsed[0] in TOOL_NAMES):
                    # json mode, or a native-mode model that wrote its call as text
                    if native:
                        stats["recovered_calls"] += 1
                        env.note_format("recovered_call")
                    name, args = parsed
                    messages.append({"role": "user", "content": f"Result of {name}:\n{env.call_tool(name, args)}"})
                elif parsed == MALFORMED and not native:
                    env.record_invalid("Couldn't parse a JSON tool call.", visible[:500])
                    messages.append({"role": "user", "content": 'Error: I couldn\'t parse that. Reply with one JSON '
                                                                'object like {"tool": "...", "arguments": {...}}.'})
                else:
                    env.implicit_finish(visible)
            if env.done:
                break
        if not env.done:
            env.truncate()
        stats["llm_seconds"] = round(stats["llm_seconds"], 3)
        return stats
