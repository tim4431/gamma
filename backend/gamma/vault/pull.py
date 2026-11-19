"""**Pull**, Gamma to disk (gamma/vault/sync.py runs it), for each page whose
seq passed its base's (and, when the labels tree changed, each note with
tags): its blocks with a span (``md_lens.project``), each block's text in the
file's space (gamma/vault/links.py; a block whose text is the base's keeps
its file text), the file patched by ``md_lens.shape``. What a file cannot
hold as Gamma has it, a block split or lifted, goes back to Gamma as ops
before the file is written, only where the vault pushes everything and no
caret is on those blocks; a split that cannot go then waits, the file as it
was. The file must still be the base, by its hash: one changed since is
pushed first, once a round, or with push off kept and reported. The write is
``disk.replace_note``, what it replaced kept in the history. A title changed
renames the note (a ``title:`` key is patched instead), a first filing
changed moves it (a row that recorded none, an older folder's, moves only
once Gamma changes it), a PDF going with it only when it is the note's stem
beside it; a folder renamed renames its directory; a page trashed, or one
that left the vault (a PDF read in place stored first), moves its files to
the vault's trash, where each keeps its row until its page is purged and the
trash has held it ``disk.TRASH_DAYS`` (a PDF another page reads in place
stored for it first), and a page back brings them back; but a note changed on
disk that push has not read stays, kept. A PDF pull copied out follows the
page's when Gamma replaces it, unless it changed on disk: in a vault that
does not push PDFs that one is kept and reported until a force pull. A page
that did not change is never written, nor a PDF pull did not put there; and a
page's new file that the disk holds already, with the very bytes pull would
write and no row (a vault made again over the files it kept when it was
removed), there or where the client kept a sub-page's, is taken as that
page's, never written beside. A PDF changed on disk whose page left the vault
stays where it is, no longer synced. A vault without notes files (an older
folder kept with its PDFs alone) writes PDFs only.
"""

import os
import stat
import time

from .. import gamma_sync, linked_files, md_lens, storage
from ..blocks_store import new_block_id
from ..documents.model import stored_name
from ..folder_links import KEPT_SHOWN
from ..jobs import error_text
from ..logbuf import log
from ..obsidian_export import anchor_marker
from . import disk, kinds, links, merge, pages, store
from .remote import Changed, Edit, Gone, Info, Page, Waiting
from .round import (LEFT_OUT, Base, Read, _base, _dir, _fields, _gamma_id, _join, _kind_of, _name, _named, _Rows,
                    _stem, _tags, _text, _token, _unknown)


BOTH_PDF = ("changed on this computer and in Gamma: a force pull writes Gamma's PDF here, a force push makes this "
            "file the page's")
UNREAD = "unread"          # the hash of an older client's note taken as it wrote it while a cloud placeholder held it
LEFT = "changed on this computer, and its page left the vault: it stays where it is, no longer synced"


def _tagged(text) -> bool:
    """Whether a note's text names tags in its front matter."""
    return bool(text) and text.lstrip("﻿").startswith("---") and bool(_tags(_fields(text)))


def _retexted(tree: list, texts: list) -> list:
    """``tree`` with each node's text the next of ``texts`` (document order)."""
    at = iter(texts)

    def node_of(node):
        out = {"id": node["id"], "content": next(at), "children": []}
        out["children"] = [node_of(child) for child in node["children"]]
        return out
    return [node_of(node) for node in tree]


# --- pull: Gamma's changes ---------------------------------------------------------------------

def _pull(rnd) -> None:
    """Gamma's changes onto the disk: folders renamed (never in a work
    folder, whose directories follow the disk), pages that left or went
    to Recently deleted or came back, each page that changed (and, once
    the labels tree changed, each note with tags: a label renamed moves no
    page's seq), and the pages a link asked an anchor of, once more."""
    rnd.layout = rnd.remote.layout(rnd.only)
    if rnd.only:
        if rnd.only in rnd.layout.pages:
            _tombstones(rnd, {rnd.only})
            rnd._each(_name_of(rnd, rnd.only), _pull_page, rnd, rnd.layout.pages[rnd.only])
        elif rnd.rows.of(rnd.only):
            rnd._each(_name_of(rnd, rnd.only), _left, rnd, rnd.only)
        return
    rnd.cursor = rnd.layout.cursor
    if rnd.layout.shared:       # filed below an earlier vault beside this one too: that vault's
        rnd.status["shared"] = [{"page": page_id, "title": title}
                                for page_id, title in rnd.layout.shared[:KEPT_SHOWN]]
    rnd.relabelled = rnd.state.get("labels") != rnd.layout.labels
    rnd.state["labels"] = rnd.layout.labels
    if not rnd.work:
        _pull_dirs(rnd)
    for page_id in sorted({row["page_id"] for row in rnd.rows.values() if row["page_id"]} - set(rnd.layout.pages)):
        rnd._each(_name_of(rnd, page_id), _left, rnd, page_id)
    _tombstones(rnd, set(rnd.layout.pages))
    rnd.total += len(rnd.layout.pages)
    for info in rnd.layout.pages.values():
        name = _name_of(rnd, info.id)
        rnd._report(info.title if name == info.id else name)     # a page with no file yet: its title
        rnd._each(name, _pull_page, rnd, info)
        rnd.done += 1
    for _ in range(2):
        again, rnd.again = rnd.again, set()
        for page_id in sorted(again & set(rnd.layout.pages)):
            rnd._each(_name_of(rnd, page_id), _pull_page, rnd, rnd.layout.pages[page_id], True)
    _drop_dirs(rnd)
    rnd._report()


