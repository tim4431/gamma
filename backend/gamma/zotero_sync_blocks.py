"""An item's notes and annotations in a Zotero link's round (see
docs/dev/zotero_sync.md#notes and docs/dev/zotero_sync.md#annotations):
``Blocks`` rides on the item's unit (gamma/zotero_sync_items.py) and adds to
its decisions.

Each child note is a region of the page, pulled as a block-level diff that
never deletes what the note does not carry (a nested highlight, a
reference, a picture of Gamma's), and the page's other top-level notes go
together as one note "Notes from Gamma" once the page has been still for
``NOTES_QUIET_S``; a note Gamma would lose part of (citations, an
annotation embed, a picture it could not fetch) is never written back. A
standalone note is a note page. The annotations of the item's primary PDF
travel only while both sides hold the same file; a block that is no
annotation Gamma may write now (being placed in a new PDF, dashed, lost,
still drawn in the file: ``zotero_annotations.hold_reason``) is held,
never trashed for; a drawing's items own their strokes; another group
member's annotation is read only (a delete here is put back, and it is
never made anew under this key). An annotation's page label is sent with
its create and when its page changes, and once more, alone, for a row not
settled under the label reading (``detail.labels``) whose annotation Gamma
made under its key and Zotero still holds the label that create carried,
read otherwise now (``_label_due``).

Also here: whether a page's notes and annotations differ from what last
synced (``blocks_dirty``), the pages to look at once for their page labels
(``label_checks``), those Zotero removed for good (``children_gone``) and
the resolvers of the note, lossy-note and annotation conflicts. Of the
engine's parts it imports only gamma/zotero_sync_rows.py.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from fractional_indexing import generate_n_keys_between

from . import storage, sync_clock, text_box, zotero_account, zotero_annotations, zotero_local, zotero_merge
from . import ink as inkmod
from . import zotero_remote as zr
from .blocks_store import new_block_id, write_lock
from .db import connect_pages_db, parse_stamp
from .logbuf import log
from .ops import OpError
from .zotero_account import AccountError
from .zotero_import import html_note_text
from .zotero_notes import note_tree, title_text
from .zotero_sync_rows import (ACTOR, CLIENT, ENGINE, MOVED_ON, Round, drop_rows, get_link, json_dict, new_row,
                               page_note, read_rows, without, write_rows)
from .zotero_web import ZoteroError, item_uri, object_key, random_key

NOTES_QUIET_S = 60          # the "Notes from Gamma" note goes once its page has been this still
NOTES_WAIT = "notes wait for the page to be still"  # the reason of a page's queue entry while they do
NOTE_MAX = 500_000          # the longest note Zotero keeps, in characters
CONFLICT_TEXT_MAX = 2000    # the characters of a note a conflict shows
NOTE_ALIKE = 0.8            # how alike (word by word) a marked note block reads to pair a first link's note
NOTE_TOO_LONG = f"Zotero keeps notes of at most {NOTE_MAX:,} characters"
GONE_READS = 3              # reads a removal for good is decided on while edits keep changing its page
_WORD_RE = re.compile(r"\w+")
# Why an annotation waits for its file (``detail.reason`` of its row, ``status.waiting.annotations.reasons``;
# a block's own reasons are ``zotero_annotations.hold_reason``'s)
WAIT_FILE = "the PDF is not here yet"
WAIT_DIFFER = "the PDFs differ"


def note_hash(html) -> str:
    """What a note's two bases keep of it: a hash of its HTML (Zotero's as
    fetched, or the region's as Gamma would send it)."""
    return hashlib.sha256((html or "").encode("utf-8")).hexdigest()[:32]


def note_words(html) -> str:
    """A note's words alone (marks, bullets and paragraph shapes aside), as a
    first link compares a note with a region."""
    return " ".join(_WORD_RE.findall(html_note_text(html or "").casefold()))


def note_text(html) -> str:
    """A note's text as a conflict shows it (its first ``CONFLICT_TEXT_MAX`` characters)."""
    return " ".join(html_note_text(html or "").split())[:CONFLICT_TEXT_MAX] or "(empty)"


def _is_gamma_notes(tree) -> bool:
    """Whether a pulled note is Gamma's own "Notes from Gamma" (its first line
    the heading it is sent with, ``zotero_merge.NOTES_HEADING``): it stands
    for the page's Gamma-notes region."""
    return bool(tree) and title_text(tree.get("content") or "") == title_text(zotero_merge.NOTES_HEADING)


def _author(r: Round, obj: dict) -> str:
    """The name of the other group member an annotation is by, "" when it is
    the key's own or the library no group's: Zotero lets only its maker
    change it, so Gamma never writes it."""
    if not r.library.startswith("groups/"):
        return ""
    who = (obj.get("meta") or {}).get("createdByUser") or {}
    if not who or str(who.get("id")) == str(r.access.get("user_id")):
        return ""
    return str(who.get("name") or who.get("username") or "another member")


def _made_here(r: Round, row) -> bool:
    """Whether the annotation of ``row`` (a mapping of its ``key``,
    ``parent``, ``block_id`` and ``slot``) is one Gamma made: its key is the
    one a create derives from its attachment, block and slot
    (``Blocks._create_annotations``). Zotero's annotations, those brought
    here and those a first link paired, have keys of Zotero's."""
    return row["key"] == object_key(r.ws, r.library, "annotation", row["parent"], row["block_id"], row["slot"])


def _locked(block: dict) -> bool:
    """Whether an annotation block is another group member's (``_bring``
    marks it so): Zotero lets only its maker change it, so Gamma never
    sends it, not even as a new annotation."""
    return (block.get("properties") or {}).get("zotero_readonly") == "locked"


def _page_index(data: dict) -> int:
    """The 0-based page an annotation item's position names (-1 for none)."""
    try:
        position = json.loads(data.get("annotationPosition") or "{}")
        return int(position["pageIndex"]) if isinstance(position, dict) else -1
    except (KeyError, TypeError, ValueError):
        return -1


def held_blocks(view) -> set:
    """The annotation blocks of a page as read (``zotero_local.read_page``)
    that Gamma may not write now (``zotero_annotations.hold_reason``)."""
    return {a.block["id"] for a in (view.annotations if view else ()) if zotero_annotations.hold_reason(a.block)}


def _keep_represented(row: dict, region) -> bool:
    """A Zotero-note row's ``detail.rep`` as its region now reads: the ids
    of the blocks it represents, root first, so a region whose root went
    while some of them stay is told from one deleted whole. Whether it
    changed."""
    if region is None or row["slot"] == "page":
        return False
    ids = [bid for bid, _, _ in region.blocks]
    if row["detail"].get("rep") == ids:
        return False
    row["detail"] = {**row["detail"], "rep": ids}
    return True


def _with_ids(node: dict, props: dict | None = None) -> dict:
    """A pulled tree (``{content, children}``) as blocks to insert: each node
    its own new id, ``props`` on the root."""
    return {"id": new_block_id(), "content": node.get("content") or "", "properties": dict(props or {}),
            "children": [_with_ids(c) for c in node.get("children") or ()]}


def _tree_inserts(conn, parent: str, nodes) -> list[dict]:
    """The inserts of block trees (``_with_ids``'), the top ones last under
    ``parent`` in order, each subtree under its root."""
    if not nodes:
        return []
    last = conn.execute("SELECT MAX(position) FROM unified_blocks WHERE parent_id = ?", (parent,)).fetchone()[0]
    out = []

    def add(under, node, position):
        out.append({"op": "insert", "id": node["id"], "parent": under, "position": position,
                    "content": node.get("content") or "", "props": node.get("properties") or {}})
        kids = node.get("children") or []
        for child, pos in zip(kids, generate_n_keys_between(None, None, len(kids)) if kids else ()):
            add(node["id"], child, pos)

    for node, position in zip(nodes, generate_n_keys_between(last, None, len(nodes))):
        add(parent, node, position)
    return out


def _empty_region(page_id: str):
    """The Gamma-notes region of a page that has none yet: the page alone,
    for a pulled "Notes from Gamma" to fill."""
    return zotero_merge.represented({"id": page_id, "parent_id": "root", "page_id": page_id, "kind": "page",
                                     "content": zotero_merge.NOTES_HEADING, "properties": {}, "children": []})


def _image_names(conn, page_id: str) -> dict[str, str]:
    """``{upload name: picture key}`` of the note pictures a page holds from
    Zotero (its image rows): sent back as Zotero's ``data-attachment-key``."""
    return {name: key for key, name in conn.execute(
        "SELECT key, json_extract(detail, '$.name') FROM zotero_objects WHERE kind = 'image' AND page_id = ?",
        (page_id,)).fetchall() if name}


def note_page_html(conn, page_id: str) -> str:
    """A note page's note as Gamma would send it (its region's HTML): what a
    standalone note's ``note`` field is compared with."""
    view = zotero_local.read_page(conn, page_id, note_roots=[page_id], image_key=_image_names(conn, page_id).get,
                                  in_trash=True)
    region = view.regions.get(page_id) if view else None
    return region.html if region else ""


def note_page_parts(tree: dict) -> tuple[str, list]:
    """A standalone note as its page: the title (its first line as text)
    and the blocks, the rest of the note (zotero_import's rule)."""
    from .zotero_import import _note_page  # local: the import's rule, kept in one place
    return _note_page(tree)


# --- an item's notes and annotations ------------------------------------------------------

