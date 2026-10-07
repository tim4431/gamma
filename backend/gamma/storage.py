"""Uploaded files: media types, content-hash storage (atomic, verified
writes), lookup, the one grammar of a reference to a stored file, and the
store itself: each workspace's ``uploads/`` directory (``db.ws_uploads_dir``),
one file per name. What becomes of a file nothing references any more is
gamma/upload_gc.py; the off-site copies of the files are gamma/offsite.py.

The store's calls, at the end of the module, each name a workspace and a
file name (checked by ``check_name``; what a name says about its bytes is
``matches_name``, at the callers): ``put``, ``exists``, ``size``, ``stat``
(``(size, mtime)``), ``open_path``, ``delete``, ``delete_workspace``,
``list`` (``(name, size, mtime)``), ``touch`` (the upload GC's re-date),
``usage`` (bytes stored, cached), ``partial_dir`` (where a file is
assembled before it is stored), ``put_path`` (a file assembled there,
stored by a rename) and ``sweep_partial`` (dead temp files). Every read
and write of a stored file goes through them.

Docs: docs/dev/user_db.md "Stored files".
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
import urllib.parse
from pathlib import Path
from stat import S_ISREG  # not ``import stat``: the module's own stat() would shadow it

from . import pdf_meta
from .db import ws_uploads_dir
from .logbuf import log
from .server_settings import check_upload_allowed

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}
IMAGE_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg"}
IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}

# Generic (non-image, non-PDF) files blocks reference as
# ``[name](/api/uploads/<hash>.<ext>)`` file blocks. POST /api/upload-file
# takes ANY extension except BLOCKED_EXTENSIONS (a lab shares notebooks, data
# files, packages — an allowlist can never be complete); this table only picks
# a better media type than ``application/octet-stream`` for the common ones.
# Everything outside INLINE_EXTENSIONS is served as a download anyway.
FILE_MEDIA_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json",
    ".ink": "application/json",   # handwriting groups (gamma/ink.py)
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": "application/toml",
    ".xml": "application/xml",
    ".tex": "application/x-tex",
    ".bib": "application/x-bibtex",
    ".py": "text/x-python; charset=utf-8",
    ".ipynb": "application/x-ipynb+json",
    ".nb": "application/mathematica",
    ".m": "text/plain; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".zip": "application/zip",
    ".gz": "application/gzip",
    ".tgz": "application/gzip",
    ".tar": "application/x-tar",
    ".7z": "application/x-7z-compressed",
    ".whl": "application/zip",
    ".h5": "application/x-hdf5",
    ".hdf5": "application/x-hdf5",
    ".mp4": "video/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}
# Files an OS runs as code when opened — the one kind Gamma refuses to host,
# on upload and (belt and braces) on serving. Archives and package formats
# (zip, whl, dmg, deb …) are fine: a lab shares software.
BLOCKED_EXTENSIONS = {
    ".exe", ".com", ".scr", ".pif", ".bat", ".cmd", ".msi", ".msix", ".appx",
    ".dll", ".cpl", ".sys", ".vbs", ".vbe", ".jse", ".wsf", ".wsh", ".hta",
    ".ps1", ".psm1", ".reg", ".lnk", ".jar", ".app",
}
# A stored extension is ``.`` + 1-12 lowercase letters/digits; a name with
# none (or an odd one) is stored as ``.bin``.
EXTENSION_RE = re.compile(r"^\.[a-z0-9]{1,12}$")
# Served inline (rendered by the browser on navigation); everything else gets
# ``Content-Disposition: attachment``. An SVG opened as a top-level document
# would run its inline <script> in this origin (stored XSS), so it downloads
# like html — <img>/<object> embedding still works, inline note images are
# unaffected. SANDBOXED_EXTENSIONS additionally get a sandboxing CSP in case
# a browser renders them anyway.
INLINE_EXTENSIONS = {".pdf", ".txt", ".md", *(e for e in IMAGE_MEDIA_TYPES if e != ".svg")}
SANDBOXED_EXTENSIONS = {".svg", ".html"}


def upload_media_type(ext: str) -> str | None:
    """Media type for a stored upload's extension (lowercase, with the dot),
    or None when Gamma never stores that kind of file (blocked, malformed).
    Unknown but well-formed extensions are ``application/octet-stream``."""
    if ext == ".pdf":
        return "application/pdf"
    if ext in BLOCKED_EXTENSIONS or not EXTENSION_RE.match(ext):
        return None
    return IMAGE_MEDIA_TYPES.get(ext) or FILE_MEDIA_TYPES.get(ext) or "application/octet-stream"


def upload_extension(name: str) -> str:
    """The stored extension for an uploaded file name: its last suffix,
    lowercased (``Data.CSV`` → ``.csv``; ``pkg.tar.gz`` → ``.gz``, the full
    name stays in the link text), ``.bin`` when there is none or it is
    malformed. Raises ValueError for BLOCKED_EXTENSIONS."""
    dot = name.rfind(".")
    ext = name[dot:].lower() if dot > 0 else ""
    if ext in BLOCKED_EXTENSIONS:
        raise ValueError(f"{ext} files are not accepted (executable)")
    return ext if EXTENSION_RE.match(ext) else ".bin"


# How a block names a stored file: ``/api/uploads/<name>`` anywhere in its
# content or properties (an image, a file chip, ``ink_url``, a page's
# ``source_url``), and a page's ``doc_id`` (its PDF, ``<doc_id>.pdf``). The
# one grammar the orphan bookkeeping (gamma/upload_gc.py), a mirror's file
# transfer and a backup's file list read references with. A name is the whole
# ``<stem>.<ext>`` run: what follows it ("….png." ending a sentence,
# "?ws=…", "#page=2") is not part of it.
UPLOAD_REF_RE = re.compile(r"/api/uploads/([0-9A-Za-z_-]+\.[0-9A-Za-z]{1,12})(?![0-9A-Za-z])")
_DOC_STEM_RE = re.compile(r"^[0-9A-Za-z_-]{1,128}$")


def upload_refs(content: str, props) -> set[str]:
    """The stored file names one block references. ``props`` is the
    properties dict or its stored JSON text."""
    raw = props if isinstance(props, str) else json.dumps(props or {})
    names = set(UPLOAD_REF_RE.findall(content or "")) if "/api/uploads/" in (content or "") else set()
    if "/api/uploads/" in raw:
        names.update(UPLOAD_REF_RE.findall(raw))
    if '"doc_id"' in raw:
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except ValueError:
                props = {}
        doc = props.get("doc_id") if isinstance(props, dict) else None
        if isinstance(doc, str) and _DOC_STEM_RE.match(doc):
            names.add(f"{doc}.pdf")
    return names


# Upload filenames are the content sha256 truncated to this many hex chars
# (long enough that collisions stay theoretical, short enough to read in logs).
DIGEST_CHARS = 24


def display_filename(name: str, fallback: str = "") -> str:
    """A browser-supplied upload name reduced to one display-only leaf.

    Directory pickers may put a relative path in the multipart filename on
    some browsers. Folder placement is carried separately, so neither POSIX
    nor Windows separators belong in a page title or original_filename.
    """
    raw = str(name or "").replace("\x00", "").strip().replace("\\", "/")
    leaf = raw.rsplit("/", 1)[-1].strip()
    return (leaf or fallback)[:500]


def attachment_disposition(filename: str) -> str:
    """A download's ``Content-Disposition``: an ASCII fallback name plus the
    UTF-8 one (RFC 6266), so a Chinese or accented title survives."""
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "") or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(filename)}"


def url_filename(url: str) -> str:
    """The display name a URL suggests for the file behind it: its last path
    segment, unquoted, without query or fragment — "" when the URL has no
    path (a bare host). Reduced through :func:`display_filename`."""
    parts = urllib.parse.urlsplit(str(url or "").strip())
    tail = urllib.parse.unquote(parts.path.rstrip("/").split("/")[-1]).strip()
    return display_filename(tail)


def is_pdf(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == b"%PDF"


def content_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:DIGEST_CHARS]


def matches_name(name: str, data: bytes) -> bool:
    """Whether ``data`` can be what the stored file ``name`` holds — for
    bytes that arrive under a name someone else chose (a mirror pulling from
    its origin). Upload names are the content hash, except a PDF's: it may
    hash the URL it was fetched from (the proxy cache, a clip), and a stored
    PDF keeps its name when its embedded annotations are stripped. So a PDF
    only has to be a PDF, and anything else with a digest-length stem has to
    hash to it."""
    stem, _, ext = name.rpartition(".")
    if ext == "pdf":
        return is_pdf(data)
    return len(stem) != DIGEST_CHARS or content_digest(data) == stem


def write_atomic(path: Path, data: bytes) -> None:
    """Store ``data`` as ``path`` all at once: written to a temporary file
    beside it (``.partial/`` in the same directory — the same filesystem, and
    no listing of stored files counts a directory), flushed to disk, then
    renamed over the name. Whatever stops a write half way (a full disk, a
    killed process) leaves nothing under the name — never a truncated file
    that the next upload of the same bytes would take for stored. Every
    writer of a stored file goes through here (``put``)."""
    partial = path.parent / ".partial"
    partial.mkdir(parents=True, exist_ok=True)
    tmp = partial / secrets.token_hex(8)
    try:
        # a plain exclusive open, not tempfile.mkstemp: the file gets the
        # umask's permissions like every stored file, not mkstemp's 0600
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _rename_over(tmp, path, len(data))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


REPLACE_ATTEMPTS = 10   # a rename over a file a reader holds open (Windows) is retried this often
REPLACE_RETRY_S = 0.05


def _rename_over(tmp: Path, path: Path, size: int) -> None:
    """``tmp`` — complete, flushed, ``size`` bytes — renamed over ``path``."""
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # Windows refuses a rename over a name another thread is renaming
            # into place at the same moment. The name is the content's hash,
            # so a stored copy of the same size already is these bytes.
            if path.is_file() and path.stat().st_size == size:
                os.unlink(tmp)
                return
            # A rewrite under the name (a PDF with its embedded annotations
            # stripped, routers/imports.py) can meet a reader that has the
            # file open for a moment (the manifest walk right after an
            # upload): Windows refuses that too, and the reader is quick.
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_RETRY_S)


def place_file(tmp: Path, path: Path) -> None:
    """Make the complete local file ``tmp`` the stored file ``path``:
    flushed to disk, then renamed over the name like write_atomic's temp
    file — ``tmp`` is one, in ``path``'s ``.partial/`` (an upload assembled
    in parts, gamma/upload_parts.py), so this is a rename and never a copy
    of the bytes. ``tmp`` is consumed whatever happens: renamed, or removed
    when the rename fails."""
    try:
        size = tmp.stat().st_size
        with open(tmp, "rb+") as f:
            os.fsync(f.fileno())
        _rename_over(tmp, path, size)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cut_short(ws: str, filename: str, size: int, head) -> bool:
    """Whether a stored copy whose size differs from the new data's ``size``
    is a write that stopped early (``head(n)``: the new data's first ``n``
    bytes). Only a PDF is ever rewritten under its name (embedded
    annotations stripped, routers/imports.py), so any other file of the
    wrong size is not what its name says; a PDF is cut short when its bytes
    are the beginning of the real ones."""
    if not filename.endswith(".pdf"):
        return True
    path = open_path(ws, filename)
    stored = path.read_bytes() if path else b""  # gone meanwhile: written again
    return len(stored) < size and head(len(stored)) == stored


def _store_with(ws: str, filename: str, nbytes: int, head, write) -> bool:
    """Store the workspace's upload ``filename`` (``nbytes`` bytes, their
    first ``n`` read by ``head(n)``, written by ``write()``) unless it is
    stored already; returns whether it was. A stored copy is re-dated
    (``touch``): the upload→reference window gets its grace again and an
    unreferenced file's retention starts over (gamma/upload_gc.py). A copy
    an earlier, non-atomic write left short is rewritten. The storage
    limits gate new bytes only (check_upload_allowed raises 413/507 past
    them)."""
    stored = size(ws, filename)
    if stored is not None and stored != nbytes and _cut_short(ws, filename, nbytes, head):
        log.warning(f"[uploads] {filename} in workspace {ws} held {stored} of {nbytes} bytes — rewritten")
        write()
        return True
    if stored is not None:
        from . import upload_gc  # local: upload_gc imports this module

        with upload_gc.guard(ws):  # never between the purge's check and its delete
            if touch(ws, filename):
                return True
            # purged a moment ago (gamma/upload_gc.py): store it again
    check_upload_allowed(ws, nbytes)
    write()
    return False


def _store(ws: str, filename: str, data: bytes) -> bool:
    """``_store_with`` for bytes in memory."""
    return _store_with(ws, filename, len(data), lambda n: data[:n], lambda: put(ws, filename, data))


def _head_of(path: Path, n: int) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def _store_path(ws: str, filename: str, path: Path, size: int) -> bool:
    """``_store_with`` for a complete file in ``partial_dir(ws)``, stored by
    ``put_path`` (a rename) and consumed whatever the outcome: a dedup hit
    or a failed put removes it."""
    try:
        return _store_with(ws, filename, size, lambda n: _head_of(path, n),
                           lambda: put_path(ws, filename, path))
    finally:
        path.unlink(missing_ok=True)


def put_upload(ws: str, name: str, data: bytes) -> None:
    """Store ``data`` as the workspace's file ``name`` as it is, written
    whole: no hashing, no dedup, no quota. For the writers that store bytes
    under a name chosen elsewhere and check them themselves — the PDF
    proxy's cache and a clip (``can_store``), a mirror's pull
    (``matches_name``), a restore, a PDF stripped of its annotations, the
    AI chat's re-download."""
    put(ws, name, data)


