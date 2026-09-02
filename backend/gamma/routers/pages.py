"""Page-first endpoints (/api/pages*): create a page, attach or detach its
PDF. Stage 1 of docs/dev/block_centric.md — a page is a root block that may
CARRY a PDF; the PDF is an action on an existing page, not the way pages come
into being. (``POST /api/blocks/by-doc/{doc_id}`` remains the lookup-or-create
BY ATTACHMENT path for PDF ingest and the extension's dedup.)

All three are session-only (``require_user``): a share token never creates
pages or changes a page's attachment (page properties stay the owner's, same
rule as PUT /blocks/{id} under a share).
"""

import json
import sqlite3
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..auth import require_user
from ..blocks_store import BLOCK_COLUMNS, block_to_dict, create_page, page_attachment
from ..db import page_now, safe_doc_id, user_db_path, user_uploads_dir
from ..foldertags import clean_path
from ..storage import cleanup_orphan_uploads, display_filename
from .blocks import _purge_derived_data

router = APIRouter(prefix="/api", tags=["pages"])

ATTACHMENT_KEYS = ("doc_id", "source_url", "original_filename")


class PageCreate(BaseModel):
    title: str = ""
    folder: str = ""


class AttachRequest(BaseModel):
    doc_id: str = ""              # content hash of an uploaded PDF, or the URL hash a proxied one gets
    source_url: str = ""          # where the viewer loads it from (upload path or external URL)
    original_filename: str = ""   # display name; becomes the title while it is still automatic


def _load_page(conn, page_id: str):
    row = conn.execute(
        f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="page not found")
    if row[1] != "root":
        raise HTTPException(status_code=400, detail="not a page (only root blocks carry attachments)")
    return block_to_dict(row)


def _url_tail(url: str) -> str:
    tail = urllib.parse.unquote((url or "").split("?")[0].rstrip("/").split("/")[-1]).strip()
    return display_filename(tail)


@router.post("/pages")
async def create_page_endpoint(payload: PageCreate, request: Request):
    """A new text-only page: ``{title?, folder?}`` → the page's block dict.
    Title defaults to "Untitled"; ``folder`` (a path like ``a/b``) becomes
    ``properties.folder``."""
    user = require_user(request)
    props = {}
    folder = clean_path(payload.folder or "")
    if folder:
        props["folder"] = folder
    with sqlite3.connect(user_db_path(user, "pages.db")) as conn:
        return create_page(conn, payload.title, props)


@router.post("/pages/{page_id}/attachment")
async def attach_pdf(page_id: str, payload: AttachRequest, request: Request):
    """Attach a PDF to an existing page that has none. Body: ``doc_id``
    (validated shape only — like ``by-doc``, a URL-opened PDF's id is the URL
    hash and the file is fetched lazily by the proxy) and/or ``source_url``,
    plus an optional ``original_filename``. 409 when the page already carries
    an attachment, or when another page already carries this ``doc_id``
    (``{"detail", "page_id"}`` so the client can offer to open it). While the
    title is still automatic ("Untitled"/empty) it becomes the file name (or
    URL tail) and is marked ``auto_title`` for the metadata worker.
    Returns the updated block."""
    user = require_user(request)
    doc_id = (payload.doc_id or "").strip()
    source_url = (payload.source_url or "").strip()
    if doc_id:
        try:
            doc_id = safe_doc_id(doc_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid doc_id")
    if not doc_id and not source_url:
        raise HTTPException(status_code=400, detail="doc_id or source_url required")
    with sqlite3.connect(user_db_path(user, "pages.db")) as conn:
        page = _load_page(conn, page_id)
        props = dict(page["properties"])
        if page_attachment(props):
            raise HTTPException(status_code=409, detail="page already has an attachment")
        if doc_id:
            other = conn.execute(
                "SELECT id FROM unified_blocks WHERE parent_id = 'root' "
                "AND json_extract(properties, '$.doc_id') = ? AND id != ?",
                (doc_id, page_id)).fetchone()
            if other:
                return JSONResponse(status_code=409, content={
                    "detail": "attachment belongs to another page", "page_id": other[0]})
            props["doc_id"] = doc_id
        props["source_url"] = source_url or f"/api/uploads/{doc_id}.pdf"
        original = display_filename(payload.original_filename)
        if original:
            props["original_filename"] = original
        content = page["content"]
        if not content.strip() or content.strip() == "Untitled":
            # Same marker semantics as get_or_create_doc_page: metadata may
            # replace an automatic title, an explicit rename clears the marker.
            content = original or _url_tail(source_url) or doc_id or content
            props["auto_title"] = content
        now = page_now()
        conn.execute(
            "UPDATE unified_blocks SET content = ?, properties = ?, updated_at = ? WHERE id = ?",
            (content, json.dumps(props), now, page_id))
        conn.commit()
    return {**page, "content": content, "properties": props, "updated_at": now}


@router.delete("/pages/{page_id}/attachment")
async def detach_pdf(page_id: str, request: Request):
    """Remove the page's PDF attachment (``doc_id`` / ``source_url`` /
    ``original_filename``). Highlight blocks keep their ``pdf_position``;
    the file itself is deleted by the orphan sweep unless another page still
    references it. → ``{"ok", "block", "removed_uploads"}``."""
    user = require_user(request)
    with sqlite3.connect(user_db_path(user, "pages.db")) as conn:
        page = _load_page(conn, page_id)
        props = dict(page["properties"])
        if not page_attachment(props):
            raise HTTPException(status_code=404, detail="page has no attachment")
        for key in ATTACHMENT_KEYS:
            props.pop(key, None)
        now = page_now()
        conn.execute("UPDATE unified_blocks SET properties = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(props), now, page_id))
        conn.commit()
        removed = cleanup_orphan_uploads(conn, user_uploads_dir(user))
        _purge_derived_data(user, conn, [])
    return {"ok": True, "block": {**page, "properties": props, "updated_at": now},
            "removed_uploads": removed}
