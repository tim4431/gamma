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
/api/admin/ai-providers*): the same shape — an API key, or a ChatGPT
sign-in made through /api/admin/ai-providers/chatgpt/* — ids namespaced
``server:<id>`` so they never collide with an account's. They live in the
users.db `settings` KV under `ai_providers` as
{"providers": [...], "guests": bool, "allowance": {"accounts": N, "guests": N}},
each api_key and each sign-in's tokens Fernet-encrypted with the data
directory's key (the cloud client secret's scheme); a shared sign-in's
tokens are refreshed under that entry's own lock and written back to the
KV. ai_runtime() offers them to every account after its own;
guest accounts only while `guests` is on. The allowance meters them per
account over a rolling 24 hours (tokens, 0 = unlimited; guests and other
accounts each have their own limit): ai_runtime() reports it and marks the
shared provider confs so ai_client.open_ai refuses a call once it is used up
(docs/dev/guests.md).
"""

import json
import re
import secrets
import sqlite3
import threading
import time

from cryptography.fernet import InvalidToken
from fastapi import HTTPException

from . import ai_protocols, ai_usage
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
        # The signed-in account's e-mail: like the key hint, not for someone
        # who sees a shared sign-in without being an admin.
        "account": (oauth.get("email") or "") if hint else "",
    }


def protocol_choices() -> dict:
    """What the settings form offers: the protocols (auth "oauth" = sign-in
    entries, no API key field) and the named services."""
    protocols = [{"id": pid, "label": proto.label, "default_base_url": proto.base_url, "auth": proto.auth}
                 for pid, proto in ai_protocols.PROTOCOLS.items()]
    return {"protocols": protocols, "services": ai_protocols.SERVICES}


# --- the server's shared entries ----------------------------------------------

SERVER_AI_KEY = "ai_providers"  # the users.db `settings` KV key
SERVER_ID_PREFIX = ai_usage.SHARED_PREFIX  # "server:"
# The shared allowance's ceiling (tokens per account per 24 h): far beyond
# any real day, low enough to stay a sane integer everywhere.
ALLOWANCE_MAX = 1_000_000_000
_server_lock = threading.Lock()


def is_server_id(provider_id) -> bool:
    return str(provider_id or "").startswith(SERVER_ID_PREFIX)


def own_entries(user: str) -> list:
    """The account's own entries (a ``server:`` id is never one of them)."""
    return [e for e in load_provider_entries(user) if not is_server_id(e.get("id"))] if user else []


def new_server_provider_id() -> str:
    return SERVER_ID_PREFIX + new_provider_id()


def _allowance_limit(value) -> int:
    """A stored allowance number as a clean limit (anything odd reads as 0 =
    unlimited — never as a lock-out)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return min(max(value, 0), ALLOWANCE_MAX)


def load_server_ai() -> dict:
    """The shared entries with their keys decrypted, the guest switch and the
    allowance: {"providers": [...], "guests": bool, "allowance": {"accounts":
    int, "guests": int}} (tokens per account per 24 h, 0 = unlimited). A key
    or a sign-in that no longer decrypts (the data directory's key changed)
    reads as none, with a warning."""
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
        sealed = e.pop("oauth", None)
        if isinstance(sealed, str) and sealed:
            try:
                oauth = json.loads(cipher().decrypt(sealed.encode("ascii")).decode("utf-8"))
                if isinstance(oauth, dict):
                    e["oauth"] = oauth
            except (InvalidToken, ValueError):
                log.warning(f"shared AI provider {e.get('id')}: the stored sign-in cannot be decrypted (key changed?)")
        out.append(e)
    allowance = value.get("allowance") if isinstance(value.get("allowance"), dict) else {}
    return {"providers": out, "guests": value.get("guests") is True,
            "allowance": {k: _allowance_limit(allowance.get(k)) for k in ("accounts", "guests")}}


def _sealed(entry: dict) -> dict:
    """A shared entry as stored: the key and the sign-in's tokens encrypted."""
    out = {**entry, "api_key": cipher().encrypt(entry["api_key"].encode("utf-8")).decode("ascii")
           if entry.get("api_key") else ""}
    oauth = entry.get("oauth")
    if isinstance(oauth, dict) and oauth:
        out["oauth"] = cipher().encrypt(json.dumps(oauth).encode("utf-8")).decode("ascii")
    else:
        out.pop("oauth", None)
    return out


def save_server_ai(config: dict) -> None:
    providers = [_sealed(e) for e in config.get("providers") or []]
    allowance = config.get("allowance") if isinstance(config.get("allowance"), dict) else {}
    _set_raw(SERVER_AI_KEY, json.dumps({
        "providers": providers, "guests": bool(config.get("guests")),
        "allowance": {k: _allowance_limit(allowance.get(k)) for k in ("accounts", "guests")}}))


def validated_allowance(value) -> dict:
    """The allowance part of an admin request — {"accounts"?: N, "guests"?:
    N}, either key or both — checked: whole token counts from 0 (unlimited)
    to ALLOWANCE_MAX. 400 on anything else."""
    if not isinstance(value, dict) or set(value) - {"accounts", "guests"}:
        raise HTTPException(status_code=400, detail='allowance takes "accounts" and "guests"')
    for v in value.values():
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= ALLOWANCE_MAX:
            raise HTTPException(status_code=400,
                                detail=f"allowance must be a whole number of tokens from 0 to {ALLOWANCE_MAX}")
    return dict(value)


def edit_server_ai(change) -> dict:
    """Read-modify-write the shared config under one lock: ``change(config)``
    mutates it in place (and may raise to abort). Returns the saved config."""
    with _server_lock:
        config = load_server_ai()
        change(config)
        save_server_ai(config)
        return config


def shared_access(user: str) -> tuple[list, int]:
    """The shared entries ``user`` may use — every account may, a guest
    account only while the admin switch is on, a name that is not an account
    never — and the allowance limit that applies to it (the guests' or the
    accounts', by the users row's ``is_guest``; 0 = unlimited)."""
    if not user:
        return [], 0
    config = load_server_ai()
    if not config["providers"]:
        return [], 0
    try:
        with connect_users_db() as conn:
            row = conn.execute("SELECT is_guest FROM users WHERE username = ?", (user,)).fetchone()
    except sqlite3.Error:
        row = None
    if not row or (row[0] and not config["guests"]):
        return [], 0
    return config["providers"], config["allowance"]["guests" if row[0] else "accounts"]


def _has_credential(entry: dict) -> bool:
    if is_oauth_protocol(entry.get("protocol")):
        oauth = entry.get("oauth")
        return isinstance(oauth, dict) and bool(oauth.get("access_token"))
    return bool((entry.get("api_key") or "").strip())


def server_entries_for(user: str) -> list:
    """The shared entries ``user`` may use (``shared_access``)."""
    return shared_access(user)[0]


def allowance_status(user: str, limit: int) -> dict:
    """What the account card, the pickers and the Usage pane show of the
    shared allowance: {"limit" (0 = unlimited), "used" (tokens through
    shared entries in the last 24 h), "exhausted"}."""
    used = ai_usage.shared_used(user)
    return {"limit": limit, "used": used, "exhausted": bool(limit) and used >= limit}


def shared_allowance(user: str) -> dict | None:
    """``allowance_status`` when a shared entry applies to ``user`` (one it
    can use: a key, or a connected sign-in; limit 0 when the admin set
    none), else None — the object ai_runtime() reports, without building
    the runtime."""
    entries, limit = shared_access(user)
    usable = any(ai_protocols.PROTOCOLS.get(e.get("protocol")) and _has_credential(e) for e in entries)
    return allowance_status(user, limit) if usable else None


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

# One refresh at a time per sign-in: per account for its own entries, per
# entry for a shared one (every account's requests refresh the same tokens).
# OpenAI rotates refresh tokens, so of two concurrent refreshes (the
# translator fires dozens of requests at once) the second fails — and its
# save must not overwrite the first one's fresh tokens.
_refresh_locks: dict = {}
_refresh_locks_guard = threading.Lock()


def _refresh_lock(key) -> threading.Lock:
    """``key``: an account name, or ``("server", <provider id>)`` for a
    shared sign-in (a tuple never equals a username)."""
    with _refresh_locks_guard:
        return _refresh_locks.setdefault(key, threading.Lock())


def _refresh_tokens(lock_key, read, write, flow) -> dict | None:
    """Refresh one sign-in's tokens through its protocol's OAuth ``flow``
    under ``lock_key``'s lock. ``read()`` gives the stored oauth dict fresh
    (so a refresh another request just did is reused, not repeated),
    ``write(oauth)`` stores the result. Returns the current oauth dict."""
    with _refresh_lock(lock_key):
        oauth = read()
        if not oauth or not oauth.get("access_token"):
            return None
        failed_at = oauth.get("refresh_failed_at") or 0
        if not flow.needs_refresh(oauth) or time.time() - failed_at <= REFRESH_BACKOFF_S:
            return oauth
        refreshed = flow.refresh(oauth)
        if refreshed:
            oauth = refreshed
        else:
            # Keep the stale token: the call will fail with a clear upstream
            # 401 → someone reconnects in Settings.
            oauth["refresh_failed_at"] = int(time.time())
        write(oauth)
        return oauth


def _entry_oauth(entries: list, provider_id: str) -> dict | None:
    e = next((x for x in entries if x.get("id") == provider_id), None)
    return e.get("oauth") if e and isinstance(e.get("oauth"), dict) else None


def _refreshed_oauth(user: str, provider_id: str, flow) -> dict | None:
    """An account's own sign-in entry, refreshed under the account's lock."""
    def write(oauth):
        entries = load_provider_entries(user)
        for e in entries:
            if e.get("id") == provider_id:
                e["oauth"] = oauth
        save_provider_entries(user, entries)
    return _refresh_tokens(user, lambda: _entry_oauth(load_provider_entries(user), provider_id), write, flow)


def _refreshed_server_oauth(provider_id: str, flow) -> dict | None:
    """A shared sign-in entry, refreshed under the entry's own lock and
    written back to the server's config (only its tokens: an admin's edit
    of the other fields meanwhile stays)."""
    def write(oauth):
        def change(config):
            for e in config["providers"]:
                if e.get("id") == provider_id:
                    e["oauth"] = oauth
        edit_server_ai(change)
    return _refresh_tokens(("server", provider_id),
                           lambda: _entry_oauth(load_server_ai()["providers"], provider_id), write, flow)


def ai_runtime(user: str) -> dict:
    """The effective AI config for a request, built from the user's provider
    entries followed by the server's shared ones (``shared_access``):
    {"providers": {id: {api_key, base_url, protocol, name}},
    "models": [{"id": "<pid>:<model>", "provider": pid, "provider_name",
    "model", "native_pdf", "shared"}], "default": the first model — the
    account's own when it has one, else the server's — or None,
    "enabled": bool, "allowance": {"limit", "used", "exhausted"} or None}.

    ``allowance`` is None unless a shared entry made it into the runtime
    (limit 0 = unlimited). Under a limit every shared provider conf carries
    ``"allowance": {"user", "limit"}``, which ai_client.open_ai checks
    before each call. The shared models stay listed once it is used up; a
    refused call says why."""
    shared, limit = shared_access(user)
    providers, models = {}, []
    for e in own_entries(user) + shared:
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
                refreshed = (_refreshed_server_oauth(pid, proto.oauth) if is_server_id(pid)
                             else _refreshed_oauth(user, pid, proto.oauth))
                oauth = refreshed or oauth
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
    allowance = None
    shared_ids = [pid for pid in providers if is_server_id(pid)]
    if shared_ids:
        # Reported whenever a shared entry applies (limit 0 = unlimited);
        # the transport only meters under a limit.
        allowance = allowance_status(user, limit)
        if limit:
            for pid in shared_ids:
                providers[pid]["allowance"] = {"user": user, "limit": limit}
    return {
        "user": user,  # whose config this is — the usage recorder's key
        "providers": providers,
        "models": models,
        "default": models[0] if models else None,
        "enabled": bool(models),
        "allowance": allowance,
    }


def clear_refresh_backoff(user: str, provider_id: str) -> None:
    """Forget a sign-in entry's failed-refresh timestamp so the next
    ai_runtime() re-attempts the token refresh immediately (an explicit
    retry, e.g. the settings Test button). A ``server:<id>`` names a shared
    entry; the caller has checked the account may touch it."""
    if is_server_id(provider_id):
        with _refresh_lock(("server", provider_id)):
            oauth = _entry_oauth(load_server_ai()["providers"], provider_id)
            if oauth and "refresh_failed_at" in oauth:
                def change(config):
                    for e in config["providers"]:
                        if e.get("id") == provider_id and isinstance(e.get("oauth"), dict):
                            e["oauth"].pop("refresh_failed_at", None)
                edit_server_ai(change)
        return
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
