"""Random edits on both sides of a Zotero link, rounds between them at
random (gamma/zotero_sync.py; see docs/dev/zotero_sync.md#tests): whatever
the order, once the rounds settle Gamma and Zotero hold the same thing
(records, tags and labels, collections and folders, the trash, annotations'
colours and comments, notes, each paper's PDF), and one more round writes
nothing (``zotero_idle``); more rounds after that stay settled: nothing is
due but what a wait holds back, no round asks for another at once, no
conflict row is added (``_quiet``), and a round's units are bounded
(``ROUND_UNITS``), so a unit that queues itself again shows. Each seed is one
run against the fake Zotero with a generated library; a failure prints the
edits and rounds that concern the object. Now and then the link turns
receive only or is detached for a while, a force takes one side's version
of everything, and a round is killed after one of its writes (``_round``).
A PDF replaced on either side has its highlights placed by a relocation
stubbed to run until the fuzz ends it (``placing``), written while it still
runs or not at all, and no new attachment of Gamma's goes beside Zotero's
PDF while they are placed, nor when each of its annotations could move
with Gamma's file (``_needless_beside``); a note page edited here may have
its note removed for good in Zotero. More runs: ``ZOTERO_FUZZ_SEEDS=2000 ZOTERO_FUZZ_STEPS=100``;
``ZOTERO_FUZZ_TRACE=<key>`` follows one object round by round. Accounts are
``zf_…``."""

import json
import os
import random

import pytest
from zotero_engine import (LIB, WRITES, Crash, _block, _conflict_count, _due_now, _rows, _stored_pdf,  # noqa: F401
                           _sync, asked_soon, background, drop_pending, fake, idle, me, no_relocation, owner_id,
                           quiet)
from zotero_fixtures import _annotation, _note, _position, generated_library, pdf_bytes

from gamma import highlight_relocate, ops, storage, zotero_local, zotero_sync, zotero_sync_blocks, zotero_sync_pdfs
from gamma import zotero_annotations as za
from gamma import zotero_sync_rows
from gamma.blocks_store import FOLDERS, LABELS, TRASH, label_names, tree_rows
from gamma.db import connect_pages_db
from gamma.ops import OpError
from gamma.routers import pages as pages_router
from gamma.zotero_annotations import file_md5
from gamma.zotero_sync_reads import HELD_ROWS

USER, PASSWORD = "zf_owner", "pw-zf_owner-123"
pytestmark = pytest.mark.usefixtures("quiet")  # a page's own notes go at once


COLOURS = ("#ffd400", "#ff6666", "#5fb236", "#2ea8e5", "#a28ae5")


WORDS = ("alpha", "beta", "gamma", "delta", "kappa", "omega")


STEPS = int(os.environ.get("ZOTERO_FUZZ_STEPS") or 40)


SEEDS = range(int(os.environ.get("ZOTERO_FUZZ_SEEDS") or 8))


ROUND_UNITS = 400  # a round's units: far more than any round of this library needs, so a loop stops at it


QUIET_ROUNDS = 3  # rounds after the link settled that must stay settled


def _first_child(ws, parent) -> str | None:
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT id FROM unified_blocks WHERE parent_id = ? ORDER BY position LIMIT 1",
                           (parent,)).fetchone()
    return row[0] if row else None


def _stamped(ws) -> dict:
    """{page id: [(block id, position)]} of the highlights of live pages
    still marked with the file they were measured on (``pdf_position.doc_id``:
    a replacement's, until its relocation writes; a page in Recently deleted
    takes no write)."""
    out: dict = {}
    with connect_pages_db(ws) as conn:
        for page_id, block_id, pos in conn.execute(
                "SELECT b.page_id, b.id, json_extract(b.properties, '$.pdf_position') FROM unified_blocks b "
                "JOIN unified_blocks p ON p.id = b.page_id WHERE p.parent_id = 'root' "
                "AND json_extract(b.properties, '$.pdf_position.doc_id') IS NOT NULL").fetchall():
            out.setdefault(page_id, []).append((block_id, json.loads(pos)))
    return out


