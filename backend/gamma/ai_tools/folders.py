"""Folders and labels, a folder chat's: rename_folder, create_label,
rename_label, label_page (a batch, organizing's machinery), and
delete_folder and delete_label, which never delete a page and which the
user can revert. Folders and labels are blocks of the ``folders`` and
``labels`` trees (blocks_store)."""

import json

from ..ai_context import quoted_paths
from ..blocks_store import (FOLDERS, LABELS, PATH_SEP, TRASH, existing_in, filing, folder_paths, label_inserts,
                            load_props, named, page_in_folder, page_parents, trashed_folders, tree_children,
                            tree_parents, write_lock)
from ..ops import MAX_OPS, after_commit, apply_batches, apply_ops, delete_tree, ensure_filing, tree_deletion
from ..work_folders import filing as work_filing
from ..work_folders.marks import Tree
from .base import (_DELETE_REVERT_MAX, _PREVIEW_ITEMS, _filed_in, _find_folder, _load_scoped_page, _one_line, _reach,
                   _scope_folder, text_diff)
from .organizing import (BATCH_MAX, _batch, _batch_answer, _batch_preview, _batch_text, _batch_write, _digest, _of,
                         _page_ids_arg, _work_refusal)


def _names_arg(value) -> list[str] | None:
    """A list-of-names argument (a lone string counts as one, and the JSON
    text of a list as that list, an empty one too — a saved chat keeps a
    list argument as its text, ``tool_action``): each on one line, blanks
    and repeats (ignoring case) left out. None for text that opens a list
    but is none (one cut off where a saved chat trimmed it): no name."""
    if isinstance(value, str) and value.lstrip().startswith("["):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    items = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    out: dict[str, str] = {}
    for item in items:
        name = _one_line(item) if isinstance(item, str) else ""
        if name:
            out.setdefault(name.lower(), name)
    return list(out.values())


def _labels_called(have: list, name) -> list[str]:
    """The ids of the labels ``have`` (``(id, name)`` pairs) called
    ``name``, as ``blocks_store.named`` matches but with each name read on
    one line (``_one_line``) on both sides: the model writes it so, while an
    imported label may keep a run of spaces, a tab or a newline."""
    return [c[0] for c in named([(i, _one_line(n)) for i, n in have], _one_line(name))]


def _find_label(conn, raw) -> tuple[tuple | None, str | None]:
    """The label a ``label`` argument names (``_labels_called``: exactly,
    else ignoring case): ``((id, name), None)``, or ``(None, error)`` for no
    label of that name or several."""
    name = _one_line(raw)
    if not name:
        return None, "error: name the label"
    have = tree_children(conn, LABELS).get(LABELS, [])
    found = _labels_called(have, name)
    if not found:
        return None, f'error: there is no label "{name}" — list_pages(list_labels=true) shows the labels'
    if len(found) > 1:
        return None, f'error: {len(found)} labels are called "{name}" — the user can tell them apart in the app'
    return (found[0], dict(have)[found[0]]), None


def _label_page_reached(conn, scope: dict, reach: set[str], sub_pages: dict, page_id: str,
                        parent_id: str, props: dict) -> bool:
    """Whether a folder chat (its folder subtree ``reach``) reaches a page
    carrying a label, for ``_label_out_of_reach``: a live sub-page (a key of
    ``sub_pages``, ``page_parents``) by where its top is filed; a page in
    Recently deleted by the folders a restore puts it back in
    (``trashed_folders``); any other page by its own folders."""
    if page_id in sub_pages:
        return page_in_folder(conn, page_id, _scope_folder(scope))
    if parent_id == TRASH:
        return _filed_in(reach, trashed_folders(conn, props))
    return _filed_in(reach, filing(props, FOLDERS))