def _drop_dirs(rnd) -> None:
    """The directories of folders gone from the vault in Gamma (deleted, or
    moved out of it): forgotten, and removed once their pages left them
    empty. A directory still holding a file stays, a person's, and so does
    a link or a junction put in its place (``disk.path``)."""
    for path, entry in sorted(store.dirs(rnd.id).items(), key=lambda d: -d[0].count("/")):
        if entry["folder_id"] in rnd.layout.folders:
            continue
        try:
            os.rmdir(disk.path(rnd.root, path))
        except OSError:
            pass      # gone already, holding a file, or a link (Outside)
        store.forget_dir(rnd.id, path)


def _name_of(rnd, page_id: str) -> str:
    """The file a page's failure is reported under: its notes, which a
    person edits and pull writes, where it has some, whatever order the
    rows come in (a note renamed only in case sorts after its PDF)."""
    rows = rnd.rows.of(page_id)
    return (rows.get(kinds.NOTES.name) or rows.get(kinds.PDF.name) or next(iter(rows.values())))[0] if rows else page_id


def _dir_of(rnd, folder_id: str) -> str:
    """The directory of a folder of the vault: the one recorded, else its
    names below the vault's folder, each one every disk takes."""
    if not folder_id or folder_id not in rnd.layout.folders:
        return ""
    recorded = {d["folder_id"]: path for path, d in store.dirs(rnd.id).items() if d["folder_id"]}
    if folder_id in recorded:
        return recorded[folder_id]
    parent, name = rnd.layout.folders[folder_id]
    return _join(_dir_of(rnd, parent), _name(name))


def _pull_dirs(rnd) -> None:
    """A folder renamed or moved in Gamma renames or moves its directory.
    A directory is its folder's name while the two give one vault name
    (``_name``): ``[Archive]`` and ``My  Notes`` keep theirs, and a folder
    moved takes its directory's name along."""
    for path in sorted(store.dirs(rnd.id), key=lambda p: p.count("/")):
        current = store.dirs(rnd.id)
        folder_id = (current.get(path) or {}).get("folder_id")
        if not folder_id or folder_id not in rnd.layout.folders or not os.path.isdir(rnd.root / path):
            continue
        parent, name = rnd.layout.folders[folder_id]
        own = path.rsplit("/", 1)[-1]
        want = _join(_dir_of(rnd, parent), own if _name(own) == _name(name) else _name(name))
        if want != path:
            rnd._each(path, _move_dir, rnd, path, disk.free_name(rnd.root, want, own=path))


def _move_dir(rnd, at: str, want: str) -> None:
    disk.move(rnd.root, at, want)
    store.repath(rnd.id, at, want)
    rnd.remote.relink(str(rnd.root / at), str(rnd.root / want))
    rnd.rows = _Rows(store.files(rnd.id))
    rnd.counts["renamed"] += 1


def _left(rnd, page_id: str) -> None:
    """A page the vault has files of that is no page of it now (in Recently
    deleted, filed elsewhere, gone for good): its files go to the vault's
    trash (``_to_trash``), a PDF read in place stored first when the page
    lives on, the page keeping everything. A note changed on disk since
    its base is the disk's word, which push has not read yet (a round of
    pull alone, a note still being written): the page's files stay and
    the note is kept, so that push brings the page back with it, or makes
    it a page again; a force pull keeps its bytes in the history first.
    A PDF changed on disk since its base (a person's annotated copy in a
    vault that does not push it) is the person's: it stays where it is,
    no longer synced, and is reported (``LEFT``), as the older client
    left one; never moved or deleted. In a work folder only the tracked
    note of a page that follows goes to the trash (a note a person
    trashed where the vault pulls); any other file, a paper's notes among
    them, stays where it is, its row an in-place tombstone: Gamma never
    moves a file the disk made. Each class goes first (``Kind.left``: the
    note kept, the PDF stored or left), the notes before the PDF."""
    rows = rnd.rows.of(page_id)
    if not rows:
        return
    for cls in kinds.CLASSES.values():
        at = rows.get(cls.name)
        if at and not cls.left(rnd, page_id, *at):
            return
    paths = [p for p, _row in rnd.rows.of(page_id).values()]
    if rnd.work:
        follows = rnd.work["pull"] and kinds.PDF.name not in rnd.rows.of(page_id)
        for path in [p for p in paths
                     if rnd.rows[p]["kind"] != kinds.NOTES.name or not follows or not rnd._tracked(p)]:
            store.bury_in_place(rnd.id, path)
            rnd.rows.pop(path, None)
            paths.remove(path)
            rnd.counts["removed"] += 1
    _to_trash(rnd, paths)


def _pdf_changed(rnd, path: str, row: dict) -> bool:
    """Whether the vault's PDF at ``path`` holds other bytes than its base
    says: its stat moved and its hash is another (one not there, or not
    to be read, is not)."""
    found = rnd._stat(path)
    if found is None or found.placeholder or not disk.moved(row, found):
        return False
    try:
        return linked_files.hash_file(disk.path(rnd.root, path))[0] != row["hash"]
    except (OSError, ValueError):
        return False


def _to_trash(rnd, paths: list) -> None:
    """Files moved to ``.gamma/trash/<stamp>/``, each row there first and
    kept there, a tombstone: the file comes back when its page comes back
    into the vault and is swept when the page is purged, thirty days on at
    the earliest (``_tombstones``, ``_sweep``), so the trash holds no file
    that no row holds."""
    when, taken = disk.stamp(), rnd._tomb_paths()
    for path in paths:
        row, there = rnd.rows[path], not rnd._gone(path)
        token = _token()
        trash = disk.trash_path(rnd.root, path, when, taken)
        taken.add(trash)
        store.put_pending(rnd.id, path, row["kind"], {"op": "trash", "token": token})
        store.bury(rnd.id, path, trash)
        if there:
            disk.move(rnd.root, path, trash)
            rnd.remote.relink(str(rnd.root / path), str(rnd.root / trash))
        store.put(rnd.id, trash, pending=None)
        rnd.rows.pop(path, None)
        rnd.counts["removed"] += 1


