"""Anthropic Messages API — Anthropic itself, and the services that speak it
(Kimi, GLM, … behind their own base URL)."""

import json
from urllib.request import Request as URLRequest

from .base import Protocol, as_int, attach_index, parse_tool_args

API_VERSION = "2023-06-01"


def _messages(messages) -> list:
    """Map the common turn list to Anthropic content blocks: tool results are
    tool_result blocks in a user turn (consecutive ones coalesced — they must
    directly follow the assistant's tool_use turn), tool calls become tool_use
    blocks after the assistant's text."""
    out = []
    for m in messages:
        if m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["call_id"], "content": m["content"]}
            if m.get("images"):
                block["content"] = [{"type": "text", "text": m["content"]}] + [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
                    for media_type, data in m["images"]]
            prev = out[-1] if out else None
            if (prev and prev["role"] == "user" and isinstance(prev["content"], list)
                    and prev["content"] and prev["content"][0].get("type") == "tool_result"):
                prev["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            content = [{"type": "text", "text": m["content"]}] if (m.get("content") or "").strip() else []
            content += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]}
                        for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": content})
        else:
            prev = out[-1] if out else None
            if (m["role"] == "user" and prev and prev["role"] == "user"
                    and isinstance(prev["content"], list)
                    and prev["content"] and prev["content"][0].get("type") == "tool_result"):
                # A tool-only assistant reply leaves its results as the last
                # user turn; fold the next real user message into it so roles
                # keep alternating. Attachment turns already carry block lists.
                prev["content"].extend(
                    m["content"] if isinstance(m["content"], list)
                    else [{"type": "text", "text": m["content"]}])
            else:
                out.append({"role": m["role"], "content": m["content"]})
    return out


class Anthropic(Protocol):
    id = "anthropic"
    label = "Anthropic Messages API"

    def request(self, conf, messages, system, model, pdf_b64s=None, effort="",
                max_tokens=8192, images=None, stream=False, tools=None):
        messages = [dict(m) for m in messages]  # attachment injection must not mutate the caller's turn list
        if pdf_b64s or images:
            last = messages[attach_index(messages)]
            last["content"] = [
                *[{"type": "document",
                   "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
                  for data in (pdf_b64s or [])],
                *[{"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
                  for media_type, data in (images or [])],
                {"type": "text", "text": last["content"]},
            ]
        body = {"model": model, "max_tokens": max_tokens, "system": system,
                "messages": _messages(messages)}
        if tools:
            body["tools"] = [{"name": t["name"], "description": t["description"],
                              "input_schema": t["parameters"]} for t in tools]
        if effort:
            # "minimal" is OpenAI's lowest level; Anthropic's is "low".
            body["output_config"] = {"effort": "low" if effort == "minimal" else effort}
        if stream:
            body["stream"] = True
        return URLRequest(f"{conf['base_url']}/v1/messages", data=json.dumps(body).encode(), headers={
            "x-api-key": conf["api_key"],
            "anthropic-version": API_VERSION,
            "Content-Type": "application/json",
        })

    def reply_text(self, data):
        text = "".join(item.get("text", "") for item in data.get("content", []) if item.get("type") == "text")
        if not text.strip():
            raise RuntimeError(f"empty response (stop_reason={data.get('stop_reason', 'unknown')})")
        return text

    def usage(self, raw):
        # Cached and freshly written prompt parts are reported beside the
        # uncached ones; summed, the way OpenAI's prompt_tokens includes them.
        if not isinstance(raw, dict):
            return None
        cache_read = as_int(raw.get("cache_read_input_tokens"))
        cache_write = as_int(raw.get("cache_creation_input_tokens"))
        usage = {"input": as_int(raw.get("input_tokens")) + cache_read + cache_write,
                 "output": as_int(raw.get("output_tokens")),
                 "cache_read": cache_read, "cache_write": cache_write}
        return usage if (usage["input"] or usage["output"]) else None

    def stream_event(self, event, state):
        kind = event.get("type")
        if kind == "message_start":
            # Input counts arrive up front; the output count comes with the
            # final message_delta (cumulative, so the last one wins).
            state["usage"] = self.usage((event.get("message") or {}).get("usage"))
        elif kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                state["tool"] = {"id": block.get("id") or "", "name": block.get("name") or "", "json": ""}
        elif kind == "content_block_delta":
            delta = event.get("delta") or {}
            tool = state.get("tool")
            if delta.get("type") == "input_json_delta" and tool is not None:
                tool["json"] += delta.get("partial_json") or ""
                yield ("tool_delta", dict(tool))
            elif delta.get("text"):
                yield ("text", delta["text"])
        elif kind == "content_block_stop":
            tool = state.pop("tool", None)
            if tool is not None:
                yield ("tool", {"id": tool["id"], "name": tool["name"],
                                "arguments": parse_tool_args(tool["json"])})
        elif kind == "message_delta":
            state["stop"] = (event.get("delta") or {}).get("stop_reason") or state["stop"]
            delta_usage = self.usage(event.get("usage"))
            if delta_usage:
                usage = state["usage"] or {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
                usage["output"] = delta_usage["output"]
                if delta_usage["input"] and not usage["input"]:
                    usage.update(input=delta_usage["input"], cache_read=delta_usage["cache_read"],
                                 cache_write=delta_usage["cache_write"])
                state["usage"] = usage
        elif kind == "error":
            raise RuntimeError((event.get("error") or {}).get("message") or "stream error")

    def models_request(self, conf):
        return URLRequest(f"{conf['base_url']}/v1/models?limit=100", headers={
            "x-api-key": conf["api_key"],
            "anthropic-version": API_VERSION,
            "Accept": "application/json",
            "User-Agent": "Gamma/model-catalog",
        })
