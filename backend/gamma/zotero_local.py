"""The Gamma side of a Zotero link's round (docs/dev/zotero_sync.md): reading
a page into what the engine (gamma/zotero_sync.py) compares, and writing
Zotero's decisions into Gamma through the op path, logged as actor
``ACTOR`` and client ``CLIENT`` so the link's own clock never reads them as
local edits.

Every write of a unit runs in one transaction under the workspace's write
lock (``apply``): the engine's ``check`` re-reads what its decision was
made on and may abort (the compare-and-set; nothing is written and the unit
goes back to the queue), the ops go in ``MAX_OPS`` chunks, the engine's own
``zotero_objects`` rows are written through ``rows`` before the commit, and
the room fan-out runs after it, inside the connection's block. No op ever
names a block outside the batch's page (``Moved``). A new page goes in
the same way (``create_page``). The page writers that commit on their own
(trash, restore, a folder delete, a move to another page, a PDF
replacement) take the same ``check`` and ``rows`` and run them in their
transaction.

The writers below return ops for ``apply``: a record (``record_op``),
filing (``filing_ops``), folders, note-region edits (``note_ops``, from
``zotero_merge.note_edits`` / ``note_removal``), annotation blocks
(``new_annotation`` + ``annotation_inserts``, ``annotation_update``,
``annotation_delete``) and the link's page and block marks
(``zotero_pdf_op``, ``readonly_op``).

Note pictures: a picture Zotero holds as an ``embedded_image`` attachment
(``<img data-attachment-key="K">``) comes to Gamma as a stored upload
(``resolve_pictures``), and a picture whose upload has such a key goes back
as that tag, so it round-trips; Gamma's own pictures stay in Gamma. While
the note machinery (zotero_notes, zotero_merge) knows no such tag, a keyed
picture stands in the notes they compare as a placeholder holding its key
(``_PIC_OPEN`` K ``_PIC_CLOSE``), which this module writes back as the
picture's markdown and sends as the tag.
"""

import html as html_mod
import re
from pathlib import Path
from typing import Callable, NamedTuple

from fractional_indexing import generate_key_between, generate_n_keys_between

from . import storage, upload_gc
from .auth import ZOTERO_ACTOR, ZOTERO_CLIENT
from . import ink as inkmod
from .bibtex import build_entry, clean_key
from .blocks_store import (BLOCK_COLUMNS, FOLDERS, LABELS, TRASH, TREES, attachment_file, batch_page, block_to_dict,
                           existing_in, fetch_subtree, filing, filing_ids, insert_page_tree, label_names, load_props,
                           new_block_id, page_attachment, page_root_id, valid_block_id, write_lock)
from .db import connect_pages_db, ws_uploads_dir
from .logbuf import log
from .markdown_export import block_ref_resolver, build_tree
from .notebook import is_sheet
from .ops import MAX_OPS, MOVED, OpError, after_commits, apply_batches, delete_tree, move_across_pages, notify_commit
from .ops import restore_page as ops_restore_page
from .ops import trash_page as ops_trash_page
from .pdf_export import highlight_note_blocks
from .storage import pdf_url, upload_refs
from .zotero_annotations import is_annotation_block
from .zotero_merge import Region, gamma_notes_tree, note_lossy as merge_note_lossy, represented
from .zotero_notes import note_html, note_tree
from .zotero_web import KEY_ALPHABET, KEY_RE

# Who the link's writes are logged as: auth's actor (shown as "Zotero" by
# auth.actor_names) and the engine's client (zotero_sync.CLIENT, both auth's).
ACTOR = ZOTERO_ACTOR
CLIENT = ZOTERO_CLIENT

# The record's keys in the order every writer of ``meta`` gives them (zotero_import).
META_ORDER = ("title", "authors", "year", "venue", "volume", "pages", "doi", "arxiv_id", "publisher", "isbn",
              "source", "kind")

# A keyed note picture as the compared notes hold it: the placeholder's two ends
# (private-use characters no note text holds) around the attachment key.
_PIC_OPEN, _PIC_CLOSE = chr(0xE000), chr(0xE001)
_PIC_RE = re.compile(f"{_PIC_OPEN}([{KEY_ALPHABET}]{{8}}){_PIC_CLOSE}")
# A stored picture in a block's markdown: Gamma's own upload, an optional width.
_UPLOAD_IMG_RE = re.compile(r"!\[(?P<alt>[^\]\n]*)\]\(\s*/api/uploads/(?P<name>[0-9a-fA-F]+\.[A-Za-z0-9]+)\s*\)"
                            r"(?:\{:width\s+(?P<lw>\d+)\})?")
