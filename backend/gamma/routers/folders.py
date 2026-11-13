"""Deleting a folder or a label (docs/dev/home_library.md), and a work
folder's directory read page by page (docs/dev/work_folders.md).

Folders and labels are blocks of the pseudo-pages ``folders`` / ``labels``
(gamma/blocks_store.py ``TREES``): a new one, a rename, a move or a reorder
is one op on the tree (``POST /api/pages/folders/ops``), touching no page.
A delete is the one change that reaches the pages, so it has its endpoint:
the subtree goes, and in the same transaction every page filed under it
loses those ids (``ops.delete_tree``, which the agent's delete tools and a
revert's redo run too; one op batch per page), so its viewers and a mirror
see the refiling like any edit. A deleted folder's AI chats (its bucket and
its subfolders') are filed into the library chat's history, and its share
links stop. Workspace editors; never through a share link. Inside a work
folder the tree is its vault's (``work_folders.filing.tree_op``): a person's
delete there is refused with the ``locked`` conflict.

The library listing leaves a work folder's files out unless they are in the
library; ``GET /api/folders/{id}/pages`` lists one of its directories
instead, sorted and paged, a row per file as the listing has it.
"""

from fastapi import APIRouter, HTTPException, Request

from .. import comments
from ..auth import actor_of, require_ws, resolve_ws, share_scope, writer_client
from ..blocks_store import FOLDERS, LABELS
from ..db import connect_pages_db
from ..ops import OpError, delete_tree
from ..work_folders.marks import Tree
from .blocks import SUMMARY_COLUMNS, op_refusal, page_summary

router = APIRouter(prefix="/api", tags=["folders"])

FOLDER_PAGES = 200      # the files one read of a directory gives by default
MAX_FOLDER_PAGES = 500  # ...and at most
SORTS = ("name", "format", "size", "modified")


def delete_tree_block(ws: str, tree: str, block_id: str, *, actor: str, client: str = ""):
    """``ops.delete_tree`` for the two endpoints, as the writer ``client``:
    ``{ids, pages, chats, shares}`` — the blocks deleted, the pages refiled,
    the conversations filed into the library's history, the share links
    stopped. 404 unless it is a block of the tree; a refusal answered as
    ``op_refusal``."""
    with connect_pages_db(ws) as conn:
        try:
            rows, carried, results = delete_tree(ws, conn, tree, block_id, actor=actor, client=client)
        except OpError as e:
            return op_refusal(e)
    return {"ids": sorted(r[0] for r in rows), "pages": list(carried),
            "chats": len(results[0]["chats_filed"]), "shares": results[0].get("shares_stopped", 0)}


def _writer(request: Request) -> str:
    if request.query_params.get("share"):
        raise HTTPException(status_code=403, detail="folders and labels cannot be changed through a share link")
    return require_ws(request, write=True)


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: str, request: Request):
    """Delete a folder with its subfolders: ``{ids, pages, chats, shares}``
    (``delete_tree_block``). The pages stay — filed elsewhere, or at the
    library root. A work folder's folders are its vault's: the writer
    ``writer_client`` names (a vault of another computer dropping a
    directory) passes, a person is refused (403, ``locked``)."""
    ws = _writer(request)
    return delete_tree_block(ws, FOLDERS, folder_id, actor=actor_of(request), client=writer_client(request))


@router.delete("/labels/{label_id}")
def delete_label(label_id: str, request: Request):
    """Delete a label and take it off every page: ``{ids, pages, chats: 0,
    shares: 0}``."""
    ws = _writer(request)
    return delete_tree_block(ws, LABELS, label_id, actor=actor_of(request))


def _order(sort: str, desc: bool) -> str:
    """The ORDER BY of a directory's read: ``sort`` in ``order``, then the
    file's name (``file_name``, then the title), then the id, the name and
    the id in ``order`` too when the sort is by name."""
    way = "DESC" if desc else "ASC"
    then = way if sort == "name" else "ASC"
    name = f"json_extract(properties, '$.file_name') COLLATE NOCASE {then}, content COLLATE NOCASE {then}"
    first = {"name": "",
             "format": f"COALESCE(json_extract(properties, '$.doc.format'), 'markdown') {way}, ",
             "size": f"json_extract(properties, '$.doc.size') IS NULL, json_extract(properties, '$.doc.size') {way}, ",
             "modified": f"updated_at {way}, "}[sort]
    return f"{first}{name}, id {then}"


@router.get("/folders/{folder_id}/pages")
def folder_pages(folder_id: str, request: Request, sort: str = "name", order: str = "", offset: int = 0,
                 limit: int = FOLDER_PAGES):
    """A work folder's directory, page by page: the files filed directly in
    the folder ``folder_id`` (their ``folders[0]``, ``idx_ub_dir``), W itself
    or a folder below it, whatever the library lists — ``{folder, pages:
    [row], offset, next, total}``, ``next`` the offset of the next page
    (null at the end). A row is the page as the library listing has it
    (``page_summary``, no preview, and no parent: a file is inside no page)
    with ``open_comments``, its open threads (``comments.open_counts``).
    ``sort``: ``name`` (the file's name, then the title; the default),
    ``format`` (a note's is markdown), ``size`` (none last) or
    ``modified``; ``order`` ``asc`` or ``desc`` (``modified`` starts with
    the newest); ``limit`` at most ``MAX_FOLDER_PAGES``. The directory's own
    folders are not rows: the client lists them from the folder tree. 400
    for a folder in no work folder (the library lists its pages) and a sort
    or order there is none of; any member, or a folder share that covers
    the folder (403 otherwise)."""
    if sort not in SORTS:
        raise HTTPException(status_code=400, detail=f"sort is one of {', '.join(SORTS)}")
    if order not in ("", "asc", "desc"):
        raise HTTPException(status_code=400, detail="order is asc or desc")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must not be negative")
    desc = order == "desc" or (not order and sort == "modified")
    limit = max(1, min(limit, MAX_FOLDER_PAGES))
    scope = share_scope(request)
    with connect_pages_db(resolve_ws(request)) as conn:
        if scope is not None and not scope.allows_folder(conn, folder_id):
            raise HTTPException(status_code=403, detail="not accessible via this share link")
        if not Tree.read(conn).work_of(folder_id):
            raise HTTPException(status_code=400, detail="the library lists this folder's pages")
        if not conn.in_transaction:
            conn.execute("BEGIN")  # the rows and the count of one snapshot
        where = "INDEXED BY idx_ub_dir WHERE parent_id = 'root' AND work_folder IS NOT NULL AND first_folder = ?"
        total = conn.execute(f"SELECT COUNT(*) FROM unified_blocks {where}", (folder_id,)).fetchone()[0]
        rows = conn.execute(f"SELECT {SUMMARY_COLUMNS} FROM unified_blocks {where} "
                            f"ORDER BY {_order(sort, desc)} LIMIT ? OFFSET ?", (folder_id, limit, offset)).fetchall()
        counts = comments.open_counts(conn, [r[0] for r in rows])
        conn.rollback()
    pages = [{**page_summary(r, ""), "open_comments": counts.get(r[0], 0)} for r in rows]
    end = offset + len(rows)
    return {"folder": folder_id, "pages": pages, "offset": offset, "next": end if end < total else None,
            "total": total}
