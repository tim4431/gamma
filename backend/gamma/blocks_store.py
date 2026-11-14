"""unified_blocks table helpers shared across routers."""

import json
import re
import secrets

import orjson
from fractional_indexing import generate_key_between, generate_n_keys_between

from . import upload_gc
from .db import page_now
from .documents.model import (SQL_IN_PLACE, check_document, copy_document, document, document_props,
                              document_title, page_document, parse_upload_url, upload_url)
from .highlights import is_ink, is_link
from .storage import path_leaf, upload_refs
from .work_folders import filing as work_filing, marks  # as modules: marks imports this one
from .work_folders.library import SQL_LIBRARY_DOC, SQL_LIBRARY_PDF, SQL_LISTED, STAMPS, work_folder_of

# A block's stored columns, in ``block_to_dict``'s row order: what an
# insert writes and what a read selects. ``kind`` and ``doc_id`` are
# generated (db.BLOCK_HOT_COLUMNS; ``doc_id`` from the block's ``doc``,
# gamma/documents/model.py), so never written, and a read leaves
# ``kind`` to Python (``block_kind``): the generated column costs up to six
# JSON functions per row, and the dict has the parsed properties anyway (a
# 5,000-block tree read a third faster; docs/dev/api.md).
BLOCK_COLUMNS = "id, parent_id, position, content, properties, created_at, updated_at, page_id"
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

# The rows inside a page: a condition for the readers that scan blocks
# across pages (block search, backlinks), which pass by the reserved rows,
# the folder and label trees and Recently deleted. ``IN_PAGE`` reaches
# every live page, a work folder's files included; ``IN_LISTED`` the pages
# the library lists (gamma/work_folders/library.py ``listed``), for a list
# of what the library holds, such as the recent pages a link picker offers.
IN_PAGE = "page_id IN (SELECT id FROM unified_blocks WHERE parent_id = 'root')"
IN_LISTED = f"page_id IN (SELECT id FROM unified_blocks WHERE parent_id = 'root' AND {SQL_LISTED})"

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


def load_props(text) -> dict:
    """A stored ``properties`` column as an object (``load_json``): {} when
    it is empty, does not parse, or is no object."""
    try:
        props = load_json(text or "{}")
    except (TypeError, ValueError):
        return {}
    return props if isinstance(props, dict) else {}


def block_kind(parent_id, page_id, props: dict) -> str | None:
    """The block's ``kind``, by the rule of the generated column
    (db.BLOCK_HOT_COLUMNS: the SQL, this and the client's blockKind agree
    on tests/shared/blockkind.json's cases): NULL on the reserved rows,
    ``page`` under root or the trash, ``folder`` / ``label`` in the trees,
    else by the properties — ``ink`` (an ``ink_url`` key, whatever its
    value: ``highlights.is_ink``), ``text_box`` / ``picture`` / ``sheet``
    / ``recording`` (an object there), ``link`` (a ``link_url`` or
    ``link_page_id`` that is neither missing, null nor empty:
    ``highlights.is_link``), ``highlight`` (a ``pdf_position``,
    ``image_position`` or ``text_position`` object: a place on a PDF page,
    a region of a picture, a range of a text file), ``note``."""
    if parent_id is None:
        return None
    if parent_id in ("root", TRASH):
        return "page"
    if page_id == FOLDERS:
        return "folder"
    if page_id == LABELS:
        return "label"
    if not isinstance(props, dict):
        return "note"
    if is_ink(props):
        return "ink"
    if isinstance(props.get("text_box"), dict):
        return "text_box"
    if isinstance(props.get("picture"), dict):
        return "picture"
    if isinstance(props.get("sheet"), dict):
        return "sheet"
    if isinstance(props.get("recording"), dict):
        return "recording"
    if is_link(props):
        return "link"
    if any(isinstance(props.get(key), dict) for key in ("pdf_position", "image_position", "text_position")):
        return "highlight"
    return "note"


