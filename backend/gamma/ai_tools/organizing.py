"""The page tools of a folder chat: rename_page, move_page, list_deleted,
restore_page and delete_page (to Recently deleted, never for good).

rename_page, move_page, label_page and restore_page take a list of pages.
Every page goes through the one-page planner, so it meets the checks a call
naming it alone meets; what the call shares (the folder, the labels) is
read once. A page that cannot change is left out and named, the others
change in one transaction, a folder or label the call makes included (a
restore: one per page). A call that names one page answers, previews and
acts as the one-page tool always has."""

import hashlib
import json
from collections import Counter

from ..ai_context import paths_text, quoted_paths
from ..blocks_store import (BLOCK_ID_RE, FOLDERS, PATH_SEP, TRASH, existing_in, folder_chain, folder_inserts,
                            folder_paths, load_props, page_descendants, page_parent, write_lock)
from ..ops import after_commits, apply_batches
from ..logbuf import log
from ..trash import KEEP_DAYS, list_trash
from ..work_folders import filing as work_filing
from ..work_folders.library import work_folder_of
from ..work_folders.marks import Tree
from .base import (CHANGE_LIMIT_PREFIX, _LIST_CAP, _NO_PAGE, _PREVIEW_ITEMS, _excerpt, _filed_in, _load_scoped_page,
                   _one_line, _reach, _resolve_folder, _scope_folder, text_diff)


BATCH_MAX = 100    # pages one move_page, label_page or restore_page call names: a list_pages(limit=100)
                   # batch (ai_revert reads it as the most one revert record holds)
_RENAME_MAX = 50   # renames one rename_page call holds: each title is written out, and a turn the
                   # provider's output cap cuts off is lost whole
BATCH_CHANGED = ("error: while the approval card waited, some of these pages changed — or the folder or "
                 "labels the call names did — so nothing was changed. Look at them again and send the call "
                 "once more; the user is asked again.")


def _listed(value) -> list:
    """A list argument as the model sent it: a list, or the whole JSON text
    of one (a saved chat keeps a list argument as its text,
    ``tool_action``, and a model reading its earlier calls back may send it
    so); [] for anything else — a list cut off where a saved chat trimmed
    it is no list."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def listed_pages(args: dict) -> list:
    """The list a call names its pages in, read as the executors read it —
    ``renames``, else ``page_ids`` (a list, its whole JSON text, or one bare
    id) — for the loop's running step; [] for the one-page form."""
    raw = args.get("page_ids")
    if isinstance(raw, str) and BLOCK_ID_RE.match(raw.strip()):
        return [raw.strip()]
    return _listed(args.get("renames")) or _listed(raw)


def _cut_id(value) -> str:
    """A page id as the model sent it, at most 64 characters (then "…"): a
    long bogus one is answered "no such page" without being looked up, and
    never shown whole."""
    text = str(value).strip()
    return text if len(text) <= 64 else text[:64] + "…"


def _page_ids_arg(args: dict, source: str = "list_pages") -> tuple[list | None, str | None]:
    """The pages a move_page, label_page or restore_page call names, in
    order, repeats dropped: ``page_ids`` — a list, its whole JSON text, or
    one bare id — else the one-page form's ``page_id``. ``(None, error)``
    for none (text that is neither, a cut-off list or ids in prose, is
    refused, never read in part) or more than ``BATCH_MAX``."""
    raw = args.get("page_ids")
    if raw in (None, "", []) and args.get("page_id"):
        raw = [args["page_id"]]
    elif isinstance(raw, str) and BLOCK_ID_RE.match(raw.strip()):
        raw = [raw.strip()]
    ids = list(dict.fromkeys(_cut_id(i) for i in _listed(raw) if isinstance(i, (str, int)) and str(i).strip()))
    if not ids:
        return None, (f"error: `page_ids` must be a list of page ids from {source} — the list itself, "
                      "not its text")
    if len(ids) > BATCH_MAX:
        return None, (f"error: {len(ids)} pages — at most {BATCH_MAX} in one call; send the first "
                      f"{BATCH_MAX} and the rest in further calls")
    return ids, None


def _renames_arg(args: dict) -> tuple[list | None, str | None]:
    """rename_page's ``renames`` as ``[(page id, title)]`` in order — each
    title on one line, a pair named twice kept once — else the one-page
    form's ``page_id`` and ``title``. ``(None, error)`` for none, a value
    that is no list of ``{page_id, title}``, or more than ``_RENAME_MAX``."""
    raw = args.get("renames")
    items = _listed(raw)
    if not items and raw in (None, "", []) and args.get("page_id"):
        items = [{"page_id": args.get("page_id"), "title": args.get("title")}]
    pairs = list(dict.fromkeys((_cut_id(r.get("page_id") or ""), _one_line(r.get("title")))
                               for r in items if isinstance(r, dict)))
    if not pairs:
        return None, ("error: `renames` must be a list of {page_id, title} — the list itself, not its "
                      "text — with ids from list_pages")
    if len(pairs) > _RENAME_MAX:
        return None, (f"error: {len(pairs)} renames — at most {_RENAME_MAX} in one call; send the first "
                      f"{_RENAME_MAX} and the rest in further calls")
    return pairs, None


