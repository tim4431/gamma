"""Block operations — THE write path for a page's blocks.

A page's notes change through small, typed operations rather than a whole-
tree replace, so several clients (two browsers of one account, share editors)
can edit one page at once and only the touched rows move:

- ``set {id, content?, base?, props?, base_props?}`` — ``content``
  replaces the text; with ``base`` (the text the client's change was
  computed from) it is applied as a patch when the block changed
  meanwhile, so two people editing different spans of one block both keep
  their edit (gamma/textmerge.py; the applied op carries the merged text).
  ``props`` is a PATCH (``{key: value | null}``, null deletes), so
  unrelated properties never conflict (Figma's property-level rule).
  ``base_props`` holds the values the patch was computed from; an ink
  group's new ``ink_url`` whose base is not the stored one is merged into
  the stored drawing by stroke (``gamma/ink.py`` ``merge_ink``, the applied
  op names the merged file), so two people drawing in one group both keep
  their strokes, and a text box's ``text_box`` is merged key by key
  (``gamma/text_box.py`` ``merge_text_box``, the applied op carries the
  merged box), so one person's move survives another's typing. Other keys
  stay last-writer-wins.
- ``insert {id, parent, position?, content, props}`` — the client mints the
  id and the fractional position; a position that collides with a sibling is
  re-keyed here and the applied op carries the final value. Re-inserting a
  known id of the page (a retried batch, a client re-sending a block it
  thought lost) is create-if-absent: the block stays as it is and the
  applied op echoes it, so a newer edit or move someone made is never undone.
- ``move {id, parent, position?}`` — cycle-checked, same collision rule.
- ``delete {id}`` — the subtree.

Every touched block (and every insert parent) must be inside the page; the
page root may only be ``set`` (a share editor: content only — rename — never
its properties), never moved or deleted. A page's filing
(``properties.folders`` / ``labels``) must be a list of block ids; it is
stored as given, an id of a block this copy does not have included — a
mirror's page may name a folder its tree has not brought yet, a published
page folders the share host never sees — and every reader passes such an id
by (gamma/blocks_store.py ``filing``).

The folder and label trees are pseudo-pages to this path
(gamma/blocks_store.py ``TREES``): a batch may name ``folders`` or
``labels`` as its page and touches the blocks under it — a folder's rename
is one ``set`` of its content, a move (or a reorder) one ``move``, a new
folder one ``insert``; labels stay flat (their parent is always
``labels``). The reserved row itself is never set, moved or deleted. A
pseudo-page has its op log, its room and its row of the change log like
any page, so the home view and a mirror follow it as they follow a page.

Only touched rows get ``updated_at``; the page is touched once per batch
(``touch_page``: the root's stamp — home-feed order — and the workspace
change log's next seq). One batch is one transaction — the notes index
follows the touched rows in it (db.BLOCK_FTS_SCHEMA's triggers) — and one
row of the per-page op log (``page_ops``, ``seq`` counting up per page) —
live clients follow the log over the page's websocket (gamma/collab.py), a
reconnecting one reads it back with ``ops_since``. A batch refused because
someone else changed the page meanwhile (the block is gone, lives in another
page now, or the move would make a cycle) says so in ``OpError.conflict``,
with the op's index, so a client can drop that op and send the rest.

A client names each batch (``batch``, the same id on every retry of it),
and the batch's log row keeps the name (``batch_id``) with the writer's
caret as stored (``cursor``): a retry finds that row under the write lock
and gets the same answer instead of applying twice, across restarts. A
retry whose row was pruned from the log is applied again.

``ws`` everywhere below is the workspace id (docs/dev/workspaces.md),
``actor`` who makes the change (an account's id, or a writer's label —
gamma/auth.py ``actor_of``). Server-side writers (the block endpoints, AI
tools, page rename/attach, metadata) go through ``apply_ops`` too, so
everything a page's viewers see comes from one code path and one log.
"""

import json
import math
import re
from datetime import timedelta
from typing import Annotated, Literal, Union

from fastapi import HTTPException
from fractional_indexing import FIError, generate_key_between, validate_order_key
from pydantic import BaseModel, Field, model_validator

from . import collab, pdf_index, textmerge, upload_gc
from . import ink as inkmod
from .text_box import merge_text_box
from .blocks_store import (
    BLOCK_COLUMNS, FOLDERS, LABELS, STORED_COLUMNS, TRASH, TREES, block_to_dict, delete_subtree, ensure_reserved,
    fetch_subtree, filing_ids, folder_inserts, free_position, is_op_page, label_inserts, last_child_position,
    move_subtree_to_page, subtree_refs, touch_page, trashed_page, valid_block_id, write_lock)
from .db import connect_pages_db, format_stamp, page_now, parse_stamp, ws_uploads_dir
from .logbuf import log
from .storage import content_digest, pdf_url, store_file, upload_refs

MAX_OPS = 500
MAX_CONTENT = 200_000
# The log per page is bounded three ways — a set logs the block's whole
# text, so typing in one long block would otherwise keep KEEP_OPS copies of
# it. The newest row always stays (seq continues from it).
KEEP_OPS = 300                # rows
KEEP_OPS_HOURS = 24           # age
KEEP_OPS_BYTES = 2_000_000    # payload
PRUNE_EVERY = 16     # prune check cadence (every Nth seq, or a batch over its share of the bytes)
# A catch-up (ops_since) longer than this is answered "reload the tree"
# (410) instead: refetching the page is cheaper than replaying it.
CATCHUP_MAX_BATCHES = 200
CATCHUP_MAX_BYTES = 1_000_000
_SURROGATE = re.compile(r"[\ud800-\udfff]")