class _Edits:
    """The random edits of one run, on either side, and their log.
    ``placing``: the pages whose relocation the stub reads as running."""

    def __init__(self, rnd, ws, owner, fake, made, placing):
        self.rnd, self.ws, self.owner, self.fake, self.made, self.placing = rnd, ws, owner, fake, made, placing
        self.n = 0
        self.log: list = []  # (what, key, value), ("round", "", "") for each round, in order
        self.beside: list = []  # (page id, why): each new attachment of Gamma's made beside Zotero's needlessly

    def word(self) -> str:
        self.n += 1
        return f"{self.rnd.choice(WORDS)} {self.n}"

    def note(self, what, key, value="") -> None:
        self.log.append((what, key, value))

    def history(self, key) -> str:
        """The edits of ``key`` (and every round, label rename, and change of
        the collections and folders), in order."""
        shown = ("round", "g_label_rename", "crashed", "mode", "trace", "force")
        return "\n".join(f"  {what} {k if k != key else ''} {value}" for what, k, value in self.log
                         if k == key or what in shown or "collection" in what or "folder" in what)

    def _item(self):
        return self.rnd.choice(self.made["items"])

    def _page(self, key):
        row = _rows(self.ws).get(key)
        return row["page_id"] if row and _block(self.ws, row["page_id"]) else None

    def _gamma(self, page_id, batch) -> None:
        try:
            ops.commit_ops(self.ws, page_id, batch, actor=self.owner, client="t")
        except OpError:
            self.note("refused", "", page_id)  # a page in Recently deleted refuses edits, as it does a person's

    # Zotero's side

    def z_title(self):
        key, title = self._item(), f"Paper {self.word()}"
        self.note("z_title", key, title)
        self.fake.edit(LIB, key, title=title)

    def z_date(self):
        key, date = self._item(), str(self.rnd.randint(1950, 2024))
        self.note("z_date", key, date)
        self.fake.edit(LIB, key, date=date)

    def z_tags(self):
        key = self._item()
        tags = [t for t in self.fake.data(LIB, key)["tags"] if t.get("type") == 1]
        tags += [{"tag": f"topic {self.rnd.randint(0, 8)}"} for _ in range(self.rnd.randint(0, 2))]
        self.note("z_tags", key, [t["tag"] for t in tags if not t.get("type")])
        self.fake.edit(LIB, key, tags=tags)

    def z_trash(self):
        key = self._item()
        deleted = bool(self.fake.data(LIB, key).get("deleted"))
        self.note("z_restore" if deleted else "z_trash", key)
        (self.fake.restore if deleted else self.fake.trash)(LIB, key)

    def z_collections(self):
        live = [k for k in self.made["collections"] if not self.fake.data(LIB, k).get("deleted")]
        key, chosen = self._item(), self.rnd.sample(live, self.rnd.randint(0, min(2, len(live))))
        self.note("z_collections", key, chosen)
        self.fake.edit(LIB, key, collections=chosen)

    def z_collection_name(self):
        key, name = self.rnd.choice(self.made["collections"]), f"Shelf {self.word()}"
        self.note("z_collection_name", key, name)
        self.fake.edit(LIB, key, name=name)

    def z_annotation(self):
        key = self.rnd.choice(self.made["annotations"])
        field = "annotationColor" if self.rnd.random() < 0.5 else "annotationComment"
        value = self.rnd.choice(COLOURS) if field == "annotationColor" else f"Zotero says {self.word()}"
        self.note(f"z_{field}", key, value)
        self.fake.edit(LIB, key, **{field: value})

    def z_note(self):
        key = self.rnd.choice(self.made["notes"])
        n = self.made["notes"].index(key)
        text = f"Zotero thought {self.word()}."
        self.note("z_note", key, text)
        self.fake.edit(LIB, key, note=_note(f"<h1>Reading paper {n}</h1><p>{text}</p><p>Second thought.</p>"))

    # Gamma's side

    def _key_of(self, page_id) -> str:
        return next((k for k, row in _rows(self.ws, "item").items() if row["page_id"] == page_id), "")

    def g_record(self):
        page = self._page(self._item())
        block = _block(self.ws, page) if page else None
        if not block or "meta" not in block["props"]:
            return
        field, value = self.rnd.choice((("title", f"Paper {self.word()}"),
                                        ("year", str(self.rnd.randint(1950, 2024)))))
        self.note(f"g_{field}", self._key_of(page), value)
        self._gamma(page, [{"op": "set", "id": page, "props": {"meta": {**block["props"]["meta"], field: value}}}])

    def g_labels(self):
        page = self._page(self._item())
        if not page:
            return
        with connect_pages_db(self.ws) as conn:
            known = {name.casefold(): lid for lid, name in label_names(conn).items()}
        name = f"topic {self.rnd.randint(0, 8)}"
        lid = known.get(name)
        if lid is None:
            lid = f"zfLabel{self.n}x{self.rnd.randint(0, 10 ** 6)}"
            ops.commit_ops(self.ws, LABELS, [{"op": "insert", "id": lid, "parent": LABELS, "content": name}],
                           actor=self.owner, client="t")
        current = list((_block(self.ws, page)["props"].get(LABELS)) or [])
        labels = [x for x in current if x != lid] if lid in current else [*current, lid]
        self.note("g_labels", self._key_of(page), ("-" if lid in current else "+") + name)
        self._gamma(page, [{"op": "set", "id": page, "props": {LABELS: labels or None}}])

    def g_label_rename(self):
        with connect_pages_db(self.ws) as conn:
            labels = label_names(conn)
        if not labels:
            return
        lid = self.rnd.choice(sorted(labels))
        name = f"topic {self.rnd.randint(9, 14)}"
        if name not in {n.casefold() for n in labels.values()}:
            self.note("g_label_rename", "", f"{labels[lid]} -> {name}")
            ops.commit_ops(self.ws, LABELS, [{"op": "set", "id": lid, "content": name}], actor=self.owner,
                           client="t")

    def g_trash(self):
        page = self._page(self._item())
        if not page:
            return
        restore = _block(self.ws, page)["parent"] == TRASH
        self.note("g_restore" if restore else "g_trash", self._key_of(page))
        with connect_pages_db(self.ws) as conn:
            (ops.restore_page if restore else ops.trash_page)(self.ws, conn, page, actor=self.owner, client="t")

    def g_refile(self):
        page = self._page(self._item())
        with connect_pages_db(self.ws) as conn:
            folders = [fid for fid, _, _, _ in tree_rows(conn, FOLDERS)]
        if page and folders:
            chosen = self.rnd.sample(folders, self.rnd.randint(0, min(2, len(folders))))
            self.note("g_refile", self._key_of(page), chosen)
            self._gamma(page, [{"op": "set", "id": page, "props": {FOLDERS: chosen or None}}])

    def g_folder_name(self):
        with connect_pages_db(self.ws) as conn:
            folders = [fid for fid, _, _, _ in tree_rows(conn, FOLDERS)]
        if folders:
            fid, name = self.rnd.choice(folders), f"Folder {self.word()}"
            self.note("g_folder_name", fid, name)
            ops.commit_ops(self.ws, FOLDERS, [{"op": "set", "id": fid, "content": name}], actor=self.owner,
                           client="t")

    def g_annotation(self):
        key = self.rnd.choice(self.made["annotations"])
        row = _rows(self.ws, "annotation").get(key)
        if not row or not row["block_id"] or not _block(self.ws, row["block_id"]):
            return
        if self.rnd.random() < 0.5:
            colour = self.rnd.choice(COLOURS)
            self.note("g_colour", key, colour)
            batch = [{"op": "set", "id": row["block_id"], "props": {"color": colour + "73"}}]
        else:
            text = f"Gamma says {self.word()}"
            self.note("g_comment", key, text)
            batch = [{"op": "set", "id": row["block_id"], "content": text}]
        self._gamma(row["page_id"], batch)

    def g_note(self):
        key = self.rnd.choice(self.made["notes"])
        row = _rows(self.ws, "note").get(key)
        if not row or not row["block_id"] or not _block(self.ws, row["block_id"]):
            return
        child = _first_child(self.ws, row["block_id"])
        if child:
            text = f"Gamma thought {self.word()}."
            self.note("g_note", key, text)
            self._gamma(row["page_id"], [{"op": "set", "id": child, "content": text}])

    # removals and new objects, on either side

    def z_trash_annotation(self):
        key = self.rnd.choice(self.made["annotations"])
        deleted = bool(self.fake.data(LIB, key).get("deleted"))
        self.note("z_restore" if deleted else "z_trash", key)
        (self.fake.restore if deleted else self.fake.trash)(LIB, key)

    def z_trash_note(self):
        key = self.rnd.choice(self.made["notes"])
        deleted = bool(self.fake.data(LIB, key).get("deleted"))
        self.note("z_restore" if deleted else "z_trash", key)
        (self.fake.restore if deleted else self.fake.trash)(LIB, key)

    def z_trash_collection(self):
        key = self.rnd.choice(self.made["collections"])
        deleted = bool(self.fake.data(LIB, key).get("deleted"))
        self.note("z_restore_collection" if deleted else "z_trash_collection", key)
        (self.fake.restore if deleted else self.fake.trash)(LIB, key)

    def z_new_annotation(self):
        n = self.rnd.randrange(len(self.made["pdfs"]))
        att = self.made["pdfs"][n]
        top = 600.0 - 30 * self.rnd.randint(0, 10)
        key = self.fake.add_item(LIB, _annotation(
            att, "highlight", self.rnd.choice(COLOURS), f"00000|{self.n:06d}|{int(792 - top - 14):05d}",
            _position(0, rects=[[72.0, top, 150.0, top + 14]]), comment=f"Zotero made {self.word()}",
            annotationText=f"Paper {n}"))
        self.made["annotations"].append(key)
        self.note("z_new_annotation", key)

    def g_delete_annotation(self):
        key = self.rnd.choice(self.made["annotations"])
        row = _rows(self.ws, "annotation").get(key)
        if row and row["block_id"] and _block(self.ws, row["block_id"]):
            self.note("g_delete", key)
            self._gamma(row["page_id"], [{"op": "delete", "id": row["block_id"]}])

    def g_delete_note(self):
        key = self.rnd.choice(self.made["notes"])
        row = _rows(self.ws, "note").get(key)
        if row and row["block_id"] and _block(self.ws, row["block_id"]):
            self.note("g_delete", key)
            self._gamma(row["page_id"], [{"op": "delete", "id": row["block_id"]}])

    def g_new_highlight(self):
        n = self.rnd.randrange(len(self.made["pdfs"]))
        page = self._page(self.made["items"][n])
        if not page:
            return
        top = 600.0 - 30 * self.rnd.randint(0, 10)
        with za.Pages(pdf_bytes(f"Paper {n}")) as pages:
            made = za.from_item({"annotationType": "highlight", "annotationColor": self.rnd.choice(COLOURS),
                                 "annotationText": f"Paper {n}", "annotationComment": "", "key": "GAMMA234",
                                 "annotationPosition": json.dumps({"pageIndex": 0, "rects": [
                                     [72.0, top, 150.0, top + 14]]})}, pages.page(1))
        block_id = f"zfHl{self.n}x{self.rnd.randint(0, 10 ** 6)}"
        props = {k: v for k, v in made["props"].items()
                 if k not in ("zotero_annotation", "imported_annot", "annot_stripped")}
        self.made["gamma_highlights"].append((block_id, n))
        self.note("g_new_highlight", block_id)
        self._gamma(page, [{"op": "insert", "id": block_id, "parent": page, "content": f"Gamma made {self.word()}",
                            "props": props}])

    def g_delete_folder(self):
        with connect_pages_db(self.ws) as conn:
            folders = [fid for fid, _, _, _ in tree_rows(conn, FOLDERS)]
        if folders:
            fid = self.rnd.choice(folders)
            self.note("g_delete_folder", fid)
            with connect_pages_db(self.ws) as conn:
                ops.delete_tree(self.ws, conn, FOLDERS, fid, actor=self.owner, client="t")

    def g_new_paper(self):
        page_id = f"zfPaper{self.n}x{self.rnd.randint(0, 10 ** 6)}"
        title = f"Written here {self.word()}"
        zotero_local.create_page(self.ws, page_id=page_id, title=title, actor=self.owner, client="t", props={
            "meta": {"title": title, "year": str(self.rnd.randint(1950, 2024)), "source": "manual"}})
        self.made["gamma_papers"].append(page_id)
        self.note("g_new_paper", page_id, title)

    def z_note_page(self):
        if not self.made["note_pages"]:
            return
        key = self.rnd.choice(self.made["note_pages"])
        text = f"Zotero line {self.word()}."
        self.note("z_note_page", key, text)
        self.fake.edit(LIB, key, note=_note(f"<h1>Standalone {self.made['note_pages'].index(key)}</h1>"
                                            f"<p>{text}</p>"))

    def g_note_page(self):
        pages = [self._page(key) for key in self.made["note_pages"]] + self.made["gone_note_pages"]
        pages = [page for page in pages if page and _block(self.ws, page)]
        if pages:
            self._edit_note_page(self.rnd.choice(pages))

    def _edit_note_page(self, page) -> None:
        child = _first_child(self.ws, page)
        if child:
            text = f"Gamma line {self.word()}."
            self.note("g_note_page", page, text)
            self._gamma(page, [{"op": "set", "id": child, "content": text}])

    def z_delete_note_page(self):
        """A standalone note removed for good in Zotero (its trash emptied),
        often right after its page was edited here."""
        live = [key for key in self.made["note_pages"] if self._page(key)]
        if not live:
            return
        key = self.rnd.choice(live)
        page = self._page(key)
        if self.rnd.random() < 0.5:
            self._edit_note_page(page)
        self.note("z_delete_note_page", key, page)
        self.note("z_delete_note_page", page, key)
        self.made["note_pages"].remove(key)
        self.made["gone_note_pages"].append(page)
        self.fake.delete(LIB, key)

    # PDFs replaced on either side, their highlights placed by a relocation stubbed to run until it ends

    def _pdf_paper(self):
        n = self.rnd.randrange(len(self.made["pdfs"]))
        return n, self.made["items"][n], self._page(self.made["items"][n])

    def g_replace_pdf(self):
        n, key, page = self._pdf_paper()
        if not page:
            return
        doc = _stored_pdf(self.ws, pdf_bytes(f"Paper {n}, Gamma's {self.word()}"))
        try:
            pages_router.replace_attachment(self.ws, page, doc, "", f"gamma-{n}.pdf", actor=self.owner,
                                            owner=self.owner)
        except OpError:
            self.note("refused", key, page)  # in Recently deleted, or Zotero's PDF is on its way
            return
        self.placing.add(page)  # its relocation runs until ``g_relocation_ends``
        self.note("g_replace_pdf", key, doc)

    def z_replace_pdf(self):
        n, key, page = self._pdf_paper()
        bound = [k for k, row in _rows(self.ws, "attachment").items()
                 if page and row["page_id"] == page and row["state"] in HELD_ROWS]
        att = bound[0] if bound else self.made["pdfs"][n]
        try:
            data = self.fake.data(LIB, att)
        except KeyError:  # Gamma's attachment, not made in Zotero yet
            return
        if data.get("deleted") or data.get("linkMode") != "imported_file" or not data.get("md5"):
            return
        self.note("z_replace_pdf", key, att)
        self.fake.add_file(LIB, att, pdf_bytes(f"Paper {n}, Zotero's {self.word()}"))

    def g_relocation_writes(self):
        """A relocation writes where it placed the highlights (each where it
        was: its mark of the file it was measured on goes) and runs on until
        ``g_relocation_ends``; on a page whose run ended without writing, a
        person's Find highlights in this PDF does so."""
        stamped = _stamped(self.ws)
        if stamped:
            page = self.rnd.choice(sorted(stamped))
            self.note("g_relocation_writes", page)
            self.placing.add(page)
            self._place(page, stamped[page])

    def g_relocation_ends(self):
        """A relocation ends (with or without its write before it), as its
        job tells the link."""
        if self.placing:
            page = self.rnd.choice(sorted(self.placing))
            self.note("g_relocation_ends", page)
            self.placing.discard(page)
            zotero_sync_pdfs.relocation_ended(self.ws, page)

    def finish_relocations(self) -> None:
        """Every relocation written and ended, as before the link settles
        (a run that wrote nothing is run again, as a person's Find
        highlights in this PDF)."""
        for page, blocks in sorted(_stamped(self.ws).items()):
            self.placing.add(page)
            self._place(page, blocks)
        for page in sorted(self.placing):
            self.note("g_relocation_ends", page)
            zotero_sync_pdfs.relocation_ended(self.ws, page)
        self.placing.clear()

    def _place(self, page, blocks) -> None:
        batch = [{"op": "set", "id": block_id, "props": {"pdf_position": {k: v for k, v in pos.items()
                                                                            if k != "doc_id"}}}
                 for block_id, pos in blocks]
        try:
            ops.commit_ops(self.ws, page, batch, actor=self.owner, client=highlight_relocate.CLIENT)
        except OpError:
            self.note("refused", "", page)  # in Recently deleted: placed once it is back

    def any(self):
        edits = [getattr(self, name) for name in sorted(dir(self)) if name[:2] in ("z_", "g_")]
        self.rnd.choice(edits)()


