"""AI chat, provider settings, model discovery, and ChatGPT OAuth routes."""

import hashlib
import ipaddress
import json
import queue
import re
import sqlite3
import threading
import time
import urllib.error
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from urllib.request import urlopen

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .. import (ai_catalog, ai_permissions, ai_protocols, ai_revert, ai_usage, chatgpt_oauth,
                paper_research, search_services, translate_engines)
from ..ai_client import (
    CallRefused,
    UpstreamError,
    add_usage as _add_usage,
    call_ai as _call_ai,
    check_allowance as _check_allowance,
    check_call_slot as _check_call_slot,
    failure_kind,
    open_ai as _open_ai,
    partial_json_strings as _partial_json_strings,
    protocol as _protocol,
    read_reply as _read_reply,
    sse_deltas as _sse_deltas,
    sse_events as _sse_events,
    upstream_detail as _upstream_detail,
    wire_protocol as _wire_protocol,
)
from ..ai_agent import AgentLoop, ApprovalGate, Conversation, Helper, PaperWait
from ..ai_tools import (
    AGENT_PROMPT,
    READ_CHARS_MAX,
    agent_system,
    agent_tools,
    available,
    tool_states,
)
from ..ai_context import (
    build_messages as _build_messages,
    MAX_CONTEXT_BLOCKS,
    context_markdown,
    gather_inputs as _gather_inputs,
    parse_images as _parse_images,
    pdf_path as _pdf_path,
    prompt_tokens,
    render_selection_crop,
    request_note_selections,
)
from ..ai_permissions import permission_state
from ..ai_protocols.base import truncated_stop
from ..ai_settings import (
    MAX_MODELS_LEN,
    MAX_NAME_LEN,
    MAX_PROVIDERS,
    ai_runtime,
    clear_refresh_backoff,
    entry_models,
    is_oauth_protocol as _is_oauth_protocol,
    is_server_id,
    load_provider_entries,
    load_server_ai,
    mask_entry,
    new_key_entry,
    new_provider_id,
    own_entries,
    protocol_choices,
    provider_label,
    require_ai_runtime,
    resolve_model as _resolve_model,
    server_entries_for,
    shared_allowance,
    update_entry,
    update_provider_entries,
)
from ..auth import actor_of, can_write, require_personal_user_id, require_user_id, require_ws
from ..blocks_store import PATH_SEP, folder_path
from ..db import connect_data_db, connect_pages_db, page_now
from ..logbuf import log
from ..pdf_text import extract_text
from ..textnorm import INDEX_VERSION
from ..translate_engines import TRANSLATE_LANGS

router = APIRouter(prefix="/api", tags=["ai"])

# Reasoning-depth values the wire protocols take (Anthropic
# output_config.effort / OpenAI reasoning_effort), lowest first — the order
# chat/modelPrefs.js mirrors. Only sent when the user picks one, and the chat
# offers each model just the levels it takes (GET /ai/model-info).
EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
EFFORT_LEVELS = set(EFFORT_ORDER)
# Offered for a model whose levels no source knows.
FALLBACK_EFFORTS = ["low", "medium", "high"]
# The service tier a call may ask for (Anthropic's fast mode, OpenAI's and
# Codex's service tiers), as the canonical names the wires map to their own
# values. The chat offers each model just the tiers it has (GET
# /ai/model-info) and sends nothing unless the user picked one.
SPEED_LEVELS = set(ai_protocols.SPEED_ORDER)


class AIChatRequest(BaseModel):
    prompt: str
    # Context is PAGES from the user's knowledge base: `pages` (several — a
    # report across pages) or, when empty, the one page of `page_id` (the
    # open page; its PDF attachment, if any, is derived server-side via
    # blocks_store.page_attachment). `doc_id` is the compatibility input:
    # it resolves to the page carrying that PDF (blocks_store.page_for_doc)
    # and does nothing when no page does — send `page_id`; nothing new may
    # depend on `doc_id`.
    doc_id: str = ""
    history: list = Field(default_factory=list)  # [{role: "user"|"ai", text: str}, ...]
    model: str = ""       # model registry id ("provider:model") from /ai/models
    # The conversation's key (the chat bucket: a page id, a folder id or
    # "home"), hashed with the account and workspace into the
    # provider's prompt-cache routing hint — never sent as it is.
    chat_key: str = ""
    # PDF passages the user selected — focus the answer on them:
    # `selections` = [{text, page (1-based, where the viewer saw it start),
    # box ([x0, y0, x1, y1] fractions of that page, top-left origin)}]; the
    # older `selection` string ("---"-joined text, no positions) is read
    # when it is empty (ai_context.request_selections).
    selection: str = ""
    selections: list = Field(default_factory=list, max_length=12)
    # What the user pointed the message at inside the NOTES: the block their
    # cursor is on (the agent's "this block"), blocks they attached as chips
    # (ids — resolved server-side to id-labelled text so the agent can edit
    # them), and note text they selected — exact source ranges
    # [{block_id, from, to, text}], which edit_block mode "selection" rewrites
    # without touching the rest of the block (ai_context.request_note_selections).
    focus_block_id: str = ""
    context_blocks: list = Field(default_factory=list)
    note_selections: list = Field(default_factory=list, max_length=12)
    attach_pdf: bool = False  # send the PDF itself instead of extracted text
    effort: str = ""      # reasoning effort; empty = provider default (param omitted)
    speed: str = ""       # service tier (ai_protocols.SPEED_ORDER); empty = the provider's usual
    system: str = ""      # custom system prompt; empty = built-in default
    pages: list[str] = Field(default_factory=list, max_length=7)  # open page + up to six references

    @field_validator("pages")
    @classmethod
    def _unique_pages(cls, pages):
        return list(dict.fromkeys(str(page) for page in pages if page))
    # Also include the user's highlights + notes for pages that carry a PDF
    # (a page without one is its notes — they always go).
    include_notes: bool = False
    images: list = Field(default_factory=list)  # pasted figures as data URLs
    files: list = Field(default_factory=list)  # uploaded PDFs as {name, data} data URLs
    stream: bool = False  # NDJSON stream of {"delta": …} lines instead of one JSON body
    # Agent chat (gamma/ai_tools.py): agent_scope declares what this chat's
    # tools reach — "folder" (the home/folder view; `folder` is the folder's
    # id, "" = library root) or "page" (the per-page chat; `page_id` is the
    # focused page — read tools + note editors). "" = plain chat (page_id
    # still names the context page). Every tool call comes back as an
    # {"action": …} NDJSON line alongside the text deltas.
    # `permissions` is the Settings → AI → Tool usage → Tools map, one state per
    # permission ({list, read, …, rename, move, block_edit} → "allow" /
    # "ask" / "off", gamma/ai_permissions.py; a key left out allows reading
    # and asks before a change) — everything off degrades to a plain chat.
    # `granted` names the permissions this conversation allowed on an
    # approval card ("Allow in this chat"): they run without asking.
    # agent_system overrides the base agent prompt (the
    # scope/permission lines are always appended); tool_rounds overrides the
    # agent round budget and read_char_limit the per-read_page-call document
    # text cap (both 0 = server default).
    agent_scope: str = ""
    folder: str = ""
    page_id: str = ""
    permissions: dict = Field(default_factory=dict)
    granted: list[str] = Field(default_factory=list, max_length=32)
    agent_system: str = ""
    tool_rounds: int = Field(default=0, ge=0, le=100)
    read_char_limit: int = Field(default=0, ge=0, le=READ_CHARS_MAX)
    # How save_paper stores a paper — the Reading choices the reply's Save to
    # library also uses: {allow_oa, save_copy, fetch_metadata}, missing = on.
    paper_save: dict = Field(default_factory=dict)
    # Whether a fetch a publisher blocked waits in this reply for the PDF the
    # user's browser can get (gamma/ai_agent.PaperWait). False is the "don't
    # wait in this chat" the card's Skip offers: the reply ends and the card
    # stays under it.
    paper_wait: bool = True
    # Whether read_paper is offered: a long document is read by a helper on
    # the same connection, and the chat carries its answer instead of the
    # document (Settings → AI → Tool usage → "Read long papers with a helper").
    delegate_reads: bool = True
    context_char_limit: int = Field(default=60000, ge=100, le=1_000_000)
    multi_context_char_limit: int = Field(default=120000, ge=100, le=1_000_000)


def _resolve_effort(requested: str) -> str:
    requested = (requested or "").strip().lower()
    return requested if requested in EFFORT_LEVELS else ""


def _resolve_speed(requested: str) -> str:
    requested = (requested or "").strip().lower()
    return requested if requested in SPEED_LEVELS else ""


def _sent_speed(speed: str, rt: dict, entry: dict, tools) -> str:
    """The speed the call's wire actually asks the provider for: the
    requested tier when the wire has it on this endpoint (``Protocol.
    speed_tiers`` — an OpenAI-compatible server or a service speaking
    Anthropic's API elsewhere is never sent one), else "" — the usual
    routing, which is what the reply then reports rather than a tier
    nobody was asked for."""
    conf = rt["providers"].get(entry["provider"]) or {}
    if not speed or not conf:
        return ""
    return speed if speed in ai_protocols.get(_wire_protocol(rt, entry, tools)).speed_tiers(conf) else ""


def _served_speed(answered: dict, usage) -> bool:
    """Take the tier a provider turn was served at off its token report
    (the ``speed`` ``Protocol.events`` / ``read_reply`` put there, "" = the
    usual routing) and make it the reply's ``speed``; the report goes on
    without the key (the ``usage`` line is counts only). True when the
    reply's speed changed — the stream then re-sends its ``model`` line.
    A turn the provider said nothing about keeps the speed asked for.
    Over an agent reply's rounds the last report wins."""
    if not isinstance(usage, dict) or "speed" not in usage:
        return False
    served = usage.pop("speed")
    if served is None or served == answered["speed"]:
        return False
    answered["speed"] = served
    return True


def _failure(error: Exception, what: str = "AI call failed") -> str:
    """The error line a stream ends with: a call Gamma refused (a used-up
    shared allowance, too many calls at once) says so in its own words (the
    429's detail), anything else is "<what>: <error>"."""
    return error.detail if isinstance(error, CallRefused) else f"{what}: {error}"


def _failure_info(error: Exception, rt: dict | None = None, entry: dict | None = None) -> dict:
    """What the chat shows a failure as, beside the raw ``detail`` string
    (which already names an upstream status, "upstream 529: …"): its
    ``kind`` (ai_client.failure_kind) and the connection it went through
    (``provider_id``, ``provider_name``, ``provider_auth`` = "key" |
    "oauth") so the error card can name it and open its settings."""
    info = {"kind": failure_kind(error)}
    conf = (rt or {}).get("providers", {}).get((entry or {}).get("provider"))
    if conf:
        info.update(provider_id=entry["provider"], provider_name=conf.get("name") or "",
                    provider_auth=ai_protocols.of(conf).auth)
    return info


