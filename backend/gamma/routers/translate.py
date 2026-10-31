"""PDF translation routes: /ai/translate behind the viewer's translated view,
/ai/translate/brief that makes a paper's brief ahead of it, the workspace's
kept translations (/translations*), and the machine-translation services'
credentials (/translate/engines*).

The frontend segments a page into paragraph blocks
(frontend/src/pdf/pdfTranslate.js) and sends their TEXT here; geometry never
leaves the client. The paragraph cache and the kept translations are
gamma/translate.py (over translate_store.py); the system prompt, the
numbered segments the texts go out and come back in, and the paper's brief
are translate_context.py; the allowlisted target languages are
translate_engines.TRANSLATE_LANGS (code → the name spliced into the prompt),
shared with the services. docs/dev/translation.md describes the whole path.
"""

import json
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import ai_catalog, ai_usage, translate_context, translate_engines, translate_store
from ..ai_client import (
    CallRefused,
    call_ai as _call_ai,
    call_slot as _call_slot,
    check_allowance as _check_allowance,
    check_call_slot as _check_call_slot,
    open_ai as _open_ai,
    protocol as _protocol,
    sse_deltas as _sse_deltas,
    wire_protocol as _wire_protocol,
)
from ..ai_protocols.base import truncated_stop
from ..ai_settings import require_ai_runtime, resolve_model as _resolve_model
from ..auth import can_write, require_user_id, require_ws
from ..db import safe_doc_id
from ..logbuf import log
from ..translate import Kept, TranslateBatch, clear, forget, generation, translate_key
from ..translate_engines import TRANSLATE_LANGS
from .ai import (
    EFFORT_ORDER,
    WatchedStream,
    _failure,
    _require_editor,
    _resolve_effort,
    _resolve_speed,
    keepalive_lines,
)

router = APIRouter(prefix="/api", tags=["translate"])

_TRANSLATE_MAX_TEXTS = 200      # paragraphs per request (a page is ~10–50)
_TRANSLATE_MAX_CHARS = 60000    # total source chars per request
_TRANSLATE_STREAM_INTERVAL = 0.05  # min seconds between streamed partial lines (~20 paints/s)
# How long a streamed request waits for the call slot its calls run under
# (ai_client.call_slot): past the door's check, the account's other calls
# may have taken the last one before its stream got to it.
_TRANSLATE_SLOT_WAIT = 120.0
# Times a model call (the chunk, its re-ask, the brief) is tried again when
# it failed before any of the reply came (ai_client.open_ai's ``retries``):
# a connection to the provider that timed out, a provider busy for the
# moment. A job's chunks are many, and one lost to a passing fault leaves
# its paragraphs in the original.
_UPSTREAM_RETRIES = 2
# Room a reasoning model's thinking takes from the same output budget as
# its answer (OpenAI's max_completion_tokens, Anthropic's adaptive
# thinking): what a translation's budget starts from, and what the brief
# call gets on top of its own at an effort the user picked.
_REASONING_TOKENS = 8000
# The brief call: a JSON object of 20–30 terms and 10 symbols fits well
# within these tokens at the model's lowest effort, and a brief that takes
# longer than this is no use to a job whose chunks have gone on without it.
_BRIEF_TOKENS = 3000
_BRIEF_TIMEOUT = 90