def pdf_url(doc_id: str) -> str:
    """Where the stored PDF ``doc_id`` is served: what a page carrying it
    shows unless it stores a ``source_url`` of its own
    (blocks_store.page_attachment)."""
    return f"/api/uploads/{doc_id}.pdf"


def store_pdf(ws: str, data: bytes) -> tuple[str, bool]:
    """Store PDF bytes under their content hash in the workspace (callers
    validate with :func:`is_pdf` first). Returns ``(doc_id,
    already_existed)``; the file is at ``pdf_url(doc_id)``. Dedup first: a
    re-upload of a stored file adds no bytes."""
    doc_id = content_digest(data)
    already_existed = _store(ws, f"{doc_id}.pdf", data)
    pdf_meta.schedule(ws, doc_id)  # the viewer's manifest, ready before the first open
    return doc_id, already_existed


def store_pdf_path(ws: str, path: Path, size: int, doc_id: str) -> tuple[str, bool]:
    """:func:`store_pdf` for a PDF assembled on disk — an upload in parts
    (gamma/upload_parts.py): ``path``, in ``partial_dir(ws)``, holds
    ``size`` bytes whose content digest is ``doc_id`` (the caller hashed
    them as they arrived and checked the PDF header), so storing it is a
    rename, never a copy. ``path`` is consumed either way."""
    already_existed = _store_path(ws, f"{doc_id}.pdf", path, size)
    pdf_meta.schedule(ws, doc_id)
    return doc_id, already_existed


