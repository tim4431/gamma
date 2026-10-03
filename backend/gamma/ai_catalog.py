"""What a provider entry offers, asked live: its model listing and each
model's context window, reasoning-effort levels and speed tiers. The protocol adapters
(gamma/ai_protocols) build the requests and read the answers; this module
fetches and caches them. Nothing here is a table of model names — model
facts come from the provider, or from the public models.dev catalog when
the provider's listing doesn't carry them (OpenAI's and DeepSeek's carry
neither; Anthropic's and the Codex backend's carry both).

``fetch_json`` is the one fetch every listing, quota and credential check
goes through (a short, UI-friendly timeout)."""

import json
import os
import re
import threading
import time
from urllib.request import Request as URLRequest, urlopen

from . import ai_protocols
from .logbuf import log

FETCH_TIMEOUT = 5
MODELS_DEV_URL = "https://models.dev/api.json"
MODELS_DEV_TIMEOUT = 15
# GAMMA_MODEL_CATALOG=off keeps the server from asking models.dev (an
# offline server, the browser suite): model facts then come from the
# providers' own listings alone.
MODEL_CATALOG = os.environ.get("GAMMA_MODEL_CATALOG", "").strip().lower() not in ("0", "off", "false", "no")
# Like the Codex version: a good answer is kept for hours; a failed lookup
# is retried after minutes, the last good answer served meanwhile.
WINDOW_TTL = 6 * 3600
WINDOW_RETRY = 600

# "<provider id>|<base url>" -> {"windows": {model: n}, "efforts": {model: [level]},
#                                "speeds": {model: [tier]}, "until": t}
_listings = {}
_listings_lock = threading.Lock()
# index: model id -> [(provider key, {"window": n, "efforts": (level, …) | None})]
_models_dev = {"index": None, "until": 0.0}
_models_dev_lock = threading.Lock()


def fetch_json(req: URLRequest):
    with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return json.loads(resp.read())


def list_models(conf: dict) -> list:
    """The entry's chat models as ``[{id, context_window, efforts, speeds}]``
    (0 / None = the listing names none), in the order to offer them. Raises
    what the fetch raises (an HTTPError carries the provider's status)."""
    proto = ai_protocols.of(conf)
    return proto.models(fetch_json(proto.models_request(conf)), conf)


def _listed(provider_id: str, conf: dict) -> dict:
    """{"windows": {model: n}, "efforts": {model: [level]}, "speeds": {model:
    [tier]}} from the entry's own listing, cached."""
    key = f"{provider_id}|{conf['base_url']}"
    with _listings_lock:
        now = time.time()
        cached = _listings.get(key)
        if cached and now < cached["until"]:
            return cached
        try:
            models = list_models(conf)
            _listings[key] = {"windows": {m["id"]: m["context_window"] for m in models if m["context_window"]},
                              "efforts": {m["id"]: m["efforts"] for m in models if m.get("efforts") is not None},
                              "speeds": {m["id"]: m["speeds"] for m in models if m.get("speeds") is not None},
                              "until": now + WINDOW_TTL}
        except Exception as e:
            log.warning(f"[ai] model listing for model facts failed ({conf.get('name')}): {e}")
            _listings[key] = {"windows": cached["windows"] if cached else {},
                              "efforts": cached["efforts"] if cached else {},
                              "speeds": cached["speeds"] if cached else {}, "until": now + WINDOW_RETRY}
        return _listings[key]


def _listed_windows(provider_id: str, conf: dict) -> dict:
    """{model: window} from the entry's own listing, cached."""
    return _listed(provider_id, conf)["windows"]


def _catalog_efforts(m: dict):
    """A models.dev entry's effort levels: its ``reasoning_options`` effort
    values; ``()`` for a model that doesn't reason or only takes a token
    budget; None when the entry predates ``reasoning_options``."""
    if m.get("reasoning") is False:
        return ()
    options = m.get("reasoning_options")
    if not isinstance(options, list):
        return None
    for option in options:
        if isinstance(option, dict) and option.get("type") == "effort" and isinstance(option.get("values"), list):
            return tuple(str(v) for v in option["values"])
    return ()


