#!/usr/bin/env python3
"""gamma-sync: keep a folder of your Gamma library as a folder on your disk.

One file, Python 3.10 or newer, standard library only: copy it anywhere
(with truststore installed, it verifies HTTPS against the system's trust
store, as a browser does).
It reads a Gamma server with an integration token (Settings → Integrations;
read access is enough unless PDFs go back) and writes the folder's papers
and notes to a directory of your choice, then keeps them up to date. The
words are a synced vault's: Gamma is the remote, the directory is local;
**pull** is Gamma to disk, **push** is disk to Gamma, **sync** is push,
then pull. ``pull`` writes Gamma's changes, and what you change on disk is
never overwritten (see the rules below). A directory linked with
``init --push`` pushes the PDFs you change in it, each its page's new
file, before each ``sync`` pulls; ``push`` does the same for one PDF
anywhere on disk (a manuscript compiled again from its TeX). The page
keeps its notes and highlights; notes files are never pushed. The desktop
app keeps its synced vaults with an engine of its own
(gamma/vault/sync.py), which pushes notes too; it takes this file's server
client, ``Push`` and ``settled``, which is why this file lives in the gamma
package. It imports nothing from it, and keeps its own state in the
directory: the app's server keeps a vault's in its data directory.

    python gamma_sync.py folders --server https://gamma.example.com --token gamma_…
    python gamma_sync.py init  ~/Papers/Quantum --server https://gamma.example.com \\
                               --folder "Papers / Quantum" --token gamma_… --save-token
    python gamma_sync.py pull  ~/Papers/Quantum             # Gamma's changes, once
    python gamma_sync.py sync  ~/Papers/Quantum --watch 60  # push, then pull, and after every change
    python gamma_sync.py status ~/Papers/Quantum
    python gamma_sync.py push  build/main.pdf --page <page id> --server https://gamma.example.com \\
                               --token gamma_… --watch 5    # each new build becomes the page's PDF

What lands on disk (docs/dev/synced_vaults.md "The client" in the Gamma repository):

    <dest>/
      .gamma-sync.json          the link: server, folder, and what was written
      Subfolder/                a folder below the linked one, empty ones too
        A paper.pdf             the paper, the file Gamma stores
        A paper.md              its notes and highlights (Obsidian dialect)
      A note.md                 a page without a PDF
      attachments/<hash>.png    the pictures the notes show

Rules: the client only ever writes, renames or removes files it wrote
itself. A file it finds in the way, or one you changed on disk, is left
alone and reported; ``--force`` replaces the changed ones. ``--full``
writes every file again. A PDF is pushed once it is whole (written a few
seconds ago, ending in ``%%EOF``) and only when its bytes are not Gamma's
already; one changed both on disk and in Gamma is kept and reported, and
``--force`` takes Gamma's. A token comes from ``--token``, the
``GAMMA_TOKEN`` environment variable, or the link when ``init --save-token``
kept it there; pushing takes a token that may write, and a server that
keeps its pages as this file reads them (``DOCUMENT_FORMAT``): update
gamma-sync with the server.
"""

import argparse
import copy
import hashlib
import http.client
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STATE = ".gamma-sync.json"
FORMAT = "gamma-sync-1"
PART = ".gamma-sync-part"   # what a file is called while it is being written
NOTES_BATCH = 100           # pages per notes request (the server takes 200)
DOC_CHARS = 24              # a stored PDF's id: the first hex characters of its SHA-256 (the server's DIGEST_CHARS)
SETTLE_S = 3.0              # a file written more recently than this is still being written
EOF_TAIL = 1024             # a whole PDF has "%%EOF" within its last bytes
KEEP_OLD_DAYS = 1           # how long the server keeps a file a push replaced, once nothing references it
CHUNK = 1 << 20             # bytes read from disk at a time
NOT_WHOLE = "not a whole PDF yet"   # ``settled``'s answer for a file at rest that does not end as a PDF ends
WRITING = "still being written"     # ``settled``'s answer for a file written less than SETTLE_S ago
NOT_THERE = "the file is not there"  # ``settled``'s, and a followed file's wait while it is gone
DOCUMENT_FORMAT = 52        # the oldest workspace data format whose pages this client reads (``doc``)
default_open = None         # tests: (method, path, headers, body) → (status, headers, body stream)


class SyncError(Exception):
    pass


class NotFound(SyncError):
    """A 404: the server has no such thing (a page that is gone, among others)."""


class Denied(SyncError):
    """A 401 or a 403: the token refused (expired, revoked, another server's),
    or not allowed what it asked."""


class Unreachable(SyncError):
    """The server could not be reached, its answer broke off, or it was no
    JSON (a sign-in or captive portal's page): nothing it said stands."""


class Changed(SyncError):
    """A file that changed while it was read to be sent (``_parts``): a push
    lets it wait for the next pass rather than keep it."""


ADDRESS_MAX = 300   # the longest address a redirect's words and fields keep