class AITranslateRequest(BaseModel):
    texts: list = Field(default_factory=list)  # source paragraphs, viewer order
    lang: str = "zh-CN"   # target language code (TRANSLATE_LANGS key)
    model: str = ""       # model registry id, or "engine:<id>" (a translation service); "" = the user's default
    effort: str = ""      # reasoning effort; "" = the model's lowest level (_translate_effort)
    speed: str = ""       # service tier; "" or unsupported = the provider's usual routing
    # NDJSON stream: {"i": [indices], "text": partial} lines as the model
    # writes each paragraph (the viewer types them into the page), then the
    # same final {"translations", "model", "cached", "redone", "failed",
    # "effort", "brief"} object as the plain reply.
    stream: bool = False
    # The page whose PDF is being translated: the paper whose brief
    # (translate_context) the system prompt carries, and whose kept
    # translations (translate_store) are read and added to. Checked against
    # the request's workspace.
    page_id: str = ""
    # The document's opening text, naming the paper when there is no page.
    head: str = ""
    # True: while the brief request (/ai/translate/brief) is making the
    # paper's brief, wait for it. False: carry it only when it is made
    # already (a job's first chunk, the selection translator). Either way
    # this request never makes it.
    wait_brief: bool = True
    # Whether this request's new translations may be kept with the paper
    # (Settings → Language and Translation → Keep translations). Kept ones
    # are read either way.
    keep: bool = True
    # The paper's text just before and after the texts, and the heading they
    # sit under: context the model reads but does not translate
    # (translate_context.user_turn cuts them to size).
    before: str = ""
    after: str = ""
    section: str = ""


class AITranslateBriefRequest(BaseModel):
    page_id: str = ""     # the paper, as AITranslateRequest's
    # The document's opening text, for a page without a stored PDF (shown
    # from its source URL) or no page: the brief reads it instead.
    head: str = ""
    lang: str = "zh-CN"
    model: str = ""       # the translation's model: the brief is made per model, by it
    effort: str = ""      # as the translation's
    speed: str = ""
    keep: bool = True     # whether the brief may be kept with the paper, as AITranslateRequest's


def _translate_effort(requested: str, rt: dict, entry: dict) -> str:
    """The effort a translation call asks for: the user's pick
    (Settings → Translation), else the model's lowest level
    (translate_context.default_effort; "" when its levels are unknown)."""
    if (requested or "").strip():
        return _resolve_effort(requested)
    conf = rt["providers"][entry["provider"]]
    try:
        levels, _ = ai_catalog.reasoning_efforts(entry["provider"], conf, entry["model"])
    except Exception as e:
        log.warning(f"[ai_translate] effort lookup failed: {e}")
        return ""
    ordered = [e for e in EFFORT_ORDER if e in (levels or [])]
    return translate_context.default_effort(ordered, _wire_protocol(rt, entry))


def _translate_speed(requested: str, rt: dict, entry: dict) -> str:
    """Only send a translation's chosen tier when the model offers it."""
    speed = _resolve_speed(requested)
    if not speed:
        return ""
    conf = rt["providers"][entry["provider"]]
    try:
        tiers, _ = ai_catalog.speed_tiers(entry["provider"], conf, entry["model"])
    except Exception as e:
        log.warning(f"[ai_translate] speed lookup failed: {e}")
        return ""
    return speed if speed in (tiers or []) else ""


def _translate_budget(batch: list) -> int:
    # Output roughly tracks input length (CJK ≈ 1 token/char); the generous
    # floor covers the segment tags and reasoning models whose thinking
    # spends from the same budget.
    return min(30000, _REASONING_TOKENS + 2 * sum(len(t) for t in batch))


def _brief_budget(requested_effort: str) -> int:
    """The brief call's output cap. It runs at the translation's effort: at
    the model's lowest (no pick), _BRIEF_TOKENS; at a level the user picked,
    which may think at length first, room for that too, or the reply would
    come back cut off and the brief fail."""
    return _BRIEF_TOKENS + (_REASONING_TOKENS if _resolve_effort(requested_effort) else 0)


def _paper(request: Request, page_id: str, head: str):
    """``(workspace, paper)`` of a translation or brief request. The
    workspace is checked only when a page is named: a context-free request
    keeps working for an account that left the header's one."""
    ws = require_ws(request) if page_id else ""
    return ws, translate_context.paper_of(ws, page_id, head)