def _batch(keys: list, plan_one) -> dict:
    """The pages a call names, each planned by ``plan_one(key)`` — the
    one-page planner's ``(plan, None)`` or ``(None, answer)``:
    ``{"plans", "same", "refused", "asked"}`` — the pages that change (in
    call order), the ones already so (an "ok — …" answer) and the ones it
    cannot change (an error), both as ``(key, answer)``, and how many it
    named. A key with no id's shape (blank, or cut by ``_cut_id``) is
    refused without being looked up."""
    out = {"plans": [], "same": [], "refused": [], "asked": len(keys)}
    for key in keys:
        plan, answer = plan_one(key) if BLOCK_ID_RE.match(_key_id(key)) else (None, _NO_PAGE)
        if plan:
            out["plans"].append(plan)
        else:
            out["same" if answer.startswith("ok") else "refused"].append((key, answer))
    return out


def _key_id(key) -> str:
    """The page id in a ``_batch`` key: an id, or rename_page's ``(id, title)``."""
    return key[0] if isinstance(key, tuple) else key


def _of(n: int, asked: int) -> str:
    """"35 of 37 pages" when a call changed fewer pages than it named, else "37 pages"."""
    return f"{n} of {asked} pages" if n < asked else f"{n} page{'s' if n != 1 else ''}"


def _left_out_lines(batch: dict) -> str:
    """What a batch did not change, for the model: one line naming the pages
    already so, then the pages it left out grouped by reason — a reason is
    one of a few planner answers, so a hundred pages read in a few lines."""
    lines = []
    if batch["same"]:
        lines.append("Already so, not changed: " + ", ".join(_key_id(k) for k, _ in batch["same"]))
    if batch["refused"]:
        why: dict[str, list] = {}
        for key, answer in batch["refused"]:
            why.setdefault(answer.removeprefix("error: "), []).append(_key_id(key))
        lines.append("Left out — fix these and send only them again:")
        lines += [f"- {reason}: {', '.join(ids)}" for reason, ids in why.items()]
    return "\n".join(lines)


def _batch_answer(scope: dict, batch: dict, verb: str) -> str | None:
    """What a call answers without a card or a write, or None when it has
    pages to change: a one-page call its page's answer, as the one-page tool
    always has; a call whose pages are all already so, or none of which can
    change, says so; one that would change more pages than this message may
    still change (``scope["changes_left"]``, set by the loop; None: no
    limit) is refused whole, before any card."""
    plans = batch["plans"]
    if batch["asked"] == 1 and not plans:
        return (batch["same"] or batch["refused"])[0][1]
    if not plans:
        if not batch["refused"]:
            return f"ok — all {batch['asked']} pages already are so; nothing changed"
        return f"error: none of these pages can be {verb}:\n" + _left_out_lines(batch)
    left = scope.get("changes_left")
    if left is not None and len(plans) > left:
        return (f"{CHANGE_LIMIT_PREFIX} is near: this call would change {len(plans)} pages and the message "
                f"may make only {left} more change{'s' if left != 1 else ''}, so nothing was changed. Send "
                f"at most {left}, or stop and tell the user what is done and what is left: they can continue "
                "in their next message")
    return None


def _digest(tool: str, shared, plans: list) -> str:
    """What a batch's card shows, as the run must find it again: the tool,
    what the call shares (the folder, the labels) and each page with what it
    is now (``before``) and ends as (``after``) — a page changed while the
    card waited voids the call, even where it would end the same. ASCII
    JSON, so a lone surrogate the model sent hashes too."""
    text = json.dumps([tool, shared, [[p["page_id"], p["before"], p["after"]] for p in plans]], sort_keys=True)
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _changed_since(scope: dict, digest: str) -> str | None:
    """``BATCH_CHANGED`` when the user allowed a batch's card
    (``scope["approved"]``, its digest — ``run_agent_tool``'s ``approved``)
    and the call, planned again under the write lock, would no longer do
    what the card showed; None otherwise (no card, or the same change)."""
    approved = scope.get("approved")
    return BATCH_CHANGED if approved and approved != digest else None


def _batch_write(conn, ws: str, scope: dict, args: dict, plan, verb: str, digest, write) -> tuple:
    """The run of rename_page, move_page and label_page: one transaction
    under the write lock from the plan to the commit, so an allowed card is
    the change made. ``plan`` is the tool's planner, run again under the
    lock: ``((shared, batch), None)`` — what the call shares (the folder,
    the labels) and its pages (``_batch``) — or ``(None, error)``. A call
    ``_batch_answer`` answers (``verb``: "renamed", …), or whose
    ``digest(shared, plans)`` is not the allowed card's (``_changed_since``),
    rolls back: ``(None, answer)``. Otherwise ``write(shared, plans)``
    applies the batches (``apply_batches``) and gives ``(results, kept)``,
    one commit makes them the change, and ``ops.after_commits`` fans them
    out: ``((shared, batch, kept), None)``, ``kept`` being what the write
    keeps for the tool's text and card. A failure rolls back and raises."""
    write_lock(conn)
    try:
        planned, error = plan(conn, scope, args)
        shared, batch = planned or (None, None)
        answer = error or _batch_answer(scope, batch, verb) or _changed_since(scope, digest(shared, batch["plans"]))
        if answer:
            conn.rollback()
            return None, answer
        results, kept = write(shared, batch["plans"])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    after_commits(ws, conn, results)
    return (shared, batch, kept), None


