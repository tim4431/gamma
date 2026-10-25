"""Taking back one change the AI chat's agent made to the notes, or a
folder or label it deleted, and putting it back again.

Each note tool's action records what its change needs to be undone and
redone (``revert`` on the action, gamma/ai_tools.py): an edit the block's
text just before and just after the write, a new block the text it was
made with and where it went, a move the parent and key it left and the
ones it went to, a deletion every block of the subtree it took. A folder's
or label's deletion (``tree`` on its revert) records the blocks it took and
each library page's ids of them. Its revert puts the blocks back under the
same ids and refiles those pages, and its redo deletes it again
(``ops.delete_tree``). The folders' chats stay in the library chat's
history. The chat keeps the action with the reply, and its row under the
reply sends it back here (``POST /api/ai/revert``) when the user reverts
the change, or redoes one they reverted.

A rename_page, move_page or label_page call records each page's title,
folders or labels before and after it (``pages``). Its revert puts back
what the call changed, every page in one step: a title only while it
reads as the call left it, folders and labels by what the call put on
and took off, so a filing made since stays. Its redo makes the call's
change again the same way. A page changed since, deleted or in Recently
deleted — or one it would leave in no folder, the folders to go back to
deleted since — is left as it is and counted (``kept``); nothing there
is forced. When every page is such a page, nothing is written and the
revert stops (409 ``kept``), so the change is not marked reverted.

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

import json

from . import textmerge
from .ai_tools import BATCH_MAX, cross_page_refusal, text_diff
from .blocks_store import (FOLDERS, LABELS, TREES, existing_in, fetch_subtree, filing, free_position, load_props,
                           named, page_root_id, tree_children, write_lock)
from .db import connect_pages_db
from .ops import OpError, after_commit, after_commits, apply_batches, apply_ops, delete_tree, move_across_pages

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


def _plan_delete(conn, block_id: str, revert: dict, force: bool, redo: bool):
    """A deleted subtree put back as it was (``revert["blocks"]``: every
    block, parent first, with its parent, key, text and properties); a redo
    deletes it again. A folder's or label's deletion is
    ``_plan_tree_delete``'s."""
    if revert.get("tree") is not None:
        return _plan_tree_delete(conn, block_id, revert, force, redo)
    rows = revert.get("blocks")
    if not isinstance(rows, list) or not rows or not all(isinstance(r, list) and len(r) == 5 for r in rows) \
            or rows[0][0] != block_id:
        raise RevertError(400, MALFORMED)
    row = _row(conn, block_id)
    if redo:
        if not row:
            return "", None  # deleted already
        return page_root_id(conn, block_id), [{"op": "delete", "id": block_id}]
    if row:
        return page_root_id(conn, block_id), None  # back already (another tab, or by hand)
    parent = rows[0][1]
    page_id = page_root_id(conn, parent)
    if not page_id:
        raise RevertError(409, "The note it was under was deleted.", "gone")
    ops = []
    for bid, par, position, content, props in rows:
        try:
            props = json.loads(props or "{}")
        except ValueError:
            props = {}
        ops.append({"op": "insert", "id": bid, "parent": par, "content": content or "",
                    "position": position if isinstance(position, str) else None, "props": props})
    return page_id, ops


def _plan_tree_delete(conn, block_id: str, revert: dict, force: bool, redo: bool):
    """A deleted folder (with its subfolders) or label put back as it was —
    the same ids, parents, keys (re-keyed if taken), names and properties —
    and the library pages that carried it filed in it again
    (``("batches", …)`` for ``apply_batches``); a redo deletes it again as it
    is then (``("tree", tree)``, ``ops.delete_tree``). Nothing here can be
    forced."""
    tree, rows, pages = revert.get("tree"), revert.get("blocks"), revert.get("pages")
    if tree not in TREES or not isinstance(rows, list) or not rows or not isinstance(pages, dict) \
            or not all(isinstance(r, list) and len(r) == 5 and isinstance(r[0], str) for r in rows) \
            or rows[0][0] != block_id:
        raise RevertError(400, MALFORMED)
    there = conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND page_id = ?", (block_id, tree)).fetchone()
    if redo:
        if not there:
            return "", None  # deleted already
        if tree == FOLDERS:
            from .routers.shares import shared_folders  # local: keep gamma.* free of the routers package
            if shared_folders(conn.ws, {r[0] for r in fetch_subtree(conn, block_id)}):
                raise RevertError(409, "The folder is shared now. Stop sharing it before deleting it again.", "gone")
        return "", ("tree", tree)
    if there:
        return "", None  # back already (another tab, or by hand)
    parent, name = rows[0][1], rows[0][3] or ""
    if parent != tree and not conn.execute(
            "SELECT 1 FROM unified_blocks WHERE id = ? AND page_id = ?", (parent, tree)).fetchone():
        raise RevertError(409, "The folder it was in was deleted.", "gone")
    if name and named(tree_children(conn, tree).get(parent, []), name):
        raise RevertError(409, f"There is a {'folder' if tree == FOLDERS else 'label'} “{name}” there again.", "gone")
    inserts = [{"op": "insert", "id": bid, "parent": par, "content": content or "",
                "position": position if isinstance(position, str) else None, "props": load_props(props)}
               for bid, par, position, content, props in rows]
    ids = {r[0] for r in rows}
    batches = [(tree, inserts)]
    live = dict(conn.execute(
        "SELECT id, properties FROM unified_blocks WHERE parent_id = 'root' AND id IN (SELECT value FROM json_each(?))",
        (json.dumps([p for p in pages if isinstance(p, str)]),)).fetchall())
    for page_id, carried in pages.items():
        if page_id not in live or not isinstance(carried, list):
            continue  # deleted or in Recently deleted since
        have = filing(load_props(live[page_id]), tree)
        back = [i for i in carried if i in ids and i not in have]
        if back:
            batches.append((page_id, [{"op": "set", "id": page_id, "props": {tree: have + back}}]))
    return "", ("batches", batches)


