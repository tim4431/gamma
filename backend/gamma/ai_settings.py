"""AI provider entries (GUI-managed API keys): each account's own, and the
server's shared ones.

Users manage a LIST of provider entries (Settings → AI → Connections), each:
  {"id", "name", "protocol": a key of ai_protocols.PROTOCOLS, "api_key" (or
   "oauth" tokens for a sign-in protocol), "base_url": "" = protocol default,
   "models": "a, b" = comma list ("" = none offered yet), "test_model",
   "created_at"}

Entries live in users.db `user_prefs` under the account-wide reserved
`ai-settings` key, which the generic /api/prefs endpoints refuse to serve: the
only read path is the masked GET /api/ai/settings (last 4 characters, never
the key itself). There is no env key.

Admins may add SHARED entries (Settings → Server → Shared AI provider,
/api/admin/ai-providers*): the same shape, API-key protocols only, ids
namespaced ``server:<id>`` so they never collide with an account's. They
live in the users.db `settings` KV under `ai_providers` as
{"providers": [...], "guests": bool}, each api_key Fernet-encrypted with the
data directory's key (the cloud client secret's scheme). ai_runtime()
offers them to every account after its own; the guest account only while
`guests` is on.
"""

import json
import re
import secrets
import sqlite3
import threading
import time

from cryptography.fernet import InvalidToken
from fastapi import HTTPException

from . import ai_protocols
from .db import connect_users_db, get_pref, page_now, set_pref
from .logbuf import log
from .publisher_sessions import cipher
from .server_settings import _get_raw, _set_raw

AI_SETTINGS_PREF_KEY = "ai-settings"

MAX_KEY_LEN = 512
MAX_URL_LEN = 300
MAX_MODELS_LEN = 1000
MAX_NAME_LEN = 60
MAX_PROVIDERS = 20


def load_provider_entries(user: str) -> list:
    value, _ = get_pref(user, AI_SETTINGS_PREF_KEY)
    entries = (value or {}).get("providers") if isinstance(value, dict) else None
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def save_provider_entries(user: str, entries: list):
    set_pref(user, AI_SETTINGS_PREF_KEY, {"providers": entries})


def new_provider_id() -> str:
    return secrets.token_urlsafe(6)


# --- validation, shared by /api/ai/providers* and /api/admin/ai-providers* ----

def is_oauth_protocol(protocol) -> bool:
    """Sign-in protocols (ChatGPT) hold OAuth tokens instead of an API key."""
    proto = ai_protocols.PROTOCOLS.get(protocol)
    return bool(proto) and proto.auth == "oauth"


def apply_provider_fields(entry: dict, fields) -> None:
    """Validate + copy the editable fields of a provider entry in place.
    ``fields`` has ``name``, ``api_key``, ``base_url``, ``models`` and
    ``test_model`` attributes (the request model); None leaves a field."""
    oauth_entry = is_oauth_protocol(entry.get("protocol"))
    if fields.name is not None:
        entry["name"] = str(fields.name).strip()[:MAX_NAME_LEN]
    # OAuth secrets and endpoints are owned by the sign-in flow. In
    # particular, accepting an arbitrary base URL here would let a crafted API
    # request redirect the bearer token on the next model or usage call.
    if fields.api_key and not oauth_entry:  # never clears; delete the entry to drop a key
        key = str(fields.api_key).strip()
        if not key or len(key) > MAX_KEY_LEN or any(c.isspace() for c in key):
            raise HTTPException(status_code=400, detail="invalid API key")
        entry["api_key"] = key
    if fields.base_url is not None and not oauth_entry:
        url = str(fields.base_url).strip().rstrip("/")
        if (url and not re.match(r"^https?://", url)) or len(url) > MAX_URL_LEN:
            raise HTTPException(status_code=400, detail="base URL must start with http(s)://")
        entry["base_url"] = url
    if fields.models is not None:
        models = str(fields.models).strip()
        if len(models) > MAX_MODELS_LEN:
            raise HTTPException(status_code=400, detail="model list too long")
        entry["models"] = models
    if fields.test_model is not None:
        test_model = str(fields.test_model).strip()
        if len(test_model) > 100:
            raise HTTPException(status_code=400, detail="test model name too long")
        entry["test_model"] = test_model