def _batch_preview(conn, batch: dict, lines: list, digest: str, **shared) -> dict:
    """A card for a call naming two or more pages: how many pages change
    (``count``), one line per page (``diff``: word-diff lines joined by line
    breaks, every page — the call's cap bounds them), the pages it leaves
    out by title (a library or Recently deleted page's, cut to one line),
    else by the id the model gave (``left_out``, the first
    ``_PREVIEW_ITEMS``, and ``left_out_count``; the reasons are for the
    model, in its result), the pages already so (``same_count``), what the
    call shares (``to``, ``new_folder``, ``new_labels``) and the ``digest``
    the run must find again."""
    diff = []
    for line in lines:
        diff += ([["ctx", "\n"]] if diff else []) + line
    refused = [_key_id(k) for k, _ in batch["refused"]][:_PREVIEW_ITEMS]
    # Pages only: a note's id the model passed would put the note's text here.
    titles = dict(conn.execute("SELECT id, content FROM unified_blocks WHERE parent_id IN ('root', ?) "
                               "AND id IN (SELECT value FROM json_each(?))", (TRASH, json.dumps(refused))).fetchall())
    return {"count": len(batch["plans"]), "diff": diff,
            "left_out": [f"“{_excerpt(titles[i])}”" if titles.get(i) else i for i in refused],
            "left_out_count": len(batch["refused"]), "same_count": len(batch["same"]),
            **shared, "digest": digest}


def _batch_text(head: str, plans: list, batch: dict, *, links: bool = False) -> str:
    """A batch's result for the model: what it did (``head``), then what it
    left out and why (``_left_out_lines``) — what the next call needs, so it
    comes before the list a saved chat may cut (``tool_action`` keeps
    ``_DETAIL_CAP`` characters) — then the ids it changed, which tell it in
    a later turn which pages are done, and for a restore links to the first
    ``_PREVIEW_ITEMS``, which the reply points the user to."""
    tail = _left_out_lines(batch)
    text = f"ok — {head}." + (f"\n{tail}" if tail else "") + "\nChanged: " + ", ".join(p["page_id"] for p in plans)
    if links:
        shown = plans[:_PREVIEW_ITEMS]
        text += ("\nLinks: " + ", ".join(f"[{p['title'][:60]}](/?page={p['page_id']})" for p in shown)
                 + (f" (and {len(plans) - len(shown)} more)" if len(plans) > len(shown) else ""))
    return text


def _work_refusal(check) -> str | None:
    """What the work-folder rule (gamma/work_folders/filing.py) answers a
    change ``check()`` asks it of, as a tool answers it: "error: " and the
    words the op path refuses it with, None when it passes. The tools ask
    up front, as a person (``client`` "ai"), so the model reads why."""
    try:
        check()
    except work_filing.Refused as e:
        return f"error: {e.detail}"
    return None