def _models_dev_index() -> dict:
    """models.dev's catalog as {lowercased model id: [(provider key, {window,
    efforts})]}, cached; also indexed by the part after a "vendor/" prefix."""
    if not MODEL_CATALOG:
        return {}
    with _models_dev_lock:
        now = time.time()
        if now < _models_dev["until"]:
            return _models_dev["index"] or {}
        try:
            with urlopen(URLRequest(MODELS_DEV_URL, headers={"Accept": "application/json",
                                                             "User-Agent": "Gamma/model-catalog"}),
                         timeout=MODELS_DEV_TIMEOUT) as resp:
                data = json.loads(resp.read())
            index = {}
            for pkey, provider in (data.items() if isinstance(data, dict) else []):
                models = provider.get("models") if isinstance(provider, dict) else None
                for mid, m in (models.items() if isinstance(models, dict) else []):
                    if not isinstance(m, dict):
                        continue
                    n = (m.get("limit") or {}).get("context")
                    facts = {"window": n if isinstance(n, int) and n > 0 else 0, "efforts": _catalog_efforts(m)}
                    if not facts["window"] and facts["efforts"] is None:
                        continue
                    name = str(m.get("id") or mid).lower()
                    for alias in {name, name.rsplit("/", 1)[-1]}:
                        index.setdefault(alias, []).append((str(pkey).lower(), facts))
            if not index:
                raise ValueError("empty catalog")
            _models_dev.update(index=index, until=now + WINDOW_TTL)
        except Exception as e:
            log.warning(f"[ai] models.dev catalog lookup failed: {e}")
            _models_dev["until"] = now + WINDOW_RETRY
        return _models_dev["index"] or {}


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _catalog_value(model: str, conf: dict, fact: str, rank):
    """One fact about the model per models.dev (None when no listing has it).
    Several providers may list one model, often with their own caps: the
    provider this entry talks to wins (``Protocol.catalog_hints`` — by
    default the endpoint's host), else the value most of them agree on
    (ties go to the higher ``rank(value)``)."""
    index = _models_dev_index()
    name = model.lower()
    found = [(pkey, facts[fact]) for pkey, facts in (index.get(name) or index.get(name.rsplit("/", 1)[-1]) or [])
             if facts[fact] or facts[fact] == ()]
    if not found:
        return None
    names = set()
    for hint in ai_protocols.of(conf).catalog_hints(conf):
        # Whole host labels and runs of them: "api.moonshot.ai" names
        # moonshot, moonshotai, … — never a substring like "a".
        labels = [_alnum(label) for label in (hint or "").split(".")]
        names |= {"".join(labels[i:j]) for i in range(len(labels)) for j in range(i + 1, len(labels) + 1)}
    for pkey, value in found:
        if _alnum(pkey) in names:
            return value
    counts = {}
    for _, value in found:
        counts[value] = counts.get(value, 0) + 1
    return max(counts, key=lambda value: (counts[value], rank(value)))


def _catalog_window(model: str, conf: dict) -> int:
    """The model's window per models.dev, 0 when unknown."""
    return _catalog_value(model, conf, "window", lambda n: n) or 0


def context_window(provider_id: str, conf: dict, model: str) -> tuple:
    """``(window, source)`` for one of the entry's models: source
    ``"provider"`` (its own listing) or ``"models.dev"``; ``(0, "")`` when
    neither knows the model — callers show nothing rather than a guess."""
    n = _listed_windows(provider_id, conf).get(model)
    if n:
        return n, "provider"
    n = _catalog_window(model, conf)
    return (n, "models.dev") if n else (0, "")


def reasoning_efforts(provider_id: str, conf: dict, model: str) -> tuple:
    """``(levels, source)`` for one of the entry's models: the reasoning
    efforts it takes, in order (``[]`` = none — no effort control), from its
    own listing (``"provider"``) or models.dev; ``(None, "")`` when neither
    knows."""
    listed = _listed(provider_id, conf)["efforts"].get(model)
    if listed is not None:
        return list(listed), "provider"
    found = _catalog_value(model, conf, "efforts", len)
    return (list(found), "models.dev") if found is not None else (None, "")


def speed_tiers(provider_id: str, conf: dict, model: str) -> tuple:
    """``(tiers, source)`` for one of the entry's models: the service tiers
    it may be asked to run at, in ``ai_protocols.SPEED_ORDER`` (``[]`` =
    none — no speed control), from its own listing (``"provider"``) or, when
    that names none, from the wire itself (``"protocol"``). models.dev
    carries no speed facts, so a wire-wide answer is as specific as it gets:
    Anthropic's listing says nothing about speed, so fast mode is offered
    for every model its endpoint serves and one that doesn't take it is
    refused upstream."""
    listed = _listed(provider_id, conf)["speeds"].get(model)
    if listed is not None:
        return list(listed), "provider"
    tiers = ai_protocols.of(conf).speed_tiers(conf)
    return (tiers, "protocol") if tiers else ([], "")
