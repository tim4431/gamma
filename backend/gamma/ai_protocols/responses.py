"""The Responses API — shared by OpenAI's platform /v1/responses (the wire
an OpenAI entry switches to for tool calls) and the ChatGPT subscription
backend (chatgpt.py)."""

import json
from urllib.request import Request as URLRequest

from .base import TOOL_IMAGES_NOTE, Protocol, as_int, parse_tool_args, tool_image_turns


def responses_input(messages, pdf_b64s=None, images=None) -> list:
    """Map the common turn list to Responses API input items."""
    items = []
    image_turn = lambda imgs: {"type": "message", "role": "user", "content": [  # noqa: E731
        {"type": "input_text", "text": TOOL_IMAGES_NOTE},
        *[{"type": "input_image", "image_url": f"data:{media_type};base64,{data}"}
          for media_type, data in imgs]]}
    image_turns = []  # never the turn the user's own attachments ride on
    for message, is_image_turn in tool_image_turns(messages, image_turn):
        if is_image_turn:
            items.append(message)
            image_turns.append(message)
        elif message["role"] == "tool":
            items.append({"type": "function_call_output", "call_id": message["call_id"],
                          "output": message["content"]})
        elif message["role"] == "assistant":
            if message.get("content") or not message.get("tool_calls"):
                content = [{"type": "output_text", "text": message["content"]}]
                items.append({"type": "message", "role": "assistant", "content": content})
            for call in message.get("tool_calls") or []:
                items.append({"type": "function_call", "call_id": call["id"],
                              "name": call["name"], "arguments": json.dumps(call["arguments"])})
        else:
            content = [{"type": "input_text", "text": message["content"]}]
            items.append({"type": "message", "role": "user", "content": content})
    if pdf_b64s or images:
        last = next((item for item in reversed(items)
                     if item.get("type") == "message" and item.get("role") == "user"
                     and not any(item is turn for turn in image_turns)), items[-1])
        last["content"] = [
            *[{"type": "input_file", "filename": f"document-{index + 1}.pdf",
               "file_data": f"data:application/pdf;base64,{data}"}
              for index, data in enumerate(pdf_b64s or [])],
            *[{"type": "input_image", "image_url": f"data:{media_type};base64,{data}"}
              for media_type, data in (images or [])],
            *last["content"],
        ]
    return items


def responses_tools(tools) -> list:
    # Responses API uses a flattened function-tool shape (no "function" nesting).
    return [{"type": "function", "name": t["name"], "description": t["description"],
             "parameters": t["parameters"], "strict": False} for t in (tools or [])]


def responses_body(messages, model, pdf_b64s, images, tools, effort) -> dict:
    """The request body both Responses backends share."""
    body = {
        "model": model,
        "input": responses_input(messages, pdf_b64s, images),
        "tools": responses_tools(tools),
        "tool_choice": "auto",
        # Batched calls (e.g. renaming a whole folder in one round) — a call
        # per round-trip would eat the tool-round budget one page at a time.
        "parallel_tool_calls": bool(tools),
        "store": False,
        "stream": True,
    }
    if effort:
        body["reasoning"] = {"effort": effort}
    return body


class ResponsesWire(Protocol):
    """The Responses stream and token report; a backend adds its request."""

    streams_only = True  # always SSE — read_reply joins the deltas

    def usage(self, raw):
        if not isinstance(raw, dict):
            return None
        usage = {"input": as_int(raw.get("input_tokens")), "output": as_int(raw.get("output_tokens")),
                 "cache_read": as_int((raw.get("input_tokens_details") or {}).get("cached_tokens")),
                 "cache_write": 0}
        return usage if (usage["input"] or usage["output"]) else None

    def stream_event(self, event, state):
        kind = event.get("type") or ""
        items = state.setdefault("items", {})  # item id -> {id (call_id), name, json} being streamed
        if kind == "response.output_text.delta":
            if event.get("delta"):
                yield ("text", event["delta"])
        elif kind == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                items[item.get("id") or ""] = {"id": item.get("call_id") or item.get("id") or "",
                                               "name": item.get("name") or "", "json": ""}
        elif kind == "response.function_call_arguments.delta":
            slot = items.get(event.get("item_id") or "")
            if slot is not None:
                slot["json"] += event.get("delta") or ""
                yield ("tool_delta", dict(slot))
        elif kind == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                items.pop(item.get("id") or "", None)
                yield ("tool", {"id": item.get("call_id") or item.get("id") or "",
                                "name": item.get("name") or "",
                                "arguments": parse_tool_args(item.get("arguments"))})
        elif kind == "response.completed":
            response = event.get("response") or {}
            state["stop"] = response.get("status") or "completed"
            state["usage"] = self.usage(response.get("usage")) or state["usage"]
        elif kind in ("response.failed", "error"):
            error = (event.get("response") or {}).get("error") or {} if kind == "response.failed" else event
            raise RuntimeError(error.get("message") or "stream error")


class OpenAIResponses(ResponsesWire):
    """OpenAI's platform /v1/responses, used instead of Chat Completions when
    a call carries function tools: reasoning models (gpt-5.x) reject tools +
    reasoning_effort on /v1/chat/completions, and OpenAI's guidance is the
    Responses API. Only against OpenAI itself — see OpenAIChat.wire."""

    id = "openai-responses"
    label = "OpenAI Responses API"
    entry = False

    def request(self, conf, messages, system, model, pdf_b64s=None, effort="",
                max_tokens=8192, images=None, stream=False, tools=None):
        body = {**responses_body(messages, model, pdf_b64s, images, tools, effort),
                "max_output_tokens": max_tokens}
        if system:
            body["instructions"] = system
        return URLRequest(f"{conf['base_url']}/v1/responses", data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {conf['api_key']}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        })


OPENAI_RESPONSES = OpenAIResponses()