def _label_out_of_reach(conn, scope: dict, label_id: str, name: str) -> str | None:
    """None when the chat reaches every page carrying the label (``_reach``;
    the library root reaches all), else the refusal. A label is
    library-wide: renaming or deleting it changes every page that carries
    it, so a folder chat may do either only when none of them lies outside
    its folder — a page in Recently deleted counted by the folders a
    restore puts it back in. A label on no page passes."""
    reach = _reach(conn, scope)
    if reach is None:
        return None
    sub_pages = page_parents(conn)
    outside = sum(1 for page_id, parent_id, raw in conn.execute(
        "SELECT p.id, p.parent_id, p.properties FROM unified_blocks p WHERE p.parent_id IN ('root', ?) AND EXISTS "
        "(SELECT 1 FROM json_each(p.properties, '$.labels') l WHERE l.value = ?)", (TRASH, label_id))
        if not _label_page_reached(conn, scope, reach, sub_pages, page_id, parent_id, load_props(raw)))
    if not outside:
        return None
    return (f'error: the label "{name}" is also on {outside} page{"s" if outside != 1 else ""} '
            "outside this chat's folder — only the library chat can rename or delete it")


def _titles(conn, page_ids) -> list[str]:
    """The titles of the pages ``page_ids``, in that order."""
    page_ids = list(page_ids)
    found = dict(conn.execute("SELECT id, content FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))",
                              (json.dumps(page_ids),)).fetchall())
    return [found.get(p) or "Untitled" for p in page_ids]


def _folder_chats(conn, ids) -> dict[str, int]:
    """``{folder id: conversations}`` that deleting the folders ``ids`` files
    into the library chat's history (routers/chats.file_into_home): a
    folder's active conversation when it holds messages, and its earlier
    ones."""
    ids = json.dumps(sorted(ids))
    return dict(conn.execute(
        "SELECT bucket, COUNT(*) FROM (SELECT bucket FROM chats WHERE bucket IN (SELECT value FROM json_each(?)) "
        "AND COALESCE(messages, '') NOT IN ('', '[]') UNION ALL SELECT bucket FROM chat_history "
        "WHERE bucket IN (SELECT value FROM json_each(?))) GROUP BY bucket", (ids, ids)).fetchall())


def _tree_revert(tree: str, rows, carried: dict) -> dict | None:
    """What reverting a folder's or label's deletion needs
    (ai_revert._plan_tree_delete): the ``tree``, every block it takes, parent
    first, as ``[id, parent, position, content, properties]``, and each
    library page's ids of them (``pages``). None when it would not fit:
    past ``_DELETE_REVERT_MAX`` characters (the pages cost ~34 each, so
    some 5,800 pages), or past ``MAX_OPS`` blocks, the most one batch puts
    back. The delete tools then refuse, so no deletion goes without its
    revert."""
    record = {"tree": tree, "blocks": [[r[0], r[1], r[2], r[3] or "", r[4] or "{}"] for r in rows],
              "pages": carried}
    return record if len(rows) <= MAX_OPS and len(json.dumps(record)) <= _DELETE_REVERT_MAX else None


def _labels_diff(entries) -> list:
    """A page's labels as the card shows a change to them: ``entries`` of
    ``(kind, name)`` (``ctx`` kept, ``del`` taken off, ``ins`` put on)
    joined with ", "."""
    out = []
    for kind, name in entries:
        if out:
            out.append(["ctx", ", "])
        out.append([kind, name])
    return out


