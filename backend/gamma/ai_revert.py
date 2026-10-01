"""Taking back one change the AI chat's agent made to the notes.

Each note tool's action records what its change needs to be undone
(``revert`` on the action, gamma/ai_tools.py): an edit the block's text
just before and just after the write, a new block the text it was made
with, a move the parent and key it left and the parent it went to. The
chat keeps the action with the reply, and its "Changed in your notes" row
sends it back here (``POST /api/ai/revert``) when the user reverts it.

A revert never assumes the note still says what the agent left. An edit is
taken back as a patch: the change ``after → before`` is merged into the
text stored now (gamma/textmerge.py, the merge the agent's own writes use),
so whatever anyone typed since stays. Only where someone changed the
agent's own text does the merge fall short; the revert then stops and
answers with the result of forcing it (``before`` with the later changes
that don't overlap), shown as a word diff, until the user forces it. A new
block that was typed in or given notes since, and a block moved on from
where the agent put it, stop the same way. Each revert is one write made
under the user's name (client ``"revert"``), fanned out like any other.
"""

from . import textmerge
from .ai_tools import cross_page_refusal, text_diff
from .blocks_store import fetch_subtree, free_position, page_root_id, write_lock
from .db import connect_pages_db
from .ops import OpError, after_commit, apply_ops, move_across_pages

CLIENT = "revert"  # the op log's writer for a revert: not the agent's "ai", not any tab
MALFORMED = "This change can't be reverted."
DELETED = "The note was deleted since."


class RevertError(Exception):
    """A revert that did not happen: ``status`` 404 (the note is gone), 409
    (``conflict``: it changed since — ``preview`` is what forcing would do —
    or it can't go back there at all), 400 for a malformed change."""

    def __init__(self, status: int, detail: str, conflict: str = "", preview: dict | None = None):
        super().__init__(detail)
        self.status, self.detail, self.conflict, self.preview = status, detail, conflict, preview


def _row(conn, block_id: str):
    return conn.execute(
        "SELECT parent_id, position, content FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()


def _plan_edit(conn, block_id: str, revert: dict, force: bool) -> list | None:
    row = _row(conn, block_id)
    if not row:
        raise RevertError(404, DELETED)
    before, after = revert.get("before"), revert.get("after")
    if not isinstance(before, str) or not isinstance(after, str):
        raise RevertError(400, MALFORMED)
    current = row[2] or ""
    # Taken back already (a second click, another tab, or by hand): merging
    # it again would undo it twice.
    if current == before or textmerge.contains(after, before, current):
        return None
    text, clean = textmerge.merge(after, before, current)
    if not clean:
        # Someone changed the agent's own text: forcing it keeps their
        # changes that don't overlap the agent's and loses the ones that do.
        forced, _ = textmerge.merge(after, current, before)
        if not force:
            raise RevertError(409, "The note was changed since.", "changed",
                              {"diff": text_diff(current, forced)})
        text = forced
    return None if text == current else [{"op": "set", "id": block_id, "content": text}]


def _plan_create(conn, block_id: str, revert: dict, force: bool) -> list | None:
    row = _row(conn, block_id)
    if not row:
        return None  # deleted already
    if row[0] == "root":
        raise RevertError(400, MALFORMED)
    under = len(fetch_subtree(conn, block_id)) - 1
    current = row[2] or ""
    if (current != revert.get("after") or under) and not force:
        raise RevertError(409, "The note was changed since.", "filled",
                          {"diff": text_diff(current, ""), "children": under})
    return [{"op": "delete", "id": block_id}]


def _plan_move(conn, block_id: str, revert: dict, force: bool):
    """The ops on the block's page, or ``("across", parent, position, src,
    dest)`` for a move back to another page; None when it is back already."""
    row = _row(conn, block_id)
    if not row:
        raise RevertError(404, DELETED)
    parent, position = revert.get("parent"), revert.get("position")
    if not isinstance(parent, str) or not isinstance(position, str) or row[0] == "root":
        raise RevertError(400, MALFORMED)
    if (row[0], row[1]) == (parent, position):
        return None
    dest = page_root_id(conn, parent)
    if not dest:
        raise RevertError(409, "The place it came from was deleted.", "gone")
    if parent in {r[0] for r in fetch_subtree(conn, block_id)}:
        raise RevertError(409, "The place it came from is now inside this note.", "gone")
    if row[0] != revert.get("to_parent") and not force:
        raise RevertError(409, "The note was moved since.", "moved")
    src = page_root_id(conn, block_id)
    if src == dest:
        return [{"op": "move", "id": block_id, "parent": parent, "position": position}]
    refusal = cross_page_refusal(conn, block_id)
    if refusal:
        raise RevertError(409, refusal[0].upper() + refusal[1:] + ".", "gone")
    return ("across", parent, free_position(conn, parent, position, block_id), src, dest)


_PLANS = {"edit": _plan_edit, "create": _plan_create, "move": _plan_move}


def revert_change(ws: str, kind: str, block_id: str, revert: dict, *, force: bool = False,
                  actor: str = "") -> dict:
    """Take back one recorded change: ``{page_id, noop}`` — ``noop`` when
    the note already reads as before (nothing written). Planned and written
    under one write lock, so nobody's edit lands in between. ``kind`` is
    one of ``_PLANS`` (the endpoint's model says so). Raises
    ``RevertError``."""
    if not block_id:
        raise RevertError(400, MALFORMED)
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            plan = _PLANS[kind](conn, block_id, revert, force)
            page_id = page_root_id(conn, block_id) or ""
            if plan is None:
                conn.rollback()
                return {"page_id": page_id, "noop": True}
            if isinstance(plan, tuple):
                _, parent, position, src, dest = plan
                move_across_pages(ws, conn, block_id, parent, position, src, dest, actor=actor)
                return {"page_id": dest, "noop": False}
            after_commit(ws, conn, apply_ops(conn, page_id, plan, actor=actor, client=CLIENT))
        except OpError as e:
            conn.rollback()
            raise RevertError(e.status if e.status in (400, 404) else 409, e.detail, "gone") from e
        except BaseException:
            conn.rollback()
            raise
    return {"page_id": page_id, "noop": False}
