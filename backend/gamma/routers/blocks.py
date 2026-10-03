"""Unified blocks API (/api/blocks/*) and block search."""

import json
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from fractional_indexing import generate_key_between
from pydantic import BaseModel

from ..auth import actor_of, require_ws, require_ws_writer, resolve_ws, share_scope
from ..blocks_store import (
    BLOCK_COLUMNS,
    FOLDERS,
    IN_LIBRARY,
    LABELS,
    STORED_COLUMNS,
    TREES,
    ancestor_chains,
    assert_block_in_scope,
    batch_page,
    block_to_dict,
    create_page,
    delete_children,
    fetch_subtree,
    filing,
    flatten_tree,
    free_position,
    get_or_create_doc_page,
    new_block_id,
    page_for_doc,
    page_root_id,
    subtree_refs,
    trash_entry,
    valid_block_id,
    write_lock,
)
from .. import cloud_auth, trash, upload_gc
from ..db import connect_pages_db, page_now
from ..markdown_export import build_tree
from ..ops import (MAX_CONTENT, OpError, StorableBody, commit_ops, delete_page, latest_seq, move_across_pages,
                   note_reload, trash_page)
from ..storage import upload_refs
from ..textnorm import fuzzy_pattern, literal_runs

router = APIRouter(prefix="/api", tags=["blocks"])

# The Ctrl+F notes scan answers with what it found so far (``partial``)
# once it has run this long.
BLOCK_SEARCH_BUDGET_S = 2.0


class UBCreateRequest(StorableBody):
    parent_id: str
    content: str = ""
    properties: dict = {}
    before: str | None = None   # fractional position of the sibling before this one
    after: str | None = None    # fractional position of the sibling after this one


class UBUpdateRequest(StorableBody):
    content: str | None = None
    base: str | None = None     # the text `content` was edited from: merged, not replaced (ops.py)
    properties: dict | None = None
    base_properties: dict | None = None  # the values `properties` was computed from (an ink merge, ops.py)


class UBReorderRequest(BaseModel):
    parent_id: str | None = None   # if provided, also reparents the block
    before: str | None = None
    after: str | None = None


class UBByDocCreate(BaseModel):
    default_title: str
    source_url: str | None = None
    original_filename: str | None = None
    folder: str | None = None       # a folder id: files a NEWLY created page (ignored when the page exists)


class UBPutChildrenRequest(BaseModel):
    blocks: list


