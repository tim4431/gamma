"""The server's Google Drive connection (docs/dev/debugging.md "Google
Drive"): an admin connects a Google account in Settings → Backups, and the
backup tasks with *Copy to Google Drive* on send it their snapshots
(gamma/snapshot_copies.py). Like the bucket, it is the server's, not an
account's.

A connection is made through Gamma's own Google OAuth clients
(``config.GOOGLE_DRIVE_CLIENT`` and ``GOOGLE_DRIVE_DESKTOP_CLIENT``, asking
for ``drive.file`` alone), so connecting is signing in to Google and
allowing it: nobody sets anything up. A server may use clients of its own
instead, saved from the command line (``manage.py gdrive-client
[--desktop]``, ``set_client``; users.db ``settings`` keys ``gdrive_client``
and ``gdrive_desktop_client``, the secrets encrypted), which the browser
suite does for its stand-in Google. A token works only through the client it was made
through (``_client_of``), so a change of client ends a connection made
through the old one (revoked) and any code waiting.

Connecting takes one of two ways (gamma/gdrive.py):

- **Google's page** (``start_browser`` / ``finish_browser``), when Gamma is
  opened on the server's own computer (the desktop app, a loopback
  address): the browser goes to Google's authorization page and comes
  back to the server's callback with a code, traded with its PKCE
  verifier (``_browser``, in memory, by state, ``BROWSER_TTL``).
- **A code** (Google's device flow, ``start`` / ``poll``) anywhere else:
  ``start`` asks Google for a code, which the pane shows with Google's
  address; the person enters it there, on any device, and ``poll``, which
  the pane calls while the code is shown, asks Google at most once per the
  interval Google gave (``_pending``, in memory: one connection at a time,
  gone with the process or once Google's code expired). Nothing polls in
  the background: a code nobody enters just expires. Once Google says yes, the account's address is
read and the connection saved in users.db (``settings`` key ``gdrive``):
the refresh token, encrypted with the data directory's key as the bucket's
secret key is, the address and name, the folder's name, when, and the
client ID it was made through.

``disconnect`` revokes the token at Google and forgets it; the copies stay
in Drive. ``set_folder`` changes the folder new copies go to (the old one
stays where it is).
"""

import base64
import hashlib
import json
import secrets
import sqlite3
import threading
import time

from cryptography.fernet import InvalidToken

from . import config, gdrive
from .logbuf import log
from .publisher_sessions import seal, unseal
from .server_settings import _peek_raw, _set_raw

SETTINGS_KEY = "gdrive"          # the connection
CLIENT_KEY = "gdrive_client"     # a client of the server's own for the code (manage.py gdrive-client)
DESKTOP_KEY = "gdrive_desktop_client"  # ... and for Google's page (gdrive-client --desktop)
DEFAULT_FOLDER = "Gamma backups"
FOLDER_MAX = 120
CLIENT_ID_SUFFIX = ".apps.googleusercontent.com"
CLIENT_FIELD_MAX = 200
NO_CLIENT = ("This build of Gamma has no Google app to sign in to Drive with: an admin can give the server one "
             "with manage.py gdrive-client")

_lock = threading.Lock()
# The device flow in progress: {device_code, user_code, verification_url,
# expires_at, interval, next_poll_at, folder, oauth}; None between
# connections.
_pending: dict | None = None
_last_error = ""  # why the last connection did not complete, which ``status`` shows
# Sign-ins on Google's page under way: state → {verifier, redirect_uri,
# folder, oauth, expires_at}.
_browser: dict[str, dict] = {}
BROWSER_TTL = 600


