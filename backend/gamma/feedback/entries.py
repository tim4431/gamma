"""The feedback reader (docs/dev/feedback.md "The entry and the document"):
the comment threads on a work folder's files as entries an agent can act
on, each with its file, its place, the source line SyncTeX maps it to (a
range of a text file names its own lines), a picture of its region and its
comments.

A scope (``resolve_scope``) is a work folder W, a directory below it or one
of its files. ``entries`` reads the scope's threads (``comments``' readers:
a file in Recently deleted and an orphaned thread are none) and turns each
into an entry: its ``kind`` and ``where`` from the place the thread was
opened on (its root's ``anchor``, gamma/feedback/anchors.py), read against
the block now; a root from before step 54 holds no anchor and is read from
its block alone. ``document`` wraps a scope's entries with what an agent
needs to answer them; gamma/feedback/markdown.py writes it as Markdown, the
routes (routers/feedback.py), the MCP tools and the files below a vault's
``.gamma/feedback/`` (gamma/feedback/files.py) serve it. Nothing here
writes, and nothing renders: a picture is named by its key
(gamma/feedback/pictures.py), drawn by whoever serves it.
"""

import json
from typing import NamedTuple

from .. import comments, highlights, storage
from ..blocks_store import load_props
from ..documents.model import file_hash, is_image, page_document
from ..highlights import COORDS
from ..work_folders.library import work_folder_of
from ..work_folders.marks import Tree
from ..work_folders.paths import dir_path, page_path, work_folders_named
from ..vault.store import in_place
from . import anchors, pictures

FORMAT = "gamma-feedback-1"
MAX_ENTRIES = 500       # entries one document carries; the rest is counted as ``truncated``
MAX_NOTE = 1000         # characters of a note's text in ``where.note``
STATES = comments.STATES
PLACED = ("passage", "area", "ink")         # the kinds that have a page
REGION = "region"                           # the kind that has a place on a picture
LINED = ("lines",)                          # the kinds that have lines in a text file
ON_THIS_BUILD = ("exact", "rough", "estimate")  # a place on the page's current file

# How to answer, the same in every document (and in a request's file, with
# its own state route): the comment routes and the MCP tools.
ANSWER = {"reply": "POST /api/pages/{page_id}/comments {block_id, thread_id, body}",
          "resolve": "POST /api/comments/{id}/resolve {resolved: true}",
          "request_state": "POST /api/feedback/requests/{request_id}/state {state, detail}",
          "mcp": ["reply_feedback", "resolve_feedback"]}