def _tombstones(rnd, page_ids: set) -> None:
    """Tombstones whose page came back (its files come back from the trash
    under a free name) or went for good (the file with it, once the trash
    has held it long enough and another page that reads a PDF of it in
    place has it stored: ``_sweep``, ``spare``)."""
    for tomb in store.tombstones(rnd.id):
        if tomb["pending"] or (rnd.only and tomb["page_id"] != rnd.only):
            continue
        if tomb["page_id"] in page_ids:
            rnd._each(tomb["was"], _restore_file, rnd, tomb)
        elif rnd.remote.state(tomb["page_id"]) is None:
            rnd._each(tomb["was"], _sweep, rnd, tomb)


def _sweep(rnd, tomb: dict) -> None:
    """A tombstone whose page went for good (or that has none: a copy a
    file come back left, ``pages._unbury``), its file in the trash with it once
    the trash has held it ``disk.TRASH_DAYS`` (a stamp that says no
    time never: the file stays). An in-place tombstone's file stays where
    it is; a PDF its row read for another page too is stored for that one
    first, as one the trash takes is (``Kind.swept``)."""
    cls = kinds.by_kind(tomb["kind"])
    if store.in_place(tomb):
        if rnd._there(tomb["was"]):
            cls.swept(rnd, tomb, tomb["was"])
    elif tomb["path"].startswith(f"{disk.TRASH}/") and rnd._there(tomb["path"]):
        at = disk.trashed_at(tomb["path"])
        if at is None or time.time() - at < disk.TRASH_DAYS * 86400:
            return
        cls.swept(rnd, tomb, tomb["path"])
        disk.purge(rnd.root, tomb["path"])
    store.forget(rnd.id, tomb["path"])


def _restore_file(rnd, tomb: dict) -> None:
    """A file of a page brought back, from the trash where it went; one
    the trash does not hold (it went from the disk) is written anew, but
    where a file is back at the place it lived and this round cannot read
    it (a page's round, or one of pull alone, in a vault that pushes
    everything): that may be the page's own, so the page waits for a
    round that reads the disk (``buried``), never writing another file
    beside it. An in-place tombstone's file is where it was: tracked, its
    row is that file's again, nothing moved. Untracked, in a work folder
    whose page has no other file tracked and there, the page goes back to
    Recently deleted, where a work folder's page whose file the vault
    does not track is; one with its other file is kept by that one, and in
    a kept folder (a work folder's push lowered since) the page stays
    Gamma's: either way the tombstone is forgotten. A place the rules
    leave out is not written to."""
    if store.in_place(tomb):
        page_id, was = tomb["page_id"], tomb["was"]
        if rnd._tracked(was) and rnd._there(was):
            store.unbury(rnd.id, tomb["path"], was)
            rnd.rows = _Rows(store.files(rnd.id))
        elif rnd.work and not _kept_by(rnd, page_id, tomb["path"]):
            rnd.remote.trash(page_id)
            rnd.buried.add(page_id)
        else:
            store.forget(rnd.id, tomb["path"])
            if rnd.work:
                rnd._keep(was, LEFT_OUT)
        return
    if rnd._gone(tomb["path"]):
        if rnd.push_all and (rnd.scan is None or not rnd.pushing) and rnd._there(tomb["was"]):
            rnd.buried.add(tomb["page_id"])
            raise Waiting("a file is back where its page's lived; the next round reads it")
        store.forget(rnd.id, tomb["path"])
        return
    if not rnd._tracked(tomb["was"]):
        rnd._not_tracked(tomb["was"], tomb["page_id"])
        return
    target = rnd._free(tomb["was"])
    store.put(rnd.id, tomb["path"], pending={"op": "restore", "token": _token(), "path": target})
    disk.move(rnd.root, tomb["path"], target)
    rnd.remote.relink(str(rnd.root / tomb["path"]), str(rnd.root / target))
    store.unbury(rnd.id, tomb["path"], target)
    store.put(rnd.id, target, pending=None)
    rnd.rows = _Rows(store.files(rnd.id))
    rnd.counts["added"] += 1


def _kept_by(rnd, page_id: str, tomb_path: str) -> bool:
    """Whether a page keeps a file other than the in-place tombstone at
    ``tomb_path``: a row of it, or another in-place tombstone of it whose
    file is tracked and there, which this round reads back."""
    if rnd.rows.of(page_id):
        return True
    return any(t["page_id"] == page_id and t["path"] != tomb_path and store.in_place(t)
               and rnd._tracked(t["was"]) and rnd._there(t["was"])
               for t in store.tombstones(rnd.id))


# --- pull: one page ---------------------------------------------------------------------------

def _pull_page(rnd, info: Info, again: bool = False) -> None:
    """One page of the vault onto the disk, when it changed since the base:
    where its files are first, a stored PDF it has and the vault has not
    copied out (a page made in Gamma, a PDF attached there to a page of
    notes) or has replaced since, then its notes. A PDF of the disk's that
    Gamma replaced is that file's problem: pull never writes it. A page
    whose file a tombstone waits to read back (``buried``) waits too. A
    work folder's PDFs are the disk's: pull copies none out, and says so
    of a stored PDF filed there; a document of another format is never
    copied out (a work folder's file is the disk's, a kept folder writes
    notes and PDFs alone). Then the page's main class pulls it
    (``Kind.pull``: a note's and a paper's write its notes, a file's
    nothing)."""
    if info.id in rnd.buried:
        return
    folder = rnd._first(info.folders, rnd.layout.folders)
    rows = rnd.rows.of(info.id)
    if rows:
        _place(rnd, info, rows, folder)
        rows = rnd.rows.of(info.id)
    notes, pdf = rows.get(kinds.NOTES.name), rows.get(kinds.PDF.name)
    row = (notes or pdf or next(iter(rows.values()), (None, None)))[1]
    if (row is not None and not again and rnd.force != "pull" and info.seq == row["seq"]
            and not (rnd.relabelled and notes and _tagged(notes[1]["text"]))):
        rnd.counts["unchanged"] += 1
        return
    if info.doc_id and info.format == "pdf" and (not pdf or pdf[1]["hash"] != info.doc_id):
        if rnd.work and not pdf:
            rnd._keep(_name_of(rnd, info.id), "a PDF filed here in Gamma is not written into the directory")
        elif not rnd.work and (not pdf and not info.linked or pdf and pdf[1]["ours"]):
            _copy_pdf(rnd, info, folder, notes, pdf)
        elif pdf:
            rnd._problem(pdf[0], "its page's PDF was replaced in Gamma, and this file is the one before")
    rows = rnd.rows.of(info.id)
    (kinds.main(rows) or kinds.NOTES).pull(rnd, info, rows, folder)