def block_to_dict(row) -> dict:
    """The API's block from a ``BLOCK_COLUMNS`` row. ``page_id`` and
    ``kind`` are read-only: no write takes them, the server derives both."""
    props = load_json(row[4] or "{}")
    return {
        "id": row[0],
        "parent_id": row[1],
        "position": row[2],
        "content": row[3] or "",
        "properties": props,
        "created_at": row[5],
        "updated_at": row[6],
        "page_id": row[7],
        "kind": block_kind(row[1], row[7], props),
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
    """Fetch a block + all its descendants: the block first, every parent
    before its children, siblings in no particular order. A page (or a
    tree, ``TREES``) is read by its ``page_id`` — one indexed read, every
    block of it carries the id — instead of the recursive walk down the
    parents, which copies each row once per level (several times faster on
    a page of thousands of highlights); the rows are then put in the walk's
    order from the page, which also leaves out a row the page's id reaches
    but no parent chain does. Other blocks walk."""
    root = conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
    if root is None:
        return []
    if root[1] in ("root", TRASH) or block_id in TREES:
        under = {}
        for row in conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE page_id = ? AND id != ?",
                                (block_id, block_id)):
            under.setdefault(row[1], []).append(row)
        out, i = [root], 0
        while i < len(out):
            out.extend(under.pop(out[i][0], ()))
            i += 1
        return out
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
    """Recursively flatten a nested block tree into flat rows with
    fractional positions. A row's ``content`` and ``properties`` are the
    node's as given (``{}`` when it has none): the caller checks their
    types and serializes the properties."""
    n = len(tree or [])
    if n == 0:
        return
    keys = generate_n_keys_between(None, None, n=n)
    for node, key in zip(tree, keys):
        props = node.get("properties")
        node_id = node.get("id") or new_block_id()
        result.append({
            "id": node_id,
            "parent_id": parent_id,
            "position": key,
            "content": node.get("content", "") or "",
            "properties": {} if props is None else props,
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
    a trashed page and its blocks keep their ``page_id``. For a page that
    went there with another (``deleted_with``, ops.trash_page) still in the
    trash, that one's entry: Recently deleted lists it, and its restore
    brings the whole tree back."""
    row = conn.execute(
        "SELECT p.id, p.content, p.properties FROM unified_blocks b JOIN unified_blocks p ON p.id = b.page_id "
        "WHERE b.id = ? AND p.parent_id = ?", (block_id, TRASH)).fetchone()
    if not row:
        return None
    went_with = conn.execute(
        "SELECT id, content, properties FROM unified_blocks WHERE id = json_extract(?, '$.deleted_with') "
        "AND parent_id = ?", (row[2], TRASH)).fetchone()
    return trashed_page(conn, *(went_with or row))


def trashed_page(conn, page_id: str, title: str, props_raw, *, memo: dict | None = None) -> dict:
    """A trashed page as Recently deleted lists it: ``{id, title, folders,
    parent, deleted_at, deleted_by, work_folder, deleted_by_vault,
    restorable?}`` — ``folders`` the folder ids a restore puts it back in
    (``trashed_folders``), ``parent`` the live page it goes back inside
    (``trashed_parent``, "" for none), the stamps ``ops.trash_page`` wrote,
    ``work_folder`` the work folder it is a file of ("" for none),
    ``deleted_by_vault`` whether its vault sent it there (its file deleted
    or untracked: only the file brings it back) and, for a file, whether a
    person's restore passes (``filing.may_restore``; the folder tree read
    once per ``memo``)."""
    props = load_props(props_raw)
    out = {"id": page_id, "title": title or "Untitled", "folders": trashed_folders(conn, props),
           "parent": trashed_parent(conn, props),
           "deleted_at": str(props.get("deleted_at") or ""),
           "deleted_by": str(props.get("deleted_by") or ""),
           "work_folder": work_folder_of(props),
           "deleted_by_vault": props.get("deleted_by_vault") is True}
    if out["work_folder"]:
        memo = {} if memo is None else memo
        if "tree" not in memo:
            memo["tree"] = marks.Tree.read(conn)
        try:
            work_filing.may_restore(memo["tree"], props, "person")
            out["restorable"] = True
        except work_filing.Refused:
            out["restorable"] = False
    return out


def _restored_props(conn, props: dict) -> dict:
    """The properties a restore of a page in Recently deleted (its
    ``props``) goes by: for a page that went there with another still there
    (``deleted_with``), that one's; else its own."""
    seen = set()
    while isinstance(went := props.get("deleted_with"), str) and went not in seen:
        seen.add(went)
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ? AND parent_id = ?",
                           (went, TRASH)).fetchone()
        if not row:
            break
        props = load_props(row[0])
    return props


def trashed_parent(conn, props: dict) -> str:
    """The page a page in Recently deleted (its ``props``) goes back inside
    when restored: its stored ``parent_page`` while that names a live page
    (a sub-page deleted on its own, ``ops.restore_page``), of the page it
    went there with for one that went with another still there; "" when it
    comes back in the library."""
    parent = _restored_props(conn, props).get(PARENT_PAGE)
    if isinstance(parent, str) and conn.execute(
            "SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'", (parent,)).fetchone():
        return parent
    return ""


def trashed_folders(conn, props: dict) -> list[str]:
    """The folder ids of a page in Recently deleted (its ``props``), where a
    restore puts it back: for a page that went there with another still
    there (``deleted_with``), that one's; for a sub-page whose parent is
    still live (``trashed_parent``), its parent's top's (``filed_folders``:
    a sub-page is filed where its top is); else the page's own."""
    parent = trashed_parent(conn, props)
    return filed_folders(conn, parent) if parent else filing(_restored_props(conn, props), FOLDERS)


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


def page_for_doc(conn, doc_id: str, columns: str = "id"):
    """Lookup BY DOCUMENT: the row (``columns`` of it) of the root page
    whose own PDF is ``doc_id`` (the generated column: a copy owns none),
    or None. The one query for "which page carries this PDF": root pages
    only, a nested block never counts, and a page that is no library
    document does not count either (gamma/work_folders/library.py
    ``SQL_LIBRARY_PDF``: an Opened files page, a work folder's file not in
    the library): a library PDF added never finds one, and the bytes it
    holds are no other page's to be refused. Should a workspace hold two
    (an older copy of the data), the oldest answers, every time."""
    if not doc_id:
        return None
    return conn.execute(
        f"SELECT {columns} FROM unified_blocks WHERE doc_id = ? AND parent_id = 'root' "
        f"AND {SQL_LIBRARY_PDF} ORDER BY created_at, id LIMIT 1",
        (doc_id,)).fetchone()


def page_for_file(conn, hash: str, ext: str, columns: str = "id"):
    """Lookup BY STORED FILE, for a document of any format: the row
    (``columns`` of it) of the root page whose own document is the stored
    file ``hash + ext`` (``stored_name``) and that is a library document
    (``SQL_LIBRARY_DOC``), or None. One library page per stored file, keyed
    by hash and extension: the same bytes uploaded as ``a.txt`` and
    ``a.log`` are two stored files, and two pages. A ``.pdf`` is
    ``page_for_doc``'s. The attach 409, Add to library's, the op path's
    library check, the duplicate rule and the file chips' lookup read it."""
    if ext == ".pdf":
        return page_for_doc(conn, hash, columns)
    if not hash or not ext:
        return None
    return conn.execute(
        f"SELECT {columns} FROM unified_blocks WHERE doc_id = ? AND parent_id = 'root' AND {SQL_LIBRARY_DOC} "
        "AND json_extract(properties, '$.doc.ext') = ? ORDER BY created_at, id LIMIT 1",
        (hash, ext)).fetchone()