def _plan_rename_folder(conn, scope: dict, args: dict):
    """``(plan, None)``: the folder (``id``, ``path``) and its path after the
    rename (``to``); ``(None, answer)`` for a call that changes nothing. The
    server does not keep sibling names apart, so this does, the way the
    app's rename does (``named``: exactly, else ignoring case). A work
    folder's directories are named by the disk (the work-folder rule)."""
    found, error = _find_folder(conn, scope, args.get("folder"))
    if error:
        return None, error
    folder_id, path = found
    if not folder_id:
        return None, "error: the library root has no name to change"
    new = _one_line(args.get("name"))
    if not new:
        return None, "error: empty name"
    if PATH_SEP in new:
        return None, (f"error: a folder name cannot hold '{PATH_SEP}', which is how paths read — "
                      "renaming never moves a folder")
    paths = folder_paths(conn)
    if new == paths[folder_id][-1]:
        return None, "ok — the folder already has that name"
    refused = _work_refusal(lambda: work_filing.tree_op(Tree.read(conn), {"op": "set", "id": folder_id, "content": new},
                                                        "person"))
    if refused:
        return None, refused
    parent = tree_parents(conn, FOLDERS)[folder_id]
    if named([c for c in tree_children(conn, FOLDERS).get(parent, []) if c[0] != folder_id], new):
        where = PATH_SEP.join(paths.get(parent, [])) or "the top level"
        return None, f'error: there is already a folder "{new}" in "{where}"'
    return {"id": folder_id, "path": path, "to": PATH_SEP.join([*paths[folder_id][:-1], new]), "name": new}, None


def _preview_rename_folder(conn, scope: dict, args: dict):
    plan, answer = _plan_rename_folder(conn, scope, args)
    if not plan:
        return None, answer
    return {"title": plan["path"], "diff": text_diff(plan["path"], plan["to"])}, None


def _run_rename_folder(conn, ws: str, scope: dict, args: dict):
    """One ``set`` of the folder's name on the folder tree: its pages,
    subfolders, chat and share name it by id, so no page is touched."""
    plan, answer = _plan_rename_folder(conn, scope, args)
    if not plan:
        return answer, None
    after_commit(ws, conn, apply_ops(conn, FOLDERS, [{"op": "set", "id": plan["id"], "content": plan["name"]}],
                                     actor=scope.get("actor", ""), client="ai"))
    return (f'ok — the folder is now "{plan["to"]}"',
            {"kind": "rename", "title": plan["path"], "from": plan["path"], "to": plan["to"],
             "summary": f"Renamed the folder “{plan['path']}” → “{plan['to']}”"})


def _plan_create_label(conn, scope: dict, args: dict):
    """``(name, None)``, or ``(None, answer)``: an empty name, or a label of
    that name (ignoring case) exists already."""
    name = _one_line(args.get("label"))
    if not name:
        return None, "error: empty label name"
    have = tree_children(conn, LABELS).get(LABELS, [])
    found = _labels_called(have, name)
    if found:
        return None, f'ok — the label "{dict(have)[found[0]]}" exists already; nothing changed'
    return name, None


def _preview_create_label(conn, scope: dict, args: dict):
    name, answer = _plan_create_label(conn, scope, args)
    return ({"title": name, "diff": [["ins", name]]}, None) if name else (None, answer)


def _run_create_label(conn, ws: str, scope: dict, args: dict):
    name, answer = _plan_create_label(conn, scope, args)
    if not name:
        return answer, None
    ensure_filing(ws, conn, labels=[name], actor=scope.get("actor", ""), client="ai")
    return (f'ok — made the label "{name}"; label_page puts it on pages',
            {"kind": "label", "title": name, "to": name, "summary": f"Made the label “{name}”"})


def _plan_rename_label(conn, scope: dict, args: dict):
    """``(plan, None)``: the label (``id``, ``name``) and its new name
    (``to``); refused when a page carrying it is out of reach or another
    label has that name (``_labels_called``, as the app's rename checks)."""
    found, error = _find_label(conn, args.get("label"))
    if error:
        return None, error
    label_id, name = found
    new = _one_line(args.get("name"))
    if not new:
        return None, "error: empty name"
    if new == name:
        return None, "ok — the label already has that name"
    error = _label_out_of_reach(conn, scope, label_id, name)
    if error:
        return None, error
    if _labels_called([c for c in tree_children(conn, LABELS).get(LABELS, []) if c[0] != label_id], new):
        return None, f'error: there is already a label "{new}"'
    return {"id": label_id, "name": name, "to": new}, None