def _settle(c, ws, fake, edits) -> None:
    """Rounds until one writes nothing and leaves the link settled: nothing
    due but what a wait holds back, no round asked for at once (a unit that
    queues itself again stops each round at ``ROUND_UNITS``, or asks for the
    next); each round after the relocations the one before started (a
    download's replacement) have run to their end."""
    for _ in range(8):
        edits.finish_relocations()
        calls = len(fake.calls)
        with connect_pages_db(ws) as conn:
            before = conn.execute("SELECT COUNT(*) FROM page_ops WHERE client = 'zotero'").fetchone()[0]
        drop_pending(ws)
        status = _sync(c, ws, ok=False)
        with connect_pages_db(ws) as conn:
            after = conn.execute("SELECT COUNT(*) FROM page_ops WHERE client = 'zotero'").fetchone()[0]
        if not any(m in WRITES for m, _, _ in fake.calls[calls:]) and after == before and not (
                edits.placing or _stamped(ws)) and not _due_now(ws) and not asked_soon(ws):
            assert not status.get("last_error"), status
            return
    recent = "\n".join(f"  {what} {key} {value}" for what, key, value in edits.log[-25:])
    raise AssertionError(f"the link never settled: due {_due_now(ws)}, another round asked for at once: "
                         f"{asked_soon(ws)}, backlog {status.get('backlog')}\n{recent}")