def _scan_blocks(conn, pattern, runs: list[str], case: bool, limit: int,
                 reach: set | None = None) -> tuple[list, bool]:
    """The ``limit`` most recently edited blocks of the library (only ones
    in ``reach`` when a share token limits what is seen) whose
    text the fuzzy ``pattern`` matches, as ``(rows, partial)``. Its
    separator-tolerant rules ("3000" hits "3,000-qubit") can't be SQL, but
    every match contains the query's literal ``runs``
    (textnorm.literal_runs), so SQLite drops the other blocks (outside the
    GIL) and Python runs the pattern over the candidates only, newest first,
    until it has ``limit``. Past BLOCK_SEARCH_BUDGET_S it stops with what it
    found (``partial``)."""
    where, args = "", []
    for run in runs[:3]:
        if case:
            where += " AND instr(content, ?) > 0"
            args.append(run)
        else:
            where += " AND content LIKE ? ESCAPE '\\'"
            args.append("%" + run.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    deadline = time.monotonic() + BLOCK_SEARCH_BUDGET_S
    conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    rows = []
    try:
        for n, r in enumerate(conn.execute(
                "SELECT id, content, kind FROM unified_blocks "
                f"WHERE content != ''{where} AND {IN_LIBRARY} ORDER BY updated_at DESC", args)):
            if (reach is None or r[0] in reach) and pattern.search(r[1]):
                rows.append(r)
                if len(rows) >= limit:
                    break
            if n % 1000 == 999 and time.monotonic() > deadline:
                return rows, True
    except sqlite3.OperationalError as e:
        if "interrupt" not in str(e):
            raise
        return rows, True
    finally:
        conn.set_progress_handler(None, 0)
    return rows, False


@router.get("/block-search")
def block_search(request: Request, q: str = "", ids: str = "", limit: int = 10,
                 case: int = 0, whole: int = 0, regex: int = 0):
    """Blocks by id (``ids``), recently edited ones (no ``q``), or the fuzzy
    notes search behind Ctrl+F and the [[ref]] popup (``case`` / ``whole``
    word). ``partial`` when the scan ran out of time. A pattern of the
    caller's (``regex``) is refused: one catastrophic regex holds the GIL,
    and with it the whole server. Sync def: the scan runs in the
    threadpool."""
    if regex:
        raise HTTPException(status_code=400, detail="regex search is not supported")
    results = []
    partial = False
    # Through a share token: the blocks it reaches, silently — a ref chip or
    # link title in the share view resolves when its target is shared, and
    # nothing reveals whether anything else exists. Recently deleted is out
    # of everyone's reach: a [[ref]] to a trashed block resolves to nothing,
    # and no search lists one.
    scope = share_scope(request)
    with connect_pages_db(resolve_ws(request)) as conn:
        reach = scope.block_ids(conn) if scope is not None else None
        if ids:
            id_list = [i.strip() for i in ids.split(",") if i.strip()]
            if not id_list:
                return {"blocks": []}
            placeholders = ",".join("?" * len(id_list))
            rows = conn.execute(
                f"SELECT id, content, kind FROM unified_blocks WHERE id IN ({placeholders}) AND {IN_LIBRARY}",
                id_list,
            ).fetchall()
        elif not q:
            # Empty query: recently edited blocks, so the [[ref]] popup (and
            # the "/link" slash command) can suggest something before the
            # user types a filter.
            rows = conn.execute(
                f"SELECT id, content, kind FROM unified_blocks WHERE content != '' AND {IN_LIBRARY} "
                "ORDER BY updated_at DESC" + (" LIMIT ?" if reach is None else ""),
                (limit,) if reach is None else (),
            ).fetchall()
        else:
            pattern = fuzzy_pattern(q, bool(case), bool(whole))
            if pattern is None:
                return {"blocks": [], "error": "empty query"}
            rows, partial = _scan_blocks(conn, pattern, literal_runs(q, bool(case)), bool(case), limit, reach)
        if reach is not None:
            rows = [r for r in rows if r[0] in reach]
        if not ids:
            rows = rows[:limit]
        if not rows:
            return {"blocks": [], "partial": True} if partial else {"blocks": []}

        ancestors_by_id, page_root_by_id = ancestor_chains(conn, [r[0] for r in rows])

        for block_id, content, kind in rows:
            block = {"id": block_id, "content": content, "kind": kind}
            ancestors = ancestors_by_id.get(block_id)
            if ancestors:
                block["ancestors"] = ancestors
                block["page_root_id"] = page_root_by_id.get(block_id, block_id)
                block["page_title"] = ancestors[0]["content"]
            else:
                block["page_root_id"] = block_id
                block["page_title"] = content
            results.append(block)
    return {"blocks": results, "partial": True} if partial else {"blocks": results}


# Route order matters: static-prefix routes must come before /{block_id}

# Lookup / create BY ATTACHMENT: the page whose PDF is `doc_id`. Text-only
# pages are created by POST /api/pages (routers/pages.py).

@router.get("/blocks/by-doc/{doc_id}")
def ub_get_by_doc(doc_id: str, request: Request):
    scope = share_scope(request)
    with connect_pages_db(resolve_ws(request)) as conn:
        row = page_for_doc(conn, doc_id, BLOCK_COLUMNS)
        # A share may only learn about its own pages — refuse before revealing
        # whether any other doc id exists.
        if scope is not None and (not row or not scope.allows_page(conn, row[0])):
            raise HTTPException(status_code=403, detail="not accessible via this share link")
    if not row:
        raise HTTPException(status_code=404, detail="block not found for doc_id")
    return block_to_dict(row)


@router.post("/blocks/by-doc/{doc_id}")
def ub_get_or_create_by_doc(doc_id: str, payload: UBByDocCreate, request: Request):
    # The page carrying this PDF, created when absent (PDF ingest from the
    # app and the extension, and "Open as document" on a PDF file block) — a
    # write, so it requires a real session (never the ?share= read principal).
    ws = require_ws(request, write=True)
    with connect_pages_db(ws) as conn:
        return get_or_create_doc_page(
            conn, doc_id, payload.default_title, payload.source_url, payload.original_filename,
            folder=payload.folder or "", ws=ws, actor=actor_of(request))


def _not_found(conn, block_id: str) -> JSONResponse:
    """The 404 of a block no page holds. For one in Recently deleted it says
    so (``trashed``: its page's trash entry, blocks_store.trash_entry) —
    what the app's "That page is in Recently deleted" notice offers Restore
    from. Members only: a share is refused before it learns anything."""
    entry = trash_entry(conn, block_id)
    if entry:
        return JSONResponse(status_code=404, content={"detail": "page is in Recently deleted",
                                                      "trashed": trash.named([entry])[0]})
    return JSONResponse(status_code=404, content={"detail": "block not found"})


def _reach(conn, block_id: str, scope) -> str | None:
    """The page ``block_id`` is or lives in, for a read: a page of the
    library, or — for a member — the folder or label tree (``batch_page``);
    None when it is neither (``_not_found`` answers). 403 for a block a
    share does not reach."""
    assert_block_in_scope(conn, block_id, scope)
    return page_root_id(conn, block_id) if scope is not None else batch_page(conn, block_id)


@router.get("/blocks/{block_id}/children")
def ub_get_children(block_id: str, request: Request):
    """A block's children (block dicts); for ``root``, the library listing:
    every page as its ``page_summary``, and the folder and label trees
    (``_trees``) — what the home view draws, in one request. Sync def: a
    big library's listing is thousands of queries, in the threadpool."""
    scope = share_scope(request)
    if scope is not None and block_id == "root" and not scope.lists_library:
        # A page share may not enumerate the owner's library; a folder share
        # lists the pages it reaches — the share view's home library.
        raise HTTPException(status_code=403, detail="not accessible via this share link")
    with connect_pages_db(resolve_ws(request)) as conn:
        if block_id == "root":
            # one read snapshot for the pages, the trees and the trees' seqs
            if not conn.in_transaction:
                conn.execute("BEGIN")
            rows = conn.execute(f"SELECT {SUMMARY_COLUMNS} FROM unified_blocks WHERE parent_id = 'root' "
                                "ORDER BY position ASC").fetchall()
            if scope is not None:
                rows = [r for r in rows if scope.allows_page(conn, r[0])]
            out = {"children": [page_summary(r, _page_preview(conn, r[0])) for r in rows],
                   **_trees(conn, scope, rows)}
            conn.rollback()
            return out
        if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ?", (block_id,)).fetchone():
            raise HTTPException(status_code=404, detail="block not found")
        if _reach(conn, block_id, scope) is None:
            return _not_found(conn, block_id)
        rows = conn.execute(
            f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE parent_id = ? ORDER BY position ASC",
            (block_id,),
        ).fetchall()
    return {"children": [block_to_dict(r) for r in rows]}


def _trees(conn, scope, rows) -> dict:
    """The listing's ``folders`` and ``labels``: each tree's blocks nested
    under ``children`` (block dicts, siblings in order) and, for a member,
    ``seq`` — the op log's position the tree reflects, which a client
    following the tree's room catches up from. A folder share sees its
    folder (with what is below it) and the labels its pages carry."""
    if scope is None:
        return {tree: {"seq": latest_seq(conn, tree),
                       "children": (build_tree(fetch_subtree(conn, tree), tree) or {"children": []})["children"]}
                for tree in TREES}
    folder = build_tree(fetch_subtree(conn, scope.folder), scope.folder) if scope.folder else None
    at = SUMMARY_PROPS.index(LABELS)
    carried = {i for r in rows for i in filing({LABELS: json.loads(r[5])[at]}, LABELS)}
    labels = [block_to_dict(r) for r in conn.execute(
        f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE page_id = ? AND id IN (SELECT value FROM json_each(?)) "
        "ORDER BY position", (LABELS, json.dumps(sorted(carried))))]
    return {FOLDERS: {"children": [folder] if folder and folder["kind"] == "folder" else []},
            LABELS: {"children": [{**label, "children": []} for label in labels]}}


# What the library listing carries of a page's properties: what the home
# view's cards and filters, quick open, the chat and link pickers and the
# DOI / arXiv link matching read (docs/dev/api.md "The library listing").
# The rest — bibtex, ppt_cite, summary, the rest of meta, … — comes with the
# page itself (GET /blocks/{id}).
SUMMARY_PROPS = ("doc_id", "source_url", "original_filename", "folders", "labels", "pinned", "seeded")
SUMMARY_META = ("title", "authors", "year", "venue", "volume", "doi", "arxiv_id")
SUMMARY_COLUMNS = ("id, position, content, created_at, updated_at, json_extract(properties, "
                   + ", ".join([f"'$.{k}'" for k in SUMMARY_PROPS] + [f"'$.meta.{k}'" for k in SUMMARY_META])
                   + ")")


def page_summary(row, preview: str) -> dict:
    """A page as the library listing has it, from a ``SUMMARY_COLUMNS`` row:
    ``{id, content, position, created_at, updated_at, preview, properties}``
    — ``properties`` holding the SUMMARY_PROPS the page has, and ``meta``
    with the SUMMARY_META fields it has. SQLite picks them out (one
    ``json_extract`` of several paths, a JSON array), so a page's big
    properties are never parsed here."""
    values = json.loads(row[5])
    props = {k: v for k, v in zip(SUMMARY_PROPS, values) if v is not None}
    meta = {k: v for k, v in zip(SUMMARY_META, values[len(SUMMARY_PROPS):]) if v is not None}
    if meta:
        props["meta"] = meta
    return {"id": row[0], "content": row[2] or "", "position": row[1], "created_at": row[3],
            "updated_at": row[4], "preview": preview, "properties": props}


PREVIEW_CHARS = 240
_PREVIEW_BLOCKS = 5


def _page_preview(conn, page_id: str) -> str:
    """A page's ``preview`` in the library listing — the first ~PREVIEW_CHARS
    characters of its first few child blocks that are no highlight, joined
    with " · "; "" when it has none. One index seek (idx_ub_parent walks the
    page's children in order and stops at the fifth), so the listing's cost
    follows the pages listed, never the blocks in the library."""
    preview = ""
    for (content,) in conn.execute(
            "SELECT content FROM unified_blocks WHERE parent_id = ? AND content != '' "
            "AND kind != 'highlight' ORDER BY position LIMIT ?", (page_id, _PREVIEW_BLOCKS)):
        if len(preview) >= PREVIEW_CHARS:
            break
        piece = " ".join(content.split())
        preview = f"{preview} · {piece}" if preview else piece
    return preview[:PREVIEW_CHARS]


@router.get("/blocks/{block_id}/subtree")
def ub_get_subtree(block_id: str, request: Request):
    """The block with its whole subtree. For a page — or the pseudo-page
    ``folders`` / ``labels``, a member's read of a whole tree — ``seq`` is
    the op log's position this tree reflects: the live session catches up
    from it."""
    scope = share_scope(request)
    with connect_pages_db(resolve_ws(request)) as conn:
        if _reach(conn, block_id, scope) is None:
            return _not_found(conn, block_id)
        # One read snapshot for the tree and its seq: a batch committed
        # between two separate reads would be counted but missing, and the
        # client would never learn of it (a block it deleted stays on screen).
        if not conn.in_transaction:
            conn.execute("BEGIN")
        rows = fetch_subtree(conn, block_id)
        seq = latest_seq(conn, block_id) if rows and (rows[0][1] == "root" or block_id in TREES) else None
        conn.rollback()
    if not rows:
        raise HTTPException(status_code=404, detail="block not found")
    out = {"block": build_tree(rows, block_id)}
    if seq is not None:
        out["seq"] = seq
    # Serialized here, in the worker thread: a returned dict is encoded on the
    # event loop, which a 5,000-block page would stall for a fifth of a second.
    return JSONResponse(out)


@router.get("/blocks/{block_id}/backlinks")
def ub_get_backlinks(block_id: str, request: Request):
    """Return all blocks that reference `block_id` via [[block_id]] syntax."""
    # Backlinks span the whole library by nature, so a share link can't use
    # them without leaking other pages.
    if share_scope(request) is not None:
        raise HTTPException(status_code=403, detail="not accessible via this share link")
    with connect_pages_db(resolve_ws(request)) as conn:
        # (a page in Recently deleted links to nothing)
        rows = conn.execute(
            f"SELECT id, content FROM unified_blocks WHERE id != ? AND content LIKE ? AND {IN_LIBRARY} "
            "ORDER BY updated_at DESC LIMIT 50",
            (block_id, f"%[[{block_id}]]%"),
        ).fetchall()
        if not rows:
            return {"backlinks": []}

        ancestors_by_id, page_root_by_id = ancestor_chains(conn, [r[0] for r in rows])

        results = []
        for bid, content in rows:
            ancestors = ancestors_by_id.get(bid)
            results.append({
                "id": bid,
                "content": content,
                "page_root_id": page_root_by_id.get(bid, bid),
                "page_title": ancestors[0]["content"] if ancestors else content,
            })
    return {"backlinks": results}


@router.get("/blocks/{block_id}")
def ub_get_block(block_id: str, request: Request):
    scope = share_scope(request)
    with connect_pages_db(resolve_ws(request)) as conn:
        if _reach(conn, block_id, scope) is None:
            return _not_found(conn, block_id)
        row = conn.execute(
            f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="block not found")
    return block_to_dict(row)


def _ops(ws: str, page_id: str, ops: list[dict], request: Request, scope) -> dict:
    """Apply ops to a page on behalf of the request: a share editor is
    confined to the shared pages, every op error is its HTTP status."""
    if scope is not None:
        with connect_pages_db(ws) as conn:
            if not scope.allows_page(conn, page_id):
                raise HTTPException(status_code=403, detail="not accessible via this share link")
    try:
        return commit_ops(ws, page_id, ops, actor=actor_of(request),
                          share_scoped=scope is not None)
    except OpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.post("/blocks")
def ub_create_block(payload: UBCreateRequest, request: Request):
    block_id = new_block_id()
    ws = require_ws_writer(request)
    scope = share_scope(request)
    if payload.parent_id == "root":
        # A new page: not an op on any page. Share editors never get here.
        if scope is not None:
            raise HTTPException(status_code=403, detail="not accessible via this share link")
        new_pos = ""  # neither neighbour named: last in the library
        if payload.before is not None or payload.after is not None:
            try:
                new_pos = generate_key_between(payload.before, payload.after)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"invalid before/after: {e}")
        with connect_pages_db(ws) as conn:
            return create_page(conn, payload.content, payload.properties, actor=actor_of(request),
                               block_id=block_id, position=new_pos)
    with connect_pages_db(ws) as conn:
        page_id = page_root_id(conn, payload.parent_id)
    if not page_id:
        raise HTTPException(status_code=404, detail="parent block not found")
    try:
        position = generate_key_between(payload.before, payload.after)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid before/after: {e}")
    _ops(ws, page_id, [{"op": "insert", "id": block_id, "parent": payload.parent_id,
                        "position": position, "content": payload.content,
                        "props": payload.properties}], request, scope)
    with connect_pages_db(ws) as conn:
        row = conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
    if not row:  # deleted by someone else the moment it was made
        raise HTTPException(status_code=404, detail="block not found")
    return block_to_dict(row)


