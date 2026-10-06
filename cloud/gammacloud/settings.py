"""What an admin edits on the Admin page, in the ``settings`` table, with
effect at once and no restart:

- the sign-up gate: the registration mode, the Cloudflare Turnstile pair,
  extra blocked mail domains, and the mail domains that may register
  without an invite code;
- which paid plans are on sale;
- the operator's alerts: on or off, and the address they go to;
- the fleet: the image tag a new hosted server runs, and whether outdated
  servers are upgraded without being asked.

``config.py`` holds what is fixed for the container's life; no value is in
both.

Reads are cached in the process, which is safe because the image runs one
uvicorn worker. A write goes through ``update``; the caller commits and then
calls ``invalidate``, so no reader caches a value that rolls back.
"""

import json
import re
from contextlib import closing

from . import config, db

REGISTRATION_MODES = ("open", "invite", "closed")
PAID_PLANS = tuple(p for p in config.PLANS if p != "free")
ON_OFF = ("on", "off")
DEFAULTS = {"registration": "invite", "turnstile_sitekey": "", "turnstile_secret": "", "blocked_email_domains": "",
            "allowed_email_domains": "", "plans_on_sale": " ".join(PAID_PLANS), "alerts": "on", "alert_email": "",
            "fleet_image_tag": "", "fleet_auto_upgrade": "off"}
SECRETS = {"turnstile_secret"}  # never sent to a browser or written to the audit log
SWITCHES = {"alerts", "fleet_auto_upgrade"}  # stored as ``on`` / ``off``

DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IMAGE_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")  # a Docker image tag
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


def allowed_email_domains() -> frozenset[str]:
    """The mail domains that may register without an invite code; empty
    means any domain may."""
    return frozenset(_values()["allowed_email_domains"].split())


def unguarded_registration() -> bool:
    """Open registration with Turnstile off, so only the rate limits stop a
    script. The Admin page and the startup log warn about it."""
    return registration() == "open" and not _turnstile_on()


def plans_on_sale() -> frozenset[str]:
    """The paid plans the operator offers. A plan left out is held back: no
    checkout and no switch to it, whatever else is configured
    (``billing.can_sell``). People who already have it keep it."""
    return frozenset(_values()["plans_on_sale"].split()) & frozenset(PAID_PLANS)


def alerts_on() -> bool:
    """Whether the operator is mailed about problems (``alerts.py``)."""
    return _values()["alerts"] != "off"


def alert_email() -> str:
    """Where alerts go; empty means every admin account's address."""
    return _values()["alert_email"]


def fleet_image_tag() -> str:
    """The image tag a new hosted server runs and what *outdated* compares
    with: the stored one, else the environment's."""
    return _values()["fleet_image_tag"] or config.FLEET_IMAGE_TAG


def fleet_auto_upgrade() -> bool:
    """Whether the hourly tick upgrades outdated servers by itself."""
    return _values()["fleet_auto_upgrade"] == "on"


# --- the Admin page ----------------------------------------------------------

def admin_view() -> dict:
    """What the Settings tab shows. A secret stays on the server; the tab
    only learns whether one is stored."""
    v = _values()
    return {"registration": registration(), "turnstile_sitekey": v["turnstile_sitekey"],
            "turnstile_secret_set": bool(v["turnstile_secret"]), "turnstile_on": _turnstile_on(),
            "blocked_email_domains": v["blocked_email_domains"], "unguarded": unguarded_registration(),
            "allowed_email_domains": v["allowed_email_domains"],
            "plans_on_sale": [p for p in PAID_PLANS if p in plans_on_sale()],
            "alerts": alerts_on(), "alert_email": v["alert_email"],
            "fleet_image_tag": v["fleet_image_tag"], "fleet_image_tag_default": config.FLEET_IMAGE_TAG,
            "fleet_auto_upgrade": fleet_auto_upgrade()}


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
    elif key in ("blocked_email_domains", "allowed_email_domains"):
        return _clean_domains(value)
    elif key == "plans_on_sale":
        plans = set(value.lower().replace(",", " ").split())
        if plans - set(PAID_PLANS):
            raise ValueError(f"Not plans that are sold: {', '.join(sorted(plans - set(PAID_PLANS)))}.")
        return " ".join(p for p in PAID_PLANS if p in plans)
    elif key in SWITCHES:
        if value not in ON_OFF:
            raise ValueError(f"{key} must be on or off.")
    elif len(value) > MAX_KEY_LENGTH:
        raise ValueError(f"{key} is too long.")
    elif key == "alert_email" and value and not EMAIL_RE.match(value):
        raise ValueError("That is not an e-mail address.")
    elif key == "fleet_image_tag" and value and not IMAGE_TAG_RE.match(value):
        raise ValueError("That is not an image tag.")
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


