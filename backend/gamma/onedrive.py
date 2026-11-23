"""OneDrive as a backup store (docs/dev/debugging.md "OneDrive",
docs/research/onedrive-icloud-backup.md): ``MICROSOFT``, how a server signs
in to a Microsoft account (gamma/cloud_oauth.py; gamma/cloud_account.py
keeps the connection), and ``Client``, one folder of that account's
OneDrive through Microsoft Graph, a backup store (gamma/backup_store.py
``Store``) as Google Drive's ``gdrive.Client`` and the bucket's
``s3.Client`` are.

Gamma's own app (``config.ONEDRIVE_CLIENT_ID``) is a public client of the
Microsoft identity platform: it has no secret, takes personal and work or
school accounts (the ``common`` tenant), and signs in with the device flow
(a code entered at microsoft.com/devicelogin) anywhere and with
Microsoft's page, sent back to ``http://localhost/...`` (the port is not
matched), on the server's own computer. The scopes are ``Files.ReadWrite``
(the person's OneDrive, of which Gamma touches only the folder chosen:
``Files.ReadWrite.AppFolder`` is documented for personal accounts alone),
``User.Read`` for the account's address and ``offline_access`` for a
refresh token. Microsoft rotates refresh tokens on every renewal and has
no endpoint an app may revoke one at.

``Client`` keeps whole files under its folder, a path of names from the
OneDrive's top (``Gamma backups`` by default). Graph addresses an item by
its path (``/me/drive/root:/Gamma backups/snapshots/ws1/x.zip``) and its
names are unique in a folder (ignoring case), so a key is that path and a
write replaces what is there (``conflictBehavior=replace``). Folders are
made, one name at a time, as a write needs them. ``put_object`` sends a
small file in one PUT and a larger one in an upload session (``CHUNK`` at
a time, a multiple of the 320 KiB Graph asks for; its chunks carry no
Authorization header; resumed where Graph says it stopped, started over
once when the session is gone); ``get_object`` downloads into a local
file, written whole (Graph redirects to a pre-signed address elsewhere,
which the transport reaches without the token); ``delete_object`` deletes
for good (``permanentDelete``, else to the recycle bin where a drive does
not take it); ``browse`` and ``make_folder`` are the folder browser's,
which sees every folder of the OneDrive; ``about`` names the account.
"""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import cloud_http, config
from .backup_store import check_key
from .cloud_http import CloudError, CloudUnreachable, Response
from .cloud_oauth import Service

SCOPES = ("Files.ReadWrite", "User.Read", "offline_access")
CHUNK = 32 * 320 * 1024     # 10 MiB: an upload session's chunk, a multiple of 320 KiB and under 60 MiB
SIMPLE_MAX = 4 * 1024 * 1024  # up to this much goes in one PUT
LIST_PAGE = 1000
JSON = {"Content-Type": "application/json"}
CLIENT_ID_RE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")

MICROSOFT = Service(
    name="Microsoft",
    device_url=lambda: f"{config.microsoft_login_base()}/common/oauth2/v2.0/devicecode",
    token_url=lambda: f"{config.microsoft_login_base()}/common/oauth2/v2.0/token",
    authorize_url=lambda: f"{config.microsoft_login_base()}/common/oauth2/v2.0/authorize",
    scopes=SCOPES,
    required="Files.ReadWrite",
    verification_url="https://microsoft.com/devicelogin",
    client_hint='it must be a public client, with "Allow public client flows" on',
    send_secret=False,
    authorize_params=(("prompt", "select_account"),),
)


def _graph(path: str) -> str:
    return f"{config.microsoft_graph_base()}/v1.0{path}"


def _quote(path: str) -> str:
    return "/".join(urllib.parse.quote(name, safe="") for name in path.split("/"))


def _at(path: str, action: str = "") -> str:
    """The Graph address of the item at ``path`` from the OneDrive's top
    ("" the top itself), or of one of its actions (``children``,
    ``content``, ``createUploadSession``)."""
    if not path:
        return _graph("/me/drive/root" + (f"/{action}" if action else ""))
    return _graph(f"/me/drive/root:/{_quote(path)}" + (f":/{action}" if action else ""))


