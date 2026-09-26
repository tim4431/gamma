"""Provider-agnostic AI transport: open a call, read or stream its reply,
count its tokens. Everything that differs between providers lives on the
protocol adapters (gamma/ai_protocols); route handlers deal in the common
``messages`` representation and call :func:`call_ai` or :func:`open_ai`."""

import json
import re
import urllib.error
import urllib.request

from fastapi import HTTPException

from . import ai_protocols, ai_usage
from .logbuf import log


def protocol(runtime, entry) -> str:
    """The protocol id of a model registry entry's provider."""
    return runtime["providers"][entry["provider"]]["protocol"]


def wire_protocol(runtime, entry, tools=None) -> str:
    """The wire a call actually goes over (an OpenAI entry's tool calls go
    over OpenAI's Responses API — OpenAIChat.wire)."""
    conf = runtime["providers"][entry["provider"]]
    return ai_protocols.of(conf).wire(conf, tools).id


class UpstreamError(RuntimeError):
    """Provider HTTP error with the status attached for fallback decisions."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class AllowanceExhausted(HTTPException):
    """A call on one of the server's shared entries after the account used
    up its shared AI allowance (docs/dev/guests.md). An HTTPException (429)
    so a route that lets it through answers with it as is; ``str()`` is the
    detail, for the in-body and stream error paths."""

    def __init__(self, used: int, limit: int):
        super().__init__(status_code=429, detail=(
            f"This server's shared AI allowance for your account is used up ({used} of {limit} "
            "tokens in the last 24 hours). Add your own key in Settings → AI, or try again later."))
        self.used, self.limit = used, limit

    def __str__(self):
        return self.detail


def check_allowance(conf: dict) -> None:
    """Refuse a call through ``conf`` (a runtime provider conf) when it is a
    shared entry whose account has used up its allowance. ai_runtime marks
    only shared confs under a non-zero limit (``conf["allowance"]``), so an
    account's own entries never get here. The count is read fresh on every
    call: an agent loop or a translation run stops at the limit, not one
    request after it."""
    allowance = conf.get("allowance")
    if not allowance:
        return
    used = ai_usage.shared_used(allowance["user"])
    if used >= allowance["limit"]:
        raise AllowanceExhausted(used, allowance["limit"])


def _summarize_error_body(body: str) -> str:
    """The human-readable core of a provider error body: JSON errors reduce
    to their message field, HTML error pages (a proxy's 502 page) to their
    <title> — never the raw markup, which is noise in any UI."""
    text = (body or "").strip()
    if re.match(r"(?i)<(!doctype|html|head|body)\b", text):
        title = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
        return re.sub(r"\s+", " ", title.group(1)).strip() if title else ""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    for node in (data.get("error"), data.get("detail"), data) if isinstance(data, dict) else ():
        if isinstance(node, str) and node.strip():
            return node.strip()
        if isinstance(node, dict) and isinstance(node.get("message"), str) and node["message"].strip():
            return node["message"].strip()
    return text


def upstream_detail(error: urllib.error.HTTPError, cap: int = 500) -> str:
    """Return an actionable provider failure including its response body."""
    body = ""
    try:
        body = _summarize_error_body(error.read().decode("utf-8", "replace")[:8192])
    except Exception:
        pass
    return f"upstream {error.code}: {body[:cap] or error.reason}"


def open_ai(
    messages, system, entry, runtime, pdf_b64s=None, effort="",
    max_tokens=8192, timeout=60, images=None, stream=False, tools=None,
):
    """Open a provider call without consuming response bytes. The one door
    every token-spending call goes through (call_ai too): a shared entry's
    allowance is checked here (AllowanceExhausted, a 429)."""
    conf = runtime["providers"][entry["provider"]]
    check_allowance(conf)
    wire = ai_protocols.of(conf).wire(conf, tools)
    request = wire.request(conf, messages, system, entry["model"], pdf_b64s,
                           effort, max_tokens, images, stream, tools)
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        detail = upstream_detail(error)
        log.warning(f"[ai] {detail}")
        raise UpstreamError(error.code, detail)


def normalize_usage(raw, provider_protocol) -> dict | None:
    """One shape for every provider's token report (Protocol.usage):
    ``{input, output, cache_read, cache_write}``, None without counts."""
    return ai_protocols.get(provider_protocol).usage(raw)


def add_usage(total: dict | None, usage: dict | None) -> dict | None:
    """Sum two normalized usage dicts (either may be None)."""
    if not usage:
        return total
    if not total:
        return dict(usage)
    return {k: total.get(k, 0) + usage.get(k, 0) for k in ("input", "output", "cache_read", "cache_write")}


def read_reply(response, provider_protocol, on_usage=None) -> str:
    """Read the full reply text from an open provider response. ``on_usage``
    (a callable taking the normalized usage dict) hears the token counts
    when the provider reports them."""
    return ai_protocols.get(provider_protocol).read_reply(response, on_usage)


def call_ai(
    messages, system, entry, runtime, pdf_b64s=None, effort="",
    max_tokens=8192, timeout=60, images=None, on_usage=None,
):
    """Send a chat and return its complete reply text."""
    with open_ai(
        messages, system, entry, runtime, pdf_b64s,
        effort, max_tokens, timeout, images,
    ) as response:
        return read_reply(response, protocol(runtime, entry), on_usage)


def sse_events(response, provider_protocol):
    """The events of a streamed reply on one wire (Protocol.events):
    ``("text", delta)``, ``("tool_delta", {id, name, json})``, ``("tool",
    {id, name, arguments})`` and a last ``("usage", {...})``."""
    return ai_protocols.get(provider_protocol).events(response)


def sse_deltas(response, provider_protocol, on_usage=None):
    """Yield text deltas from a provider's SSE response; ``on_usage`` hears
    the stream's token counts."""
    for kind, data in sse_events(response, provider_protocol):
        if kind == "text":
            yield data
        elif kind == "usage" and on_usage:
            on_usage(data)


def _partial_json_string(raw: str):
    """Decode the *unterminated* JSON string body ``raw`` (everything after
    its opening quote) as far as it goes: a trailing lone backslash or a
    half-written ``\\uXXXX`` is dropped rather than failing."""
    s = raw
    # Trim an incomplete escape at the very end (backslash run of odd length,
    # or \u with fewer than 4 hex digits after it).
    m = re.search(r'(\\+)$', s)
    if m and len(m.group(1)) % 2 == 1:
        s = s[:-1]
    m = re.search(r'(?<!\\)((?:\\\\)*)\\u([0-9a-fA-F]{0,3})$', s)
    if m:
        s = s[:m.start(2) - 2]
    try:
        return json.loads('"' + s + '"')
    except ValueError:
        return None


def _scan_json_string(s: str, i: int):
    """``s[i]`` is an opening quote: return ``(end_index_exclusive, complete)``
    — the index just past the closing quote, or ``len(s)`` when the string is
    still open."""
    j = i + 1
    n = len(s)
    while j < n:
        ch = s[j]
        if ch == "\\":
            j += 2
            continue
        if ch == '"':
            return j + 1, True
        j += 1
    return n, False


def partial_json_object(raw: str) -> dict:
    """Best-effort read of a JSON object that is still being streamed: every
    top-level key whose STRING value is complete, plus the one string value
    currently being written (decoded as far as it goes). Non-string values
    (numbers, lists, nested objects) are skipped — the tool argument the UI
    previews live (a block's markdown) is a string, and ids are strings.
    Never raises; ``{}`` when nothing is readable yet."""
    out = {}
    s = raw or ""
    i = s.find("{")
    if i < 0:
        return out
    i += 1
    n = len(s)
    while i < n:
        # key
        q = s.find('"', i)
        if q < 0:
            break
        end, complete = _scan_json_string(s, q)
        if not complete:
            break
        try:
            key = json.loads(s[q:end])
        except ValueError:
            break
        i = end
        while i < n and s[i] in " \t\r\n":
            i += 1
        if i >= n or s[i] != ":":
            break
        i += 1
        while i < n and s[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        if s[i] == '"':
            end, complete = _scan_json_string(s, i)
            if complete:
                try:
                    out[key] = json.loads(s[i:end])
                except ValueError:
                    break
                i = end
            else:
                value = _partial_json_string(s[i + 1:])
                if value is not None:
                    out[key] = value
                break
        else:
            # Skip a non-string value: scan to the next top-level comma or the
            # closing brace, honouring nesting and strings.
            depth = 0
            j = i
            while j < n:
                ch = s[j]
                if ch == '"':
                    j, complete = _scan_json_string(s, j)
                    if not complete:
                        return out
                    continue
                if ch in "[{":
                    depth += 1
                elif ch in "]}":
                    if depth == 0:
                        break
                    depth -= 1
                elif ch == "," and depth == 0:
                    break
                j += 1
            if j >= n:
                break
            i = j
        # after a value: "," → next key, "}" → done
        while i < n and s[i] in " \t\r\n":
            i += 1
        if i >= n or s[i] == "}":
            break
        if s[i] == ",":
            i += 1
    return out


def partial_json_strings(raw: str) -> list:
    """Best-effort read of a JSON array of strings still being streamed (the
    translation reply): the complete elements plus the one being written,
    decoded as far as it goes. Prose or a code fence before the ``[`` is
    skipped. Never raises; ``[]`` when nothing is readable yet."""
    out = []
    s = raw or ""
    i = s.find("[")
    if i < 0:
        return out
    i += 1
    n = len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n,":
            i += 1
        if i >= n or s[i] == "]":
            break
        if s[i] != '"':
            # null or some other non-string element: stop previewing here.
            break
        end, complete = _scan_json_string(s, i)
        if complete:
            try:
                out.append(json.loads(s[i:end]))
            except ValueError:
                break
            i = end
        else:
            value = _partial_json_string(s[i + 1:])
            if value is not None:
                out.append(value)
            break
    return out
