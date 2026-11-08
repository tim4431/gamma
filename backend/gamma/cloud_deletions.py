"""On the share host: a Gamma Cloud account that was deleted takes its
library here with it (docs/dev/cloud_accounts.md "A deleted account on the
share host").

Every hour (``tick``, at startup and then from the app lifespan) the share
host asks the account server for the accounts deleted since it last asked
(``GET /api/share-host/deletions``, signed with this server's client id and
secret as Basic auth). Each that has an account here (an identity with its
subject: one that signed in, or one a publish exchange made) is deleted by
the job an admin's delete runs (``workspaces.start_account_delete``, kind
``account-delete``), owned by the server itself (``jobs.WORKSPACE``): its
personal workspaces and the shared ones only it owned go with their files
and the shares in them, so its published pages and its page host stop
answering, and it leaves the shared workspaces it was a member of. Each
workspace that goes is first copied to backups/deleted/, which keeps the
copy ``ws_backup.DELETED_KEEP_DAYS`` days.

The cursor (``cloud_deletions_after`` in the settings KV) moves past every
deletion a tick reads. Each that has an account here goes on the list of
those due (``cloud_deletions_pending``, {subject: audit entry id}) and stays
there while an account here is linked to it, so a job that failed, or one
that waits its turn, is tried again at the next tick, and an account that
stays (the last admin) never holds the cursor back from the deletions
after it. Before one is started or kept, the account server is asked again
whether that deletion still stands (``_stands``): an account restored
since is taken off the list. At most ``AT_ONCE`` run at a time, and one
that finishes starts the next tick at once. An account that is this
server's last admin is kept, with a warning in the log at every tick;
unlinking an account from its Gamma Cloud identity (``manage.py
unlink-identity``) keeps it for good.

Only a share host with its client secret asks: cloud sign-in on, *Accept
published pages* on. Failures are warnings in the server log.
"""

import base64
import json
import threading

from . import cloud_auth, jobs, workspaces
from .cloud_auth import PROVIDER, CloudAuthError
from .db import connect_users_db
from .logbuf import log
from .server_settings import _get_raw, _set_raw

TICK_S = 3600                    # seconds between asks (the app lifespan runs tick)
AT_ONCE = 2                      # account deletes of this kind running at a time
MAX_PAGES = 20                   # pages of deletions one tick reads
CURSOR_KEY = "cloud_deletions_after"
PENDING_KEY = "cloud_deletions_pending"   # {subject: audit entry id}: the deletions due here
PATH = "/api/share-host/deletions"

_lock = threading.Lock()


def _background(fn) -> None:
    """Run ``fn`` on a daemon thread (tests run it inline)."""
    threading.Thread(target=fn, name="cloud-deletions", daemon=True).start()


def applies() -> bool:
    """Whether this server asks: a share host with a client secret."""
    cfg = cloud_auth.settings()
    return bool(cfg["enabled"] and cfg["share_host"] and cloud_auth.client_secret())


def _cursor() -> int:
    try:
        return max(0, int(_get_raw(CURSOR_KEY, "0") or 0))
    except ValueError:
        return 0


def _pending() -> dict:
    try:
        raw = json.loads(_get_raw(PENDING_KEY, "{}") or "{}")
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, int) and not isinstance(v, bool)}


def _save(cursor: int, pending: dict) -> None:
    _set_raw(CURSOR_KEY, str(cursor))
    _set_raw(PENDING_KEY, json.dumps(pending, sort_keys=True))


def _fetch(cfg: dict, after: int) -> dict:
    """One page of the account server's deletions after ``after``."""
    pair = base64.b64encode(f"{cfg['client_id']}:{cloud_auth.client_secret()}".encode()).decode()
    out = cloud_auth._http(f"{cfg['issuer']}{PATH}?after={after}", headers={"Authorization": f"Basic {pair}"})
    items, nxt = out.get("deletions"), out.get("next")
    if (not isinstance(items, list) or not isinstance(nxt, int) or isinstance(nxt, bool)
            or not all(isinstance(d, dict) and isinstance(d.get("id"), int) and isinstance(d.get("account_id"), str)
                       for d in items)):
        raise CloudAuthError("the account server's list of deleted accounts is not one")
    return {"deletions": items, "next": nxt}


