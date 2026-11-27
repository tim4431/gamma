"""Server-wide options an admin sets in Settings (which environment
variables stay beside them, and why: docs/research/environment-variables.md):

- ``guest_max`` — how many guest accounts may live at once (Users →
  Guests). ``GAMMA_GUEST_MAX`` overrides it where it is set (the public
  demo; the account server sets 0 on a hosted container), and a hosted
  container takes no guests whatever it says.
- ``metadata_lookup`` — whether papers are looked up in the registries
  (arXiv, doi.org, Crossref, OpenAlex, Open Library, Google Books) —,
  ``model_catalog`` — whether model facts are read from models.dev — and
  ``citation_styles`` — whether citation styles are searched and fetched
  in the CSL repository at zotero.org (Settings → Citations → Add style;
  a style's file can be added either way, gamma/routers/citations.py)
  (Server → Online services). The browser suite turns all three off.
- ``vault_keep_mb`` — the largest work-folder file the vault hashes and
  serves; past it a file is listed with no bytes (Server → Work folders,
  docs/dev/documents.md "The listed state").
- ``searxng_url`` and ``openalex_key`` — the search services every account
  without its own uses (AI → Tool usage → Online search): the admin's
  SearXNG instance, which may be private, and an OpenAlex API key, stored
  encrypted and never returned.
- ``ocr``, ``ocr_auto`` and ``ocr_workers`` — the text recognition of
  scanned PDFs (Server → Text recognition, docs/dev/documents.md
  "Recognized text"): whether this server recognizes text at all
  (``ocr_enabled``: ``GAMMA_OCR`` overrides it, and a hosted container or
  a demo server recognizes none unless that says on), whether a scan
  uploaded with no text is recognized by itself (gamma/ocr_auto.py), and
  how many files are recognized at once on the server (the jobs' ``ocr``
  lane, the engines kept loaded).

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

from . import config, server_settings
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
    "citation_styles": Option("bool", True),
    "vault_keep_mb": Option("int", 64, 0, 100_000),
    "searxng_url": Option("url", ""),
    "openalex_key": Option("secret", ""),
    "ocr": Option("bool", True),
    "ocr_auto": Option("bool", True),
    "ocr_workers": Option("int", 1, 1, 64),
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
    if "ocr" in changes and config.ocr_override() is not None:
        raise ValueError("Text recognition is set by GAMMA_OCR on this server.")


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
    read-only); ``work_folders``, whether this server keeps work folders
    at all (the Work folders row); ``ocr`` as in force, with ``ocr_env``
    when ``GAMMA_OCR`` sets it, and ``text_recognition`` (``ocr_rows``),
    which of the Text recognition rows apply here."""
    out = {}
    for name, option in OPTIONS.items():
        if option.kind == "secret":
            out[f"{name}_set"] = bool(get(name))
        else:
            out[name] = get(name)
    out["guest_max"], out["guest_max_env"] = guest_max(), config.guest_max_override() is not None
    out["ocr"], out["ocr_env"] = ocr_enabled(), config.ocr_override() is not None
    out["ranges"] = {name: [o.lo, o.hi] for name, o in OPTIONS.items() if o.kind == "int"}
    out["work_folders"] = config.folder_links_enabled()
    out["text_recognition"] = ocr_rows()
    return out


def guest_max() -> int:
    """How many guest accounts may live at once: 0 on a hosted container,
    whose guests would not count against the plan's accounts; else
    ``GAMMA_GUEST_MAX`` where it is set (0: no guests), else the admin's."""
    if config.hosted():
        return 0
    override = config.guest_max_override()
    return get("guest_max") if override is None else override


def ocr_enabled() -> bool:
    """Whether this server recognizes the text of scanned PDFs:
    ``GAMMA_OCR`` when it says on or off; else none on a hosted container
    or in demo mode (server_settings.demo_mode), where a run of hours takes
    the CPU every account shares; else the admin's ``ocr``. The environment
    is read first, so a server it decides reads no users.db for it (the
    desktop's engine check). Read when asked."""
    override = config.ocr_override()
    if override is not None:
        return override
    return not _forced_off() and get("ocr")


def ocr_rows() -> dict:
    """Which rows of Settings → Server → Text recognition apply here:
    ``switch``, whether recognition is this server's to turn on or off (it
    has the models, the window's engine needs them too; and it is no hosted
    container or demo server, unless ``GAMMA_OCR`` decides there, the row
    then read-only), and ``server``, whether the server recognizes text
    itself now (ocr_engine ``capability``), where the automatic recognition
    and how many files at once mean anything. No rows: no section."""
    from . import ocr_engine  # local: ocr_engine asks this module whether recognition is on

    capability = ocr_engine.capability()
    switch = ocr_engine.has_models() and (config.ocr_override() is not None or not _forced_off())
    return {"switch": switch, "server": bool(capability) and capability.get("where") == "server"}


def _forced_off() -> bool:
    """A hosted container or a demo server, where recognition is off
    whatever the admin says (``ocr_enabled``)."""
    if config.hosted():
        return True
    try:
        return server_settings.demo_mode()
    except Exception:  # noqa: BLE001 — a users.db that does not read now: a demo server says so once it does
        return False


def vault_keep_bytes() -> int:
    """How large a work folder's file may be for the vault to hash and
    serve it (gamma/vault/kinds.py, the class ``file``); PDFs and notes are
    never measured against it."""
    return get("vault_keep_mb") * 1024 * 1024