# What a page tool's revert record names (``revert["pages"]``): the field
# each change wrote, and the tree a folders or labels change files by.
_PAGE_FIELDS = {"title": "", "folders": FOLDERS, "labels": LABELS}
KEPT = "The pages changed since, or were deleted, and were left as they are."


def _page_item(item) -> bool:
    """Whether ``item`` is ``[page id, field, before, after]`` as the page
    tools record it: titles as text, folders and labels as id lists."""
    if not (isinstance(item, list) and len(item) == 4 and isinstance(item[0], str)
            and isinstance(item[1], str) and item[1] in _PAGE_FIELDS):
        return False
    if item[1] == "title":
        return isinstance(item[2], str) and isinstance(item[3], str)
    return all(isinstance(v, list) and all(isinstance(i, str) for i in v) for v in item[2:])


def _plan_pages(conn, block_id: str, revert: dict, force: bool, redo: bool):
    """The pages a rename_page, move_page or label_page call changed, put
    back as they were — with ``redo`` as the call left them — in one step
    (``("batches", [(page id, ops), …], kept)``). A title goes back only
    while the page reads as the call (or its revert) left it. Folders and
    labels go by set delta: the ids the change put on come off, the ones it
    took off go back where they still exist, and whatever was filed since
    stays. A page renamed since, deleted or in Recently deleted — or one
    whose folders to go back to were all deleted since, which would leave
    it in no folder — is left as it is and counted (``kept``); when every
    page is, there is nothing to put back (409 ``kept``). Nothing here is
    forced."""
    items = revert.get("pages")
    if not isinstance(items, list) or not items or len(items) > BATCH_MAX or not all(map(_page_item, items)):
        raise RevertError(400, MALFORMED)
    live = {r[0]: (r[1] or "", load_props(r[2])) for r in conn.execute(
        "SELECT id, content, properties FROM unified_blocks WHERE parent_id = 'root' "
        "AND id IN (SELECT value FROM json_each(?))", (json.dumps([i[0] for i in items]),))}
    batches, kept = [], 0
    for page_id, field, before, after in items:
        src, dst = (before, after) if redo else (after, before)
        if page_id not in live:
            kept += 1  # deleted, or in Recently deleted, since
            continue
        title, props = live[page_id]
        if field == "title":
            if title == dst:
                continue  # back already
            if title != src:
                kept += 1  # renamed since: the user's title stays
                continue
            batches.append((page_id, [{"op": "set", "id": page_id, "content": dst}]))
            continue
        tree = _PAGE_FIELDS[field]
        now = filing(props, tree)
        take = [i for i in src if i not in dst]
        give = existing_in(conn, tree, [i for i in dst if i not in src and i not in now])
        want = [i for i in now if i not in take] + give
        if want == now:
            continue
        if tree == FOLDERS and dst and not want:
            kept += 1  # the folders it goes back to were deleted since: it would be filed in none
            continue
        batches.append((page_id, [{"op": "set", "id": page_id, "props": {tree: want or None}}]))
    if kept == len(items):
        raise RevertError(409, KEPT, "kept", {"kept": kept})
    return "", ("batches", batches, kept)


_PLANS = {"edit": _plan_edit, "create": _plan_create, "move": _plan_move, "delete": _plan_delete,
          "pages": _plan_pages}


def revert_change(ws: str, kind: str, block_id: str, revert: dict, *, force: bool = False,
                  redo: bool = False, actor: str = "") -> dict:
    """Take back one recorded change, or with ``redo`` put a reverted one
    back: ``{page_id, noop, kept?}`` — ``noop`` when the note already reads
    that way (nothing written), ``kept``: the pages a page tool's revert
    left as they are (409 ``kept`` when that is all of them). Planned and
    written under one write lock, so nobody's edit lands in between.
    ``kind`` is one of ``_PLANS`` (the endpoint's model says so). Raises
    ``RevertError``."""
    if not block_id and kind != "pages":
        raise RevertError(400, MALFORMED)
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            page_id, plan = _PLANS[kind](conn, block_id, revert, force, redo)
            page_id = page_id or ""
            if not plan:
                conn.rollback()
                return {"page_id": page_id, "noop": True}
            if isinstance(plan, tuple) and plan[0] == "tree":
                delete_tree(ws, conn, plan[1], block_id, actor=actor, client=CLIENT)  # commits
                return {"page_id": page_id, "noop": False}
            if isinstance(plan, tuple) and plan[0] == "batches":
                kept = {"kept": plan[2]} if len(plan) > 2 and plan[2] else {}
                if not plan[1]:
                    conn.rollback()
                    return {"page_id": page_id, "noop": True, **kept}
                results = apply_batches(conn, plan[1], actor=actor, client=CLIENT)
                conn.commit()
                after_commits(ws, conn, results)
                return {"page_id": page_id, "noop": False, **kept}
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
