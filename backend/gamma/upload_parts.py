"""An upload in parts (``POST /api/uploads/parts``, routers/uploads.py): a
PDF too big for one request — a proxy in front of the server caps a
request's body (Cloudflare at 100 MB on its Free and Pro plans) — comes as
the parts the client cuts it into, one request each, appended to one file
in the store's partial directory (``storage.partial_dir``) and hashed as they
land, and is stored whole at the end by a rename (``storage.store_pdf_path``),
never a copy, so finishing takes no longer than a small upload does.

A session is the server's side of one such upload: its token, the bytes
it holds, the running digest. The size and quota checks run when it is
opened, before a byte travels. Sessions live in this process (the server
runs as one); a restart forgets them, the client starts over, and the
files they left go with the partial sweep (gamma/upload_gc.py, after a
day). One nothing touched for IDLE_S is dropped, its file with it, at the
next open. Docs: docs/dev/user_db.md "Stored files"."""

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException

from . import storage
from .server_settings import check_upload_allowed

MB = 1024 * 1024
PART_BYTES = 32 * MB      # what the open reply tells a client to send per part
PART_BYTES_MAX = 64 * MB  # the most one part may hold (413 past it)
IDLE_S = 3600             # a session nothing touched this long is dropped
MAX_PER_WS = 8            # sessions open at once per workspace (429 past it)
_CHUNK = 1 << 20


class OffsetMismatch(Exception):
    """A part offered at an offset other than the bytes held so far:
    ``received`` says where the client should go on from."""

    def __init__(self, received: int, offset: int):
        super().__init__(f"the upload holds {received} bytes, not {offset}")
        self.received = received


@dataclass
class Session:
    token: str
    ws: str
    size: int
    name: str
    path: Path
    received: int = 0
    digest: "hashlib._Hash" = field(default_factory=hashlib.sha256)
    touched: float = field(default_factory=time.monotonic)
    closed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


_sessions: dict[str, Session] = {}
_lock = threading.Lock()


def start(ws: str, size: int, name: str = "") -> Session:
    """Open an upload of ``size`` bytes into the workspace: the per-file
    limit and the quota (413 / 507, ``check_upload_allowed``) are checked
    here, before a byte travels. 400 for no size at all, 429 past
    MAX_PER_WS open sessions in the workspace."""
    if size <= 0:
        raise HTTPException(status_code=400, detail="an upload needs a size")
    check_upload_allowed(ws, size)
    stale = []
    with _lock:
        now = time.monotonic()
        for token, s in list(_sessions.items()):
            if now - s.touched > IDLE_S:
                stale.append(_sessions.pop(token))
        if sum(1 for s in _sessions.values() if s.ws == ws) >= MAX_PER_WS:
            raise HTTPException(status_code=429,
                                detail=f"{MAX_PER_WS} uploads in parts are in progress in this workspace already")
        partial = storage.partial_dir(ws)
        partial.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(24)
        path = partial / f"parts-{token}"
        with open(path, "xb"):
            pass
        session = Session(token=token, ws=ws, size=size, name=name, path=path)
        _sessions[token] = session
    for s in stale:
        _close(s)
    return session


def get(ws: str, token: str) -> Session:
    """The workspace's open session ``token``; 404 for any other."""
    with _lock:
        session = _sessions.get(token)
    if session is None or session.ws != ws or session.closed:
        raise HTTPException(status_code=404, detail="no such upload in progress")
    return session


def append(session: Session, offset: int, stream) -> int:
    """Add the part ``stream`` holds, offered at byte ``offset``, which must
    be the bytes held so far (else OffsetMismatch, telling where they
    stand). Read in chunks, written to the session's file and hashed as it
    goes; a part past PART_BYTES_MAX or past the announced size is refused
    (413) and leaves the file as it was. Returns the bytes held now."""
    with session.lock:
        if session.closed:
            raise HTTPException(status_code=404, detail="no such upload in progress")
        if offset != session.received:
            raise OffsetMismatch(session.received, offset)
        room = min(PART_BYTES_MAX, session.size - session.received)
        digest, written = session.digest.copy(), 0
        with open(session.path, "r+b") as f:
            f.seek(session.received)
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                if written + len(chunk) > room:
                    f.truncate(session.received)
                    raise HTTPException(status_code=413, detail=(
                        f"a part holds at most {PART_BYTES_MAX // MB} MB and never more than the announced size"))
                f.write(chunk)
                digest.update(chunk)
                written += len(chunk)
        if not written:
            raise HTTPException(status_code=400, detail="an empty part")
        session.digest, session.received = digest, session.received + written
        session.touched = time.monotonic()
        return session.received


def finish(session: Session) -> tuple[str, bool]:
    """Store the assembled file as a PDF (``storage.store_pdf_path``) and
    close the session: ``(doc_id, already_existed)`` like ``store_pdf``.
    400 while bytes are missing (the session stays open) or when the file
    is no PDF (the session is dropped, its file with it)."""
    with session.lock:
        if session.closed:
            raise HTTPException(status_code=404, detail="no such upload in progress")
        if session.received != session.size:
            raise HTTPException(status_code=400,
                                detail=f"{session.received} of {session.size} bytes received")
        _forget(session)
        with open(session.path, "rb") as f:
            head = f.read(4)
        if not storage.is_pdf(head):
            session.path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="not a valid PDF (missing %PDF header)")
        doc_id = session.digest.hexdigest()[:storage.DIGEST_CHARS]
        return storage.store_pdf_path(session.ws, session.path, session.size, doc_id)


def discard(session: Session) -> None:
    """Drop the session and the bytes it holds."""
    with session.lock:
        _forget(session)
        session.path.unlink(missing_ok=True)


def _forget(session: Session) -> None:
    session.closed = True
    with _lock:
        _sessions.pop(session.token, None)


def _close(session: Session) -> None:
    with session.lock:
        session.closed = True
        session.path.unlink(missing_ok=True)


def open_count(ws: str | None = None) -> int:
    """How many sessions are open (in ``ws``, or in all); tests."""
    with _lock:
        return sum(1 for s in _sessions.values() if ws is None or s.ws == ws)
