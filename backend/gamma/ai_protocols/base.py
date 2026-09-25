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
                max_tokens=8192, images=None, stream=False, tools=None) -> URLRequest:
        raise NotImplementedError

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
        counts when the provider sent them. Raises on a fully empty response
        (neither text nor tool calls) with the stop reason attached."""
        state = {"got": False, "stop": "", "usage": None}
        for event in sse_json(response):
            for out in self.stream_event(event, state):
                state["got"] = state["got"] or out[0] in ("text", "tool")
                yield out
        for out in self.stream_end(state):
            state["got"] = state["got"] or out[0] == "tool"
            yield out
        if state["usage"]:
            yield ("usage", state["usage"])
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
        """The chat models of a listing body as ``[{id, context_window}]``
        (0 = the listing names no window), in the order to offer them."""
        rows = [r for r in (data.get("data") or []) if isinstance(r, dict) and r.get("id")]
        found = {}
        for row in rows:
            found.setdefault(str(row["id"]), listed_window(row))
        return [{"id": mid, "context_window": found[mid]} for mid in sorted(found)]

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