@router.put("/blocks/{block_id}")
def ub_update_block(block_id: str, payload: UBUpdateRequest, request: Request):
    """Content and/or a properties PATCH (a null value deletes the key).
    With ``base`` (the text the client's ``content`` was computed from) the
    edit is merged into a block that changed meanwhile instead of replacing
    it — the answer's ``content`` is the text actually stored. Likewise
    ``base_properties``: an ink group's ``ink_url`` whose base is not the
    stored one is merged by stroke, a text box's ``text_box`` key by key,
    and the answer's ``properties`` is the patch actually applied (it names
    the merged file or box)."""
    ws = require_ws_writer(request)
    scope = share_scope(request)
    with connect_pages_db(ws) as conn:
        page_id = page_root_id(conn, block_id)
    if not page_id:
        raise HTTPException(status_code=404, detail="block not found")
    op = {"op": "set", "id": block_id}
    if payload.content is not None:
        op["content"] = payload.content
        if payload.base is not None:
            op["base"] = payload.base
    if payload.properties is not None:
        op["props"] = payload.properties
        if payload.base_properties:
            op["base_props"] = payload.base_properties
    result = _ops(ws, page_id, [op], request, scope)
    out = {"ok": True, "updated_at": result["at"], "seq": result["seq"]}
    if payload.content is not None:
        out["content"] = result["ops"][0].get("content", payload.content)
    if payload.properties is not None:
        out["properties"] = result["ops"][0].get("props", payload.properties)
    return out