def _needless_beside(unit, placing) -> str:
    """Why the new attachment an item's unit is about to make for its
    page's PDF should not be made now, "" when it may: beside a PDF of
    Zotero's while the page's highlights are being placed, or although each
    of that PDF's annotations is one the page holds a placed highlight for
    (the fuzz makes no dashed or lost one), which Gamma moves with its file
    in place."""
    att = None if unit.standalone else unit._primary()
    if att is None:
        return ""
    if unit.page_id in placing:
        return "while its highlights are being placed"
    view = unit.blocks.pv if unit.blocks is not None else None
    if view is None:
        with connect_pages_db(unit.r.ws) as conn:
            view = zotero_local.read_page(conn, unit.page_id, notes=False)
    blocks = {a.block["id"]: a.block for a in (view.annotations if view else ())}
    for child in unit.children:
        data = child.get("data") or {}
        if data.get("itemType") != "annotation" or data.get("parentItem") != att["key"] or data.get("deleted"):
            continue
        row = unit.rows.get(child["key"])
        block = blocks.get(row["block_id"]) if row is not None else None
        if block is None or "doc_id" in ((block.get("properties") or {}).get("pdf_position") or {}):
            return ""  # one Gamma holds no placed highlight for: Gamma's file goes beside it
    return f"beside {att['key']} although each of its annotations could move with Gamma's file"