def _preview_rename_label(conn, scope: dict, args: dict):
    plan, answer = _plan_rename_label(conn, scope, args)
    if not plan:
        return None, answer
    return {"title": plan["name"], "diff": text_diff(plan["name"], plan["to"])}, None


def _run_rename_label(conn, ws: str, scope: dict, args: dict):
    plan, answer = _plan_rename_label(conn, scope, args)
    if not plan:
        return answer, None
    after_commit(ws, conn, apply_ops(conn, LABELS, [{"op": "set", "id": plan["id"], "content": plan["to"]}],
                                     actor=scope.get("actor", ""), client="ai"))
    return (f'ok — the label is now "{plan["to"]}"',
            {"kind": "rename", "title": plan["name"], "from": plan["name"], "to": plan["to"],
             "summary": f"Renamed the label “{plan['name']}” → “{plan['to']}”"})


def _plan_label_one(conn, scope: dict, page_id: str, labels: dict, reach) -> tuple:
    """One page of a label_page call: ``({page_id, title, current, keep, put,
    diff, added, removed, now, before, after}, None)`` — the label ids it
    carries (``current``: ids that still are labels; ``before`` too), keeps
    and gets (``put``:
    labels that exist; ``labels["make"]`` names the ones still to be made),
    the names it gets and loses, the card's ``diff`` — or ``(None,
    answer)`` for a page already so."""
    loaded, error = _load_scoped_page(conn, scope, {"page_id": page_id}, reach)
    if error:
        return None, error
    page_id, title, props, _ = loaded
    names, make = labels["names"], labels["make"]
    current = existing_in(conn, LABELS, filing(props, LABELS))
    keep = [i for i in current if i not in labels["drop"]]
    put = [i for i in labels["put"] if i not in keep]
    if not put and not make and keep == current:
        return None, "ok — the page's labels already are that; nothing changed"
    diff = _labels_diff([("ctx" if i in keep else "del", names[i]) for i in current]
                        + [("ins", names[i]) for i in put] + [("ins", n) for n in make])
    return {"page_id": page_id, "title": title, "current": current, "keep": keep, "put": put, "diff": diff,
            "added": [names[i] for i in put] + make, "removed": [names[i] for i in current if i not in keep],
            "now": [names[i] for i in keep + put] + make, "before": current, "after": keep + put + make}, None


def _plan_label_page(conn, scope: dict, args: dict):
    """``((labels, batch), None)``: what a label_page call puts on and takes
    off every page it names — ``labels``: ``names`` ({id: name} of every
    label), ``drop`` (ids to take off), ``put`` (ids to put on) and
    ``make`` (names to make) — and its pages (``_batch``); ``(None,
    error)`` for a call refused whole: no pages or too many, a list of names
    cut off, no label named, one both added and removed, a label to remove
    that does not exist."""
    ids, error = _page_ids_arg(args)
    if error:
        return None, error
    add, remove = _names_arg(args.get("add")), _names_arg(args.get("remove"))
    for key, names in (("add", add), ("remove", remove)):
        if names is None:
            return None, f"error: `{key}` must be a list of label names — the list itself, not its text"
    if not add and not remove:
        return None, "error: name the labels to `add` or to `remove`"
    if {n.lower() for n in add} & {n.lower() for n in remove}:
        return None, "error: a label cannot be added and removed in one call"
    have = tree_children(conn, LABELS).get(LABELS, [])
    drop = set()
    for name in remove:
        found = _labels_called(have, name)
        if not found:
            return None, f'error: there is no label "{name}" — list_pages(list_labels=true) shows the labels'
        drop |= set(found)
    put, make = [], []
    for name in add:
        found = _labels_called(have, name)
        if not found:
            make.append(name)
        elif found[0] not in put:
            put.append(found[0])
    labels = {"names": dict(have), "drop": drop, "put": put, "make": make}
    reach = _reach(conn, scope)
    return (labels, _batch(ids, lambda page_id: _plan_label_one(conn, scope, page_id, labels, reach))), None


