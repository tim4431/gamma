"""Zotero's Web API (v3): the transport and the client a Zotero link syncs
through (docs/dev/zotero_sync.md).

``ZoteroClient`` speaks to ``config.zotero_api_base()`` with one person's
API key (gamma/zotero_account.py keeps it). Every call goes through
``_request``, plain urllib that never follows a redirect: a file download
is a 302 to a pre-signed storage address, which the client fetches itself
without Zotero's headers (so the key never travels there), after
``net_guard.validate_public_url`` unless it is on the API's own origin (a
test or e2e stand-in). The API base is trusted configuration.

Every client of one key shares a throttle: at most ``KEY_CONCURRENCY``
calls in flight, and the pause Zotero asks for with ``Backoff`` or
``Retry-After`` (each capped at ``MAX_WAIT``). A 429/503 is retried, and so
is a GET that got no answer or a 500/502/504 (a download only until a byte
of it arrived). ``heartbeat`` is called through every wait and while a
file streams, so a stopped job ends at once. Errors are ``ZoteroError``
subclasses whose text never holds the key. Tests replace ``_request``
and ``_sleep`` (tests/zotero_fake.py).
"""

import hashlib
import io
import json
import platform
import re
import secrets
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.client import HTTPException
from typing import Any

from . import config, net_guard
from .logbuf import log

API_VERSION = "3"
LIBRARY_RE = re.compile(r"^(users|groups)/[0-9]{1,12}$")
KEY_ALPHABET = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
KEY_RE = re.compile(f"^[{KEY_ALPHABET}]{{8}}$")
_ITEM_URI_RE = re.compile(rf"^http://zotero\.org/((?:users|groups)/[0-9]{{1,12}})/items/([{KEY_ALPHABET}]{{8}})$")

PAGE_LIMIT = 100       # objects per page of a listing, Zotero's maximum
WRITE_LIMIT = 50       # objects per write, Zotero's maximum
KEYS_PER_READ = 50     # keys one itemKey / collectionKey filter may name
TIMEOUT = 30           # seconds an API call may stall
FILE_TIMEOUT = 120     # seconds a storage transfer may stall (per read, not in all)
MAX_RETRIES = 4        # retries of a 429/503 (and of a GET with no answer, a 500, 502 or 504)
MAX_WAIT = 60.0        # the longest single pause, whatever Zotero asks
KEY_CONCURRENCY = 4    # calls in flight per API key, all its clients together
HEARTBEAT = 1.0        # seconds between heartbeats while a client waits
TICK_BYTES = 4 * 1024 * 1024  # bytes of a file transfer between two ticks
VERSION_PATHS = ("collections", "items", "items/top")  # what ``versions`` lists
KINDS = ("items", "collections")  # what ``objects`` reads and ``write`` writes
_TRANSIENT = (500, 502, 504)  # what a GET is asked again on, besides 429/503 and no answer
_ZIP_SIGNATURE = b"PK\x03\x04"
_CHUNK = 1024 * 1024
_REDIRECTS = (301, 302, 303, 307, 308)
_sleep = time.sleep    # the throttle's wait; tests make it instant

COMPRESSED = "Zotero stores this attachment as a zipped web page snapshot, which Gamma does not import"
UNREADABLE = "Zotero sent an unreadable answer"
NO_FILE_ACCESS = "Your Zotero key may not read this library's files"


class ZoteroError(ValueError):
    """Zotero refused or could not be reached; the text is a sentence for
    the person. ``status`` is Zotero's HTTP status, None when no answer
    came."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ZoteroUnreachable(ZoteroError):
    """No answer came (the connection refused, reset or timed out, the name
    not found); ``status`` is None."""


class ZoteroAuthError(ZoteroError):
    """401/403: the key is unknown, revoked, or lacks the access asked for."""


class ZoteroConflict(ZoteroError):
    """412: the object or library changed in Zotero since the version the
    call named, or it exists where the call said it must not."""


class ZoteroNotFound(ZoteroError):
    """404: no such library, object or stored file."""


class ZoteroQuota(ZoteroError):
    """413 on a file upload: the library owner's Zotero storage is full."""


class ZoteroCompressed(ZoteroError):
    """The stored file is the ZIP Zotero makes of an attachment of several
    files (a web page snapshot with its pictures), not the attachment's
    own file."""


@dataclass
class Response:
    """One HTTP answer: ``headers`` with lower-case names; ``body`` is b""
    when it was streamed into a sink."""
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError:
            raise ZoteroError(f"{UNREADABLE} ({self.status})", self.status) from None


