"""Uploaded-file helpers: media types, content-hash storage, lookup, orphan
cleanup."""

import hashlib
from pathlib import Path

from .db import user_uploads_dir
from .server_settings import check_upload_allowed

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}
IMAGE_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg"}
IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}

# Upload filenames are the content sha256 truncated to this many hex chars
# (long enough that collisions stay theoretical, short enough to read in logs).
DIGEST_CHARS = 24


def is_pdf(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == b"%PDF"


def content_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:DIGEST_CHARS]


def store_pdf(user: str, data: bytes) -> tuple[str, str, bool]:
    """Store PDF bytes under their content hash (callers validate with
    :func:`is_pdf` first). Returns ``(doc_id, source_url, already_existed)``.
    Dedup first: a re-upload of a stored file adds no bytes, so the storage
    limits only gate genuinely new ones (check_upload_allowed raises 413/507
    past them)."""
    uploads = user_uploads_dir(user)
    uploads.mkdir(parents=True, exist_ok=True)
    doc_id = content_digest(data)
    target = uploads / f"{doc_id}.pdf"
    already_existed = target.exists()
    if not already_existed:
        check_upload_allowed(user, len(data))
        target.write_bytes(data)
    return doc_id, f"/api/uploads/{doc_id}.pdf", already_existed


def find_upload_file(filename: str, user: str) -> Path | None:
    """The uploaded file `filename` in `user`'s uploads dir, or None.

    Deliberately scoped to the single named user — the caller resolves who that
    is (session user or a validated share owner). No cross-user fallback: that
    let anyone read any account's files by guessing a content hash.
    """
    if not user:
        return None
    try:
        path = user_uploads_dir(user) / filename
    except ValueError:
        return None
    return path if path.is_file() else None


def cleanup_orphan_uploads(conn, uploads_dir: Path):
    """Delete files in uploads_dir that are no longer referenced by any block in conn."""
    if not uploads_dir.exists():
        return []
    removed = []
    for f in uploads_dir.iterdir():
        if not f.is_file():
            continue
        filename = f.name
        stem = f.stem
        ref = conn.execute(
            "SELECT 1 FROM unified_blocks "
            "WHERE json_extract(properties, '$.doc_id') = ? "
            "   OR content LIKE ? "
            "   OR properties LIKE ? "
            "LIMIT 1",
            (stem, f"%/api/uploads/{filename}%", f"%/api/uploads/{filename}%"),
        ).fetchone()
        if not ref:
            try:
                f.unlink()
                removed.append(filename)
            except OSError:
                pass
    return removed