SPOOL_CHUNK = 1 << 20
SPOOL_CHECK_BYTES = 8 << 20  # how often a spool asks the limits whether to go on


class Spool:
    """A file arriving in chunks, written into ``partial_dir(ws)`` and
    hashed as it comes, so that storing it is ``put_path``'s rename and
    the whole file is never in memory (a scanned book is hundreds of
    MB). ``head`` is its first four bytes
    (``is_pdf``), ``size`` the bytes so far, ``digest`` the running
    SHA-256. ``discard`` removes the file; a spool that is not stored must
    be discarded."""

    def __init__(self, ws: str):
        directory = partial_dir(ws)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / secrets.token_hex(8)
        self._f = open(self.path, "xb")  # the umask's permissions, like every stored file
        self.size = 0
        self.digest = hashlib.sha256()
        self.head = b""

    def write(self, chunk: bytes) -> None:
        self._f.write(chunk)
        self.digest.update(chunk)
        self.size += len(chunk)
        if len(self.head) < 4:
            self.head += bytes(chunk[:4 - len(self.head)])

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()

    def discard(self) -> None:
        self.close()
        self.path.unlink(missing_ok=True)

    def doc_id(self) -> str:
        return self.digest.hexdigest()[:DIGEST_CHARS]


def store_pdf_stream(ws: str, stream) -> tuple[str, bool, int]:
    """:func:`store_pdf` for a PDF read from ``stream`` (``.read(n)``: an
    upload's spooled body): spooled to disk and hashed as it is read, then
    stored by a rename. ``(doc_id, already_existed, size)``. The limits are
    asked every SPOOL_CHECK_BYTES while the bytes arrive, so a file past
    the per-file cap is refused (413 / 507, ``check_upload_allowed``) a few
    MB in, not after it was written whole; a body that is no PDF is a
    ValueError. Nothing is left on disk either way."""
    spool = Spool(ws)
    try:
        checked = 0
        while True:
            chunk = stream.read(SPOOL_CHUNK)
            if not chunk:
                break
            spool.write(chunk)
            if spool.size - checked >= SPOOL_CHECK_BYTES:
                checked = spool.size
                check_upload_allowed(ws, spool.size)
        spool.close()
        if not is_pdf(spool.head):
            raise ValueError("not a valid PDF (missing %PDF header)")
    except BaseException:
        spool.discard()
        raise
    doc_id, already_existed = store_pdf_path(ws, spool.path, spool.size, spool.doc_id())
    return doc_id, already_existed, spool.size