class Blocks:
    """The notes and annotations of one item's unit: the page's note regions
    against the item's child notes (a Zotero note's subtree each, and the
    page's other top-level notes together as one note "Notes from Gamma"), and
    its annotation blocks against the annotations of the item's primary PDF. It
    adds to the unit's decisions (ops on the page, rows, Zotero writes,
    conflicts, log lines) and takes the bases of what it applied after the
    unit's ops (``rows_tx``), in the unit's transaction.

    A note row's ``zbase`` / ``gbase`` are ``{"note": hash}`` of Zotero's
    HTML and of the region's (``note_hash``); an annotation row's are the
    item's projection (``zotero_annotations.projection``) and keep the ids of
    the strokes it owns (``detail.stroke_ids``, ink). A row whose block is on
    its page but is no annotation Gamma may write now is held (state
    ``held``, its reason); one Zotero holds and Gamma cannot show yet waits
    (``waiting``); neither is ever trashed for it."""

    def __init__(self, u):  # the item's unit (gamma/zotero_sync_items.py's _Unit)
        self.u, self.r = u, u.r
        self.pv = None                 # the page as the unit read it (zotero_local.PageView)
        self.names: dict = {}          # {upload name: picture key} of the page's note pictures
        self.pages = None              # the page's PDF read (zotero_annotations.Pages), when needed
        self.plans: list = []          # ops builders, conn -> ops, run under the unit's lock in order
        self.ops: list = []            # plain ops on the page
        self.new_blocks: list = []     # annotation blocks from Zotero, after the page's last annotation block
        self.new_regions: list = []    # note regions from Zotero (``_with_ids`` trees), at the page's end
        self.decided: dict = {}        # row key -> what its bases are taken from after the ops
        self.ink: dict = {}            # block id -> its ink file as the unit's changes leave it
        self.changes: dict = {}        # block id -> the annotation changes applied to it, merged
        self.quiet_at = None           # the page's notes wait till then to go as "Notes from Gamma"
        self.bound: set = set()        # region roots bound to a Zotero note this unit (a first link)

    # reading

    def read(self, conn) -> None:
        """The page as its blocks are compared: the note rows' roots as its
        Zotero-note regions, its pictures from Zotero by their image rows."""
        u = self.u
        self.names = _image_names(conn, u.page_id)
        roots = [row["block_id"] for row in u.rows.values() if row["kind"] == "note" and row["page_id"] == u.page_id
                 and row["slot"] != "page" and row["block_id"]]
        self.pv = zotero_local.read_page(conn, u.page_id, note_roots=roots, image_key=self.names.get,
                                         notes=bool(self.r.access.get("notes")), in_trash=u.note)

    def page(self, n):
        """Page ``n`` (1-based) of the page's stored PDF, None without one."""
        pages = self._pdf()
        return pages.page(n) if pages and isinstance(n, int) else None

    def _pdf(self):
        """The page's stored PDF (``zotero_annotations.Pages``), opened on
        first use; None without one."""
        if self.pages is None:
            side = self.u.side
            path = storage.open_path(self.r.ws, f"{side.doc_id}.pdf") if side and side.doc_id else None
            self.pages = zotero_annotations.Pages(str(path)) if path else False
        return self.pages or None

    def close(self) -> None:
        if self.pages:
            self.pages.close()
        self.pages = None

    def _label(self, n) -> str:
        pages = self._pdf()
        return (pages.label(n) if pages else "") or (str(n) if n else "")

    def _label_due(self, row: dict, data: dict, author: str = "") -> str | None:
        """The page label to send Zotero for the annotation ``data`` of
        ``row``, once per row (see docs/dev/zotero_sync.md#annotations): the
        label Zotero's reader gives its page when the annotation is one
        Gamma made (``_made_here``) and Zotero still holds the label that
        create carried, which reads otherwise
        (``zotero_annotations.label_correction``); "" when nothing goes: the
        row is settled (``detail.labels``), another member's (``author``),
        Zotero's own, or its label is right or one Zotero gave it; None
        while the PDF can't be read."""
        if author or row["detail"].get("labels") == zotero_annotations.LABELS or not _made_here(self.r, row):
            return ""
        pages, n = self._pdf(), _page_index(data) + 1
        ours = pages.label(n) if pages else ""
        if not ours:
            return None
        return zotero_annotations.label_correction(str(data.get("annotationPageLabel") or ""), ours,
                                                   pages.mupdf_label(n))

    # deciding

    def decide(self) -> None:
        """The unit's notes and annotations, its page live and kept."""
        if self.pv is None:
            return
        if not self.u.standalone and not self.u.note:
            self.notes()
        self.annotations()
        for bid, change in self.changes.items():  # each block's pulled changes as one op, its ink stored now
            op = zotero_local.annotation_update(self.r.ws, bid, change)
            if op:
                self.ops.insert(0, op)
        if self.ops or self.plans or self.new_blocks or self.new_regions:
            self.u.blocks_ops = True

    def new_page(self) -> list:
        """The block tree of a page Zotero's item makes (``_make_page``): its
        child notes, each a region, a "Notes from Gamma" as the page's own
        notes; its annotations wait for the PDF."""
        u, r = self.u, self.r
        nodes, own = [], False
        if r.access.get("notes") and not u.standalone:
            for key, obj in self._zotero_notes().items():
                data = obj.get("data") or {}
                if data.get("deleted"):
                    continue
                html = str(data.get("note") or "")
                pics = self._pictures(key, html)
                lossy = zotero_local.note_lossy(html, pics)
                tree = zotero_local.pulled_tree(pics)
                version = int(obj.get("version") or 0)
                if tree is None:
                    u._put(new_row(key, "note", page_id=u.page_id, parent=u.key, zotero_type="note",
                                   version=version, state="skipped", zbase={"note": note_hash(html)}))
                    continue
                if _is_gamma_notes(tree) and not own:  # a "Notes from Gamma": the page's own notes
                    own = True
                    nodes += [_with_ids(c) for c in tree.get("children") or ()]
                    row = new_row(key, "note", page_id=u.page_id, block_id=u.page_id, slot="page", parent=u.key,
                                  zotero_type="note", version=version, zbase={"note": note_hash(html)})
                else:
                    root = _with_ids(tree, {"zotero_note": item_uri(r.library, key),
                                            **({"zotero_readonly": "lossy"} if lossy else {})})
                    nodes.append(root)
                    row = new_row(key, "note", page_id=u.page_id, block_id=root["id"], parent=u.key,
                                  zotero_type="note", version=version, zbase={"note": note_hash(html)},
                                  detail={"lossy": True} if lossy else {})
                u._put(row)
                self.decided[key] = {"new": True}
                u._count("notes_pulled")
        for key, obj in self._zotero_annotations(u._primary()).items():  # they wait for the PDF
            data = obj.get("data") or {}
            if not data.get("deleted"):
                u._put(new_row(key, "annotation", page_id=u.page_id, parent=data.get("parentItem") or "",
                               zotero_type=str(data.get("annotationType") or ""), version=int(obj.get("version") or 0),
                               state="waiting", detail={"reason": WAIT_FILE}))
        return nodes

    def note_page(self, pull: bool) -> list:
        """A standalone note's page (its row the unit's): the tree a new one
        is made with, or, with ``pull``, Zotero's note diffed into the page
        (``_merge`` decided Zotero's note wins); its pictures fetched first."""
        u = self.u
        html = str(u.data.get("note") or "")
        pics = self._pictures(u.key, html)
        lossy = zotero_local.note_lossy(html, pics)
        if u.change == "create":
            tree = zotero_local.pulled_tree(pics) or {"content": "", "children": []}
            _, blocks = note_page_parts(tree)
            return [_with_ids(b) for b in blocks]
        region = self.pv.regions.get(u.page_id) if self.pv else None
        if region is not None:
            if pull:
                self._pull_region(u.key, region, pics, title=True)
            self._mark(region.root, lossy)
        return []

    # notes

    def _zotero_notes(self) -> dict:
        return {k: c for k, c in self.u.kids.items() if (c.get("data") or {}).get("itemType") == "note"
                and (c.get("data") or {}).get("parentItem") == self.u.key}

    def _pictures(self, note_key: str, html: str):
        """The note's pictures as uploads Gamma holds (``zotero_local.resolve_pictures``):
        each ``embedded_image`` attachment by its image row, else downloaded
        and given one; one Zotero cannot hand out stays Zotero's (the note is
        then lossy)."""
        u, r = self.u, self.r
        if "data-attachment-key" not in html:
            return zotero_local.resolve_pictures(html, lambda key: None)

        def fetch(key):
            row = u.out.get(key) or u.rows.get(key)
            name = (row or {}).get("detail", {}).get("name") if row and row["kind"] == "image" else ""
            if name and storage.open_path(r.ws, name):
                return name
            att = u.kids.get(key)
            if att is None:
                return None
            data = att.get("data") or {}
            try:
                name = zr.download_image(r.ws, r.client, r.library, key, md5=str(data.get("md5") or ""))
            except zr.FileUnavailable:
                return None
            u._put(new_row(key, "image", page_id=u.page_id, parent=note_key, zotero_type="attachment",
                           version=int(att.get("version") or 0), detail={"name": name}))
            self.names[name] = key
            return name

        return zotero_local.resolve_pictures(html, fetch)

    def notes(self) -> None:
        """The item's child notes against the page's note regions."""
        u, r = self.u, self.r
        if not r.access.get("notes"):
            return  # a key without notes access sees none: nothing travels either way
        znotes = self._zotero_notes()
        rows = {k: row for k, row in u.rows.items() if row["kind"] == "note" and k not in u.dropped
                and (k in znotes or (row["page_id"] == u.page_id and row["parent"] == u.key))}
        page_row = next((row for row in rows.values() if row["slot"] == "page"), None)
        if self._reroot(rows):
            u.queue.append(zr.page_entry(u.page_id))  # decided from the page read afresh, next unit
            return
        for key, obj in znotes.items():
            row = rows.get(key)
            data = obj.get("data") or {}
            if row is None and not data.get("deleted"):
                row = self._match_note(key, obj, page_row)
                if row is not None and row["slot"] == "page":
                    page_row = row
            self._note(key, obj, row)
        for key, row in rows.items():
            if key in znotes:
                continue
            if row["state"] == "pending" and int(row["version"]) <= 0:
                self._resend_note(row)
            elif row["state"] == "rejected" and int(row["version"]) <= 0:
                self._resend_note(row, refused=True)
        page_row = any(x["kind"] == "note" and x["slot"] == "page" and x["page_id"] == u.page_id
                       and x["key"] not in u.dropped for x in [*u.rows.values(), *u.out.values()])
        if not page_row and r.pushes and self.pv.gamma_notes is not None:
            if self.bound:  # the page's own notes as read still hold the regions this first link just bound:
                u.queue.append(zr.page_entry(u.page_id))  # they go from the page read afresh, next unit
            else:
                self._create_gamma_notes()

    def _reroot(self, rows: dict) -> bool:
        """Zotero-note regions whose root went from the page while blocks
        they represent stay (the note's first line deleted here, the lines
        below lifted into its place): each is rooted again on the first of
        them in page order, marked as the note's, so what is left reads as
        Gamma's edit of the note (sent, or a ``zotero_lossy`` row for a
        lossy one), never as the note deleted (a note is deleted only when
        every represented block is gone). Whether any was."""
        u, r, pv = self.u, self.r, self.pv
        roots = {x["block_id"] for x in [*u.rows.values(), *u.out.values()]
                 if x["kind"] == "note" and x["key"] not in u.dropped} | self.bound
        owned = set(roots)
        for region in pv.regions.values():
            owned.update(bid for bid, _, _ in region.blocks)
            owned.update(region.kept)
        moved = False
        for row in rows.values():
            if row["slot"] == "page" or row["state"] != "live" or not row["block_id"] or row["block_id"] in pv.nodes:
                continue
            rep = set(row["detail"].get("rep") or ())
            left = [bid for bid in pv.nodes if bid in rep and bid not in owned and pv.nodes[bid].get("kind") == "note"]
            if not left:
                continue
            lossy = bool(row["detail"].get("lossy"))
            self.ops.append(zotero_local.props_op(left[0], {"zotero_note": item_uri(r.library, row["key"]),
                                                            **({"zotero_readonly": "lossy"} if lossy else {})}))
            u._put({**row, "block_id": left[0]})
            owned.add(left[0])
            self.bound.add(left[0])
            moved = True
        return moved

    def _match_note(self, key: str, obj: dict, page_row) -> dict | None:
        """A first link's region for a Zotero note no row names, and a link's
        whose key was just granted notes access (``zotero_state.first``
        "notes": the notes it could not see come as a first link's do): a block
        marked with its key, else a top-level note block whose text is the
        note's, else the marked one (an RDF import's, ``#item_<n>``) whose
        words read most like it, ``NOTE_ALIKE`` at least (Zotero edited it
        since the import); a "Notes from Gamma" the page's own notes. None for
        none (the note comes as a new region)."""
        u, r = self.u, self.r
        html = str((obj.get("data") or {}).get("note") or "")
        version = int(obj.get("version") or 0)
        if page_row is None and _is_gamma_notes(note_tree(html)):
            return new_row(key, "note", page_id=u.page_id, block_id=u.page_id, slot="page", parent=u.key,
                           zotero_type="note", version=version, detail={"first": True})
        if not (self.u.first_link or r.state.get("first") == "notes"):
            return None
        uri, words = item_uri(r.library, key), note_words(html)
        taken = {row["block_id"] for row in u.rows.values() if row["kind"] == "note"} | self.bound
        found, best = None, 0.0
        for node in self.pv.page["children"]:
            if node["id"] in taken or node.get("kind") != "note":
                continue
            mark = (node.get("properties") or {}).get("zotero_note")
            if mark in (uri, key):
                found = node
                break
            if not words:
                continue
            theirs = note_words(zotero_merge.represented(node).html)
            if theirs == words:
                alike = 2.0  # the same words beat any likeness
            elif isinstance(mark, str) and mark:  # never a likeness for a block that claims no note
                alike = SequenceMatcher(None, theirs.split(), words.split(), autojunk=False).ratio()
            else:
                continue
            if alike >= NOTE_ALIKE and alike > best:
                found, best = node, alike
        if found is None:
            return None
        self.bound.add(found["id"])
        regions = dict(self.pv.regions)
        regions[found["id"]] = zotero_merge.represented(found, resolve_ref=None)
        self.pv = self.pv._replace(regions=regions)
        return new_row(key, "note", page_id=u.page_id, block_id=found["id"], parent=u.key, zotero_type="note",
                       version=0, detail={"first": True})

    def _region_of(self, row: dict):
        if row["slot"] == "page":
            return self.pv.gamma_notes
        return self.pv.regions.get(row["block_id"])

    def _quiet(self) -> bool:
        """Whether the page has been still for ``NOTES_QUIET_S`` (its root's
        stamp moves with every write on it); else it is looked at again then."""
        at = parse_stamp((self.pv.page or {}).get("updated_at") or "")
        if at is None:
            return True
        due = zr.when(at) + timedelta(seconds=NOTES_QUIET_S + 1)
        if datetime.now(timezone.utc) >= due:
            return True
        self.quiet_at = due
        return False

    def _note(self, key: str, obj: dict, row: dict | None) -> None:
        """One Zotero child note against its region: pulled as a block-level
        diff, pushed (the Gamma-notes region once the page is still), both
        changed (Gamma's wins unless the note is lossy), deleted on one side
        (an edit beats a delete), its readonly mark kept up."""
        u, r = self.u, self.r
        data = obj.get("data") or {}
        html = str(data.get("note") or "")
        version = int(obj.get("version") or 0)
        zh = note_hash(html)
        if row is None:
            if not data.get("deleted"):
                self._new_region(key, obj, None)
            return
        row = dict(row)
        if row["state"] == "pending":  # our create, which Zotero now holds
            sent = row["detail"].get("sent") or {}
            row.update(state="live", zbase=sent, gbase=sent, detail=without(row["detail"], "sent"))
        if row["state"] == "skipped" and version == int(row["version"]):
            return
        region = self._region_of(row)
        first = bool(row["detail"].get("first")) or (self.u.first_link and int(row["version"]) <= 0)
        restore = bool(row["detail"].get("restore"))  # a workspace backup restored since
        moved = _keep_represented(row, region)
        if (region is not None and row["state"] == "live" and not first and not data.get("deleted")
                and not row["detail"].get("force") and zh == (row["zbase"] or {}).get("note")
                and note_hash(region.html) == (row["gbase"] or {}).get("note")):
            if version != int(row["version"]) or restore or moved:  # neither side changed it: its version alone
                row.update(version=version, detail=without(row["detail"], "restore"))
                u._put(row)
            return
        named = len(self.names)
        pics = self._pictures(key, html)
        lossy = zotero_local.note_lossy(html, pics)
        if region is None or row["state"] in ("waiting", "skipped"):
            return self._note_gone(key, obj, row, lossy, first)
        if first and len(self.names) != named:
            # a first link's pictures keyed just now (their image rows new): the region is compared as it
            # reads with them, by the next unit, never sent without them
            u._put(row)
            u.queue.append(zr.page_entry(u.page_id))
            return
        G_html = region.html
        gh = note_hash(G_html)
        if data.get("deleted"):
            return self._note_trashed(key, obj, row, region, pics, lossy, first=first, restore=restore)
        zb, gb = (row["zbase"] or {}).get("note"), (row["gbase"] or {}).get("note")
        if first:
            agree = note_words(html) == note_words(G_html)
            zb, gb = (zh, gh) if agree else (None, None)
        elif restore and zh != zb:  # Zotero changed it since the backup: the restored region wins it,
            if zotero_merge.note_edits(region, pics.html):  # unless Zotero's reads as the region already holds it
                gb = None
            else:
                zb, gb = zh, gh
        z_changed, g_changed = zh != zb, gh != gb
        adopt = row["detail"].get("adopt") or r.adopt or "theirs"
        forced = bool(row["detail"].get("force"))
        row.update(version=version, state="live", zotero_type="note",
                   detail={**without(row["detail"], "reason", "restore"), "lossy": lossy})
        push = pull = False
        if z_changed and g_changed:
            if first:
                pull = adopt == "theirs" or (lossy and not forced)
                push = not pull
                self._note_conflict("zotero_note", row, G_html, html, decided="zotero" if pull else "gamma")
                u.r.decided["notes"] = u.r.decided.get("notes", 0) + 1
            elif lossy and not forced:
                self._note_conflict("zotero_lossy", row, G_html, html)
            else:
                push = True
                self._note_conflict("zotero_note", row, G_html, html)
        elif z_changed:
            pull = True
        elif g_changed:
            if lossy and not forced:
                self._note_conflict("zotero_lossy", row, G_html, html)
            else:
                push = True
        if pull:
            self._pull_region(key, region, pics)
            row["zbase"] = {"note": zh}
            self.decided[key] = {"applied": True}
        elif push and row["slot"] == "page" and not self._quiet():
            push = False
        if push and not r.pushes:
            push = False
        if push and len(G_html) > NOTE_MAX:
            push = False
            self._refused(row, NOTE_TOO_LONG)
        if push and row["detail"].get("rejected") == gh:
            push = False  # Zotero refused this very note: sent again once it changes
        if push:
            row["zbase"] = {"note": zh}
            if z_changed:  # both changed: Gamma's goes again, even set back, should this write not land
                row["gbase"] = {"note": None}
            u.writes.update("notes", key, version, {"note": G_html})
            u.sent[key] = {"what": "note_update", "html": G_html, "gh": gh, "sig": gh}
        else:  # decided now: a first link's pair is settled
            row["detail"] = without(row["detail"], "first", "adopt")
            if not pull:
                row["zbase"] = {"note": zh}
                if not g_changed:
                    row["gbase"] = {"note": gh}
                elif z_changed:  # both changed and Gamma's did not go (receive only, lossy): it goes, whatever
                    row["gbase"] = {"note": None}  # the region holds then
        if row["slot"] != "page":
            self._mark(row["block_id"], lossy)
        u._put(row)

    def _note_conflict(self, kind: str, row: dict, mine_html: str, theirs_html: str, *, decided: str = "") -> None:
        """A note conflict: Zotero's note in its base, with Zotero's side as
        the row holds it once the unit is done (``zbase``: Zotero's note as
        decided now, refreshed by ``_synced_side`` once a write lands);
        ``decided``: the side a first link's or a force's pair took
        ("gamma", "zotero"; the row is written resolved), "" for an open
        one."""
        block = self.u.page_id if row["slot"] == "page" else row["block_id"]
        self.u.conflicts.append((kind, block, note_text(mine_html), note_text(theirs_html),
                                 json.dumps({"key": row["key"], "note": theirs_html,
                                             "zbase": {"note": note_hash(theirs_html)}}, ensure_ascii=False),
                                 decided))

    def _synced_side(self, row: dict) -> None:
        """Zotero's side as ``row`` now holds it (its bases taken after the
        ops, its write landed) into the base of the unit's note and
        annotation conflicts of it: "theirs" is refused once Zotero moves on
        from it (``resolve_note``, ``resolve_annotation``)."""
        for i, (kind, *rest) in enumerate(self.u.conflicts):
            if kind not in ("zotero_note", "zotero_annotation"):
                continue
            base = json_dict(rest[3])
            if base.get("key") == row["key"]:
                self.u.conflicts[i] = (kind, *rest[:3], json.dumps({**base, "zbase": row["zbase"] or {}},
                                                                   ensure_ascii=False), rest[4])

    def _refused(self, row: dict, message: str) -> None:
        row["detail"] = {**row["detail"], "rejected": note_hash(message), "reason": message}
        self.u.conflicts.append(("zotero_rejected", message,
                                 self.u.page_id if row["slot"] == "page" else row["block_id"]))

    def _mark(self, block_id: str, lossy: bool) -> None:
        """A region root's ``zotero_readonly`` "lossy" as Zotero's note now
        reads: Gamma never writes such a note back."""
        node = self.pv.nodes.get(block_id) if self.pv else None
        if node is None:
            return
        has = (node.get("properties") or {}).get("zotero_readonly") == "lossy"
        if has != lossy:
            self.ops.append(zotero_local.readonly_op(block_id, "lossy" if lossy else None))

    def _pull_region(self, key: str, region, pics, *, title: bool = False) -> None:
        """Zotero's note into its region as a block-level diff, never a replace
        (``zotero_merge.note_edits``): unchanged blocks keep their ids, kept
        blocks are never set or deleted, a block whose text Gamma may not set
        becomes a ``zotero_lossy`` row; a note page's title follows the
        note's first line."""
        u = self.u
        edits = zotero_merge.note_edits(region, pics.html)
        for edit in edits:
            if edit["op"] == "lossy":
                node = self.pv.nodes.get(edit["id"]) or {}
                u.conflicts.append(("zotero_lossy", edit["id"], node.get("content") or "",
                                    zotero_local.picture_markdown(edit.get("content") or "", pics),
                                    json.dumps({"key": key, "block": edit["id"],
                                                "content": zotero_local.picture_markdown(edit.get("content") or "",
                                                                                         pics)},
                                               ensure_ascii=False), False))
        if any(e["op"] != "lossy" for e in edits):
            self.plans.append(lambda conn, region=region, edits=edits, pics=pics: zotero_local.note_ops(
                conn, region, edits, pictures=pics).ops)
        if title:
            tree = note_tree(pics.html)
            name = title_text(zotero_local.picture_markdown(((tree or {}).get("content") or "").split("\n")[0],
                                                            pics)) if tree else ""
            if name and name != (self.pv.title or ""):
                self.ops.append({"op": "set", "id": u.page_id, "content": name})
        u._count("notes_pulled")
        if "pulled" not in u.notes:
            u.notes.append("pulled")

    def _new_region(self, key: str, obj: dict, row: dict | None, *, restored: bool = False) -> None:
        """A Zotero note as a new region at the page's end (a "Notes from
        Gamma" into the page's own notes), its pictures fetched first."""
        u, r = self.u, self.r
        data = obj.get("data") or {}
        html = str(data.get("note") or "")
        version = int(obj.get("version") or 0)
        pics = self._pictures(key, html)
        lossy = zotero_local.note_lossy(html, pics)
        tree = zotero_local.pulled_tree(pics)
        row = dict(row) if row else new_row(key, "note", parent=u.key, zotero_type="note")
        row.update(page_id=u.page_id, parent=u.key, version=version, zbase={"note": note_hash(html)}, gbase={},
                   detail={**without(row["detail"], "first", "adopt", "restore", "reason", "sent", "rejected"),
                           "lossy": lossy})
        if tree is None:
            row.update(state="skipped")
            u._put(row)
            return
        page_row = any(x["kind"] == "note" and x["slot"] == "page" and x["key"] != key and x["page_id"] == u.page_id
                       for x in [*u.rows.values(), *u.out.values()] if x["key"] not in u.dropped)
        if (_is_gamma_notes(tree) or row["slot"] == "page") and not page_row:
            region = self.pv.gamma_notes or _empty_region(u.page_id)
            self._pull_region(key, region, pics)
            row.update(block_id=u.page_id, slot="page", state="live")
        else:
            root = _with_ids(tree, {"zotero_note": item_uri(r.library, key),
                                    **({"zotero_readonly": "lossy"} if lossy else {})})
            self.new_regions.append(root)
            row.update(block_id=root["id"], slot="", state="live")
            u._count("notes_pulled")
            u.notes.append("restored here" if restored else "pulled")
        u._put(row)
        self.decided[key] = {"new": True}

    def _note_gone(self, key: str, obj: dict, row: dict, lossy: bool, first: bool) -> None:
        """A note row whose region is no longer on the page (its root deleted
        or moved away; the page's own notes all gone): trashed in Zotero,
        unless Zotero changed it since (an edit beats a delete) or a first
        link's or a force's pair takes Zotero's version (the region comes
        back); a pair taking Gamma's (a force push) sends Gamma's delete."""
        u, r = self.u, self.r
        data = obj.get("data") or {}
        version = int(obj.get("version") or 0)
        zh = note_hash(str(data.get("note") or ""))
        if row["state"] in ("waiting", "skipped"):  # a region not made yet (moved here, no text before)
            if not data.get("deleted"):
                self._new_region(key, obj, row)
            return
        if row["state"] == "trashed" or data.get("deleted"):
            if row["state"] == "trashed" and not data.get("deleted"):
                self._new_region(key, obj, row, restored=True)  # Zotero restored it: here too
                return
            row.update(state="trashed", version=version, zbase={"note": zh})
            u._put(row)
            return
        adopt = (row["detail"].get("adopt") or r.adopt or "theirs") if first else ""
        if adopt == "theirs" or (not first and zh != (row["zbase"] or {}).get("note")):
            if not first:  # Zotero changed it after it went here: an edit beats a delete
                u.conflicts.append(("zotero_deleted_edited", u.page_id, "(deleted here)",
                                    "Zotero changed this note after it was deleted here: it is back"))
            self._new_region(key, obj, row, restored=True)
            return
        rep = row["detail"].get("rep")
        if (any(bid in self.pv.nodes for bid in rep) if rep else lossy and row["slot"] != "page"):
            # some of its lines are still here (moved into another note), or a lossy note this row never
            # recorded the lines of: Zotero's note stays as it is
            u.conflicts.append(("zotero_deleted_edited", u.page_id, "(its first line deleted here)",
                                "Part of this note is still on the page here: the note stays in Zotero"))
            if first:  # (a force push's pair, decided)
                u._put({**row, "detail": without(row["detail"], "first", "adopt")})
            return
        if not r.pushes or (row["slot"] == "page" and not self._quiet()):
            return
        u.writes.trash("notes", key, version)
        u.sent[key] = {"what": "note_trash"}

    def _note_trashed(self, key: str, obj: dict, row: dict, region, pics, lossy: bool, *, first: bool,
                      restore: bool) -> None:
        """Zotero has the note in its trash and its region is on the page: the
        region's represented blocks go, the blocks Gamma keeps lifted out
        first, nothing sent, unless the region was changed here, came back here
        or was restored from a backup: then the note comes back in Zotero, with
        the region's text unless Zotero's already reads as it. A first link's
        or a force's pair takes its side's trash too: Zotero's (a force pull)
        goes here whatever changed here, the words Gamma held that Zotero's do
        not kept in a resolved ``zotero_note`` row; Gamma's (a force push)
        brings the note back, with the region's text only when their words
        differ."""
        u, r = self.u, self.r
        version = int(obj.get("version") or 0)
        html = str((obj.get("data") or {}).get("note") or "")
        zh = note_hash(html)
        G_html = region.html
        gh = note_hash(G_html)
        changed = gh != (row["gbase"] or {}).get("note")
        adopt = (row["detail"].get("adopt") or r.adopt or "theirs") if first else ""
        if adopt != "theirs" and (row["state"] == "trashed" or first or restore or changed):
            if row["state"] != "trashed" and not first:
                u.conflicts.append(("zotero_deleted_edited", u.page_id if row["slot"] == "page" else row["block_id"],
                                    "(changed here)",
                                    "Zotero moved this note to its trash after it was changed here: it is kept"))
            if r.pushes:
                body = {"deleted": False}
                # Zotero's reads as the region holds it (a force's pair: in the same words, as a first link reads)
                same = (note_words(html) == note_words(G_html) if first
                        else not zotero_merge.note_edits(region, pics.html))
                if not lossy and len(G_html) <= NOTE_MAX and not same:
                    body["note"] = G_html
                row.update(version=version, zbase={"note": zh})  # a trashed row is live once the restore lands
                u.writes.update("notes", key, version, body)
                u.sent[key] = {"what": "note_restore", "html": body.get("note"), "gh": gh, "same": same}
                u._put(row)
            return
        if first:  # Zotero's version, its trash with it
            if note_words(html) != note_words(G_html):
                self._note_conflict("zotero_note", row, G_html, html, decided="zotero")
                r.decided["notes"] = r.decided.get("notes", 0) + 1
            row["detail"] = without(row["detail"], "first", "adopt")
        edits = zotero_merge.note_removal(region)
        self.plans.append(lambda conn, region=region, edits=edits: zotero_local.note_ops(conn, region, edits).ops)
        row.update(state="trashed", version=version, zbase={"note": zh})
        u._put(row)
        u._count("notes_pulled")
        u.notes.append("deleted here")

    def _create_gamma_notes(self) -> None:
        """The page's own notes as one new child note "Notes from Gamma", once
        the page is still: its row written pending first."""
        u, r = self.u, self.r
        region = self.pv.gamma_notes
        html = region.html
        key = object_key(r.ws, r.library, "note", u.key, u.page_id, "page")
        existing = u.out.get(key) or u.rows.get(key)
        if existing is not None and existing["state"] == "rejected" and (
                existing["detail"].get("rejected") == note_hash(html)):
            return
        if not self._quiet():
            return
        sent = {"note": note_hash(html)}
        row = new_row(key, "note", page_id=u.page_id, block_id=u.page_id, slot="page", parent=u.key,
                      zotero_type="note", version=-1, state="pending", gsig=sent["note"], detail={"sent": sent})
        if len(html) > NOTE_MAX:
            row.update(state="rejected")
            self._refused(row, NOTE_TOO_LONG)
            u._put(row)
            return
        u._put(row)
        body = {"itemType": "note", "parentItem": u.key, "note": html}
        u.writes.create("notes", key, body)
        u.sent[key] = {"what": "note_create", "body": body, "stage": "notes", "gh": sent["note"], "html": html}

    def _resend_note(self, row: dict, *, refused: bool = False) -> None:
        """A note create Zotero does not hold (its answer lost, or refused):
        derived again from the page as it is: its region gone, the row
        goes; a refused one goes again only once it changed."""
        u = self.u
        region = self._region_of(row)
        if region is None:
            u._drop(row["key"])
            return
        if not self.r.pushes:
            return  # a receive-only round sends nothing: the first two-way round sends it
        html = region.html
        if refused and row["detail"].get("rejected") in (note_hash(html), row["gsig"]):
            return
        if row["slot"] == "page" and not self._quiet():
            return
        if len(html) > NOTE_MAX:
            row = {**row, "state": "rejected"}
            self._refused(row, NOTE_TOO_LONG)
            u._put(row)
            return
        sent = {"note": note_hash(html)}
        row = {**row, "state": "pending", "version": -1, "gsig": sent["note"],
               "detail": {**without(row["detail"], "rejected", "reason"), "sent": sent}}
        u._put(row)
        body = {"itemType": "note", "parentItem": u.key, "note": html}
        u.writes.create("notes", row["key"], body)
        u.sent[row["key"]] = {"what": "note_create", "body": body, "stage": "notes", "gh": sent["note"], "html": html}

    # annotations

    def _zotero_annotations(self, att) -> dict:
        if att is None:
            return {}
        return {k: c for k, c in self.u.kids.items() if (c.get("data") or {}).get("itemType") == "annotation"
                and (c.get("data") or {}).get("parentItem") == att["key"]}

    def _items(self, ann, rows_of=(), *, zotero_type=None, ink=None) -> dict | None:
        """``{slot: zotero_annotations.Item}`` of an annotation block (a
        ``zotero_local.Annotation``) on the page's PDF, its ink strokes
        assigned to the block's rows (by stroke id first); None when
        its place cannot be read now (its PDF page, its ink file), which
        holds it rather than reading as gone."""
        block = ann.block
        props = block.get("properties") or {}
        if ink is None and "ink_url" in props:
            ink = self.ink.get(block["id"]) or zotero_local.read_ink(self.r.ws, props)
            if ink is None:
                return None
        n = zotero_annotations.page_number(props, ink, on_sheet=ann.on_sheet)
        page = self.page(n) if n else None
        if page is None:
            return None
        ink_rows = [(row["slot"], tuple(row["detail"].get("stroke_ids") or ()), row["detail"].get("look") or "",
                     row["state"] == "readonly") for row in rows_of if row["slot"] or "ink_url" in props]
        types = {row["zotero_type"] for row in rows_of if row["zotero_type"]}
        zotero_type = zotero_type or (next(iter(types)) if len(types) == 1 else None)
        items = zotero_annotations.to_items(block, page, ink_file=ink, rows=ink_rows, zotero_type=zotero_type,
                                            nested=ann.nested)
        return {item.slot: item for item in items}

    def annotations(self) -> None:
        """The annotations of the item's primary PDF against the page's
        annotation blocks: only while both sides hold the same file (MD5); a
        held block is never pushed nor trashed for."""
        u, r = self.u, self.r
        att = u._primary()
        if att is None:
            return  # no PDF in Zotero: the page's annotation rows wait as they are
        att_key = att["key"]
        zmd5 = str((att.get("data") or {}).get("md5") or "").lower()
        side = u.side
        same = bool(side.md5) and side.md5 == zmd5
        waits = "" if same else (WAIT_FILE if not side.md5 else WAIT_DIFFER)
        zitems = self._zotero_annotations(att)
        rows = {}
        for key, row in list(u.rows.items()):
            if row["kind"] != "annotation" or key in u.dropped:
                continue
            if key in zitems or (row["page_id"] == u.page_id and row["parent"] == att_key):
                rows[key] = u.out.get(key) or row
            elif row["page_id"] == u.page_id:
                u._drop(key)  # the old primary's: left in Zotero with it, the blocks new to the new one
        blocks = {a.block["id"]: a for a in self.pv.annotations}
        by_block: dict[str, list] = {}
        for row in rows.values():
            by_block.setdefault(row["block_id"], []).append(row)
        for key, obj in sorted(zitems.items(), key=lambda kv: (str((kv[1].get("data") or {})
                                                                   .get("annotationSortIndex") or ""), kv[0])):
            row = rows.get(key)
            if row is None and self.u.first_link and not (obj.get("data") or {}).get("deleted"):
                row = self._match_annotation(key, obj, blocks, by_block, att_key)
            self._annotation(key, obj, row, blocks, by_block, att_key, same, waits)
        for key, row in rows.items():
            if key in zitems or key in u.dropped:
                continue
            if row["state"] in ("pending", "rejected") and int(row["version"]) <= 0:
                self._resend_annotation(row, blocks, by_block, same, zmd5)
        if r.pushes and same:
            for bid, ann in blocks.items():
                if zotero_annotations.eligible(ann.block, side.md5, zmd5, on_sheet=ann.on_sheet):
                    self._create_annotations(ann, by_block.get(bid, []), att_key)

    def _match_annotation(self, key, obj, blocks, by_block, att_key) -> dict | None:
        """A first link's block for a Zotero annotation no row names: one
        marked with its key, else with the key the embedded import gives the
        annotation Zotero would embed for it (``imported_annot``),
        still-embedded blocks included. None for none."""
        data = obj.get("data") or {}
        page = self.page(_page_index(data) + 1)
        made = zotero_annotations.from_item(data, page) if page else None
        imported = (made or {}).get("props", {}).get("imported_annot")
        free = [a for bid, a in blocks.items() if bid not in by_block]
        found = next((a for a in free if (a.block.get("properties") or {}).get("zotero_annotation") == key), None)
        if found is None and imported:
            found = next((a for a in free if (a.block.get("properties") or {}).get("imported_annot") == imported), None)
        if found is None:
            return None
        row = new_row(key, "annotation", page_id=self.u.page_id, block_id=found.block["id"],
                      slot=(made or {}).get("slot") or "", parent=att_key,
                      zotero_type=str(data.get("annotationType") or ""), version=0,
                      detail={"first": True, "stroke_ids": list((made or {}).get("stroke_ids") or ())})
        if "ink_url" in (found.block.get("properties") or {}):
            ink = zotero_local.read_ink(self.r.ws, found.block.get("properties"))
            row["detail"]["stroke_ids"] = [s.id for s in ink.strokes] if ink else []
        by_block.setdefault(found.block["id"], []).append(row)
        return row

    def _annotation(self, key, obj, row, blocks, by_block, att_key, same, waits) -> None:
        """One Zotero annotation of the page's PDF against its block."""
        u = self.u
        data = obj.get("data") or {}
        version = int(obj.get("version") or 0)
        trashed = bool(data.get("deleted"))
        if row is None:
            if trashed:
                return
            if not same:
                u._put(new_row(key, "annotation", page_id=u.page_id, parent=att_key,
                               zotero_type=str(data.get("annotationType") or ""), version=version,
                               state="waiting", detail={"reason": waits}))
                return
            self._bring(key, obj, None, att_key)
            return
        row = dict(row)
        row["parent"] = att_key
        if row["state"] == "pending":  # our create, which Zotero now holds
            sent = row["detail"].get("sent") or {}
            row.update(state="live", zbase=sent, gbase=sent, detail=without(row["detail"], "sent"))
        ann = blocks.get(row["block_id"])
        # not on the page yet: waiting for its PDF, skipped, or trashed in Zotero while it waited
        unplaced = row["state"] in ("waiting", "skipped") or (row["state"] == "trashed" and not row["block_id"]
                                                               and not trashed)
        if unplaced and ann is None:
            if trashed or not same:
                state = ("trashed" if row["state"] == "waiting" else row["state"]) if trashed else "waiting"
                row.update(version=version, state=state,
                           detail={**row["detail"], "reason": waits or row["detail"].get("reason") or ""})
                u._put(row)
            elif not (row["state"] == "skipped" and version == int(row["version"])):
                self._bring(key, obj, row, att_key)
            return
        if ann is None and row["state"] != "trashed" and row["block_id"] in self.pv.nodes:
            # on its page still, but no annotation block now (a text box on a sheet): held
            row.update(state="held", version=version,
                       detail={**row["detail"], "reason": zotero_annotations.WAIT_NO_PLACE})
            u._put(row)
            return
        if ann is None:
            return self._annotation_gone(key, obj, row, att_key)
        if not same:
            row.update(state="held", version=version, detail={**row["detail"], "reason": waits})
            u._put(row)
            return
        reason = zotero_annotations.hold_reason(ann.block)
        if reason:  # held: nothing sent, nothing trashed; Zotero's change waits in its base
            row.update(state="held", version=version, detail={**row["detail"], "reason": reason})
            u._put(row)
            return
        if trashed:
            return self._annotation_trashed(key, obj, row, ann, by_block.get(row["block_id"], []))
        if (row["state"] in ("live", "readonly") and not row["detail"].get("first")
                and zotero_annotations.projection(data) == row["zbase"]
                and zotero_annotations.block_sig(ann.block, ann.nested) == row["gsig"]):
            author = _author(self.r, obj) or ("readonly" if row["state"] == "readonly" else "")
            due = self._label_due(row, data, author)
            refused = row["detail"].get("rejected")
            if due and self.r.pushes and not (refused and refused == row["gsig"]):
                return self._send_label(key, row, version, due)
            settled = due == "" and row["detail"].get("labels") != zotero_annotations.LABELS
            if settled:
                row["detail"] = {**row["detail"], "labels": zotero_annotations.LABELS}
            if version != int(row["version"]) or settled:  # neither side changed it (a tag in Zotero): its version
                row["version"] = version
                u._put(row)
            return
        self._merge_annotation(key, obj, row, ann, by_block.get(row["block_id"], []))

    def _bring(self, key, obj, row, att_key, *, restored: bool = False, why: str = "") -> None:
        """Zotero's annotation as a new block after the page's last annotation
        block (``from_item``: its ink file stored first), marked when it is
        another group member's; a row it cannot make a block of is skipped at
        its version."""
        u, r = self.u, self.r
        data = obj.get("data") or {}
        version = int(obj.get("version") or 0)
        page = self.page(_page_index(data) + 1)
        made = zotero_annotations.from_item(data, page) if page else None
        row = dict(row) if row else new_row(key, "annotation")
        row.update(page_id=u.page_id, parent=att_key, zotero_type=str(data.get("annotationType") or ""),
                   version=version, zbase=zotero_annotations.projection(data), gbase={}, gsig="",
                   detail=without(row["detail"], "first", "adopt", "restore", "sent", "rejected", "reason"))
        if made is None:
            row.update(state="skipped", block_id="")
            u._put(row)
            return
        author = _author(r, obj)
        if author:
            made = {**made, "props": {**made["props"], "zotero_readonly": "locked", "zotero_author": author}}
        block = zotero_local.new_annotation(r.ws, made)
        self.new_blocks.append(block)
        row.update(block_id=block["id"], slot=made.get("slot") or "", state="readonly" if author else "live",
                   detail={**row["detail"], "stroke_ids": list(made.get("stroke_ids") or ()),
                           **({"author": author} if author else {}),
                           "labels": zotero_annotations.LABELS})  # a page label of Zotero's own: it stays
        u._put(row)
        self.decided[key] = {"new": True}
        u._count("annotations_pulled")
        u.notes.append(why or ("restored here" if restored else "pulled"))

    def _send_label(self, key, row, version, label) -> None:
        """The page label ``label`` (``_label_due``) as a PATCH of that
        alone, for an annotation neither side changed since it synced: both
        bases stay, and the row is settled once the write lands
        (``record``)."""
        u = self.u
        zb, gb = row["zbase"] or {}, row["gbase"] or {}
        row["version"] = version
        u._put(row)
        u.writes.update("annotations", key, version, {"annotationPageLabel": label})
        u.sent[key] = {"what": "ann_update", "m": zotero_merge.Merge({}, {}, [], {}, {}), "zbase": zb, "gbase": gb,
                       "Z": zb, "G": gb, "sig": row["gsig"], "label": True}

    def _annotation_gone(self, key, obj, row, att_key) -> None:
        """A row whose block is no longer an annotation block of the page
        (deleted, or moved to another page): trashed in Zotero, unless Zotero
        changed it since (an edit beats a delete), it is another member's (put
        back) or a first link's or a force's pair taking Zotero's version (put
        back); one taking Gamma's (a force push) sends Gamma's delete."""
        u, r = self.u, self.r
        data = obj.get("data") or {}
        version = int(obj.get("version") or 0)
        Z = zotero_annotations.projection(data)
        trashed = bool(data.get("deleted"))
        if row["state"] == "trashed":
            if trashed:
                row.update(version=version, zbase=Z)
                u._put(row)
            else:
                self._bring(key, obj, row, att_key, restored=True)  # Zotero restored it: here too
            return
        if trashed:
            row.update(state="trashed", version=version, zbase=Z)
            u._put(row)
            return
        author = _author(r, obj)
        if author:
            u.conflicts.append(("zotero_locked", row["block_id"] or u.page_id,
                                "(deleted here)", f"{author}'s annotation is read-only in Zotero: it is back"))
            self._bring(key, obj, row, att_key, why="restored here")
            return
        first = bool(row["detail"].get("first"))
        adopt = (row["detail"].get("adopt") or r.adopt or "theirs") if first else ""
        if adopt == "theirs" or (not first and Z != (row["zbase"] or {})):
            if not first:
                u.conflicts.append(("zotero_deleted_edited", u.page_id, "(deleted here)",
                                    "Zotero changed this annotation after it was deleted here: it is back"))
            self._bring(key, obj, row, att_key, restored=True)
            return
        if not r.pushes:
            return
        u.writes.trash("annotations", key, version)
        u.sent[key] = {"what": "ann_trash"}

    def _annotation_trashed(self, key, obj, row, ann, rows_of) -> None:
        """Zotero has the annotation in its trash and its block is on the page:
        the block goes (its children lifted to its place; an ink item's strokes
        alone when the drawing holds others), nothing sent, unless it was
        changed here, came back here (an undo) or was restored from a backup:
        then it comes back in Zotero. A first link's or a force's pair takes
        its side's trash too: Zotero's (a force pull) goes here whatever
        changed here, what Gamma held that Zotero's does not kept in a resolved
        ``zotero_annotation`` row; Gamma's (a force push) brings it back."""
        u, r = self.u, self.r
        data = obj.get("data") or {}
        version = int(obj.get("version") or 0)
        Z = zotero_annotations.projection(data)
        items = self._items(ann, rows_of, zotero_type=row["zotero_type"])
        item = (items or {}).get(row["slot"])
        G = zotero_annotations.projection(item.fields) if item else {}
        if items is None:  # its place cannot be read now: changed here unless its signature is as synced
            changed = zotero_annotations.block_sig(ann.block, ann.nested) != row["gsig"]
        else:
            changed = item is not None and G != (row["gbase"] or {})
        first = bool(row["detail"].get("first"))
        adopt = (row["detail"].get("adopt") or r.adopt or "theirs") if first else ""
        if adopt != "theirs" and (row["state"] == "trashed" or first or row["detail"].get("restore") or changed):
            if item is None or not r.pushes or row["state"] == "readonly":
                return
            if row["state"] != "trashed" and not first:
                u.conflicts.append(("zotero_deleted_edited", ann.block["id"], "(changed here)",
                                    "Zotero moved this annotation to its trash after it was changed here: "
                                    "it is kept"))
            names = [f for f in G if G.get(f) != Z.get(f)]
            body = {"deleted": False, **zotero_annotations.patch_fields(item.fields, names, row["gbase"] or None)}
            row.update(version=version, zbase=Z)
            u._put(row)
            u.writes.update("annotations", key, version, body)
            u.sent[key] = {"what": "ann_restore", "G": G, "sig": zotero_annotations.block_sig(ann.block, ann.nested),
                           "item": item}
            return
        if first:  # Zotero's version, its trash with it
            if item is not None and not zotero_annotations.same(Z, G):
                label = self._label((G.get("position") or {}).get("pageIndex", -1) + 1)
                field = next((f for f in G if G.get(f) != Z.get(f)), "")
                u.conflicts.append(("zotero_annotation", ann.block["id"], zotero_merge.annotation_line(G, label),
                                    zotero_merge.annotation_line(Z, label),
                                    json.dumps({"key": key, "z": Z, "field": field}, ensure_ascii=False), "zotero"))
                r.decided["annotations"] = r.decided.get("annotations", 0) + 1
            row["detail"] = without(row["detail"], "first", "adopt")
        others = [x for x in rows_of if x["key"] != key and x["state"] not in ("trashed",)]
        if "ink_url" in (ann.block.get("properties") or {}) and others:
            ink = self.ink.get(ann.block["id"]) or zotero_local.read_ink(r.ws, ann.block.get("properties"))
            ids = set(row["detail"].get("stroke_ids") or ())
            if ink is not None and ids:
                kept = zotero_annotations.replace_strokes(ink, ids, [])
                self._change(ann.block["id"], {"ink": kept, "props": {"pdf_position": inkmod.pdf_position(kept),
                                                                      "ink_strokes": len(kept.strokes)}})
        else:
            self.plans.append(lambda conn, bid=ann.block["id"]: zotero_local.annotation_delete(conn, bid))
        row.update(state="trashed", version=version, zbase=Z)
        u._put(row)
        u._count("annotations_pulled")
        u.notes.append("deleted here")

    def _change(self, bid: str, change: dict) -> None:
        """One pulled change of a block, merged with the others the unit
        makes of it (an ink drawing's items change one file)."""
        acc = self.changes.setdefault(bid, {"props": {}})
        acc["props"].update(change.get("props") or {})
        if change.get("content") is not None:
            acc["content"] = change["content"]
        if change.get("ink") is not None:
            acc["ink"] = change["ink"]
            self.ink[bid] = change["ink"]

    def _merge_annotation(self, key, obj, row, ann, rows_of) -> None:
        """Field by field against the two bases (see
        docs/dev/zotero_sync.md#merging): Zotero's changes into the block (only
        those fields), Gamma's as a PATCH of those fields, both apart → Gamma's
        wins and a ``zotero_annotation`` row keeps Zotero's; another member's
        annotation takes Zotero's and sends nothing (a ``zotero_locked`` row
        for an edit here)."""
        u, r = self.u, self.r
        data = obj.get("data") or {}
        version = int(obj.get("version") or 0)
        bid = ann.block["id"]
        items = self._items(ann, rows_of, zotero_type=row["zotero_type"])
        if items is None:  # its place cannot be read now: held
            row.update(state="held", version=version,
                       detail={**row["detail"], "reason": zotero_annotations.WAIT_NO_PLACE})
            u._put(row)
            return
        item = items.get(row["slot"])
        if item is None:  # its strokes left the drawing (erased, restyled into another item): gone here
            return self._annotation_gone(key, obj, row, row["parent"])
        Z, G = zotero_annotations.projection(data), zotero_annotations.projection(item.fields)
        first = bool(row["detail"].get("first"))
        author = _author(r, obj)
        adopt = row["detail"].get("adopt") or r.adopt or "theirs"
        if first:
            zb, gb = zotero_merge.first_link_base("annotation", Z, G, sets=())
            prefer = "zotero" if adopt == "theirs" else "gamma"
        else:
            zb, gb, prefer = row["zbase"] or {}, row["gbase"] or {}, "gamma"
            if row["detail"].get("restore"):  # a backup restored: what Zotero changed since reads as changed
                gb = {f: None if Z.get(f) != zb.get(f) else v for f, v in gb.items()}  # here too: the block wins
        if author:
            prefer = "zotero"
        nested = [n for n in ann.nested if n[0] >= 1]
        convert = text_box.markdown_of if row["zotero_type"] == "text" else zotero_annotations.comment_in
        texts = {} if nested or "ink_url" in (ann.block.get("properties") or {}) else {"comment": convert}
        m = zotero_merge.merge(zb, gb, Z, G, sets=(), texts=texts, prefer=prefer)
        if author:
            if any(G.get(f) != gb.get(f) for f in G):
                u.conflicts.append(("zotero_locked", bid, zotero_merge.annotation_line(G, self._label(
                    (Z.get("position") or {}).get("pageIndex", -1) + 1)),
                    f"{author}'s annotation is read-only in Zotero: this edit stays in Gamma"))
            m = m._replace(push={}, conflicts=[], merged_text={})
        changed = {f: Z[f] for f in m.apply}
        if changed:
            page = self.page((Z.get("position") or {}).get("pageIndex", -1) + 1) or self.page(
                zotero_annotations.page_number(ann.block.get("properties") or {}))
            block = {**ann.block, "properties": {**(ann.block.get("properties") or {}),
                                                 **(self.changes.get(bid, {}).get("props") or {})}}
            ink = self.ink.get(bid) or (zotero_local.read_ink(r.ws, ann.block.get("properties"))
                                        if "ink_url" in (ann.block.get("properties") or {}) else None)
            first_item = not items or row["slot"] == next(iter(items))
            change = zotero_annotations.apply_fields(
                block, page, changed, ink_file=ink, stroke_ids=row["detail"].get("stroke_ids") or (),
                nested=ann.nested, zotero_type=row["zotero_type"] or None, key=key, first=first_item) if page else {}
            refused = [what for what in change.get("conflicts") or () if what in m.apply]
            for what in refused:  # Zotero changed what Gamma cannot take (a comment's nested part): Gamma's goes
                label = self._label(page.number if page else 0)
                u.conflicts.append(("zotero_annotation", bid, zotero_merge.annotation_line(G, label),
                                    zotero_merge.annotation_line(Z, label),
                                    json.dumps({"key": key, "z": Z, "field": what}, ensure_ascii=False), ""))
            if refused:
                m = m._replace(apply={f: v for f, v in m.apply.items() if f not in refused},
                               push={**m.push, **{f: G.get(f) for f in refused}})
            if change.get("stroke_ids"):
                row["detail"] = {**row["detail"], "stroke_ids": list(change["stroke_ids"])}
            self._change(bid, change)
            u._count("annotations_pulled")
            if "pulled" not in u.notes:
                u.notes.append("pulled")
        if "comment" in m.merged_text:
            self._change(bid, {"content": m.merged_text["comment"]})
        label = self._label((G.get("position") or {}).get("pageIndex", -1) + 1)
        for name, ours, theirs in m.conflicts:  # (a first link's or a force's: the side it took, resolved)
            took = ("zotero" if name in m.apply else "gamma") if first else ""
            u.conflicts.append(("zotero_annotation", bid, zotero_merge.annotation_line(G, label),
                                zotero_merge.annotation_line(Z, label),
                                json.dumps({"key": key, "z": Z, "field": name}, ensure_ascii=False), took))
            if first:
                r.decided["annotations"] = r.decided.get("annotations", 0) + 1
        wanted = {**m.push, **m.merged_text}  # Gamma's side to send: its base waits until the write lands
        sig = zotero_annotations.block_sig(ann.block, ann.nested)
        refused = row["detail"].get("rejected")
        sends = r.pushes and not (refused and refused == sig)  # refused: again once it changes
        push = wanted if sends else {}
        due = self._label_due(row, data, author)
        label = due if sends else ""  # (settled once its write lands: ``record``)
        detail = without(row["detail"], "reason", "restore")
        if due == "":
            detail["labels"] = zotero_annotations.LABELS
        if author:
            detail["author"] = author
        if item.look:
            detail["look"] = item.look
        row.update(version=version, state="readonly" if author else "live", detail=detail)
        decision = {"m": m, "zbase": zb, "gbase": gb, "Z": Z, "G": G, "applied": bool(changed or m.merged_text),
                    "wanted": bool(wanted)}
        self.decided[key] = decision
        if push or label:
            fields = dict(item.fields)
            if "comment" in m.merged_text:
                fields["annotationComment"] = zotero_annotations.comment_out(m.merged_text["comment"], ann.nested)
            body = zotero_annotations.patch_fields(fields, list(push), gb or None)
            if label and "annotationPageLabel" not in body:
                body["annotationPageLabel"] = label
            if body:
                u.writes.update("annotations", key, version, body)
                u.sent[key] = {"what": "ann_update", **decision, "sig": sig, "label": "annotationPageLabel" in body}
        zb2, gb2 = zotero_merge.bases_after(m, zb, gb, Z, G, pushed_ok=False)
        row.update(zbase=zb2, gbase=gb2)
        self._synced_side(row)
        if not wanted:  # decided now: the signature is Gamma's as synced, a first link's pair settled
            row["gsig"] = sig
            row["detail"] = without(row["detail"], "first", "adopt")
        elif any(value is None for value in gb2.values()):
            row["gsig"] = ""  # Gamma's won a field both changed and goes, even if the block is set back meanwhile
        u._put(row)

    def _create_annotations(self, ann, rows_of, att_key) -> None:
        """A Gamma annotation block's items no row stands for, created in
        Zotero: a new highlight, a new look in a drawing, a split piece. Each
        under a key derived from its block and slot with the attachment in the
        seed, its row written pending first."""
        u, r = self.u, self.r
        live = [x for x in rows_of if x["key"] not in u.dropped]
        props = ann.block.get("properties") or {}
        readonly = any(x["state"] == "readonly" for x in live)
        if (readonly and "ink_url" not in props) or (_locked(ann.block) and not readonly):
            return  # another member's: never made under this key, even once its row went with an old primary
            # (while its row is live, a drawing's strokes no row owns are the user's own)
        sig = zotero_annotations.block_sig(ann.block, ann.nested)
        if live and all(x["gsig"] == sig for x in live):
            return  # as synced: no item of it is new
        items = self._items(ann, live) or {}
        have = {x["slot"] for x in live}
        for slot, item in items.items():
            if slot in have:
                continue
            key = object_key(r.ws, r.library, "annotation", att_key, ann.block["id"], slot)
            if key in u.rows or key in u.out:
                key = random_key()
            fields = {**item.fields, "parentItem": att_key}
            G = zotero_annotations.projection(item.fields)
            u._put(new_row(key, "annotation", page_id=u.page_id, block_id=ann.block["id"], slot=slot,
                           parent=att_key, zotero_type=str(item.fields.get("annotationType") or ""), version=-1,
                           state="pending", gsig=sig, detail={"sent": G, "stroke_ids": list(item.stroke_ids),
                                                              "look": item.look}))
            u.writes.create("annotations", key, fields)
            u.sent[key] = {"what": "ann_create", "body": fields, "stage": "annotations", "G": G, "sig": sig}

    def _resend_annotation(self, row, blocks, by_block, same, zmd5) -> None:
        """An annotation create Zotero does not hold (its answer lost, or
        refused): derived again from the block as it is: gone or held,
        the row goes (made again once it may be); a refused one is sent again
        only once it changed."""
        u = self.u
        ann = blocks.get(row["block_id"])
        side = u.side
        if ann is None or not same or not zotero_annotations.eligible(ann.block, side.md5, zmd5,
                                                                       on_sheet=ann.on_sheet):
            u._drop(row["key"])
            by_block[row["block_id"]] = [x for x in by_block.get(row["block_id"], []) if x["key"] != row["key"]]
            return
        sig = zotero_annotations.block_sig(ann.block, ann.nested)
        if row["state"] == "rejected" and row["detail"].get("rejected") == sig:
            return
        items = self._items(ann, by_block.get(row["block_id"], []))
        if items is None:
            return  # its place cannot be read now: the create waits
        item = items.get(row["slot"])
        if item is None:
            u._drop(row["key"])
            return
        if not self.r.pushes:
            return
        fields = {**item.fields, "parentItem": row["parent"]}
        G = zotero_annotations.projection(item.fields)
        u._put({**row, "state": "pending", "version": -1, "gsig": sig,
                "detail": {**without(row["detail"], "rejected", "reason"), "sent": G,
                           "stroke_ids": list(item.stroke_ids), "look": item.look}})
        u.writes.create("annotations", row["key"], fields)
        u.sent[row["key"]] = {"what": "ann_create", "body": fields, "stage": "annotations", "G": G, "sig": sig}

    # writing

    def ops_now(self, conn) -> list:
        """The unit's block ops on the page, built under its lock: pulled
        changes of blocks and marks, the plans (note diffs, deletions), the new
        annotation blocks after the last one and the new regions at the end."""
        out = list(self.ops)
        for plan in self.plans:
            out += plan(conn)
        if self.new_blocks:
            out += zotero_local.annotation_inserts(conn, self.u.page_id, self.new_blocks)
        if self.new_regions:
            out += _tree_inserts(conn, self.u.page_id, self.new_regions)
        return out

    def rows_tx(self, conn) -> None:
        """The bases of what the unit applied here, read back after its ops in
        its transaction: Gamma's projection of a block or a region as it now
        stands, so a lossy mapping never reads as a Gamma change; a field
        pushed keeps its old base until the write lands. Only the regions of
        the notes it decided are rendered (it holds the write lock)."""
        u = self.u
        if not self.decided or not u.page_id:
            return
        roots = [row["block_id"] for row in [*u.rows.values(), *u.out.values()]
                 if row["kind"] == "note" and row["page_id"] == u.page_id and row["slot"] != "page"
                 and row["block_id"] and row["key"] not in u.dropped]
        render = {u.out[k]["block_id"] for k, how in self.decided.items()  # (a page-slot row's is the page's)
                  if k in u.out and k not in u.dropped and u.out[k]["kind"] == "note"
                  and (how.get("new") or how.get("applied"))}
        view = zotero_local.read_page(conn, u.page_id, note_roots=roots, image_key=self.names.get,
                                      notes=bool(self.r.access.get("notes")) and bool(render), render=render)
        if view is None:
            return
        self.ink = {}
        anns = {a.block["id"]: a for a in view.annotations}
        for key, how in self.decided.items():
            row = u.out.get(key)
            if row is None or key in u.dropped:
                continue
            if row["kind"] == "note":
                if not (how.get("new") or how.get("applied")):
                    continue
                region = view.gamma_notes if row["slot"] == "page" else view.regions.get(row["block_id"])
                if region is None and row["slot"] == "page":
                    region = _empty_region(u.page_id)
                if region is not None and key not in u.sent:
                    row["gbase"] = {"note": note_hash(region.html)}
                _keep_represented(row, region)
                continue
            ann = anns.get(row["block_id"])
            if ann is None:
                continue
            rows_of = [u.out.get(k) or x for k, x in {**u.rows, **u.out}.items() if x["kind"] == "annotation"
                       and x["block_id"] == row["block_id"] and k not in u.dropped]
            item = (self._items(ann, rows_of, zotero_type=row["zotero_type"]) or {}).get(row["slot"])
            if item is None:
                continue
            after = zotero_annotations.projection(item.fields)
            sig = zotero_annotations.block_sig(ann.block, ann.nested)
            strokes = item.stroke_ids or row["detail"].get("stroke_ids") or ()
            row["detail"] = {**row["detail"], "stroke_ids": list(strokes), **({"look": item.look} if item.look else {})}
            if how.get("new"):
                row.update(gbase=after, gsig=sig)
            elif how.get("applied"):
                row["zbase"], row["gbase"] = zotero_merge.bases_after(how["m"], how["zbase"], how["gbase"], how["Z"],
                                                                      how["G"], pushed_ok=False, G_after=after)
                self._synced_side(row)
                if key in u.sent:
                    u.sent[key]["G_after"] = after
                    u.sent[key]["sig"] = sig
                elif not how.get("wanted"):  # an edit here not sent yet keeps the old signature
                    row["gsig"] = sig

    def record(self, row: dict, sent: dict, outcome) -> None:
        """One block write's outcome on its row: saved → its version and
        Zotero's base from the saved object, Gamma's base and signature as
        decided; a trash → trashed."""
        u = self.u
        what = sent["what"]
        saved = (outcome.data or {}).get("data") or None
        version = int(outcome.version or row["version"] or 0)
        detail = without(row["detail"], "sent", "rejected", "reason", "first", "adopt", "force", "restore")
        if what == "ann_create" or sent.get("label"):  # its page label as Zotero's reader gives it
            detail["labels"] = zotero_annotations.LABELS
        if what == "ann_create":
            row.update(state="live", version=version, zbase=zotero_annotations.projection(saved) if saved
                       else sent["G"], gbase=sent["G"], gsig=sent["sig"], detail=detail)
            u._count("annotations_pushed")
            u.notes.append("pushed")
        elif what in ("ann_update", "ann_restore"):
            Z_after = zotero_annotations.projection(saved) if saved else None
            if what == "ann_update":
                zb, gb = zotero_merge.bases_after(sent["m"], sent["zbase"], sent["gbase"], sent["Z"], sent["G"],
                                                  pushed_ok=True, G_after=sent.get("G_after"), Z_after=Z_after)
            else:
                zb, gb = Z_after or sent["G"], sent["G"]
            row.update(state="readonly" if row["state"] == "readonly" else "live", version=version, zbase=zb,
                       gbase=gb, gsig=sent["sig"], detail=detail)
            u._count("annotations_pushed")
            u.notes.append("restored there" if what == "ann_restore" else "pushed")
        elif what == "ann_trash":
            row.update(state="trashed", version=version, detail=detail)
            u._count("annotations_pushed")
            u.notes.append("deleted there")
        elif what == "note_create":
            html = (saved or {}).get("note") if saved else sent["html"]
            row.update(state="live", version=version, zbase={"note": note_hash(html)}, gbase={"note": sent["gh"]},
                       gsig="", detail=detail)
            u._count("notes_pushed")
            u.notes.append("pushed")
        elif what in ("note_update", "note_restore"):
            html = (saved or {}).get("note") if saved else sent.get("html")
            row.update(state="live", version=version, detail=detail)
            if html is not None:
                row["zbase"] = {"note": note_hash(html)}
            if sent.get("html") is not None or sent.get("same"):
                row["gbase"] = {"note": sent["gh"]}
            u._count("notes_pushed")
            u.notes.append("restored there" if what == "note_restore" else "pushed")
        elif what == "note_trash":
            row.update(state="trashed", version=version, detail=detail)
            u._count("notes_pushed")
            u.notes.append("deleted there")
        self._synced_side(row)