def _paper_keys(request: Request, user_id: str, ws: str, paper, lang: str, model: str,
                keep: bool) -> tuple[str, Kept | None]:
    """``(brief key, kept)`` of the paper in ``lang`` by ``model``: its
    memory key (translate_context.brief_key; "" for no paper) and its kept
    translations and brief (None but for a paper whose PDF is stored in the
    workspace). Both carry the document's generation, read once here: read
    twice, a forget in between would let a request that began before it
    keep its translations under the new one. The request adds to the kept
    ones when it asks to keep and its account may change the workspace (a
    viewer's translations stay in memory)."""
    if not paper:
        return "", None
    # A paper without a stored file has no kept rows, only memory, which a
    # forget names by its page (`page:<id>`) instead of a file.
    gen = generation(ws, paper.doc_id or paper.source)
    key = translate_context.brief_key(user_id, ws, paper, lang, model, gen)
    if not paper.doc_id:
        return key, None
    return key, Kept(ws, paper.doc_id, lang, model, keep and can_write(request), gen)


def _ndjson(line: dict) -> str:
    return json.dumps(line, ensure_ascii=False) + "\n"


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

    def reply_with(final):
        # The whole answer at once; a streaming client reads it as the
        # final NDJSON line.
        if not payload.stream:
            return final
        return StreamingResponse(iter([_ndjson(final)]), media_type="application/x-ndjson")

    ws, paper = _paper(request, payload.page_id, payload.head)

    def keys(model, paper_key, brief):
        return [translate_key(user_id, lang, model, paper_key, brief, t) for t in texts]

    # A machine-translation engine ("engine:<id>", Settings → Translation)
    # needs no AI provider and takes no brief; anything else resolves to a
    # chat model.
    engine = translate_engines.engine_of(payload.model)
    if engine:
        engine_conf = translate_engines.credentials(user_id, engine)
        model_id = translate_engines.MODEL_PREFIX + engine
        # A service's translation is as good as it gets without a brief, so
        # it counts as made with one: it is kept so, and found so.
        paper_key, kept = _paper_keys(request, user_id, ws, paper, lang, model_id, payload.keep)
        batch = TranslateBatch(texts, keys(model_id, paper_key, True), kept, brief=True)
        translated = []
        if batch.miss:
            # One engine call per batch limit, no streaming: the reply is
            # aligned by the API, so there is nothing to salvage either.
            try:
                translated = translate_engines.translate(engine, engine_conf, batch.miss_texts, lang, user_id)
            except translate_engines.EngineError as e:
                log.warning(f"[ai_translate] {e}")
                raise HTTPException(status_code=502, detail=f"translation failed: {e}")
            # A service that mangled a formula token shows the original
            # instead. Not `failed`: a service answers the same text the
            # same way, so asking again would get the same.
            translated = [translate_context.checked(src, t) or src for src, t in zip(batch.miss_texts, translated)]
        return reply_with({"translations": batch.settle(translated), "model": model_id,
                           "cached": not batch.miss, "redone": 0, "failed": [], "brief": True})

    rt = require_ai_runtime(user_id)
    entry = _resolve_model(rt, payload.model)
    model_id, model_name = entry["id"], entry["model"]
    effort = _translate_effort(payload.effort, rt, entry)
    speed = _translate_speed(payload.speed, rt, entry)
    count_usage = ai_usage.recorder("translate", entry, rt)

    # The paper's brief, made once per paper, language and model by the brief
    # request (ai_translate_brief), and its kept translations.
    brief_key, kept = _paper_keys(request, user_id, ws, paper, lang, model_name, payload.keep)
    prompt_cache = translate_context.cache_key(user_id, ws, paper, lang) if paper else ""

    def resolve(wait: bool):
        """(brief, batch): the paper's brief (waited for while the brief
        request makes it, with ``wait``) and the texts against memory and
        the kept translations under it. The keys name the paper, not the
        brief's text, so a brief made again after it left memory finds the
        same paragraphs."""
        brief = translate_context.wait_brief(brief_key, user_id, kept, wait) if paper else None
        has = brief is not None
        return brief, TranslateBatch(texts, keys(model_name, brief_key, has), kept, brief=has)

    def with_brief(brief):
        # What the final object's `brief` says: the translations carry the
        # paper's brief, or there is no paper to have one. False tells the
        # viewer to ask again once the brief is made.
        return brief is not None or paper is None

    def request_parts(brief, batch_texts, alone):
        # Texts asked again apart (``alone``) keep the section, not the
        # request's neighbours: they are the chunk's, not theirs.
        context = ("", "", payload.section) if alone else (payload.before, payload.after, payload.section)
        return (translate_context.user_turn(batch_texts, *context),
                translate_context.system_prompt(lang, brief or ""))

    # A call's reply comes with whether the output cap cut it off (the
    # provider's stop reason): its last segment, open at the end, is then
    # not a translation, even when it is the only one sent.
    def call(slot, brief, batch_texts, alone=False):
        """``(reply, capped)``."""
        messages, system = request_parts(brief, batch_texts, alone)
        stops = []
        reply = _call_ai(messages, system, entry, rt, effort=effort, speed=speed, max_tokens=_translate_budget(batch_texts),
                         timeout=180, on_usage=count_usage, cache_key=prompt_cache if brief else "", slot=slot,
                         retries=_UPSTREAM_RETRIES, on_stop=stops.append)
        return reply, any(truncated_stop(s) for s in stops)

    def stream_call(slot, brief, batch_texts):
        """The same call, streamed: yields ("partial", text-so-far) as the
        reply arrives, then ("reply", (full text, capped))."""
        messages, system = request_parts(brief, batch_texts, False)
        resp = _open_ai(messages, system, entry, rt, effort=effort, speed=speed, max_tokens=_translate_budget(batch_texts),
                        timeout=180, stream=True, cache_key=prompt_cache if brief else "", slot=slot,
                        retries=_UPSTREAM_RETRIES)
        acc = ""
        stops = []
        try:
            for text in _sse_deltas(resp, _protocol(rt, entry), count_usage, stops.append):
                acc += text
                yield ("partial", acc)
        finally:
            resp.close()
        yield ("reply", (acc, any(truncated_stop(s) for s in stops)))

    def finish(slot, brief, batch, reply, capped):
        """Read the reply, ask once more for what it got wrong, fill the
        cache and the kept translations, and build the final response
        object. `redone` counts the
        paragraphs asked again (the bench reads it); `failed` names the
        request's texts that come back as their source because they still
        won't translate, so the viewer asks for them again next time."""
        src = batch.miss_texts
        translated = translate_context.translations(src, reply, capped)
        # A segment the reply lacks (left out, doubled or empty), and one
        # that lost, doubled or invented a formula token (it would show the
        # wrong formula), are asked again together in ONE call, numbered
        # afresh, with the section but not the chunk's neighbours: they are
        # the chunk's, not theirs.
        redo = [j for j, t in enumerate(translated) if t is None]
        if redo:
            log.warning(f"[ai_translate] {len(redo)} of {len(src)} segments came back missing, doubled "
                        "or with a broken token — asking for them again")
            texts = [src[j] for j in redo]
            try:
                again = translate_context.translations(texts, *call(slot, brief, texts, alone=True))
            except Exception as e:  # they come back verbatim, like a text the model won't translate
                log.warning(f"[ai_translate] asking again failed: {e}")
                again = [None] * len(texts)
            for j, t in zip(redo, again):
                translated[j] = t
        # A paragraph that still won't translate comes back VERBATIM, so the
        # viewer shows the original there instead of erroring.
        failed = [j for j, t in enumerate(translated) if t is None]
        translated = [s if t is None else t for s, t in zip(src, translated)]
        return {"translations": batch.settle(translated), "model": model_id, "cached": False,
                "redone": len(redo), "failed": batch.at(failed), "effort": effort, "brief": with_brief(brief)}

    def cached(brief, batch):
        return {"translations": batch.settle([]), "model": model_id, "cached": True, "redone": 0,
                "failed": [], "effort": effort, "brief": with_brief(brief)}

    # A streamed request that will wait for the brief request starts its
    # response at once and waits inside the stream, where keepalive_lines
    # pings through a slow brief and the wait holds no request thread.
    # Otherwise everything is known now: a plain request waits here, and a
    # streamed one never does (a brief claimed just after the check would
    # hold back its response with no word of why), so it goes without a
    # brief claimed that late.
    inline = not (paper and payload.wait_brief and payload.stream and translate_context.brief_making(brief_key))
    brief = batch = None
    if inline:
        brief, batch = resolve(payload.wait_brief and not payload.stream)
        if not batch.miss:
            return reply_with(cached(brief, batch))
        if not payload.stream:
            # The call and its re-ask run under one of the account's call
            # slots, taken once the brief is settled (a request waiting for
            # it holds none) and with no wait: all in use is a 429, as for
            # any other call.
            with _call_slot(user_id) as slot:
                try:
                    reply, capped = call(slot, brief, batch.miss_texts)
                except CallRefused:
                    raise
                except Exception as e:
                    log.warning(f"[ai_translate] {e}")
                    raise HTTPException(status_code=502, detail=f"translation failed: {e}")
                return finish(slot, brief, batch, reply, capped)

    # Streamed: the call opens inside the stream, so a call Gamma would
    # refuse (a used-up shared allowance, too many open at once) is refused
    # here, while it can still be an HTTP 429.
    _check_allowance(rt["providers"][entry["provider"]])
    _check_call_slot(user_id)
    gone = threading.Event()  # the client left (WatchedStream)

    def ndjson():
        shown = {}  # segment number -> partial text already sent
        last = 0.0
        reply = capped = None
        try:
            if inline:
                b, bt = brief, batch
            else:
                # The brief request is making the paper's brief, one model
                # call that can take half a minute: the viewer says so until
                # "translate".
                yield _ndjson({"stage": "brief"})
                b, bt = resolve(True)
                yield _ndjson({"stage": "translate"})
            if not bt.miss:
                yield _ndjson(cached(b, bt))
                return
            targets, n = bt.targets(), len(bt.miss_texts)
            # The call and its re-ask run under one call slot, taken once the
            # brief is settled (a request waiting for it holds none). It is
            # waited for: past the door's check, the account's other calls
            # may have taken the last one meanwhile.
            with _call_slot(user_id, _TRANSLATE_SLOT_WAIT) as slot:
                # The relay stops this stream only at its next line, which
                # comes once the provider answers: a client that left while
                # the slot was waited for would still pay for the call.
                if gone.is_set():
                    return
                for kind, data in stream_call(slot, b, bt.miss_texts):
                    if kind == "reply":
                        reply, capped = data
                        break
                    now = time.monotonic()
                    if now - last < _TRANSLATE_STREAM_INTERVAL:
                        continue
                    last = now
                    for number, t in translate_context.partial_segments(data).items():
                        if not 1 <= number <= n:
                            continue
                        t = translate_context.unmask(bt.miss_texts[number - 1], t)
                        if shown.get(number) != t:
                            shown[number] = t
                            yield _ndjson({"i": targets[number - 1], "text": t})
                final = finish(slot, b, bt, reply, capped)
            yield _ndjson(final)
        except Exception as e:
            log.warning(f"[ai_translate] {e}")
            yield _ndjson({"error": _failure(e, "translation failed")})

    return WatchedStream(keepalive_lines(ndjson(), "ai_translate", abandoned=gone), gone,
                         media_type="application/x-ndjson")


