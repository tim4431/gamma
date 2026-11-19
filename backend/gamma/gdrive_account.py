"""The server's Google Drive connection (docs/dev/debugging.md "Google
Drive"): an admin connects a Google account in Settings → Backups, and the
backup tasks with *Copy to Google Drive* on send it their snapshots
(gamma/snapshot_copies.py). Like the bucket, it is the server's, not an
account's.

Connecting is Google's device flow (gamma/gdrive.py): ``start`` asks Google
for a code, which the pane shows with Google's address; the person enters
it there, on any device, and ``poll``, which the pane calls while the code
is shown, asks Google at most once per the interval Google gave
(``_pending``, in memory: one connection at a time, gone with the process
or once Google's code expired). Nothing polls in the background: a code
nobody enters just expires. Once Google says yes, the account's address is
read and the connection saved in users.db (``settings`` key ``gdrive``):
the refresh token, encrypted with the data directory's key as the bucket's
secret key is, the address and name, the folder's name, when, and the
client ID it was made through (a token works only with its own client, so
a connection made through another client asks to be made again).

``disconnect`` revokes the token at Google and forgets it; the copies stay
in Drive. ``set_folder`` changes the folder new copies go to (the old one
stays where it is).
"""

import json
import sqlite3
import threading
import time

from cryptography.fernet import InvalidToken

from . import gdrive
from .logbuf import log
from .publisher_sessions import cipher
from .server_settings import _peek_raw, _set_raw

SETTINGS_KEY = "gdrive"
DEFAULT_FOLDER = "Gamma backups"
FOLDER_MAX = 120

_lock = threading.Lock()
# The device flow in progress: {device_code, user_code, verification_url,
# expires_at, interval, next_poll_at, folder}; None between connections.
_pending: dict | None = None
_last_error = ""  # why the last connection did not complete, which ``status`` shows


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


def _saved() -> dict:
    """The saved connection, the refresh token decrypted ("" when it no
    longer decrypts); {} when there is none."""
    try:
        raw = _peek_raw(SETTINGS_KEY)
        value = json.loads(raw) if raw else {}
    except (sqlite3.Error, ValueError, TypeError):  # read as offsite._kv reads the bucket's
        return {}
    if not isinstance(value, dict):
        return {}
    sealed = value.get("refresh_token")
    try:
        value["refresh_token"] = cipher().decrypt(sealed.encode("ascii")).decode("utf-8") \
            if isinstance(sealed, str) and sealed else ""
    except (InvalidToken, ValueError, OSError):
        log.warning("[gdrive] the saved token cannot be decrypted (the data directory's key changed?)")
        value["refresh_token"] = ""
    return value


def _save(conn: dict) -> None:
    token = conn["refresh_token"]
    _set_raw(SETTINGS_KEY, json.dumps({**conn, "refresh_token": cipher().encrypt(token.encode("utf-8")).decode("ascii")}))


def _problem(conn: dict) -> str:
    """Why a saved connection cannot be used now, "" when it can."""
    if not conn.get("refresh_token"):
        return "Google Drive is not connected: an admin connects it in Settings → Backups"
    try:
        now = gdrive.client_id()
    except gdrive.DriveConfigError as e:
        return str(e)
    if conn.get("client_id") != now:
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
    return gdrive.Client(conn["refresh_token"], conn.get("folder") or DEFAULT_FOLDER, **kwargs)


def status() -> dict:
    """What the pane shows: ``{available, connected, email, name, folder,
    connected_at, problem, pending, error}``. ``available``: there is an
    OAuth client to connect through; ``problem``: why the saved connection
    cannot be used ("" when it can, or there is none); ``pending``: the
    code to enter, ``{user_code, verification_url, expires_at,
    interval}``, while a connection waits; ``error``: why the last one did
    not complete, until the next one starts or Cancel."""
    conn = _saved()
    with _lock:
        pending = _pending
        if pending and pending["expires_at"] <= time.time():
            _end("The code expired before it was entered: connect again.")
            pending = None
        error = _last_error
    connected = bool(conn.get("refresh_token"))
    return {"available": gdrive.available(), "connected": connected,
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
    code = gdrive.start_device_flow()
    now = time.time()
    with _lock:
        _end()
        _pending = {**code, "folder": folder, "expires_at": now + code["expires_in"],
                    "next_poll_at": now + code["interval"]}
    return status()


def cancel() -> dict:
    with _lock:
        _end()
    return status()


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
        answer = gdrive.poll_device_flow(pending["device_code"])
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
    error = _connect(answer, pending["folder"]) if state == "done" else {
        "denied": "Google Drive access was not allowed.",
        "expired": "The code expired before it was entered: connect again."}.get(state) or answer.get("error", "")
    with _lock:
        if _pending is pending:  # not replaced by a new connection meanwhile
            _end(error)
    return status()


def _connect(answer: dict, folder: str) -> str:
    """Save the connection Google just granted, replacing (and revoking)
    any; "" or why it could not be saved."""
    token = answer["refresh_token"]
    gdrive.remember(token, answer["access_token"], answer["expires_in"])
    try:
        who = gdrive.Client(token, folder).about()
    except gdrive.DriveConfigError as e:
        _revoke(token)
        return f"Google said yes, but the access could not be used: {e}"
    except gdrive.DriveError as e:  # the files work without the address
        log.warning(f"[gdrive] the connected account could not be read: {e}")
        who = {"email": "", "name": ""}
    with _lock:
        old = _saved().get("refresh_token")
        _save({"refresh_token": token, "email": who["email"], "name": who["name"], "folder": folder,
               "connected_at": _iso(time.time()), "client_id": gdrive.client_id()})
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
