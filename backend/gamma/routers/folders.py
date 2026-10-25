"""Deleting a folder or a label (docs/dev/home_library.md).

Folders and labels are blocks of the pseudo-pages ``folders`` / ``labels``
(gamma/blocks_store.py ``TREES``): a new one, a rename, a move or a reorder
is one op on the tree (``POST /api/pages/folders/ops``), touching no page.
A delete is the one change that reaches the pages, so it has its endpoint:
the subtree goes, and in the same transaction every page filed under it
loses those ids (``ops.delete_tree``, which the agent's delete tools and a
revert's redo run too; one op batch per page), so its viewers and a mirror
see the refiling like any edit. A deleted folder's AI chats (its bucket and
its subfolders') are filed into the library chat's history, and its share
links stop. Workspace editors; never through a share link.
"""

from fastapi import APIRouter, HTTPException, Request

from ..auth import actor_of, require_ws
from ..blocks_store import FOLDERS, LABELS
from ..db import connect_pages_db
from ..ops import OpError, delete_tree

router = APIRouter(prefix="/api", tags=["folders"])


def delete_tree_block(ws: str, tree: str, block_id: str, *, actor: str) -> dict:
    """``ops.delete_tree`` for the two endpoints: ``{ids, pages, chats,
    shares}`` — the blocks deleted, the pages refiled, the conversations
    filed into the library's history, the share links stopped. 404 unless
    it is a block of the tree."""
    with connect_pages_db(ws) as conn:
        try:
            rows, carried, results = delete_tree(ws, conn, tree, block_id, actor=actor)
        except OpError as e:
            raise HTTPException(status_code=e.status, detail=e.detail)
    return {"ids": sorted(r[0] for r in rows), "pages": list(carried),
            "chats": results[0]["chats_filed"], "shares": results[0].get("shares_stopped", 0)}


def _writer(request: Request) -> str:
    if request.query_params.get("share"):
        raise HTTPException(status_code=403, detail="folders and labels cannot be changed through a share link")
    return require_ws(request, write=True)


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: str, request: Request):
    """Delete a folder with its subfolders: ``{ids, pages, chats, shares}``
    (``delete_tree_block``). The pages stay — filed elsewhere, or at the
    library root."""
    ws = _writer(request)
    return delete_tree_block(ws, FOLDERS, folder_id, actor=actor_of(request))


@router.delete("/labels/{label_id}")
def delete_label(label_id: str, request: Request):
    """Delete a label and take it off every page: ``{ids, pages, chats: 0,
    shares: 0}``."""
    ws = _writer(request)
    return delete_tree_block(ws, LABELS, label_id, actor=actor_of(request))
