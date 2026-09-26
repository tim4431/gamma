"""What wants a look: the notices behind the red dot on the account button.

A notice is one thing an account should see once — a newer Gamma release,
errors in the server log, a failed backup task — and it points at the
Settings pane that shows it. Each carries a *fingerprint* naming what
changed (the release version, the seq of the newest error, the failed
task's run time); "resolved" means the account has seen that fingerprint,
recorded in the account-wide ``notices-seen`` pref as ``{id: fingerprint}``.
Visiting the pane records it (the frontend's ``useNotices``); a new release
or a fresh error changes the fingerprint and the notice is back on its own.
Nothing is ever dismissed for good.

Sources are plain functions ``fn(username) -> Notice | None`` registered
with ``@source``; each must be cheap — a cached, in-memory or small
database read — because ``for_user`` runs them on every poll of
``GET /api/notices``. Admin-only sources are skipped for everyone else.
The one network call, the update check, sits behind
``version.check``'s six-hour cache; the one directory walk, the
storage usage, runs only for an account under a quota and is remembered
for a while.
"""

import hashlib
import re
import threading
import time
from dataclasses import asdict, dataclass

from . import backup_schedule, cloud_sync, logbuf, server_settings, sync_engine, translate_engines, version
from .db import NOTICES_SEEN_PREF_KEY, get_pref, set_pref

TONES = ("info", "warn", "error")
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_MAX_SEEN = 64
MB = 1024 * 1024


@dataclass(frozen=True)
class Notice:
    id: str
    fingerprint: str
    tone: str  # one of TONES; the dot takes the strongest
    pane: str  # the Settings pane that resolves it
    title: str


_SOURCES: list[tuple[callable, bool]] = []


def source(*, admin_only=False):
    """Register a notice source: ``fn(username) -> Notice | None``."""
    def wrap(fn):
        _SOURCES.append((fn, admin_only))
        return fn
    return wrap


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


@source(admin_only=True)
def update_available(_username):
    """A newer GitHub release than this build, or for a ``-dev`` build a
    newer build of its branch (nothing for a checkout, an air-gapped server
    or an unreachable GitHub)."""
    update = version.check()["update"]
    if not update:
        return None
    return Notice("update", update["version"], "warn", "server",
                  f"Gamma v{update['version']} is available — this server runs v{version.VERSION}")


@source(admin_only=True)
def log_errors(_username):
    """Errors logged since the account last looked at the server log. The
    fingerprint is the start time plus the newest error's seq: a restart
    resets both, so an old ack never covers a new error."""
    seq = logbuf.last_seq("error")
    if not seq:
        return None
    started = version.STARTED_AT.isoformat()
    return Notice("log-errors", f"{started}:{seq}", "error", "server", "New errors in the server log")


@source()
def backup_failed(username):
    """The account's backup tasks whose last run failed (Settings →
    Backups shows the error). Another failed run, of any of them, is a new
    fingerprint."""
    failed = [t for t in backup_schedule.list_tasks(username) if t.get("state") == "failed"]
    if not failed:
        return None
    mark = ",".join(f"{t['id'][:12]}:{t.get('last_run') or ''}" for t in sorted(failed, key=lambda t: t["id"]))
    title = (f'The backup task "{failed[0]["name"]}" failed' if len(failed) == 1
             else f"{_plural(len(failed), 'backup task')} failed")
    return Notice("backup-failed", mark, "error", "backups", title)


def _conflict_marks(username, publications):
    """Per clone (or per publication), the open conflict count and newest
    conflict id, folded into one short digest (a fingerprint is capped at
    200 characters, which a dozen clones with conflicts would pass); a
    mirror with a page filter is a publication."""
    marks, total = [], 0
    for mirror in sync_engine.list_mirrors(username):
        if (mirror.get("page_filter") is not None) != publications:
            continue
        count, newest = sync_engine.open_conflict_mark(mirror["workspace_id"])
        if count:
            marks.append(f"{mirror['workspace_id']}:{count}:{newest}")
            total += count
    return hashlib.sha1(",".join(marks).encode("utf-8")).hexdigest()[:16], total


