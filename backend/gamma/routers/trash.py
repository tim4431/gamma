"""Recently deleted (/api/trash*): the pages deleted in the last
``trash.KEEP_DAYS`` days (gamma/trash.py). Any member lists it; editors and
owners restore a page or delete it for good — the same people who may delete
a page. Share links never reach it."""

from fastapi import APIRouter, HTTPException, Request

from .. import trash
from ..auth import require_ws
from ..db import connect_pages_db
from ..ops import OpError, restore_page

router = APIRouter(prefix="/api", tags=["trash"])


@router.get("/trash")
def list_deleted(request: Request):
    """``{pages: [{id, title, folder, deleted_at, deleted_by, purge_at}],
    keep_days}``, the last deleted first."""
    ws = require_ws(request)
    with connect_pages_db(ws) as conn:
        return {"pages": trash.list_trash(conn), "keep_days": trash.KEEP_DAYS}


@router.post("/trash/{page_id}/restore")
def restore_deleted(page_id: str, request: Request):
    """Put a page back in the library, filed where it was → its block dict.
    404 when it is not in Recently deleted."""
    ws = require_ws(request, write=True)
    try:
        with connect_pages_db(ws) as conn:
            return restore_page(ws, conn, page_id)
    except OpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.delete("/trash/{page_id}")
def delete_forever(page_id: str, request: Request):
    """Delete one page of Recently deleted for good (``ops.delete_page``):
    its blocks, chats and indexes; the files nothing else references go to
    the orphan check (gamma/upload_gc.py) → ``{ok, id}``. 404 when it is not
    in Recently deleted."""
    ws = require_ws(request, write=True)
    if trash.purge(ws, page_id, actor=request.state.user or "") is None:
        raise HTTPException(status_code=404, detail="not in Recently deleted")
    return {"ok": True, "id": page_id}


@router.delete("/trash")
def empty_trash(request: Request):
    """Delete every page of Recently deleted for good → ``{deleted: [ids]}``."""
    ws = require_ws(request, write=True)
    return {"deleted": trash.empty(ws, actor=request.state.user or "")}
