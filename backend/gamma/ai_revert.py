"""Taking back one change the AI chat's agent made to the notes, and
putting it back again.

Each note tool's action records what its change needs to be undone and
redone (``revert`` on the action, gamma/ai_tools.py): an edit the block's
text just before and just after the write, a new block the text it was
made with and where it went, a move the parent and key it left and the
ones it went to. The chat keeps the action with the reply, and its
"Changed in your notes" row sends it back here (``POST /api/ai/revert``)
when the user reverts the change, or redoes one they reverted.

Neither direction assumes the note still says what it last said. An edit
goes as a patch: the change ``after → before`` (redo: ``before → after``)
is merged into the text stored now (gamma/textmerge.py, the merge the
agent's own writes use), so whatever anyone typed since stays. Only where
someone changed the text that patch rewrites does the merge fall short.
The write then stops and answers with the result of forcing it (the
target text with the later changes that don't overlap), shown as a word
diff, until the user forces it. A new block that was typed in or given
notes since, and a block moved on from where the last write put it, stop
the same way. Each write is the user's (client ``"revert"``), fanned out
like any other.
"""

from . import textmerge
from .ai_tools import cross_page_refusal, text_diff
from .blocks_store import fetch_subtree, free_position, page_root_id, write_lock
from .db import connect_pages_db
from .ops import OpError, after_commit, apply_ops, move_across_pages

CLIENT = "revert"  # the op log's writer for a revert or redo: not the agent's "ai", not any tab
MALFORMED = "This change can't be reverted."
DELETED = "The note was deleted since."


class RevertError(Exception):
    """A revert or redo that did not happen: ``status`` 404 (the note is
    gone), 409 (``conflict``: it changed since — ``preview`` is what forcing
    would do — or it can't go back there at all), 400 for a malformed
    change."""

    def __init__(self, status: int, detail: str, conflict: str = "", preview: dict | None = None):
        super().__init__(detail)
        self.status, self.detail, self.conflict, self.preview = status, detail, conflict, preview


def _row(conn, block_id: str):
    return conn.execute(
        "SELECT parent_id, position, content FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()


def _plan_edit(conn, block_id: str, revert: dict, force: bool, redo: bool):
    row = _row(conn, block_id)
    if not row:
        raise RevertError(404, DELETED)
    before, after = revert.get("before"), revert.get("after")
    if not isinstance(before, str) or not isinstance(after, str):
        raise RevertError(400, MALFORMED)
    src, dst = (before, after) if redo else (after, before)
    page_id, current = page_root_id(conn, block_id), row[2] or ""
    # Done already (a second click, another tab, or by hand): merging it
    # again would apply it twice.
    if current == dst or textmerge.contains(src, dst, current):
        return page_id, None
    # Word-level hunks: a later rewrite of the agent's sentence clashes with
    # it as a whole instead of interleaving characters with it.
    text, clean = textmerge.merge(src, dst, current, semantic=True)
    if not clean:
        # Someone changed the text this patch rewrites: forcing it keeps
        # their changes that don't overlap it and loses the ones that do.
        forced, _ = textmerge.merge(src, current, dst, semantic=True)
        if not force:
            raise RevertError(409, "The note was changed since.", "changed",
                              {"diff": text_diff(current, forced)})
        text = forced
    return page_id, None if text == current else [{"op": "set", "id": block_id, "content": text}]


def _plan_create(conn, block_id: str, revert: dict, force: bool, redo: bool):
    row = _row(conn, block_id)
    if redo:
        if row:
            return page_root_id(conn, block_id), None  # back already
        # The block the revert deleted, made again under the same id where
        # the agent put it (re-keyed if a sibling took the key since).
        parent, position, content = revert.get("parent"), revert.get("position"), revert.get("after")
        if not isinstance(parent, str) or not isinstance(content, str):
            raise RevertError(400, MALFORMED)
        page_id = page_root_id(conn, parent)
        if not page_id:
            raise RevertError(409, "The note it was added under was deleted.", "gone")
        return page_id, [{"op": "insert", "id": block_id, "parent": parent, "content": content,
                          "position": position if isinstance(position, str) else None}]
    if not row:
        return "", None  # deleted already
    if row[0] == "root":
        raise RevertError(400, MALFORMED)
    under = len(fetch_subtree(conn, block_id)) - 1
    current = row[2] or ""
    if (current != revert.get("after") or under) and not force:
        raise RevertError(409, "The note was changed since.", "filled",
                          {"diff": text_diff(current, ""), "children": under})
    return page_root_id(conn, block_id), [{"op": "delete", "id": block_id}]


def _plan_move(conn, block_id: str, revert: dict, force: bool, redo: bool):
    """The ops on the page the block goes to, or ``("across", parent,
    position, src)`` for a move from page ``src`` to another one."""
    row = _row(conn, block_id)
    if not row:
        raise RevertError(404, DELETED)
    # A revert moves it from where the agent put it back to where it was; a
    # redo the other way. An older action has no `to_position`: last there.
    if redo:
        (parent, position), expected = (revert.get("to_parent"), revert.get("to_position")), revert.get("parent")
        place = "it went to"
    else:
        (parent, position), expected = (revert.get("parent"), revert.get("position")), revert.get("to_parent")
        place = "it came from"
    if not isinstance(parent, str) or not isinstance(position, (str, type(None))) or row[0] == "root" \
            or (position is None and not redo):
        raise RevertError(400, MALFORMED)
    src = page_root_id(conn, block_id)
    if row[0] == parent and position in (None, row[1]):
        return src, None
    dest = page_root_id(conn, parent)
    if not dest:
        raise RevertError(409, f"The place {place} was deleted.", "gone")
    if parent in {r[0] for r in fetch_subtree(conn, block_id)}:
        raise RevertError(409, f"The place {place} is now inside this note.", "gone")
    if row[0] != expected and not force:
        raise RevertError(409, "The note was moved since.", "moved")
    if src == dest:
        return src, [{"op": "move", "id": block_id, "parent": parent, "position": position}]
    refusal = cross_page_refusal(conn, block_id)
    if refusal:
        raise RevertError(409, refusal[0].upper() + refusal[1:] + ".", "gone")
    return dest, ("across", parent, free_position(conn, parent, position, block_id), src)


_PLANS = {"edit": _plan_edit, "create": _plan_create, "move": _plan_move}


def revert_change(ws: str, kind: str, block_id: str, revert: dict, *, force: bool = False,
                  redo: bool = False, actor: str = "") -> dict:
    """Take back one recorded change, or with ``redo`` put a reverted one
    back: ``{page_id, noop}`` — ``noop`` when the note already reads that
    way (nothing written). Planned and written under one write lock, so
    nobody's edit lands in between. ``kind`` is one of ``_PLANS`` (the
    endpoint's model says so). Raises ``RevertError``."""
    if not block_id:
        raise RevertError(400, MALFORMED)
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            page_id, plan = _PLANS[kind](conn, block_id, revert, force, redo)
            page_id = page_id or ""
            if not plan:
                conn.rollback()
                return {"page_id": page_id, "noop": True}
            if isinstance(plan, tuple):
                _, parent, position, src = plan
                move_across_pages(ws, conn, block_id, parent, position, src, page_id, actor=actor)
                return {"page_id": page_id, "noop": False}
            after_commit(ws, conn, apply_ops(conn, page_id, plan, actor=actor, client=CLIENT))
        except OpError as e:
            conn.rollback()
            raise RevertError(e.status if e.status in (400, 404) else 409, e.detail, "gone") from e
        except BaseException:
            conn.rollback()
            raise
    return {"page_id": page_id, "noop": False}
