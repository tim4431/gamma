"""AI provider entries (GUI-managed API keys): each account's own, and the
server's shared ones.

Users manage a LIST of provider entries (Settings → AI → Models), each:
  {"id", "name", "protocol": a key of ai_protocols.PROTOCOLS, "api_key" (or
   "oauth" tokens for a sign-in protocol; "" for a keyless local server,
   key_optional), "base_url": "" = protocol default, "models": "a, b" =
   comma list ("" = none offered yet), "test_model", "created_at",
   "billing": an override of how its calls are paid for (billing_of),
   "tool_checks": whether each model calls tools on it}

Which model each task uses is the account's ``ai-models`` pref
(gamma/ai_roles.py), resolved against the models offered into
``ai_runtime()["roles"]``.

Entries live in users.db `user_prefs` under the account-wide reserved
`ai-settings` key, which the generic /api/prefs endpoints refuse to serve: the
only read path is the masked GET /api/ai/settings (last 4 characters, never
the key itself). There is no env key.

Admins may add SHARED entries (Settings → Server → Shared AI provider,
/api/admin/ai-providers*): the same shape — an API key, or a sign-in
(ChatGPT, Gemini) made through /api/admin/ai-providers/<protocol>/* — ids namespaced
``server:<id>`` so they never collide with an account's, each with an
``access`` saying who may use it ({"who": "everyone" | "selected", "users":
[account ids], "guests": bool}). They live in the users.db `settings` KV
under `ai_providers` as
{"providers": [...], "allowance": {"accounts": N, "guests": N}},
each api_key and each sign-in's tokens Fernet-encrypted with the data
directory's key (the cloud client secret's scheme); a shared sign-in's
tokens are refreshed under that entry's own lock and written back to the
KV. ai_runtime() offers an account the entries whose access admits it,
after its own (shared_access). The allowance meters them per account over
a rolling 24 hours (tokens, 0 = unlimited; guests and other accounts each
have their own limit): ai_runtime() reports it and marks the shared
provider confs so ai_client.open_ai refuses a call once it is used up
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

from . import ai_protocols, ai_usage, translate_engines
from .chatgpt_oauth import RefreshUnavailable
from .ai_roles import load_model_prefs, pick_label, resolve_roles
from .db import connect_users_db, get_pref, page_now, set_pref, update_pref
from .logbuf import log
from .publisher_sessions import cipher
from .server_settings import _get_raw, _set_raw

AI_SETTINGS_PREF_KEY = "ai-settings"

MAX_KEY_LEN = 512
MAX_URL_LEN = 300
MAX_MODELS_LEN = 1000
MAX_NAME_LEN = 60
MAX_PROVIDERS = 20
MAX_TOOL_REASON = 200
# An entry's billing override ("" = from the connection: billing_of).
BILLING_OVERRIDES = ("", "api", "subscription", "local")


def _entries_of(value) -> list:
    entries = value.get("providers") if isinstance(value, dict) else None
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def load_provider_entries(user_id: str) -> list:
    return _entries_of(get_pref(user_id, AI_SETTINGS_PREF_KEY)[0])


def save_provider_entries(user_id: str, entries: list):
    """Replace the whole list. Edits go through update_provider_entries."""
    set_pref(user_id, AI_SETTINGS_PREF_KEY, {"providers": entries})


def update_provider_entries(user_id: str, change) -> list:
    """Read-modify-write the account's entries in one transaction
    (db.update_pref): ``change(entries)`` edits the list in place and may
    raise to abort. A list read before a slow call (a sign-in's token
    exchange) is never saved back over a change made meanwhile. Returns the
    saved list."""
    def apply(value):
        entries = _entries_of(value)
        change(entries)
        return {"providers": entries}
    return update_pref(user_id, AI_SETTINGS_PREF_KEY, apply)["providers"]


def new_provider_id() -> str:
    return secrets.token_urlsafe(6)


# --- validation, shared by /api/ai/providers* and /api/admin/ai-providers* ----

def is_oauth_protocol(protocol) -> bool:
    """Sign-in protocols (ChatGPT, Gemini) hold OAuth tokens instead of an API key."""
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
        if key != (entry.get("api_key") or "").strip():
            entry.pop("tool_checks", None)  # another key may reach another deployment
        entry["api_key"] = key
    if fields.base_url is not None and not oauth_entry:
        url = str(fields.base_url).strip().rstrip("/")
        if (url and not re.match(r"^https?://", url)) or len(url) > MAX_URL_LEN:
            raise HTTPException(status_code=400, detail="base URL must start with http(s)://")
        if url != (entry.get("base_url") or "").strip().rstrip("/"):
            entry.pop("tool_checks", None)
        entry["base_url"] = url
    if fields.models is not None:
        models = str(fields.models).strip()
        if len(models) > MAX_MODELS_LEN:
            raise HTTPException(status_code=400, detail="model list too long")
        entry["models"] = models
        _prune_tool_checks(entry)
    if fields.test_model is not None:
        test_model = str(fields.test_model).strip()
        if len(test_model) > 100:
            raise HTTPException(status_code=400, detail="test model name too long")
        entry["test_model"] = test_model
    billing = getattr(fields, "billing", None)
    if billing is not None:
        billing = str(billing).strip()
        if billing not in BILLING_OVERRIDES:
            raise HTTPException(status_code=400, detail='billing is "", "api", "subscription" or "local"')
        if billing and oauth_entry:
            raise HTTPException(status_code=400, detail="a sign-in is always a subscription")
        if billing:
            entry["billing"] = billing
        else:
            entry.pop("billing", None)


def key_optional(entry: dict) -> bool:
    """Whether an API-key entry may go without a key: a preset that says so
    (``key_optional``: a local server), or a custom endpoint — neither the
    protocol's own nor a named service's. ``entry`` needs its protocol and
    base URL."""
    proto = ai_protocols.PROTOCOLS.get(entry.get("protocol"))
    if not proto or proto.auth == "oauth":
        return False
    base = (entry.get("base_url") or "").strip().rstrip("/")
    if not base or base == proto.base_url.rstrip("/"):
        return False
    service = ai_protocols.service_of({"protocol": entry.get("protocol"), "base_url": base})
    return service is None or bool(service.get("key_optional"))


def new_key_entry(fields, entry_id: str) -> dict:
    """A new API-key entry from an add request (``fields.protocol`` plus the
    editable fields); 400 on a sign-in or unknown protocol or a missing key
    (a local server's preset and a custom endpoint may go without one:
    key_optional)."""
    if fields.protocol not in ai_protocols.PROTOCOLS or is_oauth_protocol(fields.protocol):
        raise HTTPException(status_code=400, detail="unknown protocol")
    entry = {"id": entry_id, "protocol": fields.protocol,
             "name": "", "api_key": "", "base_url": "", "models": "",
             "created_at": page_now()}
    apply_provider_fields(entry, fields)
    if not entry["api_key"] and not key_optional(entry):
        raise HTTPException(status_code=400, detail="API key required")
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
        entry.pop("tool_checks", None)
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
        # Sign-in entries: connection status + account label only, never
        # the tokens themselves.
        "oauth_connected": bool(oauth.get("access_token")),
        # The signed-in account's e-mail: like the key hint, not for someone
        # who sees a shared sign-in without being an admin.
        "account": (oauth.get("email") or "") if hint else "",
        # Whether each picked model calls tools here: nothing secret.
        "tool_checks": tool_checks(entry),
        # How its calls are paid for (billing_of), and the override the
        # entry stores ("" = none).
        "billing": billing_of(entry),
        "billing_override": entry.get("billing") if entry.get("billing") in BILLING_OVERRIDES else "",
        # Which service it is, for its logo (service_id).
        "service": service_id(entry),
        # Whether a quota reader says what is left of its plan
        # (ai_protocols.quota_reader: POST /api/ai/providers/{id}/usage),
        # and whether it may go without a key (key_optional).
        "quota": ai_protocols.quota_reader({"protocol": entry.get("protocol"),
                                            "base_url": entry.get("base_url") or ""}) is not None,
        "key_optional": key_optional(entry),
    }


def billing_of(entry: dict) -> str:
    """How a call through a connection is paid for — one of
    ai_usage.BILLINGS — from the whole entry (a stored one, or a runtime
    conf with its ``id``) and whose it is: a server's shared entry is
    ``shared`` (the member pays nothing; the allowance meters it), a
    sign-in a ``subscription``, else the entry's own override, else its
    service preset's (the coding plans and OpenCode Go a subscription,
    Ollama ``local``), else ``api``, per token."""
    if is_server_id(entry.get("id")):
        return "shared"
    if is_oauth_protocol(entry.get("protocol")):
        return "subscription"
    if entry.get("billing") in BILLING_OVERRIDES[1:]:
        return entry["billing"]
    service = ai_protocols.service_of({"protocol": entry.get("protocol"),
                                       "base_url": entry.get("base_url") or ""})
    return (service or {}).get("billing") or "api"


def service_id(entry: dict) -> str:
    """Which service a connection is, the ``service`` the browser picks its
    logo by (on the masked entry and on each registry row): the preset its
    protocol and base URL make (``service_of``: ``deepseek``,
    ``kimi-code``), else its protocol while it points at the protocol's own
    endpoint (``openai``, ``anthropic``, a ``chatgpt`` sign-in), else
    ``custom``, an entry made from a preset since dropped included.
    ``entry`` needs its protocol and base URL."""
    protocol = entry.get("protocol") or ""
    base = (entry.get("base_url") or "").strip().rstrip("/")
    service = ai_protocols.service_of({"protocol": protocol, "base_url": base})
    if service:
        return service["id"]
    proto = ai_protocols.PROTOCOLS.get(protocol)
    return protocol if proto and base in ("", proto.base_url.rstrip("/")) else "custom"


def protocol_choices() -> dict:
    """What the settings form offers: the protocols (auth "oauth" = sign-in
    entries, no API key field) and the named services."""
    protocols = [{"id": pid, "label": proto.label, "default_base_url": proto.base_url, "auth": proto.auth,
                  "key_placeholder": proto.key_placeholder, "key_url": proto.key_url, "note": proto.note,
                  "signin_fields": list(proto.signin_fields)}
                 for pid, proto in ai_protocols.PROTOCOLS.items()]
    # Each preset with its optional fields spelled out: how it is paid for,
    # its quota reader and whether one reads it (``quota``, as on a saved
    # entry), whether a key may be left out, and whether its base URL
    # already names its version.
    services = [{**s, "billing": s.get("billing") or "api", "usage": s.get("usage") or "",
                 "quota": ai_protocols.quota_reader(s) is not None,
                 "key_optional": bool(s.get("key_optional")), "versioned": bool(s.get("versioned"))}
                for s in ai_protocols.SERVICES]
    return {"protocols": protocols, "services": services}


# --- the server's shared entries ----------------------------------------------

SERVER_AI_KEY = "ai_providers"  # the users.db `settings` KV key
SERVER_ID_PREFIX = ai_usage.SHARED_PREFIX  # "server:"
# The shared allowance's ceiling (tokens per account per 24 h): far beyond
# any real day, low enough to stay a sane integer everywhere.
ALLOWANCE_MAX = 1_000_000_000
_server_lock = threading.Lock()

# Who may use a shared entry, its ``access``: {"who": "everyone" |
# "selected", "users": [account ids], "guests": bool}. "everyone" is every
# account, the guest accounts too when ``guests`` is on; "selected" is the
# listed accounts alone (a guest is never one, and the list is kept only
# under "selected"). An administrator always has access: the Server
# section tests, lists the models of and asks usage of every shared entry
# through the admin's own runtime.
ACCESS_WHO = ("everyone", "selected")
MAX_ACCESS_USERS = 500


def default_access(guests: bool = False) -> dict:
    return {"who": "everyone", "users": [], "guests": bool(guests)}


def _clean_access(value, legacy_guests: bool = False) -> dict:
    """A stored access object as a clean one. An entry without one was
    written when access was one server-wide guest switch: it takes that
    switch's value, so an upgrade changes nobody's access."""
    if not isinstance(value, dict):
        return default_access(legacy_guests)
    who = value.get("who") if value.get("who") in ACCESS_WHO else "everyone"
    users = value.get("users") if isinstance(value.get("users"), list) else []
    users = [u for u in users if isinstance(u, str) and u][:MAX_ACCESS_USERS] if who == "selected" else []
    return {"who": who, "users": users, "guests": who == "everyone" and value.get("guests") is True}


def is_server_id(provider_id) -> bool:
    return str(provider_id or "").startswith(SERVER_ID_PREFIX)


def own_entries(user_id: str) -> list:
    """The account's own entries (a ``server:`` id is never one of them)."""
    return [e for e in load_provider_entries(user_id) if not is_server_id(e.get("id"))] if user_id else []