def _failure_response(status: int, detail: str, info: dict) -> JSONResponse:
    """An HTTP error whose body carries the failure's kind next to its
    ``detail`` (still a plain string, as every other error's)."""
    return JSONResponse(status_code=status, content={"detail": detail, **info})


def _search_index_status(ws: str, doc_id: str) -> dict:
    """Whether the search index covers this doc — same rules as
    /api/metadata/status (ver mismatch = stale, re-indexed lazily)."""
    try:
        with connect_data_db(ws) as conn:
            row = conn.execute(
                "SELECT ver FROM pdf_fts_docs WHERE doc_id = ?", (doc_id,)
            ).fetchone()
    except sqlite3.OperationalError:
        row = None  # index tables don't exist yet — search has never run
    return {"indexed": bool(row and row[0] == INDEX_VERSION),
            "index_stale": bool(row and row[0] != INDEX_VERSION)}


# Sync def: extraction runs in the threadpool (pdfium stops at the sample cap).
@router.get("/pdf-text-status")
def pdf_text_status(doc_id: str, request: Request, preview: int = 0):
    """Whether a doc's PDF yields extractable text and whether the search
    index covers it. Feeds the metadata popover's health rows — a
    scanned/image-only PDF is why AI answers blind and metadata lookups come
    up empty. `preview` > 0 additionally returns that many characters of the
    text itself (capped)."""
    ws = require_ws(request)
    preview = min(max(preview, 0), 20000)
    index = _search_index_status(ws, doc_id)
    pdf_path = _pdf_path(ws, doc_id)
    if not pdf_path:
        return {"found": False, "ok": False, "chars": 0, **index}
    try:
        text = extract_text(str(pdf_path), preview or 4000)
        stripped = text.strip()
        out = {"found": True, "ok": len(stripped) >= 50, "chars": len(stripped), **index}
        if preview:
            out["text"] = text[:preview]
        return out
    except Exception as e:
        log.warning(f"[pdf-text-status] {e}")
        return {"found": True, "ok": False, "chars": 0, **index}


# Sync def: pdfium renders in the threadpool.
@router.get("/ai/selection-crop/{doc_id}")
def selection_crop(doc_id: str, request: Request, page: int, box: str):
    """The picture of a selected region a chat message sent the model, drawn
    again from the page and crop box saved on the reply's context report
    (``selection.passages[].box``, ``x0,y0,x1,y1`` page fractions) so the
    chat can show it. A document's file never changes under its id, so the
    picture caches."""
    ws = require_ws(request)
    if not doc_id or not all(c in "0123456789abcdef" for c in doc_id):
        raise HTTPException(status_code=400, detail="invalid document id")
    try:
        x0, y0, x1, y1 = (float(v) for v in box.split(","))
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid box")
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1) or page < 1:
        raise HTTPException(status_code=400, detail="invalid box")
    path = _pdf_path(ws, doc_id)
    image = render_selection_crop(path, page, (x0, y0, x1, y1)) if path else None
    if not image:
        raise HTTPException(status_code=404, detail="not found")
    return Response(image[0], media_type=image[1], headers={"Cache-Control": "private, max-age=86400"})


# The grounding clause is deliberate: a PDF's text below this prompt is
# usually a small head excerpt of a long paper, and without being told so the
# model answers detail questions from its memory of similar papers — inventing
# plausible, wrong numbers and attributing them to the user's paper. Scoped to
# claims ABOUT the pages and their documents, so background explanations
# still work.
_SYSTEM_PROMPT = (
    "You are a research assistant working inside the user's knowledge base in Gamma: "
    "pages of nested notes, some of which carry a PDF attachment (a paper, a book, "
    "lecture notes). The context you are given is one or more of those pages — each "
    "with its title, properties, the user's own notes and highlights, and, for a page "
    "with a PDF, the document's extracted text, which is usually an EXCERPT, not the "
    "whole document. Anything you state as being in THESE pages or their documents — "
    "a number, a parameter, a method, a result — must come from text you have actually "
    "read here. Never fill such a gap from your knowledge of similar papers: a value "
    "you half-remember from elsewhere is worse than no answer. If the text you have "
    "does not contain it, say so plainly (and look it up first if you have tools). "
    "General background the user asks you to explain is fine to answer from your own "
    "knowledge — just make clear it is background, not something these pages state. "
    "Be concise; when you cite a specific value from a PDF, give its PDF page number, "
    "and say when something comes from the user's notes rather than the document.")

# Appended whenever a document is in context, custom system prompt or not:
# the clickable-citation link shape (docs/dev/pdf_citations.md).
_CITATION_PROMPT = (
    "\n\nWhen citing a passage from a library PDF, provide a clickable citation "
    "as [p. N](/?page=PAGE_ID&pdf_page=N&quote=URL_ENCODED_QUOTE). "
    "Use the Gamma page ID supplied in context or tool results, the 1-based physical "
    "PDF page number from [PDF page N] labels (not printed page numbers), and a "
    "verbatim, distinctive quote of 8-2000 characters contained on that page, preferably one sentence. "
    "Percent-encode the quote, including spaces, ampersands and parentheses. "
    "These links only navigate and visually highlight text; they never create notes. "
    "Never invent quotes, IDs or page numbers. If the location is unknown, read the "
    "page first when tools are available, otherwise use an ordinary page link. "
    "Do not use these links for external or uploaded files without a Gamma page ID."
)

# Default prompt for AI-based metadata extraction (used when neither an arXiv id
# nor a DOI identifies the paper). Editable per-user in the frontend prompt editor.
METADATA_PROMPT = (
    "You extract bibliographic metadata from the first pages of an academic document. "
    "Reply with ONLY a JSON object (no code fences, no commentary) with these keys: "
    'title (string), authors (list of "First Last" strings, in order), year (string), '
    "venue (journal or conference name; \"arXiv\" for preprints), volume (string), "
    "pages (string, e.g. \"173-179\"), doi (string), arxiv_id (string, e.g. \"1810.11086\"), "
    "publisher (string; books only), isbn (string; books only, as printed), "
    'kind (string: "paper" for journal/conference articles and preprints; otherwise '
    '"notes", "slides", "thesis", "book", "report", or "other" — lecture notes, course '
    "materials and problem sets are \"notes\"; a textbook or monograph, however old, is \"book\"). "
    "For a book, title is the book's main title (no subtitle) and year the edition's copyright year. "
    "Use empty strings/lists for anything not stated in the text. Never invent a DOI, arXiv id or ISBN."
)

# Default prompt for the minimal slide-deck citation. Editable in the frontend.
CITE_PROMPT = (
    "The user provides a citation in an arbitrary format (BibTeX, CSL JSON, or plain text). "
    "You return ONLY a minimal, PPT-style citation suitable for a presentation slide, "
    "labeling italic and bold with markdown syntax correctly. Follow these examples exactly:\n"
    "Guo _et al._ arXiv **1810.11086** (2018).\n"
    "Schine _et al._, Nature **565**, 173–179 (2019)\n"
    "Siegman, _Lasers_ (University Science Books, 1986)\n"
    "Use the journal name (abbreviated if long), bold volume, page range, and year in parentheses. "
    "For preprints use the arXiv number in bold. For a book (@book) use the italic title, then the "
    "publisher and year in parentheses. If there is exactly one author, use their surname "
    "without _et al._; for two authors use \"Surname & Surname\"."
)


# Sync def: ai_runtime may refresh a ChatGPT token (a network round trip).
@router.get("/ai/models")
def ai_models(request: Request):
    user_id = require_user_id(request)
    rt = ai_runtime(user_id)
    return {
        "enabled": rt["enabled"],
        "models": rt["models"],             # [{id: "<pid>:<model>", provider, provider_name, model}, ...]
        "default": rt["default"]["id"] if rt["default"] else "",
        # Set-up machine-translation engines [{id: "engine:<id>", label}] —
        # the translation picker offers them next to the models.
        "translate_engines": translate_engines.configured(user_id),
        # Offered for a model whose own levels are unknown (/ai/model-info);
        # omitted from a request unless picked.
        "efforts": FALLBACK_EFFORTS,
        # Whether some connection takes dictation (Protocol.transcription —
        # an OpenAI-protocol key; the chat's mic shows only then). The same
        # entries /ai/transcribe picks from, models picked or not.
        "transcribe": any(ai_protocols.of(conf).transcription(conf) for conf in rt["providers"].values()),
        "default_prompt": _SYSTEM_PROMPT,   # shown in the prompt editor
        "metadata_prompt": METADATA_PROMPT,  # AI metadata-extraction fallback
        "cite_prompt": CITE_PROMPT,          # PPT-style citation generator
        "agent_prompt": AGENT_PROMPT,        # library-agent base role prompt
        # The shared entries' allowance ({limit, used, exhausted}; None when
        # no shared entry applies): the account card's meter.
        "allowance": rt["allowance"],
    }


# --- Per-user AI provider entries (GUI key management) ------------------------
# OpenAI-platform-style key list: add / edit / remove provider entries. Keys
# are write-only from the client: GET returns a masked hint, never the key.
# Stored under the reserved account-wide `ai-settings` pref in users.db —
# see gamma/ai_settings.py for the security rationale. The server's shared
# entries (managed under /api/admin/ai-providers*) follow as read-only rows.

def _masked_settings(request: Request) -> dict:
    user_id = request.state.user_id
    own = [mask_entry(e) for e in own_entries(user_id)]
    # Shared rows: only an admin sees their key hint.
    shared = [{**mask_entry(e, hint=request.state.is_admin), "shared": True}
              for e in server_entries_for(user_id)]
    return {
        "providers": own + shared,
        # Feeds the "Add provider" dropdown and the form placeholders
        # (protocols with auth "oauth" = sign-in entries, no API key field)
        # plus the named presets (protocol + endpoint).
        **protocol_choices(),
        "can_edit": not request.state.is_guest,
    }


def _saved_entry(request: Request, user_id: str, provider_id: str) -> dict | None:
    """A saved entry by the id the client knows it by: the account's own,
    or, for an admin only, one of the server's shared entries."""
    if is_server_id(provider_id):
        if not request.state.is_admin:
            return None
        return next((e for e in load_server_ai()["providers"] if e.get("id") == provider_id), None)
    return next((e for e in load_provider_entries(user_id) if e.get("id") == provider_id), None)


