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
stored by a rename) and ``sweep_partial`` (dead temp files). A module that
reads stored files as it works, an exporter or a merge, is handed them as
one callable, ``name -> Path | None``, and builds no path of its own:
``files(ws)``. Every read and write of a stored file goes through these;
only what works on the directory whole goes past them:
a server backup (gamma/backups.py), the off-site copy (gamma/offsite.py) and
a migration over the directory beside a pages.db (gamma/migrations.py).

On the desktop app's own server a name may instead be a **linked file**, a
PDF read where it is on this computer (gamma/linked_files.py). ``open_path``
and what reads through it find one (``linked_path``), and ``readable`` asks
"can this be read at all"; ``exists``, ``size``, ``stat``, ``touch`` and
``list`` stay "stored here", which is what keeps a linked file out of the
quota, the orphan purge, backups, the off-site copy and a mirror.
``linked_stat`` is the one look at such a file, remembered for a moment.

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

from . import config, pdf_meta
from .db import connect_vaults_db, ws_uploads_dir
from .documents.formats import EXTENSION_RE
from .documents.model import page_document, stored_name
from .logbuf import log
from .server_settings import check_upload_allowed
from .work_folders.library import work_folder_of


# How a block names a stored file: ``/api/uploads/<name>`` anywhere in its
# content or properties (an image, a file chip, ``ink_url``, a document's
# address), a block's ``doc`` (``stored_name(hash, ext)``: the file it
# stores, reads in place, caches or shows as a copy), and a
# highlight's ``pdf_position.doc_id`` and ``pdf_position.prev.doc_id``: the
# file its rectangles were measured on, which a relocation run reads them
# from, and the one a dashed placement's review card shows as Before
# (docs/dev/highlight_relocation.md). So a pending highlight holds its file
# until Accept, Use closest match, a move or a run that places it solid
# drops the name. The one grammar the orphan bookkeeping (gamma/upload_gc.py),
# a mirror's file transfer, a .gamma export and a backup's file list read
# references with. The highlights' names are **held** files, kept but not
# shown: what travels with a page to another copy (a mirror, an export
# through a share, which serves only what the pages show, a publication)
# leaves them out (``held=False``), and so the document of a work folder's
# file read in place that is no library document: a build on this computer,
# whose bytes are stored only while a comment thread on it is open
# (gamma/feedback/kept.py), never leaves it, where a library file's stored
# versions do. A name is the whole ``<stem>.<ext>`` run: what follows it
# ("….png." ending a sentence, "?ws=…", "#page=2") is not part of it.
UPLOAD_REF_RE = re.compile(r"/api/uploads/([0-9A-Za-z_-]+\.[0-9A-Za-z]{1,12})(?![0-9A-Za-z])")
_DOC_STEM_RE = re.compile(r"^[0-9A-Za-z_-]{1,128}\Z")
# The doc_id a PDF replacement (routers/pages.py) writes on the highlights
# of a page that showed its file from an address alone (a doc at ``url``
# without a hash; a copy's file is its stored file): no stored file to read
# them from, and no upload has this name (uploads are named by content
# hash), so it is no reference, and the relocation run places them by their
# quotes, as for a stored file that is gone (gamma/highlight_relocate.py).
UNSTORED = "unstored"


def upload_refs(content: str, props, *, held: bool = True) -> set[str]:
    """The stored file names one block references. ``props`` is the
    properties dict or its stored JSON text. ``held=False`` leaves out the
    files a highlight's position holds (``_position_ids``), the stored name
    of a ``doc`` that does not check (``_unread_doc_file``) and the document
    of a work folder's build (``_work_build``): only what the block shows
    and may travel with it."""
    raw = props if isinstance(props, str) else json.dumps(props or {})
    names = set(UPLOAD_REF_RE.findall(content or "")) if "/api/uploads/" in (content or "") else set()
    if "/api/uploads/" in raw:
        names.update(UPLOAD_REF_RE.findall(raw))
    if '"doc"' in raw or '"doc_id"' in raw:
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except ValueError:
                props = {}
        if isinstance(props, dict):
            view = page_document(props)
            if view and _DOC_STEM_RE.match(view["hash"]) and (held or not _work_build(props, view)):
                names.add(view["file"])
            elif view is None and held:
                names.update(_unread_doc_file(props.get("doc")))
            names.update(stored_name(doc, ".pdf") for doc in _position_ids(props, held))
    return names


