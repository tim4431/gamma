"""A page's tree as a flat snapshot, and the ops that turn one snapshot into
another — the pure half of the mirror (gamma/sync_engine.py applies them).

A snapshot is ``{block_id: {parent, position, content, props}}`` over the
page root and everything under it. ``diff(base, target, page_id)`` is the
Python twin of the frontend's ``diffTrees`` (src/shared/model/blockOps.js):
inserts for ids only ``target`` has, moves for a changed parent or key,
sets for changed content (carrying ``base``, the text it was edited from —
the server's three-way merge reads it) or a properties patch (carrying
``base_props`` with the ``ink_url`` a changed drawing was edited from, so
the server merges it by stroke, and the ``text_box`` a changed text box
was changed from, merged key by key), and deletes of the top-most removed
subtrees last. Inserts and moves come in tree
order, so a parent always exists before its children arrive.
``apply(snapshot, ops)`` is the other way round: what a server holds after
ops, keys taken as sent. ``moved(base, target)`` names the blocks a
person moved, telling them from blocks the server only re-keyed.
"""

import json

from . import storage
from .ops import props_patch


def snapshot_from_rows(rows) -> dict:
    """From ``blocks_store.fetch_subtree`` rows (``BLOCK_COLUMNS`` order)."""
    out = {}
    for r in rows:
        try:
            props = json.loads(r[4] or "{}")
        except (TypeError, ValueError):
            props = {}
        out[r[0]] = {"parent": r[1], "position": r[2], "content": r[3] or "", "props": props}
    return out


def snapshot_from_tree(node: dict) -> dict:
    """From the nested ``GET /blocks/{id}/subtree`` node."""
    out = {}
    pending = [node]
    while pending:
        n = pending.pop()
        out[n["id"]] = {"parent": n.get("parent_id"), "position": n.get("position") or "",
                        "content": n.get("content") or "", "props": dict(n.get("properties") or {})}
        pending.extend(n.get("children") or [])
    return out


def children_of(snapshot: dict) -> dict:
    """``{parent: [ids sorted by position]}``."""
    kids = {}
    for bid, b in snapshot.items():
        kids.setdefault(b["parent"], []).append(bid)
    for ids in kids.values():
        ids.sort(key=lambda i: (snapshot[i]["position"], i))
    return kids


def tree_order(snapshot: dict, page_id: str) -> list[str]:
    """Every id under the page, parents before children, siblings by key."""
    kids = children_of(snapshot)
    out, queue = [], list(kids.get(page_id, []))
    while queue:
        bid = queue.pop(0)
        out.append(bid)
        queue.extend(kids.get(bid, []))
    return out


def subtree_ids(snapshot: dict, block_id: str) -> set[str]:
    kids = children_of(snapshot)
    out, queue = set(), [block_id]
    while queue:
        bid = queue.pop()
        if bid in out:
            continue
        out.add(bid)
        queue.extend(kids.get(bid, []))
    return out


def ancestors(snapshot: dict, block_id: str) -> list[str]:
    """The chain of parents above a block, nearest first (the page root last)."""
    out, cur, seen = [], snapshot.get(block_id), set()
    while cur and cur["parent"] and cur["parent"] in snapshot and cur["parent"] not in seen:
        seen.add(cur["parent"])
        out.append(cur["parent"])
        cur = snapshot[cur["parent"]]
    return out


def _set_op(bid: str, b: dict | None, t: dict, with_base: bool) -> dict | None:
    op = {"op": "set", "id": bid}
    if b is None or t["content"] != b["content"]:
        op["content"] = t["content"]
        if with_base and b is not None:
            op["base"] = b["content"]
    patch = props_patch(b["props"] if b else {}, t["props"])
    if "content" in op and "auto_title" in t["props"] and "auto_title" not in patch:
        # a content write drops the automatic-title marker unless the patch
        # names it (ops.py) — keep the target's marker
        patch["auto_title"] = t["props"]["auto_title"]
    if patch:
        op["props"] = patch
        base_props = {}
        if with_base and b is not None and "ink_url" in patch:
            # the drawing this one was edited from: a server whose drawing
            # moved on meanwhile merges the two by stroke (ops.py merge_ink)
            base_props["ink_url"] = b["props"].get("ink_url") or ""
        if with_base and b is not None and "text_box" in patch and isinstance(b["props"].get("text_box"), dict):
            # the box this one was changed from: merged key by key into a
            # box someone else changed meanwhile (text_box.merge_text_box)
            base_props["text_box"] = b["props"]["text_box"]
        if base_props:
            op["base_props"] = base_props
    return op if len(op) > 2 else None


