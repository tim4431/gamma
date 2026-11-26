"""What a provider entry offers, asked live: its model listing and each
model's context window, reasoning-effort levels, speed tiers, whether it
reads pictures, how long a reply it may write and what it costs. The
protocol adapters (gamma/ai_protocols) build the requests and read the
answers; this module fetches and caches them. Nothing here is a table of
model names — model facts come from the provider, or from the public
models.dev catalog when the provider's listing doesn't carry them (OpenAI's
and DeepSeek's carry neither; Anthropic's and the Codex backend's carry
both).

The caches are stale-while-revalidate: a fact past its time is served as
it is while one fetch, on a thread of its own and outside the caches'
locks, asks again (``_background``: callers at the same time share it).
Only a cache never filled since the server started is waited for, and how
long is the caller's (``wait``): the chat's own lookups wait for the
fetch, the recorder of a call's cost at most ``RECORD_WAIT``, and the
snapshot readers (``facts``: /api/ai/models) not at all.

``fetch_json`` is the one fetch every listing, quota and credential check
goes through (a short, UI-friendly timeout)."""

import json
import re
import threading
import time
from urllib.request import Request as URLRequest, urlopen

from . import ai_protocols, server_options
from .ai_protocols.base import price_number, tariff
from .logbuf import log

FETCH_TIMEOUT = 5
MODELS_DEV_URL = "https://models.dev/api.json"
MODELS_DEV_TIMEOUT = 15
# The model catalog switched off (Settings → Server → Online services,
# server_options ``model_catalog``: an offline server, the browser suite)
# keeps the server from asking models.dev: model facts then come from the
# providers' own listings alone.
# Like the Codex version: a good answer is kept for hours; a failed lookup
# is retried after minutes, the last good answer served meanwhile.
WINDOW_TTL = 6 * 3600
WINDOW_RETRY = 600
# How long recording a call's cost waits for a catalog never fetched since
# the server started (a headless call such as a clip's metadata lookup is
# the first to ask for it).
RECORD_WAIT = 5
# The longest a caller that waits (``wait=None``) waits for one fetch: its
# own timeouts end it well before.
_FETCH_CEILING = MODELS_DEV_TIMEOUT * 2

_LISTED = ("windows", "efforts", "speeds", "images", "prices")
# "<provider id>|<base url>" -> {"windows": {model: n}, "efforts": {model: [level]},
#                                "speeds": {model: [tier]}, "images": {model: bool},
#                                "prices": {model: price | False}, "until": t}
_listings = {}
_listings_lock = threading.Lock()
_NO_LISTING = {**{f: {} for f in _LISTED}, "until": 0.0}
# index: model id -> [(provider key, {"window": n | None, "efforts": (level, …) | None,
#                                     "images": bool | None, "output": n | None})]
# prices: (alphanumeric provider key, lowercased model id) -> price (listed_price's shape)
# until: 0.0 until the first fetch was tried.
_models_dev = {"index": None, "prices": None, "until": 0.0}
_models_dev_lock = threading.Lock()
# The fetch under way per cache key, and the event it sets when done.
_inflight: dict = {}
_inflight_lock = threading.Lock()


def fetch_json(req: URLRequest):
    with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return json.loads(resp.read())


def list_models(conf: dict) -> list:
    """The entry's chat models as ``[{id, context_window, efforts, speeds,
    images, price}]`` (0 / None = the listing names none), in the order to
    offer them. Raises what the fetch raises (an HTTPError carries the
    provider's status)."""
    proto = ai_protocols.of(conf)
    return proto.models(fetch_json(proto.models_request(conf)), conf)


def _background(key: str, run) -> threading.Event:
    """``run()`` on a thread of its own, unless a fetch of ``key`` is under
    way already: the event set when it is done, which a caller that has
    nothing to serve yet waits on. The network never runs under a cache's
    lock, and callers at the same time share one fetch."""
    with _inflight_lock:
        done = _inflight.get(key)
        if done is not None:
            return done
        done = _inflight[key] = threading.Event()

    def go():
        try:
            run()
        except Exception as e:  # a fetch's own failures are cached by it; anything else is a bug
            log.warning(f"[ai] model catalog fetch {key[:60]} failed: {e}")
        finally:
            with _inflight_lock:
                _inflight.pop(key, None)
            done.set()
    threading.Thread(target=go, name=f"ai-catalog {key[:40]}", daemon=True).start()
    return done


