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

Each notice's sentence travels as a stable ``message`` (the English
template, also the key of the frontend's catalog, ``app/notices.js``) plus
its ``params``, so the browser shows it in the interface language; ``title``
is the same sentence filled in, for API readers and older frontends.

Sources are plain functions ``fn(user_id) -> Notice | None`` (the
account's id) registered with ``@source``; each must be cheap — a cached,
in-memory or small database read — because ``for_user`` runs them on every
poll of ``GET /api/notices``. Admin-only sources are skipped for everyone
else.
The one network call, the update check, sits behind
``version.check``'s six-hour cache; the one directory walk, the
storage usage, runs only for an account under a quota and is remembered
for a while.
"""

import hashlib
import re
import threading
import time
from dataclasses import asdict, dataclass, field

from . import (backup_schedule, cloud_sync, hosted, integrity, logbuf, server_settings, sync_engine,
               translate_engines, version)
from .db import NOTICES_SEEN_PREF_KEY, connect_users_db, get_pref, update_pref
from .server_settings import MB

TONES = ("info", "warn", "error")
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_MAX_SEEN = 64


@dataclass(frozen=True)
class Notice:
    id: str
    fingerprint: str
    tone: str  # one of TONES; the dot takes the strongest
    pane: str  # the Settings pane that resolves it
    title: str  # the sentence in English, `message` filled in
    message: str = ""  # the sentence's template: a catalog key on the client
    params: dict = field(default_factory=dict)


def notice(notice_id: str, fingerprint: str, tone: str, pane: str, message: str, **params) -> Notice:
    """A Notice whose sentence is `message` with `params` (``{name}``
    placeholders). Every message must be listed in the frontend's
    NOTICE_MESSAGES (tests/notices.test.mjs compares the two)."""
    return Notice(notice_id, fingerprint, tone, pane, message.format(**params), message, params)


_SOURCES: list[tuple[callable, bool]] = []


def source(*, admin_only=False):
    """Register a notice source: ``fn(user_id) -> Notice | None``."""
    def wrap(fn):
        _SOURCES.append((fn, admin_only))
        return fn
    return wrap


@source(admin_only=True)
def update_available(_user_id):
    """A newer GitHub release than this build, or for a ``-dev`` build a
    newer build of its branch (nothing for a checkout, an air-gapped server
    or an unreachable GitHub)."""
    update = version.check()["update"]
    if not update:
        return None
    return notice("update", update["version"], "warn", "server",
                  "Gamma v{version} is available — this server runs v{current}",
                  version=update["version"], current=version.VERSION)


@source(admin_only=True)
def log_errors(_user_id):
    """Errors logged since the account last looked at the server log. The
    fingerprint is the start time plus the newest error's seq: a restart
    resets both, so an old ack never covers a new error."""
    seq = logbuf.last_seq("error")
    if not seq:
        return None
    started = version.STARTED_AT.isoformat()
    return notice("log-errors", f"{started}:{seq}", "error", "server", "New errors in the server log")


@source(admin_only=True)
def database_damage(_user_id):
    """Database files whose latest integrity check failed — a snapshot's,
    or the admin's "Check databases" (gamma/integrity.py: one small JSON
    file). Fingerprint: the files and when each was found, so a new
    finding brings it back; a file that passes a later check drops out."""
    bad = integrity.failures()
    if not bad:
        return None
    mark = hashlib.sha1(",".join(f"{rel}:{v.get('at', '')}" for rel, v in sorted(bad.items()))
                        .encode("utf-8")).hexdigest()[:16]
    if len(bad) == 1:
        return notice("db-damage", mark, "error", "server", "A database check found damage in {file}",
                      file=next(iter(bad)))
    return notice("db-damage", mark, "error", "server", "A database check found damage in {n} database files",
                  n=len(bad))


@source()
def backup_failed(user_id):
    """The account's backup tasks whose last run failed (Settings →
    Backups shows the error). Another failed run, of any of them, is a new
    fingerprint."""
    failed = [t for t in backup_schedule.list_tasks(user_id) if t.get("state") == "failed"]
    if not failed:
        return None
    mark = ",".join(f"{t['id'][:12]}:{t.get('last_run') or ''}" for t in sorted(failed, key=lambda t: t["id"]))
    if len(failed) == 1:
        return notice("backup-failed", mark, "error", "backups", "The backup task “{name}” failed",
                      name=failed[0]["name"])
    return notice("backup-failed", mark, "error", "backups", "{n} backup tasks failed", n=len(failed))


def _conflict_marks(user_id, publications):
    """Per clone (or per publication), the open conflict count and newest
    conflict id, folded into one short digest (a fingerprint is capped at
    200 characters, which a dozen clones with conflicts would pass); a
    mirror with a page filter is a publication."""
    marks, total = [], 0
    for mirror in sync_engine.list_mirrors(user_id):
        if (mirror.get("page_filter") is not None) != publications:
            continue
        count, newest = sync_engine.open_conflict_mark(mirror["workspace_id"])
        if count:
            marks.append(f"{mirror['workspace_id']}:{count}:{newest}")
            total += count
    return hashlib.sha1(",".join(marks).encode("utf-8")).hexdigest()[:16], total


@source()
def mirror_conflicts(user_id):
    """Open conflicts in the clones the account owns (Settings → Workspaces →
    Clones). Fingerprint: per clone, the count and the newest
    conflict — a new one brings the notice back, resolving old ones does
    not."""
    mark, total = _conflict_marks(user_id, publications=False)
    if not total:
        return None
    return notice("mirror-conflicts", mark, "warn", "workspaces",
                  "{n} sync conflict to look at in your clones" if total == 1
                  else "{n} sync conflicts to look at in your clones", n=total)


@source()
def publish_conflicts(user_id):
    """Open conflicts in the pages the account publishes to Gamma Cloud
    (Settings → Account & sync → Publishing), fingerprinted like the clones'."""
    mark, total = _conflict_marks(user_id, publications=True)
    if not total:
        return None
    return notice("publish-conflicts", mark, "warn", "account",
                  "{n} sync conflict to look at in your published pages" if total == 1
                  else "{n} sync conflicts to look at in your published pages", n=total)