@dataclass
class WriteResult:
    """Zotero's report on a multi-object write, by the index of each object
    in the request: ``successful`` the saved object as Zotero now holds it
    (``key``, ``version``, ``data``, ``meta``), ``unchanged`` the key of one
    that already held what was sent, ``failed`` ``{"key", "code",
    "message"}`` (412 when it changed meanwhile or, for ``version: 0``,
    exists). ``version`` is the library's version after the write."""
    successful: dict[int, dict] = field(default_factory=dict)
    unchanged: dict[int, str] = field(default_factory=dict)
    failed: dict[int, dict] = field(default_factory=dict)
    version: int = 0


def object_key(*seed: str) -> str:
    """A Zotero object key derived from ``seed``: the same seed always gives
    the same key, so a create retried after an interruption names the
    object it made the first time (``version: 0`` then fails with 412)."""
    n = int.from_bytes(hashlib.sha256("\0".join(seed).encode("utf-8")).digest(), "big")
    out = []
    for _ in range(8):
        n, i = divmod(n, len(KEY_ALPHABET))
        out.append(KEY_ALPHABET[i])
    return "".join(out)


def random_key() -> str:
    """A fresh random object key, for an object whose derived key Zotero
    deleted for good (a key in its delete log is not reused)."""
    return "".join(secrets.choice(KEY_ALPHABET) for _ in range(8))


def item_uri(library: str, key: str) -> str:
    """The item's Zotero URI, ``http://zotero.org/users/<id>/items/<KEY>``:
    what a page's ``zotero_key`` holds, and what an RDF export names it."""
    return f"http://zotero.org/{library}/items/{key}"


def parse_item_uri(uri) -> tuple[str, str] | None:
    """``(library, key)`` of an ``item_uri``; None for anything else (an
    RDF import's item URL or ``#item_N``)."""
    match = _ITEM_URI_RE.match(uri) if isinstance(uri, str) else None
    return (match.group(1), match.group(2)) if match else None


def my_library_access(access) -> dict[str, bool]:
    """What a key's ``access`` (``keys/current``) lets it do in its own
    user's library: ``read`` it, and only then ``write`` items, read and
    store ``files`` (storing also needs ``write``) and see ``notes`` (Zotero
    leaves every note out of what a key without notes access reads).
    ``libraries`` and zotero_account's status read a key the same way."""
    mine = access.get("user") if isinstance(access, dict) else None
    mine = mine if isinstance(mine, dict) else {}
    read = bool(mine.get("library"))
    return {"read": read, **{name: read and bool(mine.get(name)) for name in ("write", "files", "notes")}}


def user_agent() -> str:
    """Gamma's agent for Zotero and its storage: cloud_auth's form without
    this server's address, which Zotero has no use for."""
    from . import version  # local, as cloud_auth does
    system = {"Darwin": "macOS"}.get(platform.system(), platform.system()) or "unknown system"
    return f"Gamma/{version.label()} ({system})"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hand every 3xx back as an answer instead of following it with
    Zotero's headers."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _lower(headers) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()} if headers else {}


def _request(method: str, url: str, headers: dict, body, timeout: float, sink=None) -> Response:
    """One HTTP call, never following a redirect. ``body`` is bytes, a
    binary file object (streamed: give its Content-Length) or None; with
    ``sink`` a 2xx body is streamed into it and ``Response.body`` is b"".
    ZoteroUnreachable when no answer came. The single transport of this
    module and gamma/zotero_oauth.py: the tests replace it."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        try:
            resp = _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            with e:
                return Response(e.code, _lower(e.headers), e.read())
        with resp:
            if sink is None:
                return Response(resp.status, _lower(resp.headers), resp.read())
            shutil.copyfileobj(resp, sink, _CHUNK)
            return Response(resp.status, _lower(resp.headers), b"")
    except urllib.error.URLError as e:
        raise ZoteroUnreachable(f"Zotero could not be reached: {e.reason}") from None
    except (OSError, HTTPException) as e:
        raise ZoteroUnreachable(f"Zotero could not be reached: {str(e) or type(e).__name__}") from None


def _seconds(value) -> float | None:
    """A ``Backoff`` / ``Retry-After`` value in seconds (None when absent or
    not a number), at most ``MAX_WAIT``."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(seconds, 0.0), MAX_WAIT)


def _version(headers: dict) -> int:
    try:
        return int(headers.get("last-modified-version") or 0)
    except ValueError:
        return 0


def _compressed(resp: Response) -> bool:
    return resp.headers.get("zotero-file-compressed", "").strip().lower() == "yes"