# --- Gamma's pages -----------------------------------------------------------------------

def blocks_dirty(r: Round, page_id: str, md5: str, bound: dict | None, *, notes: bool = True) -> bool:
    """Whether a linked page's notes or annotations differ from what last
    synced: a region changed, gone or new, an annotation block changed,
    gone, back (an undo) or new and one Zotero could take (``md5``: the
    page's PDF's, ``bound`` the attachment row that is its PDF), or an
    annotation Gamma made whose page label is not settled may hold a label
    to send (``_labels_unsettled``). Cheap signatures (``block_sig``, the
    regions' hashes); the PDF is read only for the page labels of such rows
    (``detail.labels``, ``_made_here``)."""
    with connect_pages_db(r.ws) as conn:
        rows = read_rows(conn, "page_id = ? AND kind IN ('note', 'annotation')", (page_id,))
        names = _image_names(conn, page_id)
        roots = [row["block_id"] for row in rows.values() if row["kind"] == "note" and row["slot"] != "page"
                 and row["block_id"]]
        view = zotero_local.read_page(conn, page_id, note_roots=roots, image_key=names.get,
                                      notes=bool(r.access.get("notes")))
    if view is None:
        return False
    if any((row["detail"].get("first") or row["detail"].get("restore")) and row["state"] in ("live", "readonly")
           for row in rows.values()):
        return True  # a first link's, a force's or a restore's pair not decided yet
    if notes and r.access.get("notes"):
        page_row = None
        for row in rows.values():
            if row["kind"] != "note":
                continue
            if row["slot"] == "page":
                page_row = row
                region = view.gamma_notes
            else:
                region = view.regions.get(row["block_id"])
            if row["state"] in ("waiting", "skipped"):
                continue
            if region is None:
                if row["state"] != "trashed":
                    return True
                continue
            if row["state"] == "trashed" or note_hash(region.html) != (row["gbase"] or {}).get("note"):
                return True
        if page_row is None and view.gamma_notes is not None:
            return True
    blocks = {a.block["id"]: a for a in view.annotations}
    by_block: dict[str, list] = {}
    unsettled = set()  # the 0-based pages of the live annotations Gamma made whose page label is not settled
    for row in rows.values():
        if row["kind"] != "annotation":
            continue
        by_block.setdefault(row["block_id"], []).append(row)
        if row["state"] in ("waiting", "skipped"):
            continue
        ann = blocks.get(row["block_id"])
        if ann is None:
            if row["state"] in ("trashed", "rejected") or (row["state"] == "held" and row["block_id"] in view.nodes):
                continue
            return True
        if row["state"] == "trashed":
            return True
        if row["state"] == "pending" and int(row["version"]) <= 0:
            return True  # a create Zotero has not confirmed (its answer lost, or never sent): sent again
        if row["state"] in ("live", "readonly", "held", "pending", "rejected") and \
                zotero_annotations.block_sig(ann.block, ann.nested) != row["gsig"]:
            return True  # (a create Zotero refused goes again once the block changed)
        if row["state"] == "live" and row["detail"].get("labels") != zotero_annotations.LABELS \
                and not row["detail"].get("rejected") and _made_here(r, row):
            unsettled.add(((row["zbase"] or {}).get("position") or {}).get("pageIndex"))
    if unsettled and view.pdf is not None and _labels_unsettled(view.pdf, unsettled):
        return True
    zmd5 = (bound or {}).get("detail", {}).get("md5") or ""
    return any(bid not in by_block and not _locked(a.block)
               and zotero_annotations.eligible(a.block, md5, zmd5, on_sheet=a.on_sheet) for bid, a in blocks.items())


