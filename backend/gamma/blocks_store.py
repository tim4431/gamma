"""unified_blocks table helpers shared across routers."""

import json
import re
import secrets

import orjson
from fractional_indexing import generate_key_between, generate_n_keys_between

from . import upload_gc
from .db import page_now
from .storage import display_filename, pdf_url, upload_refs, url_filename

# What a copy of a block writes (``kind`` and ``doc_id`` are generated:
# never written, db.BLOCK_HOT_COLUMNS) and what a read of one selects —
# ``block_to_dict``'s row.
STORED_COLUMNS = "id, parent_id, position, content, properties, created_at, updated_at, page_id"
BLOCK_COLUMNS = f"{STORED_COLUMNS}, kind"
_UB_COLUMNS = ", ".join(f"ub.{c}" for c in BLOCK_COLUMNS.split(", "))

# The parent of the pages in Recently deleted (ops.trash_page, gamma/trash.py):
# a reserved block id beside "root". A page moved under it keeps every block,
# chat and file, its own and its blocks' ``page_id`` too, but is no page any
# more: whatever asks for pages (``parent_id = 'root'``) passes it by, and
# neither ``page_root_id`` nor the op batches find a page for one of its
# blocks.
TRASH = "trash"

# The folder and label trees (docs/dev/home_library.md): two more reserved
# parentless rows, each a pseudo-page to the op path (gamma/ops.py). A
# folder is a block under ``folders`` or under another folder — ``content``
# its name, ``position`` its place among its siblings, ``properties`` like
# any block's (``pinned``); a label is a block right under ``labels``. Every
# block of one carries the pseudo-page's id as its ``page_id``, so its
# ``kind`` is ``folder`` / ``label``. A page is filed by id:
# ``properties.folders`` and ``properties.labels``, lists of block ids.
FOLDERS = "folders"
LABELS = "labels"
TREES = (FOLDERS, LABELS)
RESERVED = ("root", TRASH, *TREES)
PATH_SEP = " / "  # how a folder's path reads: its names from the top, joined

# The rows inside a page of the library: a condition for the readers that
# scan blocks across pages (block search, backlinks), which pass by the
# reserved rows, the folder and label trees and Recently deleted.
IN_LIBRARY = "page_id IN (SELECT id FROM unified_blocks WHERE parent_id = 'root')"

BLOCK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # the shape of a block or page id


def valid_block_id(block_id) -> bool:
    """An id a client may give a new block or page: the id shape, and none
    of the reserved ones (``RESERVED``)."""
    return isinstance(block_id, str) and bool(BLOCK_ID_RE.match(block_id)) and block_id not in RESERVED


def load_json(text: str):
    """Stored JSON text (a ``properties`` column, a ``json_extract`` of
    several paths) parsed by orjson (docs/dev/api.md has the timings).
    What orjson refuses and ``json.dumps`` may have written goes to the
    standard library: NaN or an infinity, the escape of a lone UTF-16
    surrogate (the op path stores U+FFFD, ``ops.storable``), nesting past
    1,024 levels. An integer past 64 bits comes back a float, as a browser
    reads it anyway. Writes keep ``json.dumps``, so nothing stored changes
    shape."""
    try:
        return orjson.loads(text)
    except orjson.JSONDecodeError:
        return json.loads(text)


def block_to_dict(row) -> dict:
    """The API's block from a ``BLOCK_COLUMNS`` row. ``page_id`` and
    ``kind`` are read-only: no write takes them, the server derives both."""
    return {
        "id": row[0],
        "parent_id": row[1],
        "position": row[2],
        "content": row[3] or "",
        "properties": load_json(row[4] or "{}"),
        "created_at": row[5],
        "updated_at": row[6],
        "page_id": row[7],
        "kind": row[8],
    }


def last_child_position(conn, parent_id: str) -> str | None:
    row = conn.execute(
        "SELECT position FROM unified_blocks WHERE parent_id = ? ORDER BY position DESC LIMIT 1",
        (parent_id,),
    ).fetchone()
    return row[0] if row else None