def diff(base: dict, target: dict, page_id: str, *, with_base: bool = True) -> list[dict]:
    """The ops turning ``base`` into ``target`` (see the module doc). The
    page root is only ever ``set``, and only when both snapshots have it."""
    ops = []
    b_root, t_root = base.get(page_id), target.get(page_id)
    if b_root is not None and t_root is not None:
        op = _set_op(page_id, b_root, t_root, with_base)
        if op:
            ops.append(op)
    for bid in tree_order(target, page_id):
        t, b = target[bid], base.get(bid)
        if b is None:
            ops.append({"op": "insert", "id": bid, "parent": t["parent"], "position": t["position"],
                        "content": t["content"], "props": dict(t["props"])})
            continue
        if t["parent"] != b["parent"] or t["position"] != b["position"]:
            ops.append({"op": "move", "id": bid, "parent": t["parent"], "position": t["position"]})
        op = _set_op(bid, b, t, with_base)
        if op:
            ops.append(op)
    gone = [bid for bid in base if bid not in target and bid != page_id]
    for bid in gone:
        parent = base[bid]["parent"]
        if parent in target or parent == page_id or parent not in base:
            ops.append({"op": "delete", "id": bid})
    return ops


def apply(snapshot: dict, ops: list[dict]) -> dict:
    """``snapshot`` after ``ops`` as a server applies them, keys taken as
    sent (a server re-keys a key that is taken; the caller reads its answer
    for those). An op whose block or parent is missing changes nothing,
    like a retried op there."""
    out = {k: {**v, "props": dict(v["props"])} for k, v in snapshot.items()}
    for op in ops:
        kind, bid = op["op"], op["id"]
        if kind == "insert":
            if bid not in out and op["parent"] in out:
                out[bid] = {"parent": op["parent"], "position": op.get("position") or "",
                            "content": op.get("content") or "", "props": dict(op.get("props") or {})}
        elif kind == "move":
            if bid in out and op["parent"] in out:
                out[bid] = {**out[bid], "parent": op["parent"], "position": op.get("position") or out[bid]["position"]}
        elif kind == "set":
            if bid not in out:
                continue
            b, patch = out[bid], op.get("props") or {}
            if op.get("content") is not None:
                b["content"] = op["content"]
                if "auto_title" not in patch:
                    b["props"].pop("auto_title", None)  # a title written drops the automatic marker (ops.py)
            for k, v in patch.items():
                if v is None:
                    b["props"].pop(k, None)
                else:
                    b["props"][k] = v
        elif kind == "delete" and bid in out:
            for gone in subtree_ids(out, bid):
                out.pop(gone, None)
    return out


def moved(base: dict, target: dict) -> set[str]:
    """The blocks ``target`` holds somewhere else than ``base`` did: under
    another parent, or on a key that puts them elsewhere among their
    siblings. A key that changed while ``base``'s key still falls between
    the same two neighbours in ``target`` is no move: the server re-keyed a
    block that landed on a taken key (a delete and an insert at one key
    applied in the other order, say), and nobody moved anything."""
    out = set()
    for parent, ids in children_of(target).items():
        for n, bid in enumerate(ids):
            was = base.get(bid)
            if was is None or (was["parent"] == parent and was["position"] == target[bid]["position"]):
                continue
            lower = target[ids[n - 1]]["position"] if n else None
            upper = target[ids[n + 1]]["position"] if n + 1 < len(ids) else None
            key = was["position"]
            if was["parent"] != parent or not ((lower is None or lower < key) and (upper is None or key < upper)):
                out.add(bid)
    return out


def upload_refs(blocks) -> set[str]:
    """The upload file names a set of blocks (snapshot values or op dicts)
    reference: ``/api/uploads/<name>`` in content or properties, and a
    page's ``doc_id`` (its PDF) — ``storage.upload_refs`` per block."""
    names = set()
    for b in blocks:
        names |= storage.upload_refs(b.get("content") or "", b.get("props") or {})
    return names