# Sync def: the brief call runs in the threadpool.
@router.post("/ai/translate/brief")
def ai_translate_brief(payload: AITranslateBriefRequest, request: Request):
    """Make the paper's brief (translate_context) ahead of its translation
    requests, which carry it: the viewer asks as a job starts, without
    waiting for the answer, and its chunks wait for the brief while it is
    made (``wait_brief``). Finds the brief instead when it is made already
    (memory, then the kept one), or waits for the request making it.

    Always an NDJSON stream: ``{"stage": "brief"}`` first when the brief is
    not ready at once, then ``{"ready", "error"?}`` — not ready when there
    is no paper, no text to read or the call failed (``error`` says why),
    and at once for a machine-translation service, which takes no brief."""
    user_id = require_user_id(request)
    lang = payload.lang
    if lang not in TRANSLATE_LANGS:
        raise HTTPException(status_code=400, detail="unsupported target language")

    def answer(brief, error=""):
        return {"ready": brief is not None, **({"error": error} if error else {})}

    def at_once(line):
        return StreamingResponse(iter([_ndjson(line)]), media_type="application/x-ndjson")

    if translate_engines.engine_of(payload.model):
        return at_once(answer(None))
    ws, paper = _paper(request, payload.page_id, payload.head)
    if not paper:
        return at_once(answer(None, "there is no paper to read: no page and no opening text"))
    rt = require_ai_runtime(user_id)
    entry = _resolve_model(rt, payload.model)
    key, kept = _paper_keys(request, user_id, ws, paper, lang, entry["model"], payload.keep)
    brief = translate_context.made_brief(key, kept)
    if brief is not None:
        return at_once(answer(brief))
    effort = _translate_effort(payload.effort, rt, entry)
    speed = _translate_speed(payload.speed, rt, entry)
    count_usage = ai_usage.recorder("translate", entry, rt)
    # The call opens inside the stream: refused here while that can still be
    # an HTTP 429, as for a streamed translation.
    _check_allowance(rt["providers"][entry["provider"]])
    _check_call_slot(user_id)

    def make():
        began = time.monotonic()
        system, messages = translate_context.brief_messages(paper, lang)
        with _call_slot(user_id, _TRANSLATE_SLOT_WAIT) as slot:
            # Its tries end by the time the job's chunks stop waiting for it
            # (_BRIEF_WAIT): a brief made after that is too late for them.
            return _call_ai(messages, system, entry, rt, effort=effort, speed=speed,
                            max_tokens=_brief_budget(payload.effort), timeout=_BRIEF_TIMEOUT,
                            on_usage=count_usage, slot=slot, retries=_UPSTREAM_RETRIES,
                            within=translate_context._BRIEF_WAIT - (time.monotonic() - began))

    def ndjson():
        try:
            # The turn is claimed before the first line goes out: a client
            # sends its chunks once it hears the brief is under way, and they
            # must find it being made to wait for it.
            with translate_context.BriefTurn(key, user_id, kept) as turn:
                if turn.pending:
                    yield _ndjson({"stage": "brief"})
                made, why = turn.settle(make)
        except Exception as e:
            log.warning(f"[ai_translate] the paper's brief: {e}")
            made, why = None, _failure(e, "the paper's brief failed")
        yield _ndjson(answer(made, why))

    return StreamingResponse(keepalive_lines(ndjson(), "ai_translate_brief"),
                             media_type="application/x-ndjson")