class _Meter:
    """Follows a streamed transfer: ``tick(done)`` each time another
    ``TICK_BYTES`` went through, ``heartbeat()`` at least every
    ``HEARTBEAT`` seconds while bytes flow. Either may raise to stop it."""

    def __init__(self, tick=None, heartbeat=None):
        self.tick, self.heartbeat = tick, heartbeat
        self.done, self._next, self._beat_at = 0, TICK_BYTES, 0.0

    def add(self, n: int) -> None:
        self.done += n
        if self.tick and self.done >= self._next:
            self._next = (self.done // TICK_BYTES + 1) * TICK_BYTES
            self.tick(self.done)
        if self.heartbeat:
            now = time.monotonic()
            if now >= self._beat_at:
                self._beat_at = now + HEARTBEAT / 2
                self.heartbeat()


class _Hashing:
    """A download's sink: passes the bytes on, counts and md5-hashes them,
    keeps the first few (``head``, to spot a ZIP) and reports them to
    ``meter``."""

    def __init__(self, sink, meter: _Meter):
        self.sink, self.meter, self.size = sink, meter, 0
        self.md5 = hashlib.md5(usedforsecurity=False)
        self.head = b""

    def write(self, data):
        self.size += len(data)
        self.md5.update(data)
        if len(self.head) < len(_ZIP_SIGNATURE):
            self.head += bytes(data[:len(_ZIP_SIGNATURE) - len(self.head)])
        written = self.sink.write(data)
        self.meter.add(len(data))
        return written


class _Joined:
    """``prefix`` + a file + ``suffix`` read as one body, so an upload never
    holds the file in memory; the file's bytes are reported to ``meter``."""

    def __init__(self, prefix: bytes, stream, suffix: bytes, meter: _Meter):
        self._parts = [io.BytesIO(prefix), stream, io.BytesIO(suffix)]
        self._stream, self._meter = stream, meter

    def read(self, size: int | None = -1) -> bytes:
        whole = size is None or size < 0
        out = b""
        while self._parts and (whole or len(out) < size):
            part = self._parts[0]
            chunk = part.read(-1 if whole else size - len(out))
            if chunk:
                out += chunk
                if part is self._stream:
                    self._meter.add(len(chunk))
            else:
                self._parts.pop(0)
        return out


class _Throttle:
    """What every client of one API key shares: the calls in flight, and
    the time (``time.monotonic()``) before which none starts."""

    def __init__(self):
        self.slots = threading.BoundedSemaphore(KEY_CONCURRENCY)
        self.lock = threading.Lock()
        self.resume_at = 0.0


# Each key's throttle (at each API base), by a hash of the two: kept for the
# process's life, so a Backoff outlives the client that heard it.
# tests/zotero_fake.py gives each test a fresh registry.
_throttles: dict[str, _Throttle] = {}
_throttles_lock = threading.Lock()


def _throttle(base: str, api_key: str) -> _Throttle:
    name = hashlib.sha256(f"{base}\0{api_key}".encode("utf-8")).hexdigest()
    with _throttles_lock:
        found = _throttles.get(name)
        if found is None:
            found = _throttles[name] = _Throttle()
        return found


def _library(library: str) -> str:
    if not isinstance(library, str) or not LIBRARY_RE.match(library):
        raise ValueError(f"not a Zotero library: {library!r}")
    return library


def _key(key: str) -> str:
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise ValueError(f"not a Zotero object key: {key!r}")
    return key


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"not a kind of Zotero object: {kind!r}")
    return kind


def _member_may(group: dict, user_id: int, setting: str) -> bool:
    """Whether the group's ``setting`` (``libraryEditing`` /
    ``fileEditing``: "members", "admins" or "none") lets ``user_id``, one
    of its members, edit; True when the group does not say."""
    who = group.get(setting)
    if who in (None, "", "members"):
        return True
    if who == "admins":
        return group.get("owner") == user_id or user_id in (group.get("admins") or [])
    return False