class FeedbackError(Exception):
    """A refusal of the reader or the requests: the HTTP status and the
    words. The routes answer it as an HTTPException, the tools as text."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class Scope(NamedTuple):
    kind: str            # "work_folder" | "folder" | "file"
    id: str              # W, a folder below W, or a file's page id
    work_folder: str     # W
    name: str            # W's name, the directory's path in W, or the file's path


# --- scopes ------------------------------------------------------------------------

def file_path(tree: Tree, props: dict, paths: dict | None = None, *, page_id: str = "", title: str = "") -> str:
    """A file's path in its work folder: ``paths[page_id]`` when given (the
    vault's rows on this computer, ``paths_of``), else the tree's
    (``work_folders.paths.page_path``)."""
    if paths and page_id in paths:
        return paths[page_id]
    return page_path(tree, props, title=title)


def paths_of(rows: dict) -> dict:
    """``{page id: path}`` from a vault's rows (``vault.store.files``): each
    page at its file's row, else its notes file's; tombstones (``was``, or
    in place) left out. What ``file_path`` reads first on the computer that
    holds the disk."""
    out: dict = {}
    for path, row in sorted(rows.items()):
        if not row.get("page_id") or row.get("was") or in_place(row):
            continue
        if row["page_id"] not in out or (row.get("kind") != "notes" and out[row["page_id"]][1] == "notes"):
            out[row["page_id"]] = (path, row.get("kind"))
    return {page_id: path for page_id, (path, _) in out.items()}


def resolve_scope(conn, scope_id: str, tree: Tree | None = None) -> Scope:
    """What ``scope_id`` names: a work folder's root (``work_folder``), a
    folder below one (``folder``), or a live page of a work folder (``file``).
    FeedbackError 404 for no such folder or live page, 400 for a library
    page or folder."""
    tree = tree or Tree.read(conn)
    if scope_id in tree:
        w = tree.work_of(scope_id)
        if not w:
            raise FeedbackError(400, "Feedback is for a work folder's files.")
        if scope_id == w:
            return Scope("work_folder", w, w, tree.names.get(w, ""))
        return Scope("folder", scope_id, w, dir_path(tree, scope_id, w))
    row = conn.execute("SELECT content, properties FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                       (scope_id,)).fetchone() if scope_id else None
    if not row:
        raise FeedbackError(404, "No folder or page has that id.")
    props = load_props(row[1])
    w = work_folder_of(props)
    if not w:
        raise FeedbackError(400, "Feedback is for a work folder's files.")
    return Scope("file", scope_id, w, file_path(tree, props, page_id=scope_id, title=row[0] or ""))


def work_folder_named(conn, name_or_id: str, tree: Tree | None = None) -> Scope:
    """A work folder by its root folder's id, or by its name
    (``work_folders.paths.work_folders_named``: exactly, else ignoring case;
    exactly one may carry it). FeedbackError 404 for none, 400 for a name
    two carry."""
    tree = tree or Tree.read(conn)
    found = work_folders_named(tree, name_or_id)
    if not found:
        raise FeedbackError(404, f"No work folder is named {name_or_id!r}.")
    if len(found) > 1:
        raise FeedbackError(400, f"{len(found)} work folders are named {name_or_id!r}: name one by its id.")
    return Scope("work_folder", found[0], found[0], tree.names.get(found[0], ""))


# --- which threads -------------------------------------------------------------------

def _check_state(state) -> None:
    if state not in STATES:
        raise FeedbackError(400, "state must be open, resolved or all")


def _pages(conn, page_ids) -> dict:
    """``{page id: (title, properties)}`` of the live pages among ``page_ids``."""
    rows = conn.execute("SELECT id, content, properties FROM unified_blocks WHERE parent_id = 'root' "
                        "AND id IN (SELECT value FROM json_each(?))", (json.dumps(sorted(set(page_ids))),))
    return {r[0]: (r[1] or "", load_props(r[2])) for r in rows}


def _scoped(conn, scope: Scope, state: str, tree: Tree) -> tuple[list[dict], dict]:
    """The scope's threads (``comments.threads_for``' shape) and their
    pages, ``{page id: (title, properties)}``."""
    if scope.kind == "file":
        threads = comments.threads_for(conn, [scope.id], state=state)
    else:
        threads = comments.threads_in_work_folder(conn, scope.work_folder, state=state)
    pages = _pages(conn, {t["page_id"] for t in threads})
    if scope.kind == "folder":
        below = tree.subtree(scope.id)

        def in_scope(props):
            folders = props.get("folders") if isinstance(props.get("folders"), list) else []
            return bool(folders) and folders[0] in below
        pages = {p: v for p, v in pages.items() if in_scope(v[1])}
    pages = {p: v for p, v in pages.items() if work_folder_of(v[1]) == scope.work_folder}
    return [t for t in threads if t["page_id"] in pages], pages


def thread_ids(conn, scope: Scope, *, state: str = "open", tree: Tree | None = None) -> list[str]:
    """The ids of the scope's threads in ``state``, oldest first: what a
    Send names when it names none."""
    _check_state(state)
    return [t["id"] for t in _scoped(conn, scope, state, tree or Tree.read(conn))[0]]


# --- the place ---------------------------------------------------------------------

def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _placed(kind: str, pos, recorded: dict | None, current: str, gone: bool, *, measured: str = "doc_id") -> str:
    """How the place stands on the page's current file (``where.placed``):
    ``pos`` the block's position, ``measured`` its key naming the file it
    was measured on (a PDF position's ``doc_id``, absent when the current
    one; a text range's ``hash``, always written). A text run never writes
    ``approx`` without ``match``, so a range is never ``estimate``."""
    if kind == "ink":
        return "earlier" if recorded and recorded.get("doc_id", "") != current else "exact"
    if gone or not isinstance(pos, dict):
        return "exact" if recorded and recorded.get("doc_id", "") == current else "lost"
    if pos.get("lost"):
        return "lost"
    measured = pos.get(measured) if isinstance(pos.get(measured), str) and pos[measured] else current
    if measured != current:
        return "pending"    # whatever else it carries: an area the run could not place keeps its rectangles there
    if pos.get("approx"):
        return "rough" if _number(pos.get("match")) else "estimate"
    return "exact"


def _region_placed(pos, place: dict, current: str, view) -> str:
    """How a region stands (``where.placed``): ``exact`` when the version it
    was drawn on is the page's current picture, ``earlier`` when another (a
    region is never placed again, and stays where it was), ``lost`` when
    the page's document is no longer a picture."""
    if not is_image(view):
        return "lost"
    drawn = pos["hash"] if isinstance(pos, dict) and isinstance(pos.get("hash"), str) and pos["hash"] else place["doc_id"]
    return "exact" if drawn == current else "earlier"