def _converged(ws, fake, made, edits) -> None:
    """Both sides hold the same records, tags, collections, trash,
    annotations and notes; no PDF of Gamma's went beside Zotero's
    needlessly (``_needless_beside``)."""
    for page_id, why in edits.beside:
        raise AssertionError(f"a new attachment for {page_id}'s PDF {why}\n{edits.history(page_id)}")
    items = {d["key"]: d for d in fake.items(LIB)}
    collections = {d["key"]: d for d in fake.collections(LIB)}
    rows = _rows(ws)
    bound = {row["page_id"]: key for key, row in rows.items()
             if row["kind"] == "attachment" and row["page_id"] and row["state"] in HELD_ROWS}
    with connect_pages_db(ws) as conn:
        labels = label_names(conn)
        folders = {fid: name for fid, _, name, _ in tree_rows(conn, FOLDERS)}
    folder_of = {key: row["block_id"] for key, row in rows.items()
                 if row["kind"] == "collection" and row["state"] == "live"}
    for key, row in rows.items():
        if row["kind"] == "collection" and row["state"] == "live":
            assert folders.get(row["block_id"]) == collections[key]["name"], f"{key}\n{edits.history(key)}"
    for key in made["items"]:
        row, data = rows[key], items[key]
        page = _block(ws, row["page_id"])
        assert page is not None, key
        meta = page["props"]["meta"]
        where = f"{key}: Zotero {data.get('title')!r}, Gamma {meta.get('title')!r}\n{edits.history(key)}"
        assert (page["parent"] == TRASH) == bool(data.get("deleted")), where
        assert meta["title"] == data["title"] and meta.get("year", "") == (data.get("date") or "")[:4], where
        assert {labels[lid].casefold() for lid in page["props"].get(LABELS) or ()} == {
            t["tag"].casefold() for t in data["tags"] if not t.get("type")}, where
        live = {k for k in data.get("collections") or () if not collections[k].get("deleted")}
        filed = {f for f in page["props"].get(FOLDERS) or () if f in folders}  # (a page in Recently deleted
        assert {folder_of.get(k) for k in live} == filed, where                  # keeps a folder gone since)
    for key, row in rows.items():  # a collection Zotero has in its trash has no folder here, and the other way
        if row["kind"] == "collection" and key in collections:
            where = f"{key}\n{edits.history(key)}"
            assert (row["state"] == "trashed") == bool(collections[key].get("deleted")), where
            assert (row["state"] == "live") == (row["block_id"] in folders), where
    pages = {made["items"][n]: rows[made["items"][n]]["page_id"] for n in range(len(made["pdfs"]))}
    for key in made["annotations"]:
        row, data = rows.get(key), items[key]
        if items[items[data["parentItem"]]["parentItem"]].get("deleted"):
            continue  # its page is in Recently deleted: nothing of it changes here
        if bound.get(rows[items[data["parentItem"]]["parentItem"]]["page_id"]) != data["parentItem"]:
            continue  # its PDF is no longer the page's (Gamma's went beside it): it stays in Zotero
        block = _block(ws, row["block_id"]) if row and row["block_id"] else None
        where = f"{key}: {row and row['state']}\n{edits.history(key)}"
        if data.get("deleted"):  # in Zotero's trash: gone here (or never here: trashed before a round saw it)
            assert (row is None or row["state"] == "trashed") and block is None, where
            continue
        assert row is not None and row["state"] in ("live", "readonly") and block is not None, where
        assert (block["props"]["color"][:7], block["content"]) == (
            data["annotationColor"], data["annotationComment"]), where
    for block_id, n in made["gamma_highlights"]:
        block = _block(ws, block_id)
        if block is None or _block(ws, pages[made["items"][n]])["parent"] == TRASH:
            continue
        found = [(k, r) for k, r in rows.items() if r["kind"] == "annotation" and r["block_id"] == block_id]
        assert len(found) == 1 and found[0][0] in items, f"{block_id}: {found}\n{edits.history(block_id)}"
        data = items[found[0][0]]
        assert not data.get("deleted") and (block["props"]["color"][:7], block["content"]) == (
            data["annotationColor"], data["annotationComment"]), f"{block_id}\n{edits.history(block_id)}"
    for page_id in made["gamma_papers"]:  # each paper made here is one item, alike on both sides
        page = _block(ws, page_id)
        found = [(k, r) for k, r in rows.items() if r["kind"] == "item" and r["page_id"] == page_id]
        assert len(found) == 1 and found[0][1]["state"] in ("live", "trashed"), f"{page_id}: {found}"
        data = items[found[0][0]]
        assert (data["title"], bool(data.get("deleted"))) == (page["props"]["meta"]["title"],
                                                               page["parent"] == TRASH), page_id
    titles = [d["title"] for d in items.values() if d.get("itemType") == "journalArticle"]
    assert len(titles) == len(set(titles)), "an item made twice"
    for n in range(len(made["pdfs"])):  # each paper's PDF: one attachment, the same file on both sides
        key = made["items"][n]
        page_id = rows[key]["page_id"]
        page = _block(ws, page_id)
        if page["parent"] == TRASH:
            continue
        held = [k for k, r in rows.items() if r["kind"] == "attachment" and r["page_id"] == page_id
                and r["state"] in HELD_ROWS]
        where = f"{key}: {held}\n{edits.history(key)}"
        doc = page["props"].get("doc_id")
        assert len(held) == 1 and doc and held[0] in items, where
        assert items[held[0]].get("md5") == file_md5(storage.open_path(ws, f"{doc}.pdf")), where
    for page_id in made["gone_note_pages"]:  # a note page whose note Zotero removed for good
        page = _block(ws, page_id)
        mine = [(k, r) for k, r in rows.items() if r["kind"] == "note" and r["page_id"] == page_id and not r["parent"]]
        where = f"{page_id}: {mine}\n{edits.history(page_id)}"
        if page["parent"] == TRASH:  # unchanged here when it went: to Recently deleted, its row with it
            assert not mine, where
            continue
        assert len(mine) == 1 and mine[0][1]["state"] == "live" and mine[0][0] in items, where  # a new note
        data = items[mine[0][0]]
        with connect_pages_db(ws) as conn:
            html = zotero_sync_blocks.note_page_html(conn, page_id)
        assert not data.get("deleted") and not data.get("parentItem"), where
        assert zotero_sync_blocks.note_words(html) == zotero_sync_blocks.note_words(data["note"]), where
    for key in made["note_pages"]:  # a standalone note and its note page
        row, data = rows[key], items[key]
        page = _block(ws, row["page_id"])
        where = f"{key}\n{edits.history(key)}\n{edits.history(row['page_id'])}"
        assert (page["parent"] == TRASH) == bool(data.get("deleted")), where
        with connect_pages_db(ws) as conn:
            html = zotero_sync_blocks.note_page_html(conn, row["page_id"])
        assert zotero_sync_blocks.note_words(html) == zotero_sync_blocks.note_words(data["note"]), (
            f"{key}: {html!r} / {data['note']!r}\n{where}")
    for key in made["notes"]:
        row, data = rows[key], items[key]
        if items[data["parentItem"]].get("deleted"):
            continue
        assert (row["state"] == "trashed") == bool(data.get("deleted")), f"{key}\n{edits.history(key)}"
        if row["state"] != "live":
            continue
        with connect_pages_db(ws) as conn:
            view = zotero_local.read_page(conn, row["page_id"], note_roots=[row["block_id"]])
        region = view.regions.get(row["block_id"]) if view else None
        assert region is not None and zotero_sync_blocks.note_words(region.html) == zotero_sync_blocks.note_words(
            data["note"]), f"{key}\n{edits.history(key)}"