def _label_digest(labels: dict, plans: list) -> str:
    return _digest("label_page", [labels["put"], labels["make"], sorted(labels["drop"])], plans)


def _preview_label_page(conn, scope: dict, args: dict):
    planned, error = _plan_label_page(conn, scope, args)
    labels, batch = planned or (None, None)
    answer = error or _batch_answer(scope, batch, "changed")
    if answer:
        return None, answer
    plans = batch["plans"]
    if batch["asked"] == 1:
        p = plans[0]
        return {"page_id": p["page_id"], "title": p["title"], "diff": p["diff"]}, None
    return _batch_preview(conn, batch, [[["ctx", f"{p['title']}: "]] + p["diff"] for p in plans],
                          _label_digest(labels, plans),
                          **({"new_labels": labels["make"]} if labels["make"] else {})), None


def _run_label_page(conn, ws: str, scope: dict, args: dict):
    """One ``set`` of each page's ``labels`` — the labels the call names
    that do not exist made first, once (one batch on the label tree) — in
    one transaction planned under the write lock (``_batch_write``)."""
    def write(labels, plans):
        actor, results, made = scope.get("actor", ""), [], {}
        if labels["make"]:
            tree_ops, made = label_inserts(conn, labels["make"])
            if tree_ops:
                results += apply_batches(conn, [(LABELS, tree_ops)], actor=actor, client="ai")
        afters = [p["keep"] + p["put"] + [made[n] for n in labels["make"]] for p in plans]
        results += apply_batches(conn, [(p["page_id"], [{"op": "set", "id": p["page_id"],
                                                         "props": {LABELS: after or None}}])
                                        for p, after in zip(plans, afters)], actor=actor, client="ai")
        return results, afters

    done, answer = _batch_write(conn, ws, scope, args, _plan_label_page, "changed", _label_digest, write)
    if answer:
        return answer, None
    labels, batch, afters = done
    plans = batch["plans"]
    revert = {"pages": [[p["page_id"], "labels", p["current"], after] for p, after in zip(plans, afters)]}
    k = len(labels["make"])
    made_note = f" ({k} new label{'s' if k != 1 else ''})" if k else ""
    if batch["asked"] == 1:
        p = plans[0]
        now = ", ".join(f'"{n}"' for n in p["now"]) or "no label"
        change = ", ".join([f"+{n}" for n in p["added"]] + [f"−{n}" for n in p["removed"]])
        return (f'ok — "{p["title"]}" now carries {now}{made_note}',
                {"kind": "label", "page_id": p["page_id"], "title": p["title"], "added": p["added"],
                 "removed": p["removed"], "summary": f"Labels of “{p['title'][:60]}”: {change}", "revert": revert})
    names = labels["names"]
    added = [names[i] for i in labels["put"]] + labels["make"]
    removed = [names[i] for i in names if i in labels["drop"]]
    change = ", ".join([f"+{n}" for n in added] + [f"−{n}" for n in removed])
    n = len(plans)
    return (_batch_text(f"changed the labels of {_of(n, batch['asked'])}: {change}{made_note}", plans, batch),
            {"kind": "label", "added": added, "removed": removed,
             "summary": f"Labels of {n} page{'s' if n != 1 else ''}: {change}", "changes": n,
             "items": [{"page_id": p["page_id"], "title": p["title"], "added": p["added"], "removed": p["removed"]}
                       for p in plans],
             "revert": revert})