def store_file(ws: str, data: bytes, ext: str) -> tuple[str, bool]:
    """Store any upload under its content hash as ``<sha24><ext>`` (``ext``
    lowercase with the dot, already validated by the caller). Returns
    ``(filename, already_existed)``."""
    filename = f"{content_digest(data)}{ext}"
    return filename, _store(ws, filename, data)


def find_upload_file(filename: str, ws: str) -> Path | None:
    """The workspace's stored file ``filename``, its path in the uploads
    directory, or None (no such file, or a name that cannot be one).

    Deliberately scoped to the single named workspace — the caller resolves
    which (the session's workspace or a validated share's). No cross-workspace
    fallback: that would let anyone read any file by guessing a content hash.
    """
    if not ws:
        return None
    try:
        return open_path(ws, filename)
    except ValueError:
        return None


# --- the store: the workspace's uploads/ directory -----------------------------------

USAGE_TTL_S = 60  # a workspace's stored bytes are listed again after this long

_usage_lock = threading.Lock()
_usage: dict = {}  # ws -> [monotonic expiry, the uploads directory's mtime_ns, bytes]


def check_name(name: str) -> str:
    """``name`` when it can name a stored file: one path segment, at most
    255 characters, not a dot file (``.partial/`` lives beside the files).
    ValueError otherwise."""
    if (not isinstance(name, str) or not name or len(name) > 255 or name.startswith(".")
            or any(c in name for c in "/\\\0")):
        raise ValueError(f"unsafe stored file name: {name!r}")
    return name


