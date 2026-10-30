"""An item's unit in a Zotero link's round (see
docs/dev/zotero_sync.md#items-and-pages): gamma/zotero_sync.py takes a
top-level item or a child off the queue (``unit_item``, ``unit_child``) and
this module reconciles the item with the page it is kept with (``_Unit``,
through ``reconcile``).

The unit reconciles the record, tags and collections field by field with
the page and the trash both ways (an edit beats a delete); an item Gamma
has no page of gets one, unless one of its attachments is a standalone
PDF's page, which it is bound to instead. Its PDF is
gamma/zotero_sync_pdfs.py's part of the unit (``UnitPdf``), its notes and
annotations gamma/zotero_sync_blocks.py's (``Blocks``); those Zotero moved
to another item (Merge Items) move with their blocks first. The unit
writes Gamma's side in one transaction under a compare-and-set on its
page, Zotero's right after, then records each write's outcome on its
rows; conflicts and log lines go after the commits.

Of the engine's parts it imports gamma/zotero_sync_rows.py,
gamma/zotero_sync_reads.py, gamma/zotero_sync_blocks.py,
gamma/zotero_sync_pdfs.py and gamma/zotero_sync_gone.py.
"""

import json

from . import zotero_fields, zotero_local, zotero_merge
from . import zotero_remote as zr
from .bibtex import build_entry, pinned_key
from .blocks_store import BLOCK_COLUMNS, FOLDERS, LABELS, block_to_dict, label_names, new_block_id, write_lock
from .db import connect_pages_db, page_now
from .zotero_notes import note_tree
from .zotero_sync_blocks import (NOTE_MAX, NOTE_TOO_LONG, NOTES_WAIT, WAIT_FILE, Blocks, note_page_html,
                                 note_page_parts, note_text, note_words)
from .zotero_sync_gone import absent
from .zotero_sync_pdfs import UnitPdf
from .zotero_sync_reads import (HELD_ROWS, Side, collection_map, flat_fields, gamma_item, gamma_note, gamma_standalone,
                                moving_pdf, page_side, read_unit, take_ahead, zotero_item, zotero_note,
                                zotero_standalone)
from .zotero_sync_rows import (KEPT_HERE, STOPS_ROUND, Round, drop_rows, field_base, new_row, page_note, read_rows,
                               record_conflict, signature, without, write_rows)
from .zotero_web import ZoteroError, item_uri, random_key

RECORD_TEXTS = ("title", "venue", "publisher")  # record fields both sides edit apart, merged as text
# The writes of a unit's notes and annotations (``Blocks.record`` settles their outcomes)
_BLOCK_WRITES = ("ann_create", "ann_update", "ann_restore", "ann_trash", "note_create", "note_update", "note_restore",
                 "note_trash")

# What a conflict row of a kind the blocks record says was done (``sync_conflicts.result``)...
_CONFLICT_RESULTS = {
    "zotero_note": "The note changed on both sides: Gamma's went to Zotero",
    "zotero_annotation": "The annotation changed on both sides: Gamma's went to Zotero",
    "zotero_lossy": "Zotero's note holds what Gamma cannot write back: this stays in Gamma",
}
# ...one a receive-only round recorded, Gamma's side won and not sent...
_KEPT_RESULTS = {
    "zotero_note": f"The note changed on both sides: {KEPT_HERE}",
    "zotero_annotation": f"The annotation changed on both sides: {KEPT_HERE}",
}
# ...and a resolved one whose pair (a first link's, a force pull's) took Zotero's side
_TAKEN_RESULTS = {
    "zotero_note": "The note differed from Zotero's: Zotero's was taken here",
    "zotero_annotation": "The annotation differed from Zotero's: Zotero's was taken here",
}
_is_pdf = zotero_merge.is_pdf_attachment  # a PDF attachment of a file link mode