@router.delete("/blocks/{block_id}")
def ub_delete_block(block_id: str, request: Request):
    if block_id == "root":
        raise HTTPException(status_code=400, detail="cannot delete root block")
    ws = require_ws_writer(request)
    scope = share_scope(request)
    with connect_pages_db(ws) as conn:
        page_id = page_root_id(conn, block_id)
        if not page_id:
            raise HTTPException(status_code=404, detail="block not found")
        if page_id == block_id:
            # Deleting a page: not an op on the page's blocks. It goes to
            # Recently deleted (gamma/trash.py) — on a share host, whose pages
            # are published copies, for good. Its room (if any) is told to
            # reload, which surfaces the 404.
            if scope is not None:
                raise HTTPException(status_code=403, detail="share editors cannot delete the shared page")
            actor = actor_of(request)
            if cloud_auth.settings()["share_host"]:
                delete_page(ws, conn, block_id, actor=actor)
                return {"ok": True, "id": block_id}
            try:
                entry = trash_page(ws, conn, block_id, actor=actor)
            except OpError as e:
                raise HTTPException(status_code=e.status, detail=e.detail)
            return {"ok": True, "id": block_id, "trashed": trash.named([entry])[0]}
    _ops(ws, page_id, [{"op": "delete", "id": block_id}], request, scope)
    return {"ok": True, "id": block_id}