def _place(rnd, info: Info, rows: dict, folder: str) -> None:
    """The page's files where its title and first filing put them, as its
    main class places them (``Kind.place``; a file's are only recorded)."""
    cls = kinds.main(rows)
    if cls is not None:
        cls.place(rnd, info, rows, folder)


def _place_notes(rnd, info: Info, rows: dict, folder: str) -> None:
    """A note's or a paper's files placed: a new title renames the note (a
    ``title:`` key is patched by the write instead), a new first filing
    moves it, and a PDF goes with the note only when it is the note's
    stem beside it: one alone is the disk's. In a work folder only a note
    that follows Gamma (no document, where the vault pulls) is renamed or
    moved, alone, and its file's name written; anything else's title and
    filing are only recorded (a library file's title is free, its file
    keeping its name). A place the rules leave out is not moved to."""
    notes, pdf = rows.get(kinds.NOTES.name), rows.get(kinds.PDF.name)
    path, row = notes or pdf
    moved = row["folder"] is not None and folder != row["folder"]      # none: an older folder's, recorded at its write
    renamed = row["title"] is not None and info.title != row["title"]
    if not moved and not renamed:
        return
    follows = not info.doc_id and bool(rnd.work and rnd.work["pull"])
    if notes and (follows or not rnd.work):
        if notes[0] in rnd.kept_paths:
            return               # changed on disk and not pushed: placed once it is the base again
        directory = _dir_of(rnd, folder) if moved else _dir(path)
        titled = bool(_fields(notes[1]["text"]).get("title"))
        stem = _name(info.title, ".md") if renamed and not titled else _stem(notes[0])
        for kind, (at, at_row) in rows.items():
            if kind == kinds.PDF.name and (rnd.work or not (_stem(at) == _stem(notes[0]) and _dir(at) == _dir(notes[0]))):
                continue
            wanted = _join(directory, stem + os.path.splitext(at)[1])
            if wanted != at and not rnd._tracked(wanted):
                rnd._not_tracked(wanted, info.id)
                return
            want = rnd._free(wanted, own=at)
            if want != at:
                _rename(rnd, at, want, at_row)
                _dirs_made(rnd, _dir(want), folder)
                if rnd.work and info.work_folder:
                    _file_named(rnd, info.id, want)
    for _kind, (at, at_row) in rnd.rows.of(info.id).items():
        rnd._put(at, at_row, title=info.title, folder=folder)


def _file_named(rnd, page_id: str, path: str) -> None:
    """A work folder's file's name written (``file_name``) after pull
    wrote or renamed its note, its rows' seq following where level."""
    before, after = rnd.remote.set_page(page_id, props={"file_name": path.rsplit("/", 1)[-1]})
    for at, at_row in rnd.rows.of(page_id).values():
        if at_row["seq"] == before:
            rnd._put(at, at_row, seq=after)


def _rename(rnd, at: str, want: str, row: dict) -> None:
    """A file renamed or moved by pull, in the journal first."""
    token = _token()
    store.put_pending(rnd.id, at, row["kind"], {"op": "move", "token": token, "path": want, "hash": row["hash"],
                                                    "text": row["text"], "nodes": row["nodes"], "page_id": row["page_id"]})
    try:
        disk.move(rnd.root, at, want)
    except BaseException:
        store.drop(rnd.id, at, token)
        raise
    rnd.remote.relink(str(rnd.root / at), str(rnd.root / want))
    store.promote(rnd.id, at, token)
    rnd.rows = _Rows(store.files(rnd.id))
    rnd.counts["renamed"] += 1


