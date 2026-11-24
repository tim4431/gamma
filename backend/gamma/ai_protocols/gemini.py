"""Gemini through a Google sign-in: Google's Code Assist backend
(cloudcode-pa.googleapis.com/v1internal), reached with the tokens of Gemini
CLI's OAuth flow (gamma/gemini_oauth.py). Usage counts against the account's
Gemini CLI quota. The backend wraps Gemini's own generateContent request and
answer (``{model, project, request}`` → ``{response}``), streams it as
server-sent events, and lists no models: the account's quota buckets name
the models it may use. Those contracts are Gemini CLI's, unofficial, and may
need maintenance when upstream changes.

The common turns map to Gemini ``contents``: an assistant turn is a
``model`` turn of text and ``functionCall`` parts, a tool result a
``functionResponse`` part in a user turn (its pictures beside it), and a
turn's PDFs and pictures inline data. Gemini 3 signs each step's first
function call (``thoughtSignature``) and refuses the next round without the
signature, so the stream keeps them on the turn's ``reasoning``; a turn
replayed without one (an older conversation) sends Google's documented
stand-in."""

import json
import uuid
from datetime import datetime
from urllib.request import Request as URLRequest

from .. import gemini_oauth
from .base import Protocol, as_int, attach_target, sse_events, turn_attachments

# The reasoning fields a streamed turn keeps: the thought summaries as text
# (what the chat shows and saves), and the function calls' signatures by
# call id, which stay within the reply that produced them.
THOUGHT = "thought"
SIGNATURES = "thought_signatures"
# What Google's docs give a function call whose signature is lost.
SKIP_SIGNATURE = "skip_thought_signature_validator"
# Thinking counts toward Gemini's output cap: the reply's cap goes on top
# of the budget an effort asks for, or of this when none is asked for (the
# model's own dynamic thinking), within the models' 64k output limit.
EFFORT_BUDGETS = {"none": 128, "minimal": 128, "low": 1024, "medium": 8192, "high": 24576}
DYNAMIC_THINKING = 24576
OUTPUT_LIMIT = 65536
_STOPS = {"MAX_TOKENS": "max_tokens", "STOP": "stop"}


def _inline(media_type: str, data: str) -> dict:
    return {"inlineData": {"mimeType": media_type, "data": data}}


def _user_parts(message, pdf_b64s=(), images=()) -> list:
    """A user turn's parts: its PDFs, its pictures, then its text (none
    when empty: Gemini refuses an empty text part)."""
    files, pictures = turn_attachments(message, pdf_b64s, images)
    parts = [_inline("application/pdf", data) for data in files]
    parts += [_inline(media_type, data) for media_type, data in pictures]
    if (message.get("content") or "").strip():
        parts.append({"text": message["content"]})
    return parts


def contents(messages, pdf_b64s=None, images=None) -> list:
    """The common turn list as Gemini ``contents``. Turns of one role in a
    row become one (tool results, then the user's next message); the
    request's own attachments go on the turn attach_target names."""
    target = attach_target(messages, pdf_b64s, images)
    names = {}  # call id -> tool name, for the functionResponse
    out = []

    def add(role, parts):
        if not parts:
            return
        if out and out[-1]["role"] == role:
            out[-1]["parts"].extend(parts)
        else:
            out.append({"role": role, "parts": parts})
    for m in messages:
        if m["role"] == "tool":
            response = {"functionResponse": {"name": names.get(m["call_id"], m["call_id"]),
                                             "response": {"output": m["content"]}}}
            add("user", [response, *[_inline(media_type, data) for media_type, data in m.get("images") or []]])
        elif m["role"] == "assistant":
            kept = m.get("reasoning") if isinstance(m.get("reasoning"), dict) else {}
            signed = kept.get(SIGNATURES) if isinstance(kept.get(SIGNATURES), dict) else {}
            parts = [{"text": m["content"]}] if (m.get("content") or "").strip() else []
            for i, call in enumerate(m.get("tool_calls") or []):
                names[call["id"]] = call["name"]
                part = {"functionCall": {"name": call["name"], "args": call["arguments"]}}
                signature = signed.get(call["id"]) or (SKIP_SIGNATURE if not signed and i == 0 else "")
                parts.append({**part, "thoughtSignature": signature} if signature else part)
            add("model", parts)
        elif m["role"] == "user":
            add("user", _user_parts(m, pdf_b64s, images) if m is target else _user_parts(m))
    return out