def new_key_entry(fields, entry_id: str) -> dict:
    """A new API-key entry from an add request (``fields.protocol`` plus the
    editable fields); 400 on a sign-in or unknown protocol or a missing key."""
    if fields.protocol not in ai_protocols.PROTOCOLS or is_oauth_protocol(fields.protocol):
        raise HTTPException(status_code=400, detail="unknown protocol")
    if not (fields.api_key or "").strip():
        raise HTTPException(status_code=400, detail="API key required")
    entry = {"id": entry_id, "protocol": fields.protocol,
             "name": "", "api_key": "", "base_url": "", "models": "",
             "created_at": page_now()}
    apply_provider_fields(entry, fields)
    return entry


def update_entry(entry: dict, fields) -> None:
    """Apply an edit request to a saved entry. A protocol change never
    crosses the key/OAuth boundary: it is refused outright rather than
    ignored, so the other fields of such a request (reset for the new
    service) don't land on the old entry."""
    if fields.protocol and fields.protocol != entry.get("protocol"):
        if fields.protocol not in ai_protocols.PROTOCOLS:
            raise HTTPException(status_code=400, detail="unknown protocol")
        if is_oauth_protocol(fields.protocol) != is_oauth_protocol(entry.get("protocol")):
            raise HTTPException(status_code=400,
                                detail="a sign-in connection and an API-key connection can't be "
                                       "switched into each other — add a new connection instead")
        entry["protocol"] = fields.protocol
    apply_provider_fields(entry, fields)


def mask_entry(entry: dict, hint: bool = True) -> dict:
    """What the browser may see of an entry: never the key or the tokens.
    ``hint`` = the key's last 4 characters (off for a shared entry shown to
    someone who is not an admin)."""
    key = (entry.get("api_key") or "").strip()
    oauth = entry.get("oauth") if isinstance(entry.get("oauth"), dict) else {}
    return {
        "id": entry.get("id") or "",
        "name": (entry.get("name") or "").strip(),
        "label": provider_label(entry),  # name, else the service / protocol label
        "protocol": entry.get("protocol") or "",
        # Enough to recognize the key, never enough to use it.
        "key_hint": (f"…{key[-4:]}" if len(key) >= 12 else ("set" if key else "")) if hint else "",
        "base_url": (entry.get("base_url") or "").strip(),
        "models": (entry.get("models") or "").strip(),
        "test_model": (entry.get("test_model") or "").strip(),
        "created_at": entry.get("created_at") or "",
        # ChatGPT sign-in entries: connection status + account label only,
        # never the tokens themselves.
        "oauth_connected": bool(oauth.get("access_token")),
        "account": oauth.get("email") or "",
    }


def protocol_choices(key_only: bool = False) -> dict:
    """What the settings form offers: the protocols (auth "oauth" = sign-in
    entries, no API key field) and the named services. ``key_only`` drops
    the sign-in protocols."""
    protocols = [{"id": pid, "label": proto.label, "default_base_url": proto.base_url, "auth": proto.auth}
                 for pid, proto in ai_protocols.PROTOCOLS.items()
                 if not (key_only and proto.auth == "oauth")]
    ids = {p["id"] for p in protocols}
    return {"protocols": protocols, "services": [s for s in ai_protocols.SERVICES if s["protocol"] in ids]}


# --- the server's shared entries ----------------------------------------------

SERVER_AI_KEY = "ai_providers"  # the users.db `settings` KV key
SERVER_ID_PREFIX = "server:"
_server_lock = threading.Lock()


def is_server_id(provider_id) -> bool:
    return str(provider_id or "").startswith(SERVER_ID_PREFIX)


def new_server_provider_id() -> str:
    return SERVER_ID_PREFIX + new_provider_id()