def _plan_delete_label(conn, scope: dict, args: dict):
    """``(plan, None)``: the label (``id``, ``name``), the library pages
    carrying it in library order (``pages``: ids) and the action's
    ``revert`` (``_tree_revert``). Refused with no card: a label the chat
    may not delete (``_label_out_of_reach``), and one on too many pages to
    record for a revert."""
    found, error = _find_label(conn, args.get("label"))
    if error:
        return None, error
    label_id, name = found
    error = _label_out_of_reach(conn, scope, label_id, name)
    if error:
        return None, error
    rows, carried, _ = tree_deletion(conn, LABELS, label_id)
    revert = _tree_revert(LABELS, rows, carried)
    if not revert:
        return None, (f'error: the label "{name}" is on {len(carried)} pages — too many to record for a '
                      "revert, so the assistant does not delete it; the user can, in the app")
    return {"id": label_id, "name": name, "pages": list(carried), "revert": revert}, None


def _preview_delete_label(conn, scope: dict, args: dict):
    plan, answer = _plan_delete_label(conn, scope, args)
    if not plan:
        return None, answer
    return {"block_id": plan["id"], "title": plan["name"], "diff": [["del", plan["name"]]],
            "pages": _titles(conn, plan["pages"][:_PREVIEW_ITEMS]), "page_count": len(plan["pages"])}, None


def _run_delete_label(conn, ws: str, scope: dict, args: dict):
    """``ops.delete_tree`` on the label: its checks, its revert record and
    the deletion under one write lock (``delete_tree`` reads the same
    subtree and pages again under it)."""
    write_lock(conn)
    plan, answer = _plan_delete_label(conn, scope, args)
    if not plan:
        conn.rollback()
        return answer, None
    delete_tree(ws, conn, LABELS, plan["id"], actor=scope.get("actor", ""), client="ai")
    n, name = len(plan["pages"]), plan["name"]
    text = (f'ok — deleted the label "{name}"'
            + (f"; {n} page{'s' if n != 1 else ''} lost it, and no page was deleted" if n else "")
            + ". The user can revert it from the reply.")
    return text, {"kind": "delete", "block_id": plan["id"], "title": name, "count": n,
                  "summary": f"Deleted the label “{name}”", "revert": plan["revert"]}


def _plan_delete_folder(conn, scope: dict, args: dict):
    """``(plan, None)``: the folder (``id``, ``path``), every folder's path
    (``paths``), its subfolders in tree order (``subfolders``: ids), the
    library pages filed in it or below in library order (``pages``: ids —
    they lose that filing and stay), the conversations that move to the
    library chat's history by folder (``chats``: ``{folder id: n}``) and
    the action's ``revert``. Refused with no card: the library root, the
    chat's own folder (one above it is out of reach already), a folder
    outside the chat's reach, a folder with a share link anywhere in it —
    the deletion would stop the link for good, so the user stops sharing
    first — one too large to record for a revert, and a work folder's
    directory or root, or a folder with one inside (the work-folder rule)."""
    from ..routers.shares import shared_folders  # local: keep gamma.* free of the routers package

    found, error = _find_folder(conn, scope, args.get("folder"))
    if error:
        return None, error
    folder_id, path = found
    if not folder_id:
        return None, "error: the library root is no folder to delete"
    if folder_id == _scope_folder(scope):
        return None, (f'error: "{path}" is this chat\'s own folder — it can be deleted only from the folder '
                      "above it or from the library chat")
    refused = _work_refusal(lambda: work_filing.tree_op(Tree.read(conn), {"op": "delete", "id": folder_id}, "person"))
    if refused:
        return None, refused
    rows, carried, _ = tree_deletion(conn, FOLDERS, folder_id)
    ids, paths = {r[0] for r in rows}, folder_paths(conn)
    shared = shared_folders(scope["ws"], ids)
    if shared:
        return None, (f"error: {quoted_paths(paths, sorted(shared))} "
                      f"{'has a share link' if len(shared) == 1 else 'have share links'}, which deleting would "
                      "stop for good — the user must stop sharing first (Share… → Stop sharing)")
    subfolders = [f for f in paths if f in ids and f != folder_id]  # tree order
    revert = _tree_revert(FOLDERS, rows, carried)
    if not revert:
        return None, (f'error: "{path}" ({len(subfolders)} subfolders, {len(carried)} pages filed in it) is too '
                      "large to record for a revert, so the assistant does not delete it; the user can, in the app")
    return {"id": folder_id, "path": path, "paths": paths, "subfolders": subfolders, "pages": list(carried),
            "chats": _folder_chats(conn, ids), "revert": revert}, None