def _labels_unsettled(path, indexes) -> bool:
    """Whether one of the pages ``indexes`` (0-based) of the PDF at ``path``
    has another page label as PyMuPDF reads it than as Zotero's reader
    gives it: only there can an annotation not settled hold a label to
    send (``Blocks._label_due``)."""
    with zotero_annotations.Pages(str(path)) as pages:
        return any(pages.label(i + 1) != pages.mupdf_label(i + 1) for i in indexes if isinstance(i, int))


def label_checks(r: Round, conn) -> set[str]:
    """The pages to look at once for the page labels of their annotations
    (``blocks_dirty``, ``Blocks._label_due``): those with a live row of an
    annotation Gamma made (``_made_here``) whose label is neither settled
    nor queued for it (no ``detail.labels``), each such row marked queued
    (``labels`` 0) so its page comes once; a live row of Zotero's own
    annotation without it is settled here. The caller queues the pages, in
    its write transaction (``conn`` holds the write lock)."""
    found = conn.execute("SELECT key, page_id, parent, block_id, slot FROM zotero_objects WHERE kind = 'annotation' "
                         "AND state = 'live' AND page_id != '' AND json_extract(detail, '$.labels') IS NULL").fetchall()
    ours = {key: page for key, page, parent, block_id, slot in found
            if _made_here(r, {"key": key, "parent": parent, "block_id": block_id, "slot": slot})}
    theirs = [row[0] for row in found if row[0] not in ours]
    for value, keys in ((0, list(ours)), (zotero_annotations.LABELS, theirs)):
        if keys:
            conn.execute("UPDATE zotero_objects SET detail = json_set(detail, '$.labels', ?) "
                         "WHERE key IN (SELECT value FROM json_each(?))", (value, json.dumps(keys)))
    return set(ours.values())