_CODE_RE = re.compile(r"``[^`\n]+``|`[^`\n]+`")
_ALT_WIDTH_RE = re.compile(r"(.*?)\|(\d+)(?:x\d+)?", re.S)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>/]+))""")


class Changed(Exception):
    """The compare-and-set failed: ``check`` found the blocks the decision
    was made on changed since. Nothing was written; the caller re-queues
    the unit and drops its queued remote writes."""


class Moved(OpError):
    """An op would name a block that no longer lives on the batch's page
    (``block_id``; ``where`` is its page now, None when it is gone or in
    Recently deleted). Nothing was written. For the engine the row's block
    is gone from that page: the object goes, and the block is new where it
    is now."""

    def __init__(self, block_id: str, where: str | None):
        super().__init__(409, f"block {block_id} is not on this page", conflict=MOVED)
        self.block_id = block_id
        self.where = where


# --- the transaction ------------------------------------------------------------------

def _chunks(page_id: str, ops: list) -> list[tuple[str, list]]:
    return [(page_id, ops[i:i + MAX_OPS]) for i in range(0, len(ops), MAX_OPS)]


def _guard(conn, page_id: str, ops: list) -> None:
    """Raise ``Moved`` for an op that names a block (or an insert's parent)
    outside ``page_id``: a block another page holds, or one gone (a delete
    of a gone block is no change and passes)."""
    made = set()
    for op in ops:
        kind, bid = op.get("op"), op.get("id")
        if kind == "insert":
            parent = op.get("parent")
            if parent != page_id and parent not in made and batch_page(conn, parent) != page_id:
                raise Moved(parent, batch_page(conn, parent))
            where = batch_page(conn, bid)
            if where is not None and where != page_id:
                raise Moved(bid, where)
            made.add(bid)
            continue
        if bid in made:
            continue
        where = batch_page(conn, bid)
        if where != page_id and not (kind == "delete" and where is None):
            raise Moved(bid, where)
        if kind == "move":
            parent = op.get("parent")
            if parent != page_id and parent not in made and batch_page(conn, parent) != page_id:
                raise Moved(parent, batch_page(conn, parent))


def apply(ws: str, page_id: str, ops, *, check=None, rows=None, trees=(), actor: str = ACTOR,
          client: str = CLIENT) -> list[dict]:
    """Write one unit's Gamma side in one transaction (see
    docs/dev/zotero_sync.md#writing-to-gamma) and return the batches' results
    (``ops.apply_batches``; [] when there were no ops). Under the write lock,
    in order: ``check(conn)`` re-reads what the decision was made on, and False
    raises ``Changed`` with nothing written; ``ops`` (a list, or ``ops(conn) ->
    list`` to build them under the lock: positions then see the page as it is)
    go on ``page_id`` in ``MAX_OPS`` chunks, after ``trees`` (``[(FOLDERS |
    LABELS, ops)]``, the folder or label blocks they file into; or
    ``trees(conn)``, built under the lock before ``ops``); ``rows(conn)``
    writes the engine's rows; the commit; the fan-out and the commit listeners
    (``after_commits``). An op naming a block of another page raises ``Moved``;
    an op the batch refuses raises ``OpError``; both roll everything back."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            if check is not None and not check(conn):
                raise Changed(page_id)
            tree_list = trees(conn) if callable(trees) else trees
            page_ops = list(ops(conn) if callable(ops) else ops or ())
            batches = []
            for tree, tree_ops in tree_list or ():
                if tree not in TREES:
                    raise ValueError(f"trees take the folder or label tree, not {tree!r}")
                _guard(conn, tree, list(tree_ops))
                batches += _chunks(tree, list(tree_ops))
            _guard(conn, page_id, page_ops)
            batches += _chunks(page_id, page_ops)
            results = apply_batches(conn, batches, actor=actor, client=client) if batches else []
            if rows is not None:
                rows(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        after_commits(ws, conn, results)
    return results


def _tree_refs(title: str, props: dict, tree) -> set:
    """The stored files a new page and its block tree name (``upload_refs``)."""
    names, pending = upload_refs(title, props), list(tree or ())
    while pending:
        node = pending.pop()
        names |= upload_refs(node.get("content") or "", node.get("properties") or {})
        pending += node.get("children") or []
    return names


def create_page(ws: str, *, page_id: str, title: str, props: dict | None = None, tree=(), check=None, rows=None,
                trees=(), actor: str = ACTOR, client: str = CLIENT) -> dict:
    """Make a page from Zotero (an item, a standalone note or attachment) with
    its block tree and the engine's rows in one transaction, so a crash leaves
    both or neither: ``check``, then the ``trees`` ops (``[(FOLDERS | LABELS,
    ops)]``: the labels the page is filed under that are new, as ``apply``
    takes them), then ``blocks_store.insert_page_tree`` (last in the library,
    touched by ``actor``; ``tree`` nodes are ``{content, properties?, id?,
    children?}`` and a node's own id is kept, so give one to each block a row
    names), the stored files it names claimed from the upload GC,
    ``rows(conn)``, the commit, then the commit listeners hear ``client``.
    Filing that is no list of ids is dropped, as ``blocks_store.create_page``
    drops it. OpError 409 when a block (in Recently deleted too: restore that
    page instead) holds ``page_id``. Returns the page's block dict."""
    props = dict(props or {})
    for tree_name in TREES:
        if tree_name in props:
            ids = filing_ids(props.pop(tree_name))
            if ids:
                props[tree_name] = ids
    if props.get("doc_id") and props.get("source_url") == pdf_url(props["doc_id"]):
        del props["source_url"]
    title = (title or "").strip() or "Untitled"
    if not valid_block_id(page_id):
        raise OpError(400, f"invalid page id: {page_id!r}")
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            if check is not None and not check(conn):
                raise Changed(page_id)
            if conn.execute("SELECT 1 FROM unified_blocks WHERE id = ?", (page_id,)).fetchone():
                raise OpError(409, "a block with that id exists")
            batches = []
            for tree_name, tree_ops in trees or ():
                if tree_name not in TREES:
                    raise ValueError(f"trees take the folder or label tree, not {tree_name!r}")
                _guard(conn, tree_name, list(tree_ops))
                batches += _chunks(tree_name, list(tree_ops))
            results = apply_batches(conn, batches, actor=actor, client=client) if batches else []
            insert_page_tree(conn, page_id, title, props, list(tree or ()), actor)
            upload_gc.claim(conn, _tree_refs(title, props, tree))
            if rows is not None:
                rows(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        if results:
            after_commits(ws, conn, results)
        page = block_to_dict(conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?",
                                          (page_id,)).fetchone())
    notify_commit(ws, client, page_id)
    return page


def _own_transaction(ws: str, check, rows, write) -> object:
    """Run ``write(conn)`` (a page writer that commits on its own) after
    ``check`` and ``rows`` in the transaction it commits: the engine's rows
    land with the write, or not at all."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            if check is not None and not check(conn):
                raise Changed("")
            if rows is not None:
                rows(conn)
            out = write(conn)
            if conn.in_transaction:
                conn.commit()  # a writer that found nothing to do
        except BaseException:
            conn.rollback()
            raise
    return out


def trash_page(ws: str, page_id: str, *, check=None, rows=None) -> dict | None:
    """Move the page to Recently deleted (``ops.trash_page``, client
    ``CLIENT``), the engine's rows in the same transaction. A page already
    there, or gone, is left as it is (the rows are still written): the
    re-run of a unit is no error. → its trash entry, or None."""
    def write(conn):
        row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if not row or row[0] != "root":
            return None
        return ops_trash_page(ws, conn, page_id, actor=ACTOR, client=CLIENT)
    return _own_transaction(ws, check, rows, write)


def restore_page(ws: str, page_id: str, *, check=None, rows=None) -> dict | None:
    """Bring the page back from Recently deleted (``ops.restore_page``,
    client ``CLIENT``), the engine's rows in the same transaction. A page
    that is live already, or gone (purged: make it anew with
    ``create_page``), is left as it is. → its block dict, or None."""
    def write(conn):
        row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if not row or row[0] != TRASH:
            return None
        return ops_restore_page(ws, conn, page_id, actor=ACTOR, client=CLIENT)
    return _own_transaction(ws, check, rows, write)


def delete_folder(ws: str, folder_id: str, *, check=None, rows=None):
    """Delete a folder with its subfolders and take them off the pages
    filed in them (``ops.delete_tree`` on ``FOLDERS``, client ``CLIENT``),
    the engine's rows in the same transaction. A folder gone already is no
    error. → ``delete_tree``'s ``(rows, carried, results)``, or None."""
    def write(conn):
        if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND page_id = ?",
                            (folder_id, FOLDERS)).fetchone():
            return None
        return delete_tree(ws, conn, FOLDERS, folder_id, actor=ACTOR, client=CLIENT)
    return _own_transaction(ws, check, rows, write)


def move_block_across(ws: str, block_id: str, to_page: str, *, parent: str | None = None, check=None,
                      rows=None) -> str | None:
    """Move a block with its subtree to another page (``ops.move_across_pages``):
    an annotation or a note region that Zotero moved to another item. It
    goes under ``parent`` (a block of ``to_page``), last, or by default to
    ``to_page``'s top level right after its last annotation block. The
    engine's rows go in the same transaction. ``Moved`` when the block is
    gone or the parent is not on ``to_page``. → the block's new position,
    or None when it is on ``to_page`` already. Both halves are logged
    with client ``CLIENT``, so the link's clock does not hear the move as
    a local edit."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            if check is not None and not check(conn):
                raise Changed(block_id)
            src = page_root_id(conn, block_id)
            if src is None or src == block_id:
                raise Moved(block_id, None)
            if src == to_page:
                if rows is not None:
                    rows(conn)
                conn.commit()
                return None
            parent = parent or to_page
            if page_root_id(conn, parent) != to_page:
                raise Moved(parent, page_root_id(conn, parent))
            places = _Places(conn)
            if parent == to_page:
                position = places.after_annotations(to_page, 1)[0]
            else:
                position = places.position(parent, places.last(parent))
            if rows is not None:
                rows(conn)
            move_across_pages(ws, conn, block_id, parent, position, src, to_page, actor=ACTOR, client=CLIENT)
        except BaseException:
            conn.rollback()
            raise
    return position


def set_pdf(ws: str, page_id: str, doc_id: str, *, owner: str, original_filename: str = "", source_url: str = "",
            props: dict | None = None, check=None, rows=None) -> dict:
    """Make a stored PDF the page's (a Zotero download, or Zotero's file
    replacing Gamma's) through ``routers.pages.replace_attachment`` with
    client ``CLIENT``, actor ``ACTOR``, the relocation job owned by the
    link's ``owner``, and no one-page-per-PDF check (linked items may share
    a file). ``zotero_pdf`` goes in the same op, with ``props`` (more page
    properties to set or, as None, drop); the engine's rows go in the same
    transaction. → ``replace_attachment``'s answer (``job`` None and
    ``relocation_error`` set when the highlights' run did not start: the
    page's annotation pushes then stay held). Raises ``Changed`` when
    ``check`` fails, OpError 404 for a page gone or trashed."""
    from .routers.pages import replace_attachment  # local: keep gamma.* free of the routers package

    done = replace_attachment(ws, page_id, doc_id, source_url, original_filename, actor=ACTOR, client=CLIENT,
                              owner=owner, check_held=False, props={"zotero_pdf": None, **(props or {})},
                              check=check, rows=rows, check_zotero=False)
    if done is None:
        raise Changed(page_id)
    return done


def stamps(conn, ids) -> dict:
    """``{block id: (page id, updated_at)}`` as stored now, None for a block
    gone: what a ``check`` compares with ``PageView.stamps`` of the same
    ids, since every set, move and cross-page move restamps a block."""
    out = {}
    for bid in ids:
        row = conn.execute("SELECT page_id, updated_at, parent_id FROM unified_blocks WHERE id = ?",
                           (bid,)).fetchone()
        out[bid] = None if row is None else (TRASH if row[2] == TRASH else row[0], row[1])
    return out


# --- reading a page -------------------------------------------------------------------

class Annotation(NamedTuple):
    """An annotation block of a page as the engine compares it. ``block``:
    its ``build_tree`` node (children included); ``nested``: its notes as
    ``pdf_export.highlight_note_blocks`` gives them (its own note at depth
    0, the comment's nested part after it); ``parent_id``, its ``depth``
    below the page (1 = top level); ``on_sheet``: a sheet is among its
    ancestors (the nearest sheet wins: a text box there is no PDF
    annotation); ``page_root``: the page it lives on."""
    block: dict
    nested: list
    parent_id: str
    depth: int
    on_sheet: bool
    page_root: str


class PageView(NamedTuple):
    """A page read for one round (``read_page``). ``page``: the root's
    ``build_tree`` node, ``nodes`` every block's by id; ``title`` and
    ``props`` the root's. ``annotations``: the annotation blocks at any
    depth, in page order. ``regions``: the Zotero-note regions by root id
    (``zotero_merge.represented``; for a note page, the page itself, its
    note the title as a heading and the page's blocks), and
    ``gamma_notes`` the page's Gamma-notes region (None when it has none),
    their ``html`` with keyed pictures as Zotero's tag. ``folders``: the
    folder ids it is filed in that exist; ``labels``: ``{label id: name}``
    of its labels. ``doc_id``: the stored PDF's ("" for none), ``pdf`` its
    path when the file is there."""
    page_id: str
    title: str
    props: dict
    page: dict
    nodes: dict
    annotations: list
    regions: dict
    gamma_notes: Region | None
    folders: list
    labels: dict
    doc_id: str
    pdf: Path | None

    def stamps(self, ids) -> dict:
        """``stamps`` of ``ids`` as this view read them."""
        out = {}
        for bid in ids:
            node = self.nodes.get(bid)
            out[bid] = None if node is None else (node.get("page_id"), node.get("updated_at"))
        return out


def _keyed(content: str, image_key, attrs: dict) -> str:
    """``content`` with each stored picture whose upload has a Zotero key
    (``image_key(name)``) as the placeholder of that key; its alt and width
    go into ``attrs`` for the tag it is sent as. Code spans are text."""
    if "/api/uploads/" not in content:
        return content
    spans = [m.span() for m in _CODE_RE.finditer(content)]

    def swap(m):
        if any(a <= m.start() < b for a, b in spans):
            return m.group()
        key = image_key(m.group("name"))
        if not key or not KEY_RE.fullmatch(key):
            return m.group()
        alt, width = m.group("alt"), m.group("lw")
        size = _ALT_WIDTH_RE.fullmatch(alt or "")
        if size:
            alt, width = size.group(1), size.group(2)
        attrs.setdefault(key, (alt or "", width or ""))
        return f"{_PIC_OPEN}{key}{_PIC_CLOSE}"
    return _UPLOAD_IMG_RE.sub(swap, content)


def _keyed_tree(node: dict, image_key, attrs: dict) -> dict:
    """A copy of a ``build_tree`` node whose contents hold keyed pictures as
    placeholders (``_keyed``); the node itself when there is no key lookup."""
    if image_key is None:
        return node
    out = dict(node)
    out["content"] = _keyed(node.get("content") or "", image_key, attrs)
    out["children"] = [_keyed_tree(c, image_key, attrs) for c in node.get("children") or ()]
    return out


def picture_tags(text: str, attrs: dict | None = None) -> str:
    """Note HTML (a region's) with each picture placeholder as the tag
    Zotero keeps an embedded picture as, ``<img data-attachment-key="K">``,
    with the alt and width Gamma shows it at (``attrs``: ``{key: (alt,
    width)}``)."""
    attrs = attrs or {}

    def tag(m):
        alt, width = attrs.get(m.group(1), ("", ""))
        extra = (f' alt="{html_mod.escape(alt, quote=True)}"' if alt else "") + (f' width="{width}"' if width else "")
        return f'<img data-attachment-key="{m.group(1)}"{extra}/>'
    return _PIC_RE.sub(tag, text or "")


def _region(node: dict, *, exclude, resolve_ref, image_key, page_title: str | None = None) -> Region:
    """A region as ``zotero_merge.represented`` splits it, its keyed pictures
    sent as Zotero's tag. ``page_title``: the region is a note page (a
    standalone note's), whose note is the page's title as its heading, then
    the page's blocks, as the RDF export writes one
    (``zotero_export.standalone_note_html``)."""
    attrs: dict = {}
    region = represented(_keyed_tree(node, image_key, attrs), exclude=exclude, resolve_ref=resolve_ref)
    if page_title is not None:
        body = note_html({"content": "", "children": region.tree.get("children") or []}, resolve_ref=resolve_ref)
        region = region._replace(html=f"<h1>{html_mod.escape(page_title, quote=False)}</h1>" + body)
    return region._replace(html=picture_tags(region.html, attrs)) if attrs else region


def imported_note(node: dict) -> bool:
    """Whether a block is a note an RDF import brought, marked with its
    memo's local reference (``zotero_note`` "#item_<n>" from Zotero's
    export, "#gamma_note_…" from Gamma's): it stands for a note some
    library holds, so it is never one of the page's own notes sent as
    "Notes from Gamma"; a first link pairs it with the Zotero note it reads
    like, else it stays Gamma's alone."""
    mark = (node.get("properties") or {}).get("zotero_note")
    return isinstance(mark, str) and mark.startswith("#")


def read_page(conn, page_id: str, *, note_roots=None, image_key=None, notes: bool = True,
              in_trash: bool = False, render=None) -> PageView | None:
    """The page ``page_id`` as a round compares it (``PageView``), or None
    when it is not a live page. ``note_roots``: the ids of its Zotero-note
    region roots (the blocks the engine's note rows name; by default every
    block carrying ``zotero_note``); a root not on the page is skipped.
    ``image_key(upload name) -> Zotero key | None``: the note pictures the
    link holds (its image rows), sent back as Zotero's tag; the rest of
    Gamma's pictures stay kept blocks. ``notes`` False skips the regions;
    ``render``: the roots whose regions are built (None: every one; the
    page's id stands for its own notes, or a note page's note), the others
    still left out of the rest. ``in_trash`` reads a page in Recently
    deleted too. One read of the page's rows and no PDF parsing."""
    rows = fetch_subtree(conn, page_id)
    if not rows or rows[0][1] not in (("root", TRASH) if in_trash else ("root",)):
        return None
    root = build_tree(rows, page_id)
    nodes, children_by_id, order = {}, {}, []
    stack = [root]
    while stack:
        node = stack.pop()
        nodes[node["id"]] = node
        children_by_id[node["id"]] = node["children"]
        order.append(node["id"])
        stack.extend(reversed(node["children"]))
    props = root["properties"] or {}

    annotations = []
    walk = [(c, 1, False) for c in reversed(root["children"])]
    while walk:
        node, depth, on_sheet = walk.pop()
        if is_annotation_block(node, on_sheet=on_sheet):
            annotations.append(Annotation(node, highlight_note_blocks(node, children_by_id), node["parent_id"],
                                          depth, on_sheet, page_id))
        below = on_sheet or is_sheet(node.get("properties"))
        walk.extend((c, depth + 1, below) for c in reversed(node["children"]))

    regions, gamma_notes = {}, None
    if notes:
        if note_roots is None:
            roots = [bid for bid in order if (nodes[bid].get("properties") or {}).get("zotero_note")]
        else:
            wanted = set(note_roots)
            roots = [bid for bid in order if bid in wanted]
        resolve_ref = block_ref_resolver(conn)
        for bid in roots:
            if render is not None and bid not in render:
                continue
            regions[bid] = _region(nodes[bid], exclude=set(roots) - {bid}, resolve_ref=resolve_ref,
                                   image_key=image_key,
                                   page_title=(root["content"] or "").strip() if bid == page_id else None)
        if page_id not in roots and (render is None or page_id in render):
            held = [c["id"] for c in root["children"] if imported_note(c)]
            tree = gamma_notes_tree(root["children"], page_id=page_id, exclude=[*roots, *held])
            if tree is not None:
                gamma_notes = _region(tree, exclude=roots, resolve_ref=resolve_ref, image_key=image_key)

    known = label_names(conn)
    doc_id = attachment_file(page_attachment(props))
    ws = getattr(conn, "ws", "")
    pdf = storage.open_path(ws, f"{doc_id}.pdf") if doc_id and ws else None
    return PageView(page_id=page_id, title=root["content"] or "", props=props, page=root, nodes=nodes,
                    annotations=annotations, regions=regions, gamma_notes=gamma_notes,
                    folders=existing_in(conn, FOLDERS, filing(props, FOLDERS)),
                    labels={lid: known[lid] for lid in filing(props, LABELS) if lid in known},
                    doc_id=doc_id, pdf=pdf)


def read_ink(ws: str, props: dict) -> inkmod.InkFile | None:
    """The ink file an ink block's ``ink_url`` names, parsed (``ink.read_upload``),
    or None when it names none that reads."""
    url = (props or {}).get("ink_url")
    return inkmod.read_upload(ws_uploads_dir(ws), url) if isinstance(url, str) and url else None


# --- positions ------------------------------------------------------------------------

class _Places:
    """The sibling order of the parents a list of edits touches, read once
    from the database and kept up to date as the edits are planned, so
    each insert or move gets a position between its neighbours as they
    will be when it applies."""

    def __init__(self, conn):
        self.conn = conn
        self.kids: dict[str, list] = {}
        self.parent: dict[str, str | None] = {}
        self.props: dict[str, dict] = {}

    def children(self, parent: str) -> list:
        if parent not in self.kids:
            rows = self.conn.execute("SELECT id, position, properties FROM unified_blocks WHERE parent_id = ? "
                                     "ORDER BY position, id", (parent,)).fetchall()
            self.kids[parent] = [[r[0], r[1]] for r in rows]
            for bid, _, raw in rows:
                self.parent.setdefault(bid, parent)
                self.props.setdefault(bid, load_props(raw))
        return self.kids[parent]

    def parent_of(self, bid: str) -> str | None:
        if bid not in self.parent:
            row = self.conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (bid,)).fetchone()
            self.parent[bid] = row[0] if row else None
        return self.parent[bid]

    def last(self, parent: str) -> str | None:
        kids = self.children(parent)
        return kids[-1][0] if kids else None

    def _between(self, parent: str, after: str | None, before: str | None = None) -> tuple:
        kids = self.children(parent)
        ids = [k[0] for k in kids]
        if before is not None and before in ids:
            i = ids.index(before)
            return (kids[i - 1][1] if i else None), kids[i][1], i
        if after is not None and after in ids:
            i = ids.index(after)
            return kids[i][1], (kids[i + 1][1] if i + 1 < len(kids) else None), i + 1
        return (kids[-1][1] if kids else None), None, len(kids)

    def position(self, parent: str, after: str | None, before: str | None = None) -> str:
        """A position under ``parent`` right after the sibling ``after`` (or
        right before ``before``); the end when the sibling named is not
        there."""
        lo, hi, _ = self._between(parent, after, before)
        try:
            return generate_key_between(lo, hi)
        except Exception:  # noqa: BLE001 — siblings out of order in the data: the end
            return generate_key_between(self.children(parent)[-1][1] if self.children(parent) else None, None)

    def place(self, bid: str, parent: str, after: str | None, before: str | None = None) -> str:
        """Plan ``bid`` (new, or moved from where it is) under ``parent`` at
        ``position``'s place; its position."""
        old = self.parent_of(bid)
        if old is not None:
            kids = self.children(old)
            kids[:] = [k for k in kids if k[0] != bid]
        position = self.position(parent, after, before)
        _, _, i = self._between(parent, after, before)
        self.children(parent).insert(i, [bid, position])
        self.parent[bid] = parent
        return position

    def remove(self, bid: str) -> None:
        old = self.parent_of(bid)
        if old is not None:
            kids = self.children(old)
            kids[:] = [k for k in kids if k[0] != bid]
        self.parent[bid] = None

    def after_annotations(self, parent: str, n: int) -> list[str]:
        """``n`` positions under ``parent`` right after its last child that
        is an annotation block (the end when it has none)."""
        kids = self.children(parent)
        anchor = None
        for bid, _ in kids:
            if is_annotation_block({"properties": self.props.get(bid) or {}}):
                anchor = bid
        lo, hi, _ = self._between(parent, anchor) if anchor else self._between(parent, None)
        try:
            return generate_n_keys_between(lo, hi, n)
        except Exception:  # noqa: BLE001 — siblings out of order in the data: the end
            return generate_n_keys_between(kids[-1][1] if kids else None, None, n)


# --- the record and filing ------------------------------------------------------------

def _one_line(text) -> str:
    return " ".join(str(text or "").split())


def record_op(page: dict, values: dict) -> dict | None:
    """The op that writes Zotero's record fields ``values`` (``{compare
    field: Zotero's value}``, as ``zotero_merge.item_projection`` holds
    them: the fields a merge applies) on the page ``page`` (a block dict):
    only those fields of ``meta`` change, its ``source`` stays the page's
    ("zotero" for a page that had no record), ``cite_key`` is pinned or
    unpinned, the BibTeX entry is built again (``bibtex.build_entry``) and
    the stale slide citation and lookup failure go (``ppt_cite``,
    ``meta_error``). The page's title follows the record's while it is the
    old record's title, automatic or empty. Other keys (``type``, tags,
    collections) are not the record's and are ignored. None when nothing
    changes."""
    props = page.get("properties") or {}
    old = props.get("meta") if isinstance(props.get("meta"), dict) else None
    if old is None:
        meta = {k: ([] if k == "authors" else "") for k in META_ORDER[:10]}
        meta["source"] = "zotero"
    else:
        meta = dict(old)
    for field, value in values.items():
        if field not in META_ORDER or field == "source":
            continue  # cite_key below; type, tags and collections are not the record's
        if field == "kind":
            if value:
                meta["kind"] = value
            else:
                meta.pop("kind", None)
        elif field == "authors":
            meta["authors"] = [str(a) for a in value or () if str(a).strip()]
        else:
            meta[field] = "" if value is None else str(value)
    pinned = clean_key(props.get("cite_key") or "")
    cite_key = clean_key(values.get("cite_key") or "") if "cite_key" in values else pinned
    patch = {}
    if meta != old:
        patch["meta"] = meta
    if cite_key != pinned:
        patch["cite_key"] = cite_key or None
    if patch:
        bibtex = build_entry(meta, cite_key)
        if bibtex != props.get("bibtex"):
            patch["bibtex"] = bibtex
        for stale in ("ppt_cite", "meta_error"):
            if stale in props:
                patch[stale] = None
    op = {"op": "set", "id": page["id"]}
    if patch:
        op["props"] = patch
    new_title = _one_line(values.get("title")) if "title" in values else ""
    title, old_title = _one_line(page.get("content")), _one_line((old or {}).get("title"))
    follows = (title in ("", "Untitled") or (old_title and title == old_title)
               or props.get("auto_title") == page.get("content"))
    if new_title and follows and new_title != (page.get("content") or ""):
        op["content"] = new_title
    return op if len(op) > 2 else None


class Filing(NamedTuple):
    """``filing_ops``' answer: ``trees``, the label inserts to pass to
    ``apply``'s ``trees``, and ``op``, the page's set (None for no change)."""
    trees: list
    op: dict | None


def filing_ops(conn, page: dict, *, folders=None, labels=None) -> Filing:
    """What files the page ``page`` (a block dict) in ``folders`` (folder
    ids, from the collection rows) and under ``labels`` (names, Zotero's
    spelling), each left as it is when None. A name matches a label of the
    same name ignoring case (``casefold``), one the page carries first;
    the names no label has are made, under Zotero's spelling. Ids that are
    kept keep their place on the page; new ones follow in order."""
    props = page.get("properties") or {}
    patch, label_ops = {}, []
    if folders is not None:
        have = _filed(props, FOLDERS)
        wanted = [f for f in dict.fromkeys(folders) if f]
        wanted = existing_in(conn, FOLDERS, wanted)
        ids = [f for f in have if f in wanted] + [f for f in wanted if f not in have]
        if ids != have:
            patch[FOLDERS] = ids or None
    if labels is not None:
        have = _filed(props, LABELS)
        known = label_names(conn)
        by_fold: dict[str, str] = {}
        for lid in [lid for lid in have if lid in known] + list(known):
            by_fold.setdefault(known[lid].strip().casefold(), lid)
        ids = []
        for name in labels:
            name = str(name or "").strip()
            if not name:
                continue
            lid = by_fold.get(name.casefold())
            if lid is None:
                lid = new_block_id()
                label_ops.append({"op": "insert", "id": lid, "parent": LABELS, "content": name})
                by_fold[name.casefold()] = lid
            if lid not in ids:
                ids.append(lid)
        ordered = [lid for lid in have if lid in ids] + [lid for lid in ids if lid not in have]
        if ordered != have:
            patch[LABELS] = ordered or None
    op = {"op": "set", "id": page["id"], "props": patch} if patch else None
    return Filing([(LABELS, label_ops)] if label_ops else [], op)


def _filed(props: dict, tree: str) -> list[str]:
    return list(dict.fromkeys(filing(props, tree)))


def folder_insert(folder_id: str, name: str, parent: str | None = None) -> dict:
    """The op (on ``FOLDERS``) making a folder for a collection: its own id,
    last under ``parent`` (a folder id; None for the top)."""
    return {"op": "insert", "id": folder_id, "parent": parent or FOLDERS, "content": name}


def folder_rename(folder_id: str, name: str) -> dict:
    return {"op": "set", "id": folder_id, "content": name}


def folder_move(folder_id: str, parent: str | None = None) -> dict:
    """The op moving a folder last under ``parent`` (None for the top)."""
    return {"op": "move", "id": folder_id, "parent": parent or FOLDERS}


# --- block marks ----------------------------------------------------------------------

def props_op(block_id: str, patch: dict) -> dict:
    """A set of ``patch`` (None drops a key) on one block."""
    return {"op": "set", "id": block_id, "props": dict(patch)}


def zotero_pdf_op(page_id: str, value: dict | None) -> dict:
    """The page's ``zotero_pdf`` mark (``{key, state, reason, size, filename}``
    while Zotero holds a PDF Gamma does not), or its removal (None)."""
    return props_op(page_id, {"zotero_pdf": value or None})


def readonly_op(block_id: str, value: str | None, author: str = "") -> dict:
    """A block's ``zotero_readonly`` mark (``"lossy"`` | ``"locked"``, with
    the other group member's name as ``zotero_author``), or their removal
    (None)."""
    return props_op(block_id, {"zotero_readonly": value or None, "zotero_author": (author or None) if value else None})


# --- note regions ---------------------------------------------------------------------

class Pictures(NamedTuple):
    """``resolve_pictures``' answer. ``html``: the note with each picture
    Gamma holds as its placeholder; ``missing``: the keys of the pictures
    ``fetch`` could not give (the region is then lossy); ``markdown``:
    ``{key: "![alt|width](/api/uploads/<name>)"}``, what each placeholder
    is written as."""
    html: str
    missing: list
    markdown: dict


def _attrs(tag: str) -> dict:
    return {m.group(1).lower(): next(v for v in m.groups()[1:] if v is not None) for m in _ATTR_RE.finditer(tag)}


def resolve_pictures(html_text: str, fetch: Callable[[str], str | None]) -> Pictures:
    """A pulled note's pictures made Gamma's: each ``<img
    data-attachment-key="K">`` (an ``embedded_image`` attachment) whose file
    ``fetch(K)`` gives as a stored upload name becomes that upload, written
    as a placeholder ``zotero_merge.note_edits`` compares like text and
    ``note_ops`` / ``pulled_tree`` write as the picture's markdown. A key
    ``fetch`` answers None for stays as Zotero's tag (``note_tree`` reads
    "(image)") and is listed in ``missing``. ``fetch`` is asked once per
    key."""
    names: dict[str, str | None] = {}
    markdown: dict[str, str] = {}
    missing: list[str] = []

    def swap(m):
        attrs = _attrs(m.group())
        key = (attrs.get("data-attachment-key") or "").strip()
        if not key:
            return m.group()
        if key not in names:
            names[key] = fetch(key) if KEY_RE.fullmatch(key) else None
        name = names[key]
        if not name:
            if key not in missing:
                missing.append(key)
            return m.group()
        alt = " ".join(re.sub(r"[\[\]\\`$*~=<>|]", "", html_mod.unescape(attrs.get("alt") or "")).split())
        width = (attrs.get("width") or "").strip()
        if width.isdigit() and int(width):
            alt += f"|{int(width)}"
        markdown.setdefault(key, f"![{alt}](/api/uploads/{name})")
        return f"{_PIC_OPEN}{key}{_PIC_CLOSE}"
    return Pictures(_IMG_TAG_RE.sub(swap, html_text or ""), missing, markdown)


def picture_markdown(text: str, pictures: Pictures | dict | None) -> str:
    """``text`` (a pulled block's content) with each picture placeholder as
    the picture's markdown; one no picture is known for reads "(image)"."""
    if not text or _PIC_OPEN not in text:
        return text
    known = pictures.markdown if isinstance(pictures, Pictures) else (pictures or {})
    return _PIC_RE.sub(lambda m: known.get(m.group(1), "(image)"), text)


def pulled_tree(note, pictures: Pictures | None = None) -> dict | None:
    """A pulled note as a block tree to insert (``zotero_notes.note_tree``):
    ``note`` is its HTML, or ``resolve_pictures``' answer, whose pictures
    come as their markdown. None for a note with no text and no picture."""
    if isinstance(note, Pictures):
        pictures, note = note, note.html
    tree = note_tree(note or "")
    if tree is None:
        return None
    stack = [tree]
    while stack:
        node = stack.pop()
        node["content"] = picture_markdown(node.get("content") or "", pictures)
        stack.extend(node.get("children") or ())
    return tree


def note_lossy(html_text: str, pictures: Pictures | None = None) -> bool:
    """``zotero_merge.note_lossy`` with the pictures Gamma holds
    (``pictures``) counted as carried: only a picture it could not fetch,
    or what else note HTML loses, makes the note lossy."""
    if pictures is None:
        return merge_note_lossy(html_text)
    if pictures.missing:
        return True
    return merge_note_lossy(_PIC_RE.sub("", pictures.html))


class NoteOps(NamedTuple):
    """``note_ops``' answer: ``ops`` for ``apply``; ``ids``: ``{"new:<n>":
    block id}`` of the blocks it inserts; ``lossy``: the ``lossy`` edits
    (``{"id", "content"}``, the block Gamma may not set and Zotero's text
    for it) for the engine's ``zotero_lossy`` rows."""
    ops: list
    ids: dict
    lossy: list


def note_ops(conn, region: Region, edits, *, pictures: Pictures | dict | None = None) -> NoteOps:
    """The ops for ``zotero_merge.note_edits`` / ``note_removal`` output on the
    region ``region`` they were computed from: each ``new:<n>`` gets a fresh
    block id, used for that ref in later edits; an insert or move goes right
    after its ``after`` sibling, or for ``after`` None before the parent's
    first child in the region (the end when it has none); a delete removes the
    block alone, any child still under it lifted first to its place; a kept
    block is never set or deleted; picture placeholders are written as the
    pictures' markdown (``pictures``). Positions are read from ``conn``: build
    the ops under the write lock (``apply(ws, page, lambda conn: note_ops(conn,
    ...).ops)``) to place them on the page as it is."""
    places = _Places(conn)
    ids: dict[str, str] = {}
    region_kids = {parent: set(kids) for parent, kids in region.children.items()}
    out, lossy = [], []

    def real(ref):
        return ids.get(ref, ref) if isinstance(ref, str) else ref

    for edit in edits or ():
        kind = edit.get("op")
        if kind == "lossy":
            lossy.append({"id": edit["id"], "content": picture_markdown(edit.get("content") or "", pictures)})
        elif kind == "set":
            bid = real(edit["id"])
            if bid in region.kept or (bid == region.root and region.root_fixed):
                log.warning(f"[zotero] note edit would set the kept block {bid}: skipped")
                continue
            out.append({"op": "set", "id": bid, "content": picture_markdown(edit.get("content") or "", pictures)})
        elif kind in ("insert", "move"):
            parent, after = real(edit["parent"]), real(edit.get("after"))
            before = None
            if after is None:
                first = next((k[0] for k in places.children(parent) if k[0] in region_kids.get(parent, ())), None)
                before = first
            if kind == "insert":
                bid = new_block_id()
                ids[edit["id"]] = bid
                position = places.place(bid, parent, after, before)
                out.append({"op": "insert", "id": bid, "parent": parent, "position": position,
                            "content": picture_markdown(edit.get("content") or "", pictures), "props": {}})
            else:
                bid = real(edit["id"])
                position = places.place(bid, parent, after, before)
                out.append({"op": "move", "id": bid, "parent": parent, "position": position})
            region_kids.setdefault(parent, set()).add(bid)
        elif kind == "delete":
            bid = real(edit["id"])
            if bid in region.kept or (bid == region.root and region.root_fixed) or places.parent_of(bid) is None:
                continue
            parent = places.parent_of(bid)
            anchor = bid
            for child, _ in list(places.children(bid)):  # never delete what is still under it
                position = places.place(child, parent, anchor)
                out.append({"op": "move", "id": child, "parent": parent, "position": position})
                anchor = child
            places.remove(bid)
            out.append({"op": "delete", "id": bid})
        else:
            raise ValueError(f"unknown note edit: {kind!r}")
    return NoteOps(out, ids, lossy)


# --- annotation blocks ----------------------------------------------------------------

def _store_ink(ws: str, ink_file) -> str:
    """Store an ink file (``ink.parse_ink`` checks it first) as an upload;
    its ``ink_url``."""
    checked = inkmod.parse_ink(inkmod.dumps(ink_file))
    name, _ = storage.store_file(ws, inkmod.dumps(checked), ".ink")
    return f"/api/uploads/{name}"


def new_annotation(ws: str, item: dict, *, block_id: str = "") -> dict:
    """A block for a Zotero annotation (``zotero_annotations.from_item``'s
    answer), ready for ``annotation_inserts``: ``{id, content, props}``, an
    ink item's file stored first (``storage.store_file``, which may raise
    507 when storage is full) and named by ``ink_url``. Store before the
    transaction: an upload no block names yet is not purged for 15
    minutes (gamma/upload_gc.py)."""
    props = dict(item.get("props") or {})
    if item.get("ink") is not None:
        props["ink_url"] = _store_ink(ws, item["ink"])
    return {"id": block_id or new_block_id(), "content": item.get("content") or "", "props": props}


def annotation_inserts(conn, page_id: str, blocks, *, parent: str | None = None) -> list[dict]:
    """The inserts of ``blocks`` (``new_annotation``'s) at the page's top
    level (or under ``parent``) right after its last annotation block, in
    order; the end when it has none."""
    blocks = list(blocks or ())
    if not blocks:
        return []
    places = _Places(conn)
    parent = parent or page_id
    positions = places.after_annotations(parent, len(blocks))
    return [{"op": "insert", "id": b["id"], "parent": parent, "position": pos, "content": b.get("content") or "",
             "props": b.get("props") or {}} for b, pos in zip(blocks, positions)]


def annotation_update(ws: str, block_id: str, change: dict) -> dict | None:
    """The op for ``zotero_annotations.apply_fields``' answer on the block
    ``block_id``: its props patch and content, an ink file stored first and
    named by ``ink_url``, never merged with the stored drawing (no
    ``base_props``: Zotero's strokes are taken as they are). The
    ``conflicts`` it lists are the engine's. None when nothing changes."""
    props = dict(change.get("props") or {})
    if change.get("ink") is not None:
        props["ink_url"] = _store_ink(ws, change["ink"])
    op = {"op": "set", "id": block_id}
    if props:
        op["props"] = props
    if change.get("content") is not None:
        op["content"] = change["content"]
    return op if len(op) > 2 else None


def annotation_delete(conn, block_id: str, *, keep_children: bool = True) -> list[dict]:
    """The ops deleting an annotation block Zotero deleted: the blocks under it
    are lifted first to its place, in order, so notes, pictures and annotations
    Gamma keeps under it stay (``keep_children`` False deletes them with it).
    [] for a block gone."""
    places = _Places(conn)
    parent = places.parent_of(block_id)
    if parent is None:
        return []
    out, anchor = [], block_id
    if keep_children:
        for child, _ in list(places.children(block_id)):
            position = places.place(child, parent, anchor)
            out.append({"op": "move", "id": child, "parent": parent, "position": position})
            anchor = child
    out.append({"op": "delete", "id": block_id})
    return out