def _require_editor(request: Request) -> str:
    user_id = require_user_id(request)
    if request.state.is_guest:
        # A guest account is a throwaway anyone can mint (docs/dev/guests.md):
        # it keeps nothing, keys included, and uses the shared entries only.
        raise HTTPException(status_code=403, detail="guest accounts cannot store API keys")
    return user_id


class AIProviderRequest(BaseModel):
    protocol: str = ""      # an API-key key of ai_protocols.PROTOCOLS (required on add)
    name: str | None = None      # display label; "" = service / protocol label
    api_key: str | None = None   # required on add; omitted/empty on edit = keep
    base_url: str | None = None  # "" = protocol default
    models: str | None = None    # comma-separated model names; "" = none offered
    test_model: str | None = None  # model probes use (Test button / login check); "" = first model


@router.get("/ai/usage")
def ai_usage_summary(request: Request):
    """Token usage of the signed-in account's AI calls, as the providers
    reported it: totals for today / 7 days / 30 days / all kept rows, the
    30-day split by kind (chat, translate, metadata, cite, test) and by
    model. Calls through the server's shared entries count here too, and
    ``allowance`` is their 24-hour allowance ({limit, used, exhausted}; None
    when no shared entry applies — ai_settings.shared_allowance)."""
    user_id = require_user_id(request)
    return {**ai_usage.summary(user_id), "allowance": shared_allowance(user_id)}


@router.delete("/ai/usage")
def ai_usage_reset(request: Request):
    """Forget the account's usage rows (Settings → AI → Usage → Reset) —
    except the last 24 hours on shared entries, which the allowance counts."""
    user_id = require_user_id(request)
    return {"ok": True, "deleted": ai_usage.clear(user_id)}


@router.get("/ai/settings")
def ai_settings_get(request: Request):
    require_user_id(request)
    return _masked_settings(request)


@router.post("/ai/providers")
def ai_provider_add(payload: AIProviderRequest, request: Request):
    user_id = _require_editor(request)
    if _is_oauth_protocol(payload.protocol):
        raise HTTPException(status_code=400,
                            detail="sign-in connections are created by signing in — use the Connect button")
    entry = new_key_entry(payload, new_provider_id())

    def add(entries):
        if len(entries) >= MAX_PROVIDERS:
            raise HTTPException(status_code=400, detail="too many providers")
        entries.append(entry)
    update_provider_entries(user_id, add)
    return _masked_settings(request)


def _own_entry(entries: list, provider_id: str) -> dict:
    entry = next((e for e in entries if e.get("id") == provider_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="provider not found")
    return entry


@router.put("/ai/providers/{provider_id}")
def ai_provider_update(provider_id: str, payload: AIProviderRequest, request: Request):
    # A sign-in entry stays a sign-in entry (name/models remain editable
    # here); shared entries are edited under /api/admin/ai-providers.
    user_id = _require_editor(request)
    update_provider_entries(user_id, lambda entries: update_entry(_own_entry(entries, provider_id), payload))
    return _masked_settings(request)


@router.delete("/ai/providers/{provider_id}")
def ai_provider_delete(provider_id: str, request: Request):
    user_id = _require_editor(request)

    def drop(entries):
        entries[:] = [e for e in entries if e.get("id") != provider_id]
    update_provider_entries(user_id, drop)
    return _masked_settings(request)


def _no_credential(entry: dict) -> dict:
    """The in-body failure for an entry ``ai_runtime`` dropped: no key, or a
    ChatGPT sign-in whose refresh failed."""
    return {"ok": False, "auth": True, "kind": "auth",
            "error": "ChatGPT sign-in expired or disconnected — sign in again"
            if _is_oauth_protocol(entry.get("protocol"))
            else "entry has no usable credential — set an API key or sign in again"}


def _probe_model(entry: dict, fallback: str = "") -> str:
    """The model probes go through: the entry's configured test model, else
    the caller's fallback (the client sends its effective metadata model — the
    cheap utility model), else the entry's first model."""
    return ((entry.get("test_model") or "").strip()
            or str(fallback or "").strip()[:100]
            or next(iter(entry_models(entry)), ""))


def _probe_entry(user_id: str, entry: dict, fallback_model: str = "", retry: bool = True) -> dict:
    """One tiny live completion through a saved entry — answers "does this
    credential still work" without waiting for a real chat to 502. The result
    is in-body ({ok, model, latency_ms} / {ok: False, error, auth, kind});
    `auth` marks a broken credential (expired sign-in / rejected key), and
    `kind` is ai_client.failure_kind, or ``no_model`` for an entry with no
    model picked — the UI words the failure by its kind."""
    provider_id = entry.get("id")
    # An explicit probe is an explicit retry: drop the refresh backoff so a
    # ChatGPT entry re-attempts its token refresh now instead of reusing a
    # stale token (``retry`` False: not this caller's to reset).
    if retry:
        clear_refresh_backoff(user_id, provider_id)
    rt = ai_runtime(user_id)
    if provider_id not in rt["providers"]:
        return _no_credential(entry)
    model = _probe_model(entry, fallback_model)
    if not model:
        return {"ok": False, "model": "", "auth": False, "kind": "no_model",
                "error": "no model picked — edit the connection and choose one"}
    started = time.time()
    try:
        # Generous cap: reasoning models burn invisible tokens even on "ok".
        probe_entry = {"provider": provider_id, "model": model}
        _call_ai([{"role": "user", "content": 'Reply with the single word "ok".'}],
                 "", probe_entry, rt, max_tokens=2048, timeout=45,
                 on_usage=ai_usage.recorder("test", probe_entry, rt))
    except Exception as e:
        auth = isinstance(e, UpstreamError) and e.status in (401, 403)
        return {"ok": False, "model": model, "error": str(e), "auth": auth, "kind": failure_kind(e)}
    return {"ok": True, "model": model, "latency_ms": int((time.time() - started) * 1000)}


class AIProviderTestRequest(BaseModel):
    model: str = ""  # client's preferred probe model (its effective metadata model)


# Sync def: the probe call runs in the threadpool.
@router.post("/ai/providers/{provider_id}/test")
def ai_provider_test(provider_id: str, request: Request, payload: AIProviderTestRequest | None = None):
    """Live probe of one saved entry, for the settings list's Test button.
    The probe result comes back in-body — a failed probe is a successful test,
    not an HTTP error. A shared entry (``server:<id>``) is an admin's to
    test; its tokens count on the admin's account."""
    user_id = _require_editor(request)
    entry = _saved_entry(request, user_id, provider_id)
    if not entry:
        raise HTTPException(status_code=404, detail="provider not found")
    return _probe_entry(user_id, entry, payload.model if payload else "")


# Sync def: this read-only account call runs in FastAPI's threadpool.
@router.post("/ai/providers/{provider_id}/usage")
def ai_provider_usage(provider_id: str, request: Request):
    """The subscription allowance of a sign-in entry (Protocol.account_usage).

    API-key protocols have no portable quota endpoint: OpenAI-compatible
    gateways and Anthropic-style services all expose different billing/admin
    APIs. Report that honestly instead of presenting token counts as quota.
    """
    user_id = _require_editor(request)
    # A shared sign-in (``server:<id>``) is an admin's to ask about.
    entry = _saved_entry(request, user_id, provider_id)
    if not entry:
        raise HTTPException(status_code=404, detail="provider not found")
    proto = ai_protocols.PROTOCOLS.get(entry.get("protocol"))
    if not proto or not proto.has_account_usage:
        return {"available": False,
                "reason": "This API-key provider does not expose a standard remaining-usage percentage."}

    clear_refresh_backoff(user_id, provider_id)
    conf = ai_runtime(user_id)["providers"].get(provider_id)
    if not conf:
        return {"available": False, "auth": True, "reason": "Sign in again to query usage."}
    try:
        data = ai_catalog.fetch_json(proto.account_usage_request(conf))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            # Expired/revoked sign-in: an expected state, not a server error —
            # report it in-body so the UI can say "reconnect".
            return {"available": False, "auth": True,
                    "reason": "The sign-in expired — sign in again in this entry's edit form."}
        raise HTTPException(status_code=502,
                            detail=f"usage inquiry failed: {_upstream_detail(e, 200)}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"usage inquiry failed: {e}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="usage inquiry returned invalid data")
    usage = proto.account_usage(data)
    return {"available": bool(usage["windows"]), **usage,
            "reason": "" if usage["windows"] else "The provider returned no usage windows."}


def _signed_in_conf(user_id: str, provider_id: str, proto) -> dict:
    """A sign-in protocol's model list is account-gated: the named connected
    entry's, else (the pre-connect "Add" form has no entry yet) any
    connected entry's of that protocol. There is no fallback list."""
    providers = ai_runtime(user_id)["providers"]
    conf = providers.get(provider_id)
    if not conf or conf.get("protocol") != proto.id:
        conf = next((c for c in providers.values() if c.get("protocol") == proto.id), None)
    if not conf:
        raise HTTPException(status_code=400, detail="sign in first — the model list comes from your account")
    return conf


class ModelCatalogRequest(BaseModel):
    provider_id: str = ""  # saved entry to use the stored key of; "" = use the fields below
    protocol: str = ""
    api_key: str = ""
    base_url: str | None = None


# Sync def: the upstream listing fetch runs in the threadpool.
@router.post("/ai/model-catalog")
def ai_model_catalog(payload: ModelCatalogRequest, request: Request):
    """Model names offered by a provider, for the settings form's model
    picker, asked live (ai_catalog.list_models): an API-key protocol with the
    typed key or the saved entry's, a sign-in protocol with a connected
    entry's token. An admin editing a shared entry names it by its
    ``server:<id>``."""
    user_id = _require_editor(request)
    entry = {}
    protocol = payload.protocol
    if payload.provider_id:
        entry = _saved_entry(request, user_id, payload.provider_id) or {}
        protocol = protocol or entry.get("protocol")
    proto = ai_protocols.PROTOCOLS.get(protocol)
    if not proto:
        raise HTTPException(status_code=400, detail="unknown protocol")
    if proto.auth == "oauth":
        conf = _signed_in_conf(user_id, payload.provider_id, proto)
    else:
        key = (payload.api_key or "").strip() or (entry.get("api_key") or "").strip()
        if not key:
            raise HTTPException(status_code=400, detail="enter the API key first, then load the model list")
        base = ((payload.base_url if payload.base_url is not None else entry.get("base_url") or "").strip()
                or proto.base_url).rstrip("/")
        conf = {"protocol": proto.id, "api_key": key, "base_url": base, "name": ""}
    # A key the provider refuses is the form's problem (400); a sign-in's
    # listing failing is the upstream's (502).
    status = 502 if proto.auth == "oauth" else 400
    try:
        models = ai_catalog.list_models(conf)
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=status, detail=f"model list failed: {_upstream_detail(e, 200)}")
    except Exception as e:
        raise HTTPException(status_code=status, detail=f"model list failed: {e}")
    return {"models": [m["id"] for m in models]}


