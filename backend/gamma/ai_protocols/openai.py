"""OpenAI Chat Completions — OpenAI itself and every compatible server
(DeepSeek, OpenRouter, vLLM, Ollama, llama.cpp, LiteLLM, …). The wire
follows the endpoint: only OpenAI gets max_completion_tokens, the Responses
API for tool calls, and the gpt-/o-family filter on its model listing."""

import json
import re
from urllib.request import Request as URLRequest

from .base import (TOOL_IMAGES_NOTE, Protocol, as_int, attach_index, multipart_body, parse_tool_args,
                   tool_image_turns)
from .responses import OPENAI_RESPONSES

# Listings include models the chat endpoint can't use.
_NOT_CHAT = re.compile(r"embed|whisper|tts|audio|image|dall-e|moderation|transcribe|realtime|search")
_OPENAI_CHAT_FAMILIES = re.compile(r"^(gpt-|o\d|chatgpt-)")


def is_openai_platform(base_url: str) -> bool:
    """Whether an entry talks to OpenAI itself rather than a compatible
    server (DeepSeek, a gateway, a local model)."""
    return (base_url or "").startswith("https://api.openai.com")


class OpenAIChat(Protocol):
    id = "openai"
    label = "OpenAI Chat Completions API"

    def wire(self, conf, tools=None):
        # Tool calls to OpenAI itself go over the Responses API (reasoning
        # models reject tools on chat completions); a compatible gateway may
        # not implement /v1/responses, and chat-completions tools work there.
        return OPENAI_RESPONSES if tools and is_openai_platform(conf["base_url"]) else self

    def request(self, conf, messages, system, model, pdf_b64s=None, effort="",
                max_tokens=8192, images=None, stream=False, tools=None):
        messages = [dict(m) for m in messages]
        if pdf_b64s or images:
            last = messages[attach_index(messages)]
            last["content"] = [
                *[{"type": "file", "file": {"filename": f"document-{index + 1}.pdf",
                                            "file_data": f"data:application/pdf;base64,{data}"}}
                  for index, data in enumerate(pdf_b64s or [])],
                *[{"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}}
                  for media_type, data in (images or [])],
                {"type": "text", "text": last["content"]},
            ]
        wire = [{"role": "system", "content": system}] if system else []
        image_turn = lambda imgs: {"role": "user", "content": [  # noqa: E731
            {"type": "text", "text": TOOL_IMAGES_NOTE},
            *[{"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}}
              for media_type, data in imgs]]}
        for m, is_image_turn in tool_image_turns(messages, image_turn):
            if is_image_turn:
                wire.append(m)
            elif m["role"] == "tool":
                wire.append({"role": "tool", "tool_call_id": m["call_id"], "content": m["content"]})
            elif m["role"] == "assistant" and m.get("tool_calls"):
                wire.append({"role": "assistant", "content": m.get("content") or None,
                             "tool_calls": [{"id": c["id"], "type": "function",
                                             "function": {"name": c["name"],
                                                          "arguments": json.dumps(c["arguments"])}}
                                            for c in m["tool_calls"]]})
            else:
                wire.append({"role": m["role"], "content": m["content"]})
        body = {
            "model": model,
            # Current OpenAI models take max_completion_tokens (the cap includes
            # hidden reasoning tokens, so leave a generous default); compatible
            # servers take the classic max_tokens.
            ("max_completion_tokens" if is_openai_platform(conf["base_url"]) else "max_tokens"): max_tokens,
            "messages": wire,
        }
        if tools:
            body["tools"] = [{"type": "function",
                              "function": {"name": t["name"], "description": t["description"],
                                           "parameters": t["parameters"]}} for t in tools]
        if effort:
            body["reasoning_effort"] = effort
        if stream:
            body["stream"] = True
            # The final chunk then carries the token counts (OpenAI and the
            # common compatible servers: vLLM, Ollama, llama.cpp, LiteLLM).
            body["stream_options"] = {"include_usage": True}
        return URLRequest(f"{conf['base_url']}/v1/chat/completions", data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {conf['api_key']}",
            "Content-Type": "application/json",
        })

    def reply_text(self, data):
        choices = data.get("choices") or [{}]
        text = (choices[0].get("message") or {}).get("content") or ""
        if not text.strip():
            reason = choices[0].get("finish_reason", "unknown")
            raise RuntimeError(
                f"empty response (finish_reason={reason} — a reasoning model may have spent "
                "the whole token budget thinking; try effort: low or a shorter request)")
        return text

    def usage(self, raw):
        if not isinstance(raw, dict):
            return None
        usage = {"input": as_int(raw.get("prompt_tokens")), "output": as_int(raw.get("completion_tokens")),
                 "cache_read": as_int((raw.get("prompt_tokens_details") or {}).get("cached_tokens")),
                 "cache_write": 0}
        return usage if (usage["input"] or usage["output"]) else None

    def stream_event(self, event, state):
        if event.get("error"):
            raise RuntimeError((event["error"] or {}).get("message") or "stream error")
        if event.get("usage"):
            state["usage"] = self.usage(event["usage"]) or state["usage"]
        choice = (event.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if delta.get("content"):
            yield ("text", delta["content"])
        pending = state.setdefault("pending", {})  # index -> {id, name, args} across deltas
        for tc in delta.get("tool_calls") or []:
            slot = pending.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["args"] += fn["arguments"]
                yield ("tool_delta", {"id": slot["id"], "name": slot["name"], "json": slot["args"]})
        state["stop"] = choice.get("finish_reason") or state["stop"]

    def stream_end(self, state):
        # Tool calls are announced piecewise; emit them once the stream ends.
        for _, slot in sorted(state.get("pending", {}).items()):
            yield ("tool", {"id": slot["id"], "name": slot["name"],
                            "arguments": parse_tool_args(slot["args"])})

    def models(self, data, conf):
        platform = is_openai_platform(conf["base_url"])
        # OpenAI's own listing is narrowed to its conversational families; a
        # compatible server names its models however it likes.
        return [m for m in super().models(data, conf)
                if not _NOT_CHAT.search(m["id"]) and (not platform or _OPENAI_CHAT_FAMILIES.match(m["id"]))]

    def transcription(self, conf):
        # OpenAI surely transcribes; a compatible server (DeepSeek) may not.
        return 2 if is_openai_platform(conf["base_url"]) else 1

    def transcription_request(self, conf, model, language, filename, content_type, audio):
        fields = {"model": model, **({"language": language} if language else {})}
        body, multipart_type = multipart_body(fields, filename, content_type, audio)
        return URLRequest(f"{conf['base_url']}/v1/audio/transcriptions", data=body, headers={
            "Authorization": f"Bearer {conf['api_key']}",
            "Content-Type": multipart_type,
        })

    def transcript(self, data):
        return (data.get("text") or "").strip()