def new_server_provider_id() -> str:
    return SERVER_ID_PREFIX + new_provider_id()


def _allowance_limit(value) -> int:
    """A stored allowance number as a clean limit (anything odd reads as 0 =
    unlimited — never as a lock-out)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return min(max(value, 0), ALLOWANCE_MAX)


def load_server_ai() -> dict:
    """The shared entries with their keys decrypted, each with its
    ``access``, and the allowance: {"providers": [...], "allowance":
    {"accounts": int, "guests": int}} (tokens per account per 24 h, 0 =
    unlimited). A key or a sign-in that no longer decrypts (the data
    directory's key changed) reads as none, with a warning."""
    try:
        value = json.loads(_get_raw(SERVER_AI_KEY) or "{}")
    except ValueError:
        value = {}
    if not isinstance(value, dict):
        value = {}
    entries = value.get("providers") if isinstance(value.get("providers"), list) else []
    # Written before access was per entry: the one guest switch of that time.
    legacy_guests = value.get("guests") is True
    out = []
    for e in entries:
        if not isinstance(e, dict) or not is_server_id(e.get("id")):
            continue
        e = dict(e)
        e["access"] = _clean_access(e.get("access"), legacy_guests)
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
    return {"providers": out,
            "allowance": {k: _allowance_limit(allowance.get(k)) for k in ("accounts", "guests")}}


def _sealed(entry: dict) -> dict:
    """A shared entry as stored: the key and the sign-in's tokens encrypted,
    the access a clean object."""
    out = {**entry, "api_key": cipher().encrypt(entry["api_key"].encode("utf-8")).decode("ascii")
           if entry.get("api_key") else "", "access": _clean_access(entry.get("access"))}
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
        "providers": providers,
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


def validated_access(value) -> dict:
    """The access part of an admin request — {"who": "everyone" |
    "selected", "users"?: [usernames], "guests"?: bool} — checked and in
    its stored shape (the usernames as account ids). 400 on anything else,
    a username that is no account's or a guest's included. Under
    "everyone" the list is dropped, under "selected" the guest flag."""
    if not isinstance(value, dict) or set(value) - {"who", "users", "guests"}:
        raise HTTPException(status_code=400, detail='access takes "who", "users" and "guests"')
    who = value.get("who", "everyone")
    if who not in ACCESS_WHO:
        raise HTTPException(status_code=400, detail='access.who is "everyone" or "selected"')
    guests = value.get("guests", False)
    if not isinstance(guests, bool):
        raise HTTPException(status_code=400, detail="access.guests is true or false")
    names = value.get("users", [])
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise HTTPException(status_code=400, detail="access.users is a list of usernames")
    if who == "everyone":
        return default_access(guests)
    names = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    if len(names) > MAX_ACCESS_USERS:
        raise HTTPException(status_code=400, detail=f"at most {MAX_ACCESS_USERS} accounts can be named")
    ids = []
    with connect_users_db() as conn:
        for name in names:
            row = conn.execute("SELECT id, is_guest FROM users WHERE username = ?", (name,)).fetchone()
            if not row:
                raise HTTPException(status_code=400, detail=f"no account is named {name}")
            if row[1]:
                raise HTTPException(status_code=400,
                                    detail=f"{name} is a guest account — guests are admitted by the guest switch")
            ids.append(row[0])
    return {"who": "selected", "users": ids, "guests": False}


def access_view(entry: dict, names: dict) -> dict:
    """An entry's access as the admin's form shows it: the listed accounts
    by username (``names`` = db.account_names of them; a deleted account is
    left out)."""
    access = _clean_access(entry.get("access"))
    return {**access, "users": [names[u] for u in access["users"] if u in names]}


def edit_server_ai(change) -> dict:
    """Read-modify-write the shared config under one lock: ``change(config)``
    mutates it in place (and may raise to abort). Returns the saved config."""
    with _server_lock:
        config = load_server_ai()
        change(config)
        save_server_ai(config)
        return config


def shared_access(user_id: str) -> tuple[list, int]:
    """The shared entries the account ``user_id`` (an id) may use, by each
    entry's access — every account under "everyone", a guest account only
    where ``guests`` is on, a listed account under "selected", an
    administrator always, an id that is no account never — and the
    allowance limit that applies to it (the guests' or the accounts', by
    the users row's ``is_guest``; 0 = unlimited)."""
    if not user_id:
        return [], 0
    config = load_server_ai()
    if not config["providers"]:
        return [], 0
    try:
        with connect_users_db() as conn:
            row = conn.execute("SELECT is_guest, is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return [], 0
    is_guest, is_admin = bool(row[0]), bool(row[1])
    limit = config["allowance"]["guests" if is_guest else "accounts"]
    if is_admin and not is_guest:
        return config["providers"], limit

    def admitted(entry: dict) -> bool:
        access = entry["access"]
        if access["who"] == "selected":
            return not is_guest and user_id in access["users"]
        return access["guests"] if is_guest else True
    return [e for e in config["providers"] if admitted(e)], limit


def _has_credential(entry: dict) -> bool:
    """A sign-in's token, a key, or none needed (key_optional)."""
    if is_oauth_protocol(entry.get("protocol")):
        oauth = entry.get("oauth")
        return isinstance(oauth, dict) and bool(oauth.get("access_token"))
    return bool((entry.get("api_key") or "").strip()) or key_optional(entry)


def server_entries_for(user_id: str) -> list:
    """The shared entries ``user_id`` may use (``shared_access``)."""
    return shared_access(user_id)[0]


def allowance_status(user_id: str, limit: int) -> dict:
    """What the account card, the pickers and the Usage pane show of the
    shared allowance: {"limit" (0 = unlimited), "used" (tokens through
    shared entries in the last 24 h), "exhausted"}."""
    used = ai_usage.shared_used(user_id)
    return {"limit": limit, "used": used, "exhausted": bool(limit) and used >= limit}


def shared_allowance(user_id: str) -> dict | None:
    """``allowance_status`` when a shared entry applies to ``user_id`` (one it
    can use: a key, or a connected sign-in; limit 0 when the admin set
    none), else None — the object ai_runtime() reports, without building
    the runtime."""
    entries, limit = shared_access(user_id)
    usable = any(ai_protocols.PROTOCOLS.get(e.get("protocol")) and _has_credential(e) for e in entries)
    return allowance_status(user_id, limit) if usable else None


def provider_label(entry: dict) -> str:
    """An entry's display name: its own, else the named service its endpoint
    is, else its protocol's label."""
    name = (entry.get("name") or "").strip()
    if name:
        return name
    service = ai_protocols.service_of(entry)
    if service:
        return service["label"]
    protocol = entry.get("protocol")
    proto = ai_protocols.PROTOCOLS.get(protocol)
    return proto.label if proto else protocol or ""


def entry_base_url(entry: dict) -> str:
    """Where an entry's calls go: its own base URL, else its protocol's
    default endpoint ("" for an unknown protocol), with no trailing slash."""
    proto = ai_protocols.PROTOCOLS.get(entry.get("protocol"))
    return ((entry.get("base_url") or "").strip() or (proto.base_url if proto else "")).rstrip("/")


def entry_models(entry: dict) -> list:
    """The entry's model names. None picked = none offered: there is no
    built-in default (it would go stale); the settings form lists the
    provider's live models to pick from."""
    return [m.strip() for m in (entry.get("models") or "").split(",") if m.strip()]


# --- whether a model calls tools on its connection ----------------------------
# A model's first chat with tools checks whether it calls them on this
# connection (routers/ai.py _checked_tools, docs/dev/ai.md "Models that
# can't call tools"). The verdict is kept on the entry as ``tool_checks``:
# {model: {"ok": bool, "reason": the provider's words or the model's reply
# when not, "at": stamp}}. An account's own entry keeps it in its
# ai-settings pref, a shared entry in the server's config, so one check
# covers every account the entry admits. A new key, base URL or protocol
# drops an entry's verdicts, a models edit keeps the picked models' only.

def tool_checks(entry: dict) -> dict:
    """The stored verdicts of the entry's picked models, each as {ok, reason,
    at} with a string reason and stamp even when the stored record left them
    out (a hand edit, a restored config)."""
    stored = entry.get("tool_checks") if isinstance(entry.get("tool_checks"), dict) else {}
    picked = entry_models(entry)
    return {m: {"ok": v["ok"], "reason": "" if v["ok"] else str(v.get("reason") or ""),
                "at": str(v.get("at") or "")}
            for m, v in stored.items()
            if m in picked and isinstance(v, dict) and isinstance(v.get("ok"), bool)}


def _prune_tool_checks(entry: dict) -> None:
    if "tool_checks" in entry:
        entry["tool_checks"] = tool_checks(entry)
        if not entry["tool_checks"]:
            entry.pop("tool_checks")


def _stored_entries(user_id: str, provider_id: str) -> list:
    return load_server_ai()["providers"] if is_server_id(provider_id) else load_provider_entries(user_id)


def stored_tool_check(user_id: str, provider_id: str, model: str) -> dict | None:
    """The verdict stored for ``model`` on the entry ``provider_id`` (the
    account's own, or a ``server:`` one), read fresh; None when it has none."""
    entry = next((e for e in _stored_entries(user_id, provider_id) if e.get("id") == provider_id), None)
    return tool_checks(entry).get(model) if entry else None


def _same_connection(entry: dict, conf: dict) -> bool:
    """Whether a stored entry still is the connection the runtime conf
    ``conf`` was built from: the same protocol, base URL and key (a
    sign-in's token changes as it is renewed, so it is not compared)."""
    proto = ai_protocols.PROTOCOLS.get(entry.get("protocol"))
    if not proto or entry.get("protocol") != conf.get("protocol"):
        return False
    if entry_base_url(entry) != conf.get("base_url"):
        return False
    return proto.auth == "oauth" or (entry.get("api_key") or "").strip() == conf.get("api_key")


def _edit_entries(user_id: str, provider_id: str, change) -> None:
    """``change(entries)`` on the list that holds ``provider_id``, in one
    transaction (the account's pref) or under the shared config's lock."""
    if is_server_id(provider_id):
        edit_server_ai(lambda config: change(config["providers"]))
    else:
        update_provider_entries(user_id, change)


def save_tool_check(user_id: str, provider_id: str, model: str, verdict: dict, conf: dict) -> dict | None:
    """Store a check's ``verdict`` ({ok, reason?}) for ``model`` on the entry
    ``provider_id``, while the entry still picks the model and still is the
    connection ``conf`` (the runtime conf the check went through) names: an
    edit made during the check wins. Returns the stored verdict, None when
    nothing was stored."""
    record = {"ok": bool(verdict.get("ok")), "reason": "" if verdict.get("ok")
              else str(verdict.get("reason") or "")[:MAX_TOOL_REASON], "at": page_now()}
    stored = []

    def put(entries):
        entry = next((e for e in entries if e.get("id") == provider_id), None)
        if entry and model in entry_models(entry) and _same_connection(entry, conf):
            entry["tool_checks"] = {**tool_checks(entry), model: record}
            stored.append(record)
    _edit_entries(user_id, provider_id, put)
    return stored[0] if stored else None


def forget_tool_check(user_id: str, provider_id: str, model: str) -> bool:
    """Forget that ``model`` calls tools on the entry ``provider_id`` (it
    refused them since), so its next chat with tools checks again. Writes
    nothing unless a passing verdict is stored; True when one was."""
    stored = stored_tool_check(user_id, provider_id, model)
    if not stored or not stored["ok"]:
        return False

    def drop(entries):
        entry = next((e for e in entries if e.get("id") == provider_id), None)
        checks = entry.get("tool_checks") if entry else None
        if isinstance(checks, dict) and (checks.get(model) or {}).get("ok") is True:
            checks.pop(model)
            _prune_tool_checks(entry)
    _edit_entries(user_id, provider_id, drop)
    return True


# A refused sign-in token refresh isn't retried for this long: ai_runtime
# runs on every AI request, and retrying a dead grant each time would add a
# full round trip to the identity provider to chat/metadata/model calls.
REFRESH_BACKOFF_S = 300
# One that got no verdict (RefreshUnavailable: no connection, a 5xx) is
# retried sooner, since the grant is likely fine. Not much sooner: each try
# may hold a request for the token request's 30 s timeout.
REFRESH_RETRY_S = 60


def _backed_off(oauth: dict) -> bool:
    """Whether the sign-in's last refresh failed too recently to try again."""
    window = REFRESH_RETRY_S if oauth.get("refresh_unreachable") else REFRESH_BACKOFF_S
    return time.time() - (oauth.get("refresh_failed_at") or 0) <= window


# One refresh at a time per sign-in: per account for its own entries, per
# entry for a shared one (every account's requests refresh the same tokens).
# OpenAI rotates refresh tokens, so of two concurrent refreshes (the
# translator fires dozens of requests at once) the second fails — and its
# save must not overwrite the first one's fresh tokens.
_refresh_locks: dict = {}
_refresh_locks_guard = threading.Lock()


def _refresh_lock(key) -> threading.Lock:
    """``key``: an account id, or ``("server", <provider id>)`` for a
    shared sign-in (a tuple never equals an id)."""
    with _refresh_locks_guard:
        return _refresh_locks.setdefault(key, threading.Lock())


def _refresh_tokens(lock_key, read, write, flow, rejected: str = "") -> dict | None:
    """Refresh one sign-in's tokens through its protocol's OAuth ``flow``
    under ``lock_key``'s lock. ``read()`` gives the stored oauth dict fresh
    (so a refresh another request just did is reused, not repeated),
    ``write(oauth, expected)`` stores the result while the stored refresh
    token is still ``expected`` and returns the stored dict. ``rejected``:
    an access token the provider answered 401 to, refreshed although it
    does not look expired, unless the stored one is another by now or a
    refresh got it less than REFRESH_BACKOFF_S ago (``refreshed_at``): a
    provider refusing every token then costs one refresh per window, not
    one per check. Returns the current oauth dict."""
    with _refresh_lock(lock_key):
        oauth = read()
        if not oauth or not oauth.get("access_token"):
            return None
        if rejected:
            due = (oauth["access_token"] == rejected
                   and time.time() - (oauth.get("refreshed_at") or 0) > REFRESH_BACKOFF_S)
        else:
            due = flow.needs_refresh(oauth)
        if not due or _backed_off(oauth):
            return oauth
        expected = oauth.get("refresh_token")
        try:
            refreshed = flow.refresh(oauth)
        except RefreshUnavailable:
            # No verdict on the grant: keep the tokens, try again soon.
            return write({**oauth, "refresh_failed_at": int(time.time()), "refresh_unreachable": True}, expected)
        if not refreshed:
            # Refused: keep the stale token, so the call fails with a clear
            # upstream 401 and someone signs in again in Settings.
            oauth = {k: v for k, v in oauth.items() if k != "refresh_unreachable"}
            return write({**oauth, "refresh_failed_at": int(time.time())}, expected)
        refreshed = {**refreshed, "refreshed_at": int(time.time())}
        # OpenAI has spent the old refresh token: a lost save loses the grant.
        for attempt in range(3):
            try:
                return write(refreshed, expected)
            except sqlite3.OperationalError as e:
                if attempt == 2:
                    log.error(f"[ai] a refreshed sign-in could not be saved ({e}): the stored refresh "
                              "token is spent, so the sign-in will have to be made again")
        return refreshed


def _entry_oauth(entries: list, provider_id: str) -> dict | None:
    e = next((x for x in entries if x.get("id") == provider_id), None)
    return e.get("oauth") if e and isinstance(e.get("oauth"), dict) else None


def _swap_oauth(entries: list, provider_id: str, oauth: dict, expected) -> None:
    """Put ``oauth`` on the entry while its stored refresh token is still
    ``expected``, the one the refresh started from: a refresh, failed or
    late, never overwrites a sign-in made again meanwhile."""
    stored = _entry_oauth(entries, provider_id)
    if stored is not None and stored.get("refresh_token") == expected:
        next(e for e in entries if e.get("id") == provider_id)["oauth"] = oauth


def _refreshed_oauth(user_id: str, provider_id: str, flow, rejected: str = "") -> dict | None:
    """An account's own sign-in entry, refreshed under the account's lock
    and written back as only its tokens (an edit of the list meanwhile —
    another tab adding a key — stays)."""
    def write(oauth, expected):
        return _entry_oauth(update_provider_entries(
            user_id, lambda entries: _swap_oauth(entries, provider_id, oauth, expected)), provider_id)
    return _refresh_tokens(user_id, lambda: _entry_oauth(load_provider_entries(user_id), provider_id),
                           write, flow, rejected)


def _refreshed_server_oauth(provider_id: str, flow, rejected: str = "") -> dict | None:
    """A shared sign-in entry, refreshed under the entry's own lock and
    written back to the server's config (only its tokens: an admin's edit
    of the other fields meanwhile stays)."""
    def write(oauth, expected):
        return _entry_oauth(edit_server_ai(
            lambda config: _swap_oauth(config["providers"], provider_id, oauth, expected))["providers"], provider_id)
    return _refresh_tokens(("server", provider_id),
                           lambda: _entry_oauth(load_server_ai()["providers"], provider_id), write, flow, rejected)


def _registry(user_id: str, refresh: bool = True) -> tuple[dict, list, int]:
    """``(providers, models, limit)``: the connections the account
    ``user_id`` (an id) may use — its own entries followed by the server's
    shared ones (``shared_access``) — as runtime confs by entry id, the
    model registry they offer, and the shared allowance's limit. A sign-in
    due for renewal is renewed here unless ``refresh`` is off (the notices,
    which only need to know what is offered: the set is the same either
    way, a stale token included)."""
    shared, limit = shared_access(user_id)
    providers, models, seen = {}, [], set()
    for e in own_entries(user_id) + shared:
        protocol = e.get("protocol")
        proto = ai_protocols.PROTOCOLS.get(protocol)
        pid = str(e.get("id") or "")
        if not proto or not pid or pid in providers:
            continue
        name = provider_label(e)
        conf = {
            "base_url": entry_base_url(e),
            "protocol": protocol,
            "name": name,
            # How its calls are paid for: the recorder and the masked entry read it.
            "billing": billing_of(e),
        }
        service = service_id(e)
        if proto.auth == "oauth":
            # Sign-in entry: the bearer token comes from the sign-in and is
            # refreshed lazily here (persisted so other requests reuse it).
            oauth = e.get("oauth") if isinstance(e.get("oauth"), dict) else None
            if not oauth or not oauth.get("access_token"):
                continue
            if refresh and proto.oauth.needs_refresh(oauth) and not _backed_off(oauth):
                refreshed = (_refreshed_server_oauth(pid, proto.oauth) if is_server_id(pid)
                             else _refreshed_oauth(user_id, pid, proto.oauth))
                oauth = refreshed or oauth
            conf["api_key"] = oauth["access_token"]
            conf["account_id"] = oauth.get("account_id") or ""
            if oauth.get("refresh_unreachable") and proto.oauth.needs_refresh(oauth):
                # Due for renewal, and the identity provider could not be
                # reached: a 401 then says so, not "sign in again"
                # (ai_client.open_ai). The token may still work meanwhile.
                conf["refresh_unreachable"] = True
        else:
            key = (e.get("api_key") or "").strip()
            if not key and not key_optional(e):
                continue
            conf["api_key"] = key
        providers[pid] = conf
        checks = tool_checks(e)
        for model in entry_models(e):
            mid = f"{pid}:{model}"
            if mid in seen:
                continue
            seen.add(mid)
            check = checks.get(model)
            models.append({"id": mid, "provider": pid, "provider_name": name, "model": model,
                           # Whether the provider takes the PDF file itself
                           # (native document part); if not, the chat sends
                           # extracted text.
                           "native_pdf": proto.native_pdf,
                           "shared": is_server_id(pid),
                           "billing": conf["billing"],
                           # Which service its connection is, for its logo.
                           "service": service,
                           # Whether it calls tools here: True, False (its
                           # chats go without them, for ``tools_reason``),
                           # None = not checked yet.
                           "tools": check["ok"] if check else None,
                           **({"tools_reason": check["reason"]} if check and not check["ok"] else {})})
    return providers, models, limit


def ai_runtime(user_id: str) -> dict:
    """The effective AI config for a request, built from the provider entries
    of the account ``user_id`` (an id) followed by the server's shared ones
    (``_registry``):
    {"providers": {id: {api_key, base_url, protocol, name, billing}},
    "models": [{"id": "<pid>:<model>", "provider": pid, "provider_name",
    "model", "native_pdf", "shared", "billing", "service", "tools",
    "tools_reason"?}],
    "roles": {role: {pick, model, state}} (ai_roles.resolve_roles), "default": the
    chat role's model entry (the account's chat pick, else the first model
    — the account's own when it has one, else the server's) or None,
    "favorites", "chat_migrated", "enabled": bool, "allowance": {"limit",
    "used", "exhausted"} or None}.

    ``allowance`` is None unless a shared entry made it into the runtime
    (limit 0 = unlimited). Under a limit every shared provider conf carries
    ``"allowance": {"user", "limit"}``, which ai_client.open_ai checks
    before each call. The shared models stay listed once it is used up; a
    refused call says why."""
    providers, models, limit = _registry(user_id)
    allowance = None
    shared_ids = [pid for pid in providers if is_server_id(pid)]
    if shared_ids:
        # Reported whenever a shared entry applies (limit 0 = unlimited);
        # the transport only meters under a limit.
        allowance = allowance_status(user_id, limit)
        if limit:
            for pid in shared_ids:
                providers[pid]["allowance"] = {"user": user_id, "limit": limit}
    prefs = load_model_prefs(user_id)
    roles = resolve_roles(models, prefs, translate_engines.configured(user_id))
    return {
        "user": user_id,  # whose config this is — the usage recorder's key
        "providers": providers,
        "models": models,
        "roles": roles,
        "default": next((m for m in models if m["id"] == roles["chat"]["model"]), None),
        "favorites": prefs["favorites"],
        "chat_migrated": prefs["chat_migrated"],
        "enabled": bool(models),
        "allowance": allowance,
    }


def model_roles(user_id: str) -> dict:
    """``resolve_roles`` of the account without renewing any sign-in or
    reading the allowance: what the notices check on every poll."""
    _, models, _ = _registry(user_id, refresh=False)
    return resolve_roles(models, load_model_prefs(user_id), translate_engines.configured(user_id))


def missing_picks(user_id: str, rt: dict) -> dict:
    """``{<registry id>: {model, connection}}`` for each id a task or a
    favorite of ``rt`` (an ai_runtime) names that it no longer offers, so
    the client names a pick without reading the id: the model's name, and
    the name of its connection while that entry still exists (its model
    unpicked, its sign-in gone, a shared entry that no longer admits the
    account), else "". A translation service picked is no registry id: the
    client words it from ``translate_engines``."""
    offered = {m["id"] for m in rt["models"]}
    named = [r["pick"] for r in rt["roles"].values()] + list(rt["favorites"])
    gone = [p for p in dict.fromkeys(named) if p and p not in offered and not translate_engines.engine_of(p)]
    if not gone:
        return {}
    entries = own_entries(user_id) + load_server_ai()["providers"]
    out = {}
    for pick in gone:
        entry = next((e for e in entries if e.get("id") and pick.startswith(f"{e['id']}:")), None)
        out[pick] = ({"model": pick[len(entry["id"]) + 1:], "connection": provider_label(entry)} if entry
                     else {"model": pick_label(pick), "connection": ""})
    return out


def clear_refresh_backoff(user_id: str, provider_id: str) -> None:
    """Forget a sign-in entry's failed refresh so the next ai_runtime()
    re-attempts the token refresh immediately (an explicit retry, e.g. the
    settings Test button). A ``server:<id>`` names a shared entry; the
    caller has checked the account may touch it."""
    def forget(entries):
        oauth = _entry_oauth(entries, provider_id)
        if oauth:
            oauth.pop("refresh_failed_at", None)
            oauth.pop("refresh_unreachable", None)

    if is_server_id(provider_id):
        with _refresh_lock(("server", provider_id)):
            oauth = _entry_oauth(load_server_ai()["providers"], provider_id)
            if oauth and "refresh_failed_at" in oauth:
                edit_server_ai(lambda config: forget(config["providers"]))
        return
    with _refresh_lock(user_id):
        oauth = _entry_oauth(load_provider_entries(user_id), provider_id)
        if oauth and "refresh_failed_at" in oauth:
            update_provider_entries(user_id, forget)


def renew_rejected(user_id: str, provider_id: str, rejected: str) -> str:
    """Renew a sign-in whose access token ``rejected`` the provider answered
    401 to although it did not look expired (a clock behind, a token
    revoked early). Under the refresh lock and within the backoff, like
    ai_runtime's renewal: of the requests the token was refused to, one
    refreshes and the others get its result. Returns the access token to
    try again with, "" when there is none (the grant was refused, a
    refusal is still backed off, or a refresh got the refused token only
    just now); raises RefreshUnavailable while the identity provider can't
    be reached. A shared entry's backoff is never reset here: that is an
    admin's."""
    shared = is_server_id(provider_id)
    entries = load_server_ai()["providers"] if shared else load_provider_entries(user_id)
    proto = ai_protocols.PROTOCOLS.get(next((e.get("protocol") for e in entries if e.get("id") == provider_id), None))
    if not rejected or not proto or proto.auth != "oauth":
        return ""
    oauth = (_refreshed_server_oauth(provider_id, proto.oauth, rejected) if shared
             else _refreshed_oauth(user_id, provider_id, proto.oauth, rejected)) or {}
    token = oauth.get("access_token") or ""
    if token and token != rejected:
        return token
    if oauth.get("refresh_unreachable"):
        raise RefreshUnavailable("the identity provider could not be reached to renew the sign-in")
    return ""


NOT_CONFIGURED = "AI not configured (add a connection and pick its models in Settings → AI → Models)"


def require_ai_runtime(user_id: str) -> dict:
    """ai_runtime(), raising the standard 503 when no provider is usable."""
    return require_enabled(ai_runtime(user_id))


def require_enabled(rt: dict) -> dict:
    """``rt``, or the standard 503 when it offers no model."""
    if not rt["enabled"]:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
    return rt


def transcriber_of(rt: dict, model_hint: str = "") -> str:
    """The id of the connection of ``rt`` (an ai_runtime) speech-to-text
    goes through, "" when none can take it. It needs an OpenAI credential,
    not chat models: an entry with none picked still transcribes. The entry
    that surely transcribes (Protocol.transcription: OpenAI itself) comes
    before one that may (a compatible server), the entry of ``model_hint``
    (a model-registry id, the chat's model) first within each. Reads ``rt``
    alone: no network."""
    # (A shared entry's ids have a colon of their own: "server:<id>:<model>".)
    hinted = next((m["provider"] for m in rt["models"] if m["id"] == model_hint),
                  (model_hint or "").split(":", 1)[0])
    ids = ([hinted] if hinted in rt["providers"] else []) + list(rt["providers"])
    candidates = [(ai_protocols.of(rt["providers"][pid]).transcription(rt["providers"][pid]), pid) for pid in ids]
    best = max((rank for rank, _ in candidates), default=0)
    return next((pid for rank, pid in candidates if rank == best), "") if best else ""


def transcriber_for(user_id: str, model_hint: str = "") -> tuple:
    """The connection the account's speech-to-text goes through, ``(protocol
    adapter, conf)`` (transcriber_of): dictation (``/ai/transcribe``) and a
    recording's transcript (gamma/recording.py); the standard 503 when none
    can take it."""
    rt = ai_runtime(user_id)
    pid = transcriber_of(rt, model_hint)
    if not pid:
        raise HTTPException(status_code=503,
                            detail="Voice input needs an OpenAI API key (Settings → AI → Models) — "
                                   "Anthropic and sign-in connections don't offer transcription.")
    conf = rt["providers"][pid]
    return ai_protocols.of(conf), conf