def _wait(done: threading.Event, wait) -> None:
    """Wait for a fetch: ``wait`` seconds, None until it ends, 0 not at all."""
    if wait is None:
        done.wait(_FETCH_CEILING)
    elif wait > 0:
        done.wait(wait)


def settle(timeout: float = 10.0) -> None:
    """Wait for the fetches under way (the tests: none outlives the stubs it
    began under)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _inflight_lock:
            pending = list(_inflight.values())
        if not pending:
            return
        for done in pending:
            done.wait(max(0.0, deadline - time.monotonic()))


def _store_listing(key: str, conf: dict) -> None:
    now = time.time()
    try:
        models = list_models(conf)
        value = {"windows": {m["id"]: m["context_window"] for m in models if m["context_window"]},
                 "efforts": {m["id"]: m["efforts"] for m in models if m.get("efforts") is not None},
                 "speeds": {m["id"]: m["speeds"] for m in models if m.get("speeds") is not None},
                 "images": {m["id"]: m["images"] for m in models if m.get("images") is not None},
                 "prices": {m["id"]: m["price"] for m in models if m.get("price") is not None},
                 "until": now + WINDOW_TTL}
    except Exception as e:
        log.warning(f"[ai] model listing for model facts failed ({conf.get('name')}): {e}")
        with _listings_lock:
            cached = _listings.get(key) or {}
        value = {**{f: cached.get(f) or {} for f in _LISTED}, "until": now + WINDOW_RETRY}
    with _listings_lock:
        _listings[key] = value


def _listed(provider_id: str, conf: dict, wait=None) -> dict:
    """{"windows": {model: n}, "efforts": {model: [level]}, "speeds": {model:
    [tier]}, "images": {model: bool}, "prices": {model: price}} from the
    entry's own listing, cached (stale-while-revalidate; ``wait`` how long
    to wait for a listing not fetched yet)."""
    key = f"{provider_id}|{conf['base_url']}"
    with _listings_lock:
        cached = _listings.get(key)
    if cached is not None and time.time() < cached["until"]:
        return cached
    done = _background("listing|" + key, lambda: _store_listing(key, conf))
    if cached is None:
        _wait(done, wait)
        with _listings_lock:
            cached = _listings.get(key)
    return cached or _NO_LISTING


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


def _catalog_images(m: dict):
    """A models.dev entry's picture input: True when ``modalities.input``
    names images, False when it lists the inputs without them, None when
    the entry says nothing."""
    inputs = (m.get("modalities") or {}).get("input")
    return "image" in inputs if isinstance(inputs, list) else None


def _catalog_price(cost) -> dict | None:
    """A models.dev ``cost`` as a price (``listed_price``'s shape): its
    tariff, and its context tiers (``tiers`` of type context, else
    ``context_over_200k``), each for a whole prompt over ``size`` tokens."""
    if not isinstance(cost, dict):
        return None

    def of(fields):
        return tariff(input=price_number(fields.get("input")), output=price_number(fields.get("output")),
                      cache_read=price_number(fields.get("cache_read")),
                      cache_write=price_number(fields.get("cache_write")))
    base = of(cost)
    if not base:
        return None
    tiers = []
    for tier in cost.get("tiers") or []:
        when = tier.get("tier") if isinstance(tier, dict) else None
        size = when.get("size") if isinstance(when, dict) and when.get("type") == "context" else None
        if isinstance(size, int) and not isinstance(size, bool) and of(tier):
            tiers.append({"size": size, **of(tier)})
    if not tiers and isinstance(cost.get("context_over_200k"), dict) and of(cost["context_over_200k"]):
        tiers.append({"size": 200_000, **of(cost["context_over_200k"])})
    return {**base, **({"tiers": sorted(tiers, key=lambda t: t["size"])} if tiers else {})}


def _positive(n):
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else None


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _store_models_dev() -> None:
    now = time.time()
    try:
        with urlopen(URLRequest(MODELS_DEV_URL, headers={"Accept": "application/json",
                                                         "User-Agent": "Gamma/model-catalog"}),
                     timeout=MODELS_DEV_TIMEOUT) as resp:
            data = json.loads(resp.read())
        index, prices = {}, {}
        for pkey, provider in (data.items() if isinstance(data, dict) else []):
            models = provider.get("models") if isinstance(provider, dict) else None
            for mid, m in (models.items() if isinstance(models, dict) else []):
                if not isinstance(m, dict):
                    continue
                names = {str(mid).lower(), str(m.get("id") or mid).lower()}
                price = _catalog_price(m.get("cost"))
                for name in names if price else ():
                    prices[(_alnum(str(pkey)), name)] = price
                limit = m.get("limit") or {}
                facts = {"window": _positive(limit.get("context")), "efforts": _catalog_efforts(m),
                         "images": _catalog_images(m), "output": _positive(limit.get("output"))}
                if all(value is None for value in facts.values()):
                    continue
                name = str(m.get("id") or mid).lower()
                for alias in {name, name.rsplit("/", 1)[-1]}:
                    index.setdefault(alias, []).append((str(pkey).lower(), facts))
        if not index:
            raise ValueError("empty catalog")
        with _models_dev_lock:
            _models_dev.update(index=index, prices=prices, until=now + WINDOW_TTL)
    except Exception as e:
        log.warning(f"[ai] models.dev catalog lookup failed: {e}")
        with _models_dev_lock:
            _models_dev["until"] = now + WINDOW_RETRY


def _models_dev_data(wait=None) -> dict:
    """models.dev's catalog as {"index": {lowercased model id: [(provider
    key, {window, efforts, images, output})]} (also by the part after a
    "vendor/" prefix), "prices": {(provider key, model id): price}}, cached
    (stale-while-revalidate; ``wait``: how long to wait when it was never
    fetched since the server started). {} while the catalog is switched off."""
    if not server_options.get("model_catalog"):
        return {}
    with _models_dev_lock:
        until = _models_dev.get("until") or 0.0
    if time.time() >= until:
        done = _background("models.dev", _store_models_dev)
        if not until:
            _wait(done, wait)
    with _models_dev_lock:
        return {"index": _models_dev.get("index") or {}, "prices": _models_dev.get("prices") or {}}


def _models_dev_index(wait=None) -> dict:
    return _models_dev_data(wait).get("index") or {}


def _catalog_value(model: str, conf: dict, fact: str, rank, wait=None):
    """One fact about the model per models.dev (None when no listing has it).
    Several providers may list one model, often with their own caps: the
    provider this entry talks to wins (``Protocol.catalog_hints`` — by
    default the endpoint's host), else the value most of them agree on
    (ties go to the higher ``rank(value)``)."""
    index = _models_dev_index(wait)
    name = model.lower()
    found = [(pkey, facts[fact]) for pkey, facts in (index.get(name) or index.get(name.rsplit("/", 1)[-1]) or [])
             if facts[fact] is not None]
    if not found:
        return None
    names = set(_named_keys(conf))
    for pkey, value in found:
        if _alnum(pkey) in names:
            return value
    counts = {}
    for _, value in found:
        counts[value] = counts.get(value, 0) + 1
    return max(counts, key=lambda value: (counts[value], rank(value)))


def _named_keys(conf: dict) -> list:
    """The models.dev provider keys an entry names (``Protocol.catalog_hints``),
    alphanumeric, most specific first: a named key (a service's ``catalog``,
    "openai" for the ChatGPT backend), then the endpoint host's whole labels
    and runs of them — "api.moonshot.ai" names moonshot, moonshotai, … but
    never a substring like "a"."""
    named, hosts = [], []
    for hint in ai_protocols.of(conf).catalog_hints(conf):
        hint = (hint or "").lower()
        if "." not in hint:
            named.append(_alnum(hint))
            continue
        labels = [_alnum(label) for label in hint.split(".")]
        hosts += ["".join(labels[i:j]) for i in range(len(labels)) for j in range(i + 1, len(labels) + 1)]
    return [key for key in dict.fromkeys(named + hosts) if key]


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


def image_input(provider_id: str, conf: dict, model: str) -> tuple:
    """``(reads_pictures, source)`` for one of the entry's models: its own
    listing (``"provider"``) or models.dev; ``(None, "")`` when neither
    knows — callers then send pictures as they always have."""
    listed = _listed(provider_id, conf)["images"].get(model)
    if listed is not None:
        return listed, "provider"
    # A tie goes to True: a picture is only held back when the sources say so.
    found = _catalog_value(model, conf, "images", int)
    return (found, "models.dev") if found is not None else (None, "")


def output_limit(conf: dict, model: str) -> int:
    """The longest reply the model may write per models.dev, 0 when unknown."""
    return _catalog_value(model, conf, "output", lambda n: n) or 0


def reply_cap(conf: dict, model: str) -> int:
    """The chat's reply cap for one of the entry's models: a named service's
    ``max_tokens`` (its models count their thinking toward it), bounded by
    the model's own output limit, and the default where that limit is
    unknown or the service names no cap (OpenAI, Anthropic, a custom
    endpoint)."""
    cap = (ai_protocols.service_of(conf) or {}).get("max_tokens")
    limit = output_limit(conf, model) if cap else 0
    return min(cap, limit) if limit else ai_protocols.DEFAULT_MAX_TOKENS


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


# --- prices --------------------------------------------------------------------

def _catalog_model_price(conf: dict, model: str, wait=None) -> dict | None:
    """The model's price per models.dev under a provider key the entry
    names (``_named_keys``), the exact model id only: never a consensus of
    other providers, never the loose aliases the facts above take. A free
    tariff under a subscription plan's key is the plan's, not a price."""
    prices = _models_dev_data(wait).get("prices") or {}
    name = model.lower()
    for key in _named_keys(conf):
        found = prices.get((key, name))
        if found:
            plan = (ai_protocols.service_of(conf) or {}).get("billing") == "subscription"
            return None if plan and not found["input"] and not found["output"] else found
    return None


def price(conf: dict, model: str, provider_id: str = "", wait=0) -> dict | None:
    """What one of the entry's models costs, USD per 1M tokens: ``{input,
    output, cache_read?, cache_write?, tiers?, speeds?}``
    (``ai_protocols.base.listed_price``), or None when nothing names it
    exactly. The entry's own listing first (OpenRouter's per-token prices,
    OpenCode's), then models.dev under a key the entry names
    (``_catalog_model_price``); a custom gateway naming none is unknown.
    Cached only: ``wait`` is how long a catalog never fetched since the
    server started is waited for (0 = not at all, a snapshot; the
    recorder's ``RECORD_WAIT``), shared by both lookups."""
    ends = None if wait is None else time.monotonic() + wait

    def left():
        return None if ends is None else max(0.0, ends - time.monotonic())
    listed = _listed(provider_id, conf, left()).get("prices") or {}
    if model in listed:
        return listed[model] or None  # False: a price that varies, which nothing else knows either
    return _catalog_model_price(conf, model, left())


def _facts(conf: dict, model: str, listed: dict, billing: str, wait) -> dict:
    """``{context, images, price}`` of one of the entry's models: what its
    listing says (``listed``: a ``list_models`` row's ``context_window``,
    ``images`` and ``price``), else models.dev, waited for up to ``wait``
    seconds when it was never fetched. ``price`` only for a connection paid
    per token (``billing`` "api"); null for what nobody knows, a listing's
    price that varies (False) included."""
    window = listed.get("context_window") or _catalog_value(model, conf, "window", lambda n: n, wait)
    images = listed.get("images")
    if images is None:
        images = _catalog_value(model, conf, "images", int, wait)
    cost = None
    if billing == "api":
        cost = listed.get("price")
        if cost is None:
            cost = _catalog_model_price(conf, model, wait)
    return {"context": window or None, "images": images, "price": cost or None}


def facts(provider_id: str, conf: dict, model: str, billing: str) -> dict:
    """``_facts`` from what is cached, never waiting on the network (a
    lookup not made yet is started, for the next ask): /api/ai/models'
    facts."""
    listing = _listed(provider_id, conf, 0)
    return _facts(conf, model, {"context_window": listing["windows"].get(model),
                                "images": listing["images"].get(model),
                                "price": listing["prices"].get(model)}, billing, 0)


def listing_facts(conf: dict, models: list, billing: str, wait=RECORD_WAIT) -> dict:
    """``{model: _facts}`` for a listing just fetched (``list_models``'
    rows: an unsaved connection's, in the connect form)."""
    return {m["id"]: _facts(conf, m["id"], m, billing, wait) for m in models}
