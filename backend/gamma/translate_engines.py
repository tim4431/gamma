"""Machine-translation engines for the PDF translated view: Microsoft (the
Edge browser's free endpoint, no key), Google Cloud Translation and Youdao,
next to the LLM path in routers/ai.py.

An engine is picked like a model: the viewer sends ``model: "engine:<id>"``
to /api/ai/translate, which then calls ``translate()`` here instead of a chat
model. No AI provider is needed for that path.

Credentials are per account (Settings → Reading → Translation), stored in
users.db ``user_prefs`` under the reserved account-wide ``translate-engines``
key as {engine id: {field: value, "updated_at"}}. Like ``ai-settings`` the
generic /api/prefs endpoints refuse the key; the only read path is the
masked GET /api/translate/engines.
"""

import hashlib
import json
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException

from .db import get_pref, page_now, set_pref
from .logbuf import log

ENGINES_PREF_KEY = "translate-engines"
MODEL_PREFIX = "engine:"
MAX_FIELD_LEN = 512
TIMEOUT = 60

# Microsoft: the endpoint the Edge browser's own page translation calls —
# unauthenticated, unofficial and undocumented (it replaced the
# /translate/auth token flow, which Microsoft retired in July 2026), so it
# may change or throttle without notice. Same reply shape as Translator v3.
MICROSOFT_URL = "https://edge.microsoft.com/translate/translatetext"
GOOGLE_URL = "https://translation.googleapis.com/language/translate/v2"
YOUDAO_URL = "https://openapi.youdao.com/v2/api"

# The viewer's allowlisted target languages, for both translation paths:
# code → the name the LLM prompt (routers/ai.py) splices in. Mirrored in
# frontend/src/app/prefDefs.js TRANSLATE_LANGS — keep the two in sync.
TRANSLATE_LANGS = {
    "en": "English", "zh-CN": "Simplified Chinese", "zh-TW": "Traditional Chinese",
    "ja": "Japanese", "ko": "Korean", "de": "German", "fr": "French",
    "es": "Spanish", "pt": "Portuguese", "it": "Italian", "ru": "Russian",
}

# Per engine: its credential fields (secret ones are masked when listed; an
# engine without fields needs no setup and is always offered) and its codes
# for TRANSLATE_LANGS.
ENGINES = {
    "microsoft": {
        "label": "Microsoft (free)",
        "fields": [],
        "langs": {"en": "en", "zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "ja": "ja", "ko": "ko",
                  "de": "de", "fr": "fr", "es": "es", "pt": "pt", "it": "it", "ru": "ru"},
        # Refuses requests past ~50k characters (measured); stay well under.
        "batch": (100, 20000),
    },
    "google": {
        "label": "Google Cloud Translation",
        "fields": [{"id": "api_key", "secret": True}],
        "langs": {code: code for code in TRANSLATE_LANGS},
        # Google's documented cap is 128 segments per request; the character
        # cap keeps each request well under its payload limit.
        "batch": (100, 25000),
    },
    "youdao": {
        "label": "Youdao",
        "fields": [{"id": "app_key", "secret": False}, {"id": "app_secret", "secret": True}],
        "langs": {"en": "en", "zh-CN": "zh-CHS", "zh-TW": "zh-CHT", "ja": "ja", "ko": "ko",
                  "de": "de", "fr": "fr", "es": "es", "pt": "pt", "it": "it", "ru": "ru"},
        # A single query may be up to 5000 characters.
        "batch": (50, 4500),
    },
}


class EngineError(Exception):
    """The engine refused or failed the request; the message is shown."""


# --- the free service's health ------------------------------------------------
# Microsoft's endpoint is unofficial, so it can stop answering any day. Its
# consecutive failures are counted in memory (a restart starts fresh). From
# FREE_ALERT_AFTER on, the server log gets one warning per streak, and each
# account that met the failures gets a notice (notices.free_translate_failing)
# pointing at Settings → Reading, whose Microsoft row names the error. One
# success ends the streak.
FREE_ENGINE = "microsoft"
FREE_ALERT_AFTER = 3
_health_lock = threading.Lock()
_health = {"failures": 0, "since": "", "error": "", "users": set(), "warned": False}


def _note_outcome(engine: str, user: str, error: str | None) -> None:
    if engine != FREE_ENGINE:
        return
    with _health_lock:
        if error is None:
            _health.update(failures=0, since="", error="", users=set(), warned=False)
            return
        if not _health["failures"]:
            _health["since"] = page_now()
        _health["failures"] += 1
        _health["error"] = error
        _health["users"].add(user)
        warn = _health["failures"] >= FREE_ALERT_AFTER and not _health["warned"]
        _health["warned"] = _health["warned"] or warn
    if warn:
        log.warning(f"[translate] {ENGINES[engine]['label']} failed {FREE_ALERT_AFTER} times in a row "
                    f"({error}); its endpoint may have changed. Google or Youdao keys are the fallback.")