def _reset_at(stamp) -> int:
    try:
        return int(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return 0


class GeminiSignIn(Protocol):
    id = gemini_oauth.PROTOCOL
    label = "Gemini (Google sign-in)"
    auth = "oauth"
    oauth = gemini_oauth
    entry_name = "Gemini"
    streams_only = True
    has_account_usage = True
    note = ("Google's terms forbid using Gemini CLI's sign-in in other apps, and accounts have been suspended "
            "for it. A Gemini API key from Google AI Studio is the supported way.")

    def request(self, conf, messages, system, model, pdf_b64s=None, effort="",
                max_tokens=8192, images=None, stream=False, tools=None, cache_key="", speed=""):
        budget = EFFORT_BUDGETS.get(effort or "")
        thinking = {"includeThoughts": True, **({"thinkingBudget": budget} if budget is not None else {})}
        inner = {"contents": contents(messages, pdf_b64s, images),
                 "generationConfig": {"maxOutputTokens": min(OUTPUT_LIMIT, max_tokens + (budget or DYNAMIC_THINKING)),
                                      "thinkingConfig": thinking}}
        if system:
            inner["systemInstruction"] = {"role": "user", "parts": [{"text": system}]}
        functions = [t for t in tools or [] if not t.get("hosted")]  # this wire hosts no tools
        if functions:
            inner["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t["description"], "parametersJsonSchema": t["parameters"]}
                for t in functions]}]
        if cache_key:
            inner["session_id"] = cache_key  # one per conversation, as Gemini CLI's session
        body = {"model": model, "project": conf.get("account_id", ""), "user_prompt_id": uuid.uuid4().hex,
                "request": inner}
        # Always streamed: a caller wanting the reply whole joins the events.
        return URLRequest(f"{conf['base_url']}:streamGenerateContent?alt=sse", data=json.dumps(body).encode(),
                          headers=self._headers(conf, accept="text/event-stream"))

    def _headers(self, conf, accept="application/json") -> dict:
        return {"Authorization": f"Bearer {conf['api_key']}", "Content-Type": "application/json",
                "Accept": accept, "User-Agent": gemini_oauth.USER_AGENT}

    def sse(self, response):
        return sse_events(response)  # an event may span several data: lines (Gemini CLI reads them so)

    def usage(self, raw):
        if not isinstance(raw, dict):
            return None
        # promptTokenCount includes the cached part; thoughts are output.
        usage = {"input": as_int(raw.get("promptTokenCount")) + as_int(raw.get("toolUsePromptTokenCount")),
                 "output": as_int(raw.get("candidatesTokenCount")) + as_int(raw.get("thoughtsTokenCount")),
                 "cache_read": as_int(raw.get("cachedContentTokenCount")), "cache_write": 0}
        return usage if (usage["input"] or usage["output"]) else None

    def stream_event(self, event, state):
        if isinstance(event.get("error"), dict):
            raise RuntimeError(event["error"].get("message") or "stream error")
        reply = event.get("response") if isinstance(event.get("response"), dict) else event
        usage = self.usage(reply.get("usageMetadata"))
        if usage:
            state["usage"] = usage  # cumulative: the last one wins
        blocked = (reply.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            raise RuntimeError(f"Gemini refused the request ({blocked})")
        candidate = (reply.get("candidates") or [{}])[0] or {}
        for part in (candidate.get("content") or {}).get("parts") or []:
            if not isinstance(part, dict):
                continue
            call = part.get("functionCall")
            if isinstance(call, dict) and call.get("name"):
                call_id = str(call.get("id") or f"call_{uuid.uuid4().hex[:12]}")
                if part.get("thoughtSignature"):
                    state.setdefault("signatures", {})[call_id] = part["thoughtSignature"]
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                state.setdefault("calls", []).append({"id": call_id, "name": call["name"], "arguments": args})
            elif part.get("thought"):
                if part.get("text"):
                    state["thought"] = state.get("thought", "") + part["text"]
            elif part.get("text"):
                yield ("text", part["text"])
        reason = candidate.get("finishReason")
        if reason:
            state["stop"] = _STOPS.get(reason, str(reason).lower())

    def stream_end(self, state):
        # Calls arrive whole; the thinking and the signatures go first, as
        # the turn the next round replays keeps them.
        reasoning = {**({THOUGHT: state["thought"]} if state.get("thought") else {}),
                     **({SIGNATURES: state["signatures"]} if state.get("signatures") else {})}
        if reasoning:
            yield ("reasoning", reasoning)
        for call in state.get("calls") or []:
            yield ("tool", call)

    # --- models and quota: the account's quota buckets --------------------

    def account_usage_request(self, conf):
        # Always the protocol's own endpoint, never an entry's: a sign-in's
        # bearer token can't be sent elsewhere.
        return URLRequest(f"{self.base_url}:retrieveUserQuota", data=json.dumps(
            {"project": conf.get("account_id", "")}).encode(), headers=self._headers(conf), method="POST")

    def models_request(self, conf):
        return self.account_usage_request(conf)

    def models(self, data, conf):
        found = []
        for bucket in data.get("buckets") or []:
            model = str(bucket.get("modelId") or "").strip() if isinstance(bucket, dict) else ""
            if model and model not in found:
                found.append(model)
        return [{"id": m, "context_window": 0, "efforts": None, "speeds": None, "images": None, "price": None}
                for m in sorted(found)]

    def account_usage(self, data):
        windows = []
        for bucket in data.get("buckets") or []:
            fraction = bucket.get("remainingFraction") if isinstance(bucket, dict) else None
            if not isinstance(fraction, (int, float)) or isinstance(fraction, bool):
                continue
            left = max(0.0, min(100.0, float(fraction) * 100))
            name = str(bucket.get("modelId") or "Usage")
            if bucket.get("tokenType") and bucket["tokenType"] != "REQUESTS":
                name = f"{name} ({str(bucket['tokenType']).lower()})"
            windows.append({"name": name, "used_percent": 100.0 - left, "remaining_percent": left,
                            "window_seconds": 0, "reset_at": _reset_at(bucket.get("resetTime"))})
        return {"plan_type": "", "windows": windows, "credits": None}

    def ping_request(self, conf):
        # The quota answers 401 on a dead sign-in, for free.
        return self.account_usage_request(conf)

    def catalog_hints(self, conf):
        return ["google"]  # models.dev lists these models under Google
