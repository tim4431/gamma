"""Google Drive as a backup store (docs/dev/debugging.md "Google Drive",
docs/research/google-drive-backup.md): Google's OAuth device flow, which
connects an account, and ``Client``, one folder of that Drive, a backup
store (gamma/backup_store.py ``Store``) as the bucket's ``s3.Client`` is,
so the backup tasks' snapshots (gamma/snapshot_copies.py) go to either
alike. gamma/gdrive_account.py keeps the connection.

The scope is ``drive.file`` alone: Gamma sees only the files it made, and
the device flow (a code the person enters at google.com/device) is the one
Google offers a server at any address for it. The OAuth client
(``OAuthClient``) is gdrive_account's to choose and is handed to every
call that needs it; its secret is a device client's, which Google does not
treat as secret.

``Client`` keeps whole files under its folder in My Drive, a path of
folder names from My Drive's top (``Gamma backups`` by default,
``Backups/Gamma`` a folder in a folder; "" My Drive itself, where the
folder browser starts). Keys are paths: ``snapshots/<ws>/<name>.zip`` is
the file ``<name>.zip`` in the folder ``snapshots/<ws>/`` under it, made as
a write needs it. Drive names are not unique, so a write first looks for
its name in its folder and replaces that file's content; a lookup takes the
oldest of several, and so does ``browse``, which lists a folder's folders
and files for the folder browser; ``make_folder`` makes a folder there. ``put_object`` streams a local file up in a resumable
session (``CHUNK`` at a time, resumed where Drive says it stopped);
``get_object`` downloads into a local file, written whole; ``put_bytes``
and ``get_bytes`` handle a small object; ``list_objects`` gives ``(key,
size, mtime)`` under a prefix, ``mtime`` when Drive last changed the file;
``delete_object`` deletes for good (not to the trash, which would keep
counting against the quota); ``check`` counts what the folder holds.

Every call goes through ``_request``, plain urllib on the server's network
route (gamma/net_proxy.py) and the tests' single patch point
(tests/gdrive_fake.py). An access token lasts an hour and is kept in memory
per refresh token; a 401 renews it once. A 429, a 5xx, a rate-limit 403 or
no answer is tried again after an exponential pause (``_sleep``), at most
``attempts`` times. A call Google refuses or that gets no answer raises
``DriveError``, a ``StoreError`` as the bucket's ``S3Error`` is; no
client, no connection or a connection Google no longer honours raises
``DriveConfigError``, a ``StoreConfigError``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import random
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any

from . import config, net_proxy
from .backup_store import StoreConfigError, StoreError, check_key, listing

SCOPE = "https://www.googleapis.com/auth/drive.file"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
FOLDER_MIME = "application/vnd.google-apps.folder"
CHUNK = 32 * 256 * 1024     # 8 MiB: an upload's chunk, a multiple of the 256 KiB Drive requires
READ_CHUNK = 1024 * 1024    # what a download reads at a time
TIMEOUT_S = 60
ATTEMPTS = 5
INTERACTIVE_ATTEMPTS = 2    # a call the pane waits on (a code asked for, a revocation)
MAX_PAUSE_S = 32
LIST_PAGE = 1000            # what one listing returns at most, and what ``check`` counts up to
RETRIED = (429, 500, 502, 503, 504)
RATE_REASONS = ("userRateLimitExceeded", "rateLimitExceeded")


class DriveError(StoreError):
    """Google refused a call or could not be reached."""


class DriveUnreachable(DriveError):
    """No answer came."""


class DriveConfigError(StoreConfigError):
    """No Drive that can work: no client, no connection, a connection
    Google no longer honours (revoked, unused for six months, made through
    another client)."""


# --- the transport -------------------------------------------------------------------

@dataclass
class Response:
    """One HTTP answer: ``headers`` with lower-case names; ``body`` is b""
    when it was streamed into a sink."""
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body or b"{}")
        except ValueError:
            return {}


class _Redirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect, without the Authorization header when it leaves
    the host, as requests does."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            new.remove_header("Authorization")
        return new


_opener = net_proxy.build_opener(_Redirect)


def _lower(headers) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()} if headers else {}


def _unreachable(e: BaseException) -> DriveUnreachable:
    reason = getattr(e, "reason", None) or str(e) or type(e).__name__
    return DriveUnreachable(f"Google could not be reached: {reason}")


def _read(resp, n: int = -1) -> bytes:
    try:
        return resp.read(n) if n > 0 else resp.read()
    except (OSError, HTTPException) as e:
        raise _unreachable(e) from None


def _request(method: str, url: str, headers: dict, body: bytes | None = None, timeout: float = TIMEOUT_S,
             sink=None) -> Response:
    """One HTTP call. With ``sink`` a 2xx body is streamed into it and
    ``Response.body`` is b"". DriveUnreachable when no answer came; an
    error writing the sink is the sink's own. The single transport of this
    module: the tests replace it."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        resp = _opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        with e:
            return Response(e.code, _lower(e.headers), _read(e))
    except (OSError, HTTPException) as e:  # a URLError is an OSError
        raise _unreachable(e) from None
    with resp:
        answered = _lower(resp.headers)
        if sink is None:
            return Response(resp.status, answered, _read(resp))
        while chunk := _read(resp, READ_CHUNK):
            sink.write(chunk)
        return Response(resp.status, answered, b"")


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _pause(attempt: int) -> float:
    """The exponential pause before try ``attempt + 1``, with up to a second
    of jitter, as Google asks."""
    return min(2 ** attempt + random.random(), MAX_PAUSE_S)