def _work_build(props: dict, view: dict) -> bool:
    """Whether the block is a work folder's file read in place that is no
    library document (gamma/work_folders/library.py): its document is a
    build on this computer, held while it is stored but never sent."""
    return bool(work_folder_of(props)) and props.get("library") is not True and view["at"] == "path"


def _unread_doc_file(doc):
    """The stored name a ``doc`` that does not check still names (its
    ``hash`` and ``ext``, ``.pdf`` when it has none, as a stored file's):
    held, so a doc no reader can read (a raw writer's, or one with a field
    a later release adds) never lets its file go."""
    if not isinstance(doc, dict):
        return
    hash, ext = doc.get("hash"), doc.get("ext") or ".pdf"
    if isinstance(hash, str) and _DOC_STEM_RE.match(hash) and isinstance(ext, str) and EXTENSION_RE.match(ext):
        yield stored_name(hash, ext)


def _position_ids(props: dict, held: bool = True):
    """The stored PDFs a highlight's position holds (``held``): its
    ``pdf_position.doc_id`` and ``pdf_position.prev.doc_id``. Malformed ids
    and ``UNSTORED`` name none."""
    position = props.get("pdf_position") if held else None
    position = position if isinstance(position, dict) else {}
    prev = position.get("prev")
    prev = prev if isinstance(prev, dict) else {}
    for doc in (position.get("doc_id"), prev.get("doc_id")):
        if stored_pdf_stem(doc):
            yield doc


def stored_pdf_stem(value) -> str:
    """``value`` when it names a stored PDF by its stem (a highlight's
    ``pdf_position.doc_id``, a Zotero conflict's kept file), else "":
    malformed ones and ``UNSTORED`` name none."""
    return value if isinstance(value, str) and value != UNSTORED and _DOC_STEM_RE.match(value) else ""


# Upload filenames are the content sha256 truncated to this many hex chars
# (long enough that collisions stay theoretical, short enough to read in logs).
DIGEST_CHARS = 24


def display_filename(name: str, fallback: str = "") -> str:
    """A browser-supplied upload name reduced to one display-only leaf.

    Directory pickers may put a relative path in the multipart filename on
    some browsers. Folder placement is carried separately, so neither POSIX
    nor Windows separators belong in a page title or a document's name.
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


# The containers MediaRecorder writes (and their Ogg kin), stored under these
# extensions only when they are that container (``is_audio``).
RECORDING_EXTENSIONS = (".webm", ".m4a", ".ogg", ".opus")


def is_audio(ext: str, data: bytes) -> bool:
    """Whether ``data`` opens as the audio container its extension names,
    by the container's magic bytes: EBML for WebM, an ISO ``ftyp`` box for
    M4A, ``OggS`` for Ogg and Opus, an ID3 tag or an MPEG frame sync for
    MP3, ``RIFF…WAVE`` for WAV. Another extension is no audio."""
    if ext == ".webm":
        return data[:4] == b"\x1a\x45\xdf\xa3"
    if ext == ".m4a":
        return data[4:8] == b"ftyp"
    if ext in (".ogg", ".opus"):
        return data[:4] == b"OggS"
    if ext == ".mp3":
        return data[:3] == b"ID3" or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
    if ext == ".wav":
        return data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    return False


def content_refusal(ext: str, head: bytes) -> str:
    """Why a file whose first bytes are ``head`` (16 are enough) may not be
    stored under ``ext``, "" when it may: a ``.pdf`` must be a PDF (it lands
    under the name the PDF ingest mints), a recording's extension the audio
    container it names (it is served inline to an <audio> element). Every
    writer of an arbitrary file asks: ``POST /upload-file``, an upload in
    parts, the Zotero import's attachments."""
    if ext == ".pdf" and not is_pdf(head):
        return "not a valid PDF (missing %PDF header)"
    if ext in RECORDING_EXTENSIONS and not is_audio(ext, head):
        return "not a valid audio file"
    return ""