# --- the fleet's extra environment -------------------------------------------
# Variables every hosted container gets on top of the ones the fleet sets
# itself (docs/dev/hosted.md "Environment"); a server's own (its ``env``
# column) win over them. They are a JSON object in the row FLEET_ENV_KEY,
# which ``DEFAULTS`` and the cache above do not know, like
# ``billing.RECONCILED_KEY``: ``update`` refuses the key, nothing caches it,
# and it is read from the table each time (a create or update job, the
# Admin page). Values are secrets: the audit log and the Admin page get the
# names only.

FLEET_ENV_KEY = "fleet_env"
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
ENV_VALUE_MAX = 4000
ENV_MAX_NAMES = 100
# What the fleet sets itself: the account server's (create_payload) and the agent's (the bucket, the proxy)
RESERVED_ENV = frozenset({"GAMMA_HOSTED", "GAMMA_PUBLIC_URL", "GAMMA_GUEST_MAX", "FORWARDED_ALLOW_IPS"})
RESERVED_ENV_PREFIXES = ("GAMMA_CLOUD_", "GAMMA_S3_")


def env_of(raw) -> dict[str, str]:
    """A stored environment (JSON text) as a dict; anything else reads as empty."""
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def env_changes(current: dict, values, unset) -> tuple[dict[str, str], str]:
    """``current`` with ``values`` set and the names in ``unset`` removed,
    and the change in names only (for the audit log). Raises ``ValueError``
    with the message the API returns: a name is ``ENV_NAME_RE`` and not one
    the fleet sets itself; a value is text of at most ENV_VALUE_MAX
    characters with no line break or NUL."""
    values = {} if values is None else values
    unset = [] if unset is None else unset
    if not isinstance(values, dict) or not isinstance(unset, list):
        raise ValueError("Send set as an object of names and values, and unset as a list of names.")
    for name in [*values, *unset]:
        if not isinstance(name, str) or not ENV_NAME_RE.match(name):
            raise ValueError(f"{str(name)[:80]!r} is not a variable name: capitals, digits and _, starting with a "
                             "capital, at most 64.")
        if name in RESERVED_ENV or name.startswith(RESERVED_ENV_PREFIXES):
            raise ValueError(f"{name} is set by the fleet itself.")
    for name, value in values.items():
        if not isinstance(value, str):
            raise ValueError(f"The value of {name} must be text.")
        if len(value) > ENV_VALUE_MAX or "\n" in value or "\r" in value or "\0" in value:
            raise ValueError(f"The value of {name} is at most {ENV_VALUE_MAX} characters on one line.")
    out = {**current, **values}
    for name in unset:
        out.pop(name, None)
    if len(out) > ENV_MAX_NAMES:
        raise ValueError(f"At most {ENV_MAX_NAMES} variables.")
    said = [f"set {','.join(sorted(values))}" if values else "", f"unset {','.join(sorted(unset))}" if unset else ""]
    return out, " ".join(s for s in said if s)


def fleet_env(conn=None) -> dict[str, str]:
    """The fleet's extra environment, through ``conn`` when given (so a
    caller sees its own uncommitted write)."""
    if conn is None:
        with closing(db.connect()) as own:
            return fleet_env(own)
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (FLEET_ENV_KEY,)).fetchone()
    return env_of(row["value"] if row else "")


def set_fleet_env(conn, values: dict, unset: list, actor: str) -> list[str]:
    """Set and remove the fleet's variables (``env_changes``) and audit the
    names; the names now set. Nothing that runs changes: the next create
    job takes them, and running containers with an update run
    (``fleet.update_env``). The caller commits."""
    current = fleet_env(conn)
    new, said = env_changes(current, values, unset)
    if new != current:
        conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                     (FLEET_ENV_KEY, json.dumps(new), db.now()))
        db.audit(conn, "settings.fleet_env", actor=actor, detail=said)
    return sorted(new)