def _quiet(c, ws, edits) -> None:
    """Once the edits stopped and the link settled, more rounds stay
    settled: each leaves nothing due (only what a wait holds back), asks the
    clock for no round at once, and none adds a conflict row."""
    conflicts = _conflict_count(ws)
    for n in range(QUIET_ROUNDS):
        drop_pending(ws)
        status = _sync(c, ws, ok=False)
        due, soon = _due_now(ws), asked_soon(ws)
        recent = "\n".join(f"  {what} {key} {value}" for what, key, value in edits.log[-25:])
        assert not status.get("last_error") and not due and not soon, (
            f"quiet round {n}: due {due}, another round asked for at once: {soon}, "
            f"error {status.get('last_error')!r}, backlog {status.get('backlog')}\n{recent}")
    assert _conflict_count(ws) == conflicts, f"quiet rounds added {_conflict_count(ws) - conflicts} conflict rows"


def _round(c, ws, fake, rnd, edits) -> None:
    """A round as the clock runs one, now and then killed at one of its
    first writes to Zotero (right after it landed, or before it went) or
    right after one of its commits here, and the next start after it."""
    edits.note("round", "")
    if rnd.random() >= 0.15:
        _sync(c, ws, ok=False)
        _trace(ws, fake, edits)
        return
    at, sent, here = rnd.randint(1, 3), rnd.random() < 0.5, rnd.random() < 0.5
    real, seen = fake.handle, []

    def handle(method, url, headers, body):
        if method in WRITES and not here:
            seen.append(url)
            if len(seen) == at:
                if sent:
                    real(method, url, headers, body)
                raise Crash(url)
        return real(method, url, headers, body)

    def listener(ws_, client, page_id):
        if here and client == zotero_sync_rows.CLIENT:
            seen.append(page_id)
            if len(seen) == at:
                raise Crash(page_id)

    fake.handle = handle
    ops.commit_listeners.append(listener)
    try:
        drop_pending(ws)
        zotero_sync.sync_workspace(ws)
    except Crash as e:
        edits.note("crashed", "", f"{'after commit' if here else 'at write'} {at}: {e}")
    finally:
        fake.handle = real
        ops.commit_listeners.remove(listener)
    zotero_sync.reset_interrupted()
    zotero_sync.forget_access()
    _trace(ws, fake, edits)