def digest_id(digest) -> str:
    """The stored name's stem of a SHA-256 ``digest`` (``hashlib``): its
    first DIGEST_CHARS hex characters."""
    return digest.hexdigest()[:DIGEST_CHARS]


def content_digest(data: bytes) -> str:
    return digest_id(hashlib.sha256(data))


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
    quota gates new bytes only (check_upload_allowed raises 507 past
    it)."""
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
    under a name chosen elsewhere and check them themselves — a clip
    (``can_store``), a mirror's pull (``matches_name``), a restore, a PDF
    stripped of its annotations, the AI chat's re-download. The PDF proxy's
    cache, spooled to disk, goes through :func:`put_path` instead."""
    put(ws, name, data)


def store_pdf(ws: str, data: bytes) -> tuple[str, bool]:
    """Store PDF bytes under their content hash in the workspace (callers
    validate with :func:`is_pdf` first). Returns ``(doc_id,
    already_existed)``; the file is ``stored_name(doc_id, ".pdf")``. Dedup
    first: a re-upload of a stored file adds no bytes."""
    doc_id = content_digest(data)
    already_existed = _store(ws, stored_name(doc_id, ".pdf"), data)
    pdf_meta.schedule(ws, doc_id)  # the viewer's manifest, ready before the first open
    return doc_id, already_existed


