"""An in-memory Zotero for the tests and the browser e2e scenarios: the Web
API v3 as a Zotero link uses it (docs/dev/zotero_sync.md), zotero.org's
OAuth steps and the file storage, with Zotero's versions and refusals.

``FakeZotero`` keeps libraries (``users/<id>``, ``groups/<id>``) of
collections and items; every write bumps the library's version and stamps
what it changed with it. A test builds content with the builders, which
change the library as a Zotero desktop would (``add_collection``,
``add_item``, ``add_file``, ``edit``, ``trash``, ``restore``, ``delete``;
``acting_as`` another group member; ``zotero_fixtures.standard_library``), then
``install(monkeypatch)`` points gamma at it: the API and web bases through
the environment, ``zotero_web._request`` to ``request``, the throttle's
sleep to ``sleeps`` (instant; the waits asked are kept). ``calls`` lists
every request as ``(method, path with query, lower-case headers)``.
``fail_next`` makes the next matching requests fail; ``on_request`` runs a
change just before one is answered (the library moving mid-walk).
``sent`` keeps each API write's JSON body as ``(method, path, body)``.
``serve()`` runs the same router over HTTP for the browser e2e: ``python
-I backend/tests/zotero_fake.py --port 0`` prints ``ZOTERO <base>`` and
serves the standard library until killed.

What it checks and answers as Zotero does (checked against the real API
on 28 October 2026, docs/research/zotero-sync.md, unless marked
"unverified" where it stands): keys and per-library access (read, write,
files; a key without notes access sees no note anywhere), ``limit`` (at
most 100) and 50 objects a write, versions per
object (428 without, 412 when stale, ``version: 0`` for a key that must not
exist) and per library (``If-Unmodified-Since-Version`` on a multi-object
write refuses it whole when the library moved), ``If-Modified-Since-Version``
(304), ``since``, ``format=versions``/``keys`` (unpaged), ``itemType``
filters (a negation also leaves out annotations), ``itemKey``,
``collectionKey``, ``includeTrashed`` (a trashed collection is listed
whatever the query), PATCH and PUT of one object, the multi-object POST's
PATCH semantics, the annotation rules (parent, type, text, colour, sort
index, page label, position), group members (``meta.createdByUser``; an
annotation is its creator's alone), the trash (``deleted``: an item
alone, a collection a desktop trashes with its subcollections; never a
write's cascade) and ``/deleted`` (hard deletes only), tags (a manual
one's type is not answered back), the attachments' shapes (stored files,
linked files, a note's embedded pictures, a WebDAV user's file Zotero's
storage never got), the upload steps (``If-None-Match``/``If-Match``, md5
and size, ``quota``) and zipped snapshots. Beyond Zotero: storage refuses
Zotero's headers, so a client
that leaks the key fails its test. Nothing here imports gamma at module
level: the e2e runs this file outside the backend's environment.
"""

import argparse
import contextlib
import copy
import hashlib
import io
import json
import re
import secrets
import sys
import threading
import time
import urllib.parse
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

KEY_ALPHABET = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
_KEY_RE = re.compile(f"^[{KEY_ALPHABET}]{{8}}$")
_LIBRARY_PATH_RE = re.compile(r"^/((?:users|groups)/[0-9]+)(/.*)$")
PAGE_MAX = 100
DEFAULT_LIMIT = 25
WRITE_MAX = 50
FULL_ACCESS = {"user": {"library": True, "files": True, "notes": True, "write": True},
               "groups": {"all": {"library": True, "write": True}}}
REQUEST_TOKEN, REQUEST_SECRET, GOOD_VERIFIER = "req-token", "req-secret", "good-verifier"
_BOUNDARY = "fakezoteroboundary"
_UPLOAD_TYPE = f"multipart/form-data; boundary={_BOUNDARY}"
_UPLOAD_PREFIX = f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="file"\r\n\r\n'
_UPLOAD_SUFFIX = f"\r\n--{_BOUNDARY}--\r\n"
CHUNK = 64 * 1024  # bytes per write into a sink and per read of a streamed body
_DELETED_KINDS = ("collections", "items", "searches", "tags", "settings")
ANNOTATION_TYPES = ("highlight", "underline", "note", "image", "ink", "text")
_TEXT_KINDS = ("highlight", "underline")
_PDF_ONLY = ("image", "ink", "text")
_READER_TYPES = ("application/pdf", "application/epub+zip", "text/html")
# Attachments whose file Zotero's storage keeps (filename, md5, mtime), and
# every attachment of a file (a linked URL is none): what an annotation may
# sit on.
_STORED_MODES = ("imported_file", "imported_url", "embedded_image")
_FILE_MODES = (*_STORED_MODES, "linked_file")
_COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")
_PDF_SORT_RE = re.compile(r"^\d{5}\|\d{6}\|\d{5}$")
TEXT_MAX, LABEL_MAX, POSITION_MAX = 7500, 50, 65535
VERSIONS_REQUIRED = ("Either If-Unmodified-Since-Version or object version property must be provided for "
                     "key-based writes")
# Keys an item's data holds whatever its type, and those of the three types
# the schema lists without fields.
_ITEM_KEYS = {"key", "version", "itemType", "creators", "tags", "collections", "relations", "dateAdded",
              "dateModified", "deleted", "inPublications"}
_CHILD_KEYS = {"key", "version", "itemType", "parentItem", "tags", "relations", "dateAdded", "dateModified",
               "deleted", "collections"}
_TYPE_KEYS = {
    "attachment": _CHILD_KEYS | {"linkMode", "title", "accessDate", "url", "note", "contentType", "charset",
                                 "filename", "md5", "mtime", "path"},
    "note": _CHILD_KEYS | {"note"},
    "annotation": _CHILD_KEYS | {"annotationType", "annotationAuthorName", "annotationText", "annotationComment",
                                 "annotationColor", "annotationPageLabel", "annotationSortIndex",
                                 "annotationPosition", "annotationIsExternal"},
}


def _schema() -> dict:
    """Zotero's item types and their fields (gamma/zotero_schema.json beside
    this checkout's backend), {} when it cannot be read."""
    try:
        path = Path(__file__).resolve().parent.parent / "gamma" / "zotero_schema.json"
        return {name: list(spec["fields"]) for name, spec in json.loads(path.read_text("utf-8"))["types"].items()}
    except (OSError, ValueError, KeyError):
        return {}


_FIELDS = _schema()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class Reply:
    """What the router answers: status, headers, body."""

    def __init__(self, status: int, body: bytes = b"", headers: dict | None = None):
        self.status, self.body, self.headers = status, body, dict(headers or {})