@source()
def cloud_sync_failed(user_id):
    """The account's Gamma Cloud sync in its error state (the Account
    pane's cloud row says why)."""
    status = cloud_sync.profile_status(user_id)
    if status.get("state") != "error":
        return None
    error = (status.get("error") or "").strip().rstrip(".")
    if error:
        return notice("cloud-sync", status.get("at") or "", "warn", "account",
                      "Gamma Cloud sync failed: {error}", error=error)
    return notice("cloud-sync", status.get("at") or "", "warn", "account", "Gamma Cloud sync failed")


@source()
def cloud_sync_choice(user_id):
    """The first settings sync with Gamma Cloud found two different copies
    and waits for the person to merge them or keep one (the Account pane)."""
    if cloud_sync.profile_status(user_id).get("state") != "choose":
        return None
    return notice("cloud-sync-choice", "choose", "warn", "account",
                  "Your settings here and on Gamma Cloud differ: choose which to keep")


@source()
def hosted_plan(user_id):
    """A hosted server's plan (gamma/hosted.py) in its grace period (warn,
    admins: the server becomes read-only on ``grace_until``) or read-only
    (error, everyone; an admin's points at the Server pane's Plan rows, a
    member's at the Account pane). Fingerprint: the status and since when
    it holds, so each episode is seen once."""
    plan = hosted.limits()
    if not plan or not (plan["read_only"] or plan["status"] == "grace"):
        return None
    with connect_users_db() as conn:
        row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    admin = bool(row and row[0])
    mark = f"{plan['status']}:{plan['status_since']}"
    if plan["read_only"]:
        return notice("hosted", mark, "error", "server" if admin else "account",
                      "This server is read-only: you can still read and export everything")
    if not admin:
        return None
    if plan["grace_until"]:
        return notice("hosted", mark, "warn", "server",
                      "Payment for this server failed: it becomes read-only on {date}", date=plan["grace_until"][:10])
    return notice("hosted", mark, "warn", "server", "Payment for this server failed: it becomes read-only soon")


@source()
def free_translate_failing(user_id):
    """Microsoft's free translation endpoint keeps failing for this account
    (translate_engines' in-memory streak); gone after one success."""
    failing = translate_engines.free_failing(user_id)
    if not failing:
        return None
    return notice("free-translate", failing["since"], "warn", "translation",
                  "Microsoft's free translation keeps failing — set up Google or Youdao")


# The storage walk is the one source that is not a free read: usage is
# remembered per account for a few minutes, and only computed at all when
# the account is under a quota.
_USAGE_TTL = 10 * 60
_usage: dict[str, tuple[float, int]] = {}
_usage_lock = threading.Lock()


def _usage_bytes(user_id: str) -> int:
    now = time.monotonic()
    with _usage_lock:
        known = _usage.get(user_id)
        if known and now - known[0] < _USAGE_TTL:
            return known[1]
    used = server_settings.usage_bytes(user_id)
    with _usage_lock:
        _usage[user_id] = (now, used)
    return used


def forget_usage(user_id: str | None = None) -> None:
    """Drop the remembered usage (the tests)."""
    with _usage_lock:
        if user_id is None:
            _usage.clear()
        else:
            _usage.pop(user_id, None)


@source()
def storage_nearly_full(user_id):
    """The account's personal storage past nine tenths of its quota (warn)
    or full (error). Fingerprint: the threshold crossed, so each fires once
    until the pane is seen — and again after the usage drops and climbs
    back."""
    quota_mb = server_settings.user_limits(user_id).get("quota_mb") or 0
    if not quota_mb:
        return None
    used = _usage_bytes(user_id)
    share = used / (quota_mb * MB)
    if share >= 1:
        return notice("storage", "full", "error", "account",
                      "Your storage is full ({used} of {quota} MB used)", used=used // MB, quota=quota_mb)
    if share >= 0.9:
        return notice("storage", "90", "warn", "account",
                      "Your storage is nearly full ({used} of {quota} MB used)", used=used // MB, quota=quota_mb)
    return None


def seen_map(user_id: str) -> dict:
    value, _ = get_pref(user_id, NOTICES_SEEN_PREF_KEY)
    return value if isinstance(value, dict) else {}


def for_user(user_id: str, is_admin: bool) -> list[dict]:
    """The unresolved notices of an account, strongest tone first."""
    found = [notice for fn, admin_only in _SOURCES if is_admin or not admin_only
             if (notice := fn(user_id)) is not None]
    if not found:
        return []
    seen = seen_map(user_id)
    return [asdict(n) for n in sorted(found, key=lambda n: -TONES.index(n.tone))
            if seen.get(n.id) != n.fingerprint]


def mark_seen(user_id: str, notice_id: str, fingerprint: str) -> None:
    """Record that the account has seen this fingerprint of the notice."""
    if not _ID_RE.match(notice_id or "") or not isinstance(fingerprint, str) or len(fingerprint) > 200:
        raise ValueError("invalid notice")

    def change(value):  # one transaction: two panes marked at once both stay seen
        seen = value if isinstance(value, dict) else {}
        seen[notice_id] = fingerprint
        if len(seen) > _MAX_SEEN:  # never grows past the sources that exist; a guard, not a policy
            seen = dict(list(seen.items())[-_MAX_SEEN:])
        return seen
    update_pref(user_id, NOTICES_SEEN_PREF_KEY, change)