class _Unit(UnitPdf):
    """One item's unit: Zotero's top-level item read whole with its
    children, or a standalone PDF or note, and the page it is kept with. It
    decides field by field what each side takes, then the PDF (``UnitPdf``'s
    part, gamma/zotero_sync_pdfs.py), writes Gamma's side in one transaction
    under a compare-and-set on the page and Zotero's right after, then
    records each write's outcome on its rows; conflicts and log lines go
    after the commits.

    A row's ``zbase`` / ``gbase`` are the two projections (an item's
    ``item_projection`` form, a standalone PDF's ``{title, tags,
    collections}``, a standalone note's ``{tags, collections, note}``); an
    attachment row's file is in its ``detail``: ``md5`` (Zotero's file as
    last synced), ``gmd5`` (Gamma's), ``doc_id``, ``fsig`` (the stored
    file's ``[size, mtime_ns]`` when ``gmd5`` was read), ``file`` (why it
    waits, when it does), ``size``, ``filename``, ``link_mode``."""

    def __init__(self, r: Round, unit: zr.Unit):
        self.r = r
        self.obj = unit.item
        self.key = unit.item["key"]
        self.data = unit.item.get("data") or {}
        self.version = int(unit.item.get("version") or 0)
        self.children = [c for c in unit.children if isinstance(c, dict) and c.get("key")]
        self.kids = {c["key"]: c for c in self.children}
        self.standalone = self.data.get("itemType") == "attachment"
        self.trashed = bool(self.data.get("deleted"))
        self.rows: dict[str, dict] = {}
        self.side: Side | None = None
        self.cmap: dict[str, str] = {}
        self.inverse: dict[str, str] = {}
        self.page_id = ""
        self.change = ""              # what the page undergoes: "" | "create" | "trash" | "restore"
        self.new_page: dict = {}      # a page to make: {page_id, title, props, trees}
        self.ops: list[dict] = []     # ops on the page
        self.labels = None            # the names to file the page under (Zotero's spelling), None: as they are
        self.folders = None           # the folder ids to file it in, None: as they are
        self.out: dict[str, dict] = {}  # rows as the local part writes them
        self.dropped: set[str] = set()
        self.writes = zr.Writes(r.client, r.library)
        self.sent: dict[str, dict] = {}  # key -> what was sent and what it was decided on
        self.queue: list[dict] = []      # entries to add once the local part is written
        self.conflicts: list[tuple] = []  # (kind, ...): what ``finish`` records, by kind
        self.notes: list[str] = []       # the page's log lines, by action ("kept": none)
        self.counts: dict[str, int] = {}
        self.merged: dict | None = None  # the merge the local part applies: its bases are taken after it
        self.G_after = None              # Gamma's item projection re-read after the local part
        self.moved: dict[str, dict] = {}  # attachment rows another page held until now, by key
        self.retry = False               # a write did not land: the page is looked at again a while later
        self.refetch = False             # Zotero changed an object as it was written: decided again
        self.note = self.data.get("itemType") == "note"  # a standalone note: a note page
        self.first_link = False          # the item's row is a first link's (or a force's) pair
        self.blocks: Blocks | None = None  # its notes and annotations
        self.blocks_ops = False          # they make ops on the page
        self.note_pull = False           # a note page takes Zotero's note (a block-level diff)
        self.note_html = ""              # ...or sends its own (``_note_merge``)
        self._page_html: str | None = None  # a note page's note as Gamma would send it, read once (``page_html``)
        self._before: dict = {}          # the page's change number as the write's transaction found it (``check``)
        self._labels_made: set = set()   # the labels the write makes (its ``trees``), known to the round as made

    # reading

    def read(self) -> None:
        for _ in range(2):
            with connect_pages_db(self.r.ws) as conn:
                self.rows = read_rows(conn, "key IN (SELECT value FROM json_each(?))",
                                      (json.dumps([self.key, *self.kids]),))
                row = self.rows.get(self.key)
                self.page_id = row["page_id"] if row else self._parentless_page(conn)
                if self.page_id:
                    self.rows.update(read_rows(conn, "page_id = ?", (self.page_id,)))
            if not self._take_children():
                break
        with connect_pages_db(self.r.ws) as conn:
            self.cmap = collection_map(conn)
            self.side = page_side(conn, self.r.ws, self.page_id, note=self.note) if self.page_id else None
            if self.side is not None and (self.side.live or (self.note and self.side.block is not None)):
                self.blocks = Blocks(self)  # a note page in Recently deleted too: a restore takes Zotero's note
                self.blocks.read(conn)
        self.inverse = {fid: key for key, fid in self.cmap.items()}

    def _take_children(self) -> bool:
        """Notes and annotations Zotero moved to this item from another one
        (Merge Items, a note dragged to it) whose blocks are on that item's
        page: each block moves here with its subtree before anything is
        decided, before the other item's trash; one whose block cannot move
        (gone, its page in Recently deleted) is brought from Zotero instead.
        True when a row changed."""
        if not self.page_id:
            return False
        mine = {k for k, row in self.rows.items() if row["kind"] == "attachment" and row["page_id"] == self.page_id}
        if self.standalone:
            mine.add(self.key)
        moved = []
        for key, child in self.kids.items():
            row = self.rows.get(key)
            if row is None or row["kind"] not in ("note", "annotation") or row["page_id"] in ("", self.page_id):
                continue
            parent = (child.get("data") or {}).get("parentItem") or ""
            if (row["kind"] == "note" and parent == self.key) or (row["kind"] == "annotation" and parent in mine):
                moved.append(dict(row, parent=parent))
        return self._move_here(moved)

    def _move_here(self, moved) -> bool:
        for row in moved:
            whole = row["kind"] == "annotation" or row["slot"] != "page"
            if whole and row["block_id"] and row["state"] not in ("trashed", "waiting", "skipped"):
                new = {**row, "page_id": self.page_id}
                try:
                    zotero_local.move_block_across(self.r.ws, row["block_id"], self.page_id,
                                                   rows=lambda conn, new=new: write_rows(conn, [new]))
                    page_note(self.r, self.page_id, "pulled")
                    continue
                except zotero_local.Moved:
                    pass
            with connect_pages_db(self.r.ws) as conn:  # nothing to move: Zotero's object comes here anew
                write_lock(conn)
                write_rows(conn, [{**row, "page_id": self.page_id, "block_id": "", "slot": "",
                                   "state": "waiting", "detail": {**row["detail"], "reason": WAIT_FILE}}])
                conn.commit()
        return bool(moved)

    def _rehome(self) -> None:
        """Before the page goes to Recently deleted with its item: its notes
        and annotations Zotero moved to another item's (Merge Items) move to
        that item's page first, so they never go with it."""
        gone = [row for row in self.rows.values() if row["kind"] in ("note", "annotation")
                and row["page_id"] == self.page_id and row["key"] not in self.kids
                and row["state"] not in ("trashed", "pending", "rejected")]
        if not gone:
            return
        found = zr.fetch_items(self.r.client, self.r.library, [row["key"] for row in gone]).objects
        with connect_pages_db(self.r.ws) as conn:
            parents = read_rows(conn, "key IN (SELECT value FROM json_each(?))", (json.dumps(sorted(
               {str((obj.get("data") or {}).get("parentItem") or "") for obj in found.values()})),))
        moved = False
        for row in gone:
            obj = found.get(row["key"])
            parent = parents.get(str(((obj or {}).get("data") or {}).get("parentItem") or ""))
            if parent is None or not parent["page_id"] or parent["page_id"] == self.page_id:
                continue
            target = parent["page_id"]
            try:
                zotero_local.move_block_across(self.r.ws, row["block_id"], target, rows=lambda conn, new={
                    **row, "page_id": target, "parent": parent["key"]}: write_rows(conn, [new]))
                moved = True
            except zotero_local.Moved:
                continue
        if moved:
            with connect_pages_db(self.r.ws) as conn:
                self.side = self.side._replace(stamp=zotero_local.stamps(conn, [self.page_id]))

    def _parentless_page(self, conn) -> str:
        """For an item with no row: a page one of its attachments is the PDF
        of that has no item (a standalone PDF that Zotero's Create Parent
        Item or Retrieve Metadata put under it): the item is bound to that
        page instead of making a second one."""
        if self.standalone:
            return ""
        for key, row in self.rows.items():
            if (row["kind"] == "attachment" and row["page_id"] and key in self.kids
                    and not conn.execute("SELECT 1 FROM zotero_objects WHERE kind = 'item' AND page_id = ?",
                                         (row["page_id"],)).fetchone()):
                page = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (row["page_id"],)).fetchone()
                if page and page[0] == "root":
                    return row["page_id"]
        return ""

    @property
    def row(self) -> dict | None:
        return self.out.get(self.key) or self.rows.get(self.key)

    def bound(self) -> dict | None:
        """The attachment row that is the page's PDF (a standalone PDF's is its own row)."""
        if self.standalone:
            return self.row
        found = [row for row in self.rows.values() if row["kind"] == "attachment" and row["page_id"] == self.page_id
                 and row["state"] in HELD_ROWS and row["key"] not in self.dropped]
        return (self.out.get(found[0]["key"]) or found[0]) if found else None

    def Z(self, data: dict | None = None) -> dict:
        """Zotero's projection of the item (``data``: as Zotero saved it)."""
        data, live = self.data if data is None else data, self.cmap.keys()
        if self.note:
            return zotero_note(self.r, data, live)
        return zotero_standalone(self.r, data, live) if self.standalone else zotero_item(self.r, data, live)

    def G(self, side: Side | None = None, *, item_type: str | None = None) -> dict:
        """Gamma's projection of the page (``side``: as read again) against
        an item of ``item_type`` (None: Zotero's type now). What is compared
        with ``gbase`` is read against the type ``gbase`` was taken for (the
        row's ``zotero_type``), so a retype in Zotero never reads as a change
        of Gamma's page; a new base, against Zotero's type now."""
        side = side or self.side
        if self.note:
            return gamma_note(side, self.inverse)
        if self.standalone:
            return gamma_standalone(side, self.inverse)
        return gamma_item(side, self.inverse, self.data.get("itemType") or "" if item_type is None else item_type)

    def G_synced(self, row: dict) -> dict:
        """``G`` against the type the row last synced as."""
        return self.G(item_type=row["zotero_type"] or self.data.get("itemType") or "")

    def _put(self, row: dict) -> None:
        row["synced_at"] = page_now()
        self.out[row["key"]] = row
        self.dropped.discard(row["key"])

    def _drop(self, key: str) -> None:
        self.dropped.add(key)
        self.out.pop(key, None)

    def _count(self, name: str, n: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + n

    # deciding

    def decide(self) -> None:
        row = self.rows.get(self.key)
        self.first_link = bool(row and row["detail"].get("first"))
        if self.note and not self.r.access.get("notes"):
            return  # a key without notes access: notes travel in neither direction
        self._decide_item()
        self._children()

    def _children(self) -> None:
        """The notes and annotations (``Blocks``) once the item is decided:
        a new page takes its notes in its tree, its annotations wait for its
        PDF; a page going to Recently deleted lets what Zotero moved away go
        first; a page brought back is looked at again next."""
        if not self.page_id:
            return
        if self.change == "create":
            self.blocks = Blocks(self)
            if self.note:
                self.new_page["tree"] = self.blocks.note_page(pull=False)
            else:
                self.new_page["tree"] = self.blocks.new_page()
            return
        if self.change == "trash":
            self._rehome()
            return
        if self.change == "restore" and not self.note:
            self.queue.append({"id": self.key, "kind": "top", "version": self.version})
            return
        if self.blocks is None or self.side is None or (not self.side.live and self.change != "restore"):
            return
        if self.note:
            self.blocks.note_page(pull=self.note_pull)
            self.blocks_ops = bool(self.blocks.ops or self.blocks.plans)
        else:
            self.blocks.decide()

    def _decide_item(self) -> None:
        row = self.rows.get(self.key)
        if row is None:
            self._new()
            return
        row = dict(row)
        if row["state"] == "pending":  # our create, which Zotero now names: it held what was sent
            sent = row["detail"].get("sent") or {}
            row.update(state="live", zbase=sent, gbase=sent, detail=without(row["detail"], "sent"))
        if self.standalone and row["parent"]:
            # a child attachment made standalone: its old page keeps its file, a page of its own comes
            self._drop(self.key)
            self.rows.pop(self.key, None)
            self.queue.append({"id": row["parent"], "kind": "top"})
            self.page_id, self.side = "", None
            self._new()
            return
        side = self.side
        first = bool(row["detail"].get("first"))
        # a first link's pair or a force takes ``adopt``'s side of the trash too: "mine" Gamma's, "theirs" Zotero's
        adopt = (row["detail"].get("adopt") or self.r.adopt or "theirs") if first else ""
        if side is None or side.block is None:
            self._page_gone(row, first, adopt)
        elif side.trashed:
            self._page_trashed(row, first, adopt)
        elif self.trashed:
            self._zotero_trashed(row, first, adopt)
        else:
            self._merge(row)  # (a row still trashed is live again: both sides are)
            self._file()

    def _zotero_changed(self, row: dict) -> bool:
        """Whether Zotero edited the item since it last synced: a field of its
        own, or a child that travels (its PDF's file, a note, an annotation of
        its PDF: one the link keeps changed or brought back from Zotero's
        trash, or a new one), which counts as an edit of the item when an
        edit is to beat a delete. (Its page out of the way here, the round
        cannot take a child's change in, so it must not pass it by.)"""
        if self.Z() != row["zbase"]:
            return True
        pdf = self._primary()
        for key, child in self.kids.items():
            data, kept = child.get("data") or {}, self.rows.get(key)
            if kept is None:  # a new note of the item, a new annotation of its PDF
                if not data.get("deleted") and (
                        (data.get("itemType") == "note" and data.get("parentItem") == self.key)
                        or (data.get("itemType") == "annotation" and pdf is not None
                            and data.get("parentItem") == pdf["key"])):
                    return True
                continue
            if kept["state"] in ("skipped", "rejected", "pending") or int(kept["version"]) <= 0:
                continue
            if int(child.get("version") or 0) == int(kept["version"]):
                continue
            if kept["state"] == "trashed" and data.get("deleted"):
                continue  # in both trashes still
            return True
        return False

    def _page_gone(self, row: dict, first: bool, adopt: str = "") -> None:
        """The page went for good (purged from Recently deleted)."""
        if self.trashed:  # (both sides agree: a first link's, a force's or a restore's pair is decided)
            row.update(state="trashed", version=self.version, zbase=self.Z(),
                       detail=without(row["detail"], "first", "adopt", "restore"))
            self._put(row)
        elif adopt != "mine" and (row["state"] == "trashed" or first or self._zotero_changed(row)):
            self._make_page(row)  # Zotero restored or edited it: the page anew, the rows on it
        elif self.r.pushes:
            self.writes.trash("items", self.key, self.version)
            self.sent[self.key] = {"what": "trash"}

    def _page_trashed(self, row: dict, first: bool, adopt: str = "") -> None:
        """The page is in Recently deleted."""
        if self.trashed:  # in both trashes: what Zotero changed meanwhile keeps reading as changed (its base
            # stays), so a restore on either side applies it; a first link's, a force's or a restore's pair is
            # decided
            row.update(state="trashed", version=self.version,
                       detail=without(row["detail"], "first", "adopt", "restore"))
            self._put(row)
            return
        if adopt != "mine" and (row["state"] == "trashed" or first or self._zotero_changed(row)):
            # Zotero restored it, or edited it after the page went: an edit beats a delete
            if row["state"] != "trashed" and not first:
                self.conflicts.append(("zotero_deleted_edited", self.page_id, "(moved to Recently deleted here)",
                                       "Zotero changed this item after its page was deleted here: the page is back"))
            self.change = "restore"
            row["state"] = "live"
            self.notes.append("restored here")
            self._merge(row)
            self._file()
        elif self.r.pushes:
            self.writes.trash("items", self.key, self.version)
            self.sent[self.key] = {"what": "trash"}

    def _zotero_trashed(self, row: dict, first: bool, adopt: str = "") -> None:
        """The page is live and Zotero's item is in its trash."""
        G = self.G_synced(row)
        if adopt == "theirs":  # Zotero's version, its trash with it: the page to Recently deleted, nothing sent
            # (the bases stay: what either side changed since keeps reading as changed once it comes back)
            row["detail"] = without(row["detail"], "first", "adopt")
            self.r.decided["records"] = self.r.decided.get("records", 0) + 1
        elif row["state"] == "trashed" or first:
            # the page came back here (an undo, a restore), or Gamma's version is taken: back in Zotero too
            if self.r.pushes:
                row["state"] = "live"
                self._merge(row, restore=True)
                self._file()
            else:
                self._receive_trashed(row)
            return
        elif G != row["gbase"] or row["detail"].get("restore"):
            # changed here since it synced (or restored from a backup since): an edit beats a delete
            self.conflicts.append(("zotero_deleted_edited", self.page_id, "(changed here)",
                                   "Zotero moved this item to its trash after it was changed here: it is kept"))
            if self.r.pushes:
                self._merge(row, restore=True)
                self._file()
            else:
                self._receive_trashed(row)
            return
        # to Recently deleted here: Zotero's changes made with the trash keep reading as changed (the base
        # stays), so the page takes them once it comes back on either side
        row.update(state="trashed", version=self.version)
        self._put(row)
        self.change = "trash"
        self.notes.append("deleted here")
        self._count("pages_deleted")

    def _receive_trashed(self, row: dict) -> None:
        """Receive only, the page live here and its item in Zotero's trash
        (the page came back or changed here meanwhile): Zotero's changes
        come into the page; the row stays trashed, so the round that may
        send takes the page back out of Zotero's trash with Gamma's
        changes."""
        self._merge(row)
        self.out[self.key]["state"] = "trashed"

    def _new(self) -> None:
        """An item Gamma has no row of: a page for it (or, for an item whose
        PDF is a standalone page's, that page), unless it is in the trash or
        nothing Gamma keeps (a standalone note waits while the key has no
        notes access, an attachment that is no PDF stays in Zotero)."""
        if self.trashed or (self.data.get("itemType") == "note" and not self.r.access.get("notes")):
            return
        if self.standalone and not _is_pdf(self.data):
            return
        if self.note:
            self._make_page(None)
            return
        if self.page_id and self.side is not None and self.side.live:  # Create Parent Item
            row = new_row(self.key, "item", page_id=self.page_id, block_id=self.page_id,
                          zotero_type=self.data.get("itemType") or "", version=0,
                          detail={"first": True, "adopt": "theirs"})
            named = ""
            for key, att in self.rows.items():
                if att["kind"] == "attachment" and att["page_id"] == self.page_id:
                    named = named or (att["zbase"] or {}).get("title") or ""
                    att = dict(att)
                    att.update(parent=self.key, zbase={}, gbase={})
                    self._put(att)
            self.notes.append("pulled")
            self._merge(row)
            title = zotero_fields.meta_from_item(self.data).get("title") or ""
            if title and named and " ".join(self.side.title.split()) == named:  # named after the PDF until now
                self.ops.append({"op": "set", "id": self.page_id, "content": title})
            self._file()
            return
        self._make_page(None)

    def _make_page(self, row: dict | None) -> None:
        """A page for the item (or the standalone PDF) as Zotero holds it, in
        one transaction with its rows; ``row``: the item's row when the page
        went for good here (it is pointed at the new page)."""
        data, page_id = self.data, new_block_id()
        names = list(zotero_merge.tag_names(data, auto_tags=self.r.link["auto_tags"]).values())
        folders = [self.cmap[k] for k in data.get("collections") or () if k in self.cmap]
        props = {"zotero_key": item_uri(self.r.library, self.key), FOLDERS: folders}
        if self.standalone:
            title = " ".join(str(data.get("title") or data.get("filename") or "").split()) or "Untitled"
        elif self.note:
            tree = note_tree(str(data.get("note") or ""))
            title = note_page_parts(tree)[0] if tree else "Untitled"
            props["zotero_note"] = props["zotero_key"]
        else:
            meta = zotero_fields.meta_from_item(data)
            cite_key = zotero_fields.cite_key_from_item(data)
            title = meta.get("title") or "Untitled"
            props.update(meta=meta, bibtex=build_entry(meta, cite_key))
            if cite_key:
                props["cite_key"] = cite_key
        with connect_pages_db(self.r.ws) as conn:
            filed = zotero_local.filing_ops(conn, {"id": page_id, "properties": {}}, labels=names)
            known = label_names(conn)
        label_ids = (filed.op or {}).get("props", {}).get(LABELS) or []
        new_names = {op["id"]: op["content"] for _, tree_ops in filed.trees for op in tree_ops}
        if label_ids:
            props[LABELS] = label_ids
        self.page_id = page_id
        self.change = "create"
        self.new_page = {"page_id": page_id, "title": title, "props": props, "trees": filed.trees}
        labels = {lid: known.get(lid) or new_names.get(lid, "") for lid in label_ids}
        self.side = Side(page_id, {"id": page_id, "parent_id": "root", "content": title, "properties": props},
                          False, {}, labels, folders, "", "", False)
        Z = self.Z()
        if row is None:
            row = new_row(self.key, "attachment" if self.standalone else "note" if self.note else "item", parent="",
                          zotero_type=data.get("itemType") or "")
        else:
            row = dict(row)
        row.update(page_id=page_id, block_id=page_id, version=self.version, state="live", zbase=Z, gbase=self.G(),
                   gsig=signature(self.G()), detail={**without(row["detail"], "first", "adopt", "restore", "rejected",
                                                             "reason"),
                                                     "collections": list(data.get("collections") or [])})
        self._put(row)
        for key, other in self.rows.items():  # the rows of the page that went (a page made anew)
            if key != self.key and other["page_id"] and other["kind"] in ("attachment", "note", "annotation", "image"):
                self._drop(key)
        self.notes.append("created here" if self.rows.get(self.key) is None else "restored here")
        self._count("pages_pulled")
        self._file()

    def _merge(self, row: dict, *, restore: bool = False) -> None:
        """Field by field (see docs/dev/zotero_sync.md#merging): Zotero's
        changes into the page, Gamma's to Zotero (two-way rounds), both apart →
        Gamma's wins and a ``zotero_field`` row keeps Zotero's (a first link's
        pair: ``adopt``'s side, resolved). ``restore``: the item comes out of
        Zotero's trash with it. A note page reads its note as ``Blocks._note``
        reads a child note's: at a first link (or a force) it agrees with
        Zotero's when their words do, whatever shape the HTML takes; restored
        from a backup (``detail.restore``), what Zotero changed since the
        backup reads as changed here too, so the restored page wins it and
        Zotero's value stays in its conflict, unless it is the note and it
        reads as the page does (settled)."""
        r = self.r
        Z, G = self.Z(), self.G_synced(row)
        first = bool(row["detail"].get("first"))
        if first:
            zbase, gbase = zotero_merge.first_link_base("item", Z, G)
            prefer = "zotero" if (row["detail"].get("adopt") or r.adopt or "theirs") == "theirs" else "gamma"
            if self.note and zbase.get("note") is None and self._reads_alike():
                zbase["note"], gbase["note"] = Z["note"], G["note"]
        else:
            zbase, gbase, prefer = row["zbase"], row["gbase"], "gamma"
            if self.note and row["detail"].get("restore"):
                zbase, gbase = dict(zbase or {}), dict(gbase or {})
                for f, value in Z.items():
                    if value == zbase.get(f):
                        continue  # (Zotero left it as the backup has it: each side's change as usual)
                    if f == "note" and self._reads_alike():
                        zbase[f], gbase[f] = value, G.get(f)
                    else:  # (a set: the restored page's members come back, Zotero's additions stay)
                        gbase[f] = [] if f in zotero_merge.SETS else None
        m = zotero_merge.merge(zbase, gbase, Z, G, texts=() if self.standalone or self.note else RECORD_TEXTS,
                               prefer=prefer)
        if self.note:
            m = self._note_merge(m, first)
        sig = signature(G)
        self._apply(m)
        push = {}
        refused = row["detail"].get("rejected")
        if r.pushes and not (refused and refused == sig):
            push = self._push(m, restore=restore)
        for name, ours, theirs in m.conflicts:
            if name != "note":  # (the side a first link's pair took, as a note's: "" for an open row)
                self.conflicts.append(("zotero_field", name, ours, theirs, prefer if first else ""))
                if first:
                    r.decided["records"] = r.decided.get("records", 0) + 1
        if m.apply or m.merged_text:
            self._count("pages_pulled")
            if "pulled" not in self.notes and self.change != "create":
                self.notes.append("pulled")
        zb, gb = zotero_merge.bases_after(m, zbase, gbase, Z, G, pushed_ok=False)
        detail = {**without(row["detail"], "collections", "reason"),
                  "collections": list(self.data.get("collections") or [])}
        if not push:
            detail = without(detail, "first", "adopt", "restore")
        # a restore sent to Zotero is the row's once it lands (``_record``), as a pushed field's base is: a
        # round cut off before it decides the restore again, never the item's trash here
        row.update(version=self.version, zbase=zb, gbase=gb, gsig=row["gsig"] if push else sig, detail=detail,
                   state="trashed" if restore and push else "live",
                   zotero_type=self.data.get("itemType") or row["zotero_type"])
        self._put(row)
        self.merged = {"m": m, "zbase": zbase, "gbase": gbase, "Z": Z, "G": G}
        if push:
            self.sent[self.key] = {"what": "update", "sig": sig, "restore": restore, **self.merged}

    def page_html(self) -> str:
        """A note page's note as Gamma would send it (``note_page_html``),
        read once for the unit's decision."""
        if self._page_html is None:
            with connect_pages_db(self.r.ws) as conn:
                self._page_html = note_page_html(conn, self.page_id)
        return self._page_html

    def _reads_alike(self) -> bool:
        """Whether a note page and its standalone note hold the same words
        (marks, bullets and paragraph shapes aside), as a first link
        compares a child note with its region."""
        return note_words(self.data.get("note")) == note_words(self.page_html())

    def _note_merge(self, m, first: bool):
        """A note page's ``note`` field as its merge decided it, made safe:
        Zotero's applied is a block-level diff (``note_pull``); Gamma's goes
        only when Zotero's note holds nothing Gamma would lose (else a
        ``zotero_lossy`` row, and a first link keeps Zotero's) and fits
        Zotero's limit; both changed apart is a ``zotero_note`` row (a first
        link's pair: resolved, whichever side it took)."""
        html = str(self.data.get("note") or "")
        both = any(name == "note" for name, _, _ in m.conflicts)
        base = json.dumps({"key": self.key, "note": html}, ensure_ascii=False)

        def taken():  # a first link's pair took Zotero's note: Gamma's words kept in a resolved row
            self.conflicts.append(("zotero_note", self.page_id, note_text(self.page_html()), note_text(html), base,
                                   "zotero"))
            self.r.decided["notes"] = self.r.decided.get("notes", 0) + 1

        if "note" in m.apply:
            self.note_pull = True
            if first and both:
                taken()
        if "note" not in m.push:
            return m
        region_html = self.page_html()
        if self.blocks is not None:
            pics = self.blocks._pictures(self.key, html)
        else:
            pics = zotero_local.resolve_pictures(html, lambda k: None)
        lossy = zotero_local.note_lossy(html, pics)
        row = self.rows.get(self.key) or {}
        forced = bool((row.get("detail") or {}).get("force"))
        mine, theirs = note_text(region_html), note_text(html)
        if lossy and not forced:
            push = {k: v for k, v in m.push.items() if k != "note"}
            if first:  # a first link keeps Zotero's lossy note whatever adopt says
                self.note_pull = True
                if both:
                    taken()
                return m._replace(push=push, apply={**m.apply, "note": m.push["note"]})
            self.conflicts.append(("zotero_lossy", self.page_id, mine, theirs, base, ""))
            return m._replace(push=push)
        if len(region_html) > NOTE_MAX:
            self.conflicts.append(("zotero_rejected", NOTE_TOO_LONG, self.page_id))
            return m._replace(push={k: v for k, v in m.push.items() if k != "note"})
        if both:
            self.conflicts.append(("zotero_note", self.page_id, mine, theirs, base, "gamma" if first else ""))
            if first:
                self.r.decided["notes"] = self.r.decided.get("notes", 0) + 1
        self.note_html = region_html
        return m

    def _spelled(self, folded) -> list[str]:
        """Folded tag names as a page's labels spell them, else as Zotero does."""
        ours = {zotero_merge.fold_tag(n): n.strip() for n in (self.side.labels if self.side else {}).values()}
        theirs = zotero_merge.tag_names(self.data, auto_tags=True)
        return [ours.get(f) or theirs.get(f) or f for f in folded]

    def _apply(self, m) -> None:
        """Zotero's side of a merge into the page's ops: the record's fields
        (only those), the title (a standalone PDF's), labels, folders."""
        if not m.apply and not m.merged_text:
            return
        values = {**m.apply, **m.merged_text}
        if self.standalone:
            if "title" in values and (values["title"] or "").strip():
                self.ops.append({"op": "set", "id": self.page_id, "content": values["title"]})
        else:
            op = zotero_local.record_op(self.side.block, {f: v for f, v in values.items()
                                                          if f in zotero_fields.COMPARE_FIELDS})
            if op:
                self.ops.append(op)
        if "tags" in m.apply:
            self.labels = self._spelled(m.apply["tags"])
        if "collections" in m.apply:
            kept = [f for f in self.side.folders if f not in self.inverse]  # folders no live collection stands for
            self.folders = kept + [self.cmap[k] for k in m.apply["collections"] if k in self.cmap]

    def _push(self, m, *, restore: bool) -> dict:
        """Gamma's side of a merge as the write it is: the changed record
        fields (``zotero_fields.patch_for``: a PATCH, or a PUT for a retype),
        the tags with Zotero's own tag objects kept (automatic ones
        untouched), the collections with the ones no live collection row
        stands for kept; ``deleted: false`` with a restore."""
        data, side = self.data, self.side
        pushed = {**m.push, **m.merged_text}
        body, replace = {}, False
        if self.standalone:
            if "title" in pushed:
                body["title"] = pushed["title"]
        else:
            changed = [f for f in pushed if f in zotero_fields.COMPARE_FIELDS]
            if "type" in pushed and "kind" not in changed:
                changed.append("kind")
            if changed:
                meta = dict(side.props.get("meta") or {})
                for f, value in m.merged_text.items():
                    meta[f] = value
                body, replace = zotero_fields.patch_for(data, meta, pinned_key(side.props), changed, side.title)
        if "tags" in pushed:
            body["tags"] = zotero_merge.tags_for_push(data.get("tags"), self._spelled(pushed["tags"]),
                                                      auto_tags=self.r.link["auto_tags"])
        if "collections" in pushed:
            held = [k for k in data.get("collections") or () if k not in self.cmap]
            body["collections"] = sorted(set(pushed["collections"]) | set(held))
        if self.note and "note" in pushed:
            body["note"] = self.note_html
        if restore:
            body["deleted"] = False
        if not body:
            return {}
        if replace:
            self.writes.replace(self.key, self.version, body)
        else:
            self.writes.update("attachments" if self.standalone else "items", self.key, self.version, body)
        return body

    # writing

    def _rows_tx(self, conn) -> None:
        """The rows the local part writes, in its transaction. Gamma's
        projection is read again after the ops (``bases_after``'s
        ``G_after``): a field applied takes Gamma's own reading of the result
        as its base, so a lossy mapping never reads as Gamma's change; a
        field Gamma pushes keeps its old base until the write lands."""
        if self.blocks is not None:
            self.blocks.rows_tx(conn)
        row, after = self.out.get(self.key), None
        if row is not None and self.change != "trash" and self.page_id and row["state"] in ("live", "trashed"):
            after = page_side(conn, self.r.ws, self.page_id, note=self.note)
            if after.block is not None:
                self.G_after = self.G(after)
                if self.merged is not None:
                    mm = self.merged
                    row["zbase"], row["gbase"] = zotero_merge.bases_after(
                        mm["m"], mm["zbase"], mm["gbase"], mm["Z"], mm["G"], pushed_ok=False, G_after=self.G_after)
                elif self.change == "create":
                    row["gbase"] = self.G_after
        seq = self._own_change(conn, after)
        if seq is not None and row["page_id"] == self.page_id:
            row["detail"] = {**row["detail"], "gseq": seq}
        if self._labels_made:  # the labels this write made, by the names it gave them: only a rename since queues
            # the pages carrying them again (``zotero_sync._list_gamma``), never their making
            made = {lid: name for lid, name in label_names(conn).items() if lid in self._labels_made}
            zr.set_state(conn, labels={**zr.get_state(conn)["labels"], **made})
        write_rows(conn, self.out.values())
        drop_rows(conn, self.dropped)

    def _own_change(self, conn, after: Side | None) -> int | None:
        """The change number (``page_changes.seq``) this transaction's own
        write gave the page, when the unit settles the page whole with it
        (``after``: the page as the write left it): a two-way round (a
        receive-only one leaves Gamma's side for later), nothing to send to
        Zotero (whose answer could leave the page to decide again), the
        page made here or written under the compare-and-set in this very
        transaction (a restore's two steps are not one), and nothing left
        that only the page's own unit does (a standalone PDF whose record
        now earns a parent item: ``zotero_sync_pages.unit_page``). The
        unit's row keeps it (``detail.gseq``), so a feed listing that finds
        the page at that number has nothing of a person's to bring
        (``zotero_sync._list_gamma``); else None."""
        if (not self.r.pushes or not self.page_id or self.change not in ("", "create") or len(self.writes)
                or "seq" not in self._before or after is None or after.block is None):
            return None
        if self.standalone and zotero_merge.standalone_gets_parent(after.props.get("meta")):
            return None
        found = conn.execute("SELECT seq FROM page_changes WHERE page_id = ?", (self.page_id,)).fetchone()
        return found[0] if found and found[0] != self._before["seq"] else None

    def write(self) -> bool:
        """Gamma's side in one transaction (False when the page changed under
        the decision: nothing is written, the unit goes back to the queue),
        then Zotero's, then the outcomes."""
        ws = self.r.ws
        expected = dict(self.side.stamp) if self.side is not None and self.change != "create" else None

        def check(conn):  # (first in each transaction: the page's change number as it finds it, ``_own_change``)
            found = conn.execute("SELECT seq FROM page_changes WHERE page_id = ?", (self.page_id,)).fetchone()
            self._before = {"seq": found[0] if found else None}
            return expected is None or zotero_local.stamps(conn, list(expected)) == expected

        filing = {}

        def trees(conn):
            if self.labels is None and self.folders is None:
                return []
            page = conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?", (self.page_id,)).fetchone()
            found = zotero_local.filing_ops(conn, block_to_dict(page), folders=self.folders, labels=self.labels)
            filing["op"] = found.op
            self._labels_made = _made_labels(found.trees)
            return found.trees

        def ops(conn):
            blocks = self.blocks.ops_now(conn) if self.blocks is not None and self.blocks_ops else []
            return [*self.ops, *([filing["op"]] if filing.get("op") else []), *blocks]

        try:
            if self.change == "create":
                new = self.new_page
                self._labels_made = _made_labels(new["trees"])
                zotero_local.create_page(ws, page_id=new["page_id"], title=new["title"], props=new["props"],
                                         tree=new.get("tree") or (), trees=new["trees"], check=check,
                                         rows=self._rows_tx)
            elif self.change == "trash":
                zotero_local.trash_page(ws, self.page_id, check=check, rows=self._rows_tx)
            else:
                if self.change == "restore":
                    zotero_local.restore_page(ws, self.page_id, check=check)
                    with connect_pages_db(ws) as conn:
                        expected = zotero_local.stamps(conn, [self.page_id])
                if self.ops or self.labels is not None or self.folders is not None or self.blocks_ops:
                    zotero_local.apply(ws, self.page_id, ops, check=check, rows=self._rows_tx, trees=trees)
                elif (self.out or self.dropped) and self.page_id:
                    with connect_pages_db(ws) as conn:
                        write_lock(conn)
                        if not check(conn):
                            conn.rollback()
                            raise zotero_local.Changed(self.page_id)
                        self._rows_tx(conn)
                        conn.commit()
        except zotero_local.Changed:
            return False
        except zotero_local.Moved:
            return False
        if (self.out or self.dropped) and not self.page_id:
            with connect_pages_db(ws) as conn:  # rows of no page
                write_lock(conn)
                self._rows_tx(conn)
                conn.commit()
        outcomes = self.writes.flush() if len(self.writes) else {}
        if self._record(outcomes):  # creates under a key Zotero holds: the follow-ups ``_exists`` decides, sent at once
            self._record(self.writes.flush())
        return True

    def stopped(self) -> ZoteroError | None:
        """The failure that stopped the unit's writes and stops the round
        (Zotero unreachable, busy, refusing the key), raised once the unit is
        recorded."""
        error = self.writes.error
        return error if error is not None and zr.error_kind(error) in STOPS_ROUND else None

    def _record(self, outcomes: dict) -> bool:
        """Each write's outcome on its row, in one transaction; True when it
        queued a follow-up write (a create Zotero answered 412)."""
        again = False
        rows = []
        for key, outcome in outcomes.items():
            sent = self.sent.get(key)
            if sent is None:
                continue
            row = self.out.get(key) or self.rows.get(key)
            if row is None:
                continue
            row = dict(row)
            what = sent["what"]
            if outcome.status == zr.TRANSIENT:
                self.retry = True
                continue
            if outcome.status == zr.CONFLICT:
                self.refetch = True
                continue
            if outcome.status == zr.REJECTED:
                if what in ("create", "attachment", "ann_create", "note_create"):
                    row.update(state="rejected", detail={**row["detail"], "reason": outcome.message,
                                                         "rejected": sent.get("sig") or sent.get("gh") or row["gsig"]})
                else:
                    row["detail"] = {**row["detail"], "rejected": sent.get("sig") or row["gsig"],
                                     "reason": outcome.message}
                rows.append(row)
                where = row["block_id"] if row["kind"] in ("note", "annotation") and row["block_id"] else key
                self.conflicts.append(("zotero_rejected", outcome.message, where))
                continue
            if outcome.status == zr.EXISTS:
                again |= self._exists(key, row, sent, outcome)
                continue
            if what in _BLOCK_WRITES:
                self.blocks.record(row, sent, outcome)
                rows.append(row)
                continue
            saved = (outcome.data or {}).get("data")
            version = int(outcome.version or row["version"])
            if what == "update":
                Z_after = self.Z(saved) if saved else None
                zb, gb = zotero_merge.bases_after(sent["m"], sent["zbase"], sent["gbase"], sent["Z"], sent["G"],
                                                  pushed_ok=True, G_after=self.G_after, Z_after=Z_after)
                row.update(version=version, zbase=zb, gbase=gb, gsig=sent["sig"], state="live",
                           detail=without(row["detail"], "first", "adopt", "restore", "rejected", "reason", "force"))
                if saved:
                    row["detail"]["collections"] = list(saved.get("collections") or [])
                    row["zotero_type"] = saved.get("itemType") or row["zotero_type"]  # a retype sent
                self._count("pages_pushed")
                self.notes.append("restored there" if sent.get("restore") else "pushed")
            elif what == "trash":
                row.update(state="trashed", version=version, detail=without(row["detail"], "first", "adopt"))
                self._count("pages_pushed")
                self.notes.append("deleted there")
            elif what == "attachment":
                row.update(state="live", version=version, detail=without(row["detail"], "sent"))
                self.queue.append({"id": zr.FILE_PREFIX + key, "kind": "file", "version": version,
                                   "priority": zr.PAGE_PRIORITY})
            rows.append(row)
        if rows:
            with connect_pages_db(self.r.ws) as conn:
                write_lock(conn)
                write_rows(conn, rows)
                conn.commit()
        for row in rows:
            self.out[row["key"]] = row
        return again

    def _exists(self, key: str, row: dict, sent: dict, outcome) -> bool:
        """A create under a key Zotero holds (412; see
        docs/dev/zotero_sync.md#writing-to-zotero): the same kind of object
        under the same parent is ours (its create landed, or it is in
        Zotero's trash): it is brought back with what Gamma holds now; any
        other gets a fresh key and is created again."""
        data = (outcome.data or {}).get("data") or {}
        body = sent.get("body") or row["detail"].get("sent") or {}
        stage = sent.get("stage") or ("attachments" if sent["what"] == "attachment" else "items")
        same = (data.get("itemType") == body.get("itemType")
                and (data.get("parentItem") or "") == (body.get("parentItem") or "")
                and (data.get("annotationType") or "") == (body.get("annotationType") or ""))
        if same and outcome.data:
            update = {k: v for k, v in body.items() if k not in ("itemType", "parentItem", "linkMode",
                                                                   "annotationType")}
            self.writes.update(stage, key, outcome.version, {**update, "deleted": False})
            return True
        fresh = random_key()
        new = {**row, "key": fresh, "detail": {**row["detail"]}}
        self._drop(key)
        self._put(new)
        with connect_pages_db(self.r.ws) as conn:
            write_lock(conn)
            drop_rows(conn, [key])
            write_rows(conn, [new])
            conn.commit()
        self.writes.create(stage, fresh, body)
        self.sent[fresh] = {**sent}
        return True

    def finish(self, entry_ids) -> None:
        """The queue after the unit: its entries done (the page's too, unless
        it holds what only its own unit does: ``_page_work``), its transfers
        queued; then the log lines and the conflict rows, after every
        commit."""
        r = self.r
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            done = {self.key: self.version, **{k: int(c.get("version") or 0) for k, c in self.kids.items()}}
            zr.done(conn, done)
            zr.done(conn, list(entry_ids))
            if self.page_id and not self._page_work():
                zr.done(conn, [zr.PAGE_PREFIX + self.page_id])
            zr.enqueue(conn, self.queue)
            quiet = self.blocks.quiet_at if self.blocks is not None else None
            if quiet is not None and self.page_id:  # its notes go to Zotero once the page is still
                zr.enqueue(conn, [zr.page_entry(self.page_id, next_at=quiet,
                                                reason=NOTES_WAIT)])
            if self.refetch:  # Zotero changed it while it was written: its unit decides again, next round
                zr.enqueue(conn, [{"id": self.key, "kind": "top", "version": self.version}])
            if self.retry and self.page_id:
                zr.backoff(conn, zr.PAGE_PREFIX + self.page_id, str(self.writes.error or "a write did not land"),
                           now=page_now(), kind="page")
            conn.commit()
        if self.refetch:
            r.decide_again(self.key)
        if self.retry and self.page_id:
            r.excluded.add(zr.PAGE_PREFIX + self.page_id)
        for name, n in self.counts.items():
            r.report[name] = r.report.get(name, 0) + n
        if not self.page_id:
            return
        for action in dict.fromkeys(self.notes):
            if action != "kept":
                page_note(r, self.page_id, action)
        fields = [c for c in self.conflicts if c[0] == "zotero_field"]
        if fields:
            decided = all(c[4] for c in fields)
            kept = not r.pushes and all(c[4] != "zotero" for c in fields)  # Gamma's side, not sent
            what = "title" if self.standalone else "record"
            synced = flat_fields((self.out.get(self.key) or self.rows.get(self.key) or {}).get("zbase"))
            record_conflict(r.ws, self.page_id, self.page_id, "zotero_field",
                            mine="\n".join(zotero_merge.field_line(name, ours) for _, name, ours, _, _ in fields),
                            theirs="\n".join(zotero_merge.field_line(name, theirs) for _, name, _, theirs, _ in fields),
                            base=json.dumps(field_base({name: theirs for _, name, _, theirs, _ in fields}, synced),
                                            ensure_ascii=False),
                            result=f"The {what} changed on both sides: {KEPT_HERE}" if kept else "",
                            resolved=decided)
        for kind, *rest in self.conflicts:
            if kind in ("zotero_deleted_edited", "zotero_locked"):
                block, mine, result = rest
                record_conflict(r.ws, self.page_id, block, kind, mine=mine, result=result)
            elif kind == "zotero_rejected":
                message, where = rest
                record_conflict(r.ws, self.page_id, self.page_id if where == self.key else where, kind,
                                result=f"Zotero refused this: {message}")
            elif kind in ("zotero_note", "zotero_annotation", "zotero_lossy"):
                block, mine, theirs, base, decided = rest  # (decided: the side a first link's pair took, or "")
                record_conflict(r.ws, self.page_id, block, kind, mine=mine, theirs=theirs, base=base,
                                resolved=bool(decided), result=_result(kind, decided, r.pushes))

    def _page_work(self) -> bool:
        """Whether the page holds what only its own unit does
        (``zotero_sync_pages.unit_page``), which keeps its entry queued as
        it is, a backoff too: an item's create not confirmed (or refused,
        sent again once the page changes), a standalone PDF's move under its
        new item, a standalone PDF whose record now earns a parent item."""
        rows = {**{k: row for k, row in self.rows.items() if k not in self.dropped}, **self.out}
        mine = [row for row in rows.values() if row["page_id"] == self.page_id]
        if any(row["kind"] == "item" and row["state"] in ("pending", "rejected") and int(row["version"]) <= 0
               for row in mine) or moving_pdf({row["key"]: row for row in mine}) is not None:
            return True
        return (self.standalone and self.side is not None and self.side.live and self.change != "trash"
                and not any(row["kind"] == "item" for row in mine)
                and zotero_merge.standalone_gets_parent(self.side.props.get("meta")))


def _result(kind: str, decided: str, pushes: bool) -> str:
    """What a note's, an annotation's or a lossy note's conflict row says
    was done: Zotero's side taken (``decided``, a first link's pair), Gamma's
    sent, or Gamma's kept here by a receive-only round (``KEPT_HERE``)."""
    if decided == "zotero":
        return _TAKEN_RESULTS[kind]
    if not pushes and kind in _KEPT_RESULTS:
        return _KEPT_RESULTS[kind]
    return _CONFLICT_RESULTS[kind]


def _made_labels(trees) -> set:
    """The ids of the labels ``trees`` (``zotero_local.filing_ops``'s) makes."""
    return {op["id"] for tree, ops in trees for op in ops if tree == LABELS and op.get("op") == "insert"}


def reconcile(r: Round, unit: zr.Unit, entry_ids=()) -> None:
    """One top-level item's unit, start to end; a compare-and-set that failed
    sends it back to the queue (tried again next round) with nothing
    written on either side."""
    u = _Unit(r, unit)
    try:
        u.read()
        u.decide()
        if not u.write():
            with connect_pages_db(r.ws) as conn:
                write_lock(conn)
                zr.enqueue(conn, [{"id": u.key, "kind": "top", "version": u.version}])
                conn.commit()
            r.decide_again(u.key, *entry_ids)
            return
        u.finish(entry_ids)
    finally:
        if u.blocks is not None:
            u.blocks.close()
    stopped = u.stopped()
    if stopped is not None:
        raise stopped


# --- the queue's entries of items and children ------------------------------------------

def unit_item(r: Round, entry: zr.Entry):
    """A top-level item's unit: the item read whole with its children,
    the trash included, and reconciled against its page."""
    unit = read_unit(r, entry.id, entry.version, listed=entry)
    if unit.item is None:
        return absent(r, entry.id, entry)
    if (unit.item.get("data") or {}).get("parentItem"):  # a child queued as an item: its item's unit
        return unit_child(r, entry, unit.item)
    return reconcile(r, unit, [entry.id])


def _top_of(r: Round, obj: dict) -> str | None:
    """The key of the top-level item a child belongs to (an annotation's
    attachment's item, a picture's note's item): by the rows, else read;
    "" when its parent is gone; None when the child is an attachment's
    that holds nothing that travels (an annotation of a web snapshot or an
    EPUB: no PDF of a file link mode), which its item's unit never reads."""
    with connect_pages_db(r.ws) as conn:
        for _ in range(4):
            parent = (obj.get("data") or {}).get("parentItem")
            if not parent:
                return obj["key"]
            row = read_rows(conn, "key = ?", (parent,)).get(parent)
            if row is not None and row["kind"] == "item":
                return parent
            if row is not None and row["kind"] in ("attachment", "note"):
                return row["parent"] or parent
            found = zr.fetch_items(r.client, r.library, [parent]).objects.get(parent)
            if found is None:
                return ""
            data = found.get("data") or {}
            if data.get("itemType") == "attachment" and not _is_pdf(data):
                return None
            obj = found
    return ""


def unit_child(r: Round, entry: zr.Entry, obj: dict | None = None):
    """A child item listed alone (an attachment, a note, an annotation, a
    note's picture): its top-level item's unit, which reads every child
    (``obj``: the child, read already)."""
    obj = obj or take_ahead(r, entry.id, entry.version) or zr.fetch_items(r.client, r.library, [entry.id]).objects.get(
        entry.id)
    if obj is None:
        return absent(r, entry.id, entry)
    top = _top_of(r, obj)
    if top is None:  # an annotation that never travels: kept terminal at its version, so the listing passes it
        # by until Zotero changes it, and no unit of its item reads it (``descendants`` never does)
        data = obj.get("data") or {}
        version = max(int(entry.version or 0), int(obj.get("version") or 0))
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            write_rows(conn, [new_row(entry.id, "annotation", parent=str(data.get("parentItem") or ""),
                                      zotero_type=str(data.get("annotationType") or ""), version=version,
                                      state="skipped")])
            zr.done(conn, {entry.id: version})
            conn.commit()
        return None
    if not top:  # its parent went with it: the removal for good is listed too
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            zr.done(conn, {entry.id: entry.version})
            conn.commit()
        return None
    unit = read_unit(r, top)
    if unit.item is None:
        return absent(r, top, entry)
    return reconcile(r, unit, [entry.id, top])