def _stands(cfg: dict, subject: str, entry: int) -> bool:
    """Whether the account server still lists deletion ``entry`` of
    ``subject``: it leaves out an account restored since."""
    first = _fetch(cfg, entry - 1)["deletions"][:1]
    return bool(first) and first[0]["id"] == entry and first[0]["account_id"] == subject


def _account_of(subject: str) -> tuple | None:
    """(user id, username, is_guest) of the account here linked to the
    Gamma Cloud account ``subject``, or None."""
    with connect_users_db() as conn:
        return conn.execute(
            "SELECT u.id, u.username, u.is_guest FROM identities i JOIN users u ON u.id = i.user_id "
            "WHERE i.provider = ? AND i.subject = ?", (PROVIDER, subject)).fetchone()


def _allowed(user_id: str, subject: str):
    """The check the job makes as it runs and at its point of no return:
    the account is still linked to the deleted Gamma Cloud account, and it
    is not this server's last admin (ValueError, the job's message)."""
    def check(conn) -> None:
        if not conn.execute("SELECT 1 FROM identities WHERE provider = ? AND subject = ? AND user_id = ?",
                            (PROVIDER, subject, user_id)).fetchone():
            raise ValueError("the account is no longer linked to the deleted Gamma Cloud account, so it is kept")
        admin = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
        if admin and admin[0] and conn.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_guest = 0").fetchone()[0] <= 1:
            raise ValueError("it is this server's last admin, so it is kept")
    return check


def _ended(state: str) -> None:
    if state == "done":  # the next one in line need not wait for the hour
        _background(tick)


def tick() -> dict:
    """Ask for the deletions since the cursor and start the account deletes
    they call for: ``{started, running, waiting, kept, after}`` (usernames,
    counts, the usernames kept, the cursor now); ``{}`` where this server
    does not ask, ``{error}`` when the account server could not be read."""
    if not applies():
        return {}
    with _lock:
        try:
            return _tick(cloud_auth.settings())
        except CloudAuthError as e:
            log.warning(f"cloud: could not read the deleted Gamma Cloud accounts: {e}")
            return {"error": str(e)}


def _tick(cfg: dict) -> dict:
    pending, after = _pending(), _cursor()
    for _ in range(MAX_PAGES):
        page = _fetch(cfg, after)
        for d in page["deletions"]:
            if _account_of(d["account_id"]):
                pending[d["account_id"]] = max(d["id"], pending.get(d["account_id"], 0))
        end = not page["deletions"] or page["next"] <= after
        after = max(after, page["next"])
        if end:
            break
    due = {}   # user id -> (subject, entry, account), oldest deletion first
    for subject, entry in sorted(pending.items(), key=lambda kv: kv[1]):
        found = _account_of(subject)
        if found:
            due.setdefault(found[0], (subject, entry, found))
    pending = {subject: entry for subject, entry, _ in due.values()}
    _save(after, pending)   # what was read stays read, even if a check below cannot reach the account server
    running, started, kept, waiting = 0, [], [], 0
    idle = []
    for user_id, (subject, entry, found) in due.items():
        last = jobs.latest(jobs.WORKSPACE, "account-delete", user_id)
        if last and last["state"] in jobs.ACTIVE:
            running += 1
        else:
            idle.append((user_id, subject, entry, found))
    for user_id, subject, entry, (_, username, guest) in idle:
        if running >= AT_ONCE:
            waiting += 1
            continue
        if not _stands(cfg, subject, entry):
            log.info(f"cloud: {username}'s Gamma Cloud account was restored, so the account here stays")
            del pending[subject]
            continue
        allowed = _allowed(user_id, subject)
        try:
            workspaces.check_account_deletable(user_id)
            with connect_users_db() as conn:
                allowed(conn)
        except ValueError as e:
            log.warning(f"cloud: {username}'s Gamma Cloud account was deleted, but the account here was not: {e}")
            kept.append(username)
            continue
        workspaces.start_account_delete(user_id, username, owner=jobs.WORKSPACE, by="", allowed=allowed,
                                        guest=bool(guest), ended=_ended,
                                        title=f"Delete account {username}, whose Gamma Cloud account was deleted")
        log.info(f"cloud: deleting {username} here, since its Gamma Cloud account was deleted")
        running += 1
        started.append(username)
    _save(after, pending)
    return {"started": started, "running": running, "waiting": waiting, "kept": kept, "after": after}