def free_position(conn, parent_id: str, position: str | None, block_id: str = "") -> str:
    """The sibling key ``block_id`` gets under ``parent_id``: ``position``
    (a key the client minted) unless another sibling holds it — clients
    mint keys on their own, so two can pick the same — else a key between
    it and the next sibling up; None appends after the last child. Call it
    under the write lock, in the transaction that writes the key."""
    if position is None:
        return generate_key_between(last_child_position(conn, parent_id), None)
    clash = conn.execute(
        "SELECT 1 FROM unified_blocks WHERE parent_id = ? AND position = ? AND id != ?",
        (parent_id, position, block_id)).fetchone()
    if not clash:
        return position
    nxt = conn.execute(
        "SELECT MIN(position) FROM unified_blocks WHERE parent_id = ? AND position > ? AND id != ?",
        (parent_id, position, block_id)).fetchone()[0]
    return generate_key_between(position, nxt)


def fetch_subtree(conn, block_id: str):
    """Fetch a block + all its descendants."""
    return conn.execute(
        f"""
        WITH RECURSIVE subtree AS (
            SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?
            UNION ALL
            SELECT {_UB_COLUMNS} FROM unified_blocks ub JOIN subtree s ON ub.parent_id = s.id
        )
        SELECT {BLOCK_COLUMNS} FROM subtree
        """,
        (block_id,),
    ).fetchall()


def subtree_refs(rows) -> set[str]:
    """The upload names ``fetch_subtree`` rows reference (storage.upload_refs)."""
    return set().union(*(upload_refs(r[3] or "", r[4] or "{}") for r in rows))


# The subtree writes START with DELETE / UPDATE, the recursive walk inside
# the IN (...): Python's sqlite3 opens its implicit transaction only before a
# statement that begins with INSERT/UPDATE/DELETE/REPLACE. A ``WITH …
# DELETE`` would commit on the spot, and a later failure of the same write
# could not roll it back (a page left empty, a page gone without its
# change-log row).

def _subtree_of(seed: str) -> str:
    """The ``IN (...)`` subquery of the ids of the rows ``unified_blocks``
    ``seed`` (a condition on its ``id`` / ``parent_id``, one ``?``) selects
    and all their descendants."""
    return f"""(
            WITH RECURSIVE subtree AS (
                SELECT id FROM unified_blocks WHERE {seed}
                UNION ALL
                SELECT ub.id FROM unified_blocks ub JOIN subtree s ON ub.parent_id = s.id
            )
            SELECT id FROM subtree)"""


def delete_subtree(conn, block_id: str):
    """Delete a block and all its descendants."""
    conn.execute(f"DELETE FROM unified_blocks WHERE id IN {_subtree_of('id = ?')}", (block_id,))


def delete_children(conn, block_id: str):
    """Delete all descendants of a block, keeping the block itself."""
    conn.execute(f"DELETE FROM unified_blocks WHERE id IN {_subtree_of('parent_id = ?')}", (block_id,))


def move_subtree_to_page(conn, block_id: str, page_id: str):
    """Make ``page_id`` the page of a block and all its descendants: the
    ``page_id`` half of a move to another page (the caller writes the new
    parent)."""
    conn.execute(f"UPDATE unified_blocks SET page_id = ? WHERE id IN {_subtree_of('id = ?')}", (page_id, block_id))


def flatten_tree(tree, parent_id, result, now):
    """Recursively flatten a nested block tree into flat rows with fractional positions."""
    n = len(tree or [])
    if n == 0:
        return
    keys = generate_n_keys_between(None, None, n=n)
    for node, key in zip(tree, keys):
        props = node.get("properties") or {}
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except Exception:
                props = {}
        node_id = node.get("id") or new_block_id()
        result.append({
            "id": node_id,
            "parent_id": parent_id,
            "position": key,
            "content": node.get("content", "") or "",
            "properties": json.dumps(props),
            "created_at": node.get("created_at") or now,
            "updated_at": now,
        })
        flatten_tree(node.get("children") or [], node_id, result, now)


def page_root_id(conn, block_id: str) -> str | None:
    """The page of the library that block_id is or lives in (its stored
    ``page_id``, one indexed read), or None when block_id is unknown, a
    reserved row, or in Recently deleted (a trashed page reads as gone)."""
    row = conn.execute(
        "SELECT p.id FROM unified_blocks b JOIN unified_blocks p ON p.id = b.page_id "
        "WHERE b.id = ? AND p.parent_id = 'root'", (block_id,)).fetchone()
    return row[0] if row else None


def is_op_page(conn, page_id: str) -> bool:
    """Whether an op batch may name ``page_id`` as its page: a page of the
    library, or a pseudo-page (``TREES``) whose reserved row the file has."""
    row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    return bool(row) and (row[0] == "root" or (row[0] is None and page_id in TREES))


