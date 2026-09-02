"""PDF/image uploads (content-hash deduped) and upload serving."""

import sqlite3

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..auth import require_user, require_writer, share_grant
from ..blocks_store import fetch_subtree
from ..db import user_db_path, user_uploads_dir
from ..server_settings import check_upload_allowed, usage_bytes, user_limits
from ..storage import (
    ALLOWED_IMAGE_TYPES,
    IMAGE_EXTENSIONS,
    IMAGE_MEDIA_TYPES,
    content_digest,
    find_upload_file,
    is_pdf,
    store_pdf,
)

router = APIRouter(prefix="/api", tags=["uploads"])


@router.get("/quota")
async def get_quota(request: Request):
    """The session user's effective storage limits and current usage — feeds
    the client-side pre-upload size check and the Settings usage display.
    (Deliberately its own endpoint: limits/usage change on admin edits and
    uploads, /api/session only at login.)"""
    user = require_user(request)
    limits = user_limits(user)
    return {**limits, "used_bytes": usage_bytes(user)}


@router.post("/uploads")
async def upload_pdf(request: Request, file: UploadFile = File(...)):
    user = require_user(request)
    contents = await file.read()
    if not is_pdf(contents):
        raise HTTPException(status_code=400, detail="not a valid PDF (missing %PDF header)")
    doc_id, source_url, already_existed = store_pdf(user, contents)
    return {
        "doc_id": doc_id,
        "source_url": source_url,
        "size": len(contents),
        "already_existed": already_existed,
    }


@router.post("/upload-image")
async def upload_image(request: Request, file: UploadFile = File(...)):
    # Share editors' images land in the owner's uploads (and count against the
    # owner's quota) — they are referenced from the owner's page.
    user = require_writer(request)
    uploads = user_uploads_dir(user)
    uploads.mkdir(parents=True, exist_ok=True)
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail=f"unsupported image type: {file.content_type}")
    contents = await file.read()
    digest = content_digest(contents)
    ext = IMAGE_EXTENSIONS[file.content_type]
    target = uploads / f"{digest}{ext}"
    already_existed = target.exists()
    if not already_existed:
        check_upload_allowed(user, len(contents))
        target.write_bytes(contents)
    return {
        "url": f"/api/uploads/{digest}{ext}",
        "size": len(contents),
        "already_existed": already_existed,
    }


def _share_can_read_upload(user: str, scope_page_id: str, filename: str) -> bool:
    """A share link may read only its own page's PDF (``<doc_id>.pdf``) or a
    file the page's subtree references (embedded images)."""
    with sqlite3.connect(user_db_path(user, "pages.db")) as conn:
        doc = conn.execute(
            "SELECT json_extract(properties, '$.doc_id') FROM unified_blocks WHERE id = ?",
            (scope_page_id,),
        ).fetchone()
        if not doc:
            return False
        if doc[0] and filename == f"{doc[0]}.pdf":
            return True
        rows = fetch_subtree(conn, scope_page_id)
    needle = f"/api/uploads/{filename}"
    return any(needle in (r[3] or "") or needle in (r[4] or "") for r in rows)


@router.get("/uploads/{filename}")
async def serve_upload(filename: str, request: Request):
    # Sanitize: only allow [hex].ext pattern, no path traversal
    dot = filename.rfind(".")
    if dot < 0:
        raise HTTPException(status_code=400, detail="invalid filename")
    stem = filename[:dot]
    ext = filename[dot:].lower()
    if ext == ".pdf":
        media_type = "application/pdf"
    elif ext in IMAGE_MEDIA_TYPES:
        media_type = IMAGE_MEDIA_TYPES[ext]
    else:
        raise HTTPException(status_code=400, detail="unsupported file type")
    if not stem or not all(c in "0123456789abcdef" for c in stem):
        raise HTTPException(status_code=400, detail="invalid filename")

    # Resolve who may read this: the session user (their own dir), or a valid
    # ?share= token scoped to its one page. No bare ?user= access.
    user = request.state.user
    scope_page_id = None
    if request.query_params.get("share") or not user:
        grant = share_grant(request)
        if not grant:
            raise HTTPException(status_code=401)
        user, scope_page_id, _level = grant
    if scope_page_id is not None and not _share_can_read_upload(user, scope_page_id, filename):
        raise HTTPException(status_code=403, detail="not accessible via this share link")

    path = find_upload_file(filename, user)
    if not path:
        raise HTTPException(status_code=404, detail="not found")
    # Filenames are content hashes (or URL hashes the server only writes once),
    # so a given name can never serve different bytes — cache hard for a month.
    headers = {"Cache-Control": "public, max-age=2592000, immutable",
               "X-Content-Type-Options": "nosniff"}
    # An SVG opened as a top-level document runs its inline <script> in this
    # origin (stored XSS). Force a download on direct navigation and sandbox it
    # if a browser renders it anyway; <img>/<object> embedding still works, so
    # inline note images are unaffected.
    if ext == ".svg":
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return FileResponse(path, media_type=media_type, headers=headers)