# --- removals for good -------------------------------------------------------------------

def _gone_view(conn, page_id: str, rows):
    """The page as ``children_gone`` decides on it: the rows' note regions,
    their pictures by the page's image rows."""
    roots = [row["block_id"] for row in rows if row["kind"] == "note" and row["slot"] != "page" and row["block_id"]]
    return zotero_local.read_page(conn, page_id, note_roots=roots, image_key=_image_names(conn, page_id).get)


def _gone_plan(view, page_id: str, rows, *, keep: bool = False) -> tuple[list, list, list, dict]:
    """``children_gone``'s decision on ``view``: ``(plans, fresh, dropped,
    doomed)``, ``doomed`` what each block or region that goes was decided
    on (its signature; a region's hash, blocks and kept blocks), which the
    write's compare-and-set reads again; ``keep``: none goes, each block
    still here goes to Zotero again as new."""
    blocks = {a.block["id"]: a for a in view.annotations} if view else {}
    plans, fresh, dropped, doomed = [], [], [], {}
    for row in rows:
        dropped.append(row["key"])
        if row["state"] in ("trashed", "waiting", "skipped", "rejected") or view is None:
            continue
        if row["kind"] == "annotation":
            ann = blocks.get(row["block_id"])
            if ann is None:
                continue
            sig = zotero_annotations.block_sig(ann.block, ann.nested)
            if not keep and sig == row["gsig"]:
                if not any(x["block_id"] == row["block_id"] and x["key"] != row["key"] for x in rows):
                    doomed[row["key"]] = sig
                    plans.append(lambda conn, bid=row["block_id"]: zotero_local.annotation_delete(conn, bid))
            else:
                fresh.append(new_row(random_key(), "annotation", page_id=page_id, block_id=row["block_id"],
                                     slot=row["slot"], parent=row["parent"], zotero_type=row["zotero_type"],
                                     version=-1, state="pending", detail={"sent": {}}))
            continue
        region = view.gamma_notes if row["slot"] == "page" else view.regions.get(row["block_id"])
        if region is None:
            continue
        if not keep and note_hash(region.html) == (row["gbase"] or {}).get("note"):
            doomed[row["key"]] = [note_hash(region.html), [bid for bid, _, _ in region.blocks], sorted(region.kept)]
            edits = zotero_merge.note_removal(region)
            plans.append(lambda conn, region=region, edits=edits: zotero_local.note_ops(conn, region, edits).ops)
        elif row["slot"] != "page":
            fresh.append(new_row(random_key(), "note", page_id=page_id, block_id=row["block_id"], parent=row["parent"],
                                 zotero_type="note", version=-1, state="pending", detail={"sent": {}}))
    return plans, fresh, dropped, doomed