def _copy_pdf(rnd, info: Info, folder: str, notes=None, pdf=None):
    """A page's stored PDF copied out, followed from then on: the one PDF
    pull writes, beside the page's notes under their stem (a pair the scan
    reads back as one page), else in its folder's directory under its
    title. ``pdf``: the copy an earlier pull made of a PDF Gamma has
    replaced since, written over while it is still what pull wrote; one
    changed on disk is the next push's, or in a vault that does not push
    PDFs kept and reported until a force pull writes Gamma's over it, and
    so is one push will not read again until it changes (a folder kept
    before whose client would have kept it: gamma/vault/upgrade.py). A
    new copy the disk holds already where it would go, or where the
    gamma-sync client kept a sub-page's (``_beside_parent``), with those
    bytes and no row, is taken (``_take``); bytes that cannot be the
    stored file wait (``_fetched``). → its ``(path, row)``."""
    if pdf:
        try:
            ours = linked_files.hash_file(disk.path(rnd.root, pdf[0]))[0] == pdf[1]["hash"]
        except (OSError, ValueError):
            ours = False
        if not ours and rnd.force != "pull":
            found = rnd._stat(pdf[0])
            if not rnd.push_pdfs:
                rnd._keep(pdf[0], "changed on this computer and not pushed; a force pull replaces it", True)
            elif found is not None and not disk.moved(pdf[1], found):
                rnd._keep(pdf[0], BOTH_PDF, True)
            return pdf
    directory = _dir(notes[0]) if notes else _dir_of(rnd, folder)
    wanted = _join(directory, f"{_stem(notes[0]) if notes else _name(info.title, '.pdf')}.pdf")
    beside = "" if notes or pdf else _beside_parent(rnd, info, ".pdf")
    held = next((at for at in (wanted, beside) if at and rnd._tracked(at) and _holds_doc(rnd, at, info.doc_id)),
                "") if not pdf else ""
    if held:
        _take(rnd, held, kinds.PDF.name, info.id, {"hash": info.doc_id, "text": "", "title": info.title,
                                                   "folder": folder, "seq": 0, "problem": ""})
        if held == wanted:
            _dirs_made(rnd, directory, folder)
        return held, rnd.rows[held]
    if not pdf and not rnd._tracked(wanted):
        rnd._not_tracked(wanted, info.id)
        return pdf
    data = _fetched(rnd.remote, stored_name(info.doc_id, ".pdf"))
    if data is None:
        return pdf
    path = pdf[0] if pdf else rnd._free(wanted)
    token = _token()
    fields = {"hash": info.doc_id, "ours": 1, "title": info.title, "folder": folder, "seq": pdf[1]["seq"] if pdf else 0,
              "problem": ""}
    journal = _journal(rnd, path, kinds.PDF.name, token, data, info.id, fields, "")
    if pdf:
        written = disk.replace_note(rnd.root, path, data, journal)
    else:
        written = disk.write_new(rnd.root, path, data, ready=journal)
        _dirs_made(rnd, directory, folder)
    store.promote(rnd.id, path, token, **fields, size=written.size, mtime_ns=written.mtime_ns,
                        file_id=written.file_id, read_at=time.time())
    rnd._promoted(path)
    rnd.counts["updated" if pdf else "added"] += 1
    return path, rnd.rows[path]


def _holds(rnd, rel: str, data: bytes) -> bool:
    """Whether ``rel``, a file no row holds, is there with the very bytes
    ``data`` that pull would write as a page's new notes."""
    if rel in rnd.rows:
        return False
    try:
        st = disk.lstat(rnd.root, rel)
        if st is None or not stat.S_ISREG(st.st_mode) or disk._placeholder(st) or st.st_size != len(data):
            return False
        return disk.read(rnd.root, rel) == data
    except OSError:
        return False


def _holds_doc(rnd, rel: str, doc_id: str) -> bool:
    """Whether ``rel``, a file no row holds, is there with the bytes of
    the stored PDF ``doc_id`` that pull would copy out."""
    if rel in rnd.rows:
        return False
    try:
        st = disk.lstat(rnd.root, rel)
        if st is None or not stat.S_ISREG(st.st_mode) or disk._placeholder(st):
            return False
        return linked_files.hash_file(disk.path(rnd.root, rel))[0] == doc_id
    except (OSError, ValueError):
        return False


def _take(rnd, rel: str, kind: str, page_id: str, fields: dict) -> None:
    """A file the disk holds already with the very bytes pull would write
    for a page that has none here (``_holds``, ``_holds_doc``: a vault made
    again over the files it kept when it was removed, a copy of the vault
    put back): taken as that page's, as pull's own, never written a second
    time beside it."""
    st = os.stat(disk.path(rnd.root, rel))
    store.put(rnd.id, rel, kind=kind, page_id=page_id, size=st.st_size, mtime_ns=st.st_mtime_ns,
                    file_id=str(st.st_ino), read_at=time.time(), ours=1, **fields)
    rnd._promoted(rel)
    rnd.counts["unchanged"] += 1


def _dirs_made(rnd, directory: str, folder: str) -> None:
    """A directory a write made, recorded as its folder's."""
    if directory and directory not in store.dirs(rnd.id):
        try:
            file_id = str(os.stat(rnd.root / directory).st_ino)
        except OSError:
            file_id = ""
        store.put_dir(rnd.id, directory, folder, file_id)


def _notes_path(rnd, info: Info, pdf, folder: str) -> str:
    """Where a page's new notes go: in the vault's notes folder or beside
    its PDF under the PDF's stem; else ``<dir>/<title>.md``. Not made
    free yet: a note there may be the page's already (``_older_note``)."""
    if pdf:
        directory = (rnd.marker.get("notes_folder") or "").strip("/") or _dir(pdf[0])
        return _join(directory, f"{_stem(pdf[0])}.md")
    return _join(_dir_of(rnd, folder), f"{_name(info.title, '.md')}.md")


def _beside_parent(rnd, info: Info, ext: str) -> str:
    """Where the gamma-sync client kept a sub-page's file of ``ext``: in a
    directory named for its parent's files, beside them
    (gamma/folder_sync.py); "" for a library page, or one whose parent has
    no file here. A file there may be the page's already, which pull then
    takes (``_older_note``, ``_holds``) and never writes a second time
    where it puts a new one."""
    files = rnd.rows.of(info.parent) if info.parent else {}
    at = (files.get(kinds.NOTES.name) or files.get(kinds.PDF.name) or ("",))[0]
    return _join(_join(_dir(at), _stem(at)), f"{_name(info.title, ext)}{ext}") if at else ""


def _older_note(rnd, path: str, page_id: str) -> bool:
    """Whether the note at ``path``, which no row holds, names the page by
    its ``gamma_id``: an older client's note, kept beside its paper while
    the vault's notes were off. It is that page's notes, never one written
    beside it (``pages._adopt``); one the vault's rules leave out is none."""
    if path in rnd.rows or not rnd._tracked(path):
        return False
    try:
        st = disk.lstat(rnd.root, path)
        if st is None or disk._placeholder(st) or not os.path.isfile(disk.path(rnd.root, path)):
            return False
        text = disk.read(rnd.root, path).decode("utf-8", "replace")
    except OSError:
        return False
    if _gamma_id(text) != page_id:
        return False
    pages._adopt(rnd, path, page_id,
                 Read(path, kinds.NOTES.name, st.st_size, st.st_mtime_ns, str(st.st_ino), time.time(), ""))
    return True


