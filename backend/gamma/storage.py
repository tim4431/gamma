"""Uploaded-file helpers: media types, content-hash storage (atomic,
verified writes), lookup, and the one grammar of a reference to a stored
file. What becomes of a file nothing references any more is
gamma/upload_gc.py."""

import hashlib
import json
import os
import re
import secrets
import urllib.parse
from pathlib import Path

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
    writer of a stored file goes through here."""
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
        try:
            os.replace(tmp, path)
        except PermissionError:
            # Windows refuses a rename over a name another thread is renaming
            # into place at the same moment. The name is the content's hash,
            # so a stored copy of the same size already is these bytes.
            if not (path.is_file() and path.stat().st_size == len(data)):
                raise
            os.unlink(tmp)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cut_short(path: Path, data: bytes) -> bool:
    """Whether a stored copy whose size differs from ``data`` is a write that
    stopped early. Only a PDF is ever rewritten under its name (embedded
    annotations stripped, routers/imports.py), so any other file of the
    wrong size is not what its name says; a PDF is cut short when its bytes
    are the beginning of the real ones."""
    if path.suffix != ".pdf":
        return True
    stored = path.read_bytes()
    return len(stored) < len(data) and data.startswith(stored)


def _store(ws: str, filename: str, data: bytes) -> bool:
    """Write ``data`` as the workspace's upload ``filename`` unless it is
    stored already; returns whether it was. A stored copy is re-dated
    (``os.utime``): the upload→reference window gets its grace again and an
    unreferenced file's retention starts over (gamma/upload_gc.py). A copy an
    earlier, non-atomic write left short is rewritten. The storage limits
    gate new bytes only (check_upload_allowed raises 413/507 past them)."""
    target = ws_uploads_dir(ws) / filename
    try:
        size = target.stat().st_size
    except FileNotFoundError:
        size = None
    if size is not None and size != len(data) and _cut_short(target, data):
        log.warning(f"[uploads] {filename} in workspace {ws} held {size} of {len(data)} bytes — rewritten")
        write_atomic(target, data)
        return True
    if size is not None:
        from . import upload_gc  # local: upload_gc imports this module

        with upload_gc.guard(ws):  # never between the purge's check and its delete
            try:
                os.utime(target)
                return True
            except FileNotFoundError:
                pass  # purged a moment ago (gamma/upload_gc.py): store it again
    check_upload_allowed(ws, len(data))
    write_atomic(target, data)
    return False


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


def store_file(ws: str, data: bytes, ext: str) -> tuple[str, bool]:
    """Store any upload under its content hash as ``<sha24><ext>`` (``ext``
    lowercase with the dot, already validated by the caller). Returns
    ``(filename, already_existed)``."""
    filename = f"{content_digest(data)}{ext}"
    return filename, _store(ws, filename, data)


def find_upload_file(filename: str, ws: str) -> Path | None:
    """The uploaded file `filename` in the workspace's uploads dir, or None.

    Deliberately scoped to the single named workspace — the caller resolves
    which (the session's workspace or a validated share's). No cross-workspace
    fallback: that would let anyone read any file by guessing a content hash.
    """
    if not ws:
        return None
    try:
        path = ws_uploads_dir(ws) / filename
    except ValueError:
        return None
    return path if path.is_file() else None
