"""Per-user AI provider entries (GUI-managed API keys).

Users manage a LIST of provider entries (Settings → AI → Connections), each:
  {"id", "name", "protocol": a key of config.AI_PROTOCOLS, "api_key" (or
   "oauth" tokens for a sign-in protocol), "base_url": "" = protocol default,
   "models": "a, b" = comma list ("" = none offered yet), "test_model",
   "created_at"}

Entries live in users.db `user_prefs` under the account-wide reserved
`ai-settings` key, which the generic /api/prefs endpoints refuse to serve: the
only read path is the masked GET /api/ai/settings (last 4 characters, never
the key itself). There is no env/server-wide key.
"""

import secrets
import threading
import time

from fastapi import HTTPException

from . import chatgpt_oauth
from .config import AI_PROTOCOLS, AI_SERVICES
from .db import get_pref, set_pref

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


def provider_label(entry: dict) -> str:
    """An entry's display name: its own, else the named service its endpoint
    is, else its protocol's label."""
    name = (entry.get("name") or "").strip()
    if name:
        return name
    protocol = entry.get("protocol")
    base = (entry.get("base_url") or "").strip().rstrip("/")
    service = next((s for s in AI_SERVICES
                    if s["protocol"] == protocol and s["base_url"] == base), None)
    if service:
        return service["label"]
    return AI_PROTOCOLS.get(protocol, {}).get("label") or protocol or ""


def entry_models(entry: dict) -> list:
    """The entry's model names. None picked = none offered: there is no
    built-in default (it would go stale); the settings form lists the
    provider's live models to pick from."""
    return [m.strip() for m in (entry.get("models") or "").split(",") if m.strip()]


# A failed ChatGPT token refresh isn't retried for this long: ai_runtime runs
# on every AI request, and retrying a dead grant each time would add a full
# auth.openai.com round trip to chat/metadata/model calls.
REFRESH_BACKOFF_S = 300

# One refresh at a time per account. OpenAI rotates refresh tokens, so of two
# concurrent refreshes (the translator fires dozens of requests at once) the
# second fails — and its save must not overwrite the first one's fresh tokens.
_refresh_locks: dict = {}
_refresh_locks_guard = threading.Lock()


def _refresh_lock(user: str) -> threading.Lock:
    with _refresh_locks_guard:
        return _refresh_locks.setdefault(user, threading.Lock())


def _refreshed_oauth(user: str, provider_id: str) -> dict | None:
    """Refresh one ChatGPT entry's tokens under the account's lock, reading
    the entries fresh so a refresh another request just did is reused, not
    repeated. Returns the entry's current oauth dict."""
    with _refresh_lock(user):
        entries = load_provider_entries(user)
        e = next((x for x in entries if x.get("id") == provider_id), None)
        oauth = e.get("oauth") if e and isinstance(e.get("oauth"), dict) else None
        if not oauth or not oauth.get("access_token"):
            return None
        failed_at = oauth.get("refresh_failed_at") or 0
        if not chatgpt_oauth.needs_refresh(oauth) or time.time() - failed_at <= REFRESH_BACKOFF_S:
            return oauth
        refreshed = chatgpt_oauth.refresh(oauth)
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
    entries: {"providers": {id: {api_key, base_url, protocol, name}},
    "models": [{"id": "<pid>:<model>", "provider": pid, "provider_name",
    "model"}], "default": first model or None, "enabled": bool}."""
    entries = load_provider_entries(user) if user else []
    providers, models = {}, []
    for e in entries:
        protocol = e.get("protocol")
        pid = str(e.get("id") or "")
        if protocol not in AI_PROTOCOLS or not pid or pid in providers:
            continue
        name = provider_label(e)
        conf = {
            "base_url": ((e.get("base_url") or "").strip() or AI_PROTOCOLS[protocol]["base_url"]).rstrip("/"),
            "protocol": protocol,
            "name": name,
        }
        if protocol == "chatgpt":
            # OAuth entry: the bearer token comes from the ChatGPT sign-in and
            # is refreshed lazily here (persisted so other requests reuse it).
            oauth = e.get("oauth") if isinstance(e.get("oauth"), dict) else None
            if not oauth or not oauth.get("access_token"):
                continue
            failed_at = oauth.get("refresh_failed_at") or 0
            if chatgpt_oauth.needs_refresh(oauth) and time.time() - failed_at > REFRESH_BACKOFF_S:
                oauth = _refreshed_oauth(user, pid) or oauth
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
                               # (native document part). The ChatGPT sign-in wire
                               # is the Codex backend, which refuses input_file
                               # parts — the chat falls back to extracted text.
                               "native_pdf": protocol != "chatgpt"})
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
