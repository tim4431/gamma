"""Account web-search choices and write-only, encrypted service credentials.

The reserved account preference is served only through the masked settings
API. OpenAI connection reuse is limited to the account's own official API
entries; ChatGPT sign-ins, gateways and shared allowance keys never qualify.
"""

import json
import os
import re
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import InvalidToken
from fastapi import HTTPException

from . import ai_protocols, ai_settings
from .db import get_pref, update_pref
from .publisher_sessions import cipher

SEARCH_PREF_KEY = "web-search-settings"
OPENAI_URL = "https://api.openai.com/v1/responses"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_MODEL = "gpt-4.1-mini"
PROVIDERS = ("auto", "openai", "brave", "searxng", "off")
DEFAULTS = {"provider": "auto", "openai_model": DEFAULT_MODEL,
            "openai_connection": "", "searxng_url": ""}
KEY_FIELDS = ("openai_api_key", "brave_api_key")


class SearchConfigurationError(ValueError):
    """A safe settings diagnostic, without credential values."""

    def __init__(self, message: str, code: str = "configuration"):
        super().__init__(message)
        self.code = code


def _stored(user: str) -> dict | None:
    value, stamp = get_pref(user, SEARCH_PREF_KEY) if user else (None, "")
    return (value if isinstance(value, dict) else {}) if stamp else None