def _text(status: int, message: str, headers: dict | None = None) -> Reply:
    return Reply(status, message.encode("utf-8"), {"Content-Type": "text/plain", **(headers or {})})


def _json(value, status: int = 200, headers: dict | None = None) -> Reply:
    return Reply(status, json.dumps(value).encode("utf-8"), {"Content-Type": "application/json", **(headers or {})})


class _Refused(Exception):
    """A write Zotero refuses as a whole: ``status`` and Zotero's sentence."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _type_matches(rule: str, item_type: str) -> bool:
    """Zotero's ``itemType`` filter: ``X``, ``a || b``, ``-X`` (all but X,
    and never an annotation: a negation leaves them out too), clauses
    joined by ``&&``."""
    for clause in rule.split("&&"):
        clause = clause.strip()
        if clause.startswith("-"):
            if item_type in (clause[1:].strip(), "annotation"):
                return False
        elif item_type not in {part.strip() for part in clause.split("||")}:
            return False
    return True


def _sorted(found: list, params: dict) -> list:
    """``found`` (objects in the order they were made) in Zotero's listing
    order: by ``sort`` (``dateModified`` unless asked: the fake sorts it by
    version), in ``direction`` (descending for a date by default); ties in
    the order they were made, reversed when descending."""
    field = params.get("sort") or "dateModified"
    descending = (params.get("direction") or ("desc" if field.startswith("date") else "asc")) == "desc"

    def rank(pair):
        n, d = pair
        if field == "dateModified":
            return d["version"], n
        value = d.get("title", d.get("name", "")) if field == "title" else d.get(field, "")
        return str(value).lower(), n

    return [d for _n, d in sorted(enumerate(found), key=rank, reverse=descending)]


class _Library:
    def __init__(self, library: str, name: str):
        self.id, self.name, self.version = library, name, 0
        self.number = int(library.split("/")[1])
        self.collections: dict[str, dict] = {}
        self.items: dict[str, dict] = {}
        self.files: dict[str, bytes] = {}
        self.zipped: dict[str, bytes] = {}  # what the storage serves for a file kept zipped
        self.deleted: dict[str, dict[str, int]] = {kind: {} for kind in _DELETED_KINDS}  # key → version
        self.authors: dict[str, dict] = {}  # key → {"created": user id, "modified": user id}

    @property
    def group(self) -> bool:
        return self.id.startswith("groups/")

    def bump(self) -> int:
        self.version += 1
        return self.version

    def store(self, key: str) -> dict:
        return self.items if key in self.items else self.collections


class FakeZotero:
    """One Zotero user (``user_id``, ``username``) with My Library and the
    groups ``add_group`` makes, reachable with ``api_key`` (whose access is
    ``access``, every permission by default) and the keys the OAuth steps
    hand out; ``add_user`` makes other users (group members) with keys of
    their own. ``storage_base`` is where file transfers go (the API's
    origin unless a test moves it); ``quota`` caps each library's stored
    bytes (None: no cap)."""

    def __init__(self, *, user_id=123, username="tim", api_key="zkey-good", base="https://api.zotero.test",
                 web_base="https://www.zotero.test", access=None):
        self.user_id, self.username, self.api_key = user_id, username, api_key
        self.base, self.web_base = base.rstrip("/"), web_base.rstrip("/")
        self.storage_base = self.base
        self.users = {user_id: username}
        self.keys = {api_key: copy.deepcopy(access if access is not None else FULL_ACCESS)}
        self.key_users = {api_key: user_id}
        self.libraries = {f"users/{user_id}": _Library(f"users/{user_id}", username)}
        self.groups: dict[int, dict] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.sent: list[tuple[str, str, object]] = []  # each API write's JSON body: (method, path, body)
        self.sleeps: list[float] = []
        self.oauth: list[dict] = []  # each OAuth step: {"path", "url", "params"} (the Authorization fields)
        self.uploads: dict[str, dict] = {}
        self.quota: int | None = None
        self._failures: list[tuple[int, str, dict, str | None]] = []
        self._hooks: list[list] = []  # [needle, fn, times left, method or None]
        self._callbacks: dict[str, str] = {}
        self._minted = 0
        self._counter = 0
        self._actor = user_id
        self._lock = threading.RLock()

    @property
    def library(self) -> str:
        return f"users/{self.user_id}"

    # --- people and groups ---------------------------------------------------

    def add_user(self, user_id: int, username: str, *, api_key: str | None = None) -> str:
        """Another Zotero user (a member of a group the test makes), with a
        key of their own reaching every group they belong to; the key."""
        with self._lock:
            self.users[user_id] = username
            api_key = api_key or f"zkey-{username}"
            self.keys[api_key] = {"user": {"library": True, "files": True, "notes": True, "write": True},
                                  "groups": {"all": {"library": True, "write": True}}}
            self.key_users[api_key] = user_id
        return api_key

    def add_group(self, group_id: int, name: str, *, write: bool = True, members: dict | None = None) -> str:
        """A group the user belongs to, its owner unless ``write`` is False
        (then a plain member of a group only admins edit). ``members`` adds
        others, ``{user_id: username}``. Its library id."""
        library = f"groups/{group_id}"
        with self._lock:
            for uid, uname in (members or {}).items():
                self.users.setdefault(uid, uname)
            self.libraries[library] = _Library(library, name)
            self.groups[int(group_id)] = {"name": name, "write": write, "owner": self.user_id if write else 999,
                                          "admins": [], "members": [self.user_id, *(members or {})]}
        return library

    @contextlib.contextmanager
    def acting_as(self, user_id: int):
        """The builders' changes inside are by ``user_id`` (a group member):
        ``meta.createdByUser`` / ``lastModifiedByUser`` name them."""
        with self._lock:
            before, self._actor = self._actor, user_id
            try:
                yield
            finally:
                self._actor = before

    # --- building content ----------------------------------------------------

    def _new_key(self) -> str:
        while True:
            self._counter += 1
            n = int.from_bytes(hashlib.sha256(f"fake-{self._counter}".encode()).digest(), "big")
            key = "".join(KEY_ALPHABET[(n // len(KEY_ALPHABET) ** i) % len(KEY_ALPHABET)] for i in range(8))
            if not any(key in lib.items or key in lib.collections for lib in self.libraries.values()):
                return key

    def add_collection(self, library: str, name: str, parent: str | None = None, key: str | None = None) -> str:
        return self.add_item(library, {"name": name, "parentCollection": parent or False}, key, kind="collections")

    def add_item(self, library: str, data: dict, key: str | None = None, *, kind: str = "items") -> str:
        """An object from API-shaped ``data`` (an item's ``itemType`` and
        fields, ``parentItem`` for a child; a collection's ``name``), made
        as a create through the API would make it and checked as one is
        (AssertionError for what Zotero refuses); its key."""
        with self._lock:
            lib = self.libraries[library]
            data = copy.deepcopy(data)
            key = key or data.pop("key", None) or self._new_key()
            data.pop("version", None)
            made = self._made(kind, data)
            problem = self._problem(lib, kind, key, made, None, self._actor)
            assert problem is None, problem
            self._put(lib, kind, key, made, lib.bump(), self._actor)
        return key

    def add_file(self, library: str, key: str, content: bytes, *, zipped: bool = False, stored: bool = True) -> None:
        """Store ``content`` as the attachment ``key``'s file; ``zipped``
        keeps it as Zotero keeps a snapshot of several files: the storage
        serves a ZIP holding it, the item's md5 stays ``content``'s.
        ``stored`` False names the file on the item (md5, mtime) without
        storing it, as a WebDAV user's desktop leaves an attachment: its
        download answers 404."""
        with self._lock:
            lib = self.libraries[library]
            data = lib.items[key]
            assert data["itemType"] == "attachment" and data.get("linkMode") in _STORED_MODES, \
                "only stored attachments have files at Zotero"
            assert stored or not zipped, "a zipped file is a stored one"
            filename = data.get("filename") or f"{key}.pdf"
            self._attach(lib, key, content, filename=filename, mtime=int(time.time() * 1000), stored=stored)
            if zipped:
                packed = io.BytesIO()
                with zipfile.ZipFile(packed, "w") as archive:
                    archive.writestr(filename, content)
                    archive.writestr("images/figure.png", b"not really a picture")
                lib.zipped[key] = packed.getvalue()

    def _attach(self, lib: _Library, key: str, content: bytes, *, filename: str, mtime: int,
                stored: bool = True) -> None:
        data = lib.items[key]
        if stored:
            lib.files[key] = content
        else:
            lib.files.pop(key, None)
        lib.zipped.pop(key, None)
        data.update(md5=_md5(content), filename=filename, mtime=mtime, version=lib.bump(),
                    contentType=data.get("contentType")
                    or ("application/pdf" if content.startswith(b"%PDF") else "application/octet-stream"))

    def edit(self, library: str, key: str, **fields) -> None:
        """Change an object as a Zotero client would (a field set to None
        goes); checked as an API write is."""
        with self._lock:
            lib = self.libraries[library]
            kind = "items" if key in lib.items else "collections"
            existing = lib.store(key)[key]
            merged = self._merged(kind, existing, fields)
            problem = self._problem(lib, kind, key, merged, existing, self._actor)
            assert problem is None, problem
            self._put(lib, kind, key, merged, lib.bump(), self._actor, touched="dateModified" not in fields)

    def trash(self, library: str, key: str) -> None:
        """Move an object to the trash as a desktop does: an item alone (its
        children stay as they are), a collection with its subcollections,
        in one change (their items keep naming them)."""
        self._flag(library, key, True)

    def restore(self, library: str, key: str) -> None:
        """Bring an object back from the trash (a collection with its
        subcollections)."""
        self._flag(library, key, False)

    def _flag(self, library: str, key: str, deleted: bool) -> None:
        with self._lock:
            lib = self.libraries[library]
            if key in lib.items:
                self.edit(library, key, deleted=deleted)
                return
            family, todo = [], [key]
            while todo:
                current = todo.pop()
                family.append(current)
                todo += [k for k, d in lib.collections.items() if d.get("parentCollection") == current]
            version = lib.bump()
            for member in family:
                existing = lib.collections[member]
                merged = self._merged("collections", existing, {"deleted": deleted})
                if merged != existing:
                    self._put(lib, "collections", member, merged, version, self._actor)

    def delete(self, library: str, key: str) -> None:
        """Remove an object for good, as emptying Zotero's trash does
        (``/deleted`` names it): an item with its children, a collection
        with its subcollections, its items leaving it (unverified)."""
        with self._lock:
            lib = self.libraries[library]
            kind = "items" if key in lib.items else "collections"
            version = lib.bump()
            doomed, todo = [], [key]
            while todo:
                current = todo.pop()
                doomed.append(current)
                link = "parentItem" if kind == "items" else "parentCollection"
                todo += [k for k, d in getattr(lib, kind).items() if d.get(link) == current]
            for gone in doomed:
                getattr(lib, kind).pop(gone)
                lib.files.pop(gone, None)
                lib.zipped.pop(gone, None)
                lib.authors.pop(gone, None)
                lib.deleted[kind][gone] = version
            if kind == "collections":
                for data in lib.items.values():
                    if set(data.get("collections") or ()) & set(doomed):
                        data["collections"] = [c for c in data["collections"] if c not in doomed]
                        data["version"] = version

    def data(self, library: str, key: str) -> dict:
        lib = self.libraries[library]
        return copy.deepcopy(lib.items.get(key) or lib.collections[key])

    def version(self, library: str) -> int:
        return self.libraries[library].version

    def items(self, library: str) -> list[dict]:
        return [copy.deepcopy(d) for d in self.libraries[library].items.values()]

    def collections(self, library: str) -> list[dict]:
        return [copy.deepcopy(d) for d in self.libraries[library].collections.values()]

    def fail_next(self, status: int, path_contains: str, headers: dict | None = None, times: int = 1, *,
                  method: str | None = None) -> None:
        """The next request whose path (with its query) contains
        ``path_contains`` (and whose method is ``method``, when given)
        answers ``status`` with ``headers``, once, or the next ``times`` such
        requests (a GET is made again on a 500, 502 or 504:
        ``times=zotero_web.MAX_RETRIES + 1`` outlasts it)."""
        with self._lock:
            self._failures.extend([(status, path_contains, dict(headers or {}), method)] * times)

    def on_request(self, needle: str, fn, times: int = 1, *, method: str | None = None) -> None:
        """Run ``fn()`` (a change, with the builders) just before the next
        ``times`` requests whose path and query contain ``needle`` (and whose
        method is ``method``, when given) are answered: what a Zotero client
        saved meanwhile."""
        with self._lock:
            self._hooks.append([needle, fn, times, method])

    # --- plugging in ---------------------------------------------------------

    def install(self, monkeypatch):
        """Point gamma at this fake for the test; returns the fake."""
        from gamma import zotero_web

        monkeypatch.setenv("GAMMA_ZOTERO_API_BASE", self.base)
        monkeypatch.setenv("GAMMA_ZOTERO_WEB_BASE", self.web_base)
        monkeypatch.setattr(zotero_web, "_request", self.request)
        monkeypatch.setattr(zotero_web, "_sleep", self.sleeps.append)
        monkeypatch.setattr(zotero_web, "_throttles", {})  # no Backoff heard in another test
        return self

    def request(self, method, url, headers, body, timeout, sink=None):
        """``zotero_web._request`` served by the router."""
        from gamma.zotero_web import Response

        origins = {self.base, self.web_base, self.storage_base}
        if urllib.parse.urlsplit(url)._replace(path="", query="", fragment="").geturl() not in origins:
            raise AssertionError(f"the fake Zotero does not serve {url}")
        reply = self.handle(method, url, headers, body)
        data = reply.body
        if sink is not None and 200 <= reply.status < 300:
            for i in range(0, len(data), CHUNK):
                sink.write(data[i:i + CHUNK])
            data = b""
        return Response(reply.status, {k.lower(): v for k, v in reply.headers.items()}, data)

    def serve(self, port: int = 0):
        """Serve the router over HTTP on 127.0.0.1 (in a daemon thread);
        ``(httpd, base)``. The API, web and storage bases all become that
        origin. Stop it with ``httpd.shutdown()``."""
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _any(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                reply = fake.handle(self.command, fake.base + self.path, dict(self.headers.items()), body)
                self.send_response(reply.status)
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                self.wfile.write(reply.body)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _any

            def log_message(self, *args):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        httpd.daemon_threads = True
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        self.base = self.web_base = self.storage_base = base
        threading.Thread(target=httpd.serve_forever, daemon=True, name="fake-zotero").start()
        return httpd, base

    # --- the router ----------------------------------------------------------

    def handle(self, method: str, url: str, headers: dict, body) -> Reply:
        headers = {name.lower(): value for name, value in (headers or {}).items()}
        if hasattr(body, "read"):
            body = b"".join(iter(lambda: body.read(CHUNK), b""))
        body = body or b""
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        path_q = parts.path + (f"?{parts.query}" if parts.query else "")
        params = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        with self._lock:
            self.calls.append((method, path_q, headers))
            if method in ("POST", "PATCH", "PUT") and origin == self.base and body[:1] in (b"[", b"{"):
                try:
                    self.sent.append((method, path_q, json.loads(body)))
                except ValueError:
                    pass
            for hook in [h for h in self._hooks if h[0] in path_q and h[3] in (None, method)]:
                hook[2] -= 1
                if hook[2] <= 0:
                    self._hooks.remove(hook)
                hook[1]()
            for i, (status, needle, extra, only) in enumerate(self._failures):
                if needle in path_q and only in (None, method):
                    del self._failures[i]
                    return Reply(status, b"Injected failure", {"Content-Type": "text/plain", **extra})
            if parts.path.startswith(("/__storage/", "/__upload/")) and origin in (self.base, self.storage_base):
                return self._storage(method, parts.path, headers, body)
            if origin == self.web_base and parts.path.startswith("/oauth/"):
                return self._oauth(method, url, parts.path, params, headers)
            if origin == self.base:
                return self._api(method, parts.path, params, headers, body)
            return _text(404, "Not found")

    def _grant(self, key: str, lib: _Library) -> dict:
        """What ``key`` may do in ``lib``: read, write, read and store files,
        see notes (a group's come with reading it)."""
        access, uid = self.keys[key], self.key_users.get(key, self.user_id)
        if not lib.group:
            mine = (access.get("user") or {}) if lib.number == uid else {}
            read = bool(mine.get("library"))
            write = read and bool(mine.get("write"))
            files = read and bool(mine.get("files"))
            return {"read": read, "write": write, "files_read": files, "files_write": write and files,
                    "notes": read and bool(mine.get("notes"))}
        group = self.groups[lib.number]
        grants = access.get("groups") or {}
        grant = grants.get(str(lib.number)) or grants.get("all") or {}
        read = uid in group["members"] and bool(grant.get("library"))
        editor = group["write"] or uid == group["owner"] or uid in group["admins"]
        write = read and bool(grant.get("write")) and editor
        return {"read": read, "write": write, "files_read": read, "files_write": write, "notes": read}

    def _api(self, method: str, path: str, params: dict, headers: dict, body: bytes) -> Reply:
        key = headers.get("zotero-api-key", "")
        if key not in self.keys:
            return _text(403, "Forbidden")
        uid = self.key_users.get(key, self.user_id)
        if path == "/keys/current":
            if method == "GET":
                return _json({"key": key, "userID": uid, "username": self.users.get(uid, ""), "displayName": "",
                              "access": self.keys[key]})
            if method == "DELETE":
                del self.keys[key]
                return Reply(204)
        match = re.match(r"^/users/([0-9]+)/groups$", path)
        if match and method == "GET":
            if int(match.group(1)) != uid:
                return _text(403, "Forbidden")
            return self._page([gid for gid, g in self.groups.items() if uid in g["members"]], params, 1,
                              self._group)
        match = _LIBRARY_PATH_RE.match(path)
        if not match or match.group(1) not in self.libraries:
            return _text(404, "Not found")
        lib, rest = self.libraries[match.group(1)], match.group(2)
        grant = self._grant(key, lib)
        if not grant["read"]:
            return _text(403, "Forbidden")
        if method in ("POST", "PUT", "PATCH", "DELETE") and not grant["write"]:
            return _text(403, "Write access denied")

        def seen(data) -> bool:  # without notes access Zotero leaves every note out of every read
            return grant["notes"] or data.get("itemType") != "note"

        if rest in ("/items", "/collections") and method == "POST":
            return self._write(lib, rest[1:], headers, body, uid, grant["notes"])
        if rest == "/deleted" and method == "GET":
            try:
                since = int(params["since"])
            except (KeyError, ValueError):
                return _text(400, "'since' must be provided")
            return _json({kind: [k for k, version in lib.deleted[kind].items() if version > since]
                          for kind in _DELETED_KINDS}, headers={"Last-Modified-Version": str(lib.version)})
        if rest in ("/collections", "/collections/top") and method == "GET":
            kept = [d for d in lib.collections.values()
                    if self._kept(d, params, "collections") and (rest == "/collections" or not d["parentCollection"])]
            return self._listing(lib, kept, params, headers, "collections")
        if rest in ("/items", "/items/top") and method == "GET":
            kept = [d for d in lib.items.values()
                    if seen(d) and self._kept(d, params) and (rest == "/items" or not d.get("parentItem"))]
            return self._listing(lib, kept, params, headers)
        match = re.match(r"^/(items|collections)/([^/]+)(/children|/file)?$", rest)
        if not match:
            return _text(404, "Not found")
        kind, item_key, tail = match.groups()
        store = getattr(lib, kind)
        if item_key not in store or not seen(store[item_key]):
            return _text(404, "Not found")
        if tail is None and method == "GET":
            data = store[item_key]
            since = headers.get("if-modified-since-version")
            if since is not None and data["version"] <= int(since):
                return Reply(304, b"", {"Last-Modified-Version": str(data["version"])})
            return _json(self._object(lib, data, kind), headers={"Last-Modified-Version": str(data["version"])})
        if tail is None and method in ("PUT", "PATCH"):
            return self._one(lib, kind, item_key, method, headers, body, uid)
        if kind == "items" and tail == "/children" and method == "GET":
            kept = [d for d in lib.items.values()
                    if d.get("parentItem") == item_key and seen(d) and self._kept(d, params)]
            return self._listing(lib, kept, params, headers)
        if kind == "items" and tail == "/file" and method == "GET":
            if not grant["files_read"]:
                return _text(403, "File access denied")
            if item_key not in lib.files:  # a linked file, a link, a WebDAV user's file
                return _text(404, "Not found")
            found = {"Location": f"{self.storage_base}/__storage/{lib.id}/{item_key}"}
            if item_key in lib.zipped:
                found["Zotero-File-Compressed"] = "Yes"
            return Reply(302, b"", found)
        if kind == "items" and tail == "/file" and method == "POST":
            if not grant["files_write"]:
                return _text(403, "File editing denied")
            return self._file_post(lib, item_key, headers, body)
        return _text(405, "Method not allowed")

    @staticmethod
    def _kept(data: dict, params: dict, kind: str = "items") -> bool:
        """Whether a listing with ``params`` holds ``data``. Zotero lists a
        trashed collection whatever the query."""
        if kind == "items" and data.get("deleted") and params.get("includeTrashed") not in ("1", "true"):
            return False
        if params.get("since") and data["version"] <= int(params["since"]):
            return False
        for name in ("itemKey", "collectionKey"):
            if name in params and data["key"] not in params[name].split(","):
                return False
        return not params.get("itemType") or _type_matches(params["itemType"], data.get("itemType", ""))

    def _listing(self, lib: _Library, found: list, params: dict, headers: dict, kind: str = "items") -> Reply:
        """A multi-object read: 304 when the library is still at the client's
        ``If-Modified-Since-Version``; ``format=versions`` / ``keys`` whole
        (unless ``limit`` is asked); else one page of objects."""
        version = {"Last-Modified-Version": str(lib.version)}
        since = headers.get("if-modified-since-version")
        if since is not None and lib.version <= int(since):
            return Reply(304, b"", version)
        found = _sorted(found, params)
        form = params.get("format")
        if form in ("versions", "keys"):
            if "limit" in params:
                start = int(params.get("start") or 0)
                found = found[start:start + int(params["limit"])]
            headers = {**version, "Total-Results": str(len(found))}
            if form == "keys":
                return _text(200, "".join(d["key"] + "\n" for d in found), headers)
            return _json({d["key"]: d["version"] for d in found}, headers=headers)
        return self._page(found, params, lib.version, lambda d: self._object(lib, d, kind))

    def _page(self, found: list, params: dict, version: int, build) -> Reply:
        """One page of ``found`` (``start``, ``limit``), each made an object
        by ``build``, with Zotero's paging headers."""
        try:
            start = max(0, int(params.get("start") or 0))
            limit = max(1, min(int(params.get("limit") or DEFAULT_LIMIT), PAGE_MAX))
        except ValueError:
            return _text(400, "Invalid start or limit")
        return _json([build(d) for d in found[start:start + limit]],
                     headers={"Total-Results": str(len(found)), "Last-Modified-Version": str(version)})

    def _person(self, uid: int) -> dict:
        name = self.users.get(uid, f"user{uid}")
        return {"id": uid, "username": name, "name": "",
                "links": {"alternate": {"href": f"{self.web_base}/{name}", "type": "text/html"}}}

    def _object(self, lib: _Library, data: dict, kind: str = "items") -> dict:
        key = data["key"]
        links = {"self": {"href": f"{self.base}/{lib.id}/{kind}/{key}", "type": "application/json"}}
        meta = {}
        if kind == "collections":
            meta = {"numCollections": sum(1 for d in lib.collections.values() if d["parentCollection"] == key),
                    "numItems": sum(1 for d in lib.items.values() if key in (d.get("collections") or ()))}
        else:
            if data.get("parentItem"):
                links["up"] = {"href": f"{self.base}/{lib.id}/items/{data['parentItem']}", "type": "application/json"}
            if data.get("itemType") not in ("note", "annotation"):
                meta["numChildren"] = sum(1 for d in lib.items.values() if d.get("parentItem") == key)
            if key in lib.files:
                links["enclosure"] = {"href": f"{self.base}/{lib.id}/items/{key}/file/view",
                                      "type": data.get("contentType", ""), "title": data.get("filename", ""),
                                      "length": len(lib.files[key])}
        who = lib.authors.get(key)
        if lib.group and who:
            meta["createdByUser"] = self._person(who["created"])
            if who["modified"] != who["created"]:
                meta["lastModifiedByUser"] = self._person(who["modified"])
        return {"key": key, "version": data["version"],
                "library": {"type": "group" if lib.group else "user", "id": lib.number, "name": lib.name},
                "links": links, "meta": meta, "data": copy.deepcopy(data)}

    def _group(self, gid: int) -> dict:
        group, lib = self.groups[gid], self.libraries[f"groups/{gid}"]
        editing = "members" if group["write"] else "admins"
        data = {"id": gid, "version": 1, "name": group["name"], "type": "Private", "description": "", "url": "",
                "owner": group["owner"], "admins": list(group["admins"]),
                "members": [m for m in group["members"] if m != group["owner"]],
                "libraryReading": "members", "libraryEditing": editing, "fileEditing": editing}
        return {"id": gid, "version": 1, "links": {}, "meta": {"numItems": len(lib.items)}, "data": data}

    # --- what an object holds --------------------------------------------------

    @staticmethod
    def _made(kind: str, obj: dict) -> dict:
        """A new object's data as Zotero makes it: every field of an item's
        type there, blank where nothing was sent; the dates sent kept."""
        if kind == "collections":
            return {"name": "", "parentCollection": False, "relations": {}, **obj}
        item_type = obj.get("itemType")
        now = _now()
        out = {"parentItem": obj["parentItem"]} if obj.get("parentItem") else {}  # Zotero's order
        out["itemType"] = item_type
        if item_type == "attachment":
            out.update(linkMode="", title="", accessDate="", url="", note="", contentType="", charset="")
            if obj.get("linkMode") in _STORED_MODES:
                out.update(filename="", md5=None, mtime=None)
            elif obj.get("linkMode") == "linked_file":
                out["path"] = ""  # where the file lies on the desktop that linked it
        elif item_type == "note":
            out["note"] = ""
        elif item_type != "annotation":
            out.update({"creators": [], **{name: "" for name in _FIELDS.get(item_type, ())}})
        out.update(tags=[], relations={}, dateAdded=now, dateModified=now)
        out.update(obj)
        for name in ("md5", "mtime"):  # only an upload sets them, and only on a stored file
            if name in out:
                out[name] = None
                if obj.get("linkMode") not in _STORED_MODES:
                    del out[name]
        return FakeZotero._shaped(kind, out)

    @staticmethod
    def _merged(kind: str, existing: dict, obj: dict) -> dict:
        """``existing`` with ``obj``'s fields over it (PATCH semantics; a
        field sent as None goes)."""
        merged = {**existing, **obj}
        for name, value in obj.items():
            if value is None and name not in ("md5", "mtime"):
                merged.pop(name, None)
        for name in ("md5", "mtime"):  # only an upload sets them
            if name in existing:
                merged[name] = existing[name]
            else:
                merged.pop(name, None)
        return FakeZotero._shaped(kind, merged)

    @staticmethod
    def _shaped(kind: str, data: dict) -> dict:
        """Zotero's form of the trash flag and of a child's collections: an
        item in the trash holds ``deleted: 1``, a collection ``deleted:
        true``, anything else no flag; a child item has no collections (an
        attachment moved under an item loses its own), a top-level one has
        a list; a top-level collection's parent is false; a tag names its
        type only when it is not 0 (a manual tag)."""
        if isinstance(data.get("tags"), list):
            data["tags"] = [{"tag": t["tag"], **({"type": int(t["type"])} if t.get("type") else {})}
                            if isinstance(t, dict) and "tag" in t else t for t in data["tags"]]
        if data.get("deleted"):
            data["deleted"] = 1 if kind == "items" else True
        else:
            data.pop("deleted", None)
        if kind == "collections" and not data.get("parentCollection"):
            data["parentCollection"] = False  # a top-level collection's, as Zotero answers it
        elif kind == "items":
            if not data.get("parentItem"):
                data.pop("parentItem", None)
                if data.get("itemType") != "annotation":
                    data.setdefault("collections", [])
            else:
                data.pop("collections", None)
        return data

    def _problem(self, lib: _Library, kind: str, key: str, data: dict, existing: dict | None,
                 uid: int) -> tuple[int, str] | None:
        """Why Zotero would refuse to save ``data`` as ``key`` (``existing``
        what it held, None for a create) for user ``uid``, or None."""
        if kind == "collections":
            if not str(data.get("name") or "").strip():
                return 400, "Collection name cannot be empty"
            parent = data.get("parentCollection")
            if parent and parent not in lib.collections:
                return 409, f"Parent collection {parent} doesn't exist"
            return None
        item_type = data.get("itemType")
        if not isinstance(item_type, str) or not item_type:
            return 400, "'itemType' property not provided"
        if _FIELDS and item_type not in _FIELDS:
            return 400, f"'{item_type}' is not a valid itemType"  # unverified wording
        allowed = _TYPE_KEYS.get(item_type) or (_ITEM_KEYS | set(_FIELDS.get(item_type, ())))
        if _FIELDS:
            for name in data:
                if name not in allowed:
                    return 400, f"'{name}' is not a valid field for type '{item_type}'"  # unverified wording
        parent = data.get("parentItem")
        if parent and parent not in lib.items:
            return 400, f"Parent item {parent} doesn't exist"
        if item_type == "attachment" and data.get("linkMode") == "embedded_image" and (
                not parent or lib.items[parent].get("itemType") != "note"):
            return 400, "An embedded image must be the child of a note"  # unverified wording
        for collection in data.get("collections") or []:
            if collection not in lib.collections:
                return 409, f"Collection {collection} doesn't exist"
        if item_type == "annotation":
            return self._annotation_problem(lib, key, data, existing, uid)
        return None

    def _annotation_problem(self, lib: _Library, key: str, data: dict, existing: dict | None,
                            uid: int) -> tuple[int, str] | None:
        parent_key = data.get("parentItem")
        if not parent_key:
            return 400, "Annotation must have a parent item"  # unverified wording
        parent = lib.items[parent_key]
        if parent.get("itemType") != "attachment" or parent.get("linkMode") not in _FILE_MODES:
            return 400, f"Parent item {lib.number}/{parent_key} of annotation must be a file attachment"
        kind = data.get("annotationType")
        if existing is not None and existing.get("annotationType") != kind:
            return 400, f"Cannot change existing annotationType for item {lib.number}/{key}"
        if kind not in ANNOTATION_TYPES:
            return 400, f"Invalid annotationType '{kind}'"  # unverified wording
        content = parent.get("contentType")
        if content not in (("application/pdf",) if kind in _PDF_ONLY else _READER_TYPES):
            return 400, f"'{kind}' annotations cannot be added to '{content}' attachments"  # unverified wording
        if "annotationText" in data and kind not in _TEXT_KINDS:
            return 400, "'annotationText' can only be set for highlight and underline annotations"
        color = data.get("annotationColor")
        if color is not None and not (isinstance(color, str) and _COLOR_RE.match(color)):
            return 400, "annotationColor must be a hex color (e.g., '#FF0000')"
        sort = data.get("annotationSortIndex")
        pdf_sort = isinstance(sort, str) and _PDF_SORT_RE.match(sort)
        if content == "application/pdf" and sort is not None and not pdf_sort:
            return 400, f"Invalid sortIndex '{sort}'"
        if len(str(data.get("annotationPageLabel") or "")) > LABEL_MAX:
            return 400, f"Annotation page label is too long for attachment {parent_key}"
        position = data.get("annotationPosition")
        if position is not None and not isinstance(position, str):
            return 400, "'annotationPosition' must be a string"  # unverified wording
        if len((position or "").encode("utf-8")) > POSITION_MAX:
            return 400, f"Annotation position is too long for attachment {parent_key}"
        if existing is not None and lib.group:
            creator = lib.authors.get(key, {}).get("created", uid)
            group = self.groups[lib.number]
            admin = uid == group["owner"] or uid in group["admins"]
            only_trash = {k for k in set(data) | set(existing) if data.get(k) != existing.get(k)} <= {"deleted"}
            if creator != uid and not (admin and only_trash):
                return 403, "Annotations created by other users cannot be modified"  # unverified wording
        return None

    def _put(self, lib: _Library, kind: str, key: str, data: dict, version: int, uid: int, *,
             touched: bool = True) -> None:
        """Save ``data`` as ``key`` at ``version`` (an item's
        ``dateModified`` now unless ``touched`` is False), by ``uid``."""
        if kind == "items":
            text = data.get("annotationText")
            if isinstance(text, str) and len(text) > TEXT_MAX:
                data["annotationText"] = text[:TEXT_MAX]
            if touched and key in lib.items:
                data["dateModified"] = _now()
        getattr(lib, kind)[key] = {"key": key, "version": version, **{k: v for k, v in data.items()
                                                                       if k not in ("key", "version")}}
        who = lib.authors.setdefault(key, {"created": uid, "modified": uid})
        who["modified"] = uid
        lib.deleted[kind].pop(key, None)

    # --- writes --------------------------------------------------------------

    def _write(self, lib: _Library, kind: str, headers: dict, body: bytes, uid: int, notes: bool = True) -> Reply:
        """A multi-object POST: each object saved or refused on its own, the
        saved ones sharing one new library version. Without ``notes``
        access a note is refused (unverified)."""
        try:
            objects = json.loads(body)
        except ValueError:
            return _text(400, "Invalid JSON")
        if not isinstance(objects, list):
            return _text(400, "Uploaded data must be a JSON array")
        if len(objects) > WRITE_MAX:
            return _text(413, f"Only {WRITE_MAX} objects can be saved in a single request")
        expected = headers.get("if-unmodified-since-version")
        if expected is not None and lib.version > int(expected):
            return _text(412, f"Library has been modified since specified version "
                              f"(expected {expected}, found {lib.version})")
        store = getattr(lib, kind)
        for obj in objects:  # Zotero fails the whole request on an annotation without a type
            if (kind == "items" and isinstance(obj, dict) and obj.get("itemType") == "annotation"
                    and not obj.get("annotationType") and obj.get("key") not in store):
                return _text(500, "An error occurred")
        version = lib.version + 1
        report = {"successful": {}, "success": {}, "unchanged": {}, "failed": {}}
        for i, obj in enumerate(objects):
            if not notes and isinstance(obj, dict) and "note" in (
                    obj.get("itemType"), store.get(str(obj.get("key") or ""), {}).get("itemType")):
                report["failed"][str(i)] = {"key": str(obj.get("key") or ""), "code": 403,
                                            "message": "Notes access denied"}  # unverified wording
                continue
            outcome, value = self._save(lib, kind, obj, version, uid, expected is not None)
            if outcome == "saved":
                lib.version = version
                report["successful"][str(i)] = self._object(lib, store[value], kind)
                report["success"][str(i)] = value
            else:
                report[outcome][str(i)] = value
        return _json(report, headers={"Last-Modified-Version": str(lib.version)})

    def _save(self, lib: _Library, kind: str, obj, version: int, uid: int, versioned: bool):
        """One object of a write: ("saved", key), ("unchanged", key) or
        ("failed", {"key", "code", "message"}). ``versioned``: the request
        named the library's version, which stands for a missing one."""
        noun = "Item" if kind == "items" else "Collection"
        if not isinstance(obj, dict):
            return "failed", {"key": "", "code": 400, "message": "Invalid value for index"}
        obj = copy.deepcopy(obj)
        key, given = obj.pop("key", None), obj.pop("version", None)
        if key is not None and not (isinstance(key, str) and _KEY_RE.match(key)):
            return "failed", {"key": str(key), "code": 400, "message": f"'{key}' is not a valid {noun.lower()} key"}
        store = getattr(lib, kind)
        existing = store.get(key) if key else None
        if existing is not None:
            if given is None and not versioned:
                return "failed", {"key": key, "code": 428, "message": VERSIONS_REQUIRED}
            if given is not None and given != existing["version"]:
                return "failed", {"key": key, "code": 412, "message": f"{noun} has been modified since specified "
                                                                      f"version (expected {given}, found "
                                                                      f"{existing['version']})"}
            merged = self._merged(kind, existing, obj)
            if merged == existing:
                return "unchanged", key
            problem = self._problem(lib, kind, key, merged, existing, uid)
            if problem:
                return "failed", {"key": key, "code": problem[0], "message": problem[1]}
            self._put(lib, kind, key, merged, version, uid, touched="dateModified" not in obj)
            return "saved", key
        if given not in (None, 0):
            return "failed", {"key": key or "", "code": 404,
                              "message": f"{noun} doesn't exist (expected version {given}; use 0 instead)"}
        key = key or self._new_key()
        data = self._made(kind, obj)
        problem = self._problem(lib, kind, key, data, None, uid)
        if problem:
            return "failed", {"key": key, "code": problem[0], "message": problem[1]}
        self._put(lib, kind, key, data, version, uid)
        return "saved", key

    def _one(self, lib: _Library, kind: str, key: str, method: str, headers: dict, body: bytes, uid: int) -> Reply:
        """PATCH (the fields sent) or PUT (the whole object) of one object,
        named by ``If-Unmodified-Since-Version`` or its body's ``version``."""
        noun = "Item" if kind == "items" else "Collection"
        existing = getattr(lib, kind)[key]
        try:
            data = json.loads(body)
        except ValueError:
            return _text(400, "Invalid JSON")
        if not isinstance(data, dict):
            return _text(400, "Uploaded data must be a JSON object")
        expected = headers.get("if-unmodified-since-version", data.get("version"))
        if expected is None:
            return _text(428, VERSIONS_REQUIRED)
        if int(expected) != existing["version"]:
            return _text(412, f"{noun} has been modified since specified version "
                              f"(expected {expected}, found {existing['version']})")
        sent = {k: v for k, v in data.items() if k not in ("key", "version")}
        if method == "PATCH":
            new = self._merged(kind, existing, sent)
        else:
            kept = {name: existing[name] for name in ("dateAdded", "md5", "mtime") if name in existing}
            new = {**self._made(kind, sent), **kept}
        if new == existing:
            return Reply(204, b"", {"Last-Modified-Version": str(lib.version)})
        problem = self._problem(lib, kind, key, new, existing, uid)
        if problem:
            return _text(*problem)
        self._put(lib, kind, key, new, lib.bump(), uid, touched="dateModified" not in sent)
        return Reply(204, b"", {"Last-Modified-Version": str(lib.version)})

    def _file_post(self, lib: _Library, key: str, headers: dict, body: bytes) -> Reply:
        """The upload's authorization (step 2) and registration (step 4)."""
        data = lib.items[key]
        if data.get("itemType") != "attachment" or data.get("linkMode") not in _STORED_MODES:
            return _text(400, "Item is not an imported attachment")
        form = dict(urllib.parse.parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))
        if headers.get("if-none-match") == "*":
            if key in lib.files and "upload" not in form:
                return _text(412, "If-None-Match: * set but file exists")
        elif "if-match" in headers:
            if key not in lib.files and "upload" not in form:  # a WebDAV user's file Zotero's storage never got
                return _text(412, "If-Match set but file does not exist")
            if data.get("md5") != headers["if-match"]:
                return _text(412, "ETag does not match current version of file")
        else:
            return _text(428, "If-Match/If-None-Match header not provided")
        if "upload" in form:
            pending = self.uploads.get(form["upload"])
            if not pending or (pending["library"], pending["key"]) != (lib.id, key) or pending["content"] is None:
                return _text(400, "Upload key not found")
            del self.uploads[form["upload"]]
            self._attach(lib, key, pending["content"], filename=pending["filename"], mtime=pending["mtime"])
            return Reply(204, b"", {"Last-Modified-Version": str(lib.version)})
        try:
            md5, filename = form["md5"], form["filename"]
            size, mtime = int(form["filesize"]), int(form["mtime"])
        except (KeyError, ValueError):
            return _text(400, "md5, filename, filesize and mtime are required")
        same = next((content for content in lib.files.values() if _md5(content) == md5), None)
        if same is not None:
            self._attach(lib, key, same, filename=filename, mtime=mtime)
            return _json({"exists": 1})
        if self.quota is not None:
            used = sum(len(content) for k, content in lib.files.items() if k != key) + size
            if used > self.quota:
                return _text(413, f"File would exceed quota ({used} > {self.quota})")
        upload_key = secrets.token_hex(16)
        self.uploads[upload_key] = {"library": lib.id, "key": key, "md5": md5, "filename": filename,
                                    "filesize": size, "mtime": mtime, "content": None}
        return _json({"url": f"{self.storage_base}/__upload/{upload_key}", "contentType": _UPLOAD_TYPE,
                      "prefix": _UPLOAD_PREFIX, "suffix": _UPLOAD_SUFFIX, "uploadKey": upload_key})

    def _storage(self, method: str, path: str, headers: dict, body: bytes) -> Reply:
        if any(name.startswith("zotero-") for name in headers):
            return _text(400, "Zotero's headers were sent to the file storage")
        if method == "GET" and path.startswith("/__storage/"):
            match = re.match(r"^/__storage/((?:users|groups)/[0-9]+)/([^/]+)$", path)
            lib = self.libraries.get(match.group(1)) if match else None
            if not lib or match.group(2) not in lib.files:
                return _text(404, "NoSuchKey")
            if match.group(2) in lib.zipped:
                return Reply(200, lib.zipped[match.group(2)], {"Content-Type": "application/zip"})
            return Reply(200, lib.files[match.group(2)],
                         {"Content-Type": lib.items[match.group(2)].get("contentType") or "application/octet-stream"})
        if method == "POST" and path.startswith("/__upload/"):
            pending = self.uploads.get(path.rsplit("/", 1)[-1])
            if not pending:
                return _text(404, "NoSuchUpload")
            if headers.get("content-type") != _UPLOAD_TYPE:
                return _text(400, "Wrong Content-Type")
            prefix, suffix = _UPLOAD_PREFIX.encode(), _UPLOAD_SUFFIX.encode()
            if not body.startswith(prefix) or not body.endswith(suffix):
                return _text(400, "Malformed upload body")
            content = body[len(prefix):len(body) - len(suffix)]
            if len(content) != pending["filesize"] or _md5(content) != pending["md5"]:
                return _text(400, "The uploaded file does not match its md5 or size")
            pending["content"] = content
            return Reply(201)
        return _text(405, "Method not allowed")

    # --- OAuth ---------------------------------------------------------------

    def _oauth(self, method: str, url: str, path: str, params: dict, headers: dict) -> Reply:
        """zotero.org's OAuth 1.0a steps. The approval page approves at once:
        it redirects to the callback with ``GOOD_VERIFIER``."""
        if path == "/oauth/authorize" and method == "GET":
            callback = self._callbacks.get(params.get("oauth_token", ""))
            if not callback:
                return _text(400, "Invalid request token")
            joined = "&" if "?" in callback else "?"
            query = urllib.parse.urlencode({"oauth_token": params["oauth_token"], "oauth_verifier": GOOD_VERIFIER})
            return Reply(302, b"", {"Location": f"{callback}{joined}{query}"})
        if method != "POST" or path not in ("/oauth/request", "/oauth/access"):
            return _text(404, "Not found")
        fields = {urllib.parse.unquote(k): urllib.parse.unquote(v)
                  for k, v in re.findall(r'([\w.~%-]+)="([^"]*)"', headers.get("authorization", ""))}
        self.oauth.append({"path": path, "url": url, "params": fields})
        if (not headers.get("authorization", "").startswith("OAuth ") or not fields.get("oauth_consumer_key")
                or not fields.get("oauth_signature") or fields.get("oauth_signature_method") != "HMAC-SHA1"):
            return _text(401, "Invalid signature")
        if path == "/oauth/request":
            if not fields.get("oauth_callback"):
                return _text(400, "oauth_callback is required")
            self._callbacks[REQUEST_TOKEN] = fields["oauth_callback"]
            answer = {"oauth_token": REQUEST_TOKEN, "oauth_token_secret": REQUEST_SECRET,
                      "oauth_callback_confirmed": "true"}
            return Reply(200, urllib.parse.urlencode(answer).encode(),
                         {"Content-Type": "application/x-www-form-urlencoded"})
        if fields.get("oauth_token") != REQUEST_TOKEN or fields.get("oauth_verifier") != GOOD_VERIFIER:
            return _text(401, "Invalid verifier")
        self._minted += 1
        key = f"zkey-oauth-{self._minted}"
        self.keys[key] = copy.deepcopy(FULL_ACCESS)
        self.key_users[key] = self.user_id
        return Reply(200, urllib.parse.urlencode({"oauth_token": key, "oauth_token_secret": key,
                                                  "userID": self.user_id, "username": self.username}).encode(),
                     {"Content-Type": "application/x-www-form-urlencoded"})


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Serve a fake Zotero holding the standard test library.")
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (0 = any free one)")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # also under python -I
    from zotero_fixtures import standard_library

    fake = FakeZotero()
    httpd, base = fake.serve(args.port)
    standard_library(fake)
    print(f"ZOTERO {base}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