def _mtime(stamp: str) -> float:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0.0


def _next_byte(ranges) -> int | None:
    """The first byte an upload session still expects (``["26-"]``,
    ``["26-99", "120-"]``); None when it expects none."""
    if not ranges:
        return None
    try:
        return min(int(str(r).split("-", 1)[0]) for r in ranges)
    except ValueError:
        return None


class Client:
    """One folder of one Microsoft account's OneDrive (``folder``, its path
    from the top; "" the top itself), reached with the access token
    ``auth(renew)`` gives. ``attempts``: how often a call is tried."""

    def __init__(self, auth: Callable[[bool], str], folder: str, *, attempts: int = cloud_http.ATTEMPTS):
        self.auth = auth
        self.folder = folder
        self.attempts = max(1, attempts)
        self.where = cloud_http.where("OneDrive", folder)
        self._base = folder.split("/") if folder else []
        self._made: set[str] = set()  # folders known to be there, by their path from the top
        self._permanent = True  # whether the drive takes permanentDelete; a personal OneDrive refuses it
        self._lock = threading.Lock()

    # --- calls ---

    def _call(self, method: str, url: str, **kwargs) -> Response:
        return cloud_http.call(method, url, self.auth, attempts=self.attempts, **kwargs)

    def _fail(self, what: str, resp: Response) -> CloudError:
        reason, message = cloud_http.error(resp)
        if reason in ("quotaLimitReached", "insufficientStorage") or resp.status == 507:
            return CloudError(f"{what} in {self.where}: the Microsoft account's storage is full")
        return CloudError(f"{what} in {self.where}: {resp.status} {message or reason}".rstrip())

    def _json(self, what: str, method: str, url: str, **kwargs) -> dict:
        resp = self._call(method, url, **kwargs)
        if resp.status not in (200, 201):
            raise self._fail(what, resp)
        return resp.json()

    def _path(self, key: str = "") -> str:
        """``key``'s path from the OneDrive's top: under the folder."""
        return "/".join(self._base + ([key] if key else []))

    def _item(self, path: str) -> dict | None:
        """The item at ``path`` from the top, None when there is none."""
        resp = self._call("GET", _at(path), params={"$select": "id,name,size,file,folder,parentReference"})
        if resp.status == 404:
            return None
        if resp.status != 200:
            raise self._fail(f"GET {path}", resp)
        return resp.json()

    def _children(self, path: str, *, one_page: bool = False) -> tuple[list[dict], bool] | None:
        """What the folder at ``path`` holds right in it (``name, size,
        file|folder, lastModifiedDateTime``), every page or the first, and
        whether there are more; None when there is no such folder."""
        url = _at(path, "children")
        params = {"$top": LIST_PAGE, "$select": "id,name,size,file,folder,lastModifiedDateTime"}
        out = []
        while True:
            resp = self._call("GET", url, params=params)
            if resp.status == 404:
                return None
            if resp.status != 200:
                raise self._fail(f"LIST {path or '/'}", resp)
            data = resp.json()
            out.extend(item for item in data.get("value") or () if isinstance(item, dict) and item.get("name"))
            url, params = data.get("@odata.nextLink"), None  # the next page's address carries its own query
            if not url or one_page:
                return out, bool(url)

    def _folder(self, path: str) -> None:
        """Make the folder at ``path`` from the top, and those above it that
        are not there; one there already is kept."""
        with self._lock:
            here = ""
            for name in path.split("/"):
                parent, here = here, f"{here}/{name}" if here else name
                if here.lower() in self._made:
                    continue
                resp = self._call("POST", _at(parent, "children"), headers=JSON, body=json.dumps({
                    "name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}).encode("utf-8"))
                if resp.status == 409:  # there already: a folder, or a file in the way
                    item = self._item(here)
                    if item is None or "folder" not in item:
                        raise CloudError(f"MKDIR {here} in OneDrive: a file of that name is in the way")
                elif resp.status not in (200, 201):
                    raise self._fail(f"MKDIR {here}", resp)
                self._made.add(here.lower())

    # --- the store's methods (backup_store.Store) ---

    def check(self) -> tuple[int, bool]:
        """How many things the folder holds, up to LIST_PAGE, and whether
        there are more; (0, False) before the first copy made it."""
        found = self._children(self._path(), one_page=True)
        return (0, False) if found is None else (len(found[0]), found[1])

    def put_object(self, key: str, path: Path, tick=None) -> None:
        """Store the local file at ``path`` as ``key``, streamed, replacing
        what the key held; ``tick`` hears each run of bytes Graph took.
        FileNotFoundError, never a CloudError, for a file not there."""
        with open(path, "rb") as f:
            self._put(key, f, os.fstat(f.fileno()).st_size, tick)

    def put_bytes(self, key: str, data: bytes) -> None:
        self._put(key, io.BytesIO(data), len(data), None)

    def _put(self, key: str, f, size: int, tick) -> None:
        check_key(key)
        target = self._path(key)
        parent = target.rpartition("/")[0]
        if parent:
            self._folder(parent)
        if size <= SIMPLE_MAX:
            resp = self._call("PUT", _at(target, "content"), params={"@microsoft.graph.conflictBehavior": "replace"},
                              headers={"Content-Type": "application/octet-stream"}, body=f.read())
            if resp.status not in (200, 201):
                raise self._fail(f"PUT {key}", resp)
            if tick and size:
                tick(size)
            return
        for _restart in range(2):  # a session Graph forgot (404) starts over once
            session = self._json(f"PUT {key}", "POST", _at(target, "createUploadSession"), headers=JSON,
                                 body=json.dumps({"item": {"@microsoft.graph.conflictBehavior": "replace"}}).encode())
            url = str(session.get("uploadUrl") or "")
            if not url:
                raise CloudError(f"PUT {key} in {self.where}: Microsoft gave no upload address")
            if self._send(key, url, f, size, tick):
                return
        raise CloudError(f"PUT {key} in {self.where}: Microsoft lost the upload twice")

    def _send(self, key: str, url: str, f, size: int, tick) -> bool:
        """Send the file's bytes to the upload session at ``url`` a chunk at
        a time, without the token, resuming where Graph says it stopped
        (asked after a chunk got no answer, a 5xx or a 416). False when
        Graph forgot the session."""
        sent, attempt, ask = 0, 0, False
        while True:
            resp = None
            try:
                if ask:
                    resp = cloud_http.request("GET", url, {})
                    if resp.status == 200:
                        now = _next_byte(resp.json().get("nextExpectedRanges"))
                        if now is None:  # it holds every byte: the file is complete
                            return True
                        if now > sent and tick:
                            tick(now - sent)
                        sent, ask = now, False
                        continue
                else:
                    f.seek(sent)
                    chunk = f.read(CHUNK)
                    resp = cloud_http.request("PUT", url, {
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {sent}-{sent + len(chunk) - 1}/{size}"}, chunk)
                    if resp.status in (200, 201):
                        if tick and size > sent:
                            tick(size - sent)
                        return True
                    if resp.status == 202:
                        now = _next_byte(resp.json().get("nextExpectedRanges"))
                        now = sent + len(chunk) if now is None else now
                        if now <= sent:
                            raise CloudError(f"PUT {key} in {self.where}: Microsoft took none of the upload")
                        if tick:
                            tick(now - sent)
                        sent, attempt = now, 0
                        continue
                    if resp.status == 416:  # a range Graph has already: ask where it stands
                        ask = True
                        continue
            except CloudUnreachable:
                resp = None
            if resp is not None and resp.status == 404:
                return False
            if resp is not None and not cloud_http.retried(resp):
                raise self._fail(f"PUT {key}", resp)
            attempt += 1
            if attempt >= self.attempts:
                if resp is None:
                    raise CloudUnreachable(f"PUT {key} in {self.where}: Microsoft could not be reached")
                raise self._fail(f"PUT {key}", resp)
            ask = True
            cloud_http.sleep(cloud_http.pause(attempt - 1, resp))

    def get_object(self, key: str, path: Path) -> bool:
        """Download ``key`` into ``path``, written whole (a dot-named temp
        file beside it, flushed and renamed over it); False when there is no
        such file."""
        check_key(key)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.part")
        try:
            with open(tmp, "xb") as f:
                resp = self._call("GET", _at(self._path(key), "content"), sink=f)
                if resp.status == 200:
                    f.flush()
                    os.fsync(f.fileno())
            if resp.status == 404:
                tmp.unlink(missing_ok=True)
                return False
            if resp.status != 200:
                raise self._fail(f"GET {key}", resp)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return True

    def get_bytes(self, key: str) -> bytes | None:
        check_key(key)
        resp = self._call("GET", _at(self._path(key), "content"))
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
        at, _, start = prefix.rpartition("/")
        out, todo = [], [(at, start)]
        while todo:
            folder, begins = todo.pop()
            found = self._children(self._path(folder))
            for item in (found[0] if found else ()):
                name = str(item["name"])
                if not name.startswith(begins):
                    continue
                key = f"{folder}/{name}" if folder else name
                if "folder" in item:
                    todo.append((key, ""))
                    continue
                try:
                    check_key(key)
                except ValueError:
                    continue
                out.append((key, int(item.get("size") or 0), _mtime(str(item.get("lastModifiedDateTime") or ""))))
        return sorted(out)

    def delete_object(self, key: str) -> None:
        """Delete the file for good; one already gone is no error. Where the
        drive does not take a permanent delete (a personal OneDrive answers
        400) it goes to the recycle bin, which empties itself, and the
        client asks no more."""
        check_key(key)
        item = self._item(self._path(key))
        if item is None:
            return
        drive = (item.get("parentReference") or {}).get("driveId")
        if drive and self._permanent:
            resp = self._call("POST", _graph(f"/drives/{drive}/items/{item['id']}/permanentDelete"))
            if resp.status in (200, 204, 404):
                return
            if resp.status not in (400, 403, 405, 501):
                raise self._fail(f"DELETE {key}", resp)
            self._permanent = False
        resp = self._call("DELETE", _graph(f"/me/drive/items/{item['id']}"))
        if resp.status not in (200, 204, 404):
            raise self._fail(f"DELETE {key}", resp)

    def browse(self, path: str) -> dict:
        """What the folder ``path`` (under the client's folder; "" the folder
        itself) holds right in it, for the pane's folder browser:
        ``{folders: [{name, modified}], files: [{name, size, modified}],
        more}``, one page of it, nothing for a folder not there."""
        found = self._children(self._path(check_key(path) if path else ""), one_page=True)
        if found is None:
            return {"folders": [], "files": [], "more": False}
        folders, files = [], []
        for item in sorted(found[0], key=lambda i: str(i["name"]).lower()):
            stamp = str(item.get("lastModifiedDateTime") or "")
            if "folder" in item:
                folders.append({"name": item["name"], "modified": stamp})
            else:
                files.append({"name": item["name"], "size": int(item.get("size") or 0), "modified": stamp})
        return {"folders": folders, "files": files, "more": found[1]}

    def make_folder(self, path: str) -> None:
        self._folder(self._path(check_key(path)))

    def about(self) -> dict:
        """The account: ``{email, name, used, limit}`` (bytes; ``limit`` 0
        when Graph gives none)."""
        drive = self._json("ABOUT", "GET", _graph("/me/drive"), params={"$select": "owner,quota"})
        quota, owner = drive.get("quota") or {}, ((drive.get("owner") or {}).get("user") or {})
        me = self._call("GET", _graph("/me"), params={"$select": "displayName,mail,userPrincipalName"})
        user = me.json() if me.status == 200 else {}
        return {"email": str(user.get("mail") or user.get("userPrincipalName") or owner.get("email") or ""),
                "name": str(user.get("displayName") or owner.get("displayName") or ""),
                "used": int(quota.get("used") or 0), "limit": int(quota.get("total") or 0)}