def _iso(at: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def check_folder(folder) -> str:
    """The folder's name, trimmed: one name, no ``/``, at most FOLDER_MAX
    characters. ValueError otherwise."""
    if not isinstance(folder, str):
        raise ValueError("The folder must be text.")
    folder = " ".join(folder.split())
    if not folder:
        raise ValueError("Name the folder in Google Drive.")
    if len(folder) > FOLDER_MAX or "/" in folder or "\\" in folder or any(ord(c) < 32 for c in folder):
        raise ValueError(f"The folder must be one name of at most {FOLDER_MAX} characters, without / or \\.")
    return folder


def _read(key: str) -> dict:
    """A saved record; {} when there is none or it cannot be read (as
    offsite._kv reads the bucket's)."""
    try:
        raw = _peek_raw(key)
        value = json.loads(raw) if raw else {}
    except (sqlite3.Error, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _unseal(sealed, what: str) -> str:
    """A saved secret decrypted; "" when there is none or it no longer
    decrypts (the data directory's key changed)."""
    try:
        return unseal(sealed) if isinstance(sealed, str) else ""
    except (InvalidToken, ValueError, OSError):
        log.warning(f"[gdrive] the saved {what} cannot be decrypted (the data directory's key changed?)")
        return ""


# --- the OAuth client ----------------------------------------------------------------

def _client(key: str, gammas: tuple[str, str]) -> gdrive.OAuthClient | None:
    """The server's own client saved under ``key``, else Gamma's; None
    when there is neither (a build without one, and none saved or one whose
    secret no longer decrypts)."""
    saved = _read(key)
    client_id = saved.get("client_id")
    secret = _unseal(saved.get("client_secret"), "client secret")
    if isinstance(client_id, str) and client_id and secret:
        return gdrive.OAuthClient(client_id, secret)
    return gdrive.OAuthClient(*gammas) if all(gammas) else None


def oauth_client() -> gdrive.OAuthClient | None:
    """The "TVs and Limited Input devices" client a code is asked through."""
    return _client(CLIENT_KEY, config.GOOGLE_DRIVE_CLIENT)


def browser_client() -> gdrive.OAuthClient | None:
    """The "Desktop app" client of Google's page, on the server's own
    computer."""
    return _client(DESKTOP_KEY, config.GOOGLE_DRIVE_DESKTOP_CLIENT)


def _client_of(conn: dict) -> gdrive.OAuthClient | None:
    """The client a connection was made through, among those this server
    has; None when it has it no longer."""
    for oauth in (oauth_client(), browser_client()):
        if oauth is not None and oauth.id == conn.get("client_id"):
            return oauth
    return None


def _oauth() -> gdrive.OAuthClient:
    """The client for a code; DriveConfigError when there is none."""
    oauth = oauth_client()
    if oauth is None:
        raise gdrive.DriveConfigError(NO_CLIENT)
    return oauth


def _check_client(client_id, secret) -> gdrive.OAuthClient:
    """A client given on the command line, trimmed. ValueError for one
    that cannot be: an ID is one word ending in CLIENT_ID_SUFFIX, a secret
    one word."""
    if not isinstance(client_id, str) or not isinstance(secret, str):
        raise ValueError("The client ID and secret must be text.")
    client_id, secret = client_id.strip(), secret.strip()
    if not client_id:
        raise ValueError("Paste the client ID.")
    if not client_id.endswith(CLIENT_ID_SUFFIX) or len(client_id) > CLIENT_FIELD_MAX \
            or any(c.isspace() for c in client_id):
        raise ValueError(f"A Google client ID is one word ending in {CLIENT_ID_SUFFIX}.")
    if not secret:
        raise ValueError("Paste the client secret.")
    if len(secret) > CLIENT_FIELD_MAX or any(c.isspace() for c in secret):
        raise ValueError("The client secret is one word, as Google shows it.")
    return gdrive.OAuthClient(client_id, secret)


def set_client(client_id, secret, *, desktop: bool = False) -> dict:
    """Save a client of the server's own, used instead of Gamma's for the
    code (or, ``desktop``, for Google's page): manage.py gdrive-client.
    ValueError for an ID or secret that cannot be. The new ``status``."""
    oauth = _check_client(client_id, secret)
    with _lock:
        _set_raw(DESKTOP_KEY if desktop else CLIENT_KEY,
                 json.dumps({"client_id": oauth.id, "client_secret": seal(oauth.secret)}))
    _client_changed()
    return status()


def remove_client(*, desktop: bool = False) -> dict:
    """Forget the server's own client: Gamma's is used again, and a
    connection made through the one forgotten is disconnected (revoked).
    The new ``status``."""
    with _lock:
        _set_raw(DESKTOP_KEY if desktop else CLIENT_KEY, "")
    _client_changed()
    return status()


def _client_changed() -> None:
    """After the client changed: a code waiting was asked for through the
    old one and ends; a connection made through another than the one now
    in force can no longer be renewed and is disconnected (revoked)."""
    with _lock:
        _end()
    conn = _saved()
    if conn.get("refresh_token") and _client_of(conn) is None:
        disconnect()


# --- the connection ------------------------------------------------------------------

def _saved() -> dict:
    """The saved connection, the refresh token decrypted ("" when it no
    longer decrypts); {} when there is none."""
    value = _read(SETTINGS_KEY)
    if value:
        value["refresh_token"] = _unseal(value.get("refresh_token"), "token")
    return value


def _save(conn: dict) -> None:
    _set_raw(SETTINGS_KEY, json.dumps({**conn, "refresh_token": seal(conn["refresh_token"])}))


def _problem(conn: dict) -> str:
    """Why a saved connection cannot be used now, "" when it can."""
    if not conn.get("refresh_token"):
        return "Google Drive is not connected: an admin connects it in Settings → Backups"
    if oauth_client() is None and browser_client() is None:
        return NO_CLIENT
    if _client_of(conn) is None:
        return "Google Drive was connected through another Google OAuth client: connect it again"
    return ""


def ready() -> bool:
    """Whether a connection that can be used is saved."""
    return not _problem(_saved())


def client(**kwargs) -> gdrive.Client:
    """A client for the saved connection's folder. DriveConfigError when
    there is none that can be used."""
    conn = _saved()
    problem = _problem(conn)
    if problem:
        raise gdrive.DriveConfigError(problem)
    return gdrive.Client(_client_of(conn), conn["refresh_token"], conn.get("folder") or DEFAULT_FOLDER, **kwargs)


def status() -> dict:
    """What the pane shows: ``{available, connected, email, name, folder,
    connected_at, problem, pending, error}``. ``available``: there is a
    Google app to sign in with (a code's, or the page's on this computer);
    ``problem``: why the saved connection cannot be used ("" when it can,
    or there is none); ``pending``: the code to enter, ``{user_code,
    verification_url, expires_at, interval}``, while a connection waits;
    ``error``: why the last one did not complete, until the next one
    starts or Cancel."""
    conn = _saved()
    with _lock:
        pending = _pending
        if pending and pending["expires_at"] <= time.time():
            _end("The code expired before it was entered: connect again.")
            pending = None
        error = _last_error
    connected = bool(conn.get("refresh_token"))
    return {"available": oauth_client() is not None or browser_client() is not None, "connected": connected,
            "email": str(conn.get("email") or ""), "name": str(conn.get("name") or ""),
            "folder": str(conn.get("folder") or DEFAULT_FOLDER), "connected_at": conn.get("connected_at"),
            "problem": _problem(conn) if connected else "",
            "pending": {"user_code": pending["user_code"], "verification_url": pending["verification_url"],
                        "expires_at": _iso(pending["expires_at"]), "interval": pending["interval"]}
            if pending else None,
            "error": error}


def _end(error: str = "") -> None:
    """End the device flow in progress (with ``_lock`` held), keeping why
    for ``status``."""
    global _pending, _last_error
    _pending, _last_error = None, error


def start(folder: str = DEFAULT_FOLDER) -> dict:
    """Start connecting: a code from Google for the pane to show (a new one
    replaces any in progress). ValueError for a folder that cannot be;
    DriveConfigError without a client; DriveError when Google gives no
    code. The new ``status``."""
    global _pending
    folder = check_folder(folder)
    oauth = _oauth()
    code = gdrive.start_device_flow(oauth)
    now = time.time()
    with _lock:
        _end()
        _pending = {**code, "folder": folder, "oauth": oauth, "expires_at": now + code["expires_in"],
                    "next_poll_at": now + code["interval"]}
    return status()


def cancel() -> dict:
    with _lock:
        _end()
    return status()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def start_browser(folder: str, redirect_uri: str) -> str:
    """Start signing in on Google's page, which comes back to
    ``redirect_uri`` (the server's callback at a loopback address): the
    page's address. ValueError for a folder that cannot be;
    DriveConfigError without Gamma's "Desktop app" client."""
    folder = check_folder(folder)
    oauth = browser_client()
    if oauth is None:
        raise gdrive.DriveConfigError(NO_CLIENT)
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    now = time.time()
    with _lock:
        _end()
        for old in [k for k, v in _browser.items() if v["expires_at"] <= now]:
            del _browser[old]
        _browser[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "folder": folder, "oauth": oauth,
                           "expires_at": now + BROWSER_TTL}
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return gdrive.authorize_url(oauth, redirect_uri, state, challenge)


def finish_browser(state: str, code: str = "", error: str = "") -> str:
    """Google's page came back (the callback): the code traded and the
    connection saved, replacing any. "" once connected, else why not, which
    ``status``'s ``error`` says too."""
    global _last_error
    with _lock:
        started = _browser.pop(state, None) if state else None
    if started is None or started["expires_at"] <= time.time():
        problem = "This sign-in expired or was already used: connect again."
    elif error == "access_denied":
        problem = "Google Drive access was not allowed."
    elif error or not code:
        problem = f"Google did not sign in ({error or 'no code'}): connect again."
    else:
        try:
            answer = gdrive.exchange_code(started["oauth"], code, started["verifier"], started["redirect_uri"])
        except (gdrive.DriveError, gdrive.DriveConfigError) as e:
            answer = {"state": "failed", "error": str(e)}
        if answer["state"] == "done":
            problem = _connect(answer, started["folder"], started["oauth"])
        else:
            problem = answer.get("error") or "Google Drive access was not allowed."
    with _lock:
        _last_error = problem
    return problem


def poll() -> dict:
    """Ask Google once whether the code was entered, unless the interval
    Google gave has not passed or another request is asking (then nothing
    is asked). No answer, or a 5xx, waits for the next interval. On a yes
    the connection is saved, replacing any; on a no, or a code that
    expired, the flow ends with the reason in ``status``'s ``error``. The
    new ``status``."""
    with _lock:
        pending = _pending
        due = pending is not None and time.time() >= pending["next_poll_at"]
        if due:
            pending["next_poll_at"] = float("inf")  # this request asks; another waits for its answer
    if not due:
        return status()
    try:
        answer = gdrive.poll_device_flow(pending["oauth"], pending["device_code"])
    except gdrive.DriveUnreachable:
        answer = {"state": "pending"}
    except (gdrive.DriveError, gdrive.DriveConfigError) as e:
        answer = {"state": "failed", "error": str(e)}
    state = answer["state"]
    if state in ("pending", "slow_down"):
        with _lock:
            pending["interval"] += 5 if state == "slow_down" else 0
            pending["next_poll_at"] = time.time() + pending["interval"]
        return status()
    error = _connect(answer, pending["folder"], pending["oauth"]) if state == "done" else {
        "denied": "Google Drive access was not allowed.",
        "expired": "The code expired before it was entered: connect again."}.get(state) or answer.get("error", "")
    with _lock:
        if _pending is pending:  # not replaced by a new connection meanwhile
            _end(error)
    return status()


def _connect(answer: dict, folder: str, oauth: gdrive.OAuthClient) -> str:
    """Save the connection Google just granted through ``oauth``, replacing
    (and revoking) any; "" or why it could not be saved."""
    token = answer["refresh_token"]
    gdrive.remember(token, answer["access_token"], answer["expires_in"])
    try:
        who = gdrive.Client(oauth, token, folder).about()
    except gdrive.DriveConfigError as e:
        _revoke(token)
        return f"Google said yes, but the access could not be used: {e}"
    except gdrive.DriveError as e:  # the files work without the address
        log.warning(f"[gdrive] the connected account could not be read: {e}")
        who = {"email": "", "name": ""}
    with _lock:
        old = _saved().get("refresh_token")
        _save({"refresh_token": token, "email": who["email"], "name": who["name"], "folder": folder,
               "connected_at": _iso(time.time()), "client_id": oauth.id})
    if old and old != token:
        _revoke(old)
    log.info(f"[gdrive] connected {who['email'] or 'a Google account'}")
    return ""


def _revoke(token: str) -> None:
    gdrive.forget(token)
    try:
        gdrive.revoke(token)
    except (gdrive.DriveError, gdrive.DriveConfigError) as e:  # it stays in the person's list on Google
        log.warning(f"[gdrive] the old access could not be revoked: {e}")


def disconnect() -> dict:
    """Forget the connection, revoking it at Google first (a failure there
    is logged: the person can remove it from their Google account). The
    copies stay in Drive."""
    with _lock:
        token = _saved().get("refresh_token")
        _set_raw(SETTINGS_KEY, "")
    if token:
        _revoke(token)
    return status()


def set_folder(folder: str) -> dict:
    """The folder new copies go to. ValueError when it cannot be, or when
    nothing is connected."""
    folder = check_folder(folder)
    with _lock:
        conn = _saved()
        if not conn.get("refresh_token"):
            raise ValueError("Google Drive is not connected.")
        _save({**conn, "folder": folder})
    return status()


def test() -> dict:
    """The pane's Test: what the folder holds and the account's room, tried
    once: ``{ok: True, message}`` or ``{ok: False, message: <why>}``."""
    try:
        drive = client(attempts=1)
        count, more = drive.check()
    except (gdrive.DriveConfigError, gdrive.DriveError) as e:
        return {"ok": False, "message": str(e)}
    try:
        who = drive.about()
    except gdrive.DriveError:
        who = {"email": "", "used": 0, "limit": 0}
    held = f"{count} or more items" if more else f"{count} item{'' if count == 1 else 's'}"
    room = f", {who['used'] / 1e9:.1f} of {who['limit'] / 1e9:.0f} GB used" if who["limit"] else ""
    return {"ok": True, "message": f"{who['email'] or 'Google Drive'}: {held} in {drive.folder}{room}"}