# Sync def: the listing / catalog fetches run in the threadpool.
@router.get("/ai/model-info")
def ai_model_info(request: Request, model: str = ""):
    """What the chat needs to know about a chat model ("<provider id>:<model>";
    "" = the default one), asked live (ai_catalog): {model, context_window,
    source, efforts, efforts_source, speeds, speeds_source}. context_window
    (the context ring) is null when neither the provider's listing nor
    models.dev knows it; efforts are the reasoning-effort levels it takes,
    lowest first ([] = no effort control, null = unknown — the chat offers
    /ai/models' efforts); speeds are the service tiers it may run at,
    cheapest first ([] = no speed control)."""
    rt = ai_runtime(require_user_id(request))
    m = next((x for x in rt["models"] if x["id"] == model), None) or rt["default"]
    conf = rt["providers"].get(m["provider"]) if m else None
    if not conf:
        return {"model": "", "context_window": None, "source": "", "efforts": None, "efforts_source": "",
                "speeds": [], "speeds_source": ""}
    window, source = ai_catalog.context_window(m["provider"], conf, m["model"])
    efforts, efforts_source = ai_catalog.reasoning_efforts(m["provider"], conf, m["model"])
    if efforts is not None:
        efforts = [e for e in EFFORT_ORDER if e in efforts]
    speeds, speeds_source = ai_catalog.speed_tiers(m["provider"], conf, m["model"])
    return {"model": m["model"], "context_window": window or None, "source": source,
            "efforts": efforts, "efforts_source": efforts_source,
            "speeds": speeds, "speeds_source": speeds_source}


class AIHealthRequest(BaseModel):
    provider_id: str = ""  # "" = the first configured entry
    mode: str = "ping"     # "ping" = free credential check; "test" = tiny live completion
    model: str = ""        # "test" mode: preferred probe model (the client's metadata model)


# Sync def: the upstream check runs in the threadpool.
@router.post("/ai/health")
def ai_health(payload: AIHealthRequest, request: Request):
    """Startup connection check for one provider entry, so a broken credential
    surfaces at login instead of as a failed chat later. "ping" spends no
    tokens (Protocol.ping_request: API keys list the models, sign-ins ask the
    quota endpoint) — it 401s on a dead credential. "test" runs the same tiny
    completion as the Test button (through the entry's test model). Always
    answers in-body: {configured, ok, auth?, error?, ...}. The entries are
    the ones the account can use: its own, then the server's shared ones."""
    user_id = require_user_id(request)
    entries = own_entries(user_id) + server_entries_for(user_id)
    entry = (next((e for e in entries if e.get("id") == payload.provider_id), None)
             or (entries[0] if entries else None))
    if not entry:
        return {"configured": False, "ok": True}
    result = {"configured": True, "provider_id": entry.get("id"), "mode": payload.mode,
              "provider_name": provider_label(entry),
              "provider_auth": "oauth" if _is_oauth_protocol(entry.get("protocol")) else "key"}
    # A shared sign-in's refresh backoff is the admin's to reset: every
    # account's login runs this check, and a dead shared grant must not be
    # retried once per login.
    retry = request.state.is_admin or not is_server_id(entry.get("id"))
    if payload.mode == "test":
        return {**result, **_probe_entry(user_id, entry, payload.model, retry=retry)}
    if retry:
        clear_refresh_backoff(user_id, entry.get("id"))
    conf = ai_runtime(user_id)["providers"].get(entry.get("id"))
    if not conf:
        return {**result, **_no_credential(entry)}
    try:
        ai_catalog.fetch_json(ai_protocols.of(conf).ping_request(conf))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {**result, "ok": False, "auth": True, "kind": "auth", "error": _upstream_detail(e, 200)}
        if e.code in (404, 405):
            # An OpenAI-compatible gateway without /v1/models — can't verify
            # for free; don't cry wolf.
            return {**result, "ok": True, "unverified": True}
        return {**result, "ok": False, "auth": False, "kind": failure_kind(e), "error": _upstream_detail(e, 200)}
    except Exception as e:
        return {**result, "ok": False, "auth": False, "kind": failure_kind(e), "error": str(e)[:200]}
    return {**result, "ok": True}


# --- Streaming keepalive ------------------------------------------------------

# Seconds of silence before a stream gets a keepalive line. Reverse proxies
# close a response that sends nothing for a while (nginx and Synology's
# proxy default to 60 s, Cloudflare to 100 s); the browser then sees a bare
# "network error" mid-reply and nothing reaches the server log. A tool loop
# over a long context is quiet for exactly that long while the model thinks.
KEEPALIVE_INTERVAL = 15.0


def keepalive_lines(lines, what="ai", interval=KEEPALIVE_INTERVAL, abandoned=None):
    """Relay the NDJSON line generator ``lines`` from a worker thread and put a
    ``{"ping": 1}`` line in every gap longer than ``interval`` seconds, so an
    idle proxy or browser keeps the response open while the provider is still
    thinking. Clients skip ping lines. When the consumer goes away before
    the source ends (the Stop button, or the connection dropped anyway), the
    worker stops the source at its next yield — the same point the plain
    generator would have been abandoned at — and the log says so. The
    ``abandoned`` event (made here when not given) is set then: a source
    that waits between yields (a tool call waiting on its approval card)
    watches it to give up."""
    q = queue.Queue(maxsize=64)
    done = object()
    abandoned = abandoned if abandoned is not None else threading.Event()

    def put(item):
        while not abandoned.is_set():
            try:
                q.put(item, timeout=1)
                return True
            except queue.Full:
                pass
        return False

    def pump():
        try:
            for line in lines:
                if not put(line):
                    lines.close()
                    return
        except BaseException as e:  # relayed to the consumer
            put(e)
        else:
            put(done)

    threading.Thread(target=pump, name=f"{what}-stream", daemon=True).start()
    started = time.monotonic()
    finished = False
    try:
        while True:
            try:
                item = q.get(timeout=interval)
            except queue.Empty:
                yield '{"ping": 1}\n'
                continue
            if item is done:
                finished = True
                return
            if isinstance(item, BaseException):
                finished = True
                raise item
            yield item
    finally:
        abandoned.set()
        if not finished:
            log.warning(f"[{what}] client closed the stream after "
                        f"{time.monotonic() - started:.0f}s (stop button, or the "
                        f"connection dropped — a proxy idle timeout?)")


class WatchedStream(StreamingResponse):
    """A streaming response that sets ``gone`` the moment its client leaves
    (Stop, a dropped connection), and in any case once it ends. Starlette
    hears a client leave at once (its disconnect listener, which runs under
    uvicorn's ASGI spec 2.3) but only cancels its send loop: the relay
    behind it (keepalive_lines) is closed whenever it is collected, and its
    own ``abandoned`` event can wait that long. A tool call waiting on its
    approval card watches ``gone``, so it gives up at once."""

    def __init__(self, content, gone: threading.Event, **kwargs):
        super().__init__(content, **kwargs)
        self.gone = gone

    async def listen_for_disconnect(self, receive) -> None:
        await super().listen_for_disconnect(receive)
        self.gone.set()

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.gone.set()


# --- PDF translation ----------------------------------------------------------
# Backs the viewer's translated view: the frontend segments a page into
# paragraph blocks (frontend/src/pdf/pdfTranslate.js) and sends their TEXT here;
# geometry never leaves the client. Translations are cached per (user_id, target
# language, model, source text) — IN MEMORY only, deliberately: nothing is
# persisted to disk, the cache just makes retries, re-shows and halted-job
# resumes free until the server restarts.

# The allowlisted target languages are translate_engines.TRANSLATE_LANGS
# (code → the name spliced into the prompt), shared with the engines.

_TRANSLATE_PROMPT = (
    "You translate paragraphs extracted from an academic paper into {lang}. "
    "The user message is a JSON array of strings; each string is one paragraph, "
    "heading, or caption. Reply with ONLY a JSON array of strings of the SAME "
    "length and order — element i is the translation of element i. No code "
    "fences, no commentary. Rules: keep inline math, LaTeX, numbers, symbols, "
    "variable names, citation markers like [12], and URLs exactly as written; "
    "keep the register of an academic paper; translate headings as headings. "
    "Return a string unchanged when it is already in the target language or is "
    "pure math/code."
)

_TRANSLATE_MAX_TEXTS = 200      # paragraphs per request (a page is ~10–50)
_TRANSLATE_MAX_CHARS = 60000    # total source chars per request
_TRANSLATE_STREAM_INTERVAL = 0.05  # min seconds between streamed partial lines (~20 paints/s)
# In-memory LRU: key (see _translate_key) → translated text. Process-wide,
# never written to disk; a restart simply starts cold. The lock matters:
# requests run in FastAPI's threadpool and the viewer fires several in
# parallel, so touches and evictions would otherwise race.
_TRANSLATE_CACHE: "OrderedDict[str, str]" = OrderedDict()
_TRANSLATE_CACHE_CAP = 5000     # cached paragraphs kept in memory (LRU)
_TRANSLATE_LOCK = threading.Lock()


def _cache_get(keys: list) -> dict:
    """LRU-touching lookup: {key: translation} for every key already cached."""
    with _TRANSLATE_LOCK:
        hits = {}
        for k in keys:
            if k in _TRANSLATE_CACHE:
                _TRANSLATE_CACHE.move_to_end(k)
                hits[k] = _TRANSLATE_CACHE[k]
        return hits


def _cache_put(key: str, text: str):
    with _TRANSLATE_LOCK:
        _TRANSLATE_CACHE[key] = text
        _TRANSLATE_CACHE.move_to_end(key)
        while len(_TRANSLATE_CACHE) > _TRANSLATE_CACHE_CAP:
            _TRANSLATE_CACHE.popitem(last=False)


class AITranslateRequest(BaseModel):
    texts: list = Field(default_factory=list)  # source paragraphs, viewer order
    lang: str = "zh-CN"   # target language code (TRANSLATE_LANGS key)
    model: str = ""       # model registry id, or "engine:<id>" (a translation service); "" = the user's default
    effort: str = ""      # reasoning effort; "" = provider default (param omitted)
    # NDJSON stream: {"i": [indices], "text": partial} lines as the model
    # writes each paragraph (the viewer types them into the page), then the
    # same final {"translations", "model", "cached"} object as the plain reply.
    stream: bool = False


