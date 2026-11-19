"""Server-wide options an admin sets in Settings, which were environment
variables before (docs/research/environment-variables.md):

- ``guest_max`` — how many guest accounts may live at once (Users →
  Guests). ``GAMMA_GUEST_MAX`` overrides it where it is set (the public
  demo; the account server sets 0 on a hosted container), and a hosted
  container takes no guests whatever it says.
- ``metadata_lookup`` — whether papers are looked up in the registries
  (arXiv, doi.org, Crossref, OpenAlex, Open Library, Google Books) — and
  ``model_catalog`` — whether model facts are read from models.dev
  (Server → Online services). The browser suite turns both off.
- ``vault_keep_mb`` — the largest work-folder file the vault hashes and
  serves; past it a file is listed with no bytes (Server → Work folders,
  docs/dev/documents.md "The listed state").
- ``searxng_url`` and ``openalex_key`` — the search services every account
  without its own uses (AI → Tool usage → Online search): the admin's
  SearXNG instance, which may be private, and an OpenAlex API key, stored
  encrypted and never returned.

They are one users.db ``settings`` record, ``options`` (JSON, only the
options saved: the rest take their defaults), read through a cache of a
few seconds, since a vault round asks for ``vault_keep_mb`` once per file;
``update`` refreshes it at once, and manage.py ``set-option`` writes the
same record (the browser suite's way to set one before a server starts).
"""

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken

from . import config
from .publisher_sessions import seal, unseal
from .server_settings import _peek_raw, _set_raw

KEY = "options"
CACHE_S = 5.0


@dataclass(frozen=True)
class Option:
    kind: str            # "bool", "int", "url", "secret"
    default: object
    lo: int = 0
    hi: int = 0


OPTIONS = {
    "guest_max": Option("int", 500, 1, 100_000),
    "metadata_lookup": Option("bool", True),
    "model_catalog": Option("bool", True),
    "vault_keep_mb": Option("int", 64, 0, 100_000),
    "searxng_url": Option("url", ""),
    "openalex_key": Option("secret", ""),
}
_OFF = ("0", "off", "false", "no")

_lock = threading.Lock()
_cache: dict = {"until": 0.0, "saved": {}}


def _read() -> dict:
    """The saved record, the secrets decrypted; {} when there is none or it
    cannot be read."""
    try:
        raw = _peek_raw(KEY)
        saved = json.loads(raw) if raw else {}
    except (sqlite3.Error, ValueError, TypeError):
        return {}
    if not isinstance(saved, dict):
        return {}
    for name, option in OPTIONS.items():
        if option.kind == "secret" and name in saved:
            try:
                saved[name] = unseal(saved[name]) if isinstance(saved[name], str) else ""
            except (InvalidToken, ValueError, OSError):
                saved[name] = ""  # the data directory's key changed: as if never saved
    return saved


def _saved() -> dict:
    with _lock:
        if time.monotonic() >= _cache["until"]:
            _cache["saved"], _cache["until"] = _read(), time.monotonic() + CACHE_S
        return _cache["saved"]


def check(name: str, value):
    """``value`` as option ``name`` keeps it. ValueError for an unknown
    option or a value it cannot take."""
    option = OPTIONS.get(name)
    if option is None:
        raise ValueError(f"there is no option {name!r}")
    if option.kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() not in _OFF
        return bool(value)
    if option.kind == "int":
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a whole number") from None
        if not option.lo <= number <= option.hi:
            raise ValueError(f"{name} must be {option.lo}-{option.hi}")
        return number
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    value = value.strip()
    if option.kind == "url" and value:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc or any(c.isspace() for c in value):
            raise ValueError("Give the address as http(s)://host[:port][/path].")
        return value.rstrip("/")
    if option.kind == "secret" and (len(value) > 200 or any(c.isspace() for c in value)):
        raise ValueError(f"{name} is one word of at most 200 characters")
    return value


def check_admin_change(changes: dict) -> None:
    """An admin's change in Settings: each value as ``check`` takes it, and
    none the environment decides (``guest_max`` under ``GAMMA_GUEST_MAX``).
    ValueError otherwise."""
    for name, value in changes.items():
        check(name, value)
    if "guest_max" in changes and config.guest_max_override() is not None:
        raise ValueError("The most guests at once is set by GAMMA_GUEST_MAX on this server.")


def get(name: str):
    """Option ``name``: the saved value, else its default."""
    saved = _saved()
    return saved[name] if name in saved else OPTIONS[name].default


def update(changes: dict) -> None:
    """Save ``changes`` ({name: value}), all checked first: ValueError and
    nothing saved when one cannot be. A value equal to the default is
    dropped from the record, so a later default applies to it."""
    checked = {name: check(name, value) for name, value in changes.items()}
    with _lock:
        saved = _read()
        for name, value in checked.items():
            if value == OPTIONS[name].default:
                saved.pop(name, None)
            else:
                saved[name] = value
        stored = {name: seal(value) if OPTIONS[name].kind == "secret" else value for name, value in saved.items()}
        _set_raw(KEY, json.dumps(stored) if stored else "")
        _cache["saved"], _cache["until"] = saved, time.monotonic() + CACHE_S


def view() -> dict:
    """What Settings shows: every option but the secrets, which are
    ``<name>_set`` (whether one is saved); ``guest_max`` as in force, with
    ``guest_max_env`` when the environment sets it (the row is then
    read-only); and ``work_folders``, whether this server keeps work
    folders at all (the Work folders row)."""
    out = {}
    for name, option in OPTIONS.items():
        if option.kind == "secret":
            out[f"{name}_set"] = bool(get(name))
        else:
            out[name] = get(name)
    out["guest_max"], out["guest_max_env"] = guest_max(), config.guest_max_override() is not None
    out["ranges"] = {name: [o.lo, o.hi] for name, o in OPTIONS.items() if o.kind == "int"}
    out["work_folders"] = config.folder_links_enabled()
    return out


def guest_max() -> int:
    """How many guest accounts may live at once: 0 on a hosted container,
    whose guests would not count against the plan's accounts; else
    ``GAMMA_GUEST_MAX`` where it is set (0: no guests), else the admin's."""
    if config.hosted():
        return 0
    override = config.guest_max_override()
    return get("guest_max") if override is None else override


def vault_keep_bytes() -> int:
    """How large a work folder's file may be for the vault to hash and
    serve it (gamma/vault/kinds.py, the class ``file``); PDFs and notes are
    never measured against it."""
    return get("vault_keep_mb") * 1024 * 1024