def children_gone(r: Round, page_id: str, rows) -> None:
    """Notes and annotations Zotero removed for good, on one page: a block (a
    region) unchanged since it last synced goes, what Gamma keeps under it
    lifted to its place; one changed here stays and goes to Zotero again as a
    new object under a fresh key. The write's compare-and-set reads again what
    each block that goes was decided on: one changed meanwhile and nothing is
    written, the removal decided again on the page as it is then
    (``GONE_READS`` times at most; then nothing goes, each block still here
    going to Zotero again as new)."""
    rows = list(rows)
    for attempt in range(GONE_READS + 1):
        with connect_pages_db(r.ws) as conn:
            view = _gone_view(conn, page_id, rows)
        plans, fresh, dropped, doomed = _gone_plan(view, page_id, rows, keep=attempt == GONE_READS)

        def rows_tx(conn, fresh=fresh, dropped=dropped):
            drop_rows(conn, dropped)
            write_rows(conn, fresh)
            zr.enqueue(conn, [zr.page_entry(page_id)] if fresh else [])

        def check(conn, doomed=doomed):
            return _gone_plan(_gone_view(conn, page_id, rows), page_id, rows)[3] == doomed

        try:
            zotero_local.apply(r.ws, page_id, lambda conn, plans=plans: [op for plan in plans for op in plan(conn)],
                               check=check if plans else None, rows=rows_tx)
        except zotero_local.Changed:  # edited as it went: decided again on the page as it is now
            log.info(f"[zotero] {r.ws}: {page_id} changed while Zotero's removal was decided: decided again")
            continue
        except (zotero_local.Moved, OpError) as e:
            log.warning(f"[zotero] {r.ws}: {page_id}: what Zotero removed for good stayed: {e}")
            with connect_pages_db(r.ws) as conn:
                write_lock(conn)
                rows_tx(conn)
                conn.commit()
            return
        if plans:
            page_note(r, page_id, "deleted here")
        return


