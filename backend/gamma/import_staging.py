"""Short-lived import uploads shared by the review's preview and its import.

Tokens are bound to an account and workspace. No library content is written
until the import, which runs as a background job (``POST /api/jobs/import``,
routers/imports.py): the job holds a filesystem claim while it reads the
upload, keeps the report as its result and removes the upload once done.
Cancelled and expired uploads are removed.

An upload arrives in one request (``create``) or, when it is larger than a
proxy in front of the server lets one request carry, in parts
(``open_parts``, ``append``, ``finish``: gamma/upload_parts.py's protocol,
with the bytes held here). There is no size cap: an upload is refused
(507) only when it would leave the disk with less than MIN_FREE_BYTES free.
"""
import hashlib
import json
import re
import secrets
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

from . import config, upload_parts

TTL = 2 * 60 * 60  # since the review's last activity: a part, a claim
MIN_FREE_BYTES = 1 << 30
TOKEN_RE = re.compile(r"^[a-f0-9]{40}$")


def _root():
    instance = hashlib.sha256(str(config.DATA_DIR).encode()).hexdigest()[:16]
    root = Path(tempfile.gettempdir()) / f"gamma-import-reviews-{instance}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _remove(path):
    # Only random token directories immediately inside our private staging root.
    if path.parent == _root() and TOKEN_RE.fullmatch(path.name):
        shutil.rmtree(path, ignore_errors=True)


def _expired(path):
    return time.time() - path.stat().st_mtime > TTL


def _new(size, metadata):
    """A new review's directory for an upload of ``size`` bytes, its
    metadata written → ``(token, path)``. Expired reviews are swept first."""
    root = _root()
    for path in root.iterdir():
        try:
            if path.is_dir() and _expired(path):
                _remove(path)
        except FileNotFoundError:
            pass
    if shutil.disk_usage(root).free - size < MIN_FREE_BYTES:
        raise HTTPException(status_code=507, detail="the server's disk has no room for this upload")
    token = secrets.token_hex(20)
    path = root / token
    path.mkdir()
    (path / "metadata.json").write_text(json.dumps({**metadata, "created": time.time()}), encoding="utf-8")
    return token, path


def create(file, *, user_id, ws, source, folder, strip):
    """Stage an upload that came in one request (``file``, an UploadFile)."""
    token, path = _new(file.size or 0, {"user": user_id, "ws": ws, "source": source, "folder": folder,
                                        "strip": strip, "filename": file.filename})
    try:
        with (path / "upload").open("wb") as dest:
            shutil.copyfileobj(file.file, dest, 1024 * 1024)
    except Exception:
        _remove(path)
        raise
    return token


def open_parts(*, user_id, ws, source, folder, strip, filename, size):
    """Open an upload of ``size`` bytes that comes in parts → its token,
    the review's id once ``finish`` has run."""
    if size <= 0:
        raise HTTPException(status_code=400, detail="an upload needs a size")
    token, path = _new(size, {"user": user_id, "ws": ws, "source": source, "folder": folder,
                              "strip": strip, "filename": filename, "size": size, "pending": True})
    (path / "upload").touch()
    return token


def append(token, user_id, ws, offset, stream):
    """Add one part at byte ``offset``, which must be the bytes held so far
    (else ``upload_parts.OffsetMismatch``) → the bytes held now."""
    with claim(token, user_id, ws, pending=True) as (path, metadata):
        if not metadata.get("pending"):
            raise HTTPException(status_code=409, detail="this upload is complete")
        received = (path / "upload").stat().st_size
        if offset != received:
            raise upload_parts.OffsetMismatch(received, offset)
        return received + upload_parts.write_part(path / "upload", received, metadata["size"], stream)


def finish(token, user_id, ws):
    """Mark an upload in parts complete: 400 while bytes are missing."""
    with claim(token, user_id, ws, pending=True) as (path, metadata):
        received = (path / "upload").stat().st_size
        if received != metadata.get("size", received):
            raise HTTPException(status_code=400, detail=f"{received} of {metadata['size']} bytes received")
        metadata.pop("pending", None)
        (path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")


def get(token, user_id, ws, pending=False):
    """The review's directory and metadata; an upload still arriving in
    parts only with ``pending`` (409 otherwise)."""
    if not TOKEN_RE.fullmatch(token):
        raise HTTPException(status_code=404, detail="import review not found")
    path = _root() / token
    try:
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        expired = _expired(path)
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="import review not found")
    if metadata["user"] != user_id or metadata["ws"] != ws:
        raise HTTPException(status_code=404, detail="import review not found")
    if expired:
        raise HTTPException(status_code=410, detail="import review expired; choose the file again")
    if metadata.get("pending") and not pending:
        raise HTTPException(status_code=409, detail="this upload has not finished arriving")
    return path, metadata


@contextmanager
def claim(token, user_id, ws, pending=False):
    path, metadata = get(token, user_id, ws, pending)
    lock = path / "running"
    try:
        lock.mkdir()
    except FileExistsError:
        raise HTTPException(status_code=409, detail="this import is already running")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="import review not found")
    try:
        yield path, metadata
    finally:
        try:
            lock.rmdir()
        except FileNotFoundError:
            pass  # swept meanwhile: an import that outlasted TTL keeps reading its open file


def discard(token, user_id, ws):
    with claim(token, user_id, ws, pending=True) as (path, _):
        # Remove payload while claimed; the small token directory follows.
        for name in ("upload", "metadata.json"):
            (path / name).unlink(missing_ok=True)
    _remove(path)