def _tree_rows(blocks: list, parent_id: str, now: str) -> list[dict]:
    """``flatten_tree`` of a request's nested blocks, validated like the ops
    it stands in for: objects only, ids of the block-id shape and never a
    reserved one (a node without one gets a fresh id), text no longer than
    an op may write."""
    rows: list = []
    try:
        flatten_tree(blocks, parent_id, rows, now)
    except (AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="blocks must be a tree of objects")
    for r in rows:
        if not valid_block_id(r["id"]):
            raise HTTPException(status_code=400, detail=f"invalid block id: {r['id']!r}")
        if not isinstance(r["content"], str):
            raise HTTPException(status_code=400, detail=f"block {r['id']}: content must be text")
        if len(r["content"]) > MAX_CONTENT:
            raise HTTPException(status_code=413, detail="content too long")
    return rows


@router.put("/blocks/{block_id}/children")
def ub_put_children(block_id: str, payload: UBPutChildrenRequest, request: Request):
    """Replace all children of a block with the provided nested tree (bulk
    paths — a duplicated page, tests; the editor sends ops), in one
    transaction: a tree that cannot be written (an id another block
    already has: 409) leaves the old children in place. The page's room is
    told to reload. Never the library itself: ``root``'s children are the
    pages, created and deleted one by one."""
    if block_id == "root":
        raise HTTPException(status_code=400, detail="cannot replace the children of root")
    now = page_now()
    rows = _tree_rows(payload.blocks, block_id, now)
    ws = require_ws_writer(request)
    scope = share_scope(request)
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ?", (block_id,)).fetchone():
                raise HTTPException(status_code=404, detail="block not found")
            assert_block_in_scope(conn, block_id, scope)
            page_id = page_root_id(conn, block_id)
            if page_id is None:
                raise HTTPException(status_code=404, detail="block not found")  # in Recently deleted
            old_refs = subtree_refs(r for r in fetch_subtree(conn, block_id) if r[0] != block_id)
            delete_children(conn, block_id)
            try:
                conn.executemany(
                    f"INSERT INTO unified_blocks ({STORED_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(r["id"], r["parent_id"], r["position"], r["content"],
                      r["properties"], r["created_at"], r["updated_at"], page_id) for r in rows])
            except sqlite3.IntegrityError:
                raise HTTPException(status_code=409, detail="a block id in the tree is already taken")
            conn.execute("UPDATE unified_blocks SET updated_at = ? WHERE id = ?", (now, block_id))
            new_refs = set().union(*(upload_refs(r["content"], r["properties"]) for r in rows))
            upload_gc.claim(conn, new_refs - old_refs)
            # the reload is logged and the page touched in the same
            # transaction as the rows: the change feed never misses a replace
            note_reload(ws, conn, page_id, actor_of(request))
        except BaseException:
            conn.rollback()
            raise
        upload_gc.schedule(ws, old_refs - new_refs)
    return {"ok": True, "count": len(rows), "updated_at": now}


