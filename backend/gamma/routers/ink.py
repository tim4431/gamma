"""Handwriting uploads: one ``gamma-ink`` file per ink group, content-hash
stored like any upload (docs/dev/handwriting.md)."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..auth import require_ws_writer
from ..ink import InkError, bounding_box, parse_ink, pdf_position
from ..storage import store_file

router = APIRouter(prefix="/api", tags=["ink"])


@router.post("/upload-ink")
async def upload_ink(request: Request):
    """Body: the ink file as JSON. Validated against the schema and its
    budgets, then stored as it came as ``uploads/<sha>.ink`` — the clients
    serialize a file one way (``serializeInk``, sorted keys), so identical
    strokes are identical bytes and dedup, and a client that names a file
    by its hash offline (the iPad's replica) names what the server stores.
    → ``{url, size, strokes, bbox, pdf_position, already_existed}``. A
    workspace editor or an edit share (the block writers' rule). Async only
    to read the body; the rest runs in the threadpool."""
    body = await request.body()
    return await run_in_threadpool(_store_ink, request, body)


def _store_ink(request: Request, body: bytes) -> dict:
    ws = require_ws_writer(request)
    try:
        ink = parse_ink(body)
    except InkError as e:
        raise HTTPException(status_code=400, detail=f"invalid ink file: {e}")
    filename, already_existed = store_file(ws, body, ".ink")
    return {
        "url": f"/api/uploads/{filename}",
        "size": len(body),
        "strokes": len(ink.strokes),
        "bbox": bounding_box(ink),
        "pdf_position": pdf_position(ink),
        "already_existed": already_existed,
    }
