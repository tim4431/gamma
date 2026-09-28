"""Block operations — THE write path for a page's blocks.

A page's notes change through small, typed operations rather than a whole-
tree replace, so several clients (two browsers of one account, share editors)
can edit one page at once and only the touched rows move:

- ``set {id, content?, base?, props?}`` — ``content`` replaces the text;
  with ``base`` (the text the client's change was computed from) it is
  applied as a patch when the block changed meanwhile, so two people
  editing different spans of one block both keep their edit
  (gamma/textmerge.py; the applied op carries the merged text). ``props``
  is a PATCH (``{key: value | null}``, null deletes), so unrelated
  properties never conflict (Figma's property-level rule).
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
its properties), never moved or deleted. Only touched rows get
``updated_at``; the page root is stamped once per batch (home-feed order,
the notes index fingerprint). One batch is one transaction and one row of
the per-page op log (``page_ops``, ``seq`` counting up per page) — live
clients follow the log over the page's websocket (gamma/collab.py), a
reconnecting one reads it back with ``ops_since``. A batch refused because
someone else changed the page meanwhile (the block is gone, lives in another
page now, or the move would make a cycle) says so in ``OpError.conflict``,
with the op's index, so a client can drop that op and send the rest.

A client names each batch (``batch``, the same id on every retry of it): the
answer to a batch this process already applied is replayed instead of
applying it twice (``_replays``, in memory — a restart forgets them, and the
clients catch up from the log anyway).

``ws`` everywhere below is the workspace id (docs/dev/workspaces.md), ``actor``
the account making the change. Server-side writers (the block endpoints, AI
tools, page rename/attach, metadata) go through ``apply_ops`` too, so everything a page's viewers see
comes from one code path and one log.
"""

import json
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Annotated, Literal, Union

from fractional_indexing import FIError, generate_key_between, validate_order_key
from pydantic import BaseModel, Field

from . import block_index, collab, textmerge, upload_gc
from .blocks_store import (
    BLOCK_COLUMNS, TRASH, block_to_dict, delete_subtree, ensure_trash, fetch_subtree, last_child_position,
    subtree_refs, trashed_page, valid_block_id, write_lock)
from .db import connect_pages_db, page_now
from .logbuf import log
from .storage import upload_refs

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
REPLAY_KEEP = 4096   # batch answers remembered, all pages together ...
REPLAY_TTL = 600     # ... for at most this many seconds
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