def _preview_delete_folder(conn, scope: dict, args: dict):
    plan, answer = _plan_delete_folder(conn, scope, args)
    if not plan:
        return None, answer
    paths, chats = plan["paths"], plan["chats"]
    chat_folders = [f for f in paths if f in chats]  # tree order
    return {"block_id": plan["id"], "title": plan["path"], "diff": [["del", plan["path"]]],
            "subfolders": [PATH_SEP.join(paths[f]) for f in plan["subfolders"][:_PREVIEW_ITEMS]],
            "subfolder_count": len(plan["subfolders"]),
            "pages": _titles(conn, plan["pages"][:_PREVIEW_ITEMS]), "page_count": len(plan["pages"]),
            "chats": sum(chats.values()),
            "chat_folders": [PATH_SEP.join(paths[f]) for f in chat_folders[:_PREVIEW_ITEMS]],
            "chat_folder_count": len(chat_folders)}, None


def _run_delete_folder(conn, ws: str, scope: dict, args: dict):
    """The UI's "Keep pages" delete (``ops.delete_tree``): the folder and its
    subfolders go, their pages stay, filed in their other folders or at the
    library root, and their AI chats move to the library chat's history,
    which the revert record keeps (``chats``: ``{history id: folder}``), so
    a revert puts them back. The checks, the revert record and the deletion
    run under one write lock."""
    write_lock(conn)
    plan, answer = _plan_delete_folder(conn, scope, args)
    if not plan:
        conn.rollback()
        return answer, None
    _, _, results = delete_tree(ws, conn, FOLDERS, plan["id"], actor=scope.get("actor", ""), client="ai")
    moved = results[0]["chats_filed"]
    path, subs, n, chats = plan["path"], len(plan["subfolders"]), len(plan["pages"]), len(moved)
    text = (f'ok — deleted the folder "{path}"'
            + (f" with its {subs} subfolder{'s' if subs != 1 else ''}" if subs else "")
            + (f"; its {n} page{'s stay' if n != 1 else ' stays'} in the library, filed in "
               "their other folders or at the library root" if n else "")
            + (f"; {chats} AI chat{'s' if chats != 1 else ''} of these folders moved to the library "
               "chat's history, and a revert brings them back" if chats else "")
            + ". The user can revert it from the reply.")
    return text, {"kind": "delete", "block_id": plan["id"], "title": path, "count": n, "chats": chats,
                  "summary": f"Deleted the folder “{path}”",
                  "revert": {**plan["revert"], **({"chats": moved} if moved else {})}}


# The deletions whose card names one folder or label (``block_id``): an
# allowed card deletes only that one (ai_agent.ApprovalGate).
TREE_DELETES = frozenset({"delete_folder", "delete_label"})