# Conflict codes: the page changed under the batch, so an op no longer applies.
MISSING = "missing"  # the block or the parent is gone
MOVED = "moved"      # it lives in another page now
CYCLE = "cycle"      # the move would put a block inside itself


class OpError(Exception):
    """A refused batch. ``missing``: the unknown block or parent id behind a
    404, so a client that holds that block can send it again as an insert.
    ``conflict`` (MISSING / MOVED / CYCLE): refused because someone else
    changed the page meanwhile, not because the batch is malformed;
    ``index`` is the refused op's place in the batch."""

    def __init__(self, status: int, detail: str, missing: str = "", conflict: str = ""):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.missing = missing
        self.conflict = conflict
        self.index: int | None = None


def storable(value, *, finite: bool = True):
    """``value`` with every lone UTF-16 surrogate in its strings (dict keys
    and nested values too) replaced by U+FFFD. A browser can send half an
    emoji ("\\ud83d" in JSON); SQLite cannot encode it, so one such string
    would fail its whole batch on every retry. A number that is not finite
    is refused, OpError(400): Python's JSON reader takes a bare ``NaN`` or
    ``Infinity`` (and ``1e999``), which would be stored and which no JSON
    answer can carry back. ``finite=False`` lets one through, for the tree
    reads that send a stored one as null (gamma/json_response.py)."""
    if isinstance(value, str):
        if not _SURROGATE.search(value):
            return value
        # a high + low half that arrived as two escapes become the one character
        return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if isinstance(value, dict):
        return {storable(k, finite=finite): storable(v, finite=finite) for k, v in value.items()}
    if isinstance(value, list):
        return [storable(v, finite=finite) for v in value]
    if finite and isinstance(value, float) and not math.isfinite(value):
        raise OpError(400, f"not a finite number: {json.dumps(value)}")
    return value


class StorableBody(BaseModel):
    """A request body whose strings are made ``storable`` as it is parsed:
    half an emoji becomes U+FFFD before it can reach SQLite (or a JSON
    answer, which cannot encode it either), and a NaN or an infinity is
    refused with the op batch's 400 and wording rather than a 422."""

    @model_validator(mode="before")
    @classmethod
    def _storable(cls, data):
        try:
            return storable(data)
        except OpError as e:  # not a ValueError, so pydantic passes the 400 on as it is
            raise HTTPException(status_code=e.status, detail=e.detail) from None


class SetOp(BaseModel):
    op: Literal["set"]
    id: str
    content: str | None = None
    base: str | None = None  # the text `content` was edited from (three-way merge)
    props: dict | None = None
    base_props: dict | None = None  # the values `props` was computed from (an ink group's, a text box's merge)


class InsertOp(BaseModel):
    op: Literal["insert"]
    id: str
    parent: str
    position: str | None = None
    content: str = ""
    props: dict = Field(default_factory=dict)


class MoveOp(BaseModel):
    op: Literal["move"]
    id: str
    parent: str
    position: str | None = None


class DeleteOp(BaseModel):
    op: Literal["delete"]
    id: str


Op = Annotated[Union[SetOp, InsertOp, MoveOp, DeleteOp], Field(discriminator="op")]


class CursorState(BaseModel):
    """The writer's caret at the moment it sent the batch (see collab.py)."""
    block: str = ""
    anchor: int = -1
    head: int = -1


class OpsRequest(BaseModel):
    client: str = ""
    batch: str = ""  # the client's id for this batch, the same on every retry of it
    ops: list[Op]
    cursor: CursorState | None = None


def props_patch(old: dict, new: dict) -> dict:
    """The ``set.props`` patch turning ``old`` into ``new`` (keys gone from
    ``new`` become null). Lets writers that compute a full property dict
    express it as an op."""
    patch = {k: v for k, v in new.items() if old.get(k) != v or k not in old}
    for k in old:
        if k not in new:
            patch[k] = None
    return patch


