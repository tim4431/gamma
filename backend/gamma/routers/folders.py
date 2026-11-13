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
instead, sorted and paged, a row per file as the listing has it. ``GET
/api/folders/{id}/at`` resolves a path, or a note's relative link, to the
pages at it (work_folders/paths.py), and ``GET /api/files`` finds files by
name or path across the work folders (quick open's Files group,
work_folders/file_search.py), both rows as the listing has them with each
file's ``path``.
"""

import json

from fastapi import APIRouter, HTTPException, Request

from .. import comments
from ..auth import actor_of, require_ws, resolve_ws, share_scope, writer_client
from ..blocks_store import FOLDERS, LABELS
from ..db import SQL_FILE_NAME, connect_pages_db
from ..ops import OpError, delete_tree
from ..work_folders import file_search, paths
from ..work_folders.marks import Tree
from .blocks import SUMMARY_COLUMNS, changes_seq, op_refusal, page_summary, summaries

router = APIRouter(prefix="/api", tags=["folders"])

FOLDER_PAGES = 200      # the files one read of a directory gives by default
MAX_FOLDER_PAGES = 500  # ...and at most
SORTS = ("name", "format", "size", "modified")
FILE_HITS = 20          # the files one search answers by default
MAX_FILE_HITS = 50      # ...and at most
NOT_A_DIRECTORY = "the library lists this folder's pages"
OUT_OF_SHARE = "not accessible via this share link"


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
    [row], offset, next, total, seq}``, ``next`` the offset of the next page
    (null at the end), ``seq`` the change log's as the rows were read (the
    listing's ``work_changes`` says when a directory read at it changed
    since). A row is the page as the library listing has it
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
            raise HTTPException(status_code=403, detail=OUT_OF_SHARE)
        if not Tree.read(conn).work_of(folder_id):
            raise HTTPException(status_code=400, detail=NOT_A_DIRECTORY)
        if not conn.in_transaction:
            conn.execute("BEGIN")  # the rows and the count of one snapshot
        where = "INDEXED BY idx_ub_dir WHERE parent_id = 'root' AND work_folder IS NOT NULL AND first_folder = ?"
        total = conn.execute(f"SELECT COUNT(*) FROM unified_blocks {where}", (folder_id,)).fetchone()[0]
        rows = conn.execute(f"SELECT {SUMMARY_COLUMNS} FROM unified_blocks {where} "
                            f"ORDER BY {_order(sort, desc)} LIMIT ? OFFSET ?", (folder_id, limit, offset)).fetchall()
        counts = comments.open_counts(conn, [r[0] for r in rows])
        seq = changes_seq(conn)
        conn.rollback()
    pages = [{**page_summary(r, ""), "open_comments": counts.get(r[0], 0)} for r in rows]
    end = offset + len(rows)
    return {"folder": folder_id, "pages": pages, "offset": offset, "next": end if end < total else None,
            "total": total, "seq": seq}


def _file_row(tree: Tree, row, shared: str = "") -> dict:
    """A file as the listing has it (no preview, no parent) with its
    ``path`` in its work folder (for a folder share ``shared``, from the
    shared folder's parent: ``paths.path_top``)."""
    page = page_summary(row, "")
    return {**page, "path": paths.page_path(tree, page["properties"], title=page["content"], shared=shared)}


def _member_or_folder_share(request: Request):
    """The request's share scope, None for a member; 403 for a page
    share, which reaches no folder."""
    scope = share_scope(request)
    if scope is not None and not scope.folder:
        raise HTTPException(status_code=403, detail=OUT_OF_SHARE)
    return scope


@router.get("/folders/{folder_id}/at")
def folder_path(folder_id: str, path: str, request: Request, dir: str | None = None):
    """The pages a path names in the work folder of ``folder_id`` (W or a
    directory of it): ``{pages: [row]}``, a row the page as the listing
    has it with its ``path``, by path then id. ``path`` is a link's target
    as a note wrote it, or a typed path; ``dir`` the directory of the note
    the link is in, as ``paths.dir_path`` spells it ("" for W), by default
    ``folder_id``'s own (a note's link asks with its note's directory, which
    a folder share of it covers). Resolved by ``paths.page_at_href``:
    relative to ``dir``, then from W's top. Two files the rule cannot tell
    apart are both answered, for the caller to choose; 404 for none (a link
    with a scheme or a leading ``/`` names none). 400 for a folder in no
    work folder; any member, or a folder share that covers the folder,
    which resolves the path among the pages it reaches alone (403
    otherwise, and for a page share)."""
    scope = _member_or_folder_share(request)
    with connect_pages_db(resolve_ws(request)) as conn:
        if scope is not None and not scope.allows_folder(conn, folder_id):
            raise HTTPException(status_code=403, detail=OUT_OF_SHARE)
        if not conn.in_transaction:
            conn.execute("BEGIN")  # the tree, the resolution and the rows of one snapshot
        tree = Tree.read(conn)
        work = tree.work_of(folder_id)
        if not work:
            conn.rollback()
            raise HTTPException(status_code=400, detail=NOT_A_DIRECTORY)
        base = paths.dir_path(tree, folder_id, work) if dir is None else dir
        allows = None if scope is None else (lambda page_id: scope.allows_page(conn, page_id))
        ids = paths.page_at_href(conn, tree, work, base, path, allows=allows)
        found = summaries(conn, ids) if ids else {}
        conn.rollback()
    if not found:
        raise HTTPException(status_code=404, detail="no tracked file at that path")
    shared = scope.folder if scope is not None else ""
    rows = [_file_row(tree, found[i], shared) for i in ids if i in found]
    return {"pages": sorted(rows, key=lambda r: (r["path"], r["id"]))}


@router.get("/files")
def find_files(q: str, request: Request, folder: str = "", limit: int = FILE_HITS):
    """A work folder's files by name or path, best first: ``{files: [row],
    total}``, a row the file as the listing has it with its ``path``, its
    ``dir`` (the path's directory part, "" at the top) and ``match: {name,
    path}``, the code-point spans ``[from, to)`` the query matched in the
    file's name and in ``dir``; ``total`` how many matched before the cut
    at ``limit`` (at most ``MAX_FILE_HITS``). The rule is
    ``work_folders.file_search``; a ``q`` that names nothing (blank, or
    separators alone) answers no rows. ``folder``: W or a directory of it,
    its subtree only (400 for a folder in no work folder); none: every work
    folder. Any member; a folder share keeps to the files its folder
    reaches (403 for a folder it does not cover, and for a page share),
    their paths and directories measured from the shared folder's parent
    (``paths.path_top``), so no folder above it is named or matched.

    One snapshot: the candidates are read from ``idx_ub_file_name`` alone
    (named: the planner never picks it), the longest runs of the query's
    letters and digits a LIKE each on the name's expression
    (``file_search.runs``), then ranked in Python and the top rows read by
    id."""
    scope = _member_or_folder_share(request)
    limit = max(1, min(limit, MAX_FILE_HITS))
    query = file_search.parse(q)
    with connect_pages_db(resolve_ws(request)) as conn:
        if folder and scope is not None and not scope.allows_folder(conn, folder):
            raise HTTPException(status_code=403, detail=OUT_OF_SHARE)
        if not conn.in_transaction:
            conn.execute("BEGIN")
        tree = Tree.read(conn)
        if folder and not tree.work_of(folder):
            conn.rollback()
            raise HTTPException(status_code=400, detail=NOT_A_DIRECTORY)
        if query is None:
            conn.rollback()
            return {"files": [], "total": 0}
        within = None
        if folder or scope is not None:
            within = tree.subtree(folder or scope.folder)
            if folder and scope is not None:
                within &= tree.subtree(scope.folder)
            within = sorted(f for f in within if tree.work_of(f))
        sql = (f"SELECT {SQL_FILE_NAME}, first_folder, id FROM unified_blocks INDEXED BY idx_ub_file_name "
               "WHERE parent_id = 'root' AND work_folder IS NOT NULL")
        args: list = []
        if within is not None:
            sql += " AND first_folder IN (SELECT value FROM json_each(?))"
            args.append(json.dumps(within))
        for run in file_search.runs(query):
            sql += f" AND {SQL_FILE_NAME} LIKE ?"
            args.append(f"%{run}%")
        shared = scope.folder if scope is not None else ""

        def dir_of(f):
            w = tree.work_of(f)
            return paths.dir_path(tree, f, paths.path_top(tree, w, shared))

        hits, total = file_search.rank(conn.execute(sql, args), query, dir_of, limit)
        found = summaries(conn, [h[0][2] for h in hits]) if hits else {}
        conn.rollback()
    files = []
    for (_, folder_id, page_id), _, name_spans, dir_spans in hits:
        if page_id in found:
            row = _file_row(tree, found[page_id], shared)
            files.append({**row, "dir": dir_of(folder_id), "match": {"name": name_spans, "path": dir_spans}})
    return {"files": files, "total": total}