def load_server_ai() -> dict:
    """The shared entries with their keys decrypted, and the guest switch:
    {"providers": [...], "guests": bool}. A key that no longer decrypts (the
    data directory's key changed) reads as no key, with a warning."""
    try:
        value = json.loads(_get_raw(SERVER_AI_KEY) or "{}")
    except ValueError:
        value = {}
    if not isinstance(value, dict):
        value = {}
    entries = value.get("providers") if isinstance(value.get("providers"), list) else []
    out = []
    for e in entries:
        if not isinstance(e, dict) or not is_server_id(e.get("id")):
            continue
        e = dict(e)
        stored = e.get("api_key") or ""
        try:
            e["api_key"] = cipher().decrypt(stored.encode("ascii")).decode("utf-8") if stored else ""
        except (InvalidToken, ValueError):
            log.warning(f"shared AI provider {e.get('id')}: the stored key cannot be decrypted (key changed?)")
            e["api_key"] = ""
        out.append(e)
    return {"providers": out, "guests": value.get("guests") is True}


def save_server_ai(config: dict) -> None:
    providers = [{**e, "api_key": cipher().encrypt(e["api_key"].encode("utf-8")).decode("ascii")
                  if e.get("api_key") else ""}
                 for e in config.get("providers") or []]
    _set_raw(SERVER_AI_KEY, json.dumps({"providers": providers, "guests": bool(config.get("guests"))}))


def edit_server_ai(change) -> dict:
    """Read-modify-write the shared config under one lock: ``change(config)``
    mutates it in place (and may raise to abort). Returns the saved config."""
    with _server_lock:
        config = load_server_ai()
        change(config)
        save_server_ai(config)
        return config


def server_entries_for(user: str) -> list:
    """The shared entries ``user`` may use: every account may, the guest
    account only while the admin switch is on, a name that is not an
    account never."""
    if not user:
        return []
    config = load_server_ai()
    if not config["providers"]:
        return []
    try:
        with connect_users_db() as conn:
            row = conn.execute("SELECT is_guest FROM users WHERE username = ?", (user,)).fetchone()
    except sqlite3.Error:
        row = None
    if not row or (row[0] and not config["guests"]):
        return []
    return config["providers"]


def provider_label(entry: dict) -> str:
    """An entry's display name: its own, else the named service its endpoint
    is, else its protocol's label."""
    name = (entry.get("name") or "").strip()
    if name:
        return name
    protocol = entry.get("protocol")
    base = (entry.get("base_url") or "").strip().rstrip("/")
    service = next((s for s in ai_protocols.SERVICES
                    if s["protocol"] == protocol and s["base_url"] == base), None)
    if service:
        return service["label"]
    proto = ai_protocols.PROTOCOLS.get(protocol)
    return proto.label if proto else protocol or ""


def entry_models(entry: dict) -> list:
    """The entry's model names. None picked = none offered: there is no
    built-in default (it would go stale); the settings form lists the
    provider's live models to pick from."""
    return [m.strip() for m in (entry.get("models") or "").split(",") if m.strip()]


# A failed sign-in token refresh isn't retried for this long: ai_runtime runs
# on every AI request, and retrying a dead grant each time would add a full
# round trip to the identity provider to chat/metadata/model calls.
REFRESH_BACKOFF_S = 300

# One refresh at a time per account. OpenAI rotates refresh tokens, so of two
# concurrent refreshes (the translator fires dozens of requests at once) the
# second fails — and its save must not overwrite the first one's fresh tokens.
_refresh_locks: dict = {}
_refresh_locks_guard = threading.Lock()


def _refresh_lock(user: str) -> threading.Lock:
    with _refresh_locks_guard:
        return _refresh_locks.setdefault(user, threading.Lock())


