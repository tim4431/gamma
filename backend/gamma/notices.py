"""What wants a look: the notices behind the red dot on the account button.

A notice is one thing an account should see once — a newer Gamma release,
errors in the server log — and it points at the Settings pane that shows
it. Each carries a *fingerprint* naming what changed (the release version,
the seq of the newest error); "resolved" means the account has seen that
fingerprint, recorded in the account-wide ``notices-seen`` pref as
``{id: fingerprint}``. Visiting the pane records it (the frontend's
``useNotices``); a new release or a fresh error changes the fingerprint
and the notice is back on its own. Nothing is ever dismissed for good.

Sources are plain functions registered with ``@source``; each returns a
Notice or None and must be cheap — a cached or in-memory read — because
``for_user`` runs on every poll of ``GET /api/notices``. Admin-only
sources are skipped for everyone else, so a member's poll does no work
beyond that. The one network call, the release check, sits behind
``version.latest_release``'s six-hour cache.
"""

import re
from dataclasses import asdict, dataclass

from . import logbuf, version
from .db import NOTICES_SEEN_PREF_KEY, get_pref, set_pref

TONES = ("info", "warn", "error")
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_MAX_SEEN = 64


@dataclass(frozen=True)
class Notice:
    id: str
    fingerprint: str
    tone: str  # one of TONES; the dot takes the strongest
    pane: str  # the Settings pane that resolves it
    title: str


_SOURCES: list[tuple[callable, bool]] = []


def source(*, admin_only=False):
    """Register a notice source: ``fn() -> Notice | None``."""
    def wrap(fn):
        _SOURCES.append((fn, admin_only))
        return fn
    return wrap


@source(admin_only=True)
def update_available():
    """A newer GitHub release than this build (nothing for a checkout, an
    air-gapped server or an unreachable GitHub)."""
    release, _error = version.latest_release()
    mine = version.parse_version(version.VERSION)
    theirs = version.parse_version(release["version"]) if release else None
    if not (mine and theirs and theirs > mine):
        return None
    return Notice("update", release["version"], "warn", "server",
                  f"Gamma v{release['version']} is available — this server runs v{version.VERSION}")


@source(admin_only=True)
def log_errors():
    """Errors logged since the account last looked at the server log. The
    fingerprint is the start time plus the newest error's seq: a restart
    resets both, so an old ack never covers a new error."""
    seq = logbuf.last_seq("error")
    if not seq:
        return None
    started = version.STARTED_AT.isoformat()
    return Notice("log-errors", f"{started}:{seq}", "error", "server", "New errors in the server log")


def seen_map(username: str) -> dict:
    value, _ = get_pref(username, NOTICES_SEEN_PREF_KEY)
    return value if isinstance(value, dict) else {}


def for_user(username: str, is_admin: bool) -> list[dict]:
    """The unresolved notices of an account, strongest tone first."""
    found = [notice for fn, admin_only in _SOURCES if is_admin or not admin_only
             if (notice := fn()) is not None]
    if not found:
        return []
    seen = seen_map(username)
    return [asdict(n) for n in sorted(found, key=lambda n: -TONES.index(n.tone))
            if seen.get(n.id) != n.fingerprint]


def mark_seen(username: str, notice_id: str, fingerprint: str) -> None:
    """Record that the account has seen this fingerprint of the notice."""
    if not _ID_RE.match(notice_id or "") or not isinstance(fingerprint, str) or len(fingerprint) > 200:
        raise ValueError("invalid notice")
    seen = seen_map(username)
    seen[notice_id] = fingerprint
    if len(seen) > _MAX_SEEN:  # never grows past the sources that exist; a guard, not a policy
        seen = dict(list(seen.items())[-_MAX_SEEN:])
    set_pref(username, NOTICES_SEEN_PREF_KEY, seen)
