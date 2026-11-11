"""Recently deleted (/api/trash*): the pages deleted in the last
``trash.KEEP_DAYS`` days (gamma/trash.py). Any member lists it; editors and
owners restore a page or delete it for good — the same people who may delete
a page. Share links never reach it. A work folder's file comes back when its
file does: a person restores only a note that follows Gamma, and none its
vault took out (gamma/work_folders/filing.py ``may_restore``)."""

from fastapi import APIRouter, HTTPException, Request

from .. import trash
from ..auth import actor_of, require_ws, writer_client
from ..db import connect_pages_db
from ..ops import OpError, restore_page
from .blocks import op_refusal

router = APIRouter(prefix="/api", tags=["trash"])


@router.get("/trash")
def list_deleted(request: Request):
    """``{pages: [{id, title, folders, parent, deleted_at, deleted_by,
    purge_at, sub_pages, work_folder?, deleted_by_vault?, restorable?}], keep_days}``,
    the last deleted first (``folders``: folder ids; ``parent``: the live
    page a restore puts it back inside, "" for none; ``sub_pages``: how many
    pages went with it, listed only through it; a work folder's file names
    its work folder, whether its vault took it out and whether a person may
    restore it, gamma/trash.py)."""
    ws = require_ws(request)
    with connect_pages_db(ws) as conn:
        return {"pages": trash.list_trash(conn), "keep_days": trash.KEEP_DAYS}


@router.post("/trash/{page_id}/restore")
def restore_deleted(page_id: str, request: Request):
    """Put a page back in the library, filed where it was, with the pages
    that went with it (``ops.restore_page``, as the writer
    ``writer_client`` names: a vault of another computer brings its files
    back) → its block dict. 404 when it is not in Recently deleted; a
    refusal of the work-folder rule is 403 with the ``locked`` conflict
    (``op_refusal``)."""
    ws = require_ws(request, write=True)
    try:
        with connect_pages_db(ws) as conn:
            return restore_page(ws, conn, page_id, actor=actor_of(request), client=writer_client(request))
    except OpError as e:
        return op_refusal(e)


@router.delete("/trash/{page_id}")
def delete_forever(page_id: str, request: Request):
    """Delete one page of Recently deleted for good (``ops.delete_page``,
    the pages that went with it too): its blocks, chats and indexes; the
    files nothing else references go to
    the orphan check (gamma/upload_gc.py) → ``{ok, id}``. 404 when it is not
    in Recently deleted."""
    ws = require_ws(request, write=True)
    if trash.purge(ws, page_id, actor=actor_of(request)) is None:
        raise HTTPException(status_code=404, detail="not in Recently deleted")
    return {"ok": True, "id": page_id}


@router.delete("/trash")
def empty_trash(request: Request):
    """Delete every page of Recently deleted for good → ``{deleted: [ids]}``."""
    ws = require_ws(request, write=True)
    return {"deleted": trash.empty(ws, actor=actor_of(request))}