def _refreshed_oauth(user: str, provider_id: str, flow) -> dict | None:
    """Refresh one sign-in entry's tokens through its protocol's OAuth
    ``flow`` under the account's lock, reading the entries fresh so a
    refresh another request just did is reused, not repeated. Returns the
    entry's current oauth dict."""
    with _refresh_lock(user):
        entries = load_provider_entries(user)
        e = next((x for x in entries if x.get("id") == provider_id), None)
        oauth = e.get("oauth") if e and isinstance(e.get("oauth"), dict) else None
        if not oauth or not oauth.get("access_token"):
            return None
        failed_at = oauth.get("refresh_failed_at") or 0
        if not flow.needs_refresh(oauth) or time.time() - failed_at <= REFRESH_BACKOFF_S:
            return oauth
        refreshed = flow.refresh(oauth)
        if refreshed:
            e["oauth"] = oauth = refreshed
        else:
            # Keep the stale token: the call will fail with a clear upstream
            # 401 → the user reconnects in Settings.
            oauth["refresh_failed_at"] = int(time.time())
        save_provider_entries(user, entries)
        return oauth


def ai_runtime(user: str) -> dict:
    """The effective AI config for a request, built from the user's provider
    entries followed by the server's shared ones (``server_entries_for``):
    {"providers": {id: {api_key, base_url, protocol, name}},
    "models": [{"id": "<pid>:<model>", "provider": pid, "provider_name",
    "model", "native_pdf", "shared"}], "default": the first model — the
    account's own when it has one, else the server's — or None,
    "enabled": bool}."""
    own = [e for e in (load_provider_entries(user) if user else []) if not is_server_id(e.get("id"))]
    shared = [e for e in server_entries_for(user) if not is_oauth_protocol(e.get("protocol"))]
    providers, models = {}, []
    for e in own + shared:
        protocol = e.get("protocol")
        proto = ai_protocols.PROTOCOLS.get(protocol)
        pid = str(e.get("id") or "")
        if not proto or not pid or pid in providers:
            continue
        name = provider_label(e)
        conf = {
            "base_url": ((e.get("base_url") or "").strip() or proto.base_url).rstrip("/"),
            "protocol": protocol,
            "name": name,
        }
        if proto.auth == "oauth":
            # Sign-in entry: the bearer token comes from the sign-in and is
            # refreshed lazily here (persisted so other requests reuse it).
            oauth = e.get("oauth") if isinstance(e.get("oauth"), dict) else None
            if not oauth or not oauth.get("access_token"):
                continue
            failed_at = oauth.get("refresh_failed_at") or 0
            if proto.oauth.needs_refresh(oauth) and time.time() - failed_at > REFRESH_BACKOFF_S:
                oauth = _refreshed_oauth(user, pid, proto.oauth) or oauth
            conf["api_key"] = oauth["access_token"]
            conf["account_id"] = oauth.get("account_id") or ""
        else:
            key = (e.get("api_key") or "").strip()
            if not key:
                continue
            conf["api_key"] = key
        providers[pid] = conf
        for model in entry_models(e):
            mid = f"{pid}:{model}"
            if mid not in [m["id"] for m in models]:
                models.append({"id": mid, "provider": pid, "provider_name": name, "model": model,
                               # Whether the provider takes the PDF file itself
                               # (native document part); if not, the chat sends
                               # extracted text.
                               "native_pdf": proto.native_pdf,
                               "shared": is_server_id(pid)})
    return {
        "user": user,  # whose config this is — the usage recorder's key
        "providers": providers,
        "models": models,
        "default": models[0] if models else None,
        "enabled": bool(models),
    }


def clear_refresh_backoff(user: str, provider_id: str) -> None:
    """Forget a ChatGPT entry's failed-refresh timestamp so the next
    ai_runtime() re-attempts the token refresh immediately (an explicit
    retry, e.g. the settings Test button)."""
    with _refresh_lock(user):
        entries = load_provider_entries(user)
        for e in entries:
            oauth = e.get("oauth")
            if e.get("id") == provider_id and isinstance(oauth, dict) \
                    and oauth.pop("refresh_failed_at", None) is not None:
                save_provider_entries(user, entries)
                return


def require_ai_runtime(user: str) -> dict:
    """ai_runtime(), raising the standard 503 when no provider is usable."""
    rt = ai_runtime(user)
    if not rt["enabled"]:
        raise HTTPException(status_code=503,
                            detail="AI not configured (add a connection and pick its models in Settings → AI)")
    return rt