def _write_notes(rnd, info: Info, notes, pdf, folder: str) -> None:
    """The page's blocks with a span written over its notes file as a patch
    of the base (``md_lens.shape``); a base that names none of its blocks
    (``_unknown``) is written as a new file is, the anchors that note held
    kept. A new note the disk holds already, byte for byte, where pull
    puts it or where the gamma-sync client kept a sub-page's
    (``_beside_parent``), is taken (``_take``). A vault without notes (a
    folder kept before with its PDFs alone) writes none, its PDF level
    with the page. Pull writes no note where the vault's rules leave it
    out; a work folder's note made in Gamma takes its file's name once
    written."""
    if not rnd.notes:
        if pdf:
            rnd._put(*pdf, seq=info.seq, title=info.title, folder=folder)
        return
    forced = rnd.force == "pull"
    if notes and ((notes[0] in rnd.kept_paths and not forced) or notes[1]["text"] is None
                  or not rnd._tracked(notes[0])):
        return
    found = (rnd.scan.files.get(notes[0]) if rnd.scan else rnd._stat(notes[0])) if notes else None
    if found and found.placeholder:
        return                   # its bytes are in the cloud: never read, never written over, in a page's round too
    page = rnd.remote.page(info.id)
    if page is None or page.trashed:
        return
    projected = md_lens.project(page.blocks, _kind_of(info.id))
    if notes:
        path, row = notes
        base = _base(row)
        if base is None:
            raise md_lens.Unwritable("the base of this note cannot be read")
    elif pdf and not projected:
        rnd._put(*pdf, seq=page.seq, title=info.title, folder=folder)
        return
    else:
        wanted, row, base = _notes_path(rnd, info, pdf, folder), None, None
        beside = "" if pdf else _beside_parent(rnd, info, ".md")
        if any(_older_note(rnd, at, info.id) for at in (wanted, beside) if at):
            return
        if not rnd._tracked(wanted):
            rnd._not_tracked(wanted, info.id)
            return
        path = rnd._free(wanted)
    current = _current(rnd, path) if row is not None else None
    made = row is None or current is None
    if row is not None and current is None and not forced:
        return                   # gone: the scan's to tell; a force pull writes it again
    if row is not None and row["hash"] == UNREAD:
        _first_read(rnd, path, row, current)
    if row is not None and current not in (None, row["hash"]) and not forced:
        _changed_under(rnd, path, info)
        return
    if row is not None and current is not None and _unknown(row):
        _older_anchors(rnd, path, page)
    ctx = rnd._ctx_at(path, info.id, pdf[0] if pdf else "")
    entries = {}
    tree = rnd._file_tree(page, projected, base, ctx, entries)
    try:
        shaped, text = md_lens.shape(base.doc if base else md_lens.parse(""), base.ids if base else [], tree)
    except md_lens.Unwritable as e:
        if row is not None or not pdf:
            raise
        rnd._keep(path, str(e))            # the paper's new note, by its own name; its PDF's row says why
        rnd._put(*pdf, problem=str(e))
        rnd.remote.touched.add(info.id)
        return
    text = rnd._front(text, info, new=row is None or _unknown(row))
    nodes, level, seq = _shaped(rnd, page, projected, tree, shaped, entries, ctx)
    _asked(rnd, ctx, info.id)
    _new_files(rnd, ctx)
    data = text.encode("utf-8", "surrogatepass")
    stamp = {"seq": seq if level else (row["seq"] if row else 0), "title": info.title, "folder": folder, "problem": ""}
    held = next((at for at in (wanted, beside) if at and at != path and rnd._tracked(at) and _holds(rnd, at, data)),
                "") if row is None else ""
    if held:
        path = held
        _take(rnd, path, kinds.NOTES.name, info.id, {"hash": disk.sha(data), "text": text, "nodes": nodes, **stamp})
        rnd._known_note(path, info.id, nodes)
        if path == wanted:           # beside its parent's: a sub-page's directory is no folder
            _dirs_made(rnd, _dir(path), folder)
    elif row is not None and disk.sha(data) == current:
        rnd._put(path, row, hash=current, text=text, nodes=nodes, **stamp)
        rnd.counts["unchanged"] += 1
    else:
        if forced and not made and current != row["hash"]:
            _record_force(rnd, info.id, path, row, base, ctx)
        _write(rnd, path, data, text, nodes, info.id, new=made, ours=row["ours"] if row else 1, **stamp)
        rnd._known_note(path, info.id, nodes)
        if made:
            _dirs_made(rnd, _dir(path), folder)
    if pdf and path in rnd.rows:        # a paper's two files are level with its page together
        rnd._put(*pdf, seq=rnd.rows[path]["seq"])
    elif row is None and info.work_folder and rnd.work and path in rnd.rows:
        _file_named(rnd, info.id, path)


def _first_read(rnd, path: str, row: dict, current: str) -> None:
    """An older client's note taken while a cloud placeholder held it
    (``UNREAD``), its bytes here now: its hash is the one they have while
    its stat is still the one the upgrade took, else none, a change on
    disk kept as one (gamma/vault/upgrade.py)."""
    st = disk.lstat(rnd.root, path)
    if st is not None and (st.st_size, st.st_mtime_ns) == (row["size"], row["mtime_ns"]):
        fields = {"hash": current, "file_id": str(st.st_ino), "read_at": time.time()}
    else:
        fields = {"hash": ""}
    rnd._put(path, row, **fields)