def _frame(pos) -> tuple[list, list] | None:
    """A region's rectangle and its picture's size in the picture's own
    pixels, whole (``where.rect``, ``where.size``); None without a frame or
    a placed rectangle."""
    if not isinstance(pos, dict):
        return None
    size, bound = highlights._size(pos), pos.get("boundingRect")
    if not size or not highlights.placed(bound):
        return None
    return [round(bound[k]) for k in COORDS], [round(v) for v in size]


def _has_place(kind: str) -> bool:
    return kind in PLACED or kind == REGION or kind in LINED


def _lines_where(pos, source: dict, recorded: dict | None, placed: str, *, kept: bool) -> dict:
    """A range's ``where``: its lines now while it is placed on the page's
    current file (``source`` the live anchor), else the ones it was
    commented on; the quote; and, placed roughly, how alike it read and the
    text the comment was made on (``was``: the quote the thread's anchor
    recorded, when the range reads otherwise now; for a thread without one,
    its ``prev``'s quote, the text last confirmed there). ``kept``: the
    file's text is kept from the model (``anchors.text_kept``), so neither
    quote is given, only the lines."""
    first, last = anchors.lines_of(source if placed in ON_THIS_BUILD else recorded or source) or (None, None)
    where: dict = {}
    if first:
        where["line"] = first
        if last != first:
            where["end"] = last
    quote = "" if kept else source["quote"]
    if quote:
        where["quote"] = quote
    where["placed"] = placed
    if placed == "rough":
        where["match"] = pos["match"]
        prev = highlights.valid_text_prev(pos.get("prev"))
        was = (recorded.get("quote") if recorded else (prev or {}).get("quote")) or ""
        was = was[:highlights.PREV_MAX_QUOTE]
        if was and was != quote and not kept:
            where["was"] = was
    return where


class _Item(NamedTuple):
    thread: dict
    file: str
    kind: str
    where: dict
    place: dict | None     # the place now, an anchor's shape (the order, the source's rects)
    anchor: dict           # the anchor the root keeps, else the one its block gives now
    recorded: bool         # whether the root keeps it
    color: str             # the colour of the block the place is read from
    current: str           # the page's file now
    props: dict            # the page's properties
    view: dict | None      # the page's document (page_document)


