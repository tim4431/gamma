"""The protocol adapter interface, and the helpers every wire shares.

A ``Protocol`` owns everything that differs between providers — the chat
request, the reply and its stream, the token report, the model listing,
the credential check, the account's quota, what the provider accepts — so
the routes and the chat loop never branch on a protocol id. ``conf`` is one
resolved provider entry (``ai_settings.ai_runtime``): ``{protocol,
base_url, api_key, name}`` plus protocol extras (``account_id``).

Messages come in one common shape: ``{role, content}`` turns, an assistant
turn may carry ``tool_calls`` ([{id, name, arguments-dict}]), and a
``{"role": "tool", "call_id", "content"}`` entry is a tool result, which may
carry ``images`` ([(media_type, base64)] — a rendered PDF page). Tools are
declared once as ``{name, description, parameters}`` (gamma/ai_tools.py);
each adapter maps both to its wire.

Two knobs ride along with a call: ``effort`` (how hard the model thinks) and
``speed`` (which of the provider's service tiers serves it — SPEED_ORDER).
"""

import json
import secrets
import urllib.parse
from urllib.request import Request as URLRequest

from ..config import AI_BASE_URLS

# The OpenAI-style wires only accept text in a tool result, so the pictures
# follow the round's results as one user turn the model reads in call order.
TOOL_IMAGES_NOTE = "Pictures returned by the tool calls above, in call order:"

EMPTY_REPLY_HINT = ("a reasoning model may have spent the whole token budget thinking; "
                    "try effort: low or a shorter request")


class NotAnAIStream(RuntimeError):
    """A streamed call answered without a single server-sent event: the
    endpoint is reachable but is not an AI API (a web page, a proxy's login
    screen, a base URL missing its path). ai_client.failure_kind calls it
    ``bad_endpoint``."""

# The keys a model listing may carry a context window under: Anthropic's
# max_input_tokens, the Codex backend's context_window, OpenRouter's /
# Together's / Fireworks' context_length, Mistral's max_context_length,
# vLLM's max_model_len.
WINDOW_KEYS = ("max_input_tokens", "context_window", "context_length", "max_context_length", "max_model_len")


def tool_image_turns(messages, make_turn):
    """The common turn list with every run of tool results followed by one
    user turn carrying their pictures — ``make_turn(images)`` builds it in
    the wire's shape. Yields (message, is_image_turn)."""
    pending = []
    for m in messages:
        if m["role"] != "tool" and pending:
            yield make_turn(pending), True
            pending = []
        yield m, False
        if m["role"] == "tool":
            pending.extend(m.get("images") or [])
    if pending:
        yield make_turn(pending), True


def attach_index(messages) -> int:
    """Index of the message attachments ride on: the last plain user turn
    (tool-result entries can follow it in agent rounds)."""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index]["role"] == "user":
            return index
    return len(messages) - 1


# The stop reasons that mean the reply hit the output cap (Anthropic,
# Chat Completions, the Responses API's incomplete status) rather than
# ending on its own.
TRUNCATED_STOPS = {"max_tokens", "length", "incomplete", "max_output_tokens"}


def truncated_stop(reason: str) -> bool:
    return (reason or "") in TRUNCATED_STOPS


def parse_tool_args(raw) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        parsed = None
    return parsed if isinstance(parsed, dict) else {}


def as_int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def listed_window(row) -> int:
    """A model listing row's context window, 0 when it names none."""
    if not isinstance(row, dict):
        return 0
    for key in WINDOW_KEYS:
        value = row.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return 0


def listed_efforts(row) -> list | None:
    """A model listing row's reasoning-effort levels, in order: Anthropic's
    ``capabilities.effort`` ({level: {supported}}) or the Codex backend's
    ``supported_reasoning_levels`` ([{effort}]). ``[]`` when the row says
    the model takes none, None when it doesn't say."""
    if not isinstance(row, dict):
        return None
    caps = row.get("capabilities")
    effort = caps.get("effort") if isinstance(caps, dict) else None
    if isinstance(effort, dict):
        if not effort.get("supported", True):
            return []
        return [str(level) for level, v in effort.items()
                if level != "supported" and isinstance(v, dict) and v.get("supported")]
    levels = row.get("supported_reasoning_levels")
    if isinstance(levels, list):
        names = [(x.get("effort") if isinstance(x, dict) else x) for x in levels]
        return [str(n) for n in names if isinstance(n, str) and n]
    return None


# The speed (service) tiers a call may ask for, in the order the pickers
# offer them: "flex" trades latency for a lower price, "fast" buys the
# provider's premium low-latency routing at a higher one. Each wire maps
# these canonical names to its own values (``Protocol.speeds``); "" = no
# preference — the field is left out and the provider routes as usual.
SPEED_ORDER = ("flex", "fast")
# What a listing may call them: the Codex catalog's service-tier ids
# ("priority" is its fast one) beside the canonical names.
SPEED_ALIASES = {"flex": "flex", "fast": "fast", "priority": "fast"}