def _older_anchors(rnd, path: str, page: Page) -> None:
    """The anchors a note whose base names none of its blocks (an older
    client's) holds of its page's blocks, ``^`` and the block's id
    (``obsidian_export.anchor_marker``): written again when pull writes
    the note anew, so that a link to one of those blocks, from a note
    that pull leaves or from one of the person's, still finds it. An
    anchor is never removed because a link went."""
    try:
        doc = md_lens.parse(disk.read(rnd.root, path).decode("utf-8", "replace"))
    except OSError:
        return
    markers = {anchor_marker(block_id).lower(): block_id for block_id in page.blocks if block_id != page.id}
    for node in doc.flat:
        block_id = markers.get((node.anchor or "").lower())
        if block_id:
            rnd.anchors.setdefault(block_id, node.anchor)


def _asked(rnd, ctx, page_id: str) -> None:
    """Anchors a note's links asked of other notes: theirs to write, as a
    change Gamma made. The ask is kept on the other note's row (its node's
    ``a``, its seq none, so the next pull of it writes it) before this
    note is written, and that note is written this round when it can be.
    A note whose base names none of its blocks (``_unknown``) keeps that
    base, never one of no blocks, which the next push would read as every
    block new: it takes the ask only when this round writes it anew."""
    for block_id, marker in list(ctx.new_anchors.items()):
        owner = rnd.remote.page_of(block_id)
        notes = rnd.rows.of(owner).get(kinds.NOTES.name) if owner and owner != page_id else None
        if notes and (owner in rnd.layout.pages or rnd.only):     # a page's round reads no other page
            if not _unknown(notes[1]):
                nodes = [{**n, "a": n.get("a") or marker} if n["id"] == block_id else n
                         for n in notes[1]["nodes"] or ()]
                rnd._put(*notes, nodes=nodes, seq=-1)
            rnd.anchors[block_id] = marker
            rnd.again.add(owner)
    ctx.new_anchors.clear()


def _shaped(rnd, page: Page, projected: list, tree: list, shaped: list, entries: dict, ctx):
    """The base's nodes for the file as shaped, and whether pull is done
    with the page: what a file cannot hold as Gamma has it goes to Gamma as
    ops, where the vault pushes everything and no caret is on those
    blocks; else the page waits for the next pull. A split that cannot go
    now (a caret on the block, the page written meanwhile) is ``Waiting``,
    the file as it was: written, it would hold a block no block of Gamma
    is, which an edit on disk would bring in beside the one Gamma keeps.
    → (nodes, level, seq)."""
    flat = md_lens.flatten(shaped)
    if md_lens.tree_equal(shaped, tree) or not rnd.push_all:
        return _nodes(flat, entries, ctx, [n["id"] or new_block_id() for n in flat]), True, page.seq
    split = any(n["id"] is None for n in flat)
    texts = [_nodes([n], entries, ctx, [n["id"]])[0]["g"] for n in flat]
    at = {n["id"]: k for k, n in enumerate(md_lens.flatten(projected))}
    pairs = {k: at[n["id"]] for k, n in enumerate(flat) if n["id"] in at}
    file_tree = _retexted(shaped, [_text(g) for g in texts])
    kind_of, token = _kind_of(page.id), _token()

    def plan(now, _held):
        merged = merge.merge_page(now.blocks, page.id, projected, file_tree, pairs, kind_of=kind_of)
        merged = _named(merged, token, set(at))
        return Edit(merged.ops, ids=merged.ids, level=merged.level)
    expected = plan(page, None)
    busy = rnd.remote.carets(page.id)
    try:
        if any(op["id"] in busy for op in expected.ops):
            raise Changed(page.id)
        _before, seq, edit = rnd.remote.apply(page.id, plan, token=token, built=(page.seq, expected))
    except (Changed, Gone):
        if split:
            raise Waiting("a block of two paragraphs in Gamma is being edited or changed; it is written once "
                          "Gamma takes it as two") from None
        return _nodes(flat, entries, ctx, [n["id"] for n in flat]), False, page.seq
    return _nodes(flat, entries, ctx, edit.ids), edit.level, seq


def _nodes(flat: list, entries: dict, ctx, ids: list) -> list:
    """The base's ``{id, g, m?, a?}`` of a shaped file's blocks: a block's
    text as written kept with its Gamma text, one a split made read back."""
    out = []
    for n, block_id in zip(flat, ids):
        e = entries.get(n["id"])
        if e is not None and e["content"] == n["content"]:
            g, memo = e["g"], e["m"]
        else:
            g, memo = links.to_gamma(n["content"], (e or {}).get("m") or {}, ctx, base=(e or {}).get("g"))
        out.append({"id": block_id, "g": g, **({"m": memo} if memo else {}),
                    **({"a": n["anchor"]} if n.get("anchor") else {})})
    return out


def _new_files(rnd, ctx) -> None:
    """Pictures a note's links put in the vault: copied there and read in
    place from then on, where the vault's rules track them. In a work
    folder the picture is written and nothing else: no row (the next
    whole round reads it as a new file, a page), its bytes served from
    the store meanwhile, which Gamma's link names."""
    for rel, name in list(ctx.new_files.items()):
        if not rnd._there(rel) and not rnd._tracked(rel):
            rnd._not_tracked(rel)
            continue
        data = None if rnd._there(rel) else _fetched(rnd.remote, name)
        if data is None:
            continue
        if rnd.scan is None:
            rnd._steady(_dir(rel))
        if rnd.work:
            disk.write_new(rnd.root, rel, data)
            rnd.counts["added"] += 1
            continue
        token = _token()
        fields = {"hash": name, "ours": 1}
        written = disk.write_new(rnd.root, rel, data,
                                       ready=_journal(rnd, rel, kinds.PICTURE.name, token, data, "", fields))
        rnd.remote.link_file(rnd.root / rel, name)
        store.promote(rnd.id, rel, token, **fields, size=written.size, mtime_ns=written.mtime_ns,
                            file_id=written.file_id, read_at=time.time())
        rnd._promoted(rel)
        rnd.counts["added"] += 1
    ctx.new_files.clear()


