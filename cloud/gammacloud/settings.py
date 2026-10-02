"""Server settings an admin edits at runtime, kept in the ``settings`` table
and shown on the Admin page's Settings tab.

What lives here is the sign-up gate: who may register, and what a
registration has to get past. Those are decisions an operator makes while
the service runs, so asking for a container restart to change one is the
wrong shape.

``config.py`` keeps what cannot move — the data directory, the issuer every
Gamma server verifies, and the mail and OAuth credentials, whose other half
is configured outside this server anyway. **The two sets do not overlap**:
an environment variable for a setting below is not read at all, and
``app.py`` says so at startup if a retired one is still set. An existing
``cloud.db`` imports the old variables once, during the upgrade that adds
the table (``db._step_settings``).

Reads go through the accessors and are cached in the process. A write
therefore goes through ``set`` and is followed by ``invalidate`` once the
transaction commits — the admin router does that, after the commit rather
than before, so a reader can never cache a value that then rolls back. The
image runs one uvicorn worker, so the cache and the table cannot disagree.
"""

import re
from contextlib import closing
from dataclasses import dataclass, field

from . import db

DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
MAX_BLOCKED_DOMAINS = 500
MAX_VALUE = 20000

REGISTRATION_MODES = ("open", "invite", "closed")


@dataclass(frozen=True)
class Spec:
    """One editable setting. ``kind`` is what the Admin page renders and
    whether the value may be sent back to a browser: a ``secret`` never is.
    ``clean`` normalises and validates, raising ``ValueError`` with the
    message the API returns."""

    key: str
    default: str
    label: str
    help: str
    kind: str = "text"                      # text | choice | lines | secret
    choices: tuple[str, ...] = field(default_factory=tuple)

    def clean(self, raw) -> str:
        if raw is None:
            return ""
        if not isinstance(raw, str):
            raise ValueError(f"{self.label} must be text.")
        value = raw.strip()
        if len(value) > MAX_VALUE:
            raise ValueError(f"{self.label} is too long.")
        if self.kind == "choice":
            if value not in self.choices:
                raise ValueError(f"{self.label} must be one of {', '.join(self.choices)}.")
            return value
        if self.kind == "lines":
            return _clean_domains(value)
        return value


def _clean_domains(raw: str) -> str:
    """A domain list from anything a person pastes — commas, spaces or one
    per line, an address or a ``@domain`` among them. Stored one per line,
    lowercased and deduplicated, in the order given."""
    out: list[str] = []
    for word in re.split(r"[,\s]+", raw.lower()):
        name = word.strip().strip(".").rpartition("@")[2]
        if not name:
            continue
        if not DOMAIN_RE.match(name):
            raise ValueError(f"{name!r} is not a domain name.")
        if name not in out:
            out.append(name)
    if len(out) > MAX_BLOCKED_DOMAINS:
        raise ValueError(f"At most {MAX_BLOCKED_DOMAINS} domains.")
    return "\n".join(out)


SPECS: tuple[Spec, ...] = (
    Spec("registration", "invite", "Registration",
         "Who can create an account. An invite code still works in open mode and still grants its plan.",
         kind="choice", choices=REGISTRATION_MODES),
    Spec("turnstile_sitekey", "", "Turnstile site key",
         "Cloudflare Turnstile, the anti-bot check on the sign-up and reset forms. The widget appears "
         "once both values are set; without the secret the check passes everything."),
    Spec("turnstile_secret", "", "Turnstile secret",
         "The secret half, kept in cloud.db and never shown again.", kind="secret"),
    Spec("blocked_email_domains", "", "Blocked e-mail domains",
         "Refused at registration, on top of the throwaway-mail services the code already knows. "
         "A name also covers its subdomains. One per line.", kind="lines"),
)

BY_KEY = {s.key: s for s in SPECS}

_cache: dict[str, str] | None = None


def invalidate() -> None:
    """Drop the cache; the next read loads the table. Called after the
    transaction that wrote a setting has committed."""
    global _cache
    _cache = None


def _all() -> dict[str, str]:
    global _cache
    if _cache is None:
        values = {s.key: s.default for s in SPECS}
        with closing(db.connect()) as conn:
            for row in conn.execute("SELECT key, value FROM settings").fetchall():
                if row["key"] in values:
                    values[row["key"]] = row["value"]
        _cache = values
    return _cache


def value(key: str) -> str:
    return _all()[key]


# --- the accessors the rest of the server uses -------------------------------

def registration() -> str:
    """``open`` / ``invite`` / ``closed``. A value the table should not hold
    is read as ``invite``, the closed-enough default: a corrupt row must
    never open registration."""
    mode = value("registration")
    return mode if mode in REGISTRATION_MODES else "invite"


def turnstile_sitekey() -> str:
    return value("turnstile_sitekey")


def turnstile_secret() -> str:
    return value("turnstile_secret")


def blocked_email_domains() -> frozenset[str]:
    return frozenset(value("blocked_email_domains").split())


def unguarded_registration() -> bool:
    """Open registration with no Turnstile secret: ``captcha.verify`` passes
    everything, so the rate limits are all that is left. Said on the Admin
    page and in the startup log, never enforced."""
    return registration() == "open" and not turnstile_secret()


# --- the Admin page ----------------------------------------------------------

def listing() -> list[dict]:
    """Every setting for the Settings tab. A secret's value never leaves the
    server: ``set`` says whether one is stored."""
    values = _all()
    return [{"key": s.key, "label": s.label, "help": s.help, "kind": s.kind,
             "choices": list(s.choices),
             "value": "" if s.kind == "secret" else values[s.key],
             "set": bool(values[s.key])}
            for s in SPECS]


def set(conn, key: str, raw, actor: str = "") -> str:  # noqa: A001 — the KV's verb
    """Store one setting; returns the stored value. ``None`` or ``""`` for a
    secret clears it. The caller commits and then calls ``invalidate``."""
    spec = BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"unknown setting {key!r}")
    value = spec.clean(raw)
    conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                 (key, value, db.now()))
    # A secret is never written to the audit log, only whether it now exists.
    shown = ("set" if value else "cleared") if spec.kind == "secret" else value.replace("\n", ",")
    db.audit(conn, "settings.set", actor=actor, detail=f"{key}={shown}")
    return value
