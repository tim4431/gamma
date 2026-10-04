"""The sign-up gate an admin edits on the Admin page's Settings tab: the
registration mode, the Cloudflare Turnstile pair and extra blocked mail
domains. They live in the ``settings`` table and take effect without a
restart. ``config.py`` holds what is fixed for the container's life; no
value is in both.

Reads are cached in the process, which is safe because the image runs one
uvicorn worker. A write goes through ``update``; the caller commits and then
calls ``invalidate``, so no reader caches a value that rolls back.
"""

import re
from contextlib import closing

from . import db

REGISTRATION_MODES = ("open", "invite", "closed")
DEFAULTS = {"registration": "invite", "turnstile_sitekey": "", "turnstile_secret": "", "blocked_email_domains": ""}
SECRETS = {"turnstile_secret"}  # never sent to a browser or written to the audit log

DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
MAX_BLOCKED_DOMAINS = 500
MAX_KEY_LENGTH = 200  # a Turnstile key is well under this

_cache: dict[str, str] | None = None


def invalidate() -> None:
    """Drop the cache; the next read loads the table."""
    global _cache
    _cache = None


def _values() -> dict[str, str]:
    global _cache
    if _cache is None:
        values = dict(DEFAULTS)
        with closing(db.connect()) as conn:
            for row in conn.execute("SELECT key, value FROM settings").fetchall():
                if row["key"] in values:  # a key this build does not know is ignored
                    values[row["key"]] = row["value"]
        _cache = values
    return _cache


# --- what the rest of the server reads ---------------------------------------

def registration() -> str:
    """``open`` / ``invite`` / ``closed``. A value ``update`` would refuse (a
    hand-edited row, an imported variable) reads as ``invite``: a bad row must
    never open registration."""
    mode = _values()["registration"]
    return mode if mode in REGISTRATION_MODES else "invite"


def _turnstile_on() -> bool:
    """Turnstile runs only with both halves stored. A secret without the site
    key would refuse every sign-up, since the forms would show no widget."""
    v = _values()
    return bool(v["turnstile_sitekey"] and v["turnstile_secret"])


def turnstile_sitekey() -> str:
    """The key the forms render the widget with; empty while Turnstile is off."""
    return _values()["turnstile_sitekey"] if _turnstile_on() else ""


def turnstile_secret() -> str:
    """The key ``captcha.verify`` checks a token with; empty while Turnstile is off."""
    return _values()["turnstile_secret"] if _turnstile_on() else ""


def blocked_email_domains() -> frozenset[str]:
    return frozenset(_values()["blocked_email_domains"].split())


def unguarded_registration() -> bool:
    """Open registration with Turnstile off, so only the rate limits stop a
    script. The Admin page and the startup log warn about it."""
    return registration() == "open" and not _turnstile_on()


# --- the Admin page ----------------------------------------------------------

def admin_view() -> dict:
    """What the Settings tab shows. A secret stays on the server; the tab
    only learns whether one is stored."""
    v = _values()
    return {"registration": registration(), "turnstile_sitekey": v["turnstile_sitekey"],
            "turnstile_secret_set": bool(v["turnstile_secret"]), "turnstile_on": _turnstile_on(),
            "blocked_email_domains": v["blocked_email_domains"], "unguarded": unguarded_registration()}


def _clean_domains(raw: str) -> str:
    """A domain list from whatever is pasted: commas, spaces or one per line,
    with an address or a ``@domain`` among them. Stored one per line,
    lowercased and deduplicated, in the order given."""
    out: list[str] = []
    bad: list[str] = []
    for word in re.split(r"[,\s]+", raw.lower()):
        name = word.strip(".").rpartition("@")[2]
        if not name:
            continue
        found = out if DOMAIN_RE.match(name) else bad
        if name not in found:
            found.append(name)
    if bad:
        raise ValueError(f"Not domain names: {', '.join(bad)}.")
    if len(out) > MAX_BLOCKED_DOMAINS:
        raise ValueError(f"At most {MAX_BLOCKED_DOMAINS} domains.")
    return "\n".join(out)


def clean(key: str, raw) -> str:
    """One value as it is stored. Raises ``ValueError`` with the message the
    API returns; ``None`` means empty."""
    if key not in DEFAULTS:
        raise ValueError(f"Unknown setting: {key}.")
    if raw is not None and not isinstance(raw, str):
        raise ValueError(f"{key} must be text.")
    value = (raw or "").strip()
    if key == "registration":
        if value not in REGISTRATION_MODES:
            raise ValueError("Registration must be open, invite or closed.")
    elif key == "blocked_email_domains":
        return _clean_domains(value)
    elif len(value) > MAX_KEY_LENGTH:
        raise ValueError(f"{key} is too long.")
    return value


def update(conn, changes: dict, actor: str) -> None:
    """Store the keys given and audit the ones that changed. A secret sent
    blank is kept, because the form cannot show it; ``None`` clears it. Every
    value is checked before any is written. The caller commits and then calls
    ``invalidate``."""
    cleaned = {key: clean(key, raw) for key, raw in changes.items() if not (key in SECRETS and raw == "")}
    current = _values()
    for key, value in cleaned.items():
        if value == current[key]:
            continue
        conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                     (key, value, db.now()))
        shown = ("set" if value else "cleared") if key in SECRETS else value.replace("\n", ",")
        db.audit(conn, "settings.set", actor=actor, detail=f"{key}={shown}")