# --- Kept translations (Settings → Language and Translation) ------------------
# The workspace's, like its PDFs: any member reads what is kept, an editor
# clears it.

@router.get("/translations/stats")
def translations_stats(request: Request):
    """What the workspace keeps: {documents, paragraphs, bytes}."""
    return translate_store.stats(require_ws(request))


@router.delete("/translations")
def translations_clear(request: Request):
    """Drop the workspace's kept translations and briefs, and what the
    server's memory holds of them, so every document's next job translates
    afresh: {removed} paragraphs."""
    return {"removed": clear(require_ws(request, write=True))}


@router.delete("/translations/{doc_id}")
def translations_forget(doc_id: str, request: Request, page: str = ""):
    """Forget one document's translation (the translate button's "Forget
    this translation"): its kept translations and briefs in every language
    and model, and what the server's memory holds of them, so the next job
    translates afresh. ``page``: the page showing it, whose memory a PDF
    without a stored copy (opened from its link) is kept under instead.
    {removed} paragraphs."""
    ws = require_ws(request, write=True)
    try:
        safe_doc_id(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid document id")
    if len(page) > 64:
        raise HTTPException(status_code=400, detail="invalid page id")
    removed = forget(ws, doc_id)
    if page:
        forget(ws, f"page:{page}")
    return {"removed": removed}


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