def storable(value):
    """``value`` with every lone UTF-16 surrogate in its strings (dict keys
    and nested values too) replaced by U+FFFD. A browser can send half an
    emoji ("\\ud83d" in JSON); SQLite cannot encode it, so one such string
    would fail its whole batch on every retry."""
    if isinstance(value, str):
        if not _SURROGATE.search(value):
            return value
        # a high + low half that arrived as two escapes become the one character
        return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if isinstance(value, dict):
        return {storable(k): storable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [storable(v) for v in value]
    return value


class SetOp(BaseModel):
    op: Literal["set"]
    id: str
    content: str | None = None
    base: str | None = None  # the text `content` was edited from (three-way merge)
    props: dict | None = None


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

    def __init__(self, conn, page_id: str, now: str, share_scoped: bool, cursor: dict | None = None):
        self.conn = conn
        self.page_id = page_id
        self.now = now
        self.share_scoped = share_scoped
        self.cursor = cursor  # the writer's caret, remapped when its block's text is merged
        self._page_of: dict[str, str | None] = {page_id: page_id}
        self.applied: list[dict] = []
        self.deleted: list[str] = []
        # upload names the ops stopped / started referencing (gamma/upload_gc.py)
        self.dropped: set[str] = set()
        self.added: set[str] = set()
        self.doc_deleted = False  # a deleted block carried a PDF (doc_id)

    def refs_changed(self, old: set, new: set) -> None:
        self.dropped |= old - new
        self.added |= new - old

    def page_of(self, block_id: str) -> str | None:
        """The page a block lives in (None: unknown block). Memoized per
        batch — walks parent links, one query per unseen level."""
        chain = []
        cur = block_id
        while cur not in self._page_of:
            row = self.conn.execute(
                "SELECT parent_id FROM unified_blocks WHERE id = ?", (cur,)).fetchone()
            if not row:
                self._page_of[cur] = None
                break
            chain.append(cur)
            if row[0] in (None, "root"):
                self._page_of[cur] = cur
                break
            if row[0] == TRASH:
                self._page_of[cur] = TRASH  # a page in Recently deleted: no batch reaches it
                break
            cur = row[0]
        page = self._page_of[cur]
        for b in chain:
            self._page_of[b] = page
        return page

    def require_in_page(self, block_id: str, what: str = "block") -> None:
        page = self.page_of(block_id)
        if page is None:
            raise OpError(404, f"no such {what}: {block_id}", missing=block_id, conflict=MISSING)
        if page != self.page_id:
            raise OpError(403, f"{what} {block_id} is outside this page", conflict=MOVED)

    def free_position(self, parent: str, position: str | None, block_id: str) -> str:
        if position is None:
            return generate_key_between(last_child_position(self.conn, parent), None)
        try:
            validate_order_key(position)
        except FIError as e:
            raise OpError(400, f"invalid position: {e}")
        clash = self.conn.execute(
            "SELECT 1 FROM unified_blocks WHERE parent_id = ? AND position = ? AND id != ?",
            (parent, position, block_id)).fetchone()
        if not clash:
            return position
        nxt = self.conn.execute(
            "SELECT MIN(position) FROM unified_blocks WHERE parent_id = ? AND position > ? AND id != ?",
            (parent, position, block_id)).fetchone()[0]
        return generate_key_between(position, nxt)

    def check_parent(self, parent: str, block_id: str | None) -> None:
        if parent == "root":
            raise OpError(403, "ops never create or move pages")
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
        if patch and block_id == self.page_id and self.share_scoped:
            raise OpError(403, "share editors cannot change page settings")
        if content is not None and len(content) > MAX_CONTENT:
            raise OpError(413, "content too long")
        base = op.get("base")
        if (content is not None and base is not None and len(base) <= MAX_CONTENT
                and base != (row[0] or "") and base != content and content != (row[0] or "")):
            # (a client whose edit is already the text — the same change made
            # twice, a retried batch — has nothing to merge: patching it in
            # again would double it)
            # Someone else changed the block since this client read it:
            # apply the client's edit as a patch on the current text.
            merged, _clean = textmerge.merge(base, content, row[0] or "")
            cur = self.cursor
            if cur and cur.get("block") == block_id and merged != content:
                cur["anchor"] = textmerge.map_offset(content, merged, cur.get("anchor", -1)) if cur.get("anchor", -1) >= 0 else -1
                cur["head"] = textmerge.map_offset(content, merged, cur.get("head", -1)) if cur.get("head", -1) >= 0 else -1
            content = merged
        props = json.loads(row[1] or "{}")
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
            "INSERT INTO unified_blocks (id, parent_id, position, content, properties, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (block_id, parent, position, content, json.dumps(props), self.now, self.now))
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
        self.applied.append({"op": "delete", "id": block_id})


def _log(conn, page_id: str, actor: str, client: str, now: str, applied: list, after: int = 0) -> int:
    seq = max(conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM page_ops WHERE page_id = ?", (page_id,)).fetchone()[0], after) + 1
    payload = json.dumps(applied)
    conn.execute(
        "INSERT INTO page_ops (page_id, seq, actor, client, at, ops) VALUES (?, ?, ?, ?, ?, ?)",
        (page_id, seq, actor, client, now, payload))
    if seq % PRUNE_EVERY == 0 or len(payload) > KEEP_OPS_BYTES // PRUNE_EVERY:
        _prune(conn, page_id, now)
    return seq


def _prune(conn, page_id: str, now: str) -> None:
    """Drop the page's oldest log rows past KEEP_OPS rows, KEEP_OPS_HOURS of
    age or KEEP_OPS_BYTES of payload, keeping the newest row whatever its
    size. Only a prefix of the log goes, so ``ops_since`` still tells a gap
    by the lowest seq left."""
    cutoff = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%S.%fZ")
              - timedelta(hours=KEEP_OPS_HOURS)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
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


# Answers to client-named batches by (page, client, batch id): a retry of a
# batch whose first attempt did land (the answer was lost on the way back)
# gets the same answer instead of applying twice. In memory and bounded:
# the oldest go past REPLAY_KEEP entries or REPLAY_TTL seconds.
_replays: OrderedDict = OrderedDict()
_replays_lock = threading.Lock()


def _replay_get(key: tuple) -> dict | None:
    with _replays_lock:
        hit = _replays.get(key)
        return hit[1] if hit and time.monotonic() - hit[0] <= REPLAY_TTL else None


def _replay_put(key: tuple, answer: dict) -> None:
    now = time.monotonic()
    with _replays_lock:
        _replays[key] = (now, answer)
        _replays.move_to_end(key)
        while _replays and (len(_replays) > REPLAY_KEEP or now - next(iter(_replays.values()))[0] > REPLAY_TTL):
            _replays.popitem(last=False)


def _replay_drop(key: tuple) -> None:
    with _replays_lock:
        _replays.pop(key, None)


def _replayed(conn, page_id: str, answer: dict) -> dict:
    """``apply_ops``'s result for a batch applied earlier, its ops read back
    from the log (none once the row is pruned); ``replayed`` marks it, so
    nothing is fanned out or derived again."""
    row = conn.execute("SELECT actor, client, ops FROM page_ops WHERE page_id = ? AND seq = ?",
                       (page_id, answer["seq"])).fetchone()
    result = {"page_id": page_id, "seq": answer["seq"], "at": answer["at"],
              "actor": row[0] if row else "", "client": row[1] if row else "",
              "ops": json.loads(row[2]) if row else [], "deleted_ids": [], "dropped_uploads": [],
              "doc_deleted": False, "replayed": True}
    if answer.get("cursor") is not None:
        result["cursor"] = answer["cursor"]
    return result


def apply_ops(conn, page_id: str, ops: list[dict], *, actor: str, client: str = "",
              share_scoped: bool = False, cursor: dict | None = None, batch_id: str = "") -> dict:
    """Apply one batch inside one transaction (committed here) and log it.
    Returns ``{page_id, seq, at, actor, client, ops (as applied), deleted_ids,
    dropped_uploads, doc_deleted, cursor?}`` — hand it to ``after_commit``
    for the derived-data work and the room fan-out. ``dropped_uploads``: the
    upload names the batch stopped referencing (the names it started
    referencing have their orphan rows cleared in the same transaction —
    gamma/upload_gc.py); ``doc_deleted``: a deleted block carried a PDF.
    ``cursor`` (``{block, anchor, head}``, the writer's
    caret in the text it sent) comes back remapped into the text actually
    stored when a merge changed it. Raises ``OpError`` (nothing written) on a
    bad op. ``batch_id``: the client's name for the batch — one this process
    already applied for that client is answered again (``replayed``), not
    re-applied. Lone surrogates in the ops' strings are stored as U+FFFD."""
    if not ops:
        raise OpError(400, "no ops")
    if len(ops) > MAX_OPS:
        raise OpError(413, f"too many ops in one batch (>{MAX_OPS})")
    ops = storable(ops)
    key = (page_id, client, batch_id) if batch_id else None
    write_lock(conn)  # up front: seq is per page
    try:
        root = conn.execute(
            "SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if not root or root[0] != "root":
            raise OpError(404, "page not found")
        answer = _replay_get(key) if key else None
        if answer:
            conn.rollback()
            return _replayed(conn, page_id, answer)
        now = page_now()
        cursor = dict(cursor) if cursor else None
        batch = _Batch(conn, page_id, now, share_scoped, cursor)
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
        conn.execute("UPDATE unified_blocks SET updated_at = ? WHERE id = ?", (now, page_id))
        seq = _log(conn, page_id, actor, client, now, batch.applied)
        if key:  # remembered before the lock goes, so a retry waiting on it sees the answer
            _replay_put(key, {"seq": seq, "at": now, "cursor": cursor})
        conn.commit()
    except BaseException:
        conn.rollback()
        if key:
            _replay_drop(key)
        raise
    result = {"page_id": page_id, "seq": seq, "at": now, "actor": actor, "client": client,
              "ops": batch.applied, "deleted_ids": batch.deleted, "dropped_uploads": sorted(dropped),
              "doc_deleted": batch.doc_deleted}
    if cursor is not None:
        result["cursor"] = cursor
    return result


# Called after every committed write with ``(ws, client, page_id)``: an
# offline copy's sync-on-change (sync_engine._on_commit) and the notes
# index's background re-index (block_index.page_changed, registered by
# routers/search.py). Each listener registers itself at import.
commit_listeners: list = []


def _notify(ws: str, client: str = "", page_id: str = "") -> None:
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
    thread, never on the request), and the deleted blocks' data.db rows are
    purged (the library-wide part only when a PDF-carrying block went; it
    may wait on data.db's lock)."""
    collab.publish_ops(ws, result)
    upload_gc.schedule(ws, result["dropped_uploads"])
    if result["deleted_ids"]:
        block_index.purge_page_data(ws, conn, result["deleted_ids"], library=result["doc_deleted"])
    _notify(ws, result.get("client") or "", result.get("page_id") or "")
    return result


def commit_ops(ws: str, page_id: str, ops: list[dict], *, actor: str, client: str = "",
               share_scoped: bool = False, cursor: dict | None = None, batch_id: str = "") -> dict:
    """``apply_ops`` + ``after_commit`` on a fresh connection. ``cursor``
    (``{block, anchor, head}``): the writer's caret in the text the batch
    produces, fanned out with the batch so peers place it against the same
    text (a standalone presence message would reach them first, in
    offsets their copy doesn't have yet). A replayed batch (``batch_id``
    seen before) was fanned out the first time and is only answered."""
    with connect_pages_db(ws) as conn:
        result = apply_ops(conn, page_id, ops, actor=actor, client=client,
                           share_scoped=share_scoped, cursor=cursor, batch_id=batch_id)
        if result.get("replayed"):
            return result
        return after_commit(ws, conn, result)


def delete_page(ws: str, conn, page_id: str, *, actor: str, client: str = "") -> dict:
    """Delete a page for good, in one transaction committed here: the
    subtree, its op log and a ``deleted_pages`` tombstone, so a copy of the
    workspace can tell a deleted page from one it never had (a page purged
    from Recently deleted keeps the tombstone its trashing wrote). Then the
    upload names the page held go to the orphan check, the page's data.db
    rows (chats, indexes) are purged — the one path that drops them — and
    the page's room is told to reload (which surfaces the 404). Returns
    ``{deleted_ids, dropped_uploads}``.

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
        conn.execute(f"INSERT OR {'IGNORE' if trashed else 'REPLACE'} INTO deleted_pages "
                     "(page_id, deleted_at, actor) VALUES (?, ?, ?)", (page_id, page_now(), actor))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    deleted_ids = [r[0] for r in rows]
    dropped = sorted(subtree_refs(rows))
    upload_gc.schedule(ws, dropped)
    block_index.purge_page_data(ws, conn, deleted_ids)
    collab.publish_reload(ws, page_id)
    _notify(ws, client, page_id)
    return {"deleted_ids": deleted_ids, "dropped_uploads": dropped}


def trash_page(ws: str, conn, page_id: str, *, actor: str, client: str = "") -> dict:
    """Move a page to Recently deleted (gamma/trash.py): its root goes under
    the reserved ``trash`` block, stamped ``deleted_at`` / ``deleted_by``;
    its blocks, files, chats and op log stay as they are. To everything
    else it is deleted: listings and searches pass it by, ops to it are
    refused, its room is told to reload (the 404), and the ``deleted_pages``
    tombstone a hard delete leaves is written, so a copy of the workspace
    removes its copy. ``restore_page`` brings it back, ``delete_page``
    removes it for good. Commits; returns its trash entry. OpError(404)
    unless ``page_id`` is a page, 409 when a block holds the reserved id."""
    write_lock(conn)
    try:
        row = conn.execute(
            "SELECT parent_id, content, properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if not row or row[0] != "root":
            raise OpError(404, "page not found")
        if not ensure_trash(conn):
            raise OpError(409, f"a block holds the reserved id {TRASH!r}")
        now = page_now()
        props = json.dumps({**json.loads(row[2] or "{}"), "deleted_at": now, "deleted_by": actor})
        conn.execute("UPDATE unified_blocks SET parent_id = ?, position = ?, properties = ? WHERE id = ?",
                     (TRASH, generate_key_between(last_child_position(conn, TRASH), None), props, page_id))
        conn.execute("INSERT OR REPLACE INTO deleted_pages (page_id, deleted_at, actor) VALUES (?, ?, ?)",
                     (page_id, now, actor))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    collab.publish_reload(ws, page_id)
    _notify(ws, client, page_id)
    return trashed_page(page_id, row[1], props)


def restore_page(ws: str, conn, page_id: str, *, client: str = "") -> dict:
    """Bring a page back from Recently deleted: under ``root`` again, last
    in the library, its folder labels as they were, the deletion stamps
    gone. The root is stamped and the tombstone cleared, so the change feed
    shows a page (re)created and a copy of the workspace takes it back
    whole. Commits; returns the page's block dict. OpError(404) unless the
    page is in the trash."""
    write_lock(conn)
    try:
        row = conn.execute("SELECT parent_id, properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if not row or row[0] != TRASH:
            raise OpError(404, "not in Recently deleted")
        props = json.loads(row[1] or "{}")
        props.pop("deleted_at", None)
        props.pop("deleted_by", None)
        conn.execute(
            "UPDATE unified_blocks SET parent_id = 'root', position = ?, properties = ?, updated_at = ? WHERE id = ?",
            (generate_key_between(last_child_position(conn, "root"), None), json.dumps(props), page_now(), page_id))
        conn.execute("DELETE FROM deleted_pages WHERE page_id = ?", (page_id,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    _notify(ws, client, page_id)
    return block_to_dict(conn.execute(
        f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?", (page_id,)).fetchone())


def record_ops(ws: str, conn, page_id: str, ops: list[dict], *, actor: str) -> int:
    """Log + publish ops a writer performed with its own SQL (a cross-page
    move, whose two halves are a delete on one page and an arrival on the
    other). Commits."""
    now = page_now()
    seq = _log(conn, page_id, actor, "", now, ops)
    conn.commit()
    collab.publish(ws, page_id, {"t": "ops", "seq": seq, "at": now, "actor": actor,
                                   "client": "", "ops": ops})
    _notify(ws, "", page_id)
    return seq


def note_reload(ws: str, conn, page_id: str, actor: str) -> int:
    """``log_reload`` + commit + fan-out, for writers that rewrote a page's
    tree wholesale (the subtree replace, imports into an existing page)."""
    seq = log_reload(conn, page_id, actor)
    conn.commit()
    collab.publish_reload(ws, page_id, seq)
    _notify(ws, "", page_id)
    return seq


def log_reload(conn, page_id: str, actor: str, *, after: int = 0) -> int:
    """Log a change ops can't express (a subtree replace, an import into an
    existing page) so a catching-up client knows to refetch, and stamp the
    page root like any batch (the home feed and the change feed read it).
    ``after``: a seq the entry must land above even when this log is behind
    it (a restored backup's log, which a client may have seen past —
    gamma/ws_backup.py). Caller commits and publishes
    (``collab.publish_reload``)."""
    now = page_now()
    conn.execute("UPDATE unified_blocks SET updated_at = ? WHERE id = ?", (now, page_id))
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