def second_owner_as_copy(conn, props: dict) -> dict:
    """``props`` of a new page a client writes whole (POST /api/blocks at
    root: a duplicate, an open tab's from before the doc property among
    them), checked already (``document_props``), with a ``doc`` that would
    make the page a second owner of a file stored as the copy a duplicate
    writes (``copy_document``): one whose stored file a library page owns
    already (``page_for_file``, any format), and one read in place whose
    file another page reads in place (whose reader is that page's:
    gamma/linked_files.py). The original stays the one page of its
    document."""
    doc = props.get("doc") if isinstance(props, dict) else None
    if not isinstance(doc, dict) or doc.get("copy") or not doc["hash"]:
        return props
    if doc["at"] == "path":
        held = conn.execute("SELECT 1 FROM unified_blocks WHERE doc_id = ? AND parent_id = 'root' "
                            f"AND {SQL_IN_PLACE}", (doc["hash"],)).fetchone()
    else:
        held = page_for_file(conn, doc["hash"], doc["ext"])
    return {**props, "doc": copy_document(doc)} if held else props


def write_lock(conn) -> None:
    """Take the workspace's write lock now (``BEGIN IMMEDIATE``) unless the
    connection is inside a transaction already, so a writer's checks and
    its writes run as one step: two requests racing to create the same page
    (a double-clicked clip, two tabs) find one page instead of making two,
    and op batches never share a seq. The next commit (or the connection's
    ``with`` block) releases it."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def touch_page(conn, page_id: str, actor: str, kind: str = "live", *, now: str = "", quiet: bool = False,
               follow: bool = True) -> None:
    """Record a write to the page ``page_id`` in the workspace's change log
    (``page_changes``): its row takes the next seq of the workspace, so the
    change feed (routers/sync.py) lists the page once to any cursor below
    it. ``live`` (an edit, a creation, a restore) also stamps the page's
    own row ``updated_at`` (``now``, the writer's stamp, or the time),
    unless ``quiet`` (a vault's stamps, which leave the recent pages'
    order: ops ``set``'s ``quiet``), and the writer follows the page
    (``follow_page``) unless ``follow`` is False (a batch that is no one's
    keystrokes: ops' ``UNFOLLOWED`` clients) or the write is quiet; a page
    that went (``deleted``: moved to Recently deleted, or deleted for good)
    keeps its row as it is. Every writer of a page calls it once per write,
    inside its transaction: under the write lock (``write_lock``, or the
    transaction's first write), so seqs are handed out in commit order and
    never twice. The caller commits."""
    now = now or page_now()
    if kind == "live" and not quiet:
        conn.execute("UPDATE unified_blocks SET updated_at = ? WHERE id = ?", (now, page_id))
        if follow:
            follow_page(conn, page_id, actor, now=now)
    conn.execute(
        "INSERT INTO page_changes (page_id, seq, kind, at, actor) "
        "VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM page_changes), ?, ?, ?) "
        "ON CONFLICT (page_id) DO UPDATE SET seq = excluded.seq, kind = excluded.kind, "
        "at = excluded.at, actor = excluded.actor",
        (page_id, kind, now, actor))


def follow_page(conn, page_id: str, actor: str, *, now: str = "") -> None:
    """The person ``actor`` follows the page ``page_id``: every comment on
    it reaches their Inbox (``page_follows`` level ``all``,
    docs/dev/comments.md "Following a page"), unless the page has their
    row already — their own choice, or an earlier follow. Called inside the
    writer's transaction by ``touch_page`` (a creation, an edit) and by a
    new comment. A writer that is no account (the server's own ``''``, a
    link visitor, a mirror's or a Zotero link's round) and the folder and
    label trees follow nothing."""
    from .auth import LINK_ACTOR_PREFIX, MIRROR_ACTOR, ZOTERO_ACTOR  # local: auth imports this module

    if (not actor or actor in (MIRROR_ACTOR, ZOTERO_ACTOR) or actor.startswith(LINK_ACTOR_PREFIX)
            or page_id in RESERVED):
        return
    conn.execute("INSERT OR IGNORE INTO page_follows (page_id, user_id, level, at) VALUES (?, ?, 'all', ?)",
                 (page_id, actor, now or page_now()))


def _followed_by(client: str) -> bool:
    """Whether a page the writer ``client`` names makes is followed by its
    actor: not one a batch of it would leave unfollowed (ops'
    ``UNFOLLOWED``: a synced vault's file, a mirror's page)."""
    from .ops import UNFOLLOWED  # ops imports this module

    return client not in UNFOLLOWED


def newest_change_seq(conn) -> int:
    """The change log's newest seq (0 for an empty log): the cursor a
    consumer in step with the workspace holds."""
    return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM page_changes").fetchone()[0]


# --- sub-pages --------------------------------------------------------------------
#
# A sub-page is a page of the library whose ``properties.parent_page`` names
# the page it is inside (the generated column ``parent_page``, indexed over
# the rows that carry it); nothing is stored on the parent, and every reader
# keyed on a page treats a sub-page as the page it is. Its *effective*
# parent is the stored one when that is a live page and the chain of such
# parents from it reaches a page without one, holding at most
# MAX_PAGE_DEPTH pages and none twice. Otherwise — the parent deleted for
# good or not brought by a mirror yet, in Recently deleted, no page, or a
# cycle (which only merged copies can make) — it reads as a library page,
# as a page filed in a deleted folder reads as unfiled. Its ancestors are
# that chain, top first; its top is the first of them (a library page is
# its own top), and its folders are its top's. Every reader goes through
# the helpers below.

PARENT_PAGE = "parent_page"
MAX_PAGE_DEPTH = 64  # the pages a chain holds, from its top down to the deepest sub-page

# The live stored parent of the live page ``?``: what one level of a walk up reads.
_LIVE_PARENT = ("SELECT c.parent_page FROM unified_blocks c JOIN unified_blocks p ON p.id = c.parent_page "
                "AND p.parent_id = 'root' WHERE c.id = ? AND c.parent_id = 'root'")


def valid_parent_value(value, page_id: str = "") -> bool:
    """Whether ``value`` has the shape of a ``parent_page`` of the page
    ``page_id``: a block id that is none of the reserved ones
    (``valid_block_id``) and not the page itself. Whether it names a page
    is not asked: a dangling one reads as no parent."""
    return valid_block_id(value) and value != page_id


def page_parents(conn) -> dict[str, str]:
    """``{sub-page id: its effective parent}`` for every live sub-page of the
    workspace, in library order (``position``, then the id): one read of the
    rows whose stored parent is a live page, then the chain rule — a page
    in a cycle, below one or deeper than MAX_PAGE_DEPTH, is left out. The
    read walks ``idx_ub_parent_page`` (the ``+`` keeps the planner off
    ``idx_ub_parent``, which would read every root row, a work folder's
    files included)."""
    stored = dict(conn.execute(
        "SELECT c.id, c.parent_page FROM unified_blocks c CROSS JOIN unified_blocks p ON p.id = c.parent_page "
        "AND p.parent_id = 'root' WHERE +c.parent_id = 'root' AND c.parent_page IS NOT NULL "
        "ORDER BY c.position, c.id").fetchall())
    depth: dict[str, int | None] = {}  # a page's ancestors, counted; None when its chain is unsound
    for start in stored:
        walk, on_walk, cur = [], set(), start
        while cur in stored and cur not in depth and cur not in on_walk:
            walk.append(cur)
            on_walk.add(cur)
            cur = stored[cur]
        below = None if cur in on_walk else depth.get(cur, 0)  # back on the walk: a cycle
        for page in reversed(walk):
            below = below + 1 if below is not None and below < MAX_PAGE_DEPTH - 1 else None
            depth[page] = below
    return {page: parent for page, parent in stored.items() if depth[page] is not None}


def _page_chain(conn, page_id: str) -> list[str] | None:
    """The ancestors of the live page ``page_id``, top first ([] for a
    library page), walked up one indexed read per level; None when it is no
    live page or its chain is unsound (``page_parents``' rule)."""
    if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'", (page_id,)).fetchone():
        return None
    chain, cur = [], page_id
    while row := conn.execute(_LIVE_PARENT, (cur,)).fetchone():
        cur = row[0]
        if cur == page_id or cur in chain or len(chain) == MAX_PAGE_DEPTH - 1:
            return None
        chain.append(cur)
    return chain[::-1]


def page_ancestors(conn, page_id: str) -> list[str]:
    """The effective ancestors of ``page_id``, top first, down to its
    parent; [] for a library page or a page that is not live."""
    return _page_chain(conn, page_id) or []


def page_parent(conn, page_id: str) -> str:
    """The effective parent of ``page_id`` (``page_parents``' answer for one
    page); '' for a library page or a page that is not live."""
    chain = page_ancestors(conn, page_id)
    return chain[-1] if chain else ""


def page_top(conn, page_id: str) -> str:
    """The first ancestor of ``page_id``, the page whose folders it is
    filed in; the page itself when it has none."""
    chain = page_ancestors(conn, page_id)
    return chain[0] if chain else page_id


def top_of(parents: dict[str, str], page_id: str) -> str:
    """``page_top`` read from a ``page_parents`` map already in hand."""
    while page_id in parents:
        page_id = parents[page_id]
    return page_id


def filed_folders(conn, page_id: str) -> list[str]:
    """The folder ids the page ``page_id`` is filed in: its top's
    ``folders`` (a library page's own, and a page that is not live its
    own)."""
    row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_top(conn, page_id),)).fetchone()
    return filing(load_props(row[0]) if row else {}, FOLDERS)


def sub_pages(conn, page_id: str) -> list[str]:
    """The pages right inside ``page_id``, in library order; [] when it is
    no live page, its own chain is unsound, or it is MAX_PAGE_DEPTH deep
    already (what it holds reads as library pages)."""
    chain = _page_chain(conn, page_id)
    if chain is None or len(chain) == MAX_PAGE_DEPTH - 1:
        return []
    return [r[0] for r in conn.execute(
        "SELECT id FROM unified_blocks WHERE parent_page = ? AND parent_id = 'root' ORDER BY position, id",
        (page_id,))]


def with_sub_pages(conn, page_ids, parents: dict[str, str] | None = None, *, skip=()) -> list[str]:
    """``page_ids``, each followed by its descendants — parents before
    children, siblings in library order — without repeats. ``parents`` is
    a ``page_parents`` map already read. A page of ``skip`` is passed by
    with the pages inside it, unless it is one of ``page_ids`` (a page of
    ``page_ids`` inside it still comes, with what is inside that one)."""
    if parents is None:
        parents = page_parents(conn)
    kids: dict[str, list[str]] = {}
    for page, parent in parents.items():
        kids.setdefault(parent, []).append(page)
    roots = set(page_ids)
    out, seen = [], set()
    for page_id in page_ids:
        todo = [page_id]
        while todo:
            page = todo.pop()
            if page not in seen and (page in roots or page not in skip):
                seen.add(page)
                out.append(page)
                todo += reversed(kids.get(page, []))
    return out


def page_descendants(conn, page_id: str) -> list[str]:
    """Every page inside ``page_id``, its sub-pages' too, parents before
    children and siblings in library order; [] when it is no live page or
    its own chain is unsound."""
    return with_sub_pages(conn, [page_id])[1:]


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


def live_pages(conn, parents: dict[str, str] | None = None) -> list[str]:
    """Every live page of the workspace, a work folder's files included:
    the pages with no parent, in library order, each followed by its
    descendants (``with_sub_pages``). ``parents``: a ``page_parents`` map
    already read."""
    if parents is None:
        parents = page_parents(conn)
    rows = conn.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root' ORDER BY position, id")
    return with_sub_pages(conn, [r[0] for r in rows if r[0] not in parents], parents)


def pages_in_folder(conn, folder_id: str, parents: dict[str, str] | None = None) -> list[str]:
    """The pages filed in the folder ``folder_id`` or below it: the pages
    that carry it or a folder below it, in library order, each followed by
    its descendants (``with_sub_pages``; a sub-page is filed where its top
    is, whatever ``folders`` it carries); none for an id that is no
    folder (every live page is ``live_pages``). ``parents``: a
    ``page_parents`` map already read."""
    if parents is None:
        parents = page_parents(conn)
    rows = conn.execute(_SUBTREE + f"SELECT p.id FROM unified_blocks p WHERE p.parent_id = 'root' AND "
                        f"{_FILED_IN_SUB} ORDER BY p.position, p.id", (folder_id,))
    return with_sub_pages(conn, [r[0] for r in rows if r[0] not in parents], parents)


def page_in_folder(conn, page_id: str, folder_id: str) -> bool:
    """Whether the live page ``page_id`` is filed in ``folder_id`` or below
    it (``pages_in_folder``'s rule, for one page): whether its top is."""
    return conn.execute(
        _SUBTREE + f"SELECT 1 FROM unified_blocks p WHERE p.id = ? AND p.parent_id = 'root' AND {_FILED_IN_SUB}",
        (folder_id, page_top(conn, page_id))).fetchone() is not None


# The most page ids one search request may name (POST /block-search and
# /document-search: the pages the Ctrl+F panel's predicates keep), about
# 750 KB of body; 413 past it.
MAX_SEARCH_PAGES = 50_000


def pages_in_scope(conn, scope: list[str]) -> set[str]:
    """The library's pages inside every folder and label of ``scope`` (block
    ids: the ``scope`` of GET /block-search and GET /search, the
    ``folders`` and ``labels`` of the Ctrl+F panel's POSTs,
    routers/search.scope_pages): filed in the folder or below it
    (``pages_in_folder``: a sub-page by its top), carrying the label (each
    page its own). An id that is neither reaches none."""
    pages = None
    for chip in scope:
        row = conn.execute("SELECT page_id FROM unified_blocks WHERE id = ?", (chip,)).fetchone()
        if row and row[0] == FOLDERS:
            found = set(pages_in_folder(conn, chip))
        elif row and row[0] == LABELS:
            found = {r[0] for r in conn.execute(
                "SELECT p.id FROM unified_blocks p WHERE p.parent_id = 'root' AND EXISTS "
                "(SELECT 1 FROM json_each(p.properties, '$.labels') l WHERE l.value = ?)", (chip,))}
        else:
            found = set()
        pages = found if pages is None else pages & found
    return pages or set()


def root_pages(conn, folder: str = "") -> dict:
    """{page_id: {"title", "doc_id", "format", "ext", "at", "name"}} for the
    root pages a workspace-wide operation reaches: every live page,
    sub-pages and a work folder's files included, or — with ``folder`` a
    folder id — the pages filed in that folder or below it
    (``pages_in_folder``; an id that is no folder reaches none). ``doc_id``
    is the hash of the document the page owns, of any format (the generated
    column), "" for a page without one, a copy and a listed document;
    ``format``, ``ext``, ``at`` and ``name`` its document's, "" without one
    (a copy's are its file's)."""
    columns = ("id, content, doc_id, json_extract(properties, '$.doc.format'), "
               "json_extract(properties, '$.doc.ext'), json_extract(properties, '$.doc.at'), "
               "json_extract(properties, '$.doc.name')")
    if folder:
        rows = conn.execute(f"SELECT {columns} FROM unified_blocks WHERE id IN "
                            "(SELECT value FROM json_each(?))", (json.dumps(pages_in_folder(conn, folder)),))
    else:
        rows = conn.execute(f"SELECT {columns} FROM unified_blocks WHERE parent_id = 'root'")
    return {page_id: {"title": content or "Untitled", "doc_id": doc_id or "", "format": format or "",
                      "ext": ext or "", "at": at or "", "name": name or ""}
            for page_id, content, doc_id, format, ext, at, name in rows}


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


def _children(rows) -> dict[str, list[tuple[str, str]]]:
    kids: dict = {}
    for block_id, parent, name, _props in rows:
        kids.setdefault(parent, []).append((block_id, name or ""))
    return kids


def tree_children(conn, tree: str) -> dict[str, list[tuple[str, str]]]:
    """``{parent id: [(id, name)]}`` for the blocks of ``tree``, siblings in
    order; the tree's own id keys its top level."""
    return _children(tree_rows(conn, tree))


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


def _passing_by(children: list, works) -> list:
    return [c for c in children if c[0] not in works] if works else children


def descend(kids: dict, start: str, names, *, works=()) -> tuple[list[str], list[str]]:
    """Walk ``names`` down ``kids`` (``tree_children``) from the block
    ``start``, each name matched as ``named`` does: ``(ids at the deepest
    level reached — several when siblings share a name —, the names left
    when none matched)``. The folders of ``works`` (work folders' roots,
    gamma/work_folders/marks.py ``Tree.roots``) are passed by with all
    below them, as if they were not there: a name that files library pages
    never names a work folder's directory."""
    level = [start]
    for i, name in enumerate(names):
        found = [c[0] for parent in level for c in named(_passing_by(kids.get(parent, []), works), name)]
        if not found:
            return level, list(names[i:])
        level = found
    return level, []


def folder_by_path(conn, names: list[str], *, into_work: bool = False) -> list[str]:
    """The folders whose path is ``names`` (from the top, each matched
    exactly, else ignoring case) — one id, none, or several when siblings
    share a name (the caller reports the ambiguity). A work folder and its
    directories are passed by unless ``into_work``."""
    rows = tree_rows(conn, FOLDERS)
    works = () if into_work else set(marks.Tree.of(rows).roots())
    level, rest = descend(_children(rows), FOLDERS, names, works=works)
    return level if names and not rest else []


def label_by_name(conn, name: str) -> list[str]:
    """The labels called ``name`` (exactly, else ignoring case)."""
    return [c[0] for c in named(tree_children(conn, LABELS).get(LABELS, []), name)]


def new_block_id() -> str:
    """A fresh block id, of the shape clients mint."""
    return secrets.token_urlsafe(9)


def folder_inserts(conn, paths, under: str = "", kids=None, *,
                   into_work: bool = False) -> tuple[list[dict], dict[tuple, str]]:
    """What filing pages under ``paths`` (each a list of names, from the top
    or from the folder ``under``, "" = the top) needs: the ``insert`` ops for the
    ``FOLDERS`` pseudo-page that make every path exist — a folder already
    there is reused (the first of several siblings that share a name), a new
    one goes last among its siblings — and ``{tuple(names): folder id}`` for
    each path. Apply the ops (``ops.apply_ops``) before writing the ids on a
    page. A work folder is passed by, with its directories (``descend``'s
    ``works``): an import, a clip or the agent filing by name makes a
    library folder beside one of the same name. The vault, filing its
    files in its directories, walks from its work folder with
    ``into_work``. ``kids``: the tree as ``tree_children`` reads it, from
    another server's answer (gamma/vault/http_remote.py), in place of
    ``conn``'s."""
    works: set = set()
    if kids is None:
        rows = tree_rows(conn, FOLDERS)
        kids = _children(rows)
        if not into_work:
            works = set(marks.Tree.of(rows).roots())
    ops, ids = [], {}
    for path in paths:
        path = [n.strip() for n in path if n and n.strip()]
        parent = under or FOLDERS
        for name in path:
            found = named(_passing_by(kids.get(parent, []), works), name)
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


def label_inserts(conn, names, have=None) -> tuple[list[dict], dict[str, str]]:
    """``folder_inserts`` for labels: the ``insert`` ops for the ``LABELS``
    pseudo-page that make each name a label (an existing one reused) and
    ``{name: label id}``; ``have``, the labels as ``(id, name)``, in place of
    ``conn``'s."""
    have = list(tree_children(conn, LABELS).get(LABELS, []) if have is None else have)
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


def page_root_props(props: dict | None, page_id: str = "", *, writer: str = "person") -> dict:
    """A copy of ``props`` as the new page row ``page_id`` stores them:
    ``folders`` / ``labels`` kept when they are lists of ids
    (``filing_ids``, without repeats), else dropped; ``parent_page`` kept
    when it has a parent's shape (``valid_parent_value``), else dropped —
    whether it names a page is not asked, since a mirror may bring a
    sub-page before its parent; the flat keys a document was kept in
    (``LEGACY_KEYS``) dropped, and a ``doc`` kept in canonical form when it
    checks, else dropped (``documents.model.document_props``); a work
    folder's stamps (``work_folder``, ``library``, ``file_name``) dropped
    unless ``writer`` (gamma/work_folders/filing.py ``writer_of``) is the
    vault or the mirror, since an import or a duplicate carries a file's
    properties as it found them. The page writers (``create_page``,
    ``insert_page_tree``) apply it; the op path holds a page's patch to the
    same rules (ops._filing_patch, ops._parent_patch, ops._document_patch,
    ops._Batch.work_patch), refusing what this drops."""
    props = dict(props or {})
    for tree in TREES:
        if tree in props:
            ids = filing_ids(props.pop(tree))
            if ids:
                props[tree] = ids
    if PARENT_PAGE in props and not valid_parent_value(props[PARENT_PAGE], page_id):
        del props[PARENT_PAGE]
    if writer == "person":
        for key in STAMPS:
            props.pop(key, None)
    return document_props(props, refuse=False)


def is_file(conn, page_id) -> bool:
    """Whether ``page_id`` names a work folder's file, which no page is
    inside (S3)."""
    return bool(page_id) and isinstance(page_id, str) and conn.execute(
        "SELECT 1 FROM unified_blocks WHERE id = ? AND work_folder IS NOT NULL", (page_id,)).fetchone() is not None


def _filed_as(conn, props: dict, writer: str) -> dict:
    """A new page's ``props`` (``page_root_props``'s) held to the
    work-folder rule (gamma/work_folders/filing.py ``creation``) when they
    file it, nest it or stamp it: as stored (a person's note filed in a
    work folder that pulls is stamped its file), or OpError 403 ``LOCKED``.
    Call it under the write lock."""
    if not any(key in props for key in (FOLDERS, PARENT_PAGE, *STAMPS)):
        return props
    parent = props.get(PARENT_PAGE)
    try:
        return work_filing.creation(marks.Tree.read(conn), props, writer, target_is_file=is_file(conn, parent))
    except work_filing.Refused as e:
        from .ops import locked  # ops imports this module
        raise locked(e) from None


def create_page(conn, title: str, props: dict | None = None, *, actor: str, client: str = "",
                block_id: str = "", position: str = "") -> dict:
    """Insert a new root page and return its block dict. Commits. Last in
    the library unless ``position`` (a sibling key the caller minted) says
    otherwise — re-keyed like an op's when another page holds it
    (``free_position``); ``block_id`` reuses an id (a page brought back) — its
    row of the change log turns live again, and a copy of it in
    Recently deleted gives way, as it would to a hard delete and a
    re-create (a mirror bringing the page back with its own tree; its
    comments go with it; files only that copy held are left to upload_gc's
    ``reconcile`` pass).
    ``props`` are stored as ``page_root_props`` leaves them for the writer
    ``client`` names (the op path's clients: ``sync`` the mirror, ``disk``
    the vault, anyone else a person), held to the work-folder rule
    (``_filed_as``: OpError 403 ``LOCKED``, nothing written). ``actor``
    makes it (``touch_page``).
    The writer of a page on its own (POST /api/pages, POST /api/blocks
    with parent ``root``, get_or_create_doc_page, the web clips, the seeded
    pages, a mirror's new page); an import that writes a page with its
    block tree in one of its own transactions uses ``insert_page_tree``."""
    block_id = block_id or new_block_id()
    title = (title or "").strip() or "Untitled"
    writer = work_filing.writer_of(client)
    props = page_root_props(props, block_id, writer=writer)
    now = page_now()
    began = not conn.in_transaction
    write_lock(conn)  # the free key and the insert as one step
    try:
        props = _filed_as(conn, props, writer)
    except Exception:
        if began:
            conn.rollback()
        raise
    if conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = ?",
                    (block_id, TRASH)).fetchone():
        from . import comments  # local: comments imports this module

        gone = [r[0] for r in fetch_subtree(conn, block_id)]
        delete_subtree(conn, block_id)
        comments.purge(conn, block_id, gone)  # that copy's threads go with its blocks
        conn.execute("DELETE FROM page_ops WHERE page_id = ?", (block_id,))
    new_pos = free_position(conn, "root", position or None, block_id)
    conn.execute(
        f"INSERT INTO unified_blocks ({BLOCK_COLUMNS}) VALUES (?, 'root', ?, ?, ?, ?, ?, ?)",
        (block_id, new_pos, title, json.dumps(props), now, now, block_id),
    )
    touch_page(conn, block_id, actor, now=now, follow=_followed_by(client))
    upload_gc.claim(conn, upload_refs(title, props))  # its PDF is in use (again)
    conn.commit()
    return block_to_dict(conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?",
                                      (block_id,)).fetchone())


def insert_page_tree(conn, page_id: str, title: str, props: dict, tree: list, actor: str, *,
                     client: str = "") -> int:
    """Insert a root page (last on root) and its ``{content, properties?,
    id?, children?}`` tree (a node's own ``id`` is honoured), touched by
    ``actor`` (``touch_page``); returns the number of blocks written under
    the page. The page's ``props`` are stored as ``page_root_props``
    leaves them for the writer ``client`` names, held to the work-folder
    rule as ``create_page`` holds them (OpError 403 ``LOCKED``, nothing
    written by this call). Takes the write lock first (the position is
    read under it); the caller commits. This is the bulk writer of the
    library imports: they put pages in short transactions of their own,
    where ``create_page`` commits each one. Iterative, so a deep tree never
    meets the recursion limit ``flatten_tree`` would."""
    write_lock(conn)
    writer = work_filing.writer_of(client)
    props = _filed_as(conn, page_root_props(props, page_id, writer=writer), writer)
    now = page_now()
    pos = generate_key_between(last_child_position(conn, "root"), None)
    conn.execute(
        f"INSERT INTO unified_blocks ({BLOCK_COLUMNS}) VALUES (?,'root',?,?,?,?,?,?)",
        (page_id, pos, title, json.dumps(props), now, now, page_id),
    )
    written = 0
    pending = [(page_id, tree)]
    while pending:
        parent_id, nodes = pending.pop()
        if not nodes:
            continue
        positions = generate_n_keys_between(None, None, n=len(nodes))
        for node, child_pos in zip(nodes, positions):
            child_id = node.get("id") or new_block_id()
            conn.execute(
                f"INSERT INTO unified_blocks ({BLOCK_COLUMNS}) VALUES (?,?,?,?,?,?,?,?)",
                (child_id, parent_id, child_pos, node.get("content", ""), json.dumps(node.get("properties") or {}),
                 now, now, page_id),
            )
            written += 1
            if node.get("children"):
                pending.append((child_id, node["children"]))
    touch_page(conn, page_id, actor, now=now, follow=_followed_by(client))
    return written


def get_or_create_doc_page(conn, doc_id: str, default_title: str = "",
                           source_url: str | None = None,
                           original_filename: str | None = None,
                           folder: str = "", *, ws: str, actor: str = "",
                           format: str = "pdf", ext: str = ".pdf", size: int | None = None) -> dict:
    """Lookup-or-create BY DOCUMENT: the root page whose own document is
    the stored file ``doc_id`` + ``ext`` (``page_for_file``; a PDF by
    default), created under root when absent. Shared by POST /api/blocks/by-doc
    (PDF ingest from the app, "Add to library" on a PDF file chip), the
    extension's /api/clip (dedup), POST /api/pages/by-file (a stored file of
    any format, with its ``format``, ``ext`` and ``size`` settled by the
    route) and a work folder's Save a copy. Pages without a document are
    created by POST /api/pages (create_page). A ``doc_id`` that can be no
    stored file's stem is a ValueError (``documents.model.document``).

    The title is the upload's file name, else ``default_title`` (the
    caller's — a clip's tab title), else what ``document_title`` derives
    (URL file name, doc id); a PDF's is automatic (``auto_title``: its
    record may replace it), any other format's is the name alone. ``folder``
    (a folder id) files a NEW page (one in a work folder is refused:
    ``create_page``); an existing page keeps its own. On an existing page
    this opportunistically backfills its document (one ``doc`` patch, an op
    batch on the page, like every other write to an existing page — ``ws``
    names the workspace, ``actor`` the account): the file name when it has
    none, or when it is what an older upload cut of this one at its "/"
    (``storage.path_leaf``: "DC.pdf" of "AC/DC.pdf"), the title following
    while it is still that name; and the address when it is stored and one
    came; but never on a work folder's file, whose document is its vault's.
    auto_title is only set when the page still carries the exact title this
    call considers automatic, so a re-upload can never mark a user's custom title as
    replaceable by the metadata worker. Lookup and creation run under the
    write lock (``write_lock``): one page per stored file, however many
    requests ask at once. Commits."""
    stored = document(doc_id, source_url or "", original_filename or "", format=format, ext=ext, size=size)
    title = (stored["name"] or (default_title or "").strip()
             or document_title(original_filename or "", source_url or "", doc_id))
    automatic = format == "pdf"
    write_lock(conn)
    row = page_for_file(conn, doc_id, stored["ext"], BLOCK_COLUMNS)
    if row:
        props = json.loads(row[4] or "{}")
        content = row[3]
        changed = False
        if page_document(props) is not None and not work_folder_of(props):  # a file's document is its vault's
            had = check_document(props["doc"])
            doc = dict(had)
            cut = had["name"] != stored["name"] and path_leaf(stored["name"]) == had["name"]
            if stored["name"] and (not doc["name"] or cut):
                doc["name"] = stored["name"]
            if cut and content == had["name"]:  # the title the cut name gave follows it
                content = stored["name"]
                if props.get("auto_title") == had["name"]:
                    props["auto_title"] = content
            if doc["at"] == "store" and stored["at"] == "url":
                doc.update(at="url", url=stored["url"])
            doc = check_document(doc)
            if doc != had:
                props["doc"] = doc
                changed = True
        if automatic and not props.get("auto_title") and content == title:
            props["auto_title"] = content
            changed = True
        if changed:
            from .ops import after_commit, apply_ops, props_patch  # ops imports this module
            op = {"op": "set", "id": row[0], "props": props_patch(json.loads(row[4] or "{}"), props)}
            if content != row[3]:
                op["content"] = content
            after_commit(ws, conn, apply_ops(conn, row[0], [op], actor=actor))
            row = (*row[:3], content, json.dumps(props), *row[5:])
        else:
            conn.commit()  # nothing to write: let go of the lock
        return block_to_dict(row)
    props = {"doc": stored, **({"auto_title": title} if automatic else {})}
    if folder:
        props[FOLDERS] = [folder]
    return create_page(conn, title, props, actor=actor)


def pages_for_files(conn, names) -> dict:
    """Which root page each stored file became, batched by stored name:
    ``{"<hash><ext>": {"id", "title"}}`` for every name that is some library
    page's own document, of any format (``page_for_file``'s rule, read in
    one query), or, for a ``.md`` / ``.markdown`` name, the markdown file a
    note page was made from (``markdown_import``, as ``pages_for_docs``).
    Names with no page, and names that are no stored file's
    (``parse_upload_url``), are absent. Feeds the file chip's "Open page" /
    "Add to library" for a chip of any format; of two pages of one file the
    oldest answers."""
    wanted = {}
    for name in names:
        parsed = parse_upload_url(upload_url(str(name))) if name else None
        if parsed:
            wanted[str(name)] = parsed
    if not wanted:
        return {}
    stems = json.dumps(sorted({stem for stem, _ in wanted.values()}))
    found = {}
    for page_id, title, doc_id, ext, md in conn.execute(
            f"SELECT id, content, CASE WHEN {SQL_LIBRARY_DOC} THEN doc_id END, json_extract(properties, '$.doc.ext'), "
            "json_extract(properties, '$.markdown_import') FROM unified_blocks "
            f"WHERE parent_id = 'root' AND ((doc_id IN (SELECT value FROM json_each(?1)) AND {SQL_LIBRARY_DOC}) "
            "OR json_extract(properties, '$.markdown_import') IN (SELECT value FROM json_each(?1))) "
            "ORDER BY created_at, id", (stems,)):
        keys = [f"{doc_id}{ext}"] if doc_id and isinstance(ext, str) else []
        if isinstance(md, str):
            keys += [f"{md}.md", f"{md}.markdown"]
        for key in keys:
            if key in wanted and key not in found:
                found[key] = {"id": page_id, "title": title}
    return found


def pages_for_docs(conn, hashes) -> dict:
    """Which root page a stored file became, batched: ``{hash: {"id",
    "title"}}`` for every hash that is some page's own PDF (the generated
    ``doc_id``) or the markdown file a note page was made from
    (``markdown_import`` — the upload's content digest, so a ``<hash>.md``
    chip finds its page). Hashes with no page are absent. Feeds the file
    chip's "Open page" / "Open as page" — one query per page render, never
    one per chip. As in ``page_for_doc``, a page that is no library
    document does not count and of two pages of one file the oldest
    answers."""
    wanted = {str(d) for d in hashes if d}
    if not wanted:
        return {}
    found = {}
    for page_id, title, doc_id, md in conn.execute(
            f"SELECT id, content, CASE WHEN {SQL_LIBRARY_PDF} THEN doc_id END, "
            "json_extract(properties, '$.markdown_import') FROM unified_blocks "
            f"WHERE parent_id = 'root' AND ((doc_id IN (SELECT value FROM json_each(?1)) AND {SQL_LIBRARY_PDF}) "
            "OR json_extract(properties, '$.markdown_import') IN (SELECT value FROM json_each(?1))) "
            "ORDER BY created_at, id", (json.dumps(sorted(wanted)),)):
        for key in (doc_id, md):
            if key in wanted and key not in found:
                found[key] = {"id": page_id, "title": title}
    return found