def listed_speeds(row) -> list | None:
    """A model listing row's speed tiers, in SPEED_ORDER: the Codex backend's
    ``service_tiers`` ([{id, name}], or the older ``additional_speed_tiers``
    [id]) or an Anthropic-style ``capabilities.speed`` ({tier: {supported}}).
    ``[]`` when the row says the model takes none, None when it doesn't say —
    Anthropic's listing carries no speed facts, so its models fall back to
    what the wire itself can ask for (``Protocol.speed_tiers``)."""
    if not isinstance(row, dict):
        return None
    tiers = row.get("service_tiers")
    if not isinstance(tiers, list):
        tiers = row.get("additional_speed_tiers")  # the Codex catalog's older field
    names = None
    if isinstance(tiers, list):
        names = [t.get("id") if isinstance(t, dict) else t for t in tiers]
    else:
        caps = row.get("capabilities")
        speed = caps.get("speed") if isinstance(caps, dict) else None
        if isinstance(speed, dict):
            if not speed.get("supported", True):
                return []
            names = [tier for tier, v in speed.items()
                     if tier != "supported" and isinstance(v, dict) and v.get("supported")]
    if names is None:
        return None
    found = {SPEED_ALIASES.get(n) for n in names if isinstance(n, str)}
    return [name for name in SPEED_ORDER if name in found]


def sse_json(response):
    """The JSON events of a server-sent-events response, up to ``[DONE]``."""
    for raw in response:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except ValueError:
            continue


def bearer_json_request(url: str, key: str, **headers) -> URLRequest:
    return URLRequest(url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                                    "User-Agent": "Gamma/model-catalog", **headers})