def free_failing(user: str) -> dict | None:
    """The free service's current failure streak ({since, error}) once it has
    reached FREE_ALERT_AFTER, for an account that met it; else None."""
    with _health_lock:
        if _health["failures"] < FREE_ALERT_AFTER or user not in _health["users"]:
            return None
        return {"since": _health["since"], "error": _health["error"]}


# --- stored credentials -------------------------------------------------------

def load(user: str) -> dict:
    value, _ = get_pref(user, ENGINES_PREF_KEY)
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if k in ENGINES and isinstance(v, dict)}


def _complete(engine: str, conf: dict | None) -> bool:
    if not ENGINES[engine]["fields"]:
        return True  # nothing to set up
    return bool(conf) and all((conf.get(f["id"]) or "").strip() for f in ENGINES[engine]["fields"])


def configured(user: str) -> list:
    """[{id: "engine:<id>", label}] for every engine with all its fields set —
    what the translation picker offers."""
    saved = load(user)
    return [{"id": MODEL_PREFIX + eid, "label": e["label"]}
            for eid, e in ENGINES.items() if _complete(eid, saved.get(eid))]


def masked(user: str, can_edit: bool) -> dict:
    """The settings view: every engine, its fields (secrets as a last-4 hint),
    and whether it is ready to use."""
    saved = load(user)
    rows = []
    for eid, e in ENGINES.items():
        conf = saved.get(eid) or {}
        fields = {}
        for f in e["fields"]:
            value = (conf.get(f["id"]) or "").strip()
            fields[f["id"]] = ("…" + value[-4:] if len(value) > 8 else "set") if f["secret"] and value else value
        rows.append({"id": eid, "label": e["label"], "configured": _complete(eid, conf),
                     "needs_key": bool(e["fields"]), "fields": fields,
                     "updated_at": conf.get("updated_at", ""),
                     "failing": free_failing(user) if eid == FREE_ENGINE else None})
    return {"engines": rows, "can_edit": can_edit}


def save(user: str, engine: str, fields: dict) -> None:
    """Set an engine's credentials. A secret field left empty keeps the stored
    value (the form never sees it); a plain field is taken as given."""
    if engine not in ENGINES:
        raise HTTPException(status_code=404, detail="unknown translation engine")
    if not ENGINES[engine]["fields"]:
        raise HTTPException(status_code=400, detail=f"{ENGINES[engine]['label']} needs no key")
    saved = load(user)
    old = saved.get(engine) or {}
    conf = {}
    for f in ENGINES[engine]["fields"]:
        value = fields.get(f["id"])
        value = value.strip() if isinstance(value, str) else ""
        if len(value) > MAX_FIELD_LEN:
            raise HTTPException(status_code=400, detail=f"{f['id']} is too long")
        if not value and f["secret"]:
            value = old.get(f["id"]) or ""
        if not value:
            raise HTTPException(status_code=400, detail=f"{f['id']} is required")
        conf[f["id"]] = value
    conf["updated_at"] = page_now()
    saved[engine] = conf
    set_pref(user, ENGINES_PREF_KEY, saved)


def remove(user: str, engine: str) -> None:
    saved = load(user)
    if saved.pop(engine, None) is not None:
        set_pref(user, ENGINES_PREF_KEY, saved)


def engine_of(model: str) -> str:
    """The engine id a translate request names (``engine:<id>``), else ""."""
    if isinstance(model, str) and model.startswith(MODEL_PREFIX):
        return model[len(MODEL_PREFIX):]
    return ""


def credentials(user: str, engine: str) -> dict:
    """The stored credentials for a request; 404 for an unknown engine and
    503 (like a missing AI provider) when it isn't set up."""
    if engine not in ENGINES:
        raise HTTPException(status_code=404, detail="unknown translation engine")
    conf = load(user).get(engine)
    if not _complete(engine, conf):
        raise HTTPException(status_code=503,
                            detail=f"{ENGINES[engine]['label']} is not set up — add its key in Settings → Reading")
    return conf or {}


# --- translation --------------------------------------------------------------

def translate(engine: str, conf: dict, texts: list, lang: str, user: str) -> list:
    """``texts`` translated into ``lang`` (a TRANSLATE_LANGS code), same length
    and order, for ``user`` (the free service's health counts per account).
    Split into the engine's batch limits; raises EngineError."""
    e = ENGINES[engine]
    target = e["langs"].get(lang)
    if not target:
        raise EngineError(f"{e['label']} does not translate into {lang}")
    call = {"microsoft": _microsoft, "google": _google, "youdao": _youdao}[engine]
    out = []
    try:
        for batch in _batches(texts, *e["batch"]):
            out.extend(call(conf, batch, target))
    except EngineError as err:
        _note_outcome(engine, user, str(err))
        raise
    _note_outcome(engine, user, None)
    return out