def _item(conn, thread: dict, stored: str | None, title: str, props: dict, tree: Tree, paths) -> _Item:
    page_id, block_id = thread["page_id"], thread["block_id"]
    view = page_document(props)
    current = file_hash(view) or ""
    recorded = anchors.parse(stored)
    live = anchors.anchor_of(conn, page_id, recorded["block"] if recorded else block_id)
    kind = (recorded or live)["kind"]
    gone = bool(recorded) and _has_place(kind) and (not _has_place(live["kind"]) or live["block"] != recorded["block"])
    where: dict = {}
    place, color = None, ""
    if _has_place(kind):
        # An area, ink or a region keeps the anchor's place (page, box,
        # rectangles) while the file is the anchor's, as its picture does
        # (pictures.now): a block dragged on the same build does not move the
        # thread. A passage and a range follow their highlight, and every kind
        # on a PDF or a text file the relocation on a new version; a region is
        # never placed again.
        follows = kind == "passage" or kind in LINED
        kept = recorded is not None and (gone or (not follows and live["doc_id"] == recorded["doc_id"]))
        source = recorded if kept else live
        row = None if gone else conn.execute("SELECT properties FROM unified_blocks WHERE id = ?",
                                             (live["block"],)).fetchone()
        block = load_props(row[0]) if row else {}
        color = block.get("color") if isinstance(block.get("color"), str) else ""
        box = source["box"]
        place = {**source, "kind": kind, "box": box}
        if kind == REGION:
            pos = block.get("image_position")
            if box:
                where["box"] = box
            frame = _frame(pos)
            if frame:
                where["rect"], where["size"] = frame
            where["placed"] = _region_placed(pos, source, current, view)
        elif kind in LINED:
            pos = block.get("text_position")
            placed = _placed(kind, pos, recorded, current, gone, measured="hash")
            where.update(_lines_where(pos, source, recorded, placed, kept=anchors.text_kept(view)))
        else:
            pos = block.get("pdf_position")
            placed = _placed(kind, pos, recorded, current, gone)
            if source["page"]:
                where["page"] = source["page"]
            if kind == "passage" and source["quote"]:
                where["quote"] = source["quote"]
            if kind != "passage" and box:
                where["box"] = box
            where["placed"] = placed
            if placed == "rough":
                where["match"] = pos["match"]
    if kind == "note" or (kind != "file" and block_id != (place or {}).get("block", block_id)):
        row = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
        text = (row[0] or "").strip() if row else ""
        if text:
            where["note"] = text[:MAX_NOTE]
    return _Item(thread, file_path(tree, props, paths, page_id=page_id, title=title), kind, where, place,
                 recorded or live, bool(recorded), color, current, props, view)


def _order(item: _Item) -> tuple:
    """Files by name; in a file the whole file's threads first, then by page,
    top and left edge, the threads without a page after: a picture's regions
    by top and left edge, a text file's ranges by line, then the notes; then
    by when."""
    page = item.where.get("page")
    place = item.place or {}
    box = place.get("box")
    left = box[0] if box else min((min(r[0], r[2]) for r in place.get("rects") or ()), default=0.0)
    return (item.file.casefold(), item.file, item.kind != "file", page is None, page or 0, item.kind == "note",
            item.where.get("line") or 0, anchors.top(place) if place else 0.0, left, item.thread["created_at"],
            item.thread["id"])


# --- the entry -----------------------------------------------------------------------

def _source(ws, conn, item: _Item, live_sources: bool) -> dict | None:
    """The source of the place: live when it is on the page's current file
    (and this computer holds its vault and its SyncTeX), else the one
    recorded when the thread was opened. A range of a text file is its own
    source, never SyncTeX's: its lines now while it is placed on the
    current file, else the ones it was commented on (``where``)."""
    place = item.place
    if item.kind in LINED:
        if not item.where.get("line"):
            return None
        out = {"file": item.file, "line": item.where["line"]}
        if item.where.get("end"):
            out["end"] = item.where["end"]
        return {**out, "version": "current" if item.where["placed"] in ON_THIS_BUILD else "commented"}
    if (live_sources and item.kind in PLACED and place and item.where.get("placed") in ON_THIS_BUILD
            and place["doc_id"] == item.current):
        from . import sources  # local: it reaches the vaults (folder_links), loaded where a source is asked for

        found = sources.source_for(ws, conn, item.thread["page_id"], item.props,
                                   {"kind": item.kind, "page": place["page"], "rects": place["rects"]})
        if found:
            return {**_source_fields(found), "version": "current"}
    recorded = item.anchor.get("source") if item.recorded else None
    if isinstance(recorded, dict) and isinstance(recorded.get("file"), str) and recorded["file"]:
        return {**_source_fields(recorded), "version": "commented"}
    return None