def _translate_key(user_id: str, lang: str, model: str, text: str) -> str:
    # Keyed on the bare model NAME (not the registry id): registry ids embed
    # the provider-entry id, which changes when a key is re-added — the cached
    # translation shouldn't die with it. The account id scopes the shared
    # in-memory dict per account.
    return hashlib.sha256(f"{user_id}\x00{lang}\x00{model}\x00{text}".encode()).hexdigest()


def _parse_translation_array(reply: str, n: int) -> list:
    """The model's reply as a list of n strings — tolerates code fences and
    prose around the array, nothing else."""
    s = (reply or "").strip()
    i, j = s.find("["), s.rfind("]")
    if i < 0 or j <= i:
        raise ValueError("no JSON array in reply")
    arr = json.loads(s[i:j + 1])
    if not isinstance(arr, list) or len(arr) != n:
        raise ValueError(f"expected {n} translations, got {len(arr) if isinstance(arr, list) else 'non-list'}")
    return ["" if t is None else str(t) for t in arr]


# Sync def: the AI call runs in the threadpool.
@router.post("/ai/translate")
def ai_translate(payload: AITranslateRequest, request: Request):
    user_id = require_user_id(request)
    lang = payload.lang
    if lang not in TRANSLATE_LANGS:
        raise HTTPException(status_code=400, detail="unsupported target language")
    texts = payload.texts
    if not isinstance(texts, list) or not texts or len(texts) > _TRANSLATE_MAX_TEXTS:
        raise HTTPException(status_code=400, detail=f"texts must be 1–{_TRANSLATE_MAX_TEXTS} strings")
    if any(not isinstance(t, str) for t in texts):
        raise HTTPException(status_code=400, detail="texts must be strings")
    if sum(len(t) for t in texts) > _TRANSLATE_MAX_CHARS:
        raise HTTPException(status_code=413, detail="too much text in one request")

    # A machine-translation engine ("engine:<id>", Settings → Reading) needs
    # no AI provider; anything else resolves to a chat model.
    engine = translate_engines.engine_of(payload.model)
    if engine:
        engine_conf = translate_engines.credentials(user_id, engine)
        model_id = model_name = translate_engines.MODEL_PREFIX + engine
    else:
        rt = require_ai_runtime(user_id)
        entry = _resolve_model(rt, payload.model)
        model_id, model_name = entry["id"], entry["model"]

    keys = [_translate_key(user_id, lang, model_name, t) for t in texts]
    # hits: key → translation, for every paragraph that won't need the model.
    # Filled from the cache now and from the provider reply below; the final
    # response reads texts the map doesn't cover (whitespace-only paragraphs)
    # verbatim.
    hits = _cache_get(keys)

    # Whitespace-only paragraphs never go upstream; every other cache miss
    # does, once (duplicates collapsed): one model call, or a service's
    # batches.
    miss, queued = [], set()
    for i, t in enumerate(texts):
        if keys[i] not in hits and keys[i] not in queued and t.strip():
            queued.add(keys[i])
            miss.append(i)

    def reply_with(final):
        # The whole answer at once; a streaming client reads it as the
        # final NDJSON line.
        if not payload.stream:
            return final
        return StreamingResponse(iter([json.dumps(final, ensure_ascii=False) + "\n"]),
                                 media_type="application/x-ndjson")

    def settle(translated, cached):
        """Record the misses' translations (cache + hits) and build the final
        response object."""
        for i, t in zip(miss, translated):
            hits[keys[i]] = t
            if t and t != texts[i]:  # identity fallbacks stay uncached so a retry can improve them
                _cache_put(keys[i], t)
        out = [hits.get(k, texts[i]) for i, k in enumerate(keys)]
        return {"translations": out, "model": model_id, "cached": cached}

    if not miss:
        return reply_with(settle([], True))

    miss_texts = [texts[i] for i in miss]
    if engine:
        # One engine call per batch limit, no streaming: the reply is aligned
        # by the API, so there is nothing to salvage either.
        try:
            translated = translate_engines.translate(engine, engine_conf, miss_texts, lang, user_id)
        except translate_engines.EngineError as e:
            log.warning(f"[ai_translate] {e}")
            raise HTTPException(status_code=502, detail=f"translation failed: {e}")
        return reply_with(settle(translated, False))

    system = _TRANSLATE_PROMPT.format(lang=TRANSLATE_LANGS[lang])
    effort = _resolve_effort(payload.effort)

    def user_turn(batch):
        return [{"role": "user", "content": json.dumps(batch, ensure_ascii=False)}]

    def budget(batch):
        # Output roughly tracks input length (CJK ≈ 1 token/char); the
        # generous floor covers JSON overhead and reasoning models whose
        # thinking spends from the same budget.
        return min(30000, 8000 + 2 * sum(len(t) for t in batch))

    count_usage = ai_usage.recorder("translate", entry, rt)

    def call(batch):
        return _call_ai(user_turn(batch), system, entry, rt, effort=effort,
                        max_tokens=budget(batch), timeout=180, on_usage=count_usage)

    def stream_call(batch):
        """The same call, streamed: yields ("partial", text-so-far) as the
        reply arrives, then ("reply", full text)."""
        resp = _open_ai(user_turn(batch), system, entry, rt, effort=effort,
                        max_tokens=budget(batch), timeout=180, stream=True)
        acc = ""
        try:
            for text in _sse_deltas(resp, _protocol(rt, entry), count_usage):
                acc += text
                yield ("partial", acc)
        finally:
            resp.close()
        yield ("reply", acc)

    def finish(reply):
        """Parse the batch reply (salvaging a miscounted array paragraph by
        paragraph), fill the cache, and build the final response object."""
        try:
            translated = _parse_translation_array(reply, len(miss_texts))
        except ValueError as e:
            # The provider answered but the array is off — models sometimes
            # merge or drop an element. Salvage paragraph by paragraph (a
            # 1-element array can't misalign) instead of failing the chunk;
            # a paragraph that still won't translate comes back VERBATIM, so
            # the viewer shows the original there instead of erroring. The
            # single-paragraph calls run concurrently — sequential salvage of
            # a 6-paragraph chunk would take 6 model round-trips.
            log.warning(f"[ai_translate] {e} — salvaging per paragraph")

            def salvage(t):
                try:
                    return _parse_translation_array(call([t]), 1)[0]
                except Exception as e2:
                    log.warning(f"[ai_translate] paragraph salvage failed: {e2}")
                    return t

            with ThreadPoolExecutor(max_workers=min(4, len(miss_texts))) as pool:
                translated = list(pool.map(salvage, miss_texts))
        return settle(translated, False)

    if not payload.stream:
        try:
            reply = call(miss_texts)
        except CallRefused:
            raise
        except Exception as e:
            log.warning(f"[ai_translate] {e}")
            raise HTTPException(status_code=502, detail=f"translation failed: {e}")
        return finish(reply)

    # Streamed: the call opens inside the stream, so a call Gamma would
    # refuse (a used-up shared allowance, too many open at once) is refused
    # here, while it can still be an HTTP 429.
    _check_allowance(rt["providers"][entry["provider"]])
    _check_call_slot(user_id)
    # Element j of the batch reply belongs to every request index sharing
    # its key (duplicates were collapsed into one upstream element).
    slots = {}
    for i, k in enumerate(keys):
        if k in queued:
            slots.setdefault(k, []).append(i)
    targets = [slots[keys[i]] for i in miss]

    def ndjson():
        shown = {}  # j -> partial text already sent
        last = 0.0
        reply = ""
        try:
            for kind, data in stream_call(miss_texts):
                if kind == "reply":
                    reply = data
                    break
                now = time.monotonic()
                if now - last < _TRANSLATE_STREAM_INTERVAL:
                    continue
                last = now
                parts = _partial_json_strings(data)
                for j, t in enumerate(parts[:len(miss_texts)]):
                    if t and shown.get(j) != t:
                        shown[j] = t
                        yield json.dumps({"i": targets[j], "text": t}, ensure_ascii=False) + "\n"
            yield json.dumps(finish(reply), ensure_ascii=False) + "\n"
        except Exception as e:
            log.warning(f"[ai_translate] {e}")
            yield json.dumps({"error": _failure(e, "translation failed")}) + "\n"

    return StreamingResponse(keepalive_lines(ndjson(), "ai_translate"),
                             media_type="application/x-ndjson")


# --- Machine-translation engine credentials (Settings → Reading) --------------
# Write-only like the AI keys: GET masks the secrets. Guests can't store keys
# (a guest account is a throwaway, docs/dev/guests.md).

class TranslateEngineRequest(BaseModel):
    fields: dict = Field(default_factory=dict)  # {field id: value}; empty secret = keep


class TranslateEngineTestRequest(BaseModel):
    lang: str = "zh-CN"


@router.get("/translate/engines")
def translate_engines_get(request: Request):
    user_id = require_user_id(request)
    return translate_engines.masked(user_id, can_edit=not request.state.is_guest)


@router.put("/translate/engines/{engine}")
def translate_engine_save(engine: str, payload: TranslateEngineRequest, request: Request):
    user_id = _require_editor(request)
    translate_engines.save(user_id, engine, payload.fields)
    return translate_engines.masked(user_id, can_edit=True)


@router.delete("/translate/engines/{engine}")
def translate_engine_remove(engine: str, request: Request):
    user_id = _require_editor(request)
    translate_engines.remove(user_id, engine)
    return translate_engines.masked(user_id, can_edit=True)


# Sync def: the engine call runs in the threadpool.
@router.post("/translate/engines/{engine}/test")
def translate_engine_test(engine: str, payload: TranslateEngineTestRequest, request: Request):
    """Translate one short sentence with the stored credentials: {ok, text}
    or {ok: false, error} (in the body, like the AI provider test)."""
    user_id = _require_editor(request)
    conf = translate_engines.credentials(user_id, engine)
    lang = payload.lang if payload.lang in TRANSLATE_LANGS else "zh-CN"
    sample = ("Le vif renard brun saute par-dessus le chien paresseux." if lang == "en"
              else "The quick brown fox jumps over the lazy dog.")
    try:
        text = translate_engines.translate(engine, conf, [sample], lang, user_id)[0]
    except translate_engines.EngineError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "text": text}


# --- Online search services (Settings → AI → Tool usage → Online search) -----
# Which service searches the general web, and the keys (write-only like the
# AI keys: GET masks them) — gamma/search_services.py.

class SearchServiceRequest(BaseModel):
    fields: dict = Field(default_factory=dict)  # {field id: value}; empty secret = keep


class SearchEngineRequest(BaseModel):
    engine: str


@router.get("/ai/search-services")
def search_services_get(request: Request):
    user_id = require_user_id(request)
    return search_services.masked(user_id, can_edit=not request.state.is_guest)