@router.post("/blocks/{block_id}/reorder")
def ub_reorder_block(block_id: str, payload: UBReorderRequest, request: Request):
    """Move a block: within its page (an op) or to another page
    (``parent_id`` there — the source room sees a delete, the target
    room reloads)."""
    if block_id == "root":
        raise HTTPException(status_code=400, detail="cannot reorder root block")
    ws = require_ws_writer(request)
    scope = share_scope(request)
    if scope is not None and payload.parent_id == "root":
        raise HTTPException(status_code=403, detail="not accessible via this share link")
    try:
        new_pos = generate_key_between(payload.before, payload.after)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid before/after: {e}")
    with connect_pages_db(ws) as conn:
        # One snapshot for the checks and a cross-page move's writes, which
        # commit together with the source page's log row (record_ops).
        write_lock(conn)
        src_page = page_root_id(conn, block_id)
        if not src_page:
            raise HTTPException(status_code=404, detail="block not found")
        if scope is not None and (src_page == block_id or not scope.allows_page(conn, src_page)):
            raise HTTPException(status_code=403, detail="not accessible via this share link")
        if src_page == block_id:
            raise HTTPException(status_code=400, detail="pages are reordered through the library, not here")
        row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
        parent = payload.parent_id or row[0]
        dst_page = page_root_id(conn, parent)
        if not dst_page:
            raise HTTPException(status_code=404, detail="parent block not found")
        if dst_page != src_page:
            if scope is not None:
                raise HTTPException(status_code=403, detail="not accessible via this share link")
            if parent in {r[0] for r in fetch_subtree(conn, block_id)}:
                raise HTTPException(status_code=400, detail="cannot move a block into its own subtree")
            new_pos = free_position(conn, parent, new_pos, block_id)  # re-keyed like an op's move
            move_across_pages(ws, conn, block_id, parent, new_pos, src_page, dst_page,
                              actor=actor_of(request))
            return {"ok": True, "id": block_id, "position": new_pos}
    result = _ops(ws, src_page, [{"op": "move", "id": block_id, "parent": parent,
                                    "position": new_pos}], request, scope)
    return {"ok": True, "id": block_id, "position": result["ops"][0]["position"]}
