"""What this server does with a linked account's Gamma Cloud grant
(docs/dev/cloud_accounts.md, "The Gamma side"). All of it rides on
``cloud_auth.access_token_for`` and stays off unless cloud sign-in is on
and the account has a linked identity holding a token: a self-hosted
server without cloud sign-in makes no call from here.

- **The grant check** (``check_all``, at startup and hourly from the app
  lifespan): every identity holding a refresh token is refreshed. A
  refusal (``invalid_grant``) ends the sessions its cloud sign-ins minted
  (``cloud_auth._grant_refused``); a refresh that fails for any other reason
  is tried again an hour later and does nothing else, so a laptop without
  network stays signed in.
- **The preference profile** (``sync_profile``): the account-wide
  ``profile`` pref against the account server's ``profile`` key, merged
  preference by preference against the copy both sides last agreed on
  (``profile-base``): a preference changed on one side only takes that
  side's value, one changed on both takes the newer profile's. The first
  sync of an account whose two copies differ has no base, and waits for
  the person's choice (state "choose"): merge (the defaults as the base),
  keep the cloud's, or keep this server's — Settings asks Fetch from
  cloud / Push to cloud in a dialog; merge stays an API-only action, and
  Sync now is the plain merge. Synced on a cloud sign-in,
  before the browser loads; when a browser reads the profile, at most once
  a minute (``sync_if_stale``); on every check; and a few seconds after a
  change made here (``profile_changed``). A push the account server
  refuses as older (409) reads both sides again. A failed push is retried
  by the next check. The AI provider entries and the active entry
  (``ai-settings``, ``ai-provider``) never sync. The last outcome per
  account is kept in memory for Settings (``profile_status``).
- **The server list** (``register_server``): this server's address under
  the person's account, posted on sign-in and on every check (an upsert
  that also refreshes ``last_seen_at``), removed on unlink or deletion
  (``release``).

Failures here are warnings in the server log, never errors to the person.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

from . import cloud_auth
from .cloud_auth import PROVIDER, CloudAuthError
from .db import (PROFILE_BASE_PREF_KEY, PROFILE_PREF_KEY, connect_users_db, format_stamp, get_pref, page_now,
                 parse_stamp, replace_profile_if, restamp_pref, set_pref)
from .logbuf import log

CHECK_INTERVAL = 3600   # seconds between grant checks (the app lifespan runs check_all)
PUSH_DELAY = 5.0        # seconds a profile change settles before it is pushed
SIGN_IN_TIMEOUT = 5     # the pull a sign-in, or a browser reading the profile, waits for
READ_SYNC_EVERY = 60    # seconds: a browser reading the profile syncs it at most this often
PROFILE_PATH = "/api/me/prefs/" + PROFILE_PREF_KEY


def _background(fn) -> None:
    """Run ``fn`` on a daemon thread (tests run it inline)."""
    threading.Thread(target=fn, name="cloud-sync", daemon=True).start()


def _call(method: str, path: str, token: str, body=None, *, timeout: float = cloud_auth.HTTP_TIMEOUT) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    return cloud_auth._http(cloud_auth.settings()["issuer"] + path, data=data, headers=headers, method=method,
                            timeout=timeout)


def _failed(user_id: str, what: str, e: CloudAuthError) -> None:
    if e.status == 401:  # the cached access token died with its grant; the next call refreshes
        cloud_auth.forget_access(cloud_auth.grant_of(user_id)[0])
    log.warning(f"cloud: could not {what} for {cloud_auth.name_of(user_id)} (tried again at the next check): {e}")


# --- times ------------------------------------------------------------------------

def _ms(ts) -> datetime | None:
    """An ISO time cut to the millisecond, the account server's precision."""
    if not isinstance(ts, str) or not ts:
        return None
    t = parse_stamp(ts.strip())
    if t is None:
        return None
    t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
    return t.replace(microsecond=t.microsecond // 1000 * 1000)


# --- the sync status -------------------------------------------------------------
# The last profile sync outcome per account, in memory only (Settings reads
# it through /api/auth/cloud/sync-status): "synced" (the last pull or push
# agreed), "pending" (a push is scheduled, or failed and waits for the next
# check; ``error`` then says why), "error" (the last attempt failed),
# "choose" (the first sync found two different copies and waits for the
# person to merge or keep one). "off" is never stored: ``profile_status``
# works it out on every read.

_status: dict[str, dict] = {}
_status_lock = threading.Lock()
UNREACHABLE = "Gamma Cloud could not be reached."


def _note(user_id: str, state: str, error: str = "") -> None:
    with _status_lock:
        _status[user_id] = {"state": state, "at": page_now(), "error": error}


def _note_failure(user_id: str, error) -> None:
    """A failed attempt: a pending push stays pending (with the reason),
    anything else becomes "error"."""
    with _status_lock:
        was = _status.get(user_id, {}).get("state")
    _note(user_id, "pending" if was == "pending" else "error", str(error) or UNREACHABLE)


def profile_status(user_id: str) -> dict:
    """``{state, at, error}`` of the account's profile sync: "off" without
    cloud sign-in or without an identity holding a token; an account that
    syncs but has no outcome yet (a restart, before the first check) is
    "pending". No network."""
    if not syncs(user_id):
        return {"state": "off", "at": "", "error": ""}
    with _status_lock:
        known = _status.get(user_id)
    return dict(known) if known else {"state": "pending", "at": "", "error": ""}


# --- the preference profile -----------------------------------------------------

class NothingToFetch(Exception):
    """Fetch from cloud while Gamma Cloud holds no profile."""


_MISSING = object()


def _same(a, b) -> bool:
    """Equal JSON values (``True`` is not ``1``); ``_MISSING`` only equals itself."""
    if a is _MISSING or b is _MISSING:
        return a is b
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def merge_profiles(base: dict, local: dict, remote: dict, *, local_newer: bool) -> dict:
    """Three-way merge of two profiles against the copy they both started
    from, one preference at a time: changed on one side only → that side's
    value (gone when that side dropped it); changed on both to different
    values → the newer profile's."""
    out = {}
    names = [*local, *(k for k in remote if k not in local), *(k for k in base if k not in local and k not in remote)]
    for name in names:
        was, here, there = (side.get(name, _MISSING) for side in (base, local, remote))
        if _same(here, there) or _same(there, was):
            value = here
        elif _same(here, was):
            value = there
        else:
            value = here if local_newer else there
        if value is not _MISSING:
            out[name] = value
    return out


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_tried: dict[str, float] = {}  # user_id -> time.monotonic() of the last sync attempt


def _lock_of(user_id: str) -> threading.Lock:
    """One sync per account at a time: the timer, the check and a browser's read may meet."""
    with _locks_guard:
        return _locks.setdefault(user_id, threading.Lock())


def _stamp_past(t: datetime | None) -> datetime:
    """A push time the account server takes as newer than ``t``: now, or a
    millisecond after ``t`` when that clock runs ahead of this one."""
    now = datetime.now(timezone.utc)
    now = now.replace(microsecond=now.microsecond // 1000 * 1000)
    return max(now, t + timedelta(milliseconds=1)) if t else now


def sync_profile(user_id: str, token: str | None = None, *, timeout: float = cloud_auth.HTTP_TIMEOUT,
                 resolve: str = "", defaults: dict | None = None) -> str:
    """Reconcile the account's profile with the account server's.

    ``resolve`` "" is the automatic sync: a merge against the last agreed
    copy, or "choose" when there is none and the two copies differ. The
    person's answers: "merge" (against the last agreed copy, else
    ``defaults`` — the web app's default profile), "fetch" (the cloud's
    copy replaces this one; ``NothingToFetch`` when there is none), "push"
    (this copy replaces the cloud's).

    Returns "pulled" (this copy changed), "pushed" (the cloud's changed),
    "merged" (both), "same", "choose", or "" when nothing could be done (no
    token, a failure — logged and noted)."""
    token = token or cloud_auth.access_token_for(user_id)
    if not token:
        if syncs(user_id):  # offline, or the refresh failed (logged)
            _note_failure(user_id, UNREACHABLE)
        return ""
    with _lock_of(user_id):
        _tried[user_id] = time.monotonic()
        for _ in range(3):  # the cloud or this copy moved on meanwhile: read both again
            outcome = _reconcile(user_id, token, timeout, resolve, defaults or {})
            if outcome is not None:
                return outcome
    log.warning(f"cloud: the preference profile of {cloud_auth.name_of(user_id)} kept changing while it synced "
                "(tried again at the next check)")
    _note_failure(user_id, "The settings kept changing while they synced.")
    return ""


def _reconcile(user_id: str, token: str, timeout: float, resolve: str, defaults: dict) -> str | None:
    """One round of ``sync_profile``; None = read both sides again."""
    local, local_at = get_pref(user_id, PROFILE_PREF_KEY)
    local = local if isinstance(local, dict) else None
    try:
        remote = _call("GET", PROFILE_PATH, token, timeout=timeout)
    except CloudAuthError as e:
        if e.status != 404:
            _failed(user_id, "read the preference profile", e)
            _note_failure(user_id, e)
            return ""
        remote = {}
    cloud = remote.get("value") if isinstance(remote.get("value"), dict) else None
    cloud_at = _ms(remote.get("updated_at")) if cloud is not None else None
    base = _base_of(user_id)

    if resolve == "fetch":
        if cloud is None:
            raise NothingToFetch()
        target = cloud
    elif resolve == "push":
        target = local if local is not None else {}
    elif cloud is None or local is None:
        target = local if local is not None else cloud
        if target is None:
            _note(user_id, "synced")
            return "same"
    elif base is None and resolve != "merge" and not _same(local, cloud):
        _note(user_id, "choose")
        return "choose"
    else:
        here_at = _ms(local_at)
        newer = bool(here_at and (not cloud_at or here_at > cloud_at))
        if base is not None:
            target = merge_profiles(base, local, cloud, local_newer=newer)
        else:  # a first merge: an entry a side lacks is at its default there, not dropped
            target = merge_profiles(defaults, {**defaults, **local}, {**defaults, **cloud}, local_newer=newer)
    changes_here = local is None or not _same(target, local)

    if resolve != "push" and cloud is not None and _same(target, cloud):
        # the cloud's copy as it stands: nothing to send
        if changes_here and not replace_profile_if(user_id, target, local_at, format_stamp(cloud_at)):
            return None
        _agreed(user_id, target)
        return "pulled" if changes_here else "same"

    try:
        answer = _call("PUT", PROFILE_PATH, token, {"value": target, "updated_at": format_stamp(_stamp_past(cloud_at))},
                       timeout=timeout)
    except CloudAuthError as e:
        if e.status == 409:  # another server pushed meanwhile
            return None
        _failed(user_id, "push the preference profile", e)
        _note(user_id, "pending", str(e) or UNREACHABLE)
        return ""
    # keep the account server's time here too (clamped, or cut to the
    # millisecond), so both copies carry one version
    stored_at = format_stamp(_ms(answer.get("updated_at")) or _stamp_past(cloud_at))
    if changes_here:
        if not replace_profile_if(user_id, target, local_at, stored_at):
            return None  # a change landed here meanwhile: merge it against the old base
    elif local_at:
        restamp_pref(user_id, PROFILE_PREF_KEY, local_at, stored_at)
    _agreed(user_id, target)
    return "merged" if changes_here else "pushed"


def _agreed(user_id: str, value: dict) -> None:
    """Both copies now hold ``value``: the base of the next merge, kept
    with the cloud account it was agreed with."""
    set_pref(user_id, PROFILE_BASE_PREF_KEY, {"subject": cloud_auth.grant_of(user_id)[0], "profile": value})
    _note(user_id, "synced")


def _base_of(user_id: str) -> dict | None:
    """The last agreed profile, or None: never synced, or agreed with
    another cloud account than the one linked now (an unlink, then a link
    to someone else's)."""
    stored, _ = get_pref(user_id, PROFILE_BASE_PREF_KEY)
    if not isinstance(stored, dict) or not isinstance(stored.get("profile"), dict):
        return None
    return stored["profile"] if stored.get("subject") == cloud_auth.grant_of(user_id)[0] else None


def sync_if_stale(user_id: str) -> None:
    """A browser is reading the profile (a tab opened or refocused): sync
    first when the last attempt is more than ``READ_SYNC_EVERY`` seconds
    old, so a change made on another server shows up. Never raises."""
    try:
        last = _tried.get(user_id)
        if (last is not None and time.monotonic() - last < READ_SYNC_EVERY) or not syncs(user_id):
            return
        _tried[user_id] = time.monotonic()
        sync_profile(user_id, timeout=SIGN_IN_TIMEOUT)
    except Exception as e:
        log.exception(f"cloud: syncing the preference profile of {cloud_auth.name_of(user_id)} on read failed")
        _note_failure(user_id, e)


_timers: dict[str, threading.Timer] = {}
_timers_lock = threading.Lock()


def syncs(user_id: str) -> bool:
    """Whether the account's profile follows it through Gamma Cloud: cloud
    sign-in on and an identity holding a token. No network."""
    if not cloud_auth.settings()["enabled"]:
        return False
    subject, refresh = cloud_auth.grant_of(user_id)
    return bool(subject and (refresh or cloud_auth.cached_access(subject)))


def profile_changed(user_id: str) -> None:
    """The hook for a profile change made here (``set_pref``,
    ``patch_profile``): sync it once changes have settled for
    ``PUSH_DELAY`` seconds, on a timer thread. Never raises into the
    request that stored the change."""
    try:
        if not syncs(user_id):
            return
    except Exception:
        log.exception("cloud: could not check whether a profile change syncs")
        return
    with _status_lock:
        choosing = _status.get(user_id, {}).get("state") == "choose"
    if not choosing:  # still waiting for the person's choice: nothing will be sent
        _note(user_id, "pending")
    with _timers_lock:
        old = _timers.get(user_id)
        if old:
            old.cancel()
        timer = threading.Timer(PUSH_DELAY, _push_settled, args=(user_id,))
        timer.daemon = True
        _timers[user_id] = timer
        timer.start()


def _push_settled(user_id: str) -> None:
    with _timers_lock:
        if _timers.get(user_id) is threading.current_thread():
            del _timers[user_id]
    try:
        sync_profile(user_id)
    except Exception as e:
        log.exception(f"cloud: pushing the preference profile of {cloud_auth.name_of(user_id)} failed")
        _note_failure(user_id, e)


# --- the server list ----------------------------------------------------------------

def register_server(user_id: str, token: str | None = None, url: str | None = None) -> bool:
    """Put this server on the person's server list (an upsert, which also
    refreshes its ``last_seen_at``). Nothing without an address to give."""
    url = cloud_auth.server_url() if url is None else url
    if not url:
        return False
    token = token or cloud_auth.access_token_for(user_id)
    if not token:
        return False
    try:
        _call("POST", "/api/me/servers", token, {"url": url, "name": cloud_auth.server_name(url)})
    except CloudAuthError as e:
        _failed(user_id, "register this server on the Gamma Cloud account", e)
        return False
    return True


def release(subject: str, refresh_token: str) -> None:
    """This server stops holding an identity's grant (an unlink, an account
    deleted): take this server off the person's server list, then revoke
    the refresh token. Best effort; failures are warnings."""
    if not cloud_auth.settings()["enabled"]:
        return
    token, held = cloud_auth.cached_access(subject), refresh_token
    url = cloud_auth.server_url()
    if url and not token and held:
        try:
            tokens = cloud_auth.refresh_grant(held)
            token, held = tokens.get("access_token", ""), tokens.get("refresh_token") or held
        except CloudAuthError as e:
            log.warning(f"cloud: could not refresh a grant to leave the server list: {e}")
    if url and token:
        try:
            _call("DELETE", "/api/me/servers", token, {"url": url})
        except CloudAuthError as e:
            log.warning(f"cloud: could not take this server off a Gamma Cloud account's list: {e}")
    cloud_auth.forget_access(subject)
    cloud_auth.revoke_refresh(held)


def release_later(subject: str, refresh_token: str) -> None:
    if subject:
        _background(lambda: release(subject, refresh_token))


# --- sign-in and the grant check ----------------------------------------------------

def signed_in(request, user_id: str, subject: str, tokens: dict) -> None:
    """After a cloud sign-in: cache its access token, pull the profile (the
    browser loads next, and must see the synced one) and register this
    server in the background."""
    cloud_auth.remember_access(subject, tokens)
    token = cloud_auth.cached_access(subject)
    if not token:
        return
    try:
        sync_profile(user_id, token, timeout=SIGN_IN_TIMEOUT)
    except Exception:
        log.exception(f"cloud: the profile pull of {cloud_auth.name_of(user_id)}'s sign-in failed")
    url = cloud_auth.server_url(request)
    if url:
        _background(lambda: register_server(user_id, token, url))


def check(user_id: str) -> str:
    """One account's grant check: refresh, then sync the profile and
    refresh the server list entry. "ok", or "" when no token came back
    (offline — nothing happens — or revoked, handled by cloud_auth)."""
    token = cloud_auth.access_token_for(user_id, fresh=True)
    if not token:
        if syncs(user_id):  # offline: the grant stays, the profile waits
            _note_failure(user_id, UNREACHABLE)
        return ""
    sync_profile(user_id, token)
    register_server(user_id, token)
    return "ok"


def check_all() -> dict:
    """The grant check over every identity holding a refresh token: ``{account
    id: outcome}``."""
    if not cloud_auth.settings()["enabled"]:
        return {}
    with connect_users_db() as conn:
        accounts = [r[0] for r in conn.execute(
            "SELECT user_id FROM identities WHERE provider = ? AND refresh_token != '' ORDER BY user_id", (PROVIDER,))]
    done = {}
    for user_id in accounts:
        try:
            done[user_id] = check(user_id)
        except Exception as e:
            log.exception(f"cloud: the grant check of {cloud_auth.name_of(user_id)} failed")
            _note_failure(user_id, e)
            done[user_id] = ""
    return done