@router.put("/ai/search-services/engine")
def search_engine_set(payload: SearchEngineRequest, request: Request):
    user_id = _require_editor(request)
    search_services.set_engine(user_id, payload.engine)
    return search_services.masked(user_id, can_edit=True)


@router.put("/ai/search-services/{service}")
def search_service_save(service: str, payload: SearchServiceRequest, request: Request):
    user_id = _require_editor(request)
    search_services.save(user_id, service, payload.fields)
    return search_services.masked(user_id, can_edit=True)


@router.delete("/ai/search-services/{service}")
def search_service_remove(service: str, request: Request):
    user_id = _require_editor(request)
    search_services.remove(user_id, service)
    return search_services.masked(user_id, can_edit=True)


# Sync def: the service call runs in the threadpool.
@router.post("/ai/search-services/{service}/test")
def search_service_test(service: str, request: Request):
    """One small search with the stored settings: {ok, text} or {ok: false,
    error}, in the body like the AI provider test."""
    user_id = _require_editor(request)
    return search_services.test(user_id, service)


# --- Voice dictation ----------------------------------------------------------

# Default = ChatGPT's dictation model (user-overridable per request); whisper-1
# is the retry for OpenAI-compatible servers (proxies, local gateways) that
# only expose the older Whisper API.
_TRANSCRIBE_DEFAULT = "gpt-4o-transcribe"
_TRANSCRIBE_FALLBACK = "whisper-1"
_TRANSCRIBE_MAX_BYTES = 25 * 1024 * 1024  # OpenAI's audio upload limit


# Sync def: the provider upload runs in the threadpool.
@router.post("/ai/transcribe")
def ai_transcribe(request: Request, file: UploadFile = File(...),
                  model_hint: str = Form(""), model: str = Form(""), language: str = Form("")):
    """Speech-to-text for the chat composer's mic button. Audio goes to the
    OpenAI transcriptions API with the user's own key — `model_hint` is the
    chat's current model-registry id, so dictation billing follows the chat's
    provider when that entry speaks the OpenAI protocol. `model` and
    `language` (ISO-639-1, "" = auto-detect) come from Settings → AI chat."""
    user_id = require_user_id(request)
    # Needs an OpenAI credential, not chat models: an entry with none picked
    # still transcribes.
    rt = ai_runtime(user_id)
    # The entry that surely transcribes (Protocol.transcription: OpenAI
    # itself) before one that may (a compatible server), the chat's own entry
    # first within each.
    # (A shared entry's ids have a colon of their own: "server:<id>:<model>".)
    hinted = rt["providers"].get(next((m["provider"] for m in rt["models"] if m["id"] == model_hint),
                                      (model_hint or "").split(":", 1)[0]))
    candidates = [(ai_protocols.of(c).transcription(c), c)
                  for c in ([hinted] if hinted else []) + list(rt["providers"].values())]
    best = max((rank for rank, _ in candidates), default=0)
    conf = next((c for rank, c in candidates if rank == best), None) if best else None
    if not conf:
        raise HTTPException(status_code=503,
                            detail="Voice input needs an OpenAI API key (Settings → AI → Connections) — "
                                   "Anthropic and ChatGPT sign-in connections don't offer transcription.")
    # Transcription is billed on the entry's key but reports no tokens: it is
    # not metered, only refused once a shared entry's allowance is used up.
    _check_allowance(conf)
    audio = file.file.read(_TRANSCRIBE_MAX_BYTES + 1)
    if not audio:
        raise HTTPException(status_code=400, detail="empty recording")
    if len(audio) > _TRANSCRIBE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="recording too long to transcribe (max 25 MB)")
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "") or "dictation.webm"
    requested = (model or "").strip()
    if requested and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}", requested):
        raise HTTPException(status_code=400, detail="invalid transcription model")
    language = (language or "").strip().lower()
    if language and not re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,8})?", language):
        raise HTTPException(status_code=400, detail="invalid language code")
    candidates = [requested or _TRANSCRIBE_DEFAULT]
    if _TRANSCRIBE_FALLBACK not in candidates:
        candidates.append(_TRANSCRIBE_FALLBACK)
    detail = ""
    proto = ai_protocols.of(conf)
    for model in candidates:
        req = proto.transcription_request(conf, model, language, filename,
                                          file.content_type or "application/octet-stream", audio)
        try:
            with urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            return {"text": proto.transcript(data), "model": model}
        except urllib.error.HTTPError as error:
            detail = _upstream_detail(error)
            log.warning(f"[transcribe] {model}: {detail}")
            # 400/403/404 are model-availability shaped — worth the whisper-1
            # retry; auth/rate-limit failures would just fail again.
            if error.code not in (400, 403, 404):
                break
        except Exception as error:
            detail = str(error)
            log.warning(f"[transcribe] {model}: {error}")
            break
    raise HTTPException(status_code=502, detail=f"transcription failed — {detail}")


# --- ChatGPT subscription sign-in (OAuth PKCE, Codex CLI's flow) --------------
# start → the browser opens auth.openai.com, and gamma/chatgpt_oauth.py keeps
# the sign-in until the server has its code: caught from the redirect on this
# machine, or through the device code the form's status calls poll — else the
# user pastes the address the redirect failed to load. complete then stores
# the tokens on a provider entry.

class ChatGPTAuthStart(BaseModel):
    local: bool = False   # the page runs at a loopback address
    device: bool = False  # also ask for a device code (the form shows it)


class ChatGPTAuthStatus(BaseModel):
    state: str = ""


def _same_machine(request: Request, claimed: bool) -> bool:
    """Whether the browser runs on this server's machine: the page says it is
    at a loopback address, and the request came from loopback too. (A reverse
    proxy on the same host passes the second test, never the first.)"""
    try:
        ip = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return claimed and (ip.is_loopback or bool(mapped and mapped.is_loopback))


def begin_chatgpt_signin(owner, request: Request, payload: ChatGPTAuthStart | None) -> dict:
    """Start a sign-in for ``owner``: an account name (its own entry), or
    ``("server", <admin>)`` for a shared entry (routers/admin.py). Returns
    {auth_url, state, local, device}."""
    payload = payload or ChatGPTAuthStart()
    return chatgpt_oauth.begin(owner, local=_same_machine(request, payload.local), device=payload.device)


def redeem_chatgpt_signin(owner, state: str, callback: str) -> dict:
    """The tokens of a sign-in ``owner`` started (400 otherwise): the ones the
    server caught, else the pasted redirect URL's code. The state belongs to
    whoever started it — another account, or the same admin's own-entry
    form, can't redeem it (and so can't attach that login's tokens
    elsewhere)."""
    try:
        return chatgpt_oauth.redeem(owner, state, callback)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"token exchange failed: {e}")


def new_chatgpt_entry(entry_id: str, oauth: dict, name: str, models: str) -> dict:
    return {"id": entry_id, "protocol": "chatgpt",
            "name": name.strip()[:MAX_NAME_LEN] or "ChatGPT", "api_key": "", "base_url": "",
            "models": models.strip()[:MAX_MODELS_LEN], "created_at": page_now(), "oauth": oauth}


def reconnect_chatgpt_entry(entry: dict, oauth: dict, name: str, models: str) -> None:
    """New tokens on an existing ChatGPT entry; a non-blank name or model
    list replaces the stored one."""
    entry["oauth"] = oauth
    if name.strip():
        entry["name"] = name.strip()[:MAX_NAME_LEN]
    if models.strip():
        entry["models"] = models.strip()[:MAX_MODELS_LEN]


def seeded_chatgpt_models(user_id: str, entry_id: str) -> str:
    """A new sign-in's first models, asked live from the account through
    ``user_id``'s runtime (the tokens must be stored first); "" when the
    listing fails — they are then picked in the entry's form."""
    try:
        live = [m["id"] for m in ai_catalog.list_models(ai_runtime(user_id)["providers"][entry_id])]
    except Exception:
        live = []
    return ", ".join(live[:2])[:MAX_MODELS_LEN]


# Sync def: asking for a device code is a network round trip.
@router.post("/ai/oauth/chatgpt/start")
def chatgpt_auth_start(request: Request, payload: ChatGPTAuthStart | None = None):
    return begin_chatgpt_signin(_require_editor(request), request, payload)


# Sync def: a due device code is polled with OpenAI.
@router.post("/ai/oauth/chatgpt/status")
def chatgpt_auth_status(payload: ChatGPTAuthStatus, request: Request):
    return chatgpt_oauth.status(_require_editor(request), payload.state)


class ChatGPTAuthComplete(BaseModel):
    state: str = ""
    callback: str = ""      # pasted redirect URL (or a bare code); "" once status is ready
    provider_id: str = ""   # existing entry to reconnect; "" creates a new one
    name: str = ""
    models: str = ""


# Sync def: the code exchange and the model listing are network round trips.
@router.post("/ai/oauth/chatgpt/complete")
def chatgpt_auth_complete(payload: ChatGPTAuthComplete, request: Request):
    user_id = _require_editor(request)
    oauth = redeem_chatgpt_signin(user_id, payload.state, payload.callback)
    # Every write below is a read-modify-write of the list as it is THEN
    # (update_provider_entries): a key added in another tab during the
    # network calls stays.
    if payload.provider_id:
        def reconnect(entries):
            entry = _own_entry(entries, payload.provider_id)
            if entry.get("protocol") != "chatgpt":
                raise HTTPException(status_code=404, detail="provider not found")
            reconnect_chatgpt_entry(entry, oauth, payload.name, payload.models)
        update_provider_entries(user_id, reconnect)
        return _masked_settings(request)
    entry = new_chatgpt_entry(new_provider_id(), oauth, payload.name, payload.models)

    def add(entries):
        if len(entries) >= MAX_PROVIDERS:
            raise HTTPException(status_code=400, detail="too many providers")
        entries.append(entry)
    update_provider_entries(user_id, add)
    if not entry["models"]:
        # Seed the model list live from the account (the tokens stored
        # first: the listing reads them back through ai_runtime).
        models = seeded_chatgpt_models(user_id, entry["id"])
        if models:
            def seed(entries):
                for e in entries:
                    if e.get("id") == entry["id"] and not e.get("models"):
                        e["models"] = models
            update_provider_entries(user_id, seed)
    return _masked_settings(request)


# Providers whose backend refused native input_file parts — skip the wasted
# upload on later requests. In-memory: a restart retries native once.
_NATIVE_PDF_REJECTED: set = set()

# Room left for the reply (and the estimate's error) when a conversation is
# fitted to the model's window before it is sent.
_WINDOW_RESERVE = 8192 + 2048