# --- resolving conflicts -----------------------------------------------------------------

def _note_into_region(ws: str, key: str, html: str, *, client: str) -> str:
    """Zotero's note ``html`` (of the note row ``key``) into its region as a
    block-level diff (``zotero_merge.note_edits``), its pictures by the image
    rows or downloaded, as actor ``ACTOR`` and ``client``; the region's HTML
    after. ValueError when the region is gone."""
    with connect_pages_db(ws) as conn:
        row = read_rows(conn, "key = ?", (key,)).get(key)
        if row is None or row["kind"] != "note" or not row["page_id"]:
            raise ValueError("the note is no longer kept here")
        images = read_rows(conn, "kind = 'image' AND parent = ?", (key,))
    known = {k: x["detail"].get("name") for k, x in images.items() if x["detail"].get("name")}
    link = get_link(ws) or {}
    client_ = None

    def fetch(picture):
        nonlocal client_
        name = known.get(picture)
        if name and storage.open_path(ws, name):
            return name
        try:
            client_ = client_ or zotero_account.client_for(link.get("owner") or "")
            return zr.download_image(ws, client_, link.get("library") or "", picture)
        except (zr.FileUnavailable, AccountError, ZoteroError):
            return None

    pics = zotero_local.resolve_pictures(html, fetch)
    page_id = row["page_id"]

    def region_of(view):
        if view is None:
            return None
        if row["slot"] == "page":
            return view.gamma_notes or _empty_region(page_id)
        return view.regions.get(row["block_id"])

    def read(conn):
        names = _image_names(conn, page_id)
        names.update({name: k for k, name in known.items()})
        roots = [row["block_id"]] if row["slot"] != "page" else []
        return zotero_local.read_page(conn, page_id, note_roots=roots, image_key=names.get)

    with connect_pages_db(ws) as conn:
        region = region_of(read(conn))
    if region is None:
        raise ValueError("the note's blocks are no longer on the page")
    edits = [e for e in zotero_merge.note_edits(region, pics.html) if e["op"] != "lossy"]
    after = {}

    def rows(conn):
        found = region_of(read(conn))
        after["html"] = found.html if found else ""

    zotero_local.apply(ws, page_id, lambda conn: zotero_local.note_ops(conn, region, edits, pictures=pics).ops,
                       rows=rows, actor=ACTOR, client=client)
    return after.get("html", "")