def batch_page(conn, block_id: str) -> str | None:
    """The page an op batch on ``block_id`` names: ``page_root_id``'s page,
    or the pseudo-page (``TREES``) the block is or lives in — a folder or
    label block, or the reserved row itself. None as for ``page_root_id``."""
    row = conn.execute("SELECT parent_id, page_id FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
    if not row:
        return None
    if block_id in TREES and row[0] is None:
        return block_id
    return row[1] if row[1] in TREES else page_root_id(conn, block_id)


def ensure_reserved(conn, block_id: str) -> bool:
    """Write the reserved parentless row ``block_id`` (``trash``, ``folders``
    or ``labels``; ``root`` is the workspace's first row) unless it is there.
    False when some other block holds the id: it is left alone, and what
    needs the row refuses (``ops.trash_page``; a batch on a pseudo-page).
    The caller commits."""
    row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
    if row is None:
        now = page_now()
        conn.execute(
            "INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, updated_at) "
            "VALUES (?, NULL, 'a1', '', '{}', ?, ?)", (block_id, now, now))
        return True
    return row[0] is None


def trash_entry(conn, block_id: str) -> dict | None:
    """The Recently deleted entry of the page ``block_id`` is or lives in
    (``trashed_page``'s shape), or None when the block is not in the trash:
    a trashed page and its blocks keep their ``page_id``."""
    row = conn.execute(
        "SELECT p.id, p.content, p.properties FROM unified_blocks b JOIN unified_blocks p ON p.id = b.page_id "
        "WHERE b.id = ? AND p.parent_id = ?", (block_id, TRASH)).fetchone()
    return trashed_page(*row) if row else None


def trashed_page(page_id: str, title: str, props_raw) -> dict:
    """A trashed page as Recently deleted lists it: ``{id, title, folders,
    deleted_at, deleted_by}`` — ``folders`` the folder ids it was filed in
    (a restore puts it back there), the stamps ``ops.trash_page`` wrote."""
    try:
        props = json.loads(props_raw or "{}")
    except ValueError:
        props = {}
    return {"id": page_id, "title": title or "Untitled", "folders": filing(props, FOLDERS),
            "deleted_at": str(props.get("deleted_at") or ""),
            "deleted_by": str(props.get("deleted_by") or "")}


def assert_block_in_scope(conn, block_id: str, scope) -> None:
    """For a share-scoped request (``scope`` an auth.ShareScope), raise 403
    unless block_id is a page the share reaches or lives inside one. No-op
    for full-access session users (scope is None)."""
    if scope is None:
        return
    from fastapi import HTTPException

    if not scope.allows_block(conn, block_id):
        raise HTTPException(status_code=403, detail="not accessible via this share link")


def ancestor_chains(conn, block_ids: list[str]):
    """Return {block_id: [{id, content}, ...]} ancestor chains (root-first, excluding 'root')
    and {block_id: page_root_id} for a set of blocks, in one recursive CTE."""
    if not block_ids:
        return {}, {}
    placeholders = ",".join("?" * len(block_ids))
    rows = conn.execute(
        f"""
        WITH RECURSIVE chain AS (
            SELECT id AS descendant_id, parent_id, 0 AS depth
            FROM unified_blocks WHERE id IN ({placeholders})
            UNION ALL
            SELECT c.descendant_id, u.parent_id, c.depth + 1
            FROM unified_blocks u
            JOIN chain c ON u.id = c.parent_id
            WHERE u.parent_id IS NOT NULL AND u.parent_id != 'root'
        )
        SELECT c.descendant_id, u.id, u.content, c.depth
        FROM chain c
        JOIN unified_blocks u ON u.id = c.parent_id
        ORDER BY c.descendant_id, c.depth DESC
        """,
        block_ids,
    ).fetchall()
    ancestors_by_id: dict = {}
    page_root_by_id: dict = {}
    for descendant_id, anc_id, anc_content, _depth in rows:
        if anc_id == "root":
            continue  # "root" is a virtual parent, not a real page
        ancestors_by_id.setdefault(descendant_id, []).append({"id": anc_id, "content": anc_content})
        if descendant_id not in page_root_by_id:
            page_root_by_id[descendant_id] = anc_id
    return ancestors_by_id, page_root_by_id


def page_attachment(props: dict | None) -> dict | None:
    """What a page carries: ``{"kind": "pdf", "id": doc_id, "url": source_url,
    "name": original_filename}`` when the page has a PDF attachment (a stored
    ``doc_id`` and/or a ``source_url`` the proxy fetches lazily), else None.
    ``url`` is where the file is fetched from: the stored ``source_url``,
    else the stored copy (``storage.pdf_url``) — a page stores no
    ``source_url`` that is its own ``doc_id``'s (``attachment_props``).

    The ONE place backend code reads ``doc_id``/``source_url`` off a page to
    decide what kind of page it is (gating, labels, listings) — so a later
    ``properties.attachments`` list is a drop-in. Lookups BY attachment
    (``by-doc``, the search index, the PDF export) still key on ``doc_id``
    directly; that is fine, they are about the file, not the page.
    Mirrored by ``pageAttachment()`` in frontend/src/library/libraryUtils.js."""
    props = props or {}
    doc_id = str(props.get("doc_id") or "")
    url = str(props.get("source_url") or "")
    if not doc_id and not url:
        return None
    return {"kind": "pdf", "id": doc_id, "url": url or pdf_url(doc_id),
            "name": str(props.get("original_filename") or "")}


def page_for_doc(conn, doc_id: str, columns: str = "id"):
    """Lookup BY ATTACHMENT: the row (``columns`` of it) of the root page
    whose PDF attachment is ``doc_id``, or None. The one query for "which
    page carries this PDF" — root pages only, a nested block never counts.
    Should a workspace hold two (an older copy of the data), the oldest
    answers, every time."""
    if not doc_id:
        return None
    return conn.execute(
        f"SELECT {columns} FROM unified_blocks WHERE doc_id = ? AND parent_id = 'root' "
        "ORDER BY created_at, id LIMIT 1", (doc_id,)).fetchone()


def write_lock(conn) -> None:
    """Take the workspace's write lock now (``BEGIN IMMEDIATE``) unless the
    connection is inside a transaction already, so a writer's checks and
    its writes run as one step: two requests racing to create the same page
    (a double-clicked clip, two tabs) find one page instead of making two,
    and op batches never share a seq. The next commit (or the connection's
    ``with`` block) releases it."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def touch_page(conn, page_id: str, actor: str, kind: str = "live", *, now: str = "") -> None:
    """Record a write to the page ``page_id`` in the workspace's change log
    (``page_changes``): its row takes the next seq of the workspace, so the
    change feed (routers/sync.py) lists the page once to any cursor below
    it. ``live`` (an edit, a creation, a restore) also stamps the page's
    own row ``updated_at`` (``now``, the writer's stamp, or the time); a
    page that went (``deleted``: moved to Recently deleted, or deleted for
    good) keeps its row as it is. Every writer of a page calls it once per
    write, inside its transaction: under the write lock (``write_lock``, or
    the transaction's first write), so seqs are handed out in commit order
    and never twice. The caller commits."""
    now = now or page_now()
    if kind == "live":
        conn.execute("UPDATE unified_blocks SET updated_at = ? WHERE id = ?", (now, page_id))
    conn.execute(
        "INSERT INTO page_changes (page_id, seq, kind, at, actor) "
        "VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM page_changes), ?, ?, ?) "
        "ON CONFLICT (page_id) DO UPDATE SET seq = excluded.seq, kind = excluded.kind, "
        "at = excluded.at, actor = excluded.actor",
        (page_id, kind, now, actor))


# --- folders and labels ---------------------------------------------------------
#
# Membership is read live from the pages' ``properties.folders``: "filed in F
# or below" is "carries F or a folder below it". An id whose block is gone
# (a dangling id: its folder deleted meanwhile, or not brought by a mirror
# yet) is passed by on every read, and a writer that refiles a page keeps
# only the ids that exist (``existing_in``), so it goes with the page's next
# refiling; nothing repairs it on a read, and the op path stores a filing as
# it is written (a copy of a page must not lose an id its tree lacks).

def filing(props: dict | None, tree: str) -> list[str]:
    """The block ids a page's properties file it under in ``tree``
    (``FOLDERS`` → ``properties.folders``, ``LABELS`` → ``properties.labels``),
    in their stored order; [] when it has none."""
    ids = (props or {}).get(tree)
    return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []


def filing_ids(value) -> list[str] | None:
    """A written ``folders`` / ``labels`` value without repeats, or None
    when it is no list of block ids."""
    if not isinstance(value, list) or not all(isinstance(i, str) and BLOCK_ID_RE.match(i) for i in value):
        return None
    return list(dict.fromkeys(value))


def existing_in(conn, tree: str, ids) -> list[str]:
    """``ids`` (a list of block ids; anything else counts as none) without
    repeats and without the ones that are no block of ``tree`` — what a
    writer that refiles a page keeps."""
    wanted = list(dict.fromkeys(i for i in ids if isinstance(i, str))) if isinstance(ids, list) else []
    if not wanted:
        return []
    have = {r[0] for r in conn.execute(
        "SELECT id FROM unified_blocks WHERE page_id = ? AND id IN (SELECT value FROM json_each(?))",
        (tree, json.dumps(wanted)))}
    return [i for i in wanted if i in have]


# The folder ``?`` and every folder below it, as the CTE ``sub(id)``.
_SUBTREE = ("WITH RECURSIVE sub(id) AS (SELECT id FROM unified_blocks WHERE id = ? AND page_id = 'folders' "
            "UNION ALL SELECT b.id FROM unified_blocks b JOIN sub ON b.parent_id = sub.id) ")
# Whether the page row ``p`` carries one of them.
_FILED_IN_SUB = ("EXISTS (SELECT 1 FROM json_each(p.properties, '$.folders') f "
                 "WHERE f.value IN (SELECT id FROM sub))")


def folder_subtree_ids(conn, folder_id: str) -> set[str]:
    """The folder ``folder_id`` and every folder below it; empty when it is
    no folder."""
    return {r[0] for r in conn.execute(_SUBTREE + "SELECT id FROM sub", (folder_id,))}


def pages_in_folder(conn, folder_id: str) -> list[str]:
    """The library's pages filed in the folder ``folder_id`` or below it, in
    library order; none for an id that is no folder."""
    return [r[0] for r in conn.execute(
        _SUBTREE + f"SELECT p.id FROM unified_blocks p WHERE p.parent_id = 'root' AND {_FILED_IN_SUB} "
        "ORDER BY p.position", (folder_id,))]


def page_in_folder(conn, page_id: str, folder_id: str) -> bool:
    """Whether the library page ``page_id`` is filed in ``folder_id`` or below
    it (``pages_in_folder``'s rule, for one page)."""
    return conn.execute(
        _SUBTREE + f"SELECT 1 FROM unified_blocks p WHERE p.id = ? AND p.parent_id = 'root' AND {_FILED_IN_SUB}",
        (folder_id, page_id)).fetchone() is not None


def root_pages(conn, folder: str = "") -> dict:
    """{page_id: {"title", "doc_id"}} for the root pages a library-wide
    operation reaches: every page, or — with ``folder`` a folder id — the
    pages filed in that folder or below it (``pages_in_folder``; an id that
    is no folder reaches none). ``doc_id`` is "" for a page without a PDF."""
    if folder:
        rows = conn.execute(_SUBTREE + "SELECT p.id, p.content, p.doc_id FROM unified_blocks p "
                            f"WHERE p.parent_id = 'root' AND {_FILED_IN_SUB}", (folder,))
    else:
        rows = conn.execute("SELECT id, content, doc_id FROM unified_blocks WHERE parent_id = 'root'")
    return {page_id: {"title": content or "Untitled", "doc_id": doc_id or ""} for page_id, content, doc_id in rows}


def tree_rows(conn, tree: str) -> list[tuple]:
    """Every block of the folder or label tree ``tree`` as ``(id, parent_id,
    content, properties)``, parents before children, siblings in order."""
    rows = conn.execute("SELECT id, parent_id, content, properties FROM unified_blocks WHERE page_id = ? "
                        "ORDER BY position, id", (tree,)).fetchall()
    kids: dict = {}
    for r in rows:
        kids.setdefault(r[1], []).append(r)
    out, todo = [], list(reversed(kids.get(tree, [])))
    while todo:
        r = todo.pop()
        out.append(r)
        todo.extend(reversed(kids.get(r[0], [])))
    return out


def tree_children(conn, tree: str) -> dict[str, list[tuple[str, str]]]:
    """``{parent id: [(id, name)]}`` for the blocks of ``tree``, siblings in
    order; the tree's own id keys its top level."""
    kids: dict = {}
    for block_id, parent, name, _props in tree_rows(conn, tree):
        kids.setdefault(parent, []).append((block_id, name or ""))
    return kids


def tree_parents(conn, tree: str) -> dict[str, str]:
    """``{id: parent id}`` for the blocks of ``tree``."""
    return {block_id: parent for block_id, parent, _name, _props in tree_rows(conn, tree)}


def split_path(text: str) -> list[str]:
    """The folder names of a path written with ``/`` (blank names left out)."""
    return [n.strip() for n in text.split("/") if n.strip()]


def folder_paths(conn) -> dict[str, list[str]]:
    """{folder id: its names from the top folder down}, for every folder in
    tree order — what exports write as directories and what the agent and
    a person read (joined with ``PATH_SEP``)."""
    paths: dict = {}
    for folder_id, parent, name, _props in tree_rows(conn, FOLDERS):
        paths[folder_id] = [*paths.get(parent, []), name or ""]
    return paths


def _chain(conn, folder_id: str) -> list[tuple]:
    """``(id, name)`` of the folders from the top down to ``folder_id``."""
    return conn.execute(
        "WITH RECURSIVE up(id, parent_id, content, depth) AS (SELECT id, parent_id, content, 0 FROM unified_blocks "
        "WHERE id = ? AND page_id = 'folders' UNION ALL SELECT b.id, b.parent_id, b.content, up.depth + 1 "
        "FROM unified_blocks b JOIN up ON b.id = up.parent_id WHERE b.page_id = 'folders') "
        "SELECT id, content FROM up ORDER BY depth DESC", (folder_id,)).fetchall()


def folder_path(conn, folder_id: str) -> list[str]:
    """The names from the top folder down to ``folder_id``; [] when it is no
    folder."""
    return [name or "" for _id, name in _chain(conn, folder_id)]


def folder_chain(conn, folder_id: str) -> list[str]:
    """The ids from the top folder down to ``folder_id`` itself (its
    ancestors, then it); [] when it is no folder."""
    return [folder for folder, _name in _chain(conn, folder_id)]


def refiled_paths(paths: list[list[str]], path: list[str]) -> list[list[str]]:
    """``refiled`` by names, for a plan whose folders may not exist yet
    (the Zotero import's preview): the paths a page ends in when filed
    into ``path`` on top of ``paths``."""
    return [p for p in paths if p != path[:len(p)]] + [path]


def refiled(conn, folders: list[str], folder_id: str) -> list[str]:
    """The folders a page ends in when it is filed into ``folder_id`` ("" =
    none) on top of ``folders``: a folder above the new one gives way —
    filing into a subfolder refines, it does not leave the page in both
    levels."""
    if not folder_id:
        return folders
    above = set(folder_chain(conn, folder_id))
    return [f for f in folders if f not in above] + [folder_id]


def label_names(conn) -> dict[str, str]:
    """{label id: name} for every label, in order."""
    return {r[0]: r[2] or "" for r in tree_rows(conn, LABELS)}


def named(children: list, name: str) -> list:
    """The children (``(id, name)`` pairs) called ``name``: exactly, else
    ignoring case. ``lower()``, not ``casefold()``: the web app
    (``findNamed``) and the extension (``folderByPath``) compare with
    JavaScript's ``toLowerCase()``, and the three must agree on which
    folder a typed path names ("Straße" is not "STRASSE" to any of them)."""
    name = name.strip()
    exact = [c for c in children if c[1].strip() == name]
    return exact or [c for c in children if c[1].strip().lower() == name.lower()]


def descend(kids: dict, start: str, names) -> tuple[list[str], list[str]]:
    """Walk ``names`` down ``kids`` (``tree_children``) from the block
    ``start``, each name matched as ``named`` does: ``(ids at the deepest
    level reached — several when siblings share a name —, the names left
    when none matched)``."""
    level = [start]
    for i, name in enumerate(names):
        found = [c[0] for parent in level for c in named(kids.get(parent, []), name)]
        if not found:
            return level, list(names[i:])
        level = found
    return level, []


def folder_by_path(conn, names: list[str]) -> list[str]:
    """The folders whose path is ``names`` (from the top, each matched
    exactly, else ignoring case) — one id, none, or several when siblings
    share a name (the caller reports the ambiguity)."""
    level, rest = descend(tree_children(conn, FOLDERS), FOLDERS, names)
    return level if names and not rest else []


def label_by_name(conn, name: str) -> list[str]:
    """The labels called ``name`` (exactly, else ignoring case)."""
    return [c[0] for c in named(tree_children(conn, LABELS).get(LABELS, []), name)]


def new_block_id() -> str:
    """A fresh block id, of the shape clients mint."""
    return secrets.token_urlsafe(9)


def folder_inserts(conn, paths, under: str = "") -> tuple[list[dict], dict[tuple, str]]:
    """What filing pages under ``paths`` (each a list of names, from the top
    or from the folder ``under``, "" = the top) needs: the ``insert`` ops for the
    ``FOLDERS`` pseudo-page that make every path exist — a folder already
    there is reused (the first of several siblings that share a name), a new
    one goes last among its siblings — and ``{tuple(names): folder id}`` for
    each path. Apply the ops (``ops.apply_ops``) before writing the ids on a
    page."""
    kids = tree_children(conn, FOLDERS)
    ops, ids = [], {}
    for path in paths:
        path = [n.strip() for n in path if n and n.strip()]
        parent = under or FOLDERS
        for name in path:
            found = named(kids.get(parent, []), name)
            if found:
                parent = found[0][0]
                continue
            folder_id = new_block_id()
            ops.append({"op": "insert", "id": folder_id, "parent": parent, "content": name})
            kids.setdefault(parent, []).append((folder_id, name))
            parent = folder_id
        if path:
            ids[tuple(path)] = parent
    return ops, ids


def label_inserts(conn, names) -> tuple[list[dict], dict[str, str]]:
    """``folder_inserts`` for labels: the ``insert`` ops for the ``LABELS``
    pseudo-page that make each name a label (an existing one reused) and
    ``{name: label id}``."""
    have = tree_children(conn, LABELS).get(LABELS, [])
    ops, ids = [], {}
    for name in names:
        name = name.strip()
        if not name or name in ids:
            continue
        found = named(have, name)
        if found:
            ids[name] = found[0][0]
            continue
        label_id = new_block_id()
        ops.append({"op": "insert", "id": label_id, "parent": LABELS, "content": name})
        have.append((label_id, name))
        ids[name] = label_id
    return ops, ids


def attachment_props(doc_id: str, source_url: str = "", original_filename: str = "") -> tuple[dict, str]:
    """What attaching a PDF writes on a page: ``({doc_id?, source_url?,
    original_filename?}, automatic title)``. The title is the upload's file
    name (its leaf — a browser may leak a relative path), else the URL's
    file name, else the doc id; callers store it as ``auto_title`` too, the
    compare-and-swap marker metadata_fetch replaces (an explicit rename
    clears it first — ub_update_block). Both creation paths (by-doc ingest,
    POST /pages/{id}/attachment) go through here so the rules match. A
    ``source_url`` that is the stored copy of ``doc_id`` is not stored: the
    page's attachment derives it (``page_attachment``)."""
    original = display_filename(original_filename)
    props = {}
    if doc_id:
        props["doc_id"] = doc_id
    if source_url and source_url != pdf_url(doc_id):
        props["source_url"] = source_url
    if original:
        props["original_filename"] = original
    return props, original or url_filename(source_url) or doc_id


def create_page(conn, title: str, props: dict | None = None, *, actor: str,
                block_id: str = "", position: str = "") -> dict:
    """Insert a new root page and return its block dict. Commits. Last in
    the library unless ``position`` (a sibling key the caller minted) says
    otherwise — re-keyed like an op's when another page holds it
    (``free_position``); ``block_id`` reuses an id (a page brought back) — its
    row of the change log turns live again, and a copy of it in
    Recently deleted gives way, as it would to a hard delete and a
    re-create (a mirror bringing the page back with its own tree; files
    only that copy held are left to upload_gc's ``reconcile`` pass).
    ``properties.folders`` / ``labels`` are kept when they are lists of
    ids (``filing_ids``), else dropped, and a ``source_url`` that is the
    ``doc_id``'s stored copy is not stored (``page_attachment`` derives it).
    ``actor`` makes it (``touch_page``).
    The one code path that mints pages: POST /api/pages, POST /api/blocks
    (parent ``root``) and get_or_create_doc_page all go through it."""
    block_id = block_id or new_block_id()
    title = (title or "").strip() or "Untitled"
    props = dict(props or {})
    now = page_now()
    write_lock(conn)  # the free key and the insert as one step
    for tree in TREES:
        if tree in props:
            ids = filing_ids(props.pop(tree))
            if ids:
                props[tree] = ids
    if props.get("doc_id") and props.get("source_url") == pdf_url(props["doc_id"]):
        del props["source_url"]
    if conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = ?",
                    (block_id, TRASH)).fetchone():
        delete_subtree(conn, block_id)
        conn.execute("DELETE FROM page_ops WHERE page_id = ?", (block_id,))
    new_pos = free_position(conn, "root", position or None, block_id)
    conn.execute(
        f"INSERT INTO unified_blocks ({STORED_COLUMNS}) VALUES (?, 'root', ?, ?, ?, ?, ?, ?)",
        (block_id, new_pos, title, json.dumps(props), now, now, block_id),
    )
    touch_page(conn, block_id, actor, now=now)
    upload_gc.claim(conn, upload_refs(title, props))  # its PDF is in use (again)
    conn.commit()
    return block_to_dict(conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?",
                                      (block_id,)).fetchone())


def get_or_create_doc_page(conn, doc_id: str, default_title: str = "",
                           source_url: str | None = None,
                           original_filename: str | None = None,
                           folder: str = "", *, ws: str, actor: str = "") -> dict:
    """Lookup-or-create BY ATTACHMENT: the root page whose PDF attachment is
    `doc_id`, created under root when absent. Shared by POST /api/blocks/by-doc
    (PDF ingest from the app, "Open as document" on a PDF file block) and
    the extension's /api/clip (dedup). Pages without a PDF are created by
    POST /api/pages (create_page).

    The automatic title is the upload's file name, else ``default_title``
    (the caller's — a clip's tab title), else what ``attachment_props``
    derives (URL file name, doc id). ``folder`` (a folder id) files a NEW
    page; an existing page keeps its own. On an existing page this
    opportunistically backfills the source/filename markers (an op batch on
    the page, like every other write to an existing page — ``ws`` names the
    workspace, ``actor`` the account); auto_title is
    only set when the page still carries the exact title this call considers
    automatic, so a re-upload can never mark a user's custom title as
    replaceable by the metadata worker. Lookup and creation run under the
    write lock (``write_lock``): one page per PDF, however many requests
    ask at once. Commits."""
    attachment, auto = attachment_props(doc_id, source_url or "", original_filename or "")
    original = attachment.get("original_filename", "")
    title = original or (default_title or "").strip() or auto
    write_lock(conn)
    row = page_for_doc(conn, doc_id, BLOCK_COLUMNS)
    if row:
        props = json.loads(row[4] or "{}")
        changed = False
        for key in ("source_url", "original_filename"):
            if attachment.get(key) and not props.get(key):
                props[key] = attachment[key]
                changed = True
        if not props.get("auto_title") and row[3] == title:
            props["auto_title"] = row[3]
            changed = True
        if changed:
            from .ops import after_commit, apply_ops, props_patch  # ops imports this module
            patch = props_patch(json.loads(row[4] or "{}"), props)
            after_commit(ws, conn, apply_ops(conn, row[0], [{"op": "set", "id": row[0], "props": patch}],
                                             actor=actor))
            row = (*row[:4], json.dumps(props), *row[5:])
        else:
            conn.commit()  # nothing to write: let go of the lock
        return block_to_dict(row)
    props = {**attachment, "auto_title": title}
    if folder:
        props[FOLDERS] = [folder]
    return create_page(conn, title, props, actor=actor)


def pages_for_docs(conn, hashes) -> dict:
    """Which root page a stored file became, batched: ``{hash: {"id",
    "title"}}`` for every hash that is some page's PDF attachment
    (``doc_id``) or the markdown file a note page was made from
    (``markdown_import`` — the upload's content digest, so a ``<hash>.md``
    chip finds its page). Hashes with no page are absent. Feeds the file
    chip's "Open page" / "Open as page" — one query per page render, never
    one per chip. Two pages of one file: the oldest answers, as in
    ``page_for_doc``."""
    wanted = {str(d) for d in hashes if d}
    if not wanted:
        return {}
    found = {}
    for page_id, title, doc_id, md in conn.execute(
            "SELECT id, content, doc_id, json_extract(properties, '$.markdown_import') FROM unified_blocks "
            "WHERE parent_id = 'root' AND (doc_id IN (SELECT value FROM json_each(?1)) "
            "OR json_extract(properties, '$.markdown_import') IN (SELECT value FROM json_each(?1))) "
            "ORDER BY created_at, id", (json.dumps(sorted(wanted)),)):
        for key in (doc_id, md):
            if key in wanted and key not in found:
                found[key] = {"id": page_id, "title": title}
    return found