def _host(parts) -> str:
    """The host of a split URL, lowercase, an IDN in punycode."""
    host = (parts.hostname or "").rstrip(".")
    try:
        return host if host.isascii() else host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def _netloc(parts) -> str:
    """``host[:port]`` of a split URL (``_host``, IPv6 bracketed); no sign-in."""
    host = _host(parts)
    try:
        port = parts.port
    except ValueError:
        port = None
    return (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")


class Redirected(Unreachable):
    """The server answered a redirect that keeps the request's path (a
    proxy rewriting scheme, host or port), which this client never follows:
    a bearer token never goes to an address it was not given, and a write
    never turns into a read. ``location``: where it pointed (absolute,
    capped at 300). ``to``: the server's address there, built from the
    Location's scheme, host and port only (userinfo, query and fragment
    dropped), plus this server's own path when it has one; no trailing
    slash, at most 300 characters. ``upgrade``: the same host name
    (lowercase, IDN punycode), http:// to https:// (any port: Synology's
    5000 → 5001)."""

    def __init__(self, old: str, location: str):
        there, here = urllib.parse.urlsplit(location), urllib.parse.urlsplit(old)
        self.location = location[:ADDRESS_MAX]
        self.to = f"{there.scheme.lower()}://{_netloc(there)}{here.path.rstrip('/')}"[:ADDRESS_MAX]
        self.upgrade = (here.scheme.lower() == "http" and there.scheme.lower() == "https"
                        and _host(here) == _host(there) != "")
        super().__init__(f"{old} now redirects to {self.to}")


class Blocked(Unreachable):
    """Something in front of the server answered instead of it (``blocked``
    says how it tells): ``kind`` "signin", a redirect to another path (a
    sign-in host's ``/login?rd=…``, ``where`` its host), "proxy" (a
    401/403/407 page that is not Gamma's), "cloudflare" (Cloudflare's own
    refusal) or "gateway" (a 502/503/504 that is not Gamma's)."""

    def __init__(self, host: str, status: int, kind: str, where: str = ""):
        self.host, self.status, self.kind, self.where = host, status, kind, where
        super().__init__({
            "signin": f"a sign-in or proxy in front of {host} redirected this computer to {where}",
            "cloudflare": f"Cloudflare in front of {host} refused this computer's requests",
            "gateway": f"{host} answered {status} with a page that is not Gamma's: the proxy in front of it "
                       f"could not reach Gamma",
        }.get(kind, f"{host} answered {status} with a page that is not Gamma's: a proxy or firewall in front of "
                    f"it refused this computer"))


class CertificateRefused(Unreachable):
    """The server's certificate did not verify (``reason``, the
    ``ssl.SSLCertVerificationError``). ``changed``: a pinned origin served
    another certificate (gamma/tls_trust.py ``CertificateChanged``).
    ``seen``: what the served certificate says of itself, when the desktop
    app's server looked (``{sha256, subject, issuer, not_before,
    not_after}``), else None. ``missing_intermediate``: the server sent its
    certificate alone, without the one linking it to an authority."""

    def __init__(self, host: str, reason):
        self.host = host
        self.changed = bool(getattr(reason, "changed", False))
        self.seen = getattr(reason, "seen", None)
        unknown_issuer = getattr(reason, "verify_code", None) == 20   # UNABLE_TO_GET_ISSUER_CERT_LOCALLY
        self.missing_intermediate = unknown_issuer and getattr(reason, "chain_length", None) == 1
        if self.changed:
            words = f"The certificate of {host} changed"
        elif self.missing_intermediate:
            words = (f"{host} sends its certificate without the intermediate certificate that links it to a "
                     f"trusted authority: configure the server or its proxy to send the full chain (fullchain.pem)")
        elif unknown_issuer and getattr(reason, "chain_length", None) is None:
            words = (f"{host}'s certificate does not lead to an authority this computer trusts: either it is "
                     f"signed by your own authority (trust it in Gamma, or install that authority on this "
                     f"computer), or the server does not send its intermediate certificate (send the full "
                     f"chain, fullchain.pem)")
        else:
            why = getattr(reason, "verify_message", None) or getattr(reason, "reason", None) or str(reason)
            words = f"{host}'s certificate is not trusted on this computer ({why})"
        super().__init__(words)


class NotHttps(Unreachable):
    """An https:// address whose server answered no TLS (another
    ``ssl.SSLError``: WRONG_VERSION_NUMBER, plain http there), not a
    handshake the peer cut (``failure_of``)."""

    def __init__(self, host: str):
        self.host = host
        super().__init__(f"{host} does not speak HTTPS at this address")


def is_gamma_refusal(data: bytes) -> bool:
    """Whether an answer's body is Gamma's own refusal: a JSON object with
    a ``detail`` key (FastAPI's refusal shape)."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception:
        return False
    return isinstance(obj, dict) and "detail" in obj


def blocked(status: int, headers, data: bytes, *, own=is_gamma_refusal) -> str:
    """"" | "cloudflare" | "proxy" | "gateway": why an answer is a refusal by
    something in front of the service rather than the service's own. ``own``
    says what the service's own refusal looks like. Cloudflare's: a 401 or
    403 with ``cf-mitigated`` (its challenge), or with its headers
    (``cf-ray``, ``Server: cloudflare``) and not the service's own; a
    proxy's: any other 401, 403 or 407 not the service's own; a gateway's:
    a 502, 503 or 504 not the service's own."""
    if status not in (401, 403, 407, 502, 503, 504):
        return ""
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if status in (401, 403) and headers.get("cf-mitigated"):
        return "cloudflare"
    if own(data or b""):
        return ""
    if status in (502, 503, 504):
        return "gateway"
    if status != 407 and ("cf-ray" in headers or "cloudflare" in headers.get("server", "").lower()):
        return "cloudflare"
    return "proxy"


STATUS_KEYS = ("error_kind", "move_to", "upgrade", "cert", "blocked")   # dropped before a status is written


def status_fields(e: BaseException) -> dict:
    """The fields a status keeps of a failure, beside its words: walks e,
    its __cause__ and __context__ (5 deep) to the first of the classes above.
    {"error_kind": "redirected", "move_to": to, "upgrade": bool}
    | {"error_kind": "certificate" | "certificate_changed", "cert": seen or None}
    | {"error_kind": "not_https"} | {"error_kind": "blocked", "blocked": kind} | {}"""
    todo, seen = [(e, 0)], set()
    while todo:
        e, depth = todo.pop(0)
        if e is None or depth > 5 or id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, Redirected):
            return {"error_kind": "redirected", "move_to": e.to, "upgrade": e.upgrade}
        if isinstance(e, CertificateRefused):
            return {"error_kind": "certificate_changed" if e.changed else "certificate", "cert": e.seen}
        if isinstance(e, NotHttps):
            return {"error_kind": "not_https"}
        if isinstance(e, Blocked):
            return {"error_kind": "blocked", "blocked": e.kind}
        todo += [(e.__cause__, depth + 1), (e.__context__, depth + 1)]
    return {}


class RedirectRefused(Exception):
    """A 3xx's Location, out of ``opener()`` to its caller, which names it
    with ``failure_of``."""

    def __init__(self, status: int, location: str):
        super().__init__(location)
        self.status, self.location = status, location


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise RedirectRefused(code, newurl)


make_opener = urllib.request.build_opener   # the app routes it: net_proxy.install sets net_proxy.build_opener
_opener = (None, None)                      # (the make_opener it was built with, the opener)


def opener():
    """The opener every Server request goes through: make_opener's, with
    redirects refused. Built once per make_opener."""
    global _opener
    made_with, built = _opener
    if made_with is not make_opener:
        built = make_opener(_RefuseRedirects())
        _opener = (make_opener, built)
    return built


default_open_url = None   # tests: (url, method, path, headers, body) → (status, headers, body stream); tried before default_open


def failure_of(url: str, asked: str, error):
    """What a request ``asked`` (its full URL) of the server at ``url``
    that ``opener()`` failed with is, as this module names it, or None for
    any other failure. A refused redirect (``RedirectRefused``) that keeps
    the request's path is ``Redirected`` (a proxy rewriting scheme, host or
    port), any other ``Blocked`` (``signin``: a sign-in host's
    /login?rd=…, a proxy's page); a TLS failure (a URLError's reason, or a
    raw OSError) is ``CertificateRefused`` or ``NotHttps``, but for a
    handshake the peer cut (``_cut``), which is no answer at all. Server's
    requests and sync_engine's mirror requests both read it."""
    host = _host(urllib.parse.urlsplit(url)) or url
    if isinstance(error, RedirectRefused):
        there = urllib.parse.urlsplit(error.location)
        if there.path == urllib.parse.urlsplit(asked).path:
            return Redirected(url, error.location)
        return Blocked(host, error.status, "signin", where=_host(there) or error.location[:ADDRESS_MAX])
    reason = getattr(error, "reason", error)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return CertificateRefused(host, reason)
    if isinstance(reason, ssl.SSLError) and not _cut(reason):
        return NotHttps(host)
    return None