class Protocol:
    """One wire protocol. Subclasses set the class attributes and override
    what their provider does differently; the defaults are the common
    OpenAI-shaped behaviour (a bearer key, GET {base}/v1/models)."""

    id = ""
    label = ""
    auth = "key"         # "oauth": the entry holds sign-in tokens, refreshed by ``oauth``
    oauth = None         # the sign-in module (needs_refresh / refresh) of an "oauth" protocol
    entry = True         # an entry may name it (False: a variant another protocol switches to)
    native_pdf = True    # the provider takes the PDF file itself, not only extracted text
    streams_only = False # the reply always arrives as SSE, even for a caller that wants it whole
    # The connect form's API-key field: what the provider's keys look like
    # (the placeholder) and where to make one. Only for the provider's own
    # endpoint — a custom base URL on the same wire shows neither.
    key_placeholder = ""
    key_url = ""
    # The speed tiers this wire can ask for: canonical name (SPEED_ORDER) ->
    # the value it sends. Empty = the wire has no speed control.
    speeds: dict = {}

    @property
    def base_url(self) -> str:
        """The default endpoint (env-overridable, config.AI_BASE_URLS)."""
        return AI_BASE_URLS.get(self.id, "")

    # --- chat --------------------------------------------------------------

    def wire(self, conf, tools=None) -> "Protocol":
        """The adapter a call actually goes over (a protocol may switch to a
        sibling wire for some calls)."""
        return self

    def request(self, conf, messages, system, model, pdf_b64s=None, effort="",
                max_tokens=8192, images=None, stream=False, tools=None,
                cache_key="", speed="") -> URLRequest:
        """The provider call. ``cache_key`` names the conversation (one
        opaque id per chat) for the provider's prompt cache: the wires that
        take a routing hint send it, the others ignore it. ``speed`` is a
        SPEED_ORDER name, sent as this wire's own value (``speed_value``) and
        left out when the wire can't ask for it. A tool spec with a
        ``hosted`` entry is the provider's own tool (hosted_web_search) and
        goes out as that entry."""
        raise NotImplementedError

    def speed_tiers(self, conf) -> list:
        """The speed tiers an entry may be asked for when its model listing
        names none (``listed_speeds``), in SPEED_ORDER: the wire's own, which
        a wire that only has them on the provider's own endpoint narrows."""
        return [name for name in SPEED_ORDER if name in self.speeds]

    def speed_value(self, speed) -> str:
        """A SPEED_ORDER name as this wire's own value, "" when it has none
        for it — then the request leaves the field out."""
        return self.speeds.get(speed or "", "")

    def hosted_web_search(self, conf) -> dict | None:
        """The provider's own web-search tool on this wire, as the tools
        entry to send, or None when it has none. A stream that used it
        yields ``("web_sources", [{url, title}])`` for the pages it found
        (gamma/search_services.py runs the search)."""
        return None

    def reply_text(self, data) -> str:
        """The reply text of a non-streamed response body."""
        raise NotImplementedError

    def usage(self, raw) -> dict | None:
        """The provider's token report as ``{input, output, cache_read,
        cache_write}``: ``input`` is the whole prompt as the provider counted
        it, ``cache_read`` / ``cache_write`` the parts of it that came from /
        went to the prompt cache. None when the object carries no counts."""
        raise NotImplementedError

    def read_reply(self, response, on_usage=None) -> str:
        """The full reply text of an open response; ``on_usage`` hears the
        token counts when the provider reports them."""
        if self.streams_only:
            parts = []
            for kind, data in self.events(response):
                if kind == "text":
                    parts.append(data)
                elif kind == "usage" and on_usage:
                    on_usage(data)
            return "".join(parts)
        data = json.loads(response.read())
        usage = self.usage(data.get("usage"))
        if usage and on_usage:
            on_usage(usage)
        return self.reply_text(data)

    def events(self, response):
        """Yield ``("text", delta)``, ``("tool", {id, name, arguments})`` and
        ``("tool_delta", {id, name, json})`` events from a streamed reply. A
        ``tool_delta`` carries the tool call's arguments as streamed SO FAR
        (raw, possibly truncated JSON — ai_client.partial_json_object) so a
        consumer can preview a long argument while the model is still
        writing it; the ``tool`` event with the parsed arguments always
        follows. A last ``("usage", {...})`` event reports the turn's token
        counts when the provider sent them, and ``("stop", reason)`` the
        provider's stop reason (``truncated_stop`` says whether it means
        the reply was cut off). Raises on a fully empty response (neither
        text nor tool calls) with the stop reason attached."""
        state = {"got": False, "stop": "", "usage": None}
        seen = False
        for event in sse_json(response):
            seen = True
            for out in self.stream_event(event, state):
                state["got"] = state["got"] or out[0] in ("text", "tool")
                yield out
        for out in self.stream_end(state):
            state["got"] = state["got"] or out[0] == "tool"
            yield out
        if state["usage"]:
            yield ("usage", state["usage"])
        if state["stop"] and state["got"]:
            yield ("stop", state["stop"])
        if not state["got"] and not seen:
            raise NotAnAIStream("the endpoint answered without any AI stream events — check the connection's base URL")
        if not state["got"]:
            raise RuntimeError(f"empty response (stop reason={state['stop'] or 'unknown'} — {EMPTY_REPLY_HINT})")

    def stream_event(self, event, state):
        """The events one parsed SSE event yields; ``state`` is the stream's
        scratch dict (``stop`` and ``usage`` are read at the end)."""
        raise NotImplementedError

    def stream_end(self, state):
        """Events owed once the stream ends (tool calls announced piecewise)."""
        return ()

    # --- models ------------------------------------------------------------

    def models_request(self, conf) -> URLRequest:
        """The provider's model listing — also the free way to check a
        credential (it 401s on a dead key without spending tokens)."""
        return bearer_json_request(f"{conf['base_url']}/v1/models", conf["api_key"])

    def models(self, data, conf) -> list:
        """The chat models of a listing body as ``[{id, context_window,
        efforts, speeds}]`` (0 = the listing names no window, efforts /
        speeds None = it names no effort levels / speed tiers —
        listed_efforts, listed_speeds), in the order to offer them."""
        rows = [r for r in (data.get("data") or []) if isinstance(r, dict) and r.get("id")]
        found = {}
        for row in rows:
            found.setdefault(str(row["id"]), row)
        return [{"id": mid, "context_window": listed_window(found[mid]),
                 "efforts": listed_efforts(found[mid]), "speeds": listed_speeds(found[mid])}
                for mid in sorted(found)]

    def ping_request(self, conf) -> URLRequest:
        """The free credential check behind the login connection check."""
        return self.models_request(conf)

    def catalog_hints(self, conf) -> list:
        """Names that pick this entry's provider among models.dev's listings
        of one model: by default the endpoint's host."""
        return [urllib.parse.urlparse(conf.get("base_url") or "").hostname or ""]

    # --- account and extras --------------------------------------------------

    # A subscription quota to show. API-key protocols have none that is
    # portable: they bill per token, behind each vendor's own billing API.
    has_account_usage = False

    def account_usage_request(self, conf) -> URLRequest:
        raise NotImplementedError

    def account_usage(self, data) -> dict:
        """``{plan_type, windows: [{name, used_percent, remaining_percent,
        window_seconds, reset_at}], credits}`` from the quota response."""
        raise NotImplementedError

    def transcription(self, conf) -> int:
        """Speech-to-text through this entry: 0 no, 1 maybe (a compatible
        server may implement it), 2 yes."""
        return 0

    def transcription_request(self, conf, model, language, filename, content_type, audio) -> URLRequest:
        """The speech-to-text upload (``language`` "" = auto-detect)."""
        raise NotImplementedError

    def transcript(self, data) -> str:
        """The text of a transcription response."""
        raise NotImplementedError


def multipart_body(fields: dict, filename: str, content_type: str, data: bytes):
    """Encode fields + one file as multipart/form-data (urllib has no helper)."""
    boundary = secrets.token_hex(16)
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode() + data + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