def _current(rnd, path: str) -> str | None:
    """The hash of the note as it is now: a note is small, so pull always
    hashes (None: it is gone, the scan's to tell)."""
    try:
        return disk.sha(disk.read(rnd.root, path))
    except FileNotFoundError:
        return None


def _changed_under(rnd, path: str, info: Info) -> None:
    """The note changed since the base: it is pushed first and the page
    written again, once a round; with push off it is kept until a force
    pull."""
    if not rnd.pushing or not rnd.push_all or path in rnd.pushed_again:
        rnd._hold(path)
        rnd._keep(path, "changed on this computer since Gamma last read it", not rnd.push_all)
        return
    rnd.pushed_again.add(path)
    rnd.reads.pop(path, None)
    pages._push_file(rnd, path, rnd._stat(path))
    rnd.layout = rnd.remote.layout(rnd.only)
    if info.id in rnd.layout.pages:
        _pull_page(rnd, rnd.layout.pages[info.id], True)


def _record_force(rnd, page_id: str, path: str, row: dict, base: Base, ctx) -> None:
    """What a force pull replaced in a note changed on disk, recorded
    resolved: each block of the file as it is that the base did not hold,
    against the page's text of the block it matched."""
    doc = md_lens.parse(disk.read(rnd.root, path).decode("utf-8", "replace"))
    pairs = md_lens.match(md_lens.flatten(md_lens.tree_of(base.doc, base.ids)), md_lens.flatten(md_lens.tree_of(doc)))
    rows = []
    page = rnd.remote.page(page_id)
    for n, node in enumerate(doc.flat):
        i = pairs.get(n)
        if i is not None and i < len(base.ids) and base.doc.flat[i].content == node.content:
            continue
        block_id = base.ids[i] if i is not None and i < len(base.ids) else page_id
        mine = page.blocks[block_id]["content"] if page and block_id in page.blocks and block_id != page_id else ""
        theirs = _text(links.to_gamma(node.content, {}, ctx)[0])
        rows.append({"block_id": block_id, "mine": mine, "theirs": theirs, "result": mine,
                     "base": _text(base.nodes[i]["g"]) if i is not None and i < len(base.ids) else ""})
    if rows:
        rnd.remote.apply(page_id, lambda _page, _held: Edit([], rows, True))


def _write(rnd, path: str, data: bytes, text: str, nodes: list, page_id: str, *, new: bool, ours: int,
           **stamp) -> None:
    """The note written (``replace_note``; ``write_new`` for a new one, which
    never replaces a file that appeared meanwhile), in the journal
    (``_journal``); what it replaced kept in the history, always when that
    was not the base the round checked: a person's save in the moment
    between the check and the rename. The copy of those bytes beside the
    note goes only once the history has them."""
    token, base = _token(), (rnd.rows.get(path) or {}).get("hash")
    journal = _journal(rnd, path, kinds.NOTES.name, token, data, page_id, {**stamp, "ours": ours}, text, nodes)
    try:
        if new:
            written = disk.write_new(rnd.root, path, data, ready=journal)
        else:
            written = disk.replace_note(rnd.root, path, data, journal, hold=True)
    except PermissionError as e:
        store.drop(rnd.id, path, token)
        raise OSError(f"another program holds the file: {error_text(e)}") from e
    except FileNotFoundError:
        store.drop(rnd.id, path, token)
        return
    except BaseException:
        try:
            landed = disk.sha(disk.read(rnd.root, path)) == disk.sha(data)
        except OSError:
            landed = False
        if not landed:
            store.drop(rnd.id, path, token)
        raise
    if written.replaced is not None:
        saved = disk.sha(written.replaced) != base
        if saved:
            log.warning(f"[vault] {rnd.root / path}: saved while Gamma wrote it; that save is in the vault's history")
        store.keep(rnd.id, path, written.replaced, always=saved)
    store.promote(rnd.id, path, token, size=written.size, mtime_ns=written.mtime_ns, file_id=written.file_id,
                        read_at=time.time(), ours=ours, **stamp)
    disk.drop_temp(rnd.root, written.kept)
    rnd._promoted(path)
    rnd.counts["added" if new else "updated"] += 1


def _journal(rnd, path: str, kind: str, token: str, data: bytes, page_id: str, fields: dict, text=None,
             nodes=None):
    """The ``ready`` of a file pull writes: its pending, once the bytes are
    on the disk under a temporary name and before the rename, naming the
    stat the rename puts there and where what it replaces is kept, so that
    a round that died finds the write and those bytes
    (``journal._recover_write``)."""
    def ready(written):
        store.put_pending(rnd.id, path, kind, {
            "op": "pull", "token": token, "hash": disk.sha(data), "text": text, "nodes": nodes,
            "page_id": page_id, "fields": fields, "size": written.size, "mtime_ns": written.mtime_ns,
            "file_id": written.file_id, "kept": written.kept})
    return ready


def _fetched(remote, name: str) -> bytes | None:
    """The stored file ``name`` as pull writes it into the vault (None: the
    remote has none), once its bytes can be that file: a picture's hash its
    name, a PDF's its doc id, or else a whole PDF (a stored PDF may keep a
    name its bytes do not hash to: ``storage.matches_name``). Anything else
    (a sign-in portal's page answered for it, a body cut short) is never
    written nor taken for the file: ``Waiting``, and the next round fetches
    it again."""
    data = remote.read_upload(name)
    if data is None:
        return None
    stem = name.rpartition(".")[0]
    whole = storage.content_digest(data) == stem or (
        storage.matches_name(name, data) and (not name.endswith(".pdf") or b"%%EOF" in data[-gamma_sync.EOF_TAIL:]))
    if not whole:
        raise Waiting(f"{name}: what came back is not the stored file (a sign-in page, or an answer cut short)")
    return data