def _cache_key(user_id: str, ws: str, payload) -> str:
    """One opaque id per conversation for the providers' prompt caches: the
    chat bucket under the account and workspace, hashed so neither reaches
    the provider."""
    bucket = payload.chat_key or payload.page_id or "home"
    return hashlib.sha256(f"{user_id}\0{ws}\0{bucket}".encode()).hexdigest()[:32]


def _next_drop(drop: int, history: int) -> int:
    """The next larger number of oldest turns to leave out: two more, then
    doubling, never past the history's length."""
    return min(history, max(drop + 2, drop * 2))


def _chat_scope(request: Request, ws: str, user_id: str, payload, runtime=None, entry=None, effort: str = "",
                can_ask: bool = True) -> dict:
    """The tool scope of a chat request: what its tools reach and what the
    agent prompt names — a folder chat's folder by id, with its path as it
    reads (404 when the id is no folder of ``ws``). ``runtime`` /
    ``entry`` are the connection the chat answers on (absent for a context
    export): search_web may search through it (gamma/search_services.py).
    ``permissions`` is the state of every tool permission for this request
    (ai_tools.tool_states): asking tools are armed only when the request
    can show an approval card (``can_ask``: a streamed chat of a signed-in
    account), and the tool loop turns an ask into allow once the user
    allows it for the chat."""
    names = []
    if payload.agent_scope == "folder" and payload.folder:
        with connect_pages_db(ws) as conn:
            names = folder_path(conn, payload.folder)
        if not names:
            raise HTTPException(404, "That folder no longer exists")
    states = tool_states(payload.permissions, granted=payload.granted, can_ask=can_ask and bool(user_id))
    web = {}
    if states.get("web_search") != "off":
        engine = search_services.web_engine(user_id, runtime, entry)
        web["web_engine"] = engine
        if engine == "ai":
            web["ai_search"] = {"runtime": runtime, "entry": entry, "effort": effort}
    # The person behind the request, when it is their own: a guest or a share
    # link has no browser to hand a blocked fetch to and no publisher
    # sign-ins. Only a live reply can wait on the card a handoff shows.
    personal = (user_id if not request.state.is_guest
                and not request.query_params.get("share") else None)
    return {**web, "type": payload.agent_scope, "folder": payload.folder, "folder_path": PATH_SEP.join(names),
            "page_id": payload.page_id, "read_chars": payload.read_char_limit,
            "permissions": states,
            "context_pages": list(payload.pages),
            # The agent prompt names the cursor block / attached chips so
            # "this block" resolves without a read_block round-trip.
            "focus_block_id": (payload.focus_block_id or "").strip()[:64],
            "context_blocks": [str(b)[:64] for b in payload.context_blocks[:MAX_CONTEXT_BLOCKS]],
            # What edit_block mode "selection" rewrites (labels S1, S2…).
            "note_selections": request_note_selections(payload),
            "actor": user_id, "can_write": can_write(request),
            # Bound inside fetch_paper: streamed tools run in a separate
            # thread, which does not inherit the request's ContextVars.
            # Journal sign-ins are on or off (a part of fetching, not a call to approve).
            "publisher_user": (personal if permission_state(payload.permissions, "publisher_cookies", False) != "off"
                               else None),
            # The account a blocked fetch_paper hands to the user's browser
            # (gamma/fetch_handoff.py), whose delivered PDFs it reads.
            "handoff_user": personal,
            # Whether such a fetch waits on its card inside this reply
            # (ai_agent.PaperWait): the prompt tells the model which it is.
            "paper_wait": bool(payload.paper_wait and can_ask and personal),
            # Whether read_paper is offered: a long document read by a
            # helper whose own conversation carries the windows.
            "delegates": bool(payload.delegate_reads),
            "paper_save": {key: payload.paper_save.get(key, True) is not False
                           for key in ("allow_oa", "save_copy", "fetch_metadata")},
            # This turn's reads, {block_id: full text}: what an edit_block
            # replace merges from (ai_tools.notes_seen).
            "read_texts": {}}


def _chat_tools(payload, scope: dict) -> list | None:
    """The armed tool specs: the scope decides which tools exist, the
    permission toggles pick the subset — None (or no scope) is a plain chat.
    Where the scope cannot write (a viewer, a read-scope token) no mutating
    tool is armed; search_web only with a web engine."""
    valid_scope = payload.agent_scope in ("folder", "page") and (
        payload.agent_scope != "page" or payload.page_id)
    return (agent_tools(payload.agent_scope, scope["permissions"], payload.read_char_limit,
                        can_write=scope["can_write"], has=available(scope)) or None) if valid_scope else None


def _chat_prompt(ws: str, payload, scope: dict, tools, allow_native: bool, drop: int = 0):
    """The request's turns, system prompt and native files, with the
    ``drop`` oldest history items left out — what /ai/chat sends and
    /ai/chat/context exports. Returns ``(pdf_b64s, messages, system,
    coverage, crops)``; ``crops`` are the pictures of selected regions whose
    text is unreliable, riding with the user's own images."""
    crops = []
    pdf_b64s, context, coverage, message_context = _gather_inputs(ws, payload, allow_native, crops=crops,
                                                                  notes_seen=scope.get("read_texts"))
    # The tools and the agent prompt know what the context already holds
    # (read_page never repeats it; the prompt names the pages to read).
    scope["coverage"] = coverage
    located = next((c["selection"]["passages"] for c in coverage if c.get("selection")), None)
    # Agent chats replay each saved reply's tool calls/results so the
    # model keeps what it already listed/read/changed across turns.
    messages = _build_messages(payload, context, with_tools=bool(tools), located=located,
                               message_context=message_context, drop_turns=drop)
    # A custom prompt always applies; the built-in one only when there's a document
    system = (payload.system or "").strip()[:8000] or (_SYSTEM_PROMPT if (context or pdf_b64s) else "")
    if context or pdf_b64s:
        system += _CITATION_PROMPT
    if tools:
        system = ((system + "\n\n" if system else "")
                  + agent_system(scope, scope["permissions"],
                                 (payload.agent_system or "").strip()[:8000]))
    return pdf_b64s, messages, system, coverage, crops


class AIApprovalAnswer(BaseModel):
    decision: Literal["once", "chat", "always", "deny"]
    # With "deny": what the user wants the assistant to do instead.
    note: str = Field(default="", max_length=ai_permissions.MAX_NOTE)


@router.post("/ai/approvals/{approval_id}")
def ai_approval_answer(approval_id: str, payload: AIApprovalAnswer, request: Request):
    """The user's decision on a tool call waiting on its approval card in
    their chat (gamma/ai_permissions.py): run it this once, for the rest of
    the conversation, from now on, or not at all. Authenticated like the
    chat itself; only the account whose chat asked can answer. 404 when no
    approval of theirs waits under that id: answered already, timed out,
    or its chat was stopped."""
    require_ws(request)
    if not ai_permissions.answer(approval_id, request.state.user_id or "", payload.decision, payload.note):
        raise HTTPException(404, "This approval is no longer waiting")
    return {"ok": True}


class AIRevert(BaseModel):
    kind: Literal["edit", "create", "move"]
    block_id: str = Field(max_length=64)
    revert: dict   # the action's `revert`, as the note tool recorded it
    force: bool = False
    redo: bool = False  # put a reverted change back


@router.post("/ai/revert")
def ai_revert_change(payload: AIRevert, request: Request):
    """Take back one change the agent made to the notes, or with ``redo``
    put a reverted one back, from its row under the reply
    (gamma/ai_revert.py): ``{page_id, noop}``. 409
    ``{detail, conflict, preview?}`` when the note changed since —
    ``preview`` is what ``force`` would do — or can't go there; 404 when it
    is gone. A workspace editor's, like the chat's writes; the change is
    theirs to make by hand anyway. Sync: it waits on the write lock."""
    ws = require_ws(request, write=True)
    try:
        return ai_revert.revert_change(ws, payload.kind, payload.block_id, payload.revert,
                                       force=payload.force, redo=payload.redo, actor=actor_of(request))
    except ai_revert.RevertError as e:
        return JSONResponse(status_code=e.status, content={
            "detail": e.detail, **({"conflict": e.conflict} if e.conflict else {}),
            **({"preview": e.preview} if e.preview else {})})


class ResearchJob(BaseModel):
    question: str = Field(max_length=paper_research.MAX_QUESTION)
    folder: str = ""   # the folder (an id) it reads and files the report in: the viewed one
    model: str = ""    # the connection to answer on; "" = the account's default
    read_char_limit: int = Field(default=0, ge=0, le=READ_CHARS_MAX)


@router.post("/jobs/research")
def start_research_job(payload: ResearchJob, request: Request):
    """Research one question in the background and file the report as a page
    (kind ``research``, docs/dev/tasks.md). The user starts this, never the
    model: it reads and searches for minutes and writes one page at the end.
    It needs an editor's workspace (it creates that page) and a personal
    account (it answers on the account's own AI connection)."""
    ws = require_ws(request, write=True)
    user_id = require_personal_user_id(request, "Background research needs a personal Gamma account")
    try:
        return paper_research.start(user_id=user_id, ws=ws, question=payload.question,
                                    folder=payload.folder, model=payload.model,
                                    read_chars=payload.read_char_limit)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


class AIChatContextRequest(AIChatRequest):
    title: str = ""  # the conversation's name, the export's heading


@router.post("/ai/chat/context")
def ai_chat_context(payload: AIChatContextRequest, request: Request):
    """What /ai/chat would send the model for this request, as a Markdown
    file: the system prompt, the tools and every turn — the draft in the
    composer as the last one. PDFs go as their extracted text (a file to
    read or paste elsewhere); no provider is called, none needs to be set up."""
    ws = require_ws(request)  # a token too: the chat reads its workspace
    user_id = request.state.user_id
    try:  # the connection decides whether search_web rides along
        rt = require_ai_runtime(user_id)
        entry = _resolve_model(rt, payload.model)
    except HTTPException:
        rt = entry = None
    # As the live chat sees it: a streamed chat, whose asking tools are armed.
    scope = _chat_scope(request, ws, user_id, payload, rt, entry, can_ask=True)
    tools = _chat_tools(payload, scope)
    _, messages, system, coverage, crops = _chat_prompt(ws, payload, scope, tools, allow_native=False)
    text = context_markdown(payload.title, system, messages, tools, coverage,
                            _parse_images(payload.images) + crops)
    return Response(text, media_type="text/markdown; charset=utf-8")


