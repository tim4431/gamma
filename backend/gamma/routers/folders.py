"""Deleting a folder or a label (docs/dev/home_library.md).

Folders and labels are blocks of the pseudo-pages ``folders`` / ``labels``
(gamma/blocks_store.py ``TREES``): a new one, a rename, a move or a reorder
is one op on the tree (``POST /api/pages/folders/ops``), touching no page.
A delete is the one change that reaches the pages, so it has its endpoint:
the subtree goes, and in the same transaction every page filed under it
loses those ids — one op batch per page (``ops.apply_batches``), so its
viewers and a mirror see the refiling like any edit. A deleted folder's AI
chats (its bucket and its subfolders') are filed into the library chat's
history, and its share links stop. Workspace editors; never through a share
link.
"""

import json

from fastapi import APIRouter, HTTPException, Request

from ..auth import actor_of, require_ws
from ..blocks_store import FOLDERS, LABELS, fetch_subtree, filing, write_lock
from ..db import connect_pages_db
from ..ops import OpError, after_commit, apply_batches
from . import chats, shares

router = APIRouter(prefix="/api", tags=["folders"])


def delete_tree_block(ws: str, tree: str, block_id: str, *, actor: str) -> dict:
    """Delete the folder or label ``block_id`` of ``tree`` with everything
    below it, and take the ids off the pages that carry them, in one
    transaction; then the folder chats and shares. Returns ``{ids, pages,
    chats, shares}`` — the blocks deleted, the pages refiled, the
    conversations filed into the library's history, the share links
    stopped. 404 unless it is a block of the tree (the reserved row is
    none)."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND page_id = ?",
                                (block_id, tree)).fetchone():
                raise HTTPException(status_code=404, detail="folder not found" if tree == FOLDERS else "label not found")
            ids = {r[0] for r in fetch_subtree(conn, block_id)}
            refiled = []
            for page_id, raw in conn.execute(
                    f"SELECT id, properties FROM unified_blocks p WHERE parent_id = 'root' AND EXISTS ("
                    f"SELECT 1 FROM json_each(p.properties, '$.{tree}') f WHERE f.value IN "
                    "(SELECT value FROM json_each(?)))", (json.dumps(sorted(ids)),)).fetchall():
                kept = [i for i in filing(json.loads(raw), tree) if i not in ids]
                refiled.append((page_id, [{"op": "set", "id": page_id, "props": {tree: kept or None}}]))
            try:
                results = apply_batches(conn, [(tree, [{"op": "delete", "id": block_id}]), *refiled], actor=actor)
            except OpError as e:
                raise HTTPException(status_code=e.status, detail=e.detail)
            filed = chats.file_into_home(conn, ids) if tree == FOLDERS else 0
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        for result in results:
            after_commit(ws, conn, result)
    stopped = shares.delete_folder_shares(ws, ids) if tree == FOLDERS else 0
    return {"ids": sorted(ids), "pages": [p for p, _ in refiled], "chats": filed, "shares": stopped}


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