def _error(resp: Response) -> tuple[str, str]:
    """Google's ``(reason, message)`` of an error answer: the Drive API's
    ``{error: {errors: [{reason}], message}}`` or OAuth's ``{error,
    error_description}``."""
    data = resp.json()
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        reasons = [e.get("reason", "") for e in err.get("errors") or () if isinstance(e, dict)]
        return (reasons[0] if reasons else str(err.get("status") or "")), str(err.get("message") or "")
    if isinstance(err, str):
        return err, str(data.get("error_description") or "")
    return "", resp.body[:200].decode("utf-8", "replace").strip()


def _retried(resp: Response) -> bool:
    return resp.status in RETRIED or (resp.status == 403 and _error(resp)[0] in RATE_REASONS)


def _form(url: str, fields: dict, attempts: int = ATTEMPTS) -> Response:
    """A POST of form fields to Google's OAuth endpoints, tried again as
    every call is."""
    body = urllib.parse.urlencode(fields).encode("ascii")
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    attempt = 0
    while True:
        try:
            resp = _request("POST", url, headers, body)
        except DriveUnreachable:
            attempt += 1
            if attempt >= attempts:
                raise
        else:
            attempt += 1
            if not _retried(resp) or attempt >= attempts:
                return resp
        _sleep(_pause(attempt - 1))


# --- the OAuth client and its tokens --------------------------------------------------

@dataclass(frozen=True)
class OAuthClient:
    """A Google OAuth client of the "TVs and Limited Input devices" type,
    which a connection is made through and its tokens renewed by."""
    id: str
    secret: str = field(repr=False)


def start_device_flow(oauth: OAuthClient) -> dict:
    """Ask Google for a device code (``drive.file``): ``{device_code,
    user_code, verification_url, expires_in, interval}``."""
    resp = _form(f"{config.google_oauth_base()}/device/code", {"client_id": oauth.id, "scope": SCOPE},
                 attempts=INTERACTIVE_ATTEMPTS)
    data = resp.json()
    if resp.status != 200 or not data.get("device_code") or not data.get("user_code"):
        reason, message = _error(resp)
        if reason in ("invalid_client", "unauthorized_client"):
            raise DriveConfigError(f"Google refused the OAuth client ({reason}): it must be of the "
                                   "\"TVs and Limited Input devices\" type")
        raise DriveError(f"Google did not give a sign-in code: {message or reason or resp.status}")
    return {"device_code": str(data["device_code"]), "user_code": str(data["user_code"]),
            "verification_url": str(data.get("verification_url") or data.get("verification_uri")
                                    or "https://www.google.com/device"),
            "expires_in": int(data.get("expires_in") or 1800), "interval": max(int(data.get("interval") or 5), 1)}