def _source_fields(found: dict) -> dict:
    out = {"file": found["file"], "line": found.get("line")}
    if found.get("end") and found["end"] != found.get("line"):
        out["end"] = found["end"]
    return out


def _pictures(ws, conn, item: _Item, regions: dict | None, memo: dict) -> tuple[str | None, str | None]:
    """``(picture, before)``: the region as it is now, while the place is on
    the current file (a region of a picture, which stays where it was on a
    new version, and the whole picture for a thread on its page, always),
    and as it was commented on, while that earlier file is stored; each
    ``img/<key>.jpg`` (gamma/feedback/pictures.py), ``regions`` gaining
    ``{key: (page id, Region)}``. ``memo`` is the pass's, which reads each
    page's marks once."""
    if item.kind in PLACED and item.place:
        on_this_build = item.where.get("placed") in ON_THIS_BUILD
    elif item.kind == REGION or (item.kind == "file" and is_image(item.view)):
        on_this_build = True
    else:
        return None, None
    page_id = item.thread["page_id"]

    def named(region):
        if region is None:
            return None
        key = pictures.key(region)
        if regions is not None:
            regions[key] = (page_id, region)
        return f"img/{key}.jpg"

    picture = named(pictures.now(conn, page_id, item.props, item.anchor, memo=memo)) if on_this_build else None
    before = None
    anchor = item.anchor
    if item.recorded and anchor.get("doc_id") and anchor["doc_id"] != item.current:
        name = anchors.held_file(anchor)
        if name and storage.exists(ws, name):
            before = named(pictures.made(anchor, item.color))
    return picture, before


def _entry(ws, conn, item: _Item, *, link_base: str, live_sources: bool, regions: dict | None, memo: dict) -> dict:
    thread, view = item.thread, item.view
    picture, before = _pictures(ws, conn, item, regions, memo)
    return {"id": thread["id"], "file": item.file, "format": view["format"] if view else "markdown",
           "page_id": thread["page_id"], "block_id": thread["block_id"], "kind": item.kind, "where": item.where,
           "source": _source(ws, conn, item, live_sources), "picture": picture, "before": before,
           "state": "resolved" if thread["resolved_at"] else "open", "sent": _sent(thread.get("sent")),
           "link": f"{link_base}?ws={ws}&page={thread['page_id']}&block={thread['block_id']}&thread={thread['id']}",
           "comments": [_comment(c) for c in thread["comments"]]}


def _comment(c: dict) -> dict:
    out = {"id": c["id"], "by": c["author"], "agent": c["agent"], "at": c["created_at"], "body": c["body"],
           "deleted": c["deleted"]}
    if c.get("agent_kind"):  # only a local agent's comment says so: every other entry stays as it was
        out["agent_kind"] = c["agent_kind"]
    return out


def _sent(sent: dict | None) -> dict | None:
    if not sent:
        return None
    out = {"request_id": sent["request_id"], "state": sent["state"], "at": sent["at"]}
    if sent.get("detail"):
        out["detail"] = sent["detail"]
    if sent.get("state_by"):
        out["state_by"] = sent["state_by"]
    return out


def _entries(ws, conn, scope: Scope, *, state: str, paths, link_base: str, sources: bool, threads=None,
             regions: dict | None = None, limit: int | None = None,
             tree: Tree | None = None) -> tuple[list[dict], int, list[dict]]:
    """The scope's entries in their order, the first ``limit`` of them, with
    how many there were and the threads they were made of."""
    _check_state(state)
    tree = tree or Tree.read(conn)
    found, pages = _scoped(conn, scope, state, tree)
    if threads is not None:
        wanted = set(threads)
        found = [t for t in found if t["id"] in wanted]
    stored = dict(conn.execute("SELECT id, anchor FROM comments WHERE id IN (SELECT value FROM json_each(?))",
                               (json.dumps([t["id"] for t in found]),)).fetchall())
    items = sorted((_item(conn, t, stored.get(t["id"]), *pages[t["page_id"]], tree, paths) for t in found),
                   key=_order)
    kept = items if limit is None else items[:limit]
    memo: dict = {}
    out = [_entry(ws, conn, item, link_base=link_base, live_sources=sources, regions=regions, memo=memo)
           for item in kept]
    return out, len(items), [item.thread for item in kept]