def _official_openai(base: str) -> bool:
    try:
        parsed = urlsplit(base)
        return (parsed.scheme == "https" and parsed.hostname == "api.openai.com"
                and parsed.port in (None, 443) and parsed.username is None and parsed.password is None
                and parsed.path.rstrip("/") in ("", "/v1") and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def _connections(user: str) -> list[dict]:
    default = ai_protocols.PROTOCOLS["openai"].base_url
    return [entry for entry in ai_settings.own_entries(user)
            if entry.get("protocol") == "openai" and entry.get("id")
            and isinstance(entry.get("api_key"), str) and entry["api_key"].strip()
            and _official_openai((entry.get("base_url") or default).strip())]


def _key(user: str, saved: dict, field: str) -> str:
    sealed = saved.get(field)
    if not sealed:
        return ""
    try:
        value = json.loads(cipher().decrypt(sealed.encode("ascii")).decode("utf-8"))
        if (not isinstance(value, dict) or value.get("user") != user or value.get("field") != field
                or not isinstance(value.get("key"), str)):
            raise ValueError("credential binding mismatch")
        return value["key"]
    except (InvalidToken, ValueError, AttributeError, OSError):
        raise SearchConfigurationError(
            "A saved web search key cannot be read. Replace or clear it in Settings → AI → Connections.") from None


def _valid_key(value: str) -> bool:
    return len(value) <= 512 and value.isascii() and not re.search(r"[\x00-\x20\x7f]", value)


def legacy_environment() -> tuple[str, str, str]:
    """Legacy provider selection and raw credentials, before caller validation."""
    provider = os.environ.get("GAMMA_WEB_SEARCH_PROVIDER", "").strip().lower()
    key = os.environ.get("GAMMA_BRAVE_SEARCH_API_KEY", "").strip()
    url = os.environ.get("GAMMA_SEARXNG_URL", "").strip()
    return provider or ("brave" if key else "searxng" if url else ""), key, url


def searxng_search_url(parsed) -> str:
    """Append the search path to an already validated SearXNG base URL."""
    path = parsed.path.rstrip("/")
    if not path.endswith("/search"):
        path += "/search"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _searxng_endpoint(value: str) -> str:
    # No DNS or network on a settings read. The guarded fetch validates the
    # resolved host and every redirect before making a search request.
    from .web_search import _http_url

    try:
        parsed = urlsplit(value)
        valid = _http_url(value) and not parsed.query and not parsed.fragment
    except ValueError:
        valid = False
    if not valid:
        raise SearchConfigurationError(
            "SearXNG needs an HTTP(S) URL without credentials, a query string or a fragment.")
    return searxng_search_url(parsed)


def _openai_key(user: str, saved: dict, connections: list[dict]) -> str:
    selected = saved.get("openai_connection") or ""
    if selected:
        entry = next((entry for entry in connections if entry["id"] == selected), None)
        if entry is None:
            raise SearchConfigurationError(
                "The selected OpenAI connection is unavailable. Choose your own official OpenAI API connection.")
        key = entry["api_key"].strip()
    else:
        key = _key(user, saved, "openai_api_key") or (connections[0]["api_key"].strip() if connections else "")
    if key and not _valid_key(key):
        raise SearchConfigurationError("The OpenAI web search API key is invalid. Replace it in Settings → AI → Connections.")
    return key


def _legacy_credentials() -> dict:
    provider, key, url = legacy_environment()
    if not provider:
        raise SearchConfigurationError(
            "Web search is not configured. Choose a service in Settings → AI → Connections.", "not_configured")
    if provider == "brave":
        if not key or not _valid_key(key):
            raise SearchConfigurationError("The server's Brave web search key is missing or invalid.")
        return {"provider": provider, "api_key": key, "model": "", "url": BRAVE_URL}
    if provider == "searxng":
        return {"provider": provider, "api_key": "", "model": "", "url": _searxng_endpoint(url)}
    raise SearchConfigurationError("The server web search provider must be brave or searxng.")


def credentials(user: str) -> dict:
    """Resolve one service; explicit choices never fall through to another.

    Auto prefers OpenAI, then Brave, then SearXNG. Server environment values
    are a compatibility fallback only before this account saves settings.
    """
    if not user:
        raise SearchConfigurationError("Web search needs a signed-in account.", "not_configured")
    stored = _stored(user)
    saved = {**DEFAULTS, **(stored or {})}
    provider = saved["provider"]
    if provider == "off":
        raise SearchConfigurationError("Web search is turned off in Settings → AI → Connections.", "disabled")
    if provider not in PROVIDERS:
        raise SearchConfigurationError("Choose a supported web search service in Settings → AI → Connections.")
    if provider in ("auto", "openai"):
        key = _openai_key(user, saved, _connections(user))
        if key:
            return {"provider": "openai", "api_key": key,
                    "model": saved["openai_model"] or DEFAULT_MODEL, "url": OPENAI_URL}
        if provider == "openai":
            raise SearchConfigurationError(
                "OpenAI web search needs your API key or an official OpenAI API connection.", "not_configured")
    if provider in ("auto", "brave"):
        key = _key(user, saved, "brave_api_key")
        if key:
            return {"provider": "brave", "api_key": key, "model": "", "url": BRAVE_URL}
        if provider == "brave":
            raise SearchConfigurationError("Brave web search needs an API key.", "not_configured")
    if provider in ("auto", "searxng"):
        if saved["searxng_url"]:
            return {"provider": "searxng", "api_key": "", "model": "",
                    "url": _searxng_endpoint(saved["searxng_url"])}
        if provider == "searxng":
            raise SearchConfigurationError("SearXNG web search needs an instance URL.", "not_configured")
    if stored is None:
        return _legacy_credentials()
    raise SearchConfigurationError(
        "Web search is not configured. Choose a service in Settings → AI → Connections.", "not_configured")


def masked(user: str, can_edit: bool) -> dict:
    saved = {**DEFAULTS, **(_stored(user) or {})}
    out = {field: saved[field] for field in DEFAULTS}
    for field in KEY_FIELDS:
        try:
            key = _key(user, saved, field)
        except SearchConfigurationError:
            hint = "set"  # preserve the UI's clear-key control for unreadable ciphertext
        else:
            hint = ("…" + key[-4:] if len(key) > 8 else "set") if key else ""
        out[field.replace("api_key", "key_hint")] = hint
    out.update(connections=[{"id": entry["id"], "label": ai_settings.provider_label(entry)}
                            for entry in _connections(user)],
               can_edit=can_edit, configured=False, effective_provider="")
    try:
        out.update(configured=True, effective_provider=credentials(user)["provider"])
    except SearchConfigurationError as error:
        out["configuration_error"] = str(error)
    return out


def save(user: str, fields: dict) -> None:
    """Merge only supplied fields; empty key inputs keep existing ciphertext."""
    changes = {}
    for field in DEFAULTS:
        if field not in fields:
            continue
        value = fields[field].strip()
        if field == "provider" and value not in PROVIDERS:
            raise HTTPException(400, "invalid web search provider")
        if field == "openai_model":
            value = value or DEFAULT_MODEL
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", value):
                raise HTTPException(400, "invalid OpenAI search model")
        if field == "openai_connection" and value and value not in {e["id"] for e in _connections(user)}:
            raise HTTPException(400, "choose your own official OpenAI API connection")
        if field == "searxng_url" and value:
            try:
                _searxng_endpoint(value)
            except SearchConfigurationError as error:
                raise HTTPException(400, str(error)) from None
        changes[field] = value
    for field in KEY_FIELDS:
        value = (fields.get(field) or "").strip()
        if fields.get("clear_" + field):
            if value:
                raise HTTPException(400, "cannot set and clear the same web search key")
            changes[field] = ""
        elif value:
            if not _valid_key(value):
                raise HTTPException(400, "invalid web search API key")
            payload = json.dumps({"user": user, "field": field, "key": value}).encode("utf-8")
            changes[field] = cipher().encrypt(payload).decode("ascii")
    update_pref(user, SEARCH_PREF_KEY, lambda value: {**DEFAULTS, **(value if isinstance(value, dict) else {}), **changes})