def store_pdf_path(ws: str, path: Path, size: int, doc_id: str) -> tuple[str, bool]:
    """:func:`store_pdf` for a PDF assembled on disk — an upload in parts
    (gamma/upload_parts.py): ``path``, in ``partial_dir(ws)``, holds
    ``size`` bytes whose content digest is ``doc_id`` (the caller hashed
    them as they arrived and checked the PDF header), so storing it is a
    rename, never a copy. ``path`` is consumed either way."""
    already_existed = _store_path(ws, stored_name(doc_id, ".pdf"), path, size)
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
    SHA-256. ``due_check`` says when the storage limits are to be asked
    again (every SPOOL_CHECK_BYTES); the caller refuses or stops caching.
    ``discard`` removes the file; a spool that is not stored must be
    discarded."""

    def __init__(self, ws: str):
        directory = partial_dir(ws)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / secrets.token_hex(8)
        self._f = open(self.path, "xb")  # the umask's permissions, like every stored file
        self.size = 0
        self.digest = hashlib.sha256()
        self.head = b""
        self._checked = 0  # the size at the last due check

    def write(self, chunk: bytes) -> None:
        self._f.write(chunk)
        self.digest.update(chunk)
        self.size += len(chunk)
        if len(self.head) < 4:
            self.head += bytes(chunk[:4 - len(self.head)])

    def due_check(self) -> bool:
        """Whether SPOOL_CHECK_BYTES arrived since the last check."""
        if self.size - self._checked < SPOOL_CHECK_BYTES:
            return False
        self._checked = self.size
        return True

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()

    def discard(self) -> None:
        self.close()
        self.path.unlink(missing_ok=True)

    def doc_id(self) -> str:
        return digest_id(self.digest)


def _spool_stream(ws: str, stream) -> Spool:
    """``stream`` (``.read(n)``) spooled into ``partial_dir(ws)`` and hashed
    as it is read, the quota asked every SPOOL_CHECK_BYTES
    (``check_upload_allowed``: 507), so a file past it is refused a few MB
    in, not after it was written whole. The spool is
    discarded on any exception; the caller stores or discards the one it
    gets."""
    spool = Spool(ws)
    try:
        while chunk := stream.read(SPOOL_CHUNK):
            spool.write(chunk)
            if spool.due_check():
                check_upload_allowed(ws, spool.size)
        spool.close()
    except BaseException:
        spool.discard()
        raise
    return spool


def store_pdf_stream(ws: str, stream) -> tuple[str, bool, int]:
    """:func:`store_pdf` for a PDF read from ``stream`` (``.read(n)``: an
    upload's spooled body): spooled to disk and hashed as it is read
    (``_spool_stream``, which asks the limits as the bytes arrive), then
    stored by a rename. ``(doc_id, already_existed, size)``; a body that is
    no PDF is a ValueError. Nothing is left on disk either way."""
    spool = _spool_stream(ws, stream)
    if not is_pdf(spool.head):
        spool.discard()
        raise ValueError("not a valid PDF (missing %PDF header)")
    doc_id, already_existed = store_pdf_path(ws, spool.path, spool.size, spool.doc_id())
    return doc_id, already_existed, spool.size


def store_file(ws: str, data: bytes, ext: str) -> tuple[str, bool]:
    """Store any upload under its content hash as ``<sha24><ext>`` (``ext``
    lowercase with the dot, already validated by the caller). Returns
    ``(filename, already_existed)``."""
    filename = stored_name(content_digest(data), ext)
    return filename, _store(ws, filename, data)


def store_file_path(ws: str, path: Path, size: int, digest: str, ext: str) -> tuple[str, bool]:
    """:func:`store_file` for a file assembled on disk (an upload in parts,
    gamma/upload_parts.py) whose content digest is ``digest``: stored as
    ``<digest><ext>`` by a rename; ``path`` is consumed either way."""
    filename = stored_name(digest, ext)
    return filename, _store_path(ws, filename, path, size)


def store_file_stream(ws: str, stream, ext: str) -> tuple[str, bool]:
    """:func:`store_file` for a file read from ``stream`` (``.read(n)``: an
    upload's spooled body or a zip member). The file is spooled to disk and
    hashed as it is read (``Spool``), and the quota is asked every
    SPOOL_CHECK_BYTES (``check_upload_allowed``: 507). A file
    ``content_refusal`` refuses is a ValueError. The file is then stored by a rename
    (``store_file_path``) → ``(filename, already_existed)``. Nothing is
    left on disk either way."""
    spool = _spool_stream(ws, stream)
    try:
        with open(spool.path, "rb") as f:
            refused = content_refusal(ext, f.read(16))
        if refused:
            raise ValueError(refused)
    except BaseException:
        spool.discard()
        raise
    return store_file_path(ws, spool.path, spool.size, spool.doc_id(), ext)


def find_upload_file(filename: str, ws: str) -> Path | None:
    """The workspace's file ``filename``, its path in the uploads directory
    or a linked file's (``open_path``), or None (no such file, or a name that
    cannot be one).

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


def files(ws: str):
    """The workspace's stored files as a callable, ``name -> Path | None``
    (``find_upload_file``): what a module that reads them is handed in place
    of a directory, so the store answers for what a name reads."""
    return lambda name: find_upload_file(name, ws)


# --- the store: the workspace's uploads/ directory -----------------------------------

USAGE_TTL_S = 60  # a workspace's stored bytes are listed again after this long

_usage_lock = threading.Lock()
_usage: dict = {}  # ws -> [monotonic expiry, the uploads directory's mtime_ns, bytes]

# Called with ``(ws, name)`` when a read finds a linked file's stat moved. A
# listener only takes note: it runs inside the read. Each registers itself at
# import (gamma/linked_files.py).
linked_moved: list = []

LINKED_STAT_S = 1      # a linked file's stat is asked again after this long
OFFLINE_S = 30         # a disk that did not answer is asked again after this long
LINKED_STATS_MAX = 4096

_stats_lock = threading.Lock()
_stats: dict = {}      # path -> (expiry, its stat or None)
_offline: dict = {}    # root -> expiry
_clock = time.monotonic  # what both expiries are read against; tests drive it


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
    """The path ``name`` reads from: the stored file's, else a linked file's
    (``linked_path``), None when there is neither."""
    path = _path(ws, name)
    return path if path.is_file() else linked_path(ws, name)


def readable(ws: str, name: str) -> bool:
    """Whether ``name`` can be read at all, stored or linked; ``exists`` and
    the calls beside it say only what is stored here."""
    return find_upload_file(name, ws) is not None


def linked_path(ws: str, name: str) -> Path | None:
    """The file on this computer that holds ``name``, on the server that
    keeps links (gamma/linked_files.py): the first of its paths whose size
    and time are as recorded, so what is read is what was hashed, whether a
    copy is stored too or not (the disk is the version the page shows: a
    feedback thread's source line is read beside it, gamma/feedback). One
    whose stat moved reads as nothing and its listeners hear of it
    (``linked_moved``); one that is gone reads as nothing and is the page's
    missing file. Costs a query, and nothing where links are not kept. No
    hashing and no waiting here: ``open_path`` asks it on every read, HEAD
    and range request of a name not in the directory."""
    if not config.folder_links_enabled():
        return None
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT path, size, mtime_ns FROM linked_files WHERE ws = ? AND name = ?",
                            (ws, name)).fetchall()
    moved = False
    for path, recorded, mtime_ns in rows:
        st = linked_stat(path)
        if st is None:
            continue
        if (st.st_size, st.st_mtime_ns) == (recorded, mtime_ns):
            return Path(path)
        moved = True
    if moved:
        for fn in linked_moved:
            fn(ws, name)
    return None