# Sync endpoint on purpose: the AI call can take minutes; FastAPI's threadpool
# keeps the event loop free for other requests meanwhile.
@router.post("/ai/chat")
def ai_chat(payload: AIChatRequest, request: Request):
    # The chat reads (and its tools edit) the request's workspace; the AI
    # providers are the account's own. A viewer, or a read-scope integration
    # token, gets no mutating tools (_chat_scope: auth.can_write, the rule
    # every write endpoint applies). A token reaches the chat through its
    # workspace (require_ws), never the account's provider settings.
    ws = require_ws(request)
    user_id = request.state.user_id
    try:
        rt = require_ai_runtime(user_id)
    except HTTPException as e:
        return _failure_response(e.status_code, e.detail, _failure_info(e))

    entry = _resolve_model(rt, payload.model)
    effort = _resolve_effort(payload.effort)
    speed = _resolve_speed(payload.speed)
    images = _parse_images(payload.images)
    # Only a streamed reply can show an approval card and wait for it.
    scope = _chat_scope(request, ws, user_id, payload, rt, entry, effort, can_ask=payload.stream)
    # Set once the client is gone (Stop, a dropped connection; WatchedStream):
    # a tool call waiting on its approval card gives up, and nothing runs for it.
    stopped = threading.Event()
    # The conversation the agent loop grows across tool rounds (agent mode).
    state = {"drop": 0}
    count_usage = ai_usage.recorder("chat", entry, rt)
    cache_key = _cache_key(user_id, ws, payload)
    history_len = len([h for h in payload.history if isinstance(h, dict) and not h.get("error")])
    conf = rt["providers"].get(entry["provider"]) or {}
    window = ai_catalog.context_window(entry["provider"], conf, entry["model"])[0] if conf else 0

    def open_upstream(messages, system, pdf_b64s, stream, call_tools=None, images=None):
        """One provider turn. ``call_tools`` / ``images`` differ for a
        helper's turn, which carries its own narrow tool set and none of
        the user's pictures."""
        return _open_ai(messages, system, entry, rt, pdf_b64s, effort=effort, speed=speed, timeout=180,
                        images=state["images"] if images is None else images, stream=stream,
                        tools=tools if call_tools is None else call_tools,
                        cache_key=cache_key + ("" if call_tools is None else ":helper"))

    tools = _chat_tools(payload, scope)
    # tools may reroute openai → /v1/responses, so the wire is settled once;
    # a helper's turn carries tools too, so it goes over the same one.
    proto = _wire_protocol(rt, entry, tools) if tools else None
    # The second agent read_paper hands a document to (gamma/ai_agent.py).
    # It answers on the same connection, in its own conversation, and its
    # tokens are metered here like the chat's own.
    scope["helper"] = Helper(
        ws=ws, scope=scope, on_usage=count_usage,
        open_call=lambda talk, htools: open_upstream(talk.messages, talk.system, talk.files,
                                                     True, htools, []),
        read_events=lambda resp: _sse_events(resp, proto),
    ) if tools and scope["delegates"] else None
    # Which model answers, at what effort and speed, with tools or not — the
    # reply's footer names them, and the coverage chip's advice depends on
    # the tools. The speed starts as the one the wire asks for and becomes
    # the one the provider says it served (_served_speed).
    answered = {"id": entry["id"], "name": entry["model"], "effort": effort,
                "speed": _sent_speed(speed, rt, entry, tools), "tools": bool(tools)}

    def prepared(allow_native, drop=0):
        """_chat_prompt, keeping the coverage report and the pictures."""
        pdf_b64s, messages, system, coverage, crops = _chat_prompt(ws, payload, scope, tools, allow_native, drop)
        state["coverage"] = coverage
        state["images"] = images + crops
        return pdf_b64s, messages, system

    def fitted(allow_native):
        """prepared(), with the oldest turns left out until the estimate fits
        the model's window (when a source knows it) — before the provider
        has to refuse the request."""
        drop = state["drop"]
        pdf_b64s, messages, system = prepared(allow_native, drop)
        while (window and drop < history_len
               and prompt_tokens(messages, system, tools, state["images"]) > window - _WINDOW_RESERVE):
            drop = _next_drop(drop, history_len)
            pdf_b64s, messages, system = prepared(allow_native, drop)
        if drop != state["drop"]:
            log.info(f"[ai_chat] {drop} oldest turns left out to fit the {window}-token window")
            state["drop"] = drop
        return pdf_b64s, messages, system

    def open_with_fallback(stream):
        """Open the upstream call; if the provider refuses native PDF parts
        (a 4xx before any bytes — the ChatGPT backend always does, compatible
        servers may), retry with extracted text. Auth and rate-limit failures
        aren't about the PDF and are raised as they are. A provider that
        rejected native parts and then succeeded as text is remembered, so
        later requests skip the wasted multi-MB upload. A prompt the
        provider calls too long is retried with more of the oldest turns
        left out (a window no source knew, or an estimate that fell short)."""
        attempts = (False,) if entry["provider"] in _NATIVE_PDF_REJECTED else (True, False)
        for native in attempts:
            too_long = False
            while True:
                pdf_b64s, messages, system = fitted(native)
                try:
                    resp = open_upstream(messages, system, pdf_b64s, stream)
                    if not native and True in attempts and not too_long:
                        _NATIVE_PDF_REJECTED.add(entry["provider"])
                    state.update(messages=messages, system=system, pdf_b64s=pdf_b64s)
                    return resp
                except UpstreamError as e:
                    if failure_kind(e) == "too_long" and state["drop"] < history_len:
                        too_long = True
                        state["drop"] = _next_drop(state["drop"], history_len)
                        log.info(f"[ai_chat] prompt too long for the provider, retrying with "
                                 f"{state['drop']} oldest turns left out")
                        continue
                    if not (native and pdf_b64s and 400 <= e.status < 500
                            and e.status not in (401, 403, 429)):
                        raise
                    too_long = failure_kind(e) == "too_long"
                    log.warning(f"[ai_chat] {_protocol(rt, entry)} provider rejected native PDF parts, "
                                f"retrying as text: {e}")
                    break

    def agent_events(first_resp):
        """This reply's tool loop (gamma/ai_agent.py) with the chat's two
        pauses wired in: a permission set to Ask stops on an approval card,
        and a fetch a sign-in page, bot check or paywall stopped waits here
        for the PDF the user's own browser can get. Both need a live client,
        so a non-streamed reply only gets the approval-free loop."""
        loop = AgentLoop(
            ws=ws, scope=scope, tools=tools,
            conversation=Conversation(state["messages"], state["system"], state["pdf_b64s"]),
            open_round=lambda talk: open_upstream(talk.messages, talk.system, talk.files, True),
            read_events=lambda resp: _sse_events(resp, proto),
            on_usage=count_usage,
            max_rounds=payload.tool_rounds,
            gate=ApprovalGate(ws, scope, user_id, stopped),
            settle=PaperWait(ws, scope, scope["handoff_user"], stopped) if scope["paper_wait"] else None,
        )
        return loop.run(first_resp)

    try:
        if payload.stream:
            # Open upstream eagerly: connection/auth errors still become a
            # proper HTTP error instead of dying inside a committed stream.
            resp = open_with_fallback(True)

            # First lines: what the model was given (per-document coverage,
            # native file vs text) so the UI can show "pages 1–9 of 22" and
            # "provider refused the PDF file" instead of leaving it implicit;
            # then which model answers, at what effort.
            head = (json.dumps({"context": state["coverage"]}) + "\n") if state.get("coverage") else ""
            head += json.dumps({"model": answered}) + "\n"
            if state["drop"]:
                # Oldest turns left out to fit the window — the chat says so.
                head += json.dumps({"trimmed": {"turns": state["drop"]}}) + "\n"

            if tools:
                def agent_ndjson():
                    try:
                        if head:
                            yield head
                        for kind, data in agent_events(resp):
                            if kind == "usage" and _served_speed(answered, data):
                                yield json.dumps({"model": answered}) + "\n"
                            yield json.dumps({kind: data}) + "\n"
                    except Exception as e:
                        log.warning(f"[ai_chat] agent stream error: {e}")
                        yield json.dumps({"error": _failure(e), **_failure_info(e, rt, entry)}) + "\n"

                return WatchedStream(keepalive_lines(agent_ndjson(), "ai_chat", abandoned=stopped), stopped,
                                     media_type="application/x-ndjson")

            def ndjson():
                usage = []
                try:
                    if head:
                        yield head
                    for kind, data in _sse_events(resp, _protocol(rt, entry)):
                        if kind == "text":
                            yield json.dumps({"delta": data}) + "\n"
                        elif kind == "usage":
                            usage.append(data)
                        elif kind == "stop" and truncated_stop(data):
                            yield json.dumps({"truncated": True}) + "\n"
                    # The provider's token report closes the stream — after
                    # the model line again when it served another speed.
                    for u in usage:
                        if _served_speed(answered, u):
                            yield json.dumps({"model": answered}) + "\n"
                        count_usage(u)
                        yield json.dumps({"usage": u}) + "\n"
                except Exception as e:
                    log.warning(f"[ai_chat] stream error: {e}")
                    yield json.dumps({"error": _failure(e), **_failure_info(e, rt, entry)}) + "\n"
                finally:
                    resp.close()

            return StreamingResponse(keepalive_lines(ndjson(), "ai_chat"),
                                     media_type="application/x-ndjson")
        if tools:
            # The tool loop is SSE-based on every protocol; join it for
            # non-stream callers and return the actions alongside the text.
            parts, actions, usage, truncated = [], [], None, False
            for kind, data in agent_events(open_with_fallback(True)):
                if kind == "delta":
                    parts.append(data)
                elif kind == "action":
                    actions.append(data)
                elif kind == "usage":
                    _served_speed(answered, data)
                    usage = _add_usage(usage, data)
                elif kind == "truncated":
                    truncated = True
                # "progress" previews only matter to a live UI
            return {"response": "".join(parts), "actions": actions, "model": answered,
                    "context": state.get("coverage") or [],
                    **({"usage": usage} if usage else {}),
                    **({"truncated": True} if truncated else {}),
                    **({"trimmed": {"turns": state["drop"]}} if state["drop"] else {})}
        usage = []
        with open_with_fallback(False) as resp2:
            text = _read_reply(resp2, _protocol(rt, entry), usage.append)
        for u in usage:
            _served_speed(answered, u)
            count_usage(u)
        return {"response": text, "model": answered, "context": state.get("coverage") or [],
                **({"usage": usage[0]} if usage else {}),
                **({"trimmed": {"turns": state["drop"]}} if state["drop"] else {})}
    except CallRefused as e:
        return _failure_response(e.status_code, e.detail, _failure_info(e, rt, entry))
    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"[ai_chat] API error: {e}")
        return _failure_response(502, f"AI call failed: {e}", _failure_info(e, rt, entry))