def _cut(e: ssl.SSLError) -> bool:
    """A handshake the peer cut (an EOF, a reset: a server restarting, a
    proxy accepting while its upstream is down), which passes by itself:
    an ordinary failure, never ``NotHttps``."""
    return isinstance(e, (ssl.SSLEOFError, ssl.SSLZeroReturnError, ssl.SSLSyscallError))         or getattr(e, "reason", None) == "UNEXPECTED_EOF_WHILE_READING"


# --- the server --------------------------------------------------------------

class Server:
    """A thin client of one Gamma server: JSON answers, file downloads, and
    the writes a push makes (a JSON body, an upload), with the token as a
    bearer header. It follows no redirect (``Redirected``, ``Blocked``) and
    names a TLS failure (``CertificateRefused``, ``NotHttps``);
    ``last_headers`` are the last answer's, for a caller that reads the
    status itself (``ask``) and classifies a refusal with ``blocked``."""

    def __init__(self, url, token, timeout=120):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout   # seconds a request may wait on a silent server
        if default_open_url is not None:
            self.open_ = lambda method, path, headers, body=None: default_open_url(self.url, method, path, headers, body)
        else:
            self.open_ = default_open or self._urllib_open
        self.format_checked = False   # ``check_format`` asked once
        self.last_headers = {}

    @property
    def host(self) -> str:
        return _host(urllib.parse.urlsplit(self.url)) or self.url

    def _headers(self):
        headers = {"Accept": "application/json", "User-Agent": "gamma-sync/1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _urllib_open(self, method, path, headers, body=None):
        # a body is bytes or an iterable of them, with its Content-Length among the headers
        req = urllib.request.Request(self.url + path, data=body, method=method, headers=headers)
        try:
            resp = opener().open(req, timeout=self.timeout)
            return resp.status, resp.headers, resp
        except RedirectRefused as e:
            raise failure_of(self.url, req.full_url, e) from None
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e
        except (urllib.error.URLError, OSError, ValueError) as e:
            failure = failure_of(self.url, req.full_url, e)
            if failure is not None:
                raise failure from e
            raise Unreachable(f"cannot reach {self.url}: {e}") from e

    def source(self, folder_id):
        """The folder's reads for a ``Round``."""
        return RemoteSource(self, folder_id)

    def check_format(self) -> None:
        """SyncError unless the server keeps its pages in a shape this client
        reads: its change feed's ``format`` (the workspace data format)
        ``DOCUMENT_FORMAT`` or newer. A server that names none, or an older
        one, keeps a page's PDF in properties this client no longer reads.
        Asked once."""
        if self.format_checked:
            return
        there = self.get_json("/api/sync/changes?limit=1").get("format")
        if not isinstance(there, int) or there < DOCUMENT_FORMAT:
            named = f" (workspace data format {there})" if isinstance(there, int) else ""
            raise SyncError(f"the server runs an older Gamma{named}: update it to use this gamma_sync")
        self.format_checked = True

    def _json(self, method, path, headers, body=None) -> dict:
        """The server's JSON answer. An answer cut short, or one that is no
        JSON (a sign-in or captive portal's page, answered with 200), is a
        SyncError like a refusal (``Unreachable``), so a round reports it;
        so is a refusal by something in front of the server (``Blocked``):
        ``Denied`` is a 401 or 403 of Gamma's own."""
        status, data = self._send(method, path, headers, body)
        if not 200 <= status < 300:
            kind = blocked(status, self.last_headers, data)
            if kind:
                raise Blocked(self.host, status, kind)
            raise {404: NotFound, 401: Denied, 403: Denied}.get(status, SyncError)(_refusal(status, data, path, self.token))
        try:
            return json.loads(data.decode("utf-8")) if data else {}
        except ValueError as e:
            raise Unreachable(f"{path}: the server did not answer JSON") from e

    def _send(self, method, path, headers, body=None) -> tuple[int, bytes]:
        status, self.last_headers, resp = self.open_(method, path, headers, body)
        try:
            return status, resp.read()
        except http.client.HTTPException as e:   # IncompleteRead among others; not an OSError
            raise Unreachable(f"{path}: the answer broke off ({e!r})") from e

    def get_json(self, path):
        return self._json("GET", path, self._headers())

    def send_json(self, method, path, obj) -> dict:
        """``obj`` sent as a JSON body: the server's JSON answer."""
        body = json.dumps(obj).encode("utf-8")
        return self._json(method, path, {**self._headers(), "Content-Type": "application/json",
                                         "Content-Length": str(len(body))}, body)

    def ask(self, method, path, obj=None, headers=None) -> tuple[int, bytes]:
        """``method path``, with ``obj`` as a JSON body when given and
        ``headers`` beside the client's own: ``(status, the answer's bytes)``
        whatever the status, for a caller that reads a refusal's own body
        (the desktop app's synced vault of another server,
        gamma/vault/http_remote.py). ``Unreachable`` as for ``get_json``."""
        headers, body = {**self._headers(), **(headers or {})}, None
        if obj is not None:
            body = json.dumps(obj).encode("utf-8")
            headers = {**headers, "Content-Type": "application/json", "Content-Length": str(len(body))}
        return self._send(method, path, headers, body)

    def upload_pdf(self, path: Path) -> dict:
        """The PDF at ``path`` stored on the server (``POST /api/uploads``,
        one multipart part streamed from disk): ``{doc_id, ...}``. Bytes the
        server stores already are stored once."""
        return self.upload(path, "/api/uploads", "application/pdf")

    def upload(self, path: Path, route: str, content_type: str, filename: str = "", *, field: str = "file",
               offset: int = 0, length: int | None = None, fields: dict | None = None) -> dict:
        """The file at ``path`` sent to ``route`` as its one multipart part
        ``field`` (``file``), streamed from disk, named ``filename`` (its
        own name: the server stores a file under that name's extension):
        the server's JSON answer. ``offset`` and ``length`` send a slice of
        it (an upload in parts, ``/api/uploads/parts/{token}``), ``fields``
        text fields before the file's part."""
        boundary = f"gamma-sync-{os.urandom(12).hex()}"
        name = (filename or path.name).replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")
        head = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
                       for key, value in (fields or {}).items())
        head += (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\n'
                 f"Content-Type: {content_type}\r\n\r\n")
        head = head.encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("ascii")
        size = path.stat().st_size - offset if length is None else length
        headers = {**self._headers(), "Content-Type": f"multipart/form-data; boundary={boundary}",
                   "Content-Length": str(len(head) + size + len(tail))}
        return self._json("POST", route, headers, _parts(head, path, size, tail, offset))

    def download(self, path, target: Path) -> None:
        """GET ``path`` into ``target``, written beside it and moved into
        place once whole. A body shorter than the announced length is an
        error, never a file."""
        status, headers, body = self.open_("GET", path, self._headers())
        self.last_headers = headers
        if status != 200:
            raise SyncError(_refusal(status, body.read(), path, self.token))
        length = headers.get("Content-Length")
        part = target.with_name(target.name + PART)
        size = 0
        try:
            with open(part, "wb") as f:
                while chunk := body.read(1 << 20):
                    f.write(chunk)
                    size += len(chunk)
            if length is not None and size != int(length):
                raise SyncError(f"{path}: received {size} of {length} bytes")
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)


