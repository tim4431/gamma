"""What this server does with a linked account's Gamma Cloud grant
(docs/dev/cloud_accounts.md, "The Gamma side"). All of it rides on
``cloud_auth.access_token_for`` and stays off unless cloud sign-in is on
and the account has a linked identity holding a token: a self-hosted
server without cloud sign-in makes no call from here.

- **The grant check** (``check_all``, at startup and hourly from
  ``lifespan``): every identity holding a refresh token is refreshed. A
  refusal (``invalid_grant``) ends the sessions its cloud sign-ins minted
  (``cloud_auth._grant_refused``); a refresh that fails for any other reason
  is tried again an hour later and does nothing else, so a laptop without
  network stays signed in.
- **The preference profile** (``sync_profile``): the account-wide
  ``profile`` pref against the account server's ``profile`` key,
  last-writer-wins by ``updated_at`` compared to the millisecond (the
  account server's precision). Pulled on a cloud sign-in, before the
  browser loads, and on every check; pushed a few seconds after
  ``set_pref`` stores a change made here (``profile_changed``). A push the
  account server refuses as older (409) takes its value. A failed push is
  retried by the next check. The AI provider entries and the active entry
  (``ai-settings``, ``ai-provider``) never sync. The last outcome per
  account is kept in memory for Settings (``profile_status``).
- **The server list** (``register_server``): this server's address under
  the person's account, posted on sign-in and on every check (an upsert
  that also refreshes ``last_seen_at``), removed on unlink or deletion
  (``release``).

Failures here are warnings in the server log, never errors to the person.
"""

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from . import cloud_auth
from .cloud_auth import PROVIDER, CloudAuthError
from .db import PROFILE_PREF_KEY, connect_users_db, get_pref, page_now, restamp_pref, set_profile
from .logbuf import log

CHECK_INTERVAL = 3600   # seconds between grant checks
PUSH_DELAY = 5.0        # seconds a profile change settles before it is pushed
SIGN_IN_TIMEOUT = 5     # the pull a sign-in waits for
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


def _failed(username: str, what: str, e: CloudAuthError) -> None:
    if e.status == 401:  # the cached access token died with its grant; the next call refreshes
        cloud_auth.forget_access(cloud_auth.grant_of(username)[0])
    log.warning(f"cloud: could not {what} for {username} (tried again at the next check): {e}")


# --- times ------------------------------------------------------------------------