def _trace(ws, fake, edits) -> None:
    """With ``ZOTERO_FUZZ_TRACE=<key>``: that object's row (and its folder,
    for a collection) and Zotero's object, after each round, in the log a
    failure prints."""
    key = os.environ.get("ZOTERO_FUZZ_TRACE")
    if not key:
        return
    row = _rows(ws).get(key) or {}
    with connect_pages_db(ws) as conn:
        folders = {fid: (name, parent) for fid, parent, name, _ in tree_rows(conn, FOLDERS)}
    data = fake.data(LIB, key)
    edits.note("trace", key, f"row {row.get('state')} {row.get('block_id')} {folders.get(row.get('block_id'))} "
                             f"z {row.get('zbase')} g {row.get('gbase')} | Zotero {data.get('name')} "
                             f"{data.get('parentCollection')} {data.get('deleted')}")


@pytest.mark.parametrize("seed", SEEDS)
def test_random_edits_on_both_sides_settle_with_both_sides_alike(me, fake, owner_id, seed, monkeypatch):
    placing: set = set()  # the pages whose relocation runs, as the stub reads it

    def start(owner, ws_, page_id, title, new_doc, *, outgoing=None, quiet=True):
        placing.add(page_id)
        return None, ""

    monkeypatch.setattr(pages_router, "_start_relocation", start)
    monkeypatch.setattr(zotero_sync_pdfs, "_relocating", lambda r, side: side.page_id in placing)
    monkeypatch.setattr(zotero_sync, "ROUND_UNITS", ROUND_UNITS)
    new_attachment, needless = zotero_sync_pdfs.UnitPdf._new_attachment, []

    def beside(unit, row, **kw):  # each new attachment checked as it is made (``_needless_beside``)
        why = _needless_beside(unit, placing)
        if why:
            needless.append((unit.page_id, why))
        return new_attachment(unit, row, **kw)

    monkeypatch.setattr(zotero_sync_pdfs.UnitPdf, "_new_attachment", beside)
    made = {**generated_library(fake, 12, pdfs=6, annotations=2, notes=6, collections=5), "gamma_highlights": [],
            "gamma_papers": [], "gone_note_pages": [], "note_pages": [
                fake.add_item(LIB, {"itemType": "note", "note": _note(f"<h1>Standalone {n}</h1><p>First line.</p>")})
                for n in range(2)]}
    r = me.post("/api/zotero/links", json={"library": LIB})
    assert r.status_code == 201, r.text
    ws = r.json()["workspace_id"]
    _sync(me, ws, ok=False)
    rnd = random.Random(seed)
    edits = _Edits(rnd, ws, owner_id, fake, made, placing)
    edits.beside = needless
    for _ in range(STEPS):
        link = zotero_sync.get_link(ws)
        dice = rnd.random()
        if dice < 0.03:  # detached for a while, then linked again
            edits.note("relink" if link["mode"] == "off" else "detach", "")
            r = me.post(f"/api/zotero/links/{ws}/{'relink' if link['mode'] == 'off' else 'detach'}", json={})
            assert r.status_code == 200, r.text
        elif dice < 0.07 and link["mode"] != "off":  # receive only for a while, then two-way again
            mode = "pull" if link["mode"] == "two-way" else "two-way"
            edits.note("mode", "", mode)
            assert me.patch(f"/api/zotero/links/{ws}", json={"mode": mode}).status_code == 200
        elif dice < 0.09 and link["mode"] == "two-way":  # one side's version of everything both have
            direction = rnd.choice(("pull", "push"))
            edits.note("force", "", direction)
            assert me.post(f"/api/zotero/links/{ws}/force", json={"direction": direction}).status_code == 200
        edits.any()
        if rnd.random() < 0.35:
            _round(me, ws, fake, rnd, edits)
    if zotero_sync.get_link(ws)["mode"] == "off":
        assert me.post(f"/api/zotero/links/{ws}/relink", json={}).status_code == 200
    if zotero_sync.get_link(ws)["mode"] != "two-way":
        assert me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"}).status_code == 200
    _settle(me, ws, fake, edits)
    # what is in both trashes takes no edit here: each comes back, on one side or the other, with what the
    # other side changed meanwhile
    both = [key for key in made["items"] if fake.data(LIB, key).get("deleted")
            and _block(ws, _rows(ws)[key]["page_id"])["parent"] == TRASH]
    for n, key in enumerate(both):
        edits.note("z_restore" if n % 2 else "g_restore", key)
        if n % 2:
            fake.restore(LIB, key)
        else:
            with connect_pages_db(ws) as conn:
                ops.restore_page(ws, conn, _rows(ws)[key]["page_id"], actor=owner_id, client="t")
    _settle(me, ws, fake, edits)
    _converged(ws, fake, made, edits)
    _quiet(me, ws, edits)