def authorize_url(oauth: OAuthClient, redirect_uri: str, state: str, challenge: str) -> str:
    """Google's authorization page for a browser on the server's own
    computer (``oauth`` a "Desktop app" client, ``redirect_uri`` a loopback
    address): it asks for ``drive.file`` and offline access, always shows
    the consent (so a refresh token comes back), and sends the browser to
    ``redirect_uri`` with ``state`` and a code bound to ``challenge``
    (PKCE, S256)."""
    params = {"client_id": oauth.id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
              "access_type": "offline", "prompt": "consent", "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{config.google_accounts_base()}/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


def exchange_code(oauth: OAuthClient, code: str, verifier: str, redirect_uri: str) -> dict:
    """The authorization page's code traded for the tokens: as
    ``poll_device_flow`` answers, ``{state: "done", refresh_token,
    access_token, expires_in, scope}``, or ``{state: "denied"}`` for a grant
    without ``drive.file``. DriveError when Google refuses the code."""
    resp = _form(f"{config.google_oauth_base()}/token", {
        "client_id": oauth.id, "client_secret": oauth.secret, "code": code, "code_verifier": verifier,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"}, attempts=INTERACTIVE_ATTEMPTS)
    data = resp.json()
    if resp.status == 200 and data.get("access_token"):
        if SCOPE not in str(data.get("scope", "")).split():
            return {"state": "denied"}
        if not data.get("refresh_token"):
            raise DriveError("Google gave no refresh token")
        return {"state": "done", "refresh_token": str(data["refresh_token"]),
                "access_token": str(data["access_token"]), "expires_in": int(data.get("expires_in") or 3600),
                "scope": str(data.get("scope", ""))}
    reason, message = _error(resp)
    if reason in ("invalid_client", "unauthorized_client"):
        raise DriveConfigError(f"Google refused the OAuth client ({reason})")
    raise DriveError(f"Google did not take the sign-in: {message or reason or resp.status}")


def poll_device_flow(oauth: OAuthClient, device_code: str) -> dict:
    """Ask once whether the person entered the code: ``{state: "pending"}``,
    ``{state: "slow_down"}``, ``{state: "denied"}``, ``{state: "expired"}``,
    or ``{state: "done", refresh_token, access_token, expires_in, scope}``.
    Tried once: a 5xx is ``pending`` and a 429 ``slow_down``, for the next
    interval; DriveUnreachable when no answer came. A grant without
    ``drive.file`` (the person unticked it) is ``{state: "denied"}``;
    DriveError for anything else."""
    resp = _form(f"{config.google_oauth_base()}/token", {
        "client_id": oauth.id, "client_secret": oauth.secret, "device_code": device_code, "grant_type": DEVICE_GRANT},
        attempts=1)  # the pane asks again at the next interval
    data = resp.json()
    if resp.status == 200 and data.get("access_token"):
        if SCOPE not in str(data.get("scope", "")).split():
            return {"state": "denied"}
        if not data.get("refresh_token"):
            raise DriveError("Google gave no refresh token")
        return {"state": "done", "refresh_token": str(data["refresh_token"]),
                "access_token": str(data["access_token"]), "expires_in": int(data.get("expires_in") or 3600),
                "scope": str(data.get("scope", ""))}
    reason, message = _error(resp)
    if reason == "slow_down" or resp.status == 429:
        return {"state": "slow_down"}
    if reason == "authorization_pending" or _retried(resp):
        return {"state": "pending"}
    if reason == "access_denied":
        return {"state": "denied"}
    if reason in ("expired_token", "invalid_grant"):
        return {"state": "expired"}
    if reason in ("invalid_client", "unauthorized_client"):
        raise DriveConfigError(f"Google refused the OAuth client ({reason})")
    raise DriveError(f"Google's answer to the sign-in: {message or reason or resp.status}")


def refresh(oauth: OAuthClient, refresh_token: str) -> tuple[str, int]:
    """A new access token for ``refresh_token``, made through ``oauth``,
    and its lifetime in seconds. DriveConfigError when Google no longer
    honours it."""
    resp = _form(f"{config.google_oauth_base()}/token", {
        "client_id": oauth.id, "client_secret": oauth.secret, "refresh_token": refresh_token,
        "grant_type": "refresh_token"})
    data = resp.json()
    if resp.status == 200 and data.get("access_token"):
        return str(data["access_token"]), int(data.get("expires_in") or 3600)
    reason, message = _error(resp)
    if reason == "invalid_grant":
        raise DriveConfigError("Google no longer honours this connection (revoked, or unused for six months): "
                               "connect Google Drive again in Settings → Backups")
    if reason in ("invalid_client", "unauthorized_client"):
        raise DriveConfigError(f"Google refused the OAuth client ({reason}): connect Google Drive again")
    raise DriveError(f"Google did not renew the access: {message or reason or resp.status}")


def revoke(token: str) -> None:
    """Revoke a refresh token (and the access it gave) at Google; one
    already revoked is no error."""
    resp = _form(f"{config.google_oauth_base()}/revoke", {"token": token}, attempts=INTERACTIVE_ATTEMPTS)
    if resp.status != 200 and _error(resp)[0] != "invalid_token":
        raise DriveError(f"Google did not revoke the access: {_error(resp)[1] or resp.status}")


_tokens: dict[str, tuple[str, float]] = {}  # sha256 of a refresh token → (access token, monotonic expiry)
_tokens_lock = threading.Lock()


def _digest(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def remember(refresh_token: str, access_token: str, expires_in: int) -> None:
    """Keep an access token the device flow just gave."""
    with _tokens_lock:
        _tokens[_digest(refresh_token)] = (access_token, time.monotonic() + expires_in)


def forget(refresh_token: str) -> None:
    with _tokens_lock:
        _tokens.pop(_digest(refresh_token), None)


def access_token(oauth: OAuthClient, refresh_token: str, *, renew: bool = False) -> str:
    """The access token for ``refresh_token``, renewed through ``oauth``
    when it has less than a minute left or ``renew``."""
    key = _digest(refresh_token)
    with _tokens_lock:
        held = _tokens.get(key)
        if held and not renew and held[1] > time.monotonic() + 60:
            return held[0]
        token, lifetime = refresh(oauth, refresh_token)
        _tokens[key] = (token, time.monotonic() + lifetime)
        return token


# --- the account ---------------------------------------------------------------------

def _api(path: str) -> str:
    return f"{config.google_api_base()}/drive/v3{path}"


def _upload_api(path: str) -> str:
    return f"{config.google_api_base()}/upload/drive/v3{path}"


def _q(value: str) -> str:
    """A string literal in a Drive query."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def where(folder: str) -> str:
    """The folder as the log and the messages name it: ``Google Drive ›
    Backups › Gamma``."""
    return " › ".join(["Google Drive", *filter(None, folder.split("/"))])


def _mtime(stamp: str) -> float:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0.0


class Client:
    """One folder of one Google account's My Drive (``folder``, its path
    from My Drive's top; "" My Drive itself), reached with
    ``refresh_token`` through ``oauth``, the client it was made through.
    ``attempts``: how often a call is tried; the pane's Test tries once."""

    def __init__(self, oauth: OAuthClient, refresh_token: str, folder: str, *, attempts: int = ATTEMPTS):
        if not refresh_token:
            raise DriveConfigError("Google Drive is not connected: an admin connects it in Settings → Backups")
        self.oauth = oauth
        self.refresh_token = refresh_token
        self.folder = folder
        self.attempts = max(1, attempts)
        self.where = where(folder)
        self._base = folder.split("/") if folder else []
        self._folders: dict[str, str] = {}  # a folder's path from My Drive's top → its id, as walked
        self._lock = threading.Lock()

    # --- calls ---

    def _call(self, method: str, url: str, *, params: dict | None = None, body: bytes | None = None,
              headers: dict | None = None, sink=None) -> Response:
        """One API call with the access token: renewed once on a 401,
        tried again on a 429, a 5xx, a rate-limit 403 or no answer. The
        last answer, whatever its status."""
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        renewed = False
        attempt = 0
        while True:
            sent = {"Authorization": f"Bearer {access_token(self.oauth, self.refresh_token)}", **(headers or {})}
            if sink is not None:
                sink.seek(0)
                sink.truncate()
            try:
                resp = _request(method, url, sent, body, sink=sink)
            except DriveUnreachable:
                attempt += 1
                if attempt >= self.attempts:
                    raise
            else:
                if resp.status == 401 and not renewed:
                    renewed = True
                    access_token(self.oauth, self.refresh_token, renew=True)
                    continue
                attempt += 1
                if not _retried(resp) or attempt >= self.attempts:
                    return resp
            _sleep(_pause(attempt - 1))

    def _fail(self, what: str, resp: Response) -> DriveError:
        reason, message = _error(resp)
        if reason == "storageQuotaExceeded":
            return DriveError(f"{what} in {self.where}: the Google account's storage is full")
        return DriveError(f"{what} in {self.where}: {resp.status} {message or reason}".rstrip())

    def _json(self, what: str, method: str, url: str, **kwargs) -> dict:
        resp = self._call(method, url, **kwargs)
        if resp.status not in (200, 201):
            raise self._fail(what, resp)
        return resp.json()

    def about(self) -> dict:
        """The account: ``{email, name, used, limit}`` (bytes; ``limit`` 0
        for unlimited)."""
        data = self._json("ABOUT", "GET", _api("/about"),
                          params={"fields": "user(emailAddress,displayName),storageQuota(limit,usage)"})
        user, quota = data.get("user") or {}, data.get("storageQuota") or {}
        return {"email": str(user.get("emailAddress") or ""), "name": str(user.get("displayName") or ""),
                "used": int(quota.get("usage") or 0), "limit": int(quota.get("limit") or 0)}

    def _list(self, q: str, *, one_page: bool = False, order: str = "") -> tuple[list[dict], bool]:
        """The files a query finds (``id, name, mimeType, size,
        modifiedTime``), every page or the first; whether there are more."""
        params = {"q": q, "spaces": "drive", "pageSize": LIST_PAGE,
                  "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime)"}
        if order:
            params["orderBy"] = order
        out = []
        while True:
            data = self._json("LIST", "GET", _api("/files"), params=params)
            out.extend(f for f in data.get("files") or () if isinstance(f, dict) and f.get("id"))
            token = data.get("nextPageToken")
            if not token or one_page:
                return out, bool(token)
            params["pageToken"] = token

    def _find(self, parent: str, name: str, *, folder: bool) -> dict | None:
        """The oldest file (or folder) named ``name`` in ``parent``."""
        q = (f"name = {_q(name)} and {_q(parent)} in parents and trashed = false and "
             f"mimeType {'=' if folder else '!='} {_q(FOLDER_MIME)}")
        found, _ = self._list(q, one_page=True, order="createdTime")
        return found[0] if found else None

    def _folder(self, path: str, *, create: bool) -> str | None:
        """The id of the folder ``path`` under the client's folder ("" the
        folder itself; ``root``, My Drive itself, for a client of no
        folder); made when ``create``, else None when it is not there."""
        with self._lock:
            parent, here = "root", ""  # ``here``: the path walked so far, from My Drive's top
            for name in self._base + (path.split("/") if path else []):
                here = f"{here}/{name}" if here else name
                if here not in self._folders:
                    found = self._find(parent, name, folder=True)
                    if found is None:
                        if not create:
                            return None
                        found = self._json(f"MKDIR {here}", "POST", _api("/files"),
                                           params={"fields": "id"},
                                           body=json.dumps({"name": name, "mimeType": FOLDER_MIME,
                                                            "parents": [parent]}).encode("utf-8"),
                                           headers={"Content-Type": "application/json; charset=UTF-8"})
                    self._folders[here] = str(found["id"])
                parent = self._folders[here]
            return parent

    def _file(self, key: str, *, create_folder: bool = False) -> tuple[str | None, str, dict | None]:
        """``(folder id, name, file)`` of ``key``: its folder's id (None when
        there is none and ``create_folder`` is off), its name, and the file
        that holds it (None when there is none)."""
        check_key(key)
        path, _, name = key.rpartition("/")
        parent = self._folder(path, create=create_folder)
        return parent, name, (self._find(parent, name, folder=False) if parent else None)

    # --- the store's methods (backup_store.Store) ---

    def check(self) -> tuple[int, bool]:
        """How many things the folder holds, up to LIST_PAGE, and whether
        there are more; (0, False) before the first copy made it."""
        folder = self._folder("", create=False)
        if folder is None:
            return 0, False
        found, more = self._list(f"{_q(folder)} in parents and trashed = false", one_page=True)
        return len(found), more

    def put_object(self, key: str, path: Path, tick=None) -> None:
        """Store the local file at ``path`` as ``key``, streamed; a file of
        the key is replaced whole. ``tick``, when given, is called with
        each run of bytes Drive took. A file that is not there raises
        FileNotFoundError, never a DriveError."""
        with open(path, "rb") as f:
            self._put(key, f, os.fstat(f.fileno()).st_size, tick)

    def put_bytes(self, key: str, data: bytes) -> None:
        """Store ``data`` as ``key`` (a small object)."""
        self._put(key, io.BytesIO(data), len(data), None)

    def _put(self, key: str, f, size: int, tick) -> None:
        parent, name, existing = self._file(key, create_folder=True)
        for _restart in range(2):  # a session Drive forgot (404/410) starts over once
            meta = {"name": name} if existing else {"name": name, "parents": [parent]}
            url = _upload_api(f"/files/{existing['id']}" if existing else "/files")
            resp = self._call("PATCH" if existing else "POST", url,
                              params={"uploadType": "resumable", "fields": "id,size"},
                              body=json.dumps(meta).encode("utf-8"),
                              headers={"Content-Type": "application/json; charset=UTF-8",
                                       "X-Upload-Content-Type": "application/octet-stream",
                                       "X-Upload-Content-Length": str(size)})
            session = resp.headers.get("location", "")
            if resp.status != 200 or not session:
                raise self._fail(f"PUT {key}", resp)
            if self._send(key, session, f, size, tick):
                return
        raise DriveError(f"PUT {key} in {self.where}: Google lost the upload twice")

    def _send(self, key: str, session: str, f, size: int, tick) -> bool:
        """Send the file's bytes to the resumable session a chunk at a time,
        resuming where Drive says it stopped. False when Drive forgot the
        session."""
        sent, attempt, ask = 0, 0, False
        while True:
            if ask:  # how much Drive holds, after a chunk got no answer or a 5xx
                chunk, span = b"", f"bytes */{size}"
            else:
                f.seek(sent)
                chunk = f.read(CHUNK)
                span = f"bytes {sent}-{sent + len(chunk) - 1}/{size}" if chunk else f"bytes */{size}"
            headers = {"Content-Type": "application/octet-stream", "Content-Length": str(len(chunk)),
                       "Content-Range": span}
            try:
                resp = _request("PUT", session, headers, chunk)
            except DriveUnreachable:
                resp = None
            if resp is not None and resp.status in (200, 201):
                if tick and size > sent:
                    tick(size - sent)
                return True
            if resp is not None and resp.status == 308:
                held = resp.headers.get("range", "")  # "bytes=0-1048575"; none while Drive holds nothing
                now = int(held.rpartition("-")[2]) + 1 if held.startswith("bytes=") else 0
                if now > sent:
                    if tick:
                        tick(now - sent)
                    attempt = 0
                elif not ask:  # a chunk Drive took none of
                    attempt += 1
                    if attempt >= self.attempts:
                        raise DriveError(f"PUT {key} in {self.where}: Google took none of the upload")
                sent, ask = now, False
                continue
            if resp is not None and resp.status in (404, 410):
                return False
            if resp is not None and not _retried(resp):
                raise self._fail(f"PUT {key}", resp)
            attempt += 1
            if attempt >= self.attempts:
                if resp is None:
                    raise DriveUnreachable(f"PUT {key} in {self.where}: Google could not be reached")
                raise self._fail(f"PUT {key}", resp)
            ask = True
            _sleep(_pause(attempt - 1))

    def get_object(self, key: str, path: Path) -> bool:
        """Download ``key`` into ``path``, written whole (a dot-named temp
        file beside it, flushed and renamed over it); False when there is no
        such file."""
        _parent, _name, found = self._file(key)
        if found is None:
            return False
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.part")
        try:
            with open(tmp, "xb") as f:
                resp = self._call("GET", _api(f"/files/{found['id']}"), params={"alt": "media"}, sink=f)
                if resp.status == 404:
                    tmp.unlink(missing_ok=True)
                    return False
                if resp.status != 200:
                    raise self._fail(f"GET {key}", resp)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return True

    def get_bytes(self, key: str) -> bytes | None:
        """A small file's bytes, read whole; None when there is no such
        file."""
        _parent, _name, found = self._file(key)
        if found is None:
            return None
        resp = self._call("GET", _api(f"/files/{found['id']}"), params={"alt": "media"})
        if resp.status == 404:
            return None
        if resp.status != 200:
            raise self._fail(f"GET {key}", resp)
        return resp.body

    def list_objects(self, prefix: str) -> list[tuple[str, int, float]]:
        """``(key, size, mtime)`` of each file whose key starts with
        ``prefix``, sorted by key: the folders under it walked."""
        if prefix:
            check_key(prefix, prefix=True)
        path, _, start = prefix.rpartition("/")
        top = self._folder(path, create=False)
        if top is None:
            return []
        out, todo = [], [(top, path, start)]
        while todo:
            folder, at, begins = todo.pop()
            found, _ = self._list(f"{_q(folder)} in parents and trashed = false")
            for item in found:
                name = str(item.get("name") or "")
                if not name.startswith(begins):
                    continue
                key = f"{at}/{name}" if at else name
                if item.get("mimeType") == FOLDER_MIME:
                    todo.append((str(item["id"]), key, ""))
                    continue
                try:
                    check_key(key)
                except ValueError:
                    continue  # nothing a key names (a name with a backslash)
                if "size" in item:  # Google's own documents have none
                    out.append((key, int(item["size"]), _mtime(str(item.get("modifiedTime") or ""))))
        return sorted(out)

    def delete_object(self, key: str) -> None:
        """Delete the file for good; one already gone is no error."""
        _parent, _name, found = self._file(key)
        if found is None:
            return
        resp = self._call("DELETE", _api(f"/files/{found['id']}"))
        if resp.status not in (200, 204, 404):
            raise self._fail(f"DELETE {key}", resp)

    def browse(self, path: str) -> dict:
        """What the folder ``path`` holds right in it (``backup_store.Store.
        browse``), one page of it: of several folders of one name the
        oldest, the one a key's walk takes. Only what Gamma made shows
        (``drive.file``)."""
        folder = self._folder(check_key(path) if path else "", create=False)
        if folder is None:
            return listing([], [])
        found, more = self._list(f"{_q(folder)} in parents and trashed = false", one_page=True, order="createdTime")
        folders, files, named = [], [], set()
        for item in found:
            name = str(item.get("name") or "")
            stamp = str(item.get("modifiedTime") or "")
            if item.get("mimeType") != FOLDER_MIME:
                files.append({"name": name, "size": int(item.get("size") or 0), "modified": stamp})
            elif name not in named:
                named.add(name)
                folders.append({"name": name, "modified": stamp})
        return listing(folders, files, more=more)

    def make_folder(self, path: str) -> None:
        """Make the folder ``path`` under the client's folder, and the ones
        above it that are not there; one already there is kept."""
        self._folder(check_key(path), create=True)