class ZoteroClient:
    """One person's Zotero: their API key and its throttle. ``heartbeat()``
    (a job's ``progress``) is called at least every ``HEARTBEAT`` seconds
    while the client waits and while a file streams; it may raise
    (``jobs.Cancelled``) to stop at once."""

    def __init__(self, api_key: str, *, base: str | None = None, heartbeat=None):
        self._api_key = api_key
        self.base = (base or config.zotero_api_base()).rstrip("/")
        self.heartbeat = heartbeat
        self._agent = user_agent()
        self._throttle = _throttle(self.base, api_key)

    # --- transport ---------------------------------------------------------

    def _url(self, path: str, params: dict | None = None) -> str:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        return f"{self.base}{path}" + (f"?{query}" if query else "")

    def _pause(self, seconds: float) -> None:
        """Hold every call of the key for ``seconds``."""
        throttle = self._throttle
        with throttle.lock:
            throttle.resume_at = max(throttle.resume_at, time.monotonic() + seconds)

    def _wait(self) -> None:
        throttle = self._throttle
        with throttle.lock:
            delay = throttle.resume_at - time.monotonic()
        if delay > 0:
            self._doze(delay)

    def _doze(self, seconds: float) -> None:
        """Sleep ``seconds``: in one go, or beating every ``HEARTBEAT``."""
        if not self.heartbeat:
            _sleep(seconds)
            return
        while seconds > 0:
            self.heartbeat()
            step = min(seconds, HEARTBEAT)
            _sleep(step)
            seconds -= step

    @contextmanager
    def _slot(self):
        """One of the key's slots, for one call."""
        slots = self._throttle.slots
        if not self.heartbeat:
            slots.acquire()
        else:
            while not slots.acquire(timeout=HEARTBEAT):
                self.heartbeat()
        try:
            yield
        finally:
            slots.release()

    def _result(self, future):
        """A pool future's result, beating while it is not there yet."""
        if not self.heartbeat:
            return future.result()
        while True:
            try:
                return future.result(timeout=HEARTBEAT)
            except FutureTimeout:
                if future.done():
                    raise
                self.heartbeat()

    @staticmethod
    def _again(method: str, attempt: int, received: int, retries: int | None = None) -> bool:
        """Whether a call that got no answer, or a 500, 502 or 504, is made
        again: an idempotent one, within ``retries`` (``MAX_RETRIES``), none
        of whose body reached its sink."""
        return method in ("GET", "HEAD") and attempt < (MAX_RETRIES if retries is None else retries) and not received

    def request(self, method: str, path: str, *, params: dict | None = None, json_body=None,
                form: dict | None = None, headers: dict | None = None, expect=(200,), sink=None,
                retries: int | None = None) -> Response:
        """One API call to ``path`` (from the base, e.g. ``/users/1/items``),
        throttled and retried as Zotero asks (at most ``retries`` times,
        ``MAX_RETRIES`` unless said);
        the Response when its status is in ``expect`` (``(200, 304)`` with
        ``If-Modified-Since-Version``), else the matching ZoteroError.
        ``json_body`` / ``form`` become the body; ``sink`` takes a 2xx body
        (``_request``)."""
        url = self._url(path, params)
        sent = {"Zotero-API-Version": API_VERSION, "Zotero-API-Key": self._api_key, "User-Agent": self._agent}
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            sent["Content-Type"] = "application/json"
        elif form is not None:
            body = urllib.parse.urlencode(form).encode("ascii")
            sent["Content-Type"] = "application/x-www-form-urlencoded"
        sent.update(headers or {})
        retries = MAX_RETRIES if retries is None else retries
        attempt = 0
        while True:
            self._wait()
            try:
                with self._slot():
                    resp = _request(method, url, sent, body, TIMEOUT, sink)
            except ZoteroError as e:  # no answer came
                if not self._again(method, attempt, getattr(sink, "size", 0), retries):
                    raise
                why = str(e)
            else:
                backoff = _seconds(resp.headers.get("backoff"))
                if backoff:
                    self._pause(backoff)
                if resp.status in (429, 503) and attempt < retries:
                    wait = _seconds(resp.headers.get("retry-after"))
                    if wait is None:
                        wait = min(2.0 ** attempt, MAX_WAIT)
                    log.info(f"[zotero] answered {resp.status}: waiting {wait:g} s before trying again")
                    self._pause(wait)  # every call of the key waits
                    attempt += 1
                    continue
                received = getattr(sink, "size", 0)
                if resp.status not in _TRANSIENT or not self._again(method, attempt, received, retries):
                    break
                why = f"Zotero answered {resp.status}"
            wait = min(2.0 ** attempt, MAX_WAIT)
            log.info(f"[zotero] {why}: trying again in {wait:g} s")
            self._doze(wait)  # only this call waits
            attempt += 1
        if resp.status in expect:
            return resp
        raise self._error(resp)

    def _said(self, resp: Response) -> str:
        """Zotero's own words from an error body, when short and plain."""
        text = resp.body.decode("utf-8", "replace").strip()
        if not text or len(text) > 300 or text.startswith("<"):
            return ""
        return text.replace(self._api_key, "…") if self._api_key else text

    def _error(self, resp: Response) -> ZoteroError:
        status, said = resp.status, self._said(resp)
        if status in (401, 403):
            return ZoteroAuthError("Zotero refused the key: reconnect your account in Settings"
                                   + (f" (Zotero said: {said})" if said else ""), status)
        if status == 404:
            return ZoteroNotFound("Zotero has no such library, item or file", status)
        if status == 412:
            return ZoteroConflict("The object changed in Zotero meanwhile" + (f" ({said})" if said else ""), status)
        if status == 413:
            return ZoteroQuota("Your Zotero storage is full" + (f" ({said})" if said else ""), status)
        if status in (429, 503):
            return ZoteroError("Zotero is busy: try again in a few minutes", status)
        return ZoteroError(f"Zotero answered {status}" + (f": {said}" if said else ""), status)

    def get_json(self, path: str, params: dict | None = None, *, headers: dict | None = None,
                 expect=(200,)) -> tuple[Any, dict]:
        """A GET's decoded JSON and its headers; None for a 304 (with
        ``expect=(200, 304)`` and ``If-Modified-Since-Version``)."""
        resp = self.request("GET", path, params=params, headers=headers, expect=expect)
        return (None if resp.status == 304 else resp.json()), resp.headers

    def paged(self, path: str, params: dict | None = None) -> tuple[list, int]:
        """Every object of a multi-object read, page after page, and the
        first page's library version. Each page is a read of its own: in
        Zotero's default order (the last modified first) an object edited
        meanwhile can be skipped or read twice, so a sync reads ``versions``
        then ``objects``, or sorts by ``dateAdded`` as ``children`` does."""
        query = {k: v for k, v in (params or {}).items() if k not in ("start", "limit")}
        out, version = [], None
        while True:
            found, headers = self.get_json(path, {**query, "start": len(out), "limit": PAGE_LIMIT})
            if not isinstance(found, list):
                raise ZoteroError(UNREADABLE)
            if version is None:
                version = _version(headers)
            out.extend(found)
            total = headers.get("total-results", "")
            last = len(out) >= int(total) if total.isdigit() else len(found) < PAGE_LIMIT
            if not found or last:
                return out, version

    def pages(self, path: str, params: dict | None = None, *, most: int) -> tuple[list, int, int]:
        """At most the first ``most`` pages (``PAGE_LIMIT`` objects each) of
        a multi-object read, the pages after the first read several at a
        time: ``(objects, Total-Results, the first page's library
        version)``. Fewer objects than the total: the read stopped at
        ``most`` pages. In Zotero's default order an object edited meanwhile
        can be skipped or read twice: for counts, not for a sync."""
        query = {k: v for k, v in (params or {}).items() if k not in ("start", "limit")}

        def read(start):
            found, headers = self.get_json(path, {**query, "start": start, "limit": PAGE_LIMIT})
            if not isinstance(found, list):
                raise ZoteroError(UNREADABLE)
            return found, headers

        first, headers = read(0)
        total = headers.get("total-results", "")
        total = int(total) if total.isdigit() else len(first)
        starts = list(range(PAGE_LIMIT, min(total, max(1, int(most)) * PAGE_LIMIT), PAGE_LIMIT)) if first else []
        rest = self._gather(read, starts)
        return [*first, *(obj for found, _ in rest for obj in found)], total, _version(headers)

    def _gather(self, read, args) -> list:
        """``[read(arg) for arg in args]``, several reads in flight
        (``KEY_CONCURRENCY``; the key's throttle holds every client to it),
        beating while they run; a stop does not wait for the reads in flight."""
        if len(args) <= 1:
            return [read(arg) for arg in args]
        pool = ThreadPoolExecutor(max_workers=KEY_CONCURRENCY, thread_name_prefix="zotero")
        finished = False
        try:
            futures = [pool.submit(read, arg) for arg in args]
            out = [self._result(future) for future in futures]
            finished = True
            return out
        finally:
            pool.shutdown(wait=finished, cancel_futures=True)

    # --- the key and its libraries -------------------------------------------

    def key_info(self) -> dict:
        """What Zotero says about the key (``userID``, ``username``,
        ``access``), without the key itself."""
        info, _ = self.get_json("/keys/current")
        if not isinstance(info, dict) or not isinstance(info.get("userID"), int):
            raise ZoteroError(f"{UNREADABLE} about the key")
        return {k: v for k, v in info.items() if k != "key"}

    def groups(self, user_id: int) -> list[dict]:
        """The group objects of the groups ``user_id`` belongs to."""
        return self.paged(f"/users/{int(user_id)}/groups")[0]

    def libraries(self) -> list[dict]:
        """The libraries the key reaches: My Library first, then each group,
        as ``{"id", "name", "kind": "user"|"group", "user_id", "read",
        "write", "files", "notes"}``. ``user_id`` is the key's own Zotero
        user (a group annotation another member made is theirs alone).
        ``write``: items may be created and edited (the key's access and,
        in a group, its library-editing setting). ``files``: stored files
        may be downloaded, and uploaded where ``write`` holds too (My
        Library: the key's file access; a group: its file-editing setting
        lets this member). ``notes``: notes are seen at all (My Library: the
        key's notes access; a group's come with reading it)."""
        info = self.key_info()
        user_id = info["userID"]
        access = info.get("access") or {}
        out = [{"id": f"users/{user_id}", "name": "My Library", "kind": "user", "user_id": user_id,
                **my_library_access(access)}]
        granted = access.get("groups") or {}
        if not granted:
            return out
        for group in self.groups(user_id):
            data = group.get("data") or {}
            gid = group.get("id", data.get("id"))
            grant = granted.get(str(gid)) or granted.get("all") or {}
            read = bool(grant.get("library"))
            write = read and bool(grant.get("write")) and _member_may(data, user_id, "libraryEditing")
            out.append({"id": f"groups/{gid}", "name": str(data.get("name") or f"Group {gid}"), "kind": "group",
                        "user_id": user_id, "read": read, "write": write,
                        "files": read and _member_may(data, user_id, "fileEditing"), "notes": read})
        return out

    # --- reads for a sync ----------------------------------------------------

    def library_version(self, library: str, since: int = 0) -> int | None:
        """The library's version now; None when it is still ``since`` (Zotero
        answers 304: nothing changed there since). One small call."""
        headers = {"If-Modified-Since-Version": str(int(since))} if since else None
        resp = self.request("GET", f"/{_library(library)}/items", params={"limit": 1, "format": "versions"},
                            headers=headers, expect=(200, 304))
        return None if resp.status == 304 else _version(resp.headers)

    def versions(self, library: str, path: str, since: int, params: dict | None = None) -> tuple[dict[str, int], int]:
        """``{key: version}`` of the objects ``path`` ("collections", "items"
        or "items/top") lists that changed after library version ``since``
        (0: all of them), the trashed ones included, and the library version
        the answer is of. ``params`` narrow it (``itemType``; a negation
        also leaves out annotations, so name the types instead). One call:
        Zotero does not page this answer."""
        if path not in VERSION_PATHS:
            raise ValueError(f"not a Zotero listing: {path!r}")
        query = {**(params or {}), "since": int(since), "format": "versions"}
        if path != "collections":
            query["includeTrashed"] = 1  # Zotero lists a trashed collection whatever the query
        found, headers = self.get_json(f"/{_library(library)}/{path}", query)
        return self._versions_of(found), _version(headers)

    @staticmethod
    def _versions_of(found) -> dict[str, int]:
        if not isinstance(found, dict):
            raise ZoteroError(UNREADABLE)
        return {str(k): int(v) for k, v in found.items() if isinstance(v, int)}

    def deleted(self, library: str, since: int) -> tuple[dict[str, list[str]], int]:
        """The keys Zotero removed for good after library version ``since``,
        by kind (``collections``, ``items``, ``searches``, ``tags``,
        ``settings``), and the library version. What went to the trash is
        not here: it is an edit (``deleted``), listed by ``versions``."""
        found, headers = self.get_json(f"/{_library(library)}/deleted", {"since": int(since)})
        if not isinstance(found, dict):
            raise ZoteroError(UNREADABLE)
        return ({kind: [str(k) for k in keys] for kind, keys in found.items() if isinstance(keys, list)},
                _version(headers))

    def objects(self, library: str, kind: str, keys) -> list[dict]:
        """The whole objects (``key``, ``version``, ``data``, ``meta``) of
        ``keys``, "items" or "collections", the trashed ones included: read
        ``KEYS_PER_READ`` at a time, several reads in flight. A key Zotero
        does not have is absent."""
        _library(library)
        _kind(kind)
        keys = [_key(k) for k in dict.fromkeys(keys)]
        chunks = [keys[i:i + KEYS_PER_READ] for i in range(0, len(keys), KEYS_PER_READ)]
        path = f"/{library}/{kind}"

        def read(chunk):
            params = ({"itemKey": ",".join(chunk), "includeTrashed": 1} if kind == "items"
                      else {"collectionKey": ",".join(chunk)})
            found, _ = self.get_json(path, {**params, "limit": KEYS_PER_READ})
            if not isinstance(found, list):
                raise ZoteroError(UNREADABLE)
            return found

        return [obj for found in self._gather(read, chunks) for obj in found]

    def children(self, library: str, key: str) -> list[dict]:
        """Every child of an item (attachments and notes; an attachment's
        annotations; a note's embedded pictures), trashed ones included, as
        whole objects, page after page in the order they were added (an
        edit meanwhile does not move one across a page; an addition comes
        last)."""
        return self.paged(f"/{_library(library)}/items/{_key(key)}/children",
                          {"includeTrashed": 1, "sort": "dateAdded", "direction": "asc"})[0]

    # --- files ---------------------------------------------------------------

    def _storage_url(self, url: str) -> str:
        """``url`` (a storage address Zotero handed out) when it may be
        fetched: on the API's own origin, or public by ``net_guard``."""
        here, there = urllib.parse.urlsplit(self.base), urllib.parse.urlsplit(url)
        if (here.scheme, here.netloc) == (there.scheme, there.netloc):
            return url
        try:
            return net_guard.validate_public_url(url)
        except net_guard.BlockedUrlError as e:
            raise ZoteroError(f"Zotero's file storage address was refused ({e.reason})") from None

    def _fetch(self, url: str, sink: _Hashing) -> Response:
        """GET a storage address into ``sink``, without Zotero's headers;
        made again on no answer or a 500, 502, 503 or 504 while none of the
        file reached ``sink``."""
        attempt = 0
        while True:
            try:
                resp = _request("GET", url, {"User-Agent": self._agent}, None, FILE_TIMEOUT, sink)
            except ZoteroError as e:
                if not self._again("GET", attempt, sink.size):
                    raise
                why = str(e)
            else:
                if resp.status not in (*_TRANSIENT, 503) or not self._again("GET", attempt, sink.size):
                    return resp
                why = f"Zotero's file storage answered {resp.status}"
            wait = min(2.0 ** attempt, MAX_WAIT)
            log.info(f"[zotero] {why}: trying the file again in {wait:g} s")
            self._doze(wait)
            attempt += 1

    def download(self, library: str, key: str, sink, *, md5: str = "", tick=None) -> int:
        """Stream the attachment's stored file into ``sink``; its size.
        ZoteroNotFound when Zotero's storage holds none (a linked file,
        WebDAV storage, a file not uploaded yet); ZoteroAuthError (403) when
        the key may not read the library's files; ZoteroCompressed when
        Zotero keeps it zipped; ZoteroError when it does not match ``md5``.
        ``tick(bytes so far)`` follows every ``TICK_BYTES``. On an error
        ``sink`` may hold part of the file: the caller discards it."""
        path = f"/{_library(library)}/items/{_key(key)}/file"
        hashing = _Hashing(sink, _Meter(tick, self.heartbeat))
        try:
            resp = self.request("GET", path, expect=(200, *_REDIRECTS), sink=hashing)
        except ZoteroAuthError as e:
            if e.status != 403:
                raise
            raise ZoteroAuthError(NO_FILE_ACCESS, 403) from None
        if _compressed(resp):  # said before the storage is asked: nothing is fetched
            raise ZoteroCompressed(COMPRESSED, resp.status)
        if resp.status != 200:
            location = resp.headers.get("location", "")
            if not location:
                raise ZoteroError("Zotero sent no address for the file", resp.status)
            url = self._storage_url(urllib.parse.urljoin(self._url(path), location))
            resp = self._fetch(url, hashing)
            if resp.status == 404:
                raise ZoteroNotFound("Zotero's file storage holds no such file", 404)
            if resp.status != 200:
                raise ZoteroError(f"Zotero's file storage answered {resp.status}", resp.status)
        mismatch = bool(md5) and hashing.md5.hexdigest() != md5.lower()
        if _compressed(resp) or (mismatch and hashing.head == _ZIP_SIGNATURE):
            raise ZoteroCompressed(COMPRESSED, resp.status)
        if mismatch:
            raise ZoteroError("The file Zotero sent does not match its checksum: try again")
        return hashing.size

    def upload_file(self, library: str, key: str, path, *, filename: str, content_type: str, md5: str,
                    mtime_ms: int, size: int, replace_md5: str = "", tick=None) -> int | None:
        """Store the file at ``path`` (``size`` bytes, ``md5``) as the file of
        the imported attachment ``key``: Zotero's authorize, upload,
        register steps. Without ``replace_md5`` the attachment must have no
        file yet; with it, its file must still be that one (the file Gamma
        replaces). The attachment's version once its file is registered
        (the register's ``Last-Modified-Version``, which the caller records
        so its own upload does not read as a change in Zotero); None when
        Zotero had the bytes already and kept them without an upload.
        ZoteroConflict (412) when the attachment's file is not what the
        call assumed, which stays; ZoteroQuota (413) when the library
        owner's storage is full. ``tick(bytes sent)`` follows every
        ``TICK_BYTES``."""
        target = f"/{_library(library)}/items/{_key(key)}/file"
        condition = {"If-Match": replace_md5} if replace_md5 else {"If-None-Match": "*"}
        auth = self.request("POST", target, headers=condition,
                            form={"md5": md5, "filename": filename, "filesize": int(size), "mtime": int(mtime_ms)})
        grant = auth.json()
        if not isinstance(grant, dict):
            raise ZoteroError(f"{UNREADABLE} to an upload")
        if grant.get("exists"):
            return None
        try:
            url, upload_key = grant["url"], grant["uploadKey"]
            prefix, suffix = grant.get("prefix", "").encode("utf-8"), grant.get("suffix", "").encode("utf-8")
        except (KeyError, AttributeError):
            raise ZoteroError(f"{UNREADABLE} to an upload") from None
        with open(path, "rb") as stream:
            resp = _request("POST", self._storage_url(url),
                            {"Content-Type": grant.get("contentType") or content_type,
                             "Content-Length": str(len(prefix) + int(size) + len(suffix)),
                             "User-Agent": self._agent},
                            _Joined(prefix, stream, suffix, _Meter(tick, self.heartbeat)), FILE_TIMEOUT)
        if resp.status not in (200, 201, 204):
            raise ZoteroError(f"Zotero's file storage refused the upload ({resp.status})", resp.status)
        registered = self.request("POST", target, form={"upload": upload_key}, headers=condition, expect=(200, 204))
        return _version(registered.headers)

    # --- writes --------------------------------------------------------------

    def write(self, library: str, kind: str, objects: list[dict]) -> WriteResult:
        """Create or update up to ``WRITE_LIMIT`` "items" or "collections" in
        one call. Each object carries its own ``version`` (0 to create one
        under a key it names, which must not exist yet); a field left out
        stays as Zotero has it. One object failing (``WriteResult.failed``)
        does not fail the others."""
        _library(library)
        _kind(kind)
        if len(objects) > WRITE_LIMIT:
            raise ValueError(f"Zotero takes at most {WRITE_LIMIT} objects per write")
        if not objects:
            return WriteResult()
        resp = self.request("POST", f"/{library}/{kind}", json_body=objects)
        report = resp.json()
        if not isinstance(report, dict):
            raise ZoteroError(f"{UNREADABLE} to a write")

        def part(name):
            found = report.get(name)
            return {int(i): v for i, v in found.items() if str(i).isdigit()} if isinstance(found, dict) else {}

        failed = {i: {"key": str((v or {}).get("key") or ""), "code": int((v or {}).get("code") or 0),
                      "message": str((v or {}).get("message") or "")} for i, v in part("failed").items()}
        return WriteResult(part("successful"), part("unchanged"), failed, _version(resp.headers))

    def trash(self, library: str, kind: str, entries, *, restore: bool = False) -> WriteResult:
        """Move ``entries`` (``(key, version)`` pairs, up to ``WRITE_LIMIT``)
        to Zotero's trash, or out of it with ``restore``; never a DELETE, so
        Zotero's own users can still bring them back."""
        return self.write(library, kind, [{"key": _key(key), "version": int(version), "deleted": not restore}
                                          for key, version in entries])

    def put_item(self, library: str, key: str, version: int, data: dict) -> int:
        """Replace the item's editable fields with ``data`` (a retyped item,
        ``zotero_fields.retype``) while it is still at ``version``
        (ZoteroConflict otherwise); its new version."""
        resp = self.request("PUT", f"/{_library(library)}/items/{_key(key)}", json_body=data,
                            headers={"If-Unmodified-Since-Version": str(int(version))}, expect=(200, 204))
        return _version(resp.headers)

    def revoke(self) -> None:
        """Delete the key at Zotero (it stops working everywhere), asked
        once more at most when Zotero is busy, so a disconnect or an
        account deletion never waits long on it; a failure is only logged:
        the key is forgotten here either way."""
        try:
            self.request("DELETE", "/keys/current", expect=(200, 204), retries=1)
        except ZoteroError as e:
            log.warning(f"[zotero] could not revoke a key: {e}")
