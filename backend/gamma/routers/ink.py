"""Handwriting uploads: one ``gamma-ink`` file per ink group, content-hash
stored like any upload (docs/dev/handwriting.md)."""

import json
import re

from fastapi import APIRouter, HTTPException, Request

from ..auth import actor_of, require_ws_writer, share_scope_page
from ..blocks_store import page_root_id
from ..db import connect_pages_db
from ..ink import MAX_BYTES, InkError, bounding_box, dumps, parse_ink, pdf_position
from ..ops import OpError, after_commit, apply_ops
from ..storage import content_digest, store_file

router = APIRouter(prefix="/api", tags=["ink"])


@router.put("/blocks/{block_id}/ink")
async def save_ink(block_id: str, request: Request):
    """Atomic open-format ink save using workspace permissions and the op log.

    expected_url is null for a new client-generated ID, otherwise the URL the
    edit started from. Exact lost-response retries succeed without a new op.
    Empty drawings keep their block, caption and children.
    """
    ws = require_ws_writer(request)
    scope = share_scope_page(request)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", block_id):
        raise HTTPException(400, "invalid block ID")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BYTES + 4096:
            raise HTTPException(413, "ink request too large")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or set(payload) != {"parent_id", "expected_url", "ink"}:
            raise ValueError("expected parent_id, expected_url and ink")
        parent, expected = payload["parent_id"], payload["expected_url"]
        if not isinstance(parent, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", parent):
            raise ValueError("invalid parent ID")
        if expected is not None and (not isinstance(expected, str) or len(expected) > 256):
            raise ValueError("invalid expected URL")
        ink = parse_ink(payload["ink"])
        if ink.space.kind != "pdf-page":
            raise ValueError("a PDF page is required")
        data = dumps(ink)
        if len(data) > MAX_BYTES:
            raise ValueError("ink file too large")
    except (ValueError, TypeError, InkError) as e:
        raise HTTPException(400, f"invalid ink save: {e}")
    with connect_pages_db(ws) as conn:
        conn.execute("BEGIN IMMEDIATE")
        page_id = page_root_id(conn, parent)
        if not page_id or parent == "root" or block_id == page_id:
            raise HTTPException(404, "parent page not found")
        if scope is not None and scope != page_id:
            raise HTTPException(403, "not accessible via this share link")
        row = conn.execute("SELECT parent_id, properties FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
        props = json.loads(row[1] or "{}") if row else {}
        url = f"/api/uploads/{content_digest(data)}.ink"
        if row:
            if row[0] != parent or "ink_url" not in props or props.get("pdf_page") != ink.space.page:
                raise HTTPException(409, "ink target changed; reopen the group")
            if props["ink_url"] == url:
                return {"id": block_id, "properties": props, "unchanged": True}
            if expected != props["ink_url"]:
                raise HTTPException(409, "handwriting changed elsewhere; your local draft has been kept")
        elif expected is not None:
            raise HTTPException(409, "handwriting was deleted; your local draft has been kept")
        filename, _ = store_file(ws, data, ".ink")
        properties = {"ink_url": f"/api/uploads/{filename}", "pdf_page": ink.space.page,
                      "ink_strokes": len(ink.strokes), "pdf_position": pdf_position(ink)}
        op = ({"op": "set", "id": block_id, "props": properties} if row else
              {"op": "insert", "id": block_id, "parent": parent, "content": "", "props": properties})
        try:
            result = apply_ops(conn, page_id, [op], actor=actor_of(request), share_scoped=scope is not None)
            after_commit(ws, conn, result)
        except OpError as e:
            raise HTTPException(e.status, e.detail)
        return {"id": block_id, "properties": {**props, **properties}, "seq": result["seq"]}


@router.post("/upload-ink")
async def upload_ink(request: Request):
    """Body: the ink file as JSON. Validated against the schema and its
    budgets, stored canonically (sorted keys, so identical strokes dedup)
    as ``uploads/<sha>.ink``. → ``{url, size, strokes, pdf_position,
    already_existed}``; the client puts ``url`` and ``pdf_position`` on the
    block. A workspace editor or an edit share (the block writers' rule)."""
    ws = require_ws_writer(request)
    try:
        ink = parse_ink(await request.body())
    except InkError as e:
        raise HTTPException(status_code=400, detail=f"invalid ink file: {e}")
    data = dumps(ink)
    filename, already_existed = store_file(ws, data, ".ink")
    return {
        "url": f"/api/uploads/{filename}",
        "size": len(data),
        "strokes": len(ink.strokes),
        "bbox": bounding_box(ink),
        "pdf_position": pdf_position(ink),
        "already_existed": already_existed,
    }