def _root(path: str) -> str:
    """Where the disk of a path begins, for a whole disk gone: its drive or
    share, on a system with neither its first two directories (a mount
    point: ``/Volumes/Disk``, ``/mnt/nas``)."""
    parts = Path(path).parts
    return parts[0] if len(parts[0]) > 1 else str(Path(*parts[:-1][:3]))


def linked_stat(path: str) -> os.stat_result | None:
    """The stat of the regular file at ``path``, None when there is none, by
    at most one look per path per ``LINKED_STAT_S``: a viewer's range
    requests come by the dozen. A failed look asks the file's disk too
    (``_root``), and one that does not answer either is offline: every path
    on it answers None for ``OFFLINE_S`` with no look at all, for a dead
    share holds a look for many seconds, and a thread of the pool with it."""
    root = _root(path)
    with _stats_lock:
        now = _clock()
        if _offline.get(root, 0) > now:
            return None
        hit = _stats.get(path)
        if hit and hit[0] > now:
            return hit[1]
    st, offline = None, False
    try:
        st = os.stat(path)
    except OSError:
        offline = not os.path.exists(root)
    found = st if st is not None and S_ISREG(st.st_mode) else None
    with _stats_lock:
        now = _clock()
        if offline:
            _offline[root] = now + OFFLINE_S
        else:
            if len(_stats) >= LINKED_STATS_MAX:
                _stats.clear()
            _stats[path] = (now + LINKED_STAT_S, found)
    return found


def forget_linked_stat(path: str) -> None:
    """What ``linked_stat`` remembers of ``path`` and its disk: the file was
    just read, or its row changed, so the next look is a real one."""
    with _stats_lock:
        _stats.pop(path, None)
        _offline.pop(_root(path), None)


def is_linked(ws: str, name: str) -> bool:
    """Whether a linked file goes by ``name``, readable right now or not: for
    a caller that must tell "out of reach for a moment" from "no such file"."""
    if not config.folder_links_enabled():
        return False
    with connect_vaults_db() as conn:
        return conn.execute("SELECT 1 FROM linked_files WHERE ws = ? AND name = ? LIMIT 1",
                            (ws, name)).fetchone() is not None


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