def _batches(texts: list, max_n: int, max_chars: int):
    batch, size = [], 0
    for t in texts:
        if batch and (len(batch) >= max_n or size + len(t) > max_chars):
            yield batch
            batch, size = [], 0
        batch.append(t)
        size += len(t)
    if batch:
        yield batch


def _send(req: Request, name: str) -> dict:
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as err:
        # A JSON {"error": {"message"}} body (Google, Microsoft), else the
        # start of a plain-text one.
        detail = ""
        try:
            raw = err.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw)
                detail = (body.get("error") or {}).get("message", "") if isinstance(body, dict) else ""
            except ValueError:
                detail = raw.strip()[:200]
        except Exception:
            pass
        raise EngineError(f"{name}: HTTP {err.code}{' — ' + detail if detail else ''}") from None
    except (URLError, TimeoutError, OSError) as err:
        raise EngineError(f"{name}: {getattr(err, 'reason', err)}") from None
    except ValueError:
        raise EngineError(f"{name}: unreadable reply") from None


def _microsoft(conf: dict, texts: list, target: str) -> list:
    # No "from": the service detects the source language per text.
    query = urlencode({"to": target, "isEnterpriseClient": "false"})
    req = Request(f"{MICROSOFT_URL}?{query}", data=json.dumps(texts).encode("utf-8"), method="POST",
                  headers={"Content-Type": "application/json"})
    data = _send(req, "Microsoft")
    if not isinstance(data, list) or len(data) != len(texts):
        raise EngineError("Microsoft: unexpected reply")
    out = []
    for item, t in zip(data, texts):
        tr = (item.get("translations") or [{}])[0] if isinstance(item, dict) else {}
        text = tr.get("text") if isinstance(tr, dict) else None
        out.append(str(text) if text else t)
    return out


def _google(conf: dict, texts: list, target: str) -> list:
    # Basic (v2) API with an API key, sent as a header so it never lands in
    # a URL. format "text": the reply is plain text, not HTML-escaped.
    body = json.dumps({"q": texts, "target": target, "format": "text"}).encode("utf-8")
    req = Request(GOOGLE_URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Goog-Api-Key": conf["api_key"]})
    data = _send(req, "Google")
    items = (data.get("data") or {}).get("translations") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != len(texts):
        raise EngineError("Google: unexpected reply")
    return [str(it.get("translatedText") or t) for it, t in zip(items, texts)]


# Youdao error codes worth naming; the rest show as the bare number.
_YOUDAO_ERRORS = {
    "101": "missing parameter", "108": "invalid app key", "110": "no service bound to this app",
    "202": "signature check failed — check the app secret", "206": "clock skew",
    "401": "account balance exhausted", "411": "too many requests", "412": "too many long requests",
}


def youdao_sign(app_key: str, app_secret: str, texts: list, salt: str, curtime: str) -> str:
    """v3 signature: sha256(appKey + input + salt + curtime + appSecret), where
    input is the concatenated queries, shortened to first 10 chars + length +
    last 10 chars past 20 characters."""
    q = "".join(texts)
    size = len(q)
    short = q if size <= 20 else q[:10] + str(size) + q[size - 10:]
    return hashlib.sha256((app_key + short + salt + curtime + app_secret).encode("utf-8")).hexdigest()


def _youdao(conf: dict, texts: list, target: str) -> list:
    salt, curtime = str(uuid.uuid4()), str(int(time.time()))
    form = [("q", t) for t in texts] + [
        ("from", "auto"), ("to", target), ("appKey", conf["app_key"]), ("salt", salt),
        ("sign", youdao_sign(conf["app_key"], conf["app_secret"], texts, salt, curtime)),
        ("signType", "v3"), ("curtime", curtime),
    ]
    req = Request(YOUDAO_URL, data=urlencode(form).encode("utf-8"), method="POST",
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    data = _send(req, "Youdao")
    code = str(data.get("errorCode", "")) if isinstance(data, dict) else ""
    if code != "0":
        raise EngineError(f"Youdao: error {code}" + (f" — {_YOUDAO_ERRORS[code]}" if code in _YOUDAO_ERRORS else ""))
    results = data.get("translateResults")
    if not isinstance(results, list):
        raise EngineError("Youdao: unexpected reply")
    # Queries that failed are listed in errorIndex and may be missing from
    # the results; they come back verbatim (the viewer shows the original).
    failed = {int(i) for i in data.get("errorIndex") or [] if str(i).isdigit()}
    ok = [i for i in range(len(texts)) if i not in failed]
    if len(results) == len(texts):
        pairs = zip(range(len(texts)), results)
    elif len(results) == len(ok):
        pairs = zip(ok, results)
    else:
        raise EngineError("Youdao: unexpected reply")
    out = list(texts)
    for i, r in pairs:
        tr = r.get("translation") if isinstance(r, dict) else None
        if isinstance(tr, list):
            tr = "\n".join(str(x) for x in tr)
        if i not in failed and tr:
            out[i] = str(tr)
    return out