class _Batch:
    """One apply_ops call: the page, the timestamp, an ancestry cache."""

    def __init__(self, conn, page_id: str, now: str, share_scoped: bool, cursor: dict | None = None,
                 merges: dict | None = None):
        self.conn = conn
        self.ws = getattr(conn, "ws", "")  # the workspace whose files an ink merge reads
        self.page_id = page_id
        self.tree = page_id in TREES  # a batch on the folder or label tree
        self.now = now
        self.share_scoped = share_scoped
        self.cursor = cursor  # the writer's caret, remapped when its block's text is merged
        self.merges = {} if merges is None else merges  # text merges by their texts (``_merged``)
        self._page_of: dict[str, str | None] = {page_id: page_id}
        self.applied: list[dict] = []
        self.deleted: list[str] = []
        # upload names the ops stopped / started referencing (gamma/upload_gc.py)
        self.dropped: set[str] = set()
        self.added: set[str] = set()
        self.doc_deleted = False  # a deleted block carried a PDF (doc_id)
        self.chats_filed = 0  # folder chats a tree delete filed into the library's history

    def refs_changed(self, old: set, new: set) -> None:
        self.dropped |= old - new
        self.added |= new - old

    def page_of(self, block_id: str) -> str | None:
        """The page a block lives in: its ``page_id`` — ``TRASH`` for one in
        Recently deleted (no batch reaches it), '' for a reserved row, None
        for an unknown block. Memoized per batch."""
        if block_id not in self._page_of:
            row = self.conn.execute(
                "SELECT b.page_id, p.parent_id FROM unified_blocks b "
                "LEFT JOIN unified_blocks p ON p.id = b.page_id WHERE b.id = ?", (block_id,)).fetchone()
            self._page_of[block_id] = None if not row else TRASH if row[1] == TRASH else row[0]
        return self._page_of[block_id]

    def require_in_page(self, block_id: str, what: str = "block") -> None:
        page = self.page_of(block_id)
        if page is None:
            raise OpError(404, f"no such {what}: {block_id}", missing=block_id, conflict=MISSING)
        if page != self.page_id:
            raise OpError(403, f"{what} {block_id} is outside this page", conflict=MOVED)

    def free_position(self, parent: str, position: str | None, block_id: str) -> str:
        if position is not None:
            try:
                validate_order_key(position)
            except FIError as e:
                raise OpError(400, f"invalid position: {e}")
        return free_position(self.conn, parent, position, block_id)

    def check_parent(self, parent: str, block_id: str | None) -> None:
        if parent == "root":
            raise OpError(403, "ops never create or move pages")
        if self.page_id == LABELS and parent != LABELS:
            raise OpError(400, "labels do not nest")
        self.require_in_page(parent, "parent")
        if block_id and (parent == block_id
                         or parent in {r[0] for r in fetch_subtree(self.conn, block_id)}):
            raise OpError(400, "cannot move a block into its own subtree", conflict=CYCLE)

    # --- the four ops --------------------------------------------------------

    def set(self, op: dict) -> None:
        block_id = op["id"]
        row = self.conn.execute(
            "SELECT content, properties FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
        if not row:
            raise OpError(404, f"no such block: {block_id}", missing=block_id, conflict=MISSING)
        self.require_in_page(block_id)
        content, patch = op.get("content"), op.get("props")
        if content is None and patch is None:
            return
        if block_id == self.page_id and self.tree:
            raise OpError(403, f"the reserved row {block_id!r} cannot be changed")
        if patch and block_id == self.page_id and self.share_scoped:
            raise OpError(403, "share editors cannot change page settings")
        if patch and block_id == self.page_id:
            patch = _filing_patch(patch)
        if content is not None and len(content) > MAX_CONTENT:
            raise OpError(413, "content too long")
        if content is not None and op.get("base") is not None:
            content = _merged(self.merges, block_id, op["base"], content, row[0] or "", self.cursor)
        props = json.loads(row[1] or "{}")
        if patch and block_id == self.page_id:
            patch = _attachment_patch(patch, props)
        base_props = op.get("base_props")
        if patch and base_props:
            patch = self.merge_ink(props, patch, base_props)
            if "text_box" in patch and "text_box" in base_props:
                patch = {**patch, "text_box": merge_text_box(props.get("text_box"), patch["text_box"],
                                                              base_props["text_box"])}
        echo = {"op": "set", "id": block_id}
        sets, values = ["updated_at = ?"], [self.now]
        if content is not None:
            sets.append("content = ?")
            values.append(content)
            echo["content"] = content
            # An explicit title write is user intent: drop the automatic-title
            # marker so a slow metadata lookup can't overwrite the rename.
            if props.pop("auto_title", None) is not None and not (patch and "auto_title" in patch):
                patch = {**(patch or {}), "auto_title": None}
        if patch is not None:
            for k, v in patch.items():
                if v is None:
                    props.pop(k, None)
                else:
                    props[k] = v
            echo["props"] = patch
        sets.append("properties = ?")
        values.append(json.dumps(props))
        values.append(block_id)
        self.conn.execute(f"UPDATE unified_blocks SET {', '.join(sets)} WHERE id = ?", values)
        new_content = (row[0] or "") if content is None else content
        new_props = (row[1] or "{}") if patch is None else props
        self.refs_changed(upload_refs(row[0] or "", row[1] or "{}"), upload_refs(new_content, new_props))
        self.applied.append(echo)

    def merge_ink(self, props: dict, patch: dict, base_props: dict) -> dict:
        """``patch`` for an ink group whose drawing changed since the writer
        read it (its base ``ink_url`` is not the stored one): the writer's
        file merged into the stored one stroke by stroke (gamma/ink.py
        ``merge_ink``), and the patch naming the result with its stroke
        count and box. As sent when there is nothing to merge, a file
        cannot be read or the result is over the file budgets.

        A merged file is written only when it is new. One that is stored
        already stays: the purge deletes only under the write lock this
        batch holds, so it is still there when the batch commits the
        reference."""
        new, base, now = patch.get("ink_url"), base_props.get("ink_url"), props.get("ink_url") or ""
        if not (isinstance(new, str) and new and isinstance(base, str)) or now in (base, new) or not self.ws:
            return patch
        uploads = ws_uploads_dir(self.ws)
        ours = inkmod.read_upload(uploads, new)
        theirs = inkmod.read_upload(uploads, now) if now else None
        if ours is None or (now and theirs is None):
            return patch

        def empty():
            return inkmod.InkFile(format=inkmod.FORMAT, version=inkmod.VERSION, space=ours.space, strokes=[])

        try:
            merged, _clean = inkmod.merge_ink(inkmod.read_upload(uploads, base) if base else empty(),
                                              ours, theirs or empty())
        except ValueError:
            return patch
        if merged is ours:
            url = new
        elif merged is theirs:
            url = now
        else:
            data = inkmod.dumps(merged)
            name = f"{content_digest(data)}.ink"
            if not (uploads / name).is_file():
                try:
                    store_file(self.ws, data, ".ink")
                except Exception as e:  # noqa: BLE001 — storage full: the writer's file as sent
                    log.warning(f"[ops] ink merge in {self.page_id} not stored: {e}")
                    return patch
            url = f"/api/uploads/{name}"
        out = {**patch, "ink_url": url, "ink_strokes": len(merged.strokes)}
        if "pdf_position" in patch:
            out["pdf_position"] = inkmod.pdf_position(merged)
        return out

    def insert(self, op: dict) -> None:
        block_id, parent = op["id"], op["parent"]
        if not valid_block_id(block_id):
            raise OpError(400, f"invalid block id: {block_id!r}")
        content = op.get("content") or ""
        if len(content) > MAX_CONTENT:
            raise OpError(413, "content too long")
        props = op.get("props") or {}
        existing = self.conn.execute(
            "SELECT parent_id, position, content, properties FROM unified_blocks WHERE id = ?",
            (block_id,)).fetchone()
        if existing and existing[0] != TRASH and self.page_of(block_id) == TRASH:
            # A block a page in Recently deleted still holds, inserted here (a
            # mirror bringing over a block the other side moved out of that
            # page before deleting it): it leaves the trashed copy, new here.
            gone = fetch_subtree(self.conn, block_id)
            delete_subtree(self.conn, block_id)
            for r in gone:
                self._page_of.pop(r[0], None)
            self.refs_changed(subtree_refs(gone), set())
            existing = None
        if existing:
            # A retried batch, or a client re-sending a block it thought the
            # server lost: create-if-absent. The block stays as it is — an
            # edit, move or property change someone made since is never
            # undone — and the echo carries it, so the sender converges.
            self.require_in_page(block_id)
            if block_id == self.page_id:
                raise OpError(400, "the page itself cannot be inserted")
            self.applied.append({"op": "insert", "id": block_id, "parent": existing[0],
                                 "position": existing[1], "content": existing[2] or "",
                                 "props": json.loads(existing[3] or "{}")})
            return
        self.check_parent(parent, None)
        position = self.free_position(parent, op.get("position"), block_id)
        self.conn.execute(
            f"INSERT INTO unified_blocks ({STORED_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (block_id, parent, position, content, json.dumps(props), self.now, self.now, self.page_id))
        self._page_of[block_id] = self.page_id
        self.refs_changed(set(), upload_refs(content, props))
        self.applied.append({"op": "insert", "id": block_id, "parent": parent,
                             "position": position, "content": content, "props": props})

    def move(self, op: dict) -> None:
        block_id, parent = op["id"], op["parent"]
        self.require_in_page(block_id)
        if block_id == self.page_id:
            raise OpError(403, "the page itself cannot be moved")
        self.check_parent(parent, block_id)
        position = self.free_position(parent, op.get("position"), block_id)
        self.conn.execute(
            "UPDATE unified_blocks SET parent_id = ?, position = ?, updated_at = ? WHERE id = ?",
            (parent, position, self.now, block_id))
        self.applied.append({"op": "move", "id": block_id, "parent": parent, "position": position})

    def delete(self, op: dict) -> None:
        block_id = op["id"]
        page = self.page_of(block_id)
        if page is None:
            return  # already gone: a retried or concurrent delete
        if page != self.page_id:
            raise OpError(403, f"block {block_id} is outside this page", conflict=MOVED)
        if block_id == self.page_id:
            raise OpError(403, "the page itself cannot be deleted through ops")
        rows = fetch_subtree(self.conn, block_id)
        delete_subtree(self.conn, block_id)
        for r in rows:
            self._page_of[r[0]] = None
            self.deleted.append(r[0])
            self.doc_deleted = self.doc_deleted or '"doc_id"' in (r[4] or "")
        self.dropped |= subtree_refs(rows)
        if self.page_id == FOLDERS:
            # A folder's conversations are never dropped with it, whichever
            # path deletes it (DELETE /folders/{id}, or a tree batch as a
            # mirror or the iPad relays one): filed into the library chat's
            # history in this transaction. Local import: the router imports ops.
            from .routers.chats import file_into_home
            self.chats_filed += file_into_home(self.conn, [r[0] for r in rows])
        self.applied.append({"op": "delete", "id": block_id})


def _memo(memo: dict, fn, *args):
    """``fn(*args)``, kept in ``memo`` by the function and its arguments."""
    key = (fn, *args)
    if key not in memo:
        memo[key] = fn(*args)
    return memo[key]


def _merged(merges: dict, block_id: str, base: str, content: str, stored: str, cursor: dict | None) -> str:
    """The text a ``set`` of ``content``, edited from ``base``, stores over
    the block's text ``stored`` now, the writer's ``cursor`` remapped into
    it when it is in that block. When someone else changed the block since
    the writer read it, the writer's edit is applied as a patch on the
    current text (gamma/textmerge.py). An edit that is already the text —
    the same change made twice, a retried batch — has nothing to merge:
    patching it in again would double it. The diffs are kept in ``merges``
    by their texts, so a merge ``_premerge`` computed before the write lock
    is not computed again under it."""
    if len(base) > MAX_CONTENT or stored in (base, content) or base == content:
        return content
    merged, _clean = _memo(merges, textmerge.merge, base, content, stored)
    if cursor and cursor.get("block") == block_id and merged != content:
        for end in ("anchor", "head"):
            at = cursor.get(end, -1)
            cursor[end] = _memo(merges, textmerge.map_offset, content, merged, at) if at >= 0 else -1
    return merged


def _premerge(conn, page_id: str, ops: list[dict], cursor: dict | None) -> dict:
    """The batch's text merges (``_merged``) against the texts its blocks
    hold now, computed before the write lock is taken: the merge of a block
    edited in many places (an offline copy's round, the agent's edit of a
    long note) takes tens of milliseconds, which under the lock every
    writer of the workspace would wait out. Returns the memo the batch
    starts from under the lock, where a merge whose block still holds the
    text merged into is taken from it and any other is computed as before:
    the batch stores the same either way."""
    sets = [op for op in ops if op.get("op") == "set" and isinstance(op.get("id"), str)
            and isinstance(op.get("content"), str) and isinstance(op.get("base"), str)
            and len(op["content"]) <= MAX_CONTENT]
    merges: dict = {}
    if not sets:
        return merges
    rows = conn.execute(  # by id: the page's index would walk all of its blocks
        "SELECT id, content, page_id FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps([op["id"] for op in sets]),)).fetchall()
    stored = {r[0]: r[1] or "" for r in rows if r[2] == page_id}  # the lock refuses any other block
    cursor = dict(cursor) if cursor else None  # remapped here only to fill the memo
    for op in sets:
        if op["id"] in stored:
            _merged(merges, op["id"], op["base"], op["content"], stored[op["id"]], cursor)
    return merges


def _filing_patch(patch: dict) -> dict:
    """A page root's ``patch`` with its ``folders`` / ``labels`` (a list of
    block ids, or null) without repeats; an empty list removes the key.
    400 for anything that is no list of ids."""
    for tree in TREES:
        if patch.get(tree) is None:
            continue
        ids = filing_ids(patch[tree])
        if ids is None:
            raise OpError(400, f"{tree} must be a list of block ids")
        patch = {**patch, tree: ids or None}
    return patch


def _attachment_patch(patch: dict, props: dict) -> dict:
    """A page root's ``patch`` against its stored ``props``, a
    ``source_url`` that is the page's ``doc_id``'s stored copy removed
    instead of stored (blocks_store.page_attachment derives it)."""
    doc_id = patch.get("doc_id", props.get("doc_id"))
    if doc_id and patch.get("source_url") == pdf_url(doc_id):
        patch = {**patch, "source_url": None}
    return patch


def _log(conn, page_id: str, actor: str, client: str, now: str, applied: list, after: int = 0, *,
         batch_id: str = "", cursor: dict | None = None) -> int:
    """One row of the page's log. ``batch_id`` and ``cursor`` (the caret as
    stored, kept for a named batch only) are what a retry of the batch is
    answered from (``_replayed``); every other writer logs ''."""
    seq = max(conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM page_ops WHERE page_id = ?", (page_id,)).fetchone()[0], after) + 1
    payload = json.dumps(applied)
    caret = json.dumps(cursor) if batch_id and cursor is not None else ""
    conn.execute(
        "INSERT INTO page_ops (page_id, seq, actor, client, at, ops, batch_id, cursor) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (page_id, seq, actor, client, now, payload, batch_id, caret))
    if seq % PRUNE_EVERY == 0 or len(payload) > KEEP_OPS_BYTES // PRUNE_EVERY:
        _prune(conn, page_id, now)
    return seq


def _prune(conn, page_id: str, now: str) -> None:
    """Drop the page's oldest log rows past KEEP_OPS rows, KEEP_OPS_HOURS of
    age or KEEP_OPS_BYTES of payload, keeping the newest row whatever its
    size. Only a prefix of the log goes, so ``ops_since`` still tells a gap
    by the lowest seq left."""
    cutoff = format_stamp(parse_stamp(now) - timedelta(hours=KEEP_OPS_HOURS))
    # length() of the ASCII JSON payload (json.dumps escapes the rest) is its byte count
    rows = conn.execute(
        "SELECT seq, at, length(ops) FROM page_ops WHERE page_id = ? ORDER BY seq DESC", (page_id,)).fetchall()
    keep_from, size = None, 0
    for i, (seq, at, n) in enumerate(rows):
        size += n
        if i and (i >= KEEP_OPS or size > KEEP_OPS_BYTES or at < cutoff):
            break
        keep_from = seq
    if keep_from is not None:
        conn.execute("DELETE FROM page_ops WHERE page_id = ? AND seq < ?", (page_id, keep_from))


def _replayed(conn, page_id: str, client: str, batch_id: str) -> dict | None:
    """``apply_ops``'s result for the batch ``client`` named ``batch_id``
    on the page, applied earlier (a retry whose answer was lost on the way
    back), read back from its row of the log. None when the log holds no
    such row: never applied, or pruned since. ``replayed`` marks it, so
    nothing is fanned out or derived again."""
    row = conn.execute(
        "SELECT seq, at, actor, ops, cursor FROM page_ops "
        "WHERE page_id = ? AND client = ? AND batch_id = ? AND batch_id != ''",  # the partial index's term
        (page_id, client, batch_id)).fetchone()
    if not row:
        return None
    result = {"page_id": page_id, "seq": row[0], "at": row[1], "actor": row[2], "client": client,
              "ops": json.loads(row[3]), "deleted_ids": [], "dropped_uploads": [],
              "doc_deleted": False, "replayed": True}
    if row[4]:
        result["cursor"] = json.loads(row[4])
    return result


def apply_ops(conn, page_id: str, ops: list[dict], *, actor: str, client: str = "",
              share_scoped: bool = False, cursor: dict | None = None, batch_id: str = "") -> dict:
    """Apply one batch inside one transaction (committed here) and log it.
    Returns ``{page_id, seq, at, actor, client, ops (as applied), deleted_ids,
    dropped_uploads, doc_deleted, chats_filed, cursor?}`` — hand it to
    ``after_commit`` for the derived-data work and the room fan-out.
    ``chats_filed``: the folder conversations a tree delete filed into the
    library chat's history (``after_commit`` adds ``shares_stopped``). ``dropped_uploads``: the
    upload names the batch stopped referencing (the names it started
    referencing have their orphan rows cleared in the same transaction —
    gamma/upload_gc.py); ``doc_deleted``: a deleted block carried a PDF.
    ``cursor`` (``{block, anchor, head}``, the writer's
    caret in the text it sent) comes back remapped into the text actually
    stored when a merge changed it; the text merges are computed before the
    write lock is taken (``_premerge``). Raises ``OpError`` (nothing
    written) on a bad op. ``batch_id``: the client's name for the batch,
    kept on its log row with the caret: one the page's log holds for that
    client is answered from that row again (``replayed``), not re-applied, a restart
    in between or not; one pruned from the log since is applied again. Lone
    surrogates in the ops' strings are stored as U+FFFD. ``page_id`` is a
    page of the library or a pseudo-page (``TREES``)."""
    ops = _checked(ops)
    merges = _premerge(conn, page_id, ops, cursor)  # the slow part of a batch, before the lock
    write_lock(conn)  # then at once: seq is per page, and a retry waiting here finds the row its first try wrote
    try:
        if not is_op_page(conn, page_id):
            raise OpError(404, "page not found")
        replay = _replayed(conn, page_id, client, batch_id) if batch_id else None
        if replay:
            conn.rollback()
            return replay
        result = _apply(conn, page_id, ops, actor=actor, client=client, share_scoped=share_scoped,
                        cursor=dict(cursor) if cursor else None, batch_id=batch_id, merges=merges)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return result


def apply_batches(conn, batches: list[tuple[str, list[dict]]], *, actor: str) -> list[dict]:
    """Several batches, ``[(page_id, ops), …]`` — each its own page's, each
    logged on its page — in the caller's transaction: what one request
    changes across pages as one step (a folder deleted together with the
    ids it leaves on the pages filed in it). Takes the write lock; the
    caller commits, or rolls back and nothing is written, then hands each
    result to ``after_commit``. Raises ``OpError`` as ``apply_ops`` does."""
    write_lock(conn)
    results = []
    for page_id, ops in batches:
        ops = _checked(ops)
        if not is_op_page(conn, page_id):
            raise OpError(404, "page not found")
        results.append(_apply(conn, page_id, ops, actor=actor))
    return results


def _checked(ops: list[dict]) -> list[dict]:
    if not ops:
        raise OpError(400, "no ops")
    if len(ops) > MAX_OPS:
        raise OpError(413, f"too many ops in one batch (>{MAX_OPS})")
    return storable(ops)


def _apply(conn, page_id: str, ops: list[dict], *, actor: str, client: str = "",
           share_scoped: bool = False, cursor: dict | None = None, batch_id: str = "",
           merges: dict | None = None) -> dict:
    """One checked batch on ``page_id`` inside the caller's transaction,
    under its write lock: the ops applied, the page touched, the batch
    logged (under ``batch_id``, with the caret as stored). ``merges``: the
    text merges ``_premerge`` computed before the lock. Returns
    ``apply_ops``' result; commits nothing."""
    now = page_now()
    batch = _Batch(conn, page_id, now, share_scoped, cursor, merges)
    for i, op in enumerate(ops):
        kind = op.get("op")
        try:
            if kind == "set":
                batch.set(op)
            elif kind == "insert":
                batch.insert(op)
            elif kind == "move":
                batch.move(op)
            elif kind == "delete":
                batch.delete(op)
            else:
                raise OpError(400, f"unknown op: {kind!r}")
        except OpError as e:
            e.index = i
            raise
    # a name one op dropped and another took up (a cut and paste within
    # the batch) changed nothing
    dropped = batch.dropped - batch.added
    upload_gc.claim(conn, batch.added - batch.dropped)
    touch_page(conn, page_id, actor, now=now)
    seq = _log(conn, page_id, actor, client, now, batch.applied, batch_id=batch_id, cursor=cursor)
    result = {"page_id": page_id, "seq": seq, "at": now, "actor": actor, "client": client,
              "ops": batch.applied, "deleted_ids": batch.deleted, "dropped_uploads": sorted(dropped),
              "doc_deleted": batch.doc_deleted, "chats_filed": batch.chats_filed}
    if cursor is not None:
        result["cursor"] = cursor
    return result


# Called after every committed write with ``(ws, client, page_id)``: an
# offline copy's sync-on-change (sync_engine._on_commit). Each listener
# registers itself at import.
commit_listeners: list = []


def notify_commit(ws: str, client: str = "", page_id: str = "") -> None:
    """Tell the commit listeners a write committed (``page_id`` "" for a
    write across the workspace, such as a restore)."""
    for fn in commit_listeners:
        try:
            fn(ws, client, page_id)
        except Exception as e:  # noqa: BLE001 — a listener must never break a write
            log.warning(f"[ops] commit listener: {e}")


def after_commit(ws: str, conn, result: dict) -> dict:
    """After a committed batch: the fan-out to the page's room first, so
    batches committed one after the other by several worker threads reach
    the room in seq order as a rule (the client's ordered inbox copes with
    the exception). Then the derived data: the upload names the batch
    dropped go to the orphan check (gamma/upload_gc.py, debounced on its own
    thread, never on the request), and when a deleted block carried a PDF,
    data.db's rows of papers nothing carries any more go
    (``pdf_index.purge_unused``; it may wait on data.db's lock). A batch
    that changed what a folder holds — a page refiled, a folder moved or
    deleted — re-checks the room peers a folder share admitted, and the
    shares of folders that are gone stop (``shares_stopped`` in the
    result), whichever path deleted them."""
    collab.publish_ops(ws, result)
    if _refiles(result):
        collab.revalidate_shares(ws)
    if result["page_id"] == FOLDERS and result["deleted_ids"]:
        from .routers.shares import delete_folder_shares  # local: the router imports ops' neighbours
        result["shares_stopped"] = delete_folder_shares(ws, result["deleted_ids"])
    upload_gc.schedule(ws, result["dropped_uploads"])
    if result["doc_deleted"]:
        pdf_index.purge_unused(ws, conn)
    notify_commit(ws, result.get("client") or "", result.get("page_id") or "")
    return result


def _refiles(result: dict) -> bool:
    """Whether an applied batch may have changed a folder share's pages: a
    page's ``folders`` set, a folder moved or deleted."""
    if result["page_id"] == FOLDERS:
        return any(op["op"] in ("move", "delete") for op in result["ops"])
    return any(op["op"] == "set" and op["id"] == result["page_id"] and FOLDERS in (op.get("props") or {})
               for op in result["ops"])


def commit_ops(ws: str, page_id: str, ops: list[dict], *, actor: str, client: str = "",
               share_scoped: bool = False, cursor: dict | None = None, batch_id: str = "") -> dict:
    """``apply_ops`` + ``after_commit`` on a fresh connection. ``cursor``
    (``{block, anchor, head}``): the writer's caret in the text the batch
    produces, fanned out with the batch so peers place it against the same
    text (a standalone presence message would reach them first, in
    offsets their copy doesn't have yet). A replayed batch (``batch_id``
    found in the page's log) was fanned out the first time and is only
    answered."""
    with connect_pages_db(ws) as conn:
        result = apply_ops(conn, page_id, ops, actor=actor, client=client,
                           share_scoped=share_scoped, cursor=cursor, batch_id=batch_id)
        if result.get("replayed"):
            return result
        return after_commit(ws, conn, result)


def ensure_filing(ws: str, conn, *, paths=(), labels=(), under: str = "",
                  actor: str) -> tuple[dict, dict]:
    """The folders at ``paths`` (lists of names, from the top or from the
    folder ``under``, "" = the top) and the labels called ``labels``, made where they are
    missing — one batch on each tree that needs one
    (``blocks_store.folder_inserts`` / ``label_inserts``), committed — for a
    writer about to file pages by name (an import, a clip, the agent).
    Returns ``({tuple(names): folder id}, {name: label id})``."""
    folder_ops, folder_ids = folder_inserts(conn, paths, under)
    label_ops, label_ids = label_inserts(conn, labels)
    for tree, tree_ops in ((FOLDERS, folder_ops), (LABELS, label_ops)):
        if tree_ops:
            after_commit(ws, conn, apply_ops(conn, tree, tree_ops, actor=actor))
    return folder_ids, label_ids


def delete_page(ws: str, conn, page_id: str, *, actor: str, client: str = "") -> dict:
    """Delete a page for good, in one transaction committed here: the
    subtree (its notes index rows with it), its op log and its AI chats —
    the active conversation and the history of its bucket; this is the one
    path that drops them — go, and its row of the change log turns
    ``deleted`` (``touch_page``), so a copy of the workspace can tell a
    deleted page from one it never had (a page purged from Recently deleted
    keeps the row its trashing wrote: to a copy it went then). Then the
    upload names the page held go to the orphan check, data.db's rows of
    papers no block carries any more are purged, and the page's room is
    told to reload (which surfaces the 404). Returns ``{deleted_ids,
    dropped_uploads}``.

    Deleting a page in the app moves it to the trash (``trash_page``); this
    runs on Delete permanently, Empty, the 30-day purge (gamma/trash.py) and
    a share host's deletes. Pages are not blocks of any page, so the page
    writers here are the ones outside the op batches — every other block
    write goes through ``apply_ops``. ``client`` names the writer to the
    commit listeners like a batch's."""
    write_lock(conn)
    try:
        rows = fetch_subtree(conn, page_id)
        trashed = any(r[0] == page_id and r[1] == TRASH for r in rows)
        delete_subtree(conn, page_id)
        conn.execute("DELETE FROM page_ops WHERE page_id = ?", (page_id,))
        conn.execute("DELETE FROM chats WHERE bucket = ?", (page_id,))
        conn.execute("DELETE FROM chat_history WHERE bucket = ?", (page_id,))
        if not trashed:
            touch_page(conn, page_id, actor, "deleted")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    deleted_ids = [r[0] for r in rows]
    dropped = sorted(subtree_refs(rows))
    upload_gc.schedule(ws, dropped)
    pdf_index.purge_unused(ws, conn)
    collab.publish_reload(ws, page_id)
    notify_commit(ws, client, page_id)
    return {"deleted_ids": deleted_ids, "dropped_uploads": dropped}


def trash_page(ws: str, conn, page_id: str, *, actor: str, client: str = "") -> dict:
    """Move a page to Recently deleted (gamma/trash.py): its root goes under
    the reserved ``trash`` block, stamped ``deleted_at`` / ``deleted_by``;
    its blocks, files, chats and op log stay as they are (every row keeps
    its ``page_id``, the trash entry a 404 names). To everything
    else it is deleted: listings and searches pass it by, ops to it are
    refused, its room hears ``trashed`` (an open tab keeps what its typist
    has not sent yet, for a restore to take back), and its row of the change
    log turns ``deleted`` as on a hard delete, so a copy of the workspace
    removes its copy. ``restore_page`` brings it back, ``delete_page``
    removes it for good. Commits; returns its trash entry. OpError(404)
    unless ``page_id`` is a page, 409 when a block holds the reserved id."""
    write_lock(conn)
    try:
        row = conn.execute(
            "SELECT parent_id, content, properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if not row or row[0] != "root":
            raise OpError(404, "page not found")
        if not ensure_reserved(conn, TRASH):
            raise OpError(409, f"a block holds the reserved id {TRASH!r}")
        now = page_now()
        props = json.dumps({**json.loads(row[2] or "{}"), "deleted_at": now, "deleted_by": actor})
        conn.execute("UPDATE unified_blocks SET parent_id = ?, position = ?, properties = ? WHERE id = ?",
                     (TRASH, generate_key_between(last_child_position(conn, TRASH), None), props, page_id))
        touch_page(conn, page_id, actor, "deleted", now=now)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    collab.publish(ws, page_id, {"t": "trashed"})
    notify_commit(ws, client, page_id)
    return trashed_page(page_id, row[1], props)


def restore_page(ws: str, conn, page_id: str, *, actor: str, client: str = "") -> dict:
    """Bring a page back from Recently deleted: under ``root`` again, last
    in the library, filed where it was, the deletion stamps
    gone. The page is touched live by ``actor`` (``touch_page``), so the
    change feed shows a page (re)created and a copy of the workspace takes
    it back whole; its room hears ``reload`` (a tab still open on it
    refetches and sends what it kept). Commits; returns the page's block
    dict. OpError(404) unless the page is in the trash."""
    write_lock(conn)
    try:
        row = conn.execute("SELECT parent_id, properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if not row or row[0] != TRASH:
            raise OpError(404, "not in Recently deleted")
        props = json.loads(row[1] or "{}")
        props.pop("deleted_at", None)
        props.pop("deleted_by", None)
        conn.execute(
            "UPDATE unified_blocks SET parent_id = 'root', position = ?, properties = ? WHERE id = ?",
            (generate_key_between(last_child_position(conn, "root"), None), json.dumps(props), page_id))
        touch_page(conn, page_id, actor)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    collab.publish_reload(ws, page_id)
    notify_commit(ws, client, page_id)
    return block_to_dict(conn.execute(
        f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?", (page_id,)).fetchone())


def record_ops(ws: str, conn, page_id: str, ops: list[dict], *, actor: str) -> int:
    """Log + publish ops a writer performed with its own SQL (a cross-page
    move, whose two halves are a delete on one page and an arrival on the
    other), touching the page like a batch. Commits."""
    now = page_now()
    touch_page(conn, page_id, actor, now=now)
    seq = _log(conn, page_id, actor, "", now, ops)
    conn.commit()
    collab.publish(ws, page_id, {"t": "ops", "seq": seq, "at": now, "actor": actor,
                                   "client": "", "ops": ops})
    notify_commit(ws, "", page_id)
    return seq


def move_across_pages(ws: str, conn, block_id: str, parent_id: str, position: str,
                      src_page_id: str, page_id: str, *, actor: str) -> None:
    """Move a block with its subtree under ``parent_id`` on another page.
    The op vocabulary is per page, so this is its own SQL: the subtree's
    rows take the page's id, a delete is logged on the page it leaves and a
    reload on the page it joins (each touching its page). Commits."""
    conn.execute("UPDATE unified_blocks SET parent_id = ?, position = ?, updated_at = ? WHERE id = ?",
                 (parent_id, position, page_now(), block_id))
    move_subtree_to_page(conn, block_id, page_id)
    record_ops(ws, conn, src_page_id, [{"op": "delete", "id": block_id}], actor=actor)
    note_reload(ws, conn, page_id, actor)


def note_reload(ws: str, conn, page_id: str, actor: str) -> int:
    """``log_reload`` + commit + fan-out, for writers that rewrote a page's
    tree wholesale (the subtree replace, imports into an existing page)."""
    seq = log_reload(conn, page_id, actor)
    conn.commit()
    collab.publish_reload(ws, page_id, seq)
    notify_commit(ws, "", page_id)
    return seq


def log_reload(conn, page_id: str, actor: str, *, after: int = 0) -> int:
    """Log a change ops can't express (a subtree replace, an import into an
    existing page) so a catching-up client knows to refetch, and touch the
    page like any batch (``touch_page``: the home feed and the change feed).
    ``after``: a seq the entry must land above even when this log is behind
    it (a restored backup's log, which a client may have seen past —
    gamma/ws_backup.py). Caller commits and publishes
    (``collab.publish_reload``)."""
    now = page_now()
    touch_page(conn, page_id, actor, now=now)
    return _log(conn, page_id, actor, "", now, [{"op": "reload"}], after)


def latest_seq(conn, page_id: str) -> int:
    return conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM page_ops WHERE page_id = ?", (page_id,)).fetchone()[0]


def ops_since(conn, page_id: str, since: int) -> tuple[list[dict], bool]:
    """``(batches after seq, pruned)`` — pruned means the log no longer
    reaches back to ``since``, or what it holds after it is more than a
    catch-up carries (CATCHUP_MAX_BATCHES / CATCHUP_MAX_BYTES), or it cannot
    continue from ``since`` at all: it ends before it (the page's log was
    replaced — a backup restore — or restarted) or skips the batch right
    after it (a restore moved the page's seq forward). Either way the client
    must reload the tree."""
    low = conn.execute(
        "SELECT MIN(seq) FROM page_ops WHERE page_id = ?", (page_id,)).fetchone()[0]
    if low is not None and since < low - 1:
        return [], True
    if since > latest_seq(conn, page_id):
        return [], True
    batches, size = [], 0
    for seq, actor, client, at, ops in conn.execute(
            "SELECT seq, actor, client, at, ops FROM page_ops WHERE page_id = ? AND seq > ? ORDER BY seq",
            (page_id, since)):
        if not batches and seq != since + 1:
            return [], True
        size += len(ops)
        if len(batches) >= CATCHUP_MAX_BATCHES or size > CATCHUP_MAX_BYTES:
            return [], True
        batches.append({"seq": seq, "actor": actor, "client": client, "at": at, "ops": json.loads(ops)})
    return batches, False