def _refuse_moved_on(ws: str, key: str, was, fields) -> None:
    """ValueError while Zotero's side of the row ``key`` moved on since its
    conflict was recorded (``was``: that side then, the conflict's
    ``base.zbase``; an older conflict has none and is never refused) in one
    of ``fields``: "theirs" would write the conflict's value back over
    Zotero's newer one, which came here meanwhile."""
    if not isinstance(was, dict):
        return
    with connect_pages_db(ws) as conn:
        found = read_rows(conn, "key = ?", (key,)).get(key)
    if found is not None and any((found["zbase"] or {}).get(f) != was.get(f) for f in fields):
        raise ValueError(MOVED_ON)


def resolve_note(ws: str, row: dict, choice: str) -> None:
    """``zotero_note`` "theirs": Zotero's note (kept in ``base``) into the
    region, written as a person's edit would be (client ""), so the next round
    sends it back and both sides hold Zotero's. Refused while Zotero changed
    the note again since the conflict (its newer note came here then; the
    row stays open, to keep)."""
    if choice != "theirs":
        raise ValueError("this conflict offers keep or theirs")
    base = json_dict(row["base"])
    key = str(base.get("key") or "")
    _refuse_moved_on(ws, key, base.get("zbase"), ("note",))
    _note_into_region(ws, key, str(base.get("note") or ""), client="")


def resolve_lossy(ws: str, row: dict, choice: str) -> None:
    """``zotero_lossy``: "mine" sends Gamma's note anyway at the next round
    (the row's ``detail.force``, spent once it went); "theirs" takes Zotero's
    into the region (or into the one block Gamma could not set) as the link
    writes, the bases renewed, so nothing travels after it."""
    base = json_dict(row["base"])
    key = str(base.get("key") or "")
    if choice == "mine":
        with connect_pages_db(ws) as conn:
            write_lock(conn)
            found = read_rows(conn, "key = ?", (key,)).get(key)
            if found is None:
                raise ValueError("the note is no longer kept here")
            found["detail"] = {**found["detail"], "force": True}
            write_rows(conn, [found])
            zr.enqueue(conn, [zr.page_entry(found["page_id"])])
            conn.commit()
        sync_clock.get(ENGINE).request(ws, 0)  # the link's clock: a round, at once
        return
    if base.get("block"):
        zotero_local.apply(ws, row["page_id"], [{"op": "set", "id": base["block"],
                                                 "content": str(base.get("content") or "")}], actor=ACTOR,
                           client=CLIENT)
        return
    html = str(base.get("note") or "")
    after = _note_into_region(ws, key, html, client=CLIENT)
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        found = read_rows(conn, "key = ?", (key,)).get(key)
        if found is not None:  # (a note page's bases hold its tags and collections too)
            found.update(zbase={**found["zbase"], "note": note_hash(html)},
                         gbase={**found["gbase"], "note": note_hash(after)})
            write_rows(conn, [found])
        conn.commit()


def resolve_annotation(ws: str, row: dict, choice: str) -> None:
    """``zotero_annotation`` "theirs": Zotero's values (kept in ``base``) into
    the block, field by field as a pull writes them, as a person's edit would
    be (client ""), so the next round sends them back. Refused while Zotero
    changed one of them again since the conflict (as ``resolve_note``)."""
    if choice != "theirs":
        raise ValueError("this conflict offers keep or theirs")
    base = json_dict(row["base"])
    key, Z = str(base.get("key") or ""), base.get("z") or {}
    _refuse_moved_on(ws, key, base.get("zbase"), [f for f in Z if f != "type"])
    with connect_pages_db(ws) as conn:
        found = read_rows(conn, "key = ?", (key,)).get(key)
        view = zotero_local.read_page(conn, found["page_id"], notes=False) if found and found["page_id"] else None
    ann = next((a for a in (view.annotations if view else ()) if found and a.block["id"] == found["block_id"]), None)
    if ann is None or not view.doc_id:
        raise ValueError("the annotation is no longer on its page")
    path = storage.open_path(ws, f"{view.doc_id}.pdf")
    if not path:
        raise ValueError("the page's PDF is not here")
    props = ann.block.get("properties") or {}
    ink = zotero_local.read_ink(ws, props) if "ink_url" in props else None
    with zotero_annotations.Pages(str(path)) as pages:
        n = (Z.get("position") or {}).get("pageIndex")
        page = pages.page(n + 1) if isinstance(n, int) else pages.page(zotero_annotations.page_number(props, ink))
        if page is None:
            raise ValueError("the annotation's page is not in the PDF")
        change = zotero_annotations.apply_fields(
            ann.block, page, {f: v for f, v in Z.items() if f != "type"}, ink_file=ink,
            stroke_ids=found["detail"].get("stroke_ids") or (), nested=ann.nested,
            zotero_type=found["zotero_type"] or None, key=key)
    if change.get("conflicts") and "comment" in change["conflicts"] and Z.get("comment") is not None:
        change["content"] = zotero_annotations.comment_in(str(Z["comment"]))  # the whole comment as its note
    op = zotero_local.annotation_update(ws, ann.block["id"], change)
    if op:
        zotero_local.apply(ws, found["page_id"], [op], actor=ACTOR, client="")