def _parts(head: bytes, path: Path, size: int, tail: bytes, offset: int = 0):
    """A multipart body: ``head``, ``size`` bytes of ``path`` from ``offset``,
    ``tail``. A file that shrank meanwhile is ``Changed``, never a short
    body."""
    yield head
    left = size
    with open(path, "rb") as f:
        f.seek(offset)
        while left:
            chunk = f.read(min(left, CHUNK))
            if not chunk:
                raise Changed(f"{path.name} changed while it was sent")
            left -= len(chunk)
            yield chunk
    yield tail


class RemoteSource:
    """What a round reads of one folder, over HTTP: the manifest, the notes
    files of some pages, a stored file (gamma/folder_sync.py serves them)."""

    def __init__(self, server: Server, folder_id: str):
        self.server = server
        self.folder = urllib.parse.quote(folder_id, safe="")

    def manifest(self) -> dict:
        return self.server.get_json(f"/api/sync/folders/{self.folder}")

    def notes(self, page_ids) -> dict:
        return self.server.get_json(f"/api/sync/folders/{self.folder}/notes?pages={','.join(page_ids)}")["pages"]

    def download(self, name: str, target: Path) -> None:
        self.server.download(f"/api/uploads/{name}", target)


class RemoteSink:
    """Where a push writes, over HTTP with a token that may write: the
    file's bytes stored, a page's PDF replaced by them, and what the page
    shows now. The desktop app's own server writes the same three
    in-process for the files it pushes (gamma/folder_links.py
    ``LocalSink``)."""

    def __init__(self, server: Server):
        self.server = server

    def store(self, path: Path) -> str:
        return self.server.upload_pdf(path)["doc_id"]

    def replace(self, page_id: str, doc_id: str, filename: str) -> dict:
        return self.server.send_json("PUT", f"/api/pages/{urllib.parse.quote(page_id, safe='')}/attachment",
                                     {"doc_id": doc_id, "original_filename": filename, "keep_old_days": KEEP_OLD_DAYS})

    def current(self, page_id: str) -> dict | None:
        """``{doc_id, filename, title}`` of the page now: the hash of the
        document it owns (its ``doc``; none for a duplicated page, which
        shows another's) and that document's name (``""`` for no PDF or no
        file name); None when it is gone, in the trash, or no page. The
        server's format is checked first (``Server.check_format``)."""
        self.server.check_format()
        try:
            page = self.server.get_json(f"/api/blocks/{urllib.parse.quote(page_id, safe='')}")
        except NotFound:
            return None
        if page.get("parent_id") != "root":
            return None
        doc = (page.get("properties") or {}).get("doc")
        doc = doc if isinstance(doc, dict) else {}
        return {"doc_id": "" if doc.get("copy") else str(doc.get("hash") or ""), "filename": str(doc.get("name") or ""),
                "title": page.get("content") or ""}


def scrubbed(detail, token: str):
    """A server's ``detail`` with ``token``, the one it was sent, left out
    (a server or a proxy that echoes the request's header): a string, or a
    list or an object, written as JSON then, that holds it."""
    if not token:
        return detail
    text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)
    return text.replace(token, "…") if token in text else detail


def _refusal(status, data, path, token=""):
    """What a refusal says, without ``token``, the one the request carried."""
    try:
        detail = scrubbed(json.loads(data.decode("utf-8")).get("detail", ""), token)
    except Exception:
        detail = ""
    if status == 401:
        return "the server refused the token: expired, revoked, or made for another server"
    if status == 403:
        return f"the token may not do this: {detail or 'forbidden'}"
    if status == 404:
        return f"not found on the server: {detail or path}"
    return f"{path}: {status} {detail}".strip()


# --- the link ---------------------------------------------------------------

