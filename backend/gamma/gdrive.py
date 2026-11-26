"""Google Drive as a backup store (docs/dev/debugging.md "Google Drive",
docs/research/google-drive-backup.md): ``GOOGLE``, how a server signs in to
Google (gamma/cloud_oauth.py; gamma/cloud_account.py keeps the connection),
and ``Client``, one folder of that Drive, a backup
store (gamma/backup_store.py ``Store``) as the bucket's ``s3.Client`` and
OneDrive's ``onedrive.Client`` are, so the backup tasks' snapshots
(gamma/snapshot_copies.py) go to any of them alike.

The scope is ``drive.file`` alone: Gamma sees only the files it made, and
the device flow (a code the person enters at google.com/device) is the one
Google offers a server at any address for it. Gamma's own clients
(``config.GOOGLE_DRIVE_CLIENT``, a "TVs and Limited Input devices" one for
the code, and ``GOOGLE_DRIVE_DESKTOP_CLIENT``, a "Desktop app" one for
Google's page on the server's own computer) have secrets Google does not
treat as secret.

``Client`` keeps whole files under its folder in My Drive, a path of
folder names from My Drive's top (``Gamma backups`` by default,
``Backups/Gamma`` a folder in a folder; "" My Drive itself, where the
folder browser starts). Keys are paths: ``snapshots/<ws>/<name>.zip`` is
the file ``<name>.zip`` in the folder ``snapshots/<ws>/`` under it, made as
a write needs it. Drive names are not unique, so a write first looks for
its name in its folder and replaces that file's content; a lookup takes the
oldest of several, and so does ``browse``, which lists a folder's folders
and files for the folder browser; ``make_folder`` makes a folder there.
``put_object`` streams a local file up in a resumable session (``CHUNK`` at
a time, resumed where Drive says it stopped); ``get_object`` downloads into
a local file, written whole; ``put_bytes`` and ``get_bytes`` handle a small
object; ``list_objects`` gives ``(key, size, mtime)`` under a prefix,
``mtime`` when Drive last changed the file; ``delete_object`` deletes for
good (not to the trash, which would keep counting against the quota);
``check`` counts what the folder holds; ``about`` names the account.

Every call goes through gamma/cloud_http.py with the access token ``auth``
gives (the connection's, cloud_account); a 401 asks it for a renewed one
once. A 429, a 5xx, a rate-limit 403 or no answer is tried again after an
exponential pause, at most ``attempts`` times. A call Google refuses or
that gets no answer raises ``CloudError``.
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
from pathlib import Path
from typing import Callable

from . import cloud_http, config
from .backup_store import check_key
from .cloud_http import CloudError, CloudUnreachable, Response
from .cloud_oauth import Service

SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"
CHUNK = 32 * 256 * 1024     # 8 MiB: an upload's chunk, a multiple of the 256 KiB Drive requires
LIST_PAGE = 1000            # what one listing returns at most, and what ``check`` counts up to

GOOGLE = Service(
    name="Google",
    device_url=lambda: f"{config.google_oauth_base()}/device/code",
    token_url=lambda: f"{config.google_oauth_base()}/token",
    authorize_url=lambda: f"{config.google_accounts_base()}/o/oauth2/v2/auth",
    revoke_url=lambda: f"{config.google_oauth_base()}/revoke",
    scopes=(SCOPE,),
    required=SCOPE,
    verification_url="https://www.google.com/device",
    client_hint='it must be of the "TVs and Limited Input devices" type',
    # Offline access, and the consent always shown, so a refresh token comes back.
    authorize_params=(("access_type", "offline"), ("prompt", "consent")),
)
CLIENT_ID_RE = re.compile(r"\S{1,200}\.apps\.googleusercontent\.com")


def _api(path: str) -> str:
    return f"{config.google_api_base()}/drive/v3{path}"


def _upload_api(path: str) -> str:
    return f"{config.google_api_base()}/upload/drive/v3{path}"


def _q(value: str) -> str:
    """A string literal in a Drive query."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


class Client:
    """One folder of one Google account's My Drive (``folder``, its path
    from My Drive's top; "" My Drive itself), reached with the access token
    ``auth(renew)`` gives. ``attempts``: how often a call is tried; the
    pane's Test tries once."""

    def __init__(self, auth: Callable[[bool], str], folder: str, *, attempts: int = cloud_http.ATTEMPTS):
        self.auth = auth
        self.folder = folder
        self.attempts = max(1, attempts)
        self.where = cloud_http.where("Google Drive", folder)
        self._base = folder.split("/") if folder else []
        self._folders: dict[str, str] = {}  # a folder's path from My Drive's top → its id, as walked
        self._lock = threading.Lock()

    # --- calls ---

    def _call(self, method: str, url: str, **kwargs) -> Response:
        return cloud_http.call(method, url, self.auth, attempts=self.attempts, **kwargs)

    def _fail(self, what: str, resp: Response) -> CloudError:
        reason, message = cloud_http.error(resp)
        if reason == "storageQuotaExceeded":
            return CloudError(f"{what} in {self.where}: the Google account's storage is full")
        return CloudError(f"{what} in {self.where}: {resp.status} {message or reason}".rstrip())

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
        FileNotFoundError, never a CloudError."""
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
        raise CloudError(f"PUT {key} in {self.where}: Google lost the upload twice")

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
                resp = cloud_http.request("PUT", session, headers, chunk)
            except CloudUnreachable:
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
                        raise CloudError(f"PUT {key} in {self.where}: Google took none of the upload")
                sent, ask = now, False
                continue
            if resp is not None and resp.status in (404, 410):
                return False
            if resp is not None and not cloud_http.retried(resp):
                raise self._fail(f"PUT {key}", resp)
            attempt += 1
            if attempt >= self.attempts:
                if resp is None:
                    raise CloudUnreachable(f"PUT {key} in {self.where}: Google could not be reached")
                raise self._fail(f"PUT {key}", resp)
            ask = True
            cloud_http.sleep(cloud_http.pause(attempt - 1, resp))

    def get_object(self, key: str, path: Path) -> bool:
        """Download ``key`` into ``path``, written whole (a dot-named temp
        file beside it, flushed and renamed over it); False when there is no
        such file."""
        _parent, _name, found = self._file(key)
        if found is None:
            return False
        # a 404 now: gone since it was found
        return cloud_http.download(
            path, lambda f: self._call("GET", _api(f"/files/{found['id']}"), params={"alt": "media"}, sink=f),
            lambda resp: self._fail(f"GET {key}", resp))

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
                    out.append((key, int(item["size"]), cloud_http.mtime(str(item.get("modifiedTime") or ""))))
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
        """What the folder ``path`` (under the client's folder; "" the folder
        itself) holds right in it, for the pane's folder browser:
        ``{folders: [{name, modified}], files: [{name, size, modified}],
        more}``, one page of it (``more`` when there was another), nothing
        for a folder not there. Of several folders of one name the oldest,
        the one a key's walk takes. Only what Gamma made shows
        (``drive.file``)."""
        folder = self._folder(check_key(path) if path else "", create=False)
        if folder is None:
            return {"folders": [], "files": [], "more": False}
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
        return {"folders": folders, "files": files, "more": more}

    def make_folder(self, path: str) -> None:
        """Make the folder ``path`` under the client's folder, and the ones
        above it that are not there; one already there is kept."""
        self._folder(check_key(path), create=True)