def _plan_rename_one(conn, scope: dict, page_id: str, title: str, reach, tree: Tree) -> tuple:
    """One page of a rename_page call: ``({page_id, title, before, to,
    after}, None)`` — its title now as it reads, as it is stored
    (``before``: "" for an untitled page, which a revert gives back) and
    the new one — or ``(None, answer)`` for a page it does not change, and
    for a work folder's file named by its file (``tree``, the folders as
    the rule reads them)."""
    loaded, error = _load_scoped_page(conn, scope, {"page_id": page_id}, reach)
    if error:
        return None, error
    page_id, old, props, _ = loaded
    new = _one_line(title)
    if not new:
        return None, "error: empty title"
    if new == old:
        return None, "ok — title already is that"
    refused = _work_refusal(lambda: work_filing.root_patch(tree, props, None, new, "person"))
    if refused:
        return None, refused
    raw = old if old != "Untitled" else (conn.execute(
        "SELECT content FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()[0] or "")
    return {"page_id": page_id, "title": old, "before": raw, "to": new, "after": new}, None


def _plan_rename_page(conn, scope: dict, args: dict):
    """``((None, batch), None)``: the pages a rename_page call names
    (``_batch``; move_page's shape, with nothing shared) — a page named
    twice with two titles is refused both times — or ``(None, error)`` for
    a call refused whole (``_renames_arg``)."""
    pairs, error = _renames_arg(args)
    if error:
        return None, error
    reach, times, tree = _reach(conn, scope), Counter(page_id for page_id, _ in pairs), Tree.read(conn)

    def one(pair):
        if times[pair[0]] > 1:
            return None, "error: named twice with different titles — send it once"
        return _plan_rename_one(conn, scope, pair[0], pair[1], reach, tree)
    return (None, _batch(pairs, one)), None


def _rename_digest(_, plans: list) -> str:
    return _digest("rename_page", "", plans)


def _preview_rename_page(conn, scope: dict, args: dict):
    planned, error = _plan_rename_page(conn, scope, args)
    _, batch = planned or (None, None)
    answer = error or _batch_answer(scope, batch, "renamed")
    if answer:
        return None, answer
    plans = batch["plans"]
    if batch["asked"] == 1:
        p = plans[0]
        return {"page_id": p["page_id"], "title": p["title"], "diff": text_diff(p["title"], p["to"])}, None
    return _batch_preview(conn, batch, [text_diff(p["title"], p["to"]) for p in plans],
                          _rename_digest(None, plans)), None


def _run_rename_page(conn, ws: str, scope: dict, args: dict):
    """Each page's title, one ``set`` of its content per page, in one
    transaction planned under the write lock (``_batch_write``)."""
    def write(_, plans):
        return apply_batches(conn, [(p["page_id"], [{"op": "set", "id": p["page_id"], "content": p["to"]}])
                                    for p in plans], actor=scope.get("actor", ""), client="ai"), None

    done, answer = _batch_write(conn, ws, scope, args, _plan_rename_page, "renamed", _rename_digest, write)
    if answer:
        return answer, None
    _, batch, _ = done
    plans = batch["plans"]
    revert = {"pages": [[p["page_id"], "title", p["before"], p["to"]] for p in plans]}
    if batch["asked"] == 1:
        p = plans[0]
        return (f'ok — renamed to "{p["to"]}"',
                {"kind": "rename", "page_id": p["page_id"], "title": p["title"], "from": p["title"],
                 "to": p["to"], "summary": f"Renamed “{p['title']}” → “{p['to']}”", "revert": revert})
    n = len(plans)
    return (_batch_text(f"renamed {_of(n, batch['asked'])}", plans, batch),
            {"kind": "rename", "summary": f"Renamed {n} page{'s' if n != 1 else ''}", "changes": n,
             "items": [{"page_id": p["page_id"], "title": p["title"], "from": p["title"], "to": p["to"]}
                       for p in plans],
             "revert": revert})


def _moved_into(p: dict, folder_id: str) -> list:
    """The folders a move_page leaves a page in (``folder_id`` the target,
    "" the library root): those it stays in, then the target — first for a
    work folder's file moved to another directory of its work folder
    (``lead``), whose directory leads its folders."""
    target = [folder_id] if folder_id else []
    return target + p["stays"] if p.get("lead") else p["stays"] + target


def _plan_move_one(conn, scope: dict, page_id: str, target: dict, reach, chain: set, tree: Tree) -> tuple:
    """One page of a move_page call: ``({page_id, title, folders, stays,
    lead, before, after}, None)`` — the folders it is in now (ids that
    still are folders; ``before`` too) and the ones it stays in beside the
    target: those outside the chat's folder, less any above the target
    (``chain``: the target's folders from the top), which give way as
    ``refiled`` has them, and a work folder's file its directory, unless
    the target is another directory of its work folder (``lead``) — or
    ``(None, answer)`` for a page already there, for a sub-page, which is
    filed where the page it is inside is, and for a move the work-folder
    rule refuses (``tree``, the folders as it reads them)."""
    loaded, error = _load_scoped_page(conn, scope, {"page_id": page_id}, reach)
    if error:
        return None, error
    page_id, title, props, folders = loaded
    parent = page_parent(conn, page_id)
    if parent:
        return None, (f"error: that page is inside page {parent} and is filed where that page is — "
                      "move the outermost page instead")
    folders = existing_in(conn, FOLDERS, folders)
    stays = [f for f in folders if reach is not None and f not in reach and f not in chain]
    work = work_folder_of(props)
    lead = bool(work and target["id"] and tree.work_of(target["id"]) == work)
    if work and folders and not lead:
        stays = [folders[0], *(f for f in stays if f != folders[0])]
    if not target.get("names") and set(stays + ([target["id"]] if target["id"] else [])) == set(folders):
        return None, "ok — page is already there"
    plan = {"page_id": page_id, "title": title, "folders": folders, "stays": stays, "lead": lead, "before": folders,
            "after": stays}
    refused = _work_refusal(lambda: work_filing.root_patch(tree, props, {FOLDERS: _moved_into(plan, target["id"])},
                                                           None, "person"))
    return (None, refused) if refused else (plan, None)


def _plan_move_page(conn, scope: dict, args: dict):
    """``((target, batch), None)``: the folder a move_page call files its
    pages into (``_resolve_folder``'s target: id "" = the library root,
    ``names`` for one to make below ``under``) and its pages (``_batch``);
    ``(None, error)`` for a call refused whole — no pages or too many, a
    folder outside the chat's, a path several folders share, an id that is
    no folder's."""
    ids, error = _page_ids_arg(args)
    if error:
        return None, error
    target, error = _resolve_folder(conn, scope, args.get("folder"), write=True)
    if error:
        return None, error
    reach, tree = _reach(conn, scope), Tree.read(conn)
    if target.get("names") and tree.work_of(target["under"]):
        return None, f"error: {work_filing.refusal_text('directories')}"
    # A folder to make sits below `under`: its chain is under's, plus itself (no page is in it yet).
    chain = set(folder_chain(conn, target["id"] or target.get("under") or ""))
    return (target, _batch(ids, lambda page_id: _plan_move_one(conn, scope, page_id, target, reach, chain,
                                                               tree))), None


def _move_digest(target: dict, plans: list) -> str:
    """A move's digest: the folder by id; for one the call makes, the folder
    it goes under and the names it makes — its path alone reads the same
    after that folder is renamed (``_resolve_folder`` then walks from the
    top and would make it elsewhere)."""
    shared = ["+", target["under"], target["names"]] if target.get("names") else target["id"]
    return _digest("move_page", shared, plans)


def _move_line(paths: dict, p: dict, target_id: str) -> list:
    """A move card's line for one page: its title, the folders it leaves
    struck out, then the ones it stays in (the target is the headline's)."""
    gone = [f for f in p["folders"] if f not in p["stays"] and f != target_id]
    line = [["ctx", p["title"]]]
    if gone:
        line += [["ctx", " · "], ["del", ", ".join(paths_text(paths, gone))]]
    if p["stays"]:
        line += [["ctx", (", " if gone else " · ") + ", ".join(paths_text(paths, p["stays"]))]]
    return line


def _preview_move_page(conn, scope: dict, args: dict):
    planned, error = _plan_move_page(conn, scope, args)
    target, batch = planned or (None, None)
    answer = error or _batch_answer(scope, batch, "moved")
    if answer:
        return None, answer
    paths, plans = folder_paths(conn), batch["plans"]
    if batch["asked"] == 1:
        p = plans[0]
        return {"page_id": p["page_id"], "title": p["title"],
                "from": ", ".join(paths_text(paths, p["folders"])), "to": target["path"]}, None
    return _batch_preview(conn, batch, [_move_line(paths, p, target["id"]) for p in plans],
                          _move_digest(target, plans), to=target["path"],
                          **({"new_folder": True} if target.get("names") else {})), None


def _run_move_page(conn, ws: str, scope: dict, args: dict):
    """File the pages into the target folder — made first when it is
    missing — as one transaction (``_batch_write``): the folder tree's
    batch, then one ``set`` of each page's ``folders``: the ones it stays
    in, then the target."""
    def write(target, plans):
        paths, actor = folder_paths(conn), scope.get("actor", "")
        results, folder_id = [], target["id"]
        if target.get("names"):
            tree_ops, made = folder_inserts(conn, [target["names"]], target["under"])
            if tree_ops:
                results += apply_batches(conn, [(FOLDERS, tree_ops)], actor=actor, client="ai")
            folder_id = made[tuple(target["names"])]
        afters = [_moved_into(p, folder_id) for p in plans]  # = refiled(kept, folder_id)
        results += apply_batches(conn, [(p["page_id"], [{"op": "set", "id": p["page_id"], "props": {FOLDERS: after}}])
                                        for p, after in zip(plans, afters)], actor=actor, client="ai")
        return results, (paths, afters)

    done, answer = _batch_write(conn, ws, scope, args, _plan_move_page, "moved", _move_digest, write)
    if answer:
        return answer, None
    target, batch, (paths, afters) = done
    plans = batch["plans"]
    where = target["path"] or "the library root"
    new = " (a new folder)" if target.get("names") else ""
    revert = {"pages": [[p["page_id"], "folders", p["folders"], after] for p, after in zip(plans, afters)]}
    items = [{"page_id": p["page_id"], "title": p["title"], "from": ", ".join(paths_text(paths, p["folders"])),
              "to": target["path"]} for p in plans]
    if batch["asked"] == 1:
        return (f'ok — moved to "{where}"{new}',
                {"kind": "move", **items[0], "summary": f"Moved “{items[0]['title']}” → {where}", "revert": revert})
    n = len(plans)
    return (_batch_text(f'moved {_of(n, batch["asked"])} to "{where}"{new}', plans, batch),
            {"kind": "move", "to": target["path"], "summary": f"Moved {n} page{'s' if n != 1 else ''} → {where}",
             "changes": n, "items": items, "revert": revert})


def _run_list_deleted(conn, ws: str, scope: dict, args: dict):
    """Recently deleted (gamma/trash.py) as far as the chat's folder
    reaches — the pages that were filed under it — the last deleted first,
    with the folders a restore puts each page back in, the page it goes
    back inside (``parent``) and how many pages inside it come back with
    it (``sub_pages``)."""
    query = str(args.get("title_contains") or "").strip().lower()
    reach = _reach(conn, scope)
    pages = [p for p in list_trash(conn) if _filed_in(reach, p["folders"])
             and (not query or query in p["title"].lower())]
    paths = folder_paths(conn)
    path = PATH_SEP.join(paths.get(_scope_folder(scope), []))
    where = f' from "{path}"' if path else ""
    action = {"kind": "list", "summary": f"Listed Recently deleted{where} — "
                                         f"{len(pages)} page{'s' if len(pages) != 1 else ''}"}
    if not pages:
        return (f"Recently deleted holds no pages{where}"
                + (f' with "{query}" in the title' if query else "") + ".", action)
    lines = [f'- id={p["id"]} | "{p["title"]}" | was in {quoted_paths(paths, p["folders"]) or "no folder"} '
             + (f'| goes back inside page {p["parent"]} ' if p["parent"] else "")
             + (f'| {_back_with(p["sub_pages"])} ' if p["sub_pages"] else "")
             + f'| deleted {p["deleted_at"][:10]}' + (f' by {p["deleted_by"]}' if p["deleted_by"] else "")
             + f' | gone for good after {p["purge_at"][:10]}' for p in pages[:_LIST_CAP]]
    more = f"\n(+{len(pages) - _LIST_CAP} more not shown)" if len(pages) > _LIST_CAP else ""
    return (f"Recently deleted{where} ({len(pages)}, the last deleted first; a page is deleted for "
            f"good {KEEP_DAYS} days after it went) — restore_page(page_ids) puts them back in the "
            "folders they were in, a page deleted from inside a page back inside it, and the pages "
            "that went with a page back inside it, which are not listed apart:\n" + "\n".join(lines) + more), action


def _back_with(n: int) -> str:
    """How many pages went to Recently deleted with a page, and come back with it."""
    return "the page inside it comes back with it" if n == 1 else f"the {n} pages inside it come back with it"


def _plan_restore_one(conn, trash: dict, reach, page_id: str, tree: Tree) -> tuple:
    """One page of a restore_page call: ``(entry, None)`` — its Recently
    deleted entry (``trash.list_trash``'s) with ``page_id``, ``before``
    (when it was deleted) and ``after`` (the folders it goes back to) — or
    ``(None, answer)`` for a page it cannot restore: a work folder's file
    comes back with its file (``tree``, the folders as the rule reads
    them)."""
    page = trash.get(page_id)
    if page is None:
        if conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'", (page_id,)).fetchone():
            return None, "ok — that page is not deleted; it is in the library"
        return None, "error: no such page in Recently deleted — use ids from list_deleted"
    if not _filed_in(reach, page["folders"]):
        return None, "error: that page was not filed in this chat's folder — restore it from the library root"
    props = load_props(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()[0])
    refused = _work_refusal(lambda: work_filing.may_restore(tree, props, "person"))
    if refused:
        return None, refused
    return {**page, "page_id": page["id"], "before": page["deleted_at"], "after": page["folders"]}, None


def _plan_restore_page(conn, scope: dict, args: dict):
    """``(batch, None)``: the pages a restore_page call names (``_batch``;
    Recently deleted read once), or ``(None, error)``."""
    ids, error = _page_ids_arg(args, "list_deleted")
    if error:
        return None, error
    trash, reach, tree = {p["id"]: p for p in list_trash(conn)}, _reach(conn, scope), Tree.read(conn)
    return _batch(ids, lambda page_id: _plan_restore_one(conn, trash, reach, page_id, tree)), None


def _preview_restore_page(conn, scope: dict, args: dict):
    batch, error = _plan_restore_page(conn, scope, args)
    answer = error or _batch_answer(scope, batch, "restored")
    if answer:
        return None, answer
    paths, plans = folder_paths(conn), batch["plans"]
    if batch["asked"] == 1:
        return {"title": plans[0]["title"], "to": ", ".join(paths_text(paths, plans[0]["folders"]))}, None
    lines = [[["ctx", p["title"]]] + ([["ctx", " → "], ["ins", ", ".join(paths_text(paths, p["folders"]))]]
                                      if p["folders"] else []) for p in plans]
    return _batch_preview(conn, batch, lines, _digest("restore_page", "", plans)), None


def _run_restore_page(conn, ws: str, scope: dict, args: dict):
    """Bring the pages back from Recently deleted (``ops.restore_page``, one
    each: a restore commits on its own and reloads the page's room): under
    the library root again, in the folders each was in, with its notes,
    highlights, files and chats. A page that fails is reported, the others
    restored — every page that came back is in the result and the action."""
    from ..ops import OpError, restore_page

    batch, error = _plan_restore_page(conn, scope, args)
    answer = error or _batch_answer(scope, batch, "restored") or _changed_since(
        scope, _digest("restore_page", "", batch["plans"]))
    if answer:
        return answer, None
    done = []
    for p in batch["plans"]:
        try:
            restore_page(ws, conn, p["page_id"], actor=scope.get("actor", ""), client="ai")
        except Exception as e:  # one page's failure leaves the others' restores standing
            # A restore commits before it fans out (ops.restore_page): a page
            # back in the library came back, whatever failed after that.
            if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                                (p["page_id"],)).fetchone():
                if not isinstance(e, OpError):
                    log.warning(f"[ai_tools] restore_page {p['page_id']} failed: {e}")
                batch["refused"].append((p["page_id"], f"error: {e.detail if isinstance(e, OpError) else e}"))
                continue
        done.append(p)
    if not done:
        return (batch["refused"][-1][1] if batch["asked"] == 1
                else "error: none of these pages could be restored:\n" + _left_out_lines(batch)), None
    paths = folder_paths(conn)
    items = [{"page_id": p["page_id"], "title": p["title"], "to": ", ".join(paths_text(paths, p["folders"]))}
             for p in done]
    revert = {"restore": [p["page_id"] for p in done]}  # ai_revert._plan_trash: a revert deletes them again
    went = sum(p["sub_pages"] for p in done)
    along = f", with the {went} page{'s' if went != 1 else ''} inside {'it' if len(done) == 1 else 'them'}" \
        if went else ""
    if batch["asked"] == 1:
        p = done[0]
        where = quoted_paths(paths, p["folders"]) or "the library root"
        if p["parent"]:  # (a sub-page deleted on its own: filed where its parent's top is)
            where = f"inside page {p['parent']}, filed where that page is ({where})"
        return (f'ok — restored [{p["title"]}](/?page={p["page_id"]}) (page_id {p["page_id"]}) to {where}{along}',
                {"kind": "restore", **items[0], "summary": f"Restored “{p['title'][:60]}”", "revert": revert})
    n = len(done)
    inside = [p for p in done if p["parent"]]
    back = ("; back inside the pages they were in: " + ", ".join(f"{p['page_id']} inside {p['parent']}"
                                                                   for p in inside[:_PREVIEW_ITEMS])) if inside else ""
    return (_batch_text(f"restored {_of(n, batch['asked'])}{along}{back}", done, batch, links=True),
            {"kind": "restore", "summary": f"Restored {n} page{'s' if n != 1 else ''}", "changes": n,
             "items": items, "revert": revert})


def _plan_trash_one(conn, scope: dict, page_id: str, reach, tree: Tree) -> tuple:
    """One page of a delete_page call: ``({page_id, title, inside, before,
    after}, None)`` — its title and the pages inside it, which go to
    Recently deleted with it (``inside``: ids, ``page_descendants``), both
    held by the digest (``before``) — or ``(None, answer)``: a page in
    Recently deleted already, one outside the chat's reach, no page, a
    work folder's file that leaves with its file (``tree``, the folders as
    the rule reads them)."""
    loaded, error = _load_scoped_page(conn, scope, {"page_id": page_id}, reach)
    if error:
        if error == _NO_PAGE and conn.execute(
                "SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = ?", (page_id, TRASH)).fetchone():
            return None, "ok — that page is in Recently deleted already"
        return None, error
    page_id, title, props, _ = loaded
    refused = _work_refusal(lambda: work_filing.may_trash(tree, props, "person"))
    if refused:
        return None, refused
    inside = page_descendants(conn, page_id)
    return {"page_id": page_id, "title": title, "inside": inside, "before": [title, inside], "after": TRASH}, None


def _plan_delete_page(conn, scope: dict, args: dict):
    """``(batch, None)``: the pages a delete_page call names (``_batch``),
    or ``(None, error)``. A page inside another page of the call goes with
    that one, as the app's Delete of a selection sends the outer page only:
    it counts as already so."""
    ids, error = _page_ids_arg(args)
    if error:
        return None, error
    reach, tree = _reach(conn, scope), Tree.read(conn)
    batch = _batch(ids, lambda page_id: _plan_trash_one(conn, scope, page_id, reach, tree))
    outer = {sub: p["page_id"] for p in batch["plans"] for sub in p["inside"]}
    for p in [p for p in batch["plans"] if p["page_id"] in outer]:
        batch["plans"].remove(p)
        batch["same"].append((p["page_id"], f"ok — it is inside page {outer[p['page_id']]} and goes with it"))
    return batch, None


def _preview_delete_page(conn, scope: dict, args: dict):
    batch, error = _plan_delete_page(conn, scope, args)
    answer = error or _batch_answer(scope, batch, "deleted")
    if answer:
        return None, answer
    plans = batch["plans"]
    inside = sum(len(p["inside"]) for p in plans)
    if batch["asked"] == 1:
        return {"page_id": plans[0]["page_id"], "title": plans[0]["title"], "diff": [["del", plans[0]["title"]]],
                "inside": inside, "days": KEEP_DAYS}, None
    return _batch_preview(conn, batch, [[["del", p["title"]]] for p in plans],
                          _digest("delete_page", "", plans), inside=inside, days=KEEP_DAYS), None


def _run_delete_page(conn, ws: str, scope: dict, args: dict):
    """Move the pages to Recently deleted as the app's Delete does
    (``ops.trash_page``, one each: a trashing commits on its own and tells
    the page's room), each with the pages inside it. A page that fails is
    reported, the others go — every page that went is in the result and in
    the action, whose ``revert`` restores them (ai_revert._plan_trash)."""
    from ..ops import OpError, trash_page

    batch, error = _plan_delete_page(conn, scope, args)
    answer = error or _batch_answer(scope, batch, "deleted") or _changed_since(
        scope, _digest("delete_page", "", batch["plans"]))
    if answer:
        return answer, None
    done = []
    for p in batch["plans"]:
        try:
            trash_page(ws, conn, p["page_id"], actor=scope.get("actor", ""), client="ai")
        except Exception as e:  # one page's failure leaves the others standing
            # A trashing commits before it fans out: a page out of the
            # library went, whatever failed after that.
            if conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                            (p["page_id"],)).fetchone():
                if not isinstance(e, OpError):
                    log.warning(f"[ai_tools] delete_page {p['page_id']} failed: {e}")
                batch["refused"].append((p["page_id"], f"error: {e.detail if isinstance(e, OpError) else e}"))
                continue
        done.append(p)
    if not done:
        return (batch["refused"][-1][1] if batch["asked"] == 1
                else "error: none of these pages could be deleted:\n" + _left_out_lines(batch)), None
    went = sum(len(p["inside"]) for p in done)
    along = f", with the {went} page{'s' if went != 1 else ''} inside" if went else ""
    keep = (f"They stay there for {KEEP_DAYS} days: restore_page brings them back, and the user can revert "
            "this from the reply.")
    revert = {"trash": [p["page_id"] for p in done]}
    items = [{"page_id": p["page_id"], "title": p["title"], "inside": len(p["inside"])} for p in done]
    if batch["asked"] == 1:
        p = done[0]
        return (f'ok — moved "{p["title"]}" (page_id {p["page_id"]}) to Recently deleted{along}. {keep}',
                {"kind": "delete", **items[0], "summary": f"Deleted “{p['title'][:60]}”", "revert": revert})
    n = len(done)
    return (_batch_text(f"moved {_of(n, batch['asked'])} to Recently deleted{along}", done, batch) + "\n" + keep,
            {"kind": "delete", "summary": f"Deleted {n} page{'s' if n != 1 else ''}", "changes": n,
             "inside": went, "items": items, "revert": revert})