class Link:
    """A destination directory and its state file: the server, the folder,
    and every file the client wrote (its page, its identity, its size and
    time on disk)."""

    def __init__(self, dest: Path, state=None):
        self.dest = dest
        self.file = dest / STATE
        self.state = state

    @classmethod
    def load(cls, dest: Path):
        link = cls(dest)
        if not link.file.is_file():
            raise SyncError(f"{dest} is not a linked folder: run init first")
        link.state = json.loads(link.file.read_text(encoding="utf-8"))
        if link.state.get("format") != FORMAT:
            raise SyncError(f"{link.file} was written by another version of gamma-sync")
        link.state.setdefault("files", {})
        link.state.setdefault("dirs", [])
        return link

    def save(self):
        part = self.file.with_name(self.file.name + PART)
        part.write_text(json.dumps(self.state, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(part, self.file)


def kind(entry) -> str:
    return "pdf" if "doc" in entry else "notes" if "version" in entry else "attachment"


def identity(entry):
    return entry.get("doc") or entry.get("version")


def safe_rel(path: str) -> bool:
    """A relative path the server may ask for: forward slashes, no empty,
    dot or dot-dot parts, nothing that could leave the directory or collide
    with the client's own files."""
    if not path or path.startswith("/") or "\\" in path or ":" in path:
        return False
    parts = path.split("/")
    return all(p not in ("", ".", "..") and p != STATE and not p.endswith(PART) for p in parts)


def file_stat(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtime": round(st.st_mtime, 3)}


def modified(path: Path, entry) -> bool:
    """Whether the file differs from what the client wrote (size, or a time
    more than a second away). A missing file is not a modified one."""
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_size != entry.get("size") or abs(st.st_mtime - float(entry.get("mtime") or 0)) > 1.0


def write_text(target: Path, text: str) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + PART)
    part.write_bytes(text.encode("utf-8"))
    os.replace(part, target)
    return file_stat(target)


# --- sending PDFs back ------------------------------------------------------

def doc_of(path: Path) -> str:
    """The id Gamma gives the file's bytes: the first DOC_CHARS hex
    characters of their SHA-256. Equal to the hash of a page's document,
    the file is the page's PDF, and nothing needs to travel to know it."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()[:DOC_CHARS]


def settled(path: Path, tail: bytes | None = b"%%EOF") -> str:
    """"" when the file is whole and may be sent: written at least SETTLE_S
    ago (a time ahead of this clock, a share's whose clock runs ahead, counts
    as long ago: the tests after it say whether it is whole), open for
    reading (Windows refuses while its writer holds it), and ending as a PDF
    ends, with ``tail`` ("%%EOF") in its last EOF_TAIL bytes; ``tail`` None
    skips that test for a file of any format (a synced vault's work folder
    file), the two before it staying. Otherwise why not yet; a compile
    writes its PDF over a second or more."""
    try:
        written = path.stat().st_mtime
    except OSError:
        return NOT_THERE
    if 0 <= time.time() - written < SETTLE_S:
        return WRITING
    try:
        with open(path, "rb") as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - EOF_TAIL))
            end = f.read()
    except OSError as e:
        return f"cannot be read: {e}"
    return "" if tail is None or tail in end else NOT_WHOLE


def moved(path: Path, entry) -> bool:
    """Whether a loop has a round to start for the file: it is there and
    moved since the entry recorded it, or since ``seen``, the stat a push
    recorded when it could not send the file (kept, or not a whole PDF), so
    that file starts no round until it moves again. A round that runs for
    another reason still looks at it (``Push``)."""
    return modified(path, entry.get("seen") or entry)


def pdfs_moved(dest: Path, files: dict) -> bool:
    """Whether a PDF among a link's ``files`` (its state's) moved in ``dest``
    since a round recorded it (``moved``): a link that sends PDFs back has
    a round to run, whatever Gamma's change feed says."""
    return any(kind(e) == "pdf" and safe_rel(p) and moved(dest / p, e) for p, e in files.items())


def _stat(path: Path) -> dict | None:
    try:
        return file_stat(path)
    except OSError:
        return None


class Push:
    """PDFs changed on disk made their pages' new files: one pass over
    ``items``, each ``(key, file, entry)`` with the entry what was last known
    of the file (``{page, doc, size, mtime}``, and ``seen``: the stat of a
    file a pass could not send), brought up to date in place. It writes
    through a **sink** (``store``, ``replace``, ``current``: ``RemoteSink``
    over HTTP, or the desktop server's ``LocalSink``). A file is sent only
    when it differs from what the entry recorded, is whole (``settled``)
    and holds other bytes than the page shows. It says what it sends; what
    waits and what is kept it lists (``waiting``, ``kept``) for its caller
    to report. Every pass looks at a kept file again (and sends it again
    when it is whole): the next one that runs for any reason. ``seen``
    only keeps the loops (``moved``) from starting a pass for it before it
    moves, or, for the desktop server's file link, before its retry wait
    (``RETRY_S``) ends. A file that changed while it was read to be sent
    waits, without ``seen``.

    ``mode`` "folder" is a kept folder's PDFs, sent before its pull: a
    missing file is the pull's to fetch again, and one whose page Gamma gave
    another file meanwhile is left to the pull, which keeps the disk's and
    says so. ``mode`` "file" is one followed file, where the disk wins: a
    file replaced in Gamma by hand gives way at the file's next change."""

    def __init__(self, items, sink, *, mode, dry_run=False, say=print):
        self.items, self.sink, self.mode, self.dry_run, self.say = items, sink, mode, dry_run, say
        self.counts = {"sent": 0, "waiting": 0, "kept": 0}
        self.sent = []      # (key, the server's answer to the replacement)
        self.waiting = []   # (key, why): not sent yet, tried again at the next pass
        self.kept = []      # (key, why): not sent; the next pass that runs tries again (``seen``)

    def run(self) -> dict:
        for key, file, entry in self.items:
            try:
                self._push(key, file, entry)
            except (SyncError, OSError) as e:
                self._keep(key, file, entry, str(e))
        return self.counts

    def _push(self, key, file: Path, entry):
        before = _stat(file)
        if before is None:
            if self.mode == "file":
                self._wait(key, NOT_THERE)
            return
        if not modified(file, entry):
            return
        why = settled(file)
        if why:
            self._wait(key, why)
            if why == NOT_WHOLE:
                # at rest and still no PDF's end (a download cut short, another file saved over it): no
                # loop starts a pass for it until it is written again, which moves its size or time. A
                # file still being written or locked has no such mark: a lock can end with no new stat.
                entry["seen"] = before
            return
        doc = doc_of(file)
        if doc == entry.get("doc"):
            self._record(entry, doc, before)   # touched, the same bytes
            return
        page = self.sink.current(entry["page"])
        if page is None:
            self._keep(key, file, entry, "the page is no longer in Gamma")
            return
        if page["doc_id"] == doc:
            self._record(entry, doc, before)   # Gamma has these bytes already
            return
        if self.mode == "folder" and page["doc_id"] != entry.get("doc"):
            entry["seen"] = before             # changed here and in Gamma: the pull keeps the file
            return
        if self.dry_run:
            self.say("would send", key)
            self.counts["sent"] += 1
            return
        try:
            stored = self.sink.store(file)
        except Changed:
            stored = None
        except (SyncError, OSError):
            if _stat(file) == before:
                raise             # the server's refusal, or the disk's: kept
            stored = None         # the file moved under the read, whatever the sink made of that
        if stored != doc or _stat(file) != before:
            self._wait(key, "the file changed while it was sent")
            return
        answer = self.sink.replace(entry["page"], stored, page["filename"] or file.name)
        self._record(entry, stored, before)
        self.sent.append((key, answer))
        self.counts["sent"] += 1
        self.say("sent", key)

    @staticmethod
    def _record(entry, doc, stat):
        entry.update(doc=doc, **stat)
        entry.pop("seen", None)

    def _wait(self, key, why):
        self.waiting.append((key, why))
        self.counts["waiting"] += 1

    def _keep(self, key, file: Path, entry, why):
        self.kept.append((key, why))
        self.counts["kept"] += 1
        seen = _stat(file)
        if seen is not None:
            entry["seen"] = seen


# --- one round --------------------------------------------------------------

class Round:
    """One pass: the folder's manifest against the link's state, and the
    difference pulled to disk — renames first, then new and changed files,
    then the attachments the notes need, then what is gone. A link that
    pushes PDFs (its state's ``push``, and a ``sink`` to write through: a
    sync's, never a pull's) first pushes the ones changed on disk
    (``Push``).

    ``progress``, when given, is called with what the round fetches as it
    goes, from the comparison on: ``{done, total, bytes: {done, total},
    path}``, the files (notes, PDFs, then the attachments the notes name,
    which join ``total`` once known) and the PDFs' bytes from the
    manifest's ``pdf_size``, ``path`` the file being fetched now."""

    def __init__(self, link: Link, source, *, sink=None, full=False, force=False, dry_run=False, say=print,
                 progress=None):
        self.link, self.source = link, source
        self.full, self.force, self.dry_run, self.say, self.progress = full, force, dry_run, say, progress
        self.done = self.total = self.bytes_done = self.bytes_total = 0
        self.dest = link.dest
        self.state = copy.deepcopy(link.state) if dry_run else link.state
        self.sink = sink if self.state.get("push") else None
        self.counts = {"added": 0, "updated": 0, "renamed": 0, "removed": 0, "kept": 0, "unchanged": 0,
                       "sent": 0, "waiting": 0}
        self.kept = []   # (path, why, force): ``force`` when a forced round (``--force``) would replace it

    def keep(self, path, why, force=False):
        self.kept.append((path, why, force))
        self.counts["kept"] += 1
        self.say("kept", f"{path}  ({why})")

    def run(self) -> dict:
        st = self.state
        files = st["files"]
        if self.sink is not None:
            self._push(files)
        manifest = self.source.manifest()
        wanted = self._wanted(manifest)
        old_dirs = list(st["dirs"])
        self._rename(files, wanted)
        self._make_dirs(manifest)
        notes_todo, pdf_todo = self._compare(files, wanted)
        self.total = len(notes_todo) + len(pdf_todo)
        self.bytes_total = sum(w.get("size", 0) for _, w, _ in pdf_todo)
        self._report()
        self._fetch_notes(notes_todo, files)
        self._fetch_pdfs(pdf_todo, files)
        self._attachments(files)
        self._remove(files, wanted)
        self._prune_dirs(manifest, old_dirs)
        st["cursor"] = manifest.get("cursor", "")
        st["folder_path"] = manifest["folder"]["path"]
        st["synced_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not self.dry_run:
            self.link.save()
        return self.counts

    def _report(self, path=""):
        if self.progress is not None:
            self.progress({"done": self.done, "total": self.total, "path": path,
                           "bytes": {"done": self.bytes_done, "total": self.bytes_total}})

    def _push(self, files):
        """The PDFs changed on disk, sent before the manifest is fetched, so
        that it names their new identity and the pull leaves them alone."""
        items = [(path, self.dest / path, e) for path, e in files.items() if kind(e) == "pdf" and safe_rel(path)]
        push = Push(items, self.sink, mode="folder", dry_run=self.dry_run, say=self.say)
        push.run()
        self.counts["sent"] += push.counts["sent"]
        self.counts["waiting"] += push.counts["waiting"]
        for path, why in push.waiting:
            self.say("waiting", f"{path}  ({why})")
        for path, why in push.kept:
            self.keep(path, why)   # not sent: Gamma's file is the same, so --force has nothing to replace

    def _wanted(self, manifest) -> dict:
        """path → the entry the file should have: its page and identity."""
        wanted = {}
        for p in manifest["pages"]:
            if p.get("pdf") and safe_rel(p["pdf"]):
                wanted[p["pdf"]] = {"page": p["id"], "doc": p["doc_id"], "size": int(p.get("pdf_size") or 0)}
            if self.state.get("notes", True) and p.get("notes") and safe_rel(p["notes"]):
                wanted[p["notes"]] = {"page": p["id"], "version": p["version"]}
        return wanted

    def _rename(self, files, wanted):
        """A page's file that moved — the title or a folder changed — is
        renamed on disk rather than fetched again, when it is still what the
        client wrote and nothing is in the way."""
        by_key = {(e["page"], kind(e)): path for path, e in files.items() if "page" in e}
        for new, w in wanted.items():
            old = by_key.get((w["page"], kind(w)))
            if not old or old == new or new in files:
                continue
            src, dst = self.dest / old, self.dest / new
            if src.is_file() and not dst.exists() and not modified(src, files[old]):
                self.say("renamed", f"{old} -> {new}")
                if not self.dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(src, dst)
                files[new] = files.pop(old)
                self.counts["renamed"] += 1

    def _make_dirs(self, manifest):
        for d in manifest["dirs"]:
            if safe_rel(d["path"]) and not self.dry_run:
                (self.dest / d["path"]).mkdir(parents=True, exist_ok=True)

    def _compare(self, files, wanted):
        """Which wanted files to fetch, and why."""
        notes_todo, pdf_todo = [], []
        for path, w in wanted.items():
            have = files.get(path)
            target = self.dest / path
            if have is None:
                if target.exists():
                    self.keep(path, "a file gamma-sync did not write is already there")
                    continue
                verb = "added"
            elif identity(have) == identity(w) and not self.full:
                if target.is_file():
                    self.counts["unchanged"] += 1
                    continue
                verb = "restored"
            elif modified(target, have) and not self.force:
                self.keep(path, "changed here and in Gamma; --force takes Gamma's"
                          if self.sink is not None and kind(w) == "pdf" else "changed on disk; --force replaces it",
                          force=True)
                continue
            else:
                verb = "updated"
            (pdf_todo if kind(w) == "pdf" else notes_todo).append((path, w, verb))
        return notes_todo, pdf_todo

    def _count(self, verb):
        self.counts["added" if verb in ("added", "restored") else "updated"] += 1

    def _fetch_notes(self, todo, files):
        for i in range(0, len(todo), NOTES_BATCH):
            batch = todo[i:i + NOTES_BATCH]
            got = {}
            if not self.dry_run:
                got = self.source.notes([w["page"] for _, w, _ in batch])
            for path, w, verb in batch:
                self.done += 1
                if self.dry_run:
                    self.say(verb, path)
                    self._count(verb)
                    continue
                page = got.get(w["page"])
                if page is None:
                    self.keep(path, "the server did not return this page")
                    continue
                self.say(verb, path)
                self._count(verb)
                files[path] = {"page": w["page"], "version": page.get("version") or w["version"],
                               "attachments": page.get("attachments") or [], **write_text(self.dest / path, page["markdown"])}
            self._report(batch[-1][0])

    def _fetch_pdfs(self, todo, files):
        for path, w, verb in todo:
            self.say(verb, path)
            self._count(verb)
            if self.dry_run:
                self._fetched(w)
                continue
            self._report(path)
            target = self.dest / path
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.source.download(f"{w['doc']}.pdf", target)
            except SyncError as e:
                self.keep(path, str(e))
                self._fetched(w)
                continue
            files[path] = {"page": w["page"], "doc": w["doc"], **file_stat(target)}
            self._fetched(w)
        if todo:
            self._report()

    def _fetched(self, w):
        self.done += 1
        self.bytes_done += w.get("size", 0)

    def _attachments(self, files):
        """The pictures and files the notes refer to, by content-hash name:
        fetched once, dropped when no note names them any more."""
        referenced = set()
        for e in files.values():
            referenced.update(e.get("attachments") or [])
        todo = [name for name in sorted(referenced) if safe_rel(f"attachments/{name}") and "/" not in name
                and not (f"attachments/{name}" in files and (self.dest / "attachments" / name).is_file())]
        self.total += len(todo)
        for name in todo:
            path = f"attachments/{name}"
            self._report(path)
            self.say("added", path)
            self.counts["added"] += 1
            self.done += 1
            if self.dry_run:
                continue
            (self.dest / "attachments").mkdir(exist_ok=True)
            try:
                self.source.download(name, self.dest / path)
            except SyncError as e:
                self.keep(path, str(e))
                continue
            files[path] = {"attachment": True, **file_stat(self.dest / path)}
        if todo:
            self._report()
        for path in [p for p, e in files.items() if kind(e) == "attachment" and p.split("/", 1)[-1] not in referenced]:
            self._delete(path, files)

    def _remove(self, files, wanted):
        for path in [p for p, e in files.items() if "page" in e and p not in wanted]:
            self._delete(path, files)

    def _delete(self, path, files):
        target = self.dest / path
        if target.is_file() and modified(target, files[path]):
            self.keep(path, "changed on disk; left in place, no longer synced")
            files.pop(path)
            return
        self.say("removed", path)
        self.counts["removed"] += 1
        if not self.dry_run:
            target.unlink(missing_ok=True)
        files.pop(path)

    def _prune_dirs(self, manifest, old_dirs):
        """Directories of folders that are gone go too, deepest first, when
        nothing is left in them."""
        new_dirs = sorted({d["path"] for d in manifest["dirs"] if safe_rel(d["path"])})
        for d in sorted(set(old_dirs) - set(new_dirs), key=len, reverse=True):
            p = self.dest / d
            if p.is_dir() and not any(p.iterdir()):
                self.say("removed", d + "/")
                if not self.dry_run:
                    p.rmdir()
        self.state["dirs"] = new_dirs


# --- commands ---------------------------------------------------------------

def resolve_token(args, state=None) -> str:
    token = getattr(args, "token", None) or os.environ.get("GAMMA_TOKEN") or (state or {}).get("token")
    if not token:
        raise SyncError("no token: pass --token, set GAMMA_TOKEN, or link with init --save-token")
    return token


def _path_text(names) -> str:
    return " / ".join(names)


def resolve_folder(server: Server, text: str):
    """The folder ``text`` names — its id, or its path with ``/`` between the
    names (spaces around the slashes do not matter, nor does case); ``root``
    is the whole library — as ``(id, names)``."""
    if text.strip().lower() == "root":
        return "root", []
    folders = server.get_json("/api/sync/folders")["folders"]
    for f in folders:
        if f["id"] == text:
            return f["id"], f["path"]
    norm = "/".join(part.strip().lower() for part in text.split("/"))
    found = [f for f in folders if "/".join(n.strip().lower() for n in f["path"]) == norm
             or _path_text(f["path"]).lower() == text.strip().lower()]
    if len(found) == 1:
        return found[0]["id"], found[0]["path"]
    if found:
        raise SyncError(f"{len(found)} folders are called {text!r}; name one by id: "
                        + ", ".join(f["id"] for f in found))
    raise SyncError(f"no folder called {text!r}; `folders` lists them")


def cmd_folders(args):
    server = Server(args.server, resolve_token(args))
    print("root  (the whole library)")
    for f in server.get_json("/api/sync/folders")["folders"]:
        print(f"{_path_text(f['path'])}  ({f['id']})")
    return 0


def cmd_init(args):
    dest = Path(args.dest).expanduser().resolve()
    if (dest / STATE).exists():
        raise SyncError(f"{dest} is already linked; run sync, or delete {STATE} to link it again")
    token = resolve_token(args)
    server = Server(args.server, token)
    who = server.get_json("/api/sync/whoami")
    if args.push and who.get("scope") != "write":
        raise SyncError("--push needs a token that may write")
    folder_id, path = resolve_folder(server, args.folder)
    dest.mkdir(parents=True, exist_ok=True)
    state = {"format": FORMAT, "server": server.url, "workspace": who["workspace"]["id"],
             "workspace_name": who["workspace"].get("name", ""), "folder": folder_id, "folder_path": path,
             "notes": not args.no_notes, "push": args.push, "files": {}, "dirs": [], "cursor": ""}
    if args.save_token:
        state["token"] = token
    Link(dest, state).save()
    where = _path_text(path) or "the whole library"
    print(f"Linked {dest} to {where} in {state['workspace_name'] or who['workspace']['id']} on {server.url}.")
    if args.push:
        print("sync pushes the PDFs you change there; pull never does.")
    print(f"Next: python {Path(sys.argv[0]).name} sync {dest}")
    return 0


def _summary(counts) -> str:
    parts = [f"{n} {verb}" for verb, n in counts.items() if n and verb != "unchanged"]
    return ", ".join(parts) if parts else "up to date"


def changed_since(server: Server, cursor: str) -> bool:
    """Whether the server's change feed moved past ``cursor`` (always, for
    no cursor yet): one request, what ``--watch`` and a server's own links
    with a remote source (gamma/folder_links.py) ask between rounds."""
    if not cursor:
        return True
    feed = server.get_json(f"/api/sync/changes?since={urllib.parse.quote(cursor)}&limit=1")
    return bool(feed.get("pages") or feed.get("deleted"))


def cmd_sync(args):
    """Push, when the link pushes, then pull; with ``--watch``, again after
    every change, on disk or in Gamma."""
    return _rounds(args, push=True)


def cmd_pull(args):
    """Pull alone: Gamma's changes written, nothing pushed, whatever the link
    says; with ``--watch``, again after every change in Gamma."""
    return _rounds(args, push=False)


def _rounds(args, *, push: bool):
    dest = Path(args.dest).expanduser().resolve()
    link = Link.load(dest)
    server = Server(link.state["server"], resolve_token(args, link.state))
    sink = RemoteSink(server) if push and link.state.get("push") else None
    while True:
        try:
            counts = Round(link, server.source(link.state["folder"]), sink=sink, full=args.full, force=args.force,
                           dry_run=args.dry_run).run()
            print(("would: " if args.dry_run else "") + _summary(counts)
                  + (f"; {counts['unchanged']} unchanged" if counts["unchanged"] else ""))
        except SyncError as e:
            if not args.watch:
                raise
            print(f"error: {e}", file=sys.stderr)
        if not args.watch:
            return 0
        while True:
            time.sleep(args.watch)
            if sink is not None and pdfs_moved(dest, link.state["files"]):
                break
            try:
                if changed_since(server, link.state.get("cursor", "")):
                    break
            except SyncError as e:
                print(f"error: {e}", file=sys.stderr)


def cmd_status(args):
    dest = Path(args.dest).expanduser().resolve()
    st = Link.load(dest).state
    kinds = {"pdf": 0, "notes": 0, "attachment": 0}
    for e in st["files"].values():
        kinds[kind(e)] += 1
    print(f"{dest}")
    print(f"  folder:    {_path_text(st.get('folder_path') or []) or 'the whole library'} ({st['folder']})")
    print(f"  server:    {st['server']} ({st.get('workspace_name') or st.get('workspace')})")
    print(f"  notes:     {'on' if st.get('notes', True) else 'off'}")
    print(f"  push:      {'on: sync pushes the PDFs changed here' if st.get('push') else 'off'}")
    print(f"  last sync: {st.get('synced_at') or 'never'}")
    print(f"  files:     {kinds['pdf']} PDFs, {kinds['notes']} notes, {kinds['attachment']} attachments, "
          f"{len(st['dirs'])} folders")
    return 0


def _pushed(push: Push, dry_run: bool) -> str:
    """What one push of a followed file did, in a line."""
    if push.counts["sent"]:
        if dry_run:
            return "would send"
        problem = push.sent[0][1].get("relocation_error")
        return f"sent; the highlights stay where they were: {problem}" if problem else "sent"
    if push.waiting:
        return f"waiting: {push.waiting[0][1]}"
    if push.kept:
        return f"not sent: {push.kept[0][1]}"
    return "up to date"


def _rested(path: Path) -> None:
    """Sleep until the file was last written SETTLE_S ago: ``push`` runs the
    moment a build ends, and a file that fresh would only wait. Never more
    than SETTLE_S: a file dated in the future (a clock set wrong) does not
    hold the run up."""
    try:
        fresh = SETTLE_S - (time.time() - path.stat().st_mtime)
    except OSError:
        return
    if fresh > 0:
        time.sleep(min(fresh, SETTLE_S))


def cmd_push(args):
    file = Path(args.file).expanduser().resolve()
    server = Server(args.server, resolve_token(args))
    if server.get_json("/api/sync/whoami").get("scope") != "write":
        raise SyncError("push needs a token that may write")
    sink = RemoteSink(server)
    if sink.current(args.page) is None:
        raise SyncError(f"no page {args.page} in Gamma")
    entry = {"page": args.page}   # what this run knows of the file; the page is the state between runs
    while True:
        _rested(file)
        push = Push([(file.name, file, entry)], sink, mode="file", dry_run=args.dry_run, say=lambda *_: None)
        push.run()
        print(_pushed(push, args.dry_run))
        if not args.watch:
            return 0
        while True:
            time.sleep(args.watch)
            if moved(file, entry):
                break


# Options whose value is free text. A page, folder or block id comes from
# token_urlsafe, so about one in 64 begins with "-", which argparse reads as
# another option ("--folder: expected one argument"): `--folder <id>` is
# passed on as `--folder=<id>`, the one spelling argparse takes as given.
VALUE_OPTIONS = ("--server", "--token", "--folder", "--page", "--watch")


def glued(argv):
    """``argv`` with each VALUE_OPTIONS value joined to its option by "=".
    An option at the very end keeps its own missing-value error."""
    out, rest = [], list(argv)
    while rest:
        arg = rest.pop(0)
        if arg in VALUE_OPTIONS and rest:
            arg = f"{arg}={rest.pop(0)}"
        out.append(arg)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gamma_sync.py", description=__doc__.split("\n\n", 1)[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__.split("\n\n", 1)[1])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("folders", help="list the workspace's folders")
    p.add_argument("--server", required=True, help="the Gamma server's URL")
    p.add_argument("--token", help="an integration token (or set GAMMA_TOKEN)")
    p.set_defaults(run=cmd_folders)

    p = sub.add_parser("init", help="link a directory to a folder")
    p.add_argument("dest", help="the directory on disk (made if missing)")
    p.add_argument("--server", required=True, help="the Gamma server's URL")
    p.add_argument("--folder", required=True, help="the folder: its path (Papers / Quantum), its id, or root")
    p.add_argument("--token", help="an integration token (or set GAMMA_TOKEN)")
    p.add_argument("--save-token", action="store_true", help=f"keep the token in {STATE} (readable by anyone with the directory)")
    p.add_argument("--no-notes", action="store_true", help="PDFs only, no notes files")
    p.add_argument("--push", action="store_true",
                   help="push the PDFs you change in the directory, at each sync (a token that may write)")
    p.set_defaults(run=cmd_init)

    for name, run, what in (("pull", cmd_pull, "write Gamma's changes to the directory, push nothing"),
                            ("sync", cmd_sync, "push the PDFs changed on disk (with init --push), then pull")):
        p = sub.add_parser(name, help=what)
        p.add_argument("dest")
        p.add_argument("--token")
        p.add_argument("--watch", type=float, default=0, metavar="SECONDS",
                       help=f"keep running: look for changes every SECONDS and {name} when there are any")
        p.add_argument("--full", action="store_true", help="write every file again, not only the changed ones")
        p.add_argument("--force", action="store_true", help="replace files changed on disk with Gamma's")
        p.add_argument("--dry-run", action="store_true", help="say what would be done, change nothing")
        p.set_defaults(run=run)

    p = sub.add_parser("status", help="what the directory is linked to and holds")
    p.add_argument("dest")
    p.set_defaults(run=cmd_status)

    p = sub.add_parser("push", help="make a PDF on disk a page's file, now or at each change")
    p.add_argument("file", help="the PDF on disk")
    p.add_argument("--page", required=True, help="the page's id")
    p.add_argument("--server", required=True, help="the Gamma server's URL")
    p.add_argument("--token", help="an integration token that may write (or set GAMMA_TOKEN)")
    p.add_argument("--watch", type=float, default=0, metavar="SECONDS",
                   help="keep running: look at the file every SECONDS and send each new version")
    p.add_argument("--dry-run", action="store_true", help="say whether it would be sent, send nothing")
    p.set_defaults(run=cmd_push)

    args = parser.parse_args(glued(sys.argv[1:] if argv is None else argv))
    try:
        return args.run(args) or 0
    except Redirected as e:
        print(f'gamma-sync: {e}: if the server moved, set "server" to "{e.to}" in {STATE}', file=sys.stderr)
        return 1
    except SyncError as e:
        print(f"gamma-sync: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):   # titles are not all Latin; a cp1252 console must not stop the sync
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:   # the one optional import: HTTPS verified as the browser does (a Keychain root), when installed
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    sys.exit(main())