def _ms(ts) -> datetime | None:
    """An ISO time cut to the millisecond, the account server's precision."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        t = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
    return t.replace(microsecond=t.microsecond // 1000 * 1000)


def _local_form(t: datetime) -> str:
    """The form ``db.page_now`` writes, so stored times compare as strings."""
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# --- the sync status -------------------------------------------------------------
# The last profile sync outcome per account, in memory only (Settings reads
# it through /api/auth/cloud/sync-status): "synced" (the last pull or push
# agreed), "pending" (a push is scheduled, or failed and waits for the next
# check; ``error`` then says why), "error" (the last attempt failed).
# "off" is never stored: ``profile_status`` works it out on every read.

_status: dict[str, dict] = {}
_status_lock = threading.Lock()
UNREACHABLE = "Gamma Cloud could not be reached."


def _note(username: str, state: str, error: str = "") -> None:
    with _status_lock:
        _status[username] = {"state": state, "at": page_now(), "error": error}


def _note_failure(username: str, error) -> None:
    """A failed attempt: a pending push stays pending (with the reason),
    anything else becomes "error"."""
    with _status_lock:
        was = _status.get(username, {}).get("state")
    _note(username, "pending" if was == "pending" else "error", str(error) or UNREACHABLE)


def profile_status(username: str) -> dict:
    """``{state, at, error}`` of the account's profile sync: "off" without
    cloud sign-in or without an identity holding a token; an account that
    syncs but has no outcome yet (a restart, before the first check) is
    "pending". No network."""
    if not _syncs(username):
        return {"state": "off", "at": "", "error": ""}
    with _status_lock:
        known = _status.get(username)
    return dict(known) if known else {"state": "pending", "at": "", "error": ""}


# --- the preference profile -----------------------------------------------------

def sync_profile(username: str, token: str | None = None, *, timeout: float = cloud_auth.HTTP_TIMEOUT) -> str:
    """Reconcile the account's profile with the account server's: "pulled",
    "pushed", "same", or "" when nothing could be done (no token, a failure
    — logged)."""
    token = token or cloud_auth.access_token_for(username)
    if not token:
        if _syncs(username):  # offline, or the refresh failed (logged)
            _note_failure(username, UNREACHABLE)
        return ""
    local, local_at = get_pref(username, PROFILE_PREF_KEY)
    try:
        remote = _call("GET", PROFILE_PATH, token, timeout=timeout)
    except CloudAuthError as e:
        if e.status != 404:
            _failed(username, "read the preference profile", e)
            _note_failure(username, e)
            return ""
        remote = {}
    remote_at = _ms(remote.get("updated_at")) if isinstance(remote.get("value"), dict) else None
    here_at = _ms(local_at) if isinstance(local, dict) else None
    if remote_at and (not here_at or remote_at > here_at):
        set_profile(username, remote["value"], updated_at=_local_form(remote_at))
        _note(username, "synced")
        return "pulled"
    if not here_at or here_at == remote_at:
        _note(username, "synced")
        return "same"
    return _push(username, token, local, local_at, timeout=timeout)


def _push(username: str, token: str, value: dict, updated_at: str, *, timeout: float = cloud_auth.HTTP_TIMEOUT) -> str:
    try:
        answer = _call("PUT", PROFILE_PATH, token, {"value": value, "updated_at": updated_at}, timeout=timeout)
    except CloudAuthError as e:
        stored_at = _ms(e.body.get("updated_at"))
        if e.status == 409 and isinstance(e.body.get("value"), dict) and stored_at:
            # the account server holds a newer profile: take it
            set_profile(username, e.body["value"], updated_at=_local_form(stored_at))
            _note(username, "synced")
            return "pulled"
        _failed(username, "push the preference profile", e)
        _note(username, "pending", str(e) or UNREACHABLE)
        return ""
    stored_at = _ms(answer.get("updated_at"))
    if stored_at and stored_at != _ms(updated_at):
        # stored under the account server's time (clamped, or cut to the
        # millisecond): keep that version here too, so the next check agrees
        restamp_pref(username, PROFILE_PREF_KEY, updated_at, _local_form(stored_at))
    _note(username, "synced")
    return "pushed"


def push_profile(username: str) -> str:
    """Push the stored profile now (the debounced push)."""
    value, updated_at = get_pref(username, PROFILE_PREF_KEY)
    if not isinstance(value, dict) or not updated_at:
        return ""
    token = cloud_auth.access_token_for(username)
    if not token:
        if _syncs(username):
            _note_failure(username, UNREACHABLE)
        return ""
    return _push(username, token, value, updated_at)


_timers: dict[str, threading.Timer] = {}
_timers_lock = threading.Lock()


def _syncs(username: str) -> bool:
    """Whether the account's profile follows it through Gamma Cloud: cloud
    sign-in on and an identity holding a token. No network."""
    if not cloud_auth.settings()["enabled"]:
        return False
    subject, refresh = cloud_auth.grant_of(username)
    return bool(subject and (refresh or cloud_auth.cached_access(subject)))


def profile_changed(username: str) -> None:
    """``set_pref``'s hook for a profile change made here: push it once
    changes have settled for ``PUSH_DELAY`` seconds, on a timer thread.
    Never raises into the request that stored the change."""
    try:
        if not _syncs(username):
            return
    except Exception:
        log.exception("cloud: could not check whether a profile change syncs")
        return
    _note(username, "pending")
    with _timers_lock:
        old = _timers.get(username)
        if old:
            old.cancel()
        timer = threading.Timer(PUSH_DELAY, _push_settled, args=(username,))
        timer.daemon = True
        _timers[username] = timer
        timer.start()


def _push_settled(username: str) -> None:
    with _timers_lock:
        if _timers.get(username) is threading.current_thread():
            del _timers[username]
    try:
        push_profile(username)
    except Exception as e:
        log.exception(f"cloud: pushing the preference profile of {username} failed")
        _note_failure(username, e)


# --- the server list ----------------------------------------------------------------

def register_server(username: str, token: str | None = None, url: str | None = None) -> bool:
    """Put this server on the person's server list (an upsert, which also
    refreshes its ``last_seen_at``). Nothing without an address to give."""
    url = cloud_auth.server_url() if url is None else url
    if not url:
        return False
    token = token or cloud_auth.access_token_for(username)
    if not token:
        return False
    try:
        _call("POST", "/api/me/servers", token, {"url": url, "name": cloud_auth.server_name(url)})
    except CloudAuthError as e:
        _failed(username, "register this server on the Gamma Cloud account", e)
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

def signed_in(request, username: str, subject: str, tokens: dict) -> None:
    """After a cloud sign-in: cache its access token, pull the profile (the
    browser loads next, and must see the synced one) and register this
    server in the background."""
    cloud_auth.remember_access(subject, tokens)
    token = cloud_auth.cached_access(subject)
    if not token:
        return
    try:
        sync_profile(username, token, timeout=SIGN_IN_TIMEOUT)
    except Exception:
        log.exception(f"cloud: the profile pull of {username}'s sign-in failed")
    url = cloud_auth.server_url(request)
    if url:
        _background(lambda: register_server(username, token, url))


def check(username: str) -> str:
    """One account's grant check: refresh, then sync the profile and
    refresh the server list entry. "ok", or "" when no token came back
    (offline — nothing happens — or revoked, handled by cloud_auth)."""
    token = cloud_auth.access_token_for(username, fresh=True)
    if not token:
        if _syncs(username):  # offline: the grant stays, the profile waits
            _note_failure(username, UNREACHABLE)
        return ""
    sync_profile(username, token)
    register_server(username, token)
    return "ok"


def check_all() -> dict:
    """The grant check over every identity holding a refresh token."""
    if not cloud_auth.settings()["enabled"]:
        return {}
    with connect_users_db() as conn:
        names = [r[0] for r in conn.execute(
            "SELECT username FROM identities WHERE provider = ? AND refresh_token != '' ORDER BY username", (PROVIDER,))]
    done = {}
    for username in names:
        try:
            done[username] = check(username)
        except Exception as e:
            log.exception(f"cloud: the grant check of {username} failed")
            _note_failure(username, e)
            done[username] = ""
    return done


@asynccontextmanager
async def lifespan():
    """The grant check at startup, then every ``CHECK_INTERVAL`` seconds."""
    stop = asyncio.Event()

    async def loop():
        while not stop.is_set():
            try:
                await asyncio.to_thread(check_all)
            except Exception:
                log.exception("cloud: the grant check failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=CHECK_INTERVAL)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        stop.set()
        await task