@source()
def mirror_conflicts(username):
    """Open conflicts in the clones the account owns (Settings → Account & sync →
    Clones). Fingerprint: per clone, the count and the newest
    conflict — a new one brings the notice back, resolving old ones does
    not."""
    mark, total = _conflict_marks(username, publications=False)
    if not total:
        return None
    return Notice("mirror-conflicts", mark, "warn", "account",
                  f"{_plural(total, 'sync conflict')} to look at in your clones")


@source()
def publish_conflicts(username):
    """Open conflicts in the pages the account publishes to Gamma Cloud
    (Settings → Account & sync → Publishing), fingerprinted like the clones'."""
    mark, total = _conflict_marks(username, publications=True)
    if not total:
        return None
    return Notice("publish-conflicts", mark, "warn", "account",
                  f"{_plural(total, 'sync conflict')} to look at in your published pages")


@source()
def cloud_sync_failed(username):
    """The account's Gamma Cloud sync in its error state (the Account
    pane's cloud row says why)."""
    status = cloud_sync.profile_status(username)
    if status.get("state") != "error":
        return None
    error = (status.get("error") or "").strip().rstrip(".")
    return Notice("cloud-sync", status.get("at") or "", "warn", "account",
                  f"Gamma Cloud sync failed: {error}" if error else "Gamma Cloud sync failed")


@source()
def cloud_sync_choice(username):
    """The first settings sync with Gamma Cloud found two different copies
    and waits for the person to merge them or keep one (the Account pane)."""
    if cloud_sync.profile_status(username).get("state") != "choose":
        return None
    return Notice("cloud-sync-choice", "choose", "warn", "account",
                  "Your settings here and on Gamma Cloud differ: choose which to keep")


@source()
def free_translate_failing(username):
    """Microsoft's free translation endpoint keeps failing for this account
    (translate_engines' in-memory streak); gone after one success."""
    failing = translate_engines.free_failing(username)
    if not failing:
        return None
    return Notice("free-translate", failing["since"], "warn", "reading",
                  "Microsoft's free translation keeps failing — set up Google or Youdao")


# The storage walk is the one source that is not a free read: usage is
# remembered per account for a few minutes, and only computed at all when
# the account is under a quota.
_USAGE_TTL = 10 * 60
_usage: dict[str, tuple[float, int]] = {}
_usage_lock = threading.Lock()


def _usage_bytes(username: str) -> int:
    now = time.monotonic()
    with _usage_lock:
        known = _usage.get(username)
        if known and now - known[0] < _USAGE_TTL:
            return known[1]
    used = server_settings.usage_bytes(username)
    with _usage_lock:
        _usage[username] = (now, used)
    return used


def forget_usage(username: str | None = None) -> None:
    """Drop the remembered usage (tests; a caller that just changed it)."""
    with _usage_lock:
        if username is None:
            _usage.clear()
        else:
            _usage.pop(username, None)


@source()
def storage_nearly_full(username):
    """The account's personal storage past nine tenths of its quota (warn)
    or full (error). Fingerprint: the threshold crossed, so each fires once
    until the pane is seen — and again after the usage drops and climbs
    back."""
    quota_mb = server_settings.user_limits(username).get("quota_mb") or 0
    if not quota_mb:
        return None
    used = _usage_bytes(username)
    share = used / (quota_mb * MB)
    if share >= 1:
        return Notice("storage", "full", "error", "account",
                      f"Your storage is full ({used // MB} of {quota_mb} MB used)")
    if share >= 0.9:
        return Notice("storage", "90", "warn", "account",
                      f"Your storage is nearly full ({used // MB} of {quota_mb} MB used)")
    return None


def seen_map(username: str) -> dict:
    value, _ = get_pref(username, NOTICES_SEEN_PREF_KEY)
    return value if isinstance(value, dict) else {}


def for_user(username: str, is_admin: bool) -> list[dict]:
    """The unresolved notices of an account, strongest tone first."""
    found = [notice for fn, admin_only in _SOURCES if is_admin or not admin_only
             if (notice := fn(username)) is not None]
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