def _path(ws: str, name: str) -> Path:
    return ws_uploads_dir(ws) / check_name(name)


def _stamp(ws: str) -> int | None:
    try:
        return ws_uploads_dir(ws).stat().st_mtime_ns
    except FileNotFoundError:
        return None


def _adjust(ws: str, before: int | None, delta: int) -> None:
    """After a call here changed the uploads directory, which was dated
    ``before``: the cached usage follows, dated by the directory as it is
    now; one the directory changed under since is dropped."""
    with _usage_lock:
        hit = _usage.get(ws)
        if hit and hit[1] == before:
            hit[1], hit[2] = _stamp(ws), hit[2] + delta
        elif hit:
            del _usage[ws]


def _files(folder: Path):
    """``(name, stat)`` of each regular file in ``folder``, none when it is
    missing; the dot files (``.partial/``) are left out."""
    if not folder.is_dir():
        return
    for f in folder.iterdir():
        if f.name.startswith("."):
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        if S_ISREG(st.st_mode):
            yield f.name, st


def put(ws: str, name: str, data: bytes) -> None:
    """Store ``data`` as ``name``, whole (``write_atomic``): an existing
    file of the name is replaced at once, never left half written."""
    stamp, before = _stamp(ws), size(ws, name) or 0
    write_atomic(_path(ws, name), data)
    _adjust(ws, stamp, len(data) - before)


