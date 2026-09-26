"""Folders are labels (``properties.folder`` on page roots, gamma/foldertags.py),
so a rename, move or delete is a prefix rewrite the frontend applies page by
page. What ELSE names a folder by its path follows through this one call:
the per-folder chat buckets (``home:<path>``, gamma/routers/chats.py) and
folder shares (gamma/routers/shares.py). The frontend calls it with the
same src → dst mapping it applies to the tags (subfolders ride along; dst ""
means the folder is gone). Best-effort on the frontend's side — a failed
call orphans a conversation or a share, never page data.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..auth import require_ws
from . import chats, shares

router = APIRouter(prefix="/api", tags=["folders"])


class FolderRenameRequest(BaseModel):
    src: str
    dst: str


@router.post("/folders/rename")
async def rename_folder(payload: FolderRenameRequest, request: Request):
    """Carry a folder's chat buckets and share links along a rename / move
    (``dst`` the new path) or drop them (``dst`` ""). Workspace editors; never
    through a share link."""
    if request.query_params.get("share"):
        raise HTTPException(status_code=403, detail="folders cannot be changed through a share link")
    ws = require_ws(request, write=True)
    src = (payload.src or "").strip().strip("/")
    dst = (payload.dst or "").strip().strip("/")
    if not src:
        raise HTTPException(status_code=400, detail="src folder path required")
    moved = chats.move_folder_buckets(ws, src, dst)
    return {"ok": True, **moved, "shares_moved": shares.move_folder_shares(ws, src, dst)}