TOOLS = [
    {
        "perm": "rename", "kind": "rename", "scopes": ("folder",), "mutating": True, "run": _run_rename_page,
        "preview": _preview_rename_page,
        "spec": {
            "name": "rename_page",
            "description": (
                f"Set the titles of pages. `renames` lists each page with its new title, up to {_RENAME_MAX} "
                "in one call: put a task's renames in as few calls as you can — the user approves a whole "
                "call at once — and send one such call per turn, since a long list takes much of a turn. "
                "A page that cannot be renamed is left out and named in the result with the reason; the "
                "others are renamed. Use exact page ids from list_pages."),
            "parameters": {
                "type": "object",
                "properties": {"renames": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"page_id": {"type": "string"},
                                   "title": {"type": "string", "description": "the new title"}},
                    "required": ["page_id", "title"]}}},
                "required": ["renames"],
            },
        },
    },
    {
        "perm": "move", "kind": "move", "scopes": ("folder",), "mutating": True, "run": _run_move_page,
        "preview": _preview_move_page,
        "spec": {
            "name": "move_page",
            "description": (
                f"File pages into a folder. `page_ids`: the pages, up to {BATCH_MAX} in one call (exact ids "
                "from list_pages); to sort pages into several folders, make one call per folder. `folder` "
                "is the folder's id or its path as list_folders shows it (\"readout / nondestructive\"; "
                "\"readout/nondestructive\" works too where no name holds a '/'); a path that names no "
                "folder makes it. A path is read inside the current folder: one that does not start with "
                "the current folder's path is taken as a subfolder of it, and \"\" files the pages in the "
                "current folder itself (at the library root: out of every folder). Folder memberships "
                "outside the current folder are kept — a page can be in several folders. When several "
                "folders share the path the call fails naming their ids: pass the id. A sub-page (a "
                "`parent=` line of list_pages) goes where the page it is inside goes and is not moved on "
                "its own. A page that cannot be moved is left out and named in the result; the others are "
                "moved."),
            "parameters": {
                "type": "object",
                "properties": {"page_ids": {"type": "array", "items": {"type": "string"}},
                               "folder": {"type": "string"}},
                "required": ["page_ids", "folder"],
            },
        },
    },
    {
        # A permission either reads or changes, so the listing is List pages'.
        "perm": "list", "kind": "list", "scopes": ("folder",), "mutating": False, "run": _run_list_deleted,
        "spec": {
            "name": "list_deleted",
            "description": (
                f"List Recently deleted: the pages deleted in the last {KEEP_DAYS} days that were "
                "filed under the current folder — id, title, the folders they were in, when and by "
                "whom they were deleted. `title_contains` filters by title. Deleted pages are in no "
                "other tool's results; restore_page brings them back."),
            "parameters": {
                "type": "object",
                "properties": {"title_contains": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "perm": "restore", "kind": "restore", "scopes": ("folder",), "mutating": True, "run": _run_restore_page,
        "preview": _preview_restore_page,
        "spec": {
            "name": "restore_page",
            "description": (
                "Bring pages back from Recently deleted — with their notes, highlights, files and chats — "
                f"each filed in the folders it was in. `page_ids`: ids from list_deleted, up to {BATCH_MAX} "
                "in one call."),
            "parameters": {
                "type": "object",
                "properties": {"page_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["page_ids"],
            },
        },
    },
    {
        "perm": "delete", "kind": "delete", "scopes": ("folder",), "mutating": True, "run": _run_delete_page,
        "preview": _preview_delete_page,
        "spec": {
            "name": "delete_page",
            "description": (
                f"Move pages to Recently deleted, as the user's Delete does. `page_ids`: the pages, up to "
                f"{BATCH_MAX} in one call (exact ids from list_pages). The pages inside a page go with it. A "
                "deleted page keeps its notes, highlights, files and chats; it is deleted for good after "
                f"{KEEP_DAYS} days, and until then restore_page brings it back and the user can revert the "
                "call from the reply. A page that cannot be deleted is left out and named in the result; "
                "the others go. Only for pages the user asked to delete."),
            "parameters": {
                "type": "object",
                "properties": {"page_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["page_ids"],
            },
        },
    },
]