def exists(ws: str, name: str) -> bool:
    return _path(ws, name).is_file()


def size(ws: str, name: str) -> int | None:
    """The stored file's bytes, None when there is none."""
    try:
        st = _path(ws, name).stat()
    except FileNotFoundError:
        return None
    return st.st_size if S_ISREG(st.st_mode) else None


def stat(ws: str, name: str) -> tuple[int, float] | None:
    """``(size, mtime)`` of the stored file, None when there is none: the
    purge's last look at one due file's date (gamma/upload_gc.py)."""
    try:
        st = _path(ws, name).stat()
    except FileNotFoundError:
        return None
    return (st.st_size, st.st_mtime) if S_ISREG(st.st_mode) else None


def open_path(ws: str, name: str) -> Path | None:
    """The stored file's path, None when there is none."""
    path = _path(ws, name)
    return path if path.is_file() else None


def delete(ws: str, name: str) -> None:
    """Remove the stored file; one already gone is no error."""
    stamp, before = _stamp(ws), size(ws, name)
    try:
        _path(ws, name).unlink()
    except FileNotFoundError:
        return
    _adjust(ws, stamp, -(before or 0))


def delete_workspace(ws: str) -> None:
    """Remove every stored file of the workspace (it is being deleted)."""
    shutil.rmtree(ws_uploads_dir(ws), ignore_errors=True)
    with _usage_lock:
        _usage.pop(ws, None)


def touch(ws: str, name: str) -> bool:
    """Date the stored file now (the upload GC's clocks start over);
    False when there is no such file."""
    try:
        os.utime(_path(ws, name))
    except FileNotFoundError:
        return False
    return True


def usage(ws: str) -> int:
    """Bytes the workspace's stored files take. Every quota check reads it
    (``server_settings``), an ink merge's under the workspace's write lock,
    so it must not walk a directory that grows with the library on every
    stored write: the directory is listed once and the total kept for
    USAGE_TTL_S while the directory's mtime stays what it was, and the
    calls here that write or delete adjust it. A file added or removed by
    anything else (a restore of a server backup, an admin's copy) moves
    the mtime and is counted at the next read, or after USAGE_TTL_S when
    it landed within the clock tick of one of those writes."""
    stamp = _stamp(ws)
    with _usage_lock:
        hit = _usage.get(ws)
        if hit and hit[0] > time.monotonic() and hit[1] == stamp:
            return hit[2]
    total = sum(nbytes for _, nbytes, _ in list(ws))
    with _usage_lock:
        _usage[ws] = [time.monotonic() + USAGE_TTL_S, stamp, total]
    return total


def partial_dir(ws: str) -> Path:
    """The uploads directory's ``.partial/``, where a write in progress
    lives (``write_atomic``) and an upload in parts is assembled
    (gamma/upload_parts.py) so that ``put_path`` is a rename. Made by the
    caller; what is left there goes with ``sweep_partial``."""
    return ws_uploads_dir(ws) / ".partial"


def put_path(ws: str, name: str, path: Path) -> None:
    """Store the complete file ``path``, lying in ``partial_dir(ws)``, as
    ``name``, consuming it: renamed into place (``place_file``). An
    existing file of the name is replaced whole, like ``put``'s."""
    path = Path(path)
    stamp, before = _stamp(ws), size(ws, name) or 0
    nbytes = path.stat().st_size
    place_file(path, _path(ws, name))
    _adjust(ws, stamp, nbytes - before)


def sweep_partial(ws: str, max_age_s: float) -> int:
    """Remove the workspace's temp files older than ``max_age_s`` (a write
    a killed process left, an upload in parts that never finished); how
    many went."""
    partial = partial_dir(ws)
    if not partial.is_dir():
        return 0
    cutoff, removed = time.time() - max_age_s, 0
    for f in partial.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def list(ws: str):  # noqa: A001 — last in the module: it shadows the builtin below it
    """``(name, size, mtime)`` of each of the workspace's stored files."""
    return [(name, st.st_size, st.st_mtime) for name, st in _files(ws_uploads_dir(ws))]