TOOLS = [
    {
        "perm": "organize", "kind": "rename", "scopes": ("folder",), "mutating": True,
        "run": _run_rename_folder, "preview": _preview_rename_folder,
        "spec": {
            "name": "rename_folder",
            "description": (
                "Rename a folder. `folder` is its id or its path as list_folders shows it, read "
                "like move_page's (\"\" = the current folder); `name` is its new name — a name, "
                "never a path: renaming never moves a folder. Its pages, subfolders, chat and "
                "share link stay with it. A name a folder beside it already has is refused."),
            "parameters": {
                "type": "object",
                "properties": {"folder": {"type": "string"},
                               "name": {"type": "string", "description": "the new name"}},
                "required": ["folder", "name"],
            },
        },
    },
    {
        "perm": "organize", "kind": "label", "scopes": ("folder",), "mutating": True,
        "run": _run_create_label, "preview": _preview_create_label,
        "spec": {
            "name": "create_label",
            "description": (
                "Make a new label, on no page yet. Labels are flat and library-wide. A label of "
                "that name (ignoring case) is not made twice. To put labels on pages use "
                "label_page, which makes a label it does not find — so call this only when the "
                "user wants a label with no pages."),
            "parameters": {
                "type": "object",
                "properties": {"label": {"type": "string", "description": "the new label's name"}},
                "required": ["label"],
            },
        },
    },
    {
        "perm": "organize", "kind": "rename", "scopes": ("folder",), "mutating": True,
        "run": _run_rename_label, "preview": _preview_rename_label,
        "spec": {
            "name": "rename_label",
            "description": (
                "Rename a label on every page that carries it. `label` is its current name, as "
                "list_pages shows it (exact, else ignoring case); `name` the new one. A name "
                "another label has is refused. In a folder below the library root, only a label "
                "that no page outside the folder carries."),
            "parameters": {
                "type": "object",
                "properties": {"label": {"type": "string", "description": "the label's current name"},
                               "name": {"type": "string", "description": "the new name"}},
                "required": ["label", "name"],
            },
        },
    },
    {
        "perm": "organize", "kind": "label", "scopes": ("folder",), "mutating": True,
        "run": _run_label_page, "preview": _preview_label_page,
        "spec": {
            "name": "label_page",
            "description": (
                f"Put labels on pages and/or take labels off them. `page_ids`: the pages, up to {BATCH_MAX} "
                "in one call (exact ids from list_pages). `add` and `remove` are lists of label names, as "
                "list_pages shows them (exact, else ignoring case), and apply to every page of the call; a "
                "label to add that does not exist yet is made. To give pages different labels, make one "
                "call per set of labels. Each page's other labels stay. A page that cannot be changed is "
                "left out and named in the result."),
            "parameters": {
                "type": "object",
                "properties": {"page_ids": {"type": "array", "items": {"type": "string"}},
                               "add": {"type": "array", "items": {"type": "string"},
                                       "description": "label names to put on the pages"},
                               "remove": {"type": "array", "items": {"type": "string"},
                                          "description": "label names to take off them"}},
                "required": ["page_ids"],
            },
        },
    },
    {
        "perm": "organize_delete", "kind": "delete", "scopes": ("folder",), "mutating": True,
        "run": _run_delete_folder, "preview": _preview_delete_folder,
        "spec": {
            "name": "delete_folder",
            "description": (
                "Delete a folder with its subfolders. Its pages are never deleted: they stay in "
                "the library, in their other folders or at the library root; only this filing "
                "goes. The folders' AI chats move to the library chat's history. `folder` is an "
                "id or path, read like move_page's. Refused for the current folder and the ones "
                "above it, and for a folder with a share link (the user stops sharing it first). "
                "The user can revert it from the reply, chats included. Only for a folder the "
                "user asked to delete."),
            "parameters": {
                "type": "object",
                "properties": {"folder": {"type": "string"}},
                "required": ["folder"],
            },
        },
    },
    {
        "perm": "organize_delete", "kind": "delete", "scopes": ("folder",), "mutating": True,
        "run": _run_delete_label, "preview": _preview_delete_label,
        "spec": {
            "name": "delete_label",
            "description": (
                "Delete a label and take it off every page that carries it; no page is deleted. "
                "`label` is its name, as list_pages shows it. In a folder below the library root, "
                "only a label that no page outside the folder carries. The user can revert it "
                "from the reply. Only for a label the user asked to delete."),
            "parameters": {
                "type": "object",
                "properties": {"label": {"type": "string"}},
                "required": ["label"],
            },
        },
    },
]
