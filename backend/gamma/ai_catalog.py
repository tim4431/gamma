"""What a provider entry offers, asked live: its model listing and each
model's context window. The protocol adapters (gamma/ai_protocols) build the
requests and read the answers; this module fetches and caches them. Nothing
here is a table of model names — model facts come from the provider, or
from the public models.dev catalog when the provider's listing carries no
context window (OpenAI's and DeepSeek's don't).

``fetch_json`` is the one fetch every listing, quota and credential check
goes through (a short, UI-friendly timeout)."""

import json
import re
import threading
import time
from urllib.request import Request as URLRequest, urlopen

from . import ai_protocols
from .logbuf import log

FETCH_TIMEOUT = 5
MODELS_DEV_URL = "https://models.dev/api.json"
MODELS_DEV_TIMEOUT = 15
# Like the Codex version: a good answer is kept for hours; a failed lookup
# is retried after minutes, the last good answer served meanwhile.
WINDOW_TTL = 6 * 3600
WINDOW_RETRY = 600

_listings = {}  # "<provider id>|<base url>" -> {"windows": {model: n}, "until": t}
_listings_lock = threading.Lock()
_models_dev = {"windows": None, "until": 0.0}  # windows: model id -> [(provider key, n)]
_models_dev_lock = threading.Lock()


def fetch_json(req: URLRequest):
    with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return json.loads(resp.read())


def list_models(conf: dict) -> list:
    """The entry's chat models as ``[{id, context_window}]`` (0 = the
    listing names none), in the order to offer them. Raises what the fetch
    raises (an HTTPError carries the provider's status)."""
    proto = ai_protocols.of(conf)
    return proto.models(fetch_json(proto.models_request(conf)), conf)


def _listed_windows(provider_id: str, conf: dict) -> dict:
    """{model: window} from the entry's own listing, cached."""
    key = f"{provider_id}|{conf['base_url']}"
    with _listings_lock:
        now = time.time()
        cached = _listings.get(key)
        if cached and now < cached["until"]:
            return cached["windows"]
        try:
            windows = {m["id"]: m["context_window"] for m in list_models(conf) if m["context_window"]}
            _listings[key] = {"windows": windows, "until": now + WINDOW_TTL}
        except Exception as e:
            log.warning(f"[ai] model listing for context windows failed ({conf.get('name')}): {e}")
            _listings[key] = {"windows": cached["windows"] if cached else {}, "until": now + WINDOW_RETRY}
        return _listings[key]["windows"]


def _models_dev_windows() -> dict:
    """models.dev's catalog as {lowercased model id: [(provider key, window)]},
    cached; also indexed by the part after a "vendor/" prefix."""
    with _models_dev_lock:
        now = time.time()
        if now < _models_dev["until"]:
            return _models_dev["windows"] or {}
        try:
            with urlopen(URLRequest(MODELS_DEV_URL, headers={"Accept": "application/json",
                                                             "User-Agent": "Gamma/model-catalog"}),
                         timeout=MODELS_DEV_TIMEOUT) as resp:
                data = json.loads(resp.read())
            windows = {}
            for pkey, provider in (data.items() if isinstance(data, dict) else []):
                models = provider.get("models") if isinstance(provider, dict) else None
                for mid, m in (models.items() if isinstance(models, dict) else []):
                    n = ((m.get("limit") or {}).get("context")) if isinstance(m, dict) else None
                    if not isinstance(n, int) or n <= 0:
                        continue
                    name = str(m.get("id") or mid).lower()
                    for alias in {name, name.rsplit("/", 1)[-1]}:
                        windows.setdefault(alias, []).append((str(pkey).lower(), n))
            if not windows:
                raise ValueError("empty catalog")
            _models_dev.update(windows=windows, until=now + WINDOW_TTL)
        except Exception as e:
            log.warning(f"[ai] models.dev catalog lookup failed: {e}")
            _models_dev["until"] = now + WINDOW_RETRY
        return _models_dev["windows"] or {}


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _catalog_window(model: str, conf: dict) -> int:
    """The model's window per models.dev. Several providers may list one
    model, often with their own caps: the provider this entry talks to wins
    (``Protocol.catalog_hints`` — by default the endpoint's host), else the
    value most of them agree on."""
    windows = _models_dev_windows()
    name = model.lower()
    found = windows.get(name) or windows.get(name.rsplit("/", 1)[-1]) or []
    if not found:
        return 0
    names = set()
    for hint in ai_protocols.of(conf).catalog_hints(conf):
        # Whole host labels and runs of them: "api.moonshot.ai" names
        # moonshot, moonshotai, … — never a substring like "a".
        labels = [_alnum(label) for label in (hint or "").split(".")]
        names |= {"".join(labels[i:j]) for i in range(len(labels)) for j in range(i + 1, len(labels) + 1)}
    for pkey, n in found:
        if _alnum(pkey) in names:
            return n
    counts = {}
    for _, n in found:
        counts[n] = counts.get(n, 0) + 1
    return max(counts, key=lambda n: (counts[n], n))


def context_window(provider_id: str, conf: dict, model: str) -> tuple:
    """``(window, source)`` for one of the entry's models: source
    ``"provider"`` (its own listing) or ``"models.dev"``; ``(0, "")`` when
    neither knows the model — callers show nothing rather than a guess."""
    n = _listed_windows(provider_id, conf).get(model)
    if n:
        return n, "provider"
    n = _catalog_window(model, conf)
    return (n, "models.dev") if n else (0, "")