def entries(ws, conn, scope: Scope, *, state: str = "open", paths: dict | None = None, link_base: str = "",
            sources: bool = True, threads=None, regions: dict | None = None) -> list[dict]:
    """The entries of the scope's threads in ``state`` (open, resolved or
    all), in the document's order (files by name; in a file the whole
    file's threads, then by page and place, a picture's regions, the notes
    last). ``paths``:
    ``{page id: path}`` (``paths_of``) for the files' names; ``link_base``
    goes before each thread's link (MCP's absolute base; "" for a link
    that is its query alone); ``sources`` false leaves the live SyncTeX
    lookup out (the recorded source stays); ``threads``: only those ids;
    ``regions`` gains ``{key: (page id, Region)}`` for each picture named,
    for whoever draws them (``pictures.cached``). FeedbackError 400 for
    another state."""
    return _entries(ws, conn, scope, state=state, paths=paths, link_base=link_base, sources=sources,
                    threads=threads, regions=regions)[0]


def document(ws, conn, scope: Scope, *, state: str = "open", paths: dict | None = None, owner: str = "",
             link_base: str = "", sources: bool = True, regions: dict | None = None) -> dict:
    """The scope's feedback as one document (``FORMAT``): its work folder
    and scope, ``owner`` (the username of the account it is written for:
    whose comments are the requests), ``updated_at`` (the newest activity
    among its threads, so an unchanged document reads byte for byte the
    same), how to answer, and at most ``MAX_ENTRIES`` entries, ``truncated``
    saying how many more there were."""
    tree = Tree.read(conn)
    out, total, threads = _entries(ws, conn, scope, state=state, paths=paths, link_base=link_base,
                                   sources=sources, regions=regions, limit=MAX_ENTRIES, tree=tree)
    doc = {"format": FORMAT, "workspace": ws,
           "work_folder": {"id": scope.work_folder, "name": tree.names.get(scope.work_folder, "")},
           "scope": {"kind": scope.kind, "id": scope.id, "name": scope.name}, "owner": owner, "state": state,
           "updated_at": max((t["last_at"] for t in threads), default=None), "count": len(out)}
    if total > len(out):
        doc["truncated"] = total - len(out)
    doc["answer"] = ANSWER
    doc["entries"] = out
    return doc


def entry(ws, conn, thread_id: str, *, paths: dict | None = None, link_base: str = "", sources: bool = True,
          regions: dict | None = None) -> dict | None:
    """One thread's entry, in any state; None when it is hidden (orphaned,
    gone) or not on a live file of a work folder."""
    thread = comments.thread_of(conn, thread_id)
    if thread is None:
        return None
    try:
        scope = resolve_scope(conn, thread["page_id"])
    except FeedbackError:
        return None
    if scope.kind != "file":
        return None
    found = entries(ws, conn, scope, state="all", paths=paths, link_base=link_base, sources=sources,
                    threads=[thread_id], regions=regions)
    return found[0] if found else None


def key_of(picture: str | None) -> str:
    """The key an entry's picture is named by ("" for none)."""
    return picture.rsplit("/", 1)[-1].removesuffix(".jpg") if picture else ""


def picture_url(thread_id: str, at: str, picture: str | None) -> str | None:
    """The route that serves an entry's picture (``at`` now or before),
    named by its key (``v``, which only busts a browser's cache)."""
    return f"/api/feedback/threads/{thread_id}/picture?at={at}&v={key_of(picture)}" if picture else None


def with_urls(entry: dict, base: str = "") -> dict:
    """The entry with ``picture_url`` and ``before_url`` after its pictures:
    what the HTTP API and MCP answer (``base`` before each route)."""
    out = {}
    for key, value in entry.items():
        out[key] = value
        if key == "before":
            for name, at, picture in (("picture_url", "now", entry["picture"]), ("before_url", "before", value)):
                url = picture_url(entry["id"], at, picture)
                out[name] = base + url if url else None
    return out

