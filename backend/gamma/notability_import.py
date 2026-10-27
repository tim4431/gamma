"""The Notability import: an upload of Notability's "Note" or "NTB" export
(one ``.note`` or ``.ntb``, or a ``.zip`` of them, a library's export, a
zipped note folder) → one Gamma page per note, with its PDF, handwriting,
shapes, typed text, pictures and recordings (docs/dev/import_export.md
"Notability notes").

It is a source of the reviewed import (routers/imports.py): a preview,
then a commit run as a background job. The run over the upload, the
guards on it, the commit's transaction and the storing of a note's files
are what every note app's import shares (gamma/note_app_import.py); here
is what a Notability note is: its document (Session.plist, or an .ntb's
noteBundle), read by gamma/notability.py or gamma/notability_ntb.py,
placed by gamma/notability_map.py, its key the digest of that document
(``notability_import``), and what Gamma's own export left in it
(``Note.private``), used where the note still holds what it describes.
"""

import contextlib
import posixpath
from collections import Counter

from fastapi import HTTPException

from . import note_app_import as app
from . import notability as nb
from . import notability_map as nmap
from . import notability_ntb as ntb
from .blocks_store import attachment_props
from .import_zip import READ_ERRORS
from .note_app_import import Skip, Stop, compose_pdf
from .storage import content_digest, display_filename, store_pdf


def import_notes(ws: str, data, conn, folder: str = "", *, name: str = "", actor: str = "",
                 preview: bool = False, selected: set[str] | None = None, progress=None) -> dict:
    """Import the Notability notes in the upload ``data`` into ``ws``, through
    the open pages.db connection ``conn``. ``data`` is a seekable binary
    file: one .note or .ntb, or a .zip of notes.

    - One page per note, filed below the folder ``folder`` (an id; "" is
      the top; 400 when it is no folder), made by ``actor``.
    - ``name`` is the upload's file name.
    - A ``preview`` writes nothing.
    - ``selected``: the bundle paths to import. None means all; unknown ids
      are a 400.
    - ``progress`` is a job's report, called per note and per stored file.
      A stopped job keeps the notes it finished.

    Returns the report. Raises HTTPException 400 (no notes, a Supernote
    file, a bad destination or selection), 413 (a central directory or an
    archive past the limits) or 507 (no disk to unpack)."""
    name = display_filename(name)  # a browser may send a relative path
    run = _Import(ws, conn, folder, actor, preview, progress)
    return run.run_upload(data, selected, refused=nb.NO_NOTES_REASON, refuse=_supernote,
                          find=lambda entries: nb.find_bundles(entries, name), nested=nb.nested_zip)


def _supernote(head: bytes) -> str | None:
    return nb.SUPERNOTE_REASON if head.startswith(nb.SUPERNOTE_MAGIC) else None


class _Import(app.AppImport):
    KEY = "notability_import"
    TAG = "notability"

    def __init__(self, *args):
        super().__init__(*args)
        self.opened = {}   # the note at hand's PDFs: member path → (sizes, reader, bytes), or None (_pdf_sources)

    def note(self, bundle: nb.Bundle, stack: contextlib.ExitStack) -> None:
        start = len(self.report["warnings"])
        self.opened = {}
        try:
            members = self._open(bundle, stack)
            name, data = self._document(members)
            digest = content_digest(data)
            if self.skip(bundle, digest, start):
                return
            if name == "Session.plist":
                note = nb.read_note(members, data, path=bundle.path, digest=digest, ink=not self.preview)
            else:
                note = ntb.read_ntb(members, data, path=bundle.path, digest=digest, ink=not self.preview,
                                    pdf_pages={} if self.preview else self._ntb_pages(members))
            del data
        except (Skip, nb.NoteError) as exc:
            self.warn(bundle.path, f"Not imported: {exc}")
            return
        except HTTPException as exc:
            if exc.status_code == 507:
                raise Stop(str(exc.detail)) from None
            self.warn(bundle.path, f"Not imported: {exc.detail}")
            return
        title = note.title
        for warning in note.warnings:
            self.warn(title, warning)
        for path in note.missing:
            if self.preview or not path.startswith("PDFs/"):  # a commit's frames name a missing PDF
                self.warn(title, _missing(path))
        dirs = self.dirs.get(bundle.path) or ([note.subject] if note.subject else [])
        entry = self.entry(bundle, title, dirs, "pdf" if any(e.pdf in note.pdfs for e in note.layout) else "page",
                           bool(note.missing))
        if self.preview:
            self._left_out(title, note.left_out, note.classes)
            for name in dict.fromkeys(e.pdf for e in note.layout if e.pdf in note.pdfs):
                if members.find(note.pdfs[name].path).size > app.MAX_HELD_BYTES:
                    self.warn(title, f"Not imported: its PDF {name} is over {app.MAX_HELD_BYTES >> 20} MB")
            self.done(entry, note.digest, start, False)
            return
        written = self.commit_safely(title, lambda: self._commit(members, note, dirs, entry))
        if written is not None:
            self.done(entry, note.digest, start, written)

    def _open(self, bundle: nb.Bundle, stack: contextlib.ExitStack) -> nb.Members:
        """A note's files (``open_packed``), a Supernote file refused."""
        if bundle.packed is not None:
            try:
                if bundle.packed.head(16).startswith(nb.SUPERNOTE_MAGIC):
                    raise Skip(nb.SUPERNOTE_REASON)
            except READ_ERRORS:
                raise Skip("not a readable zip") from None
        return self.open_packed(bundle, stack, nb.note_members, "not a Notability note")

    def _document(self, members: nb.Members) -> tuple[str, bytes]:
        """``(its name, its bytes)`` of the file the note is: a .note's
        Session.plist, else an .ntb's noteBundle, its size checked against
        MAX_SESSION_BYTES before it is read."""
        name = "Session.plist" if members.find("Session.plist") is not None else "noteBundle"
        entry = members.find(name)
        if entry.size > nb.MAX_SESSION_BYTES:
            raise Skip(f"its {name} is over {nb.MAX_SESSION_BYTES >> 20} MB")
        try:
            return name, entry.read()
        except READ_ERRORS:
            raise Skip(f"its {name} could not be read") from None

    def _ntb_pages(self, members: nb.Members) -> dict:
        """The display sizes of an .ntb's PDF's pages, which place the items
        on them: the PDF opened once for the note (``_pdf_sources`` takes it
        from ``self.opened``). Empty when the note has several, or its one is
        past MAX_HELD_BYTES (``_pdf_sources`` then skips the note)."""
        found = ntb.pdf_assets(members)
        if len(found) != 1 or members.find(found[0]).size > app.MAX_HELD_BYTES:
            return {}
        key = found[0]
        if key not in self.opened:
            self.opened[key] = self.open_pdf(members.find(key), posixpath.basename(key))
        return {posixpath.basename(key): self.opened[key][0]} if self.opened[key] else {}

    def _left_out(self, title: str, counts, classes) -> None:
        for reason in nb.left_out_reasons(counts, classes):
            self.warn(title, reason)

    # --- the commit ------------------------------------------------------------------------

    def _commit(self, members, note: nb.Note, dirs: list, entry: dict) -> bool:
        """Store the note's files, then write its page; False when another
        import wrote the same note meanwhile (the entry then names that
        page)."""
        title = note.title
        pdf_pages, readers, sources = self._pdf_sources(members, note)
        plan = nmap.plan_note(note, pdf_pages)
        for warning in plan.warnings:
            self.warn(title, warning)
        props = {self.KEY: note.digest}
        if plan.kind in ("original", "composed"):
            frames = [page.frame for page in plan.pages]
            data = sources[plan.pdf] if plan.kind == "original" else compose_pdf(
                frames, [plan.paper if f.pdf is None else None for f in frames], readers)
            readers.clear()
            sources.clear()
            self.room(len(data))
            doc_id, existed = store_pdf(self.ws, data)
            del data
            if not existed:
                self.report["pdfs_stored"] += 1
            props.update(attachment_props(doc_id, "", f"{title}.pdf")[0])
            self.progress(item=title)
        groups = (self.store_ink(plan, lambda page, left_out, left: nmap.page_strokes(note, page, left_out, left),
                                 title) if plan.kind != "note" else [])
        boxes, counts, uses = [], Counter(), Counter()
        for page in plan.pages:
            found = []
            for i in page.boxes:
                media = note.media[i]
                box = (self.store_picture(members, media, page.frame, title, uses) if media.kind == "picture"
                       else nmap.text_box(media, page.frame, plan.left_out))
                box = nmap.copied_box(note, media, box, page.frame)
                if box is not None:
                    found.append(box)
                    counts["pictures" if media.kind == "picture" else "boxes"] += 1
            boxes.append(found)
        recordings = self._recordings(members, note)
        self._left_out(title, note.left_out + plan.left_out, note.classes)
        tree = _ordered(note, plan, list(plan.flow), app.markup(plan, groups, boxes), recordings)
        written = self.write(title, note.digest, props, tree, dirs, entry)
        if written:
            self.report["strokes_imported"] += sum(g["ink_strokes"] for page in groups for g in page)
            self.report["text_boxes_imported"] += counts["boxes"]
            self.report["pictures_imported"] += counts["pictures"]
            self.report["recordings_imported"] += len(recordings)
            entry["created"] = True
            entry["kind"] = "pdf" if props.get("doc_id") else "page"
        return written

    def _pdf_sources(self, members, note: nb.Note) -> tuple[dict, dict, dict]:
        """The PDFs the layout names, each checked against MAX_HELD_BYTES
        before it is read (one past it skips the note), read whole and
        opened once however many names lead to it (an .ntb's before it was
        read, ``_ntb_pages``): ``(display sizes of their pages, readers,
        bytes)`` by file name, which the note's own ``opened`` lets go of.
        One that does not open is left out, and its pages become paper."""
        pages, readers, sources = {}, {}, {}
        opened, self.opened = self.opened, {}  # member path → (sizes, reader, bytes), or None when it does not open
        for name in dict.fromkeys(e.pdf for e in note.layout if e.pdf in note.pdfs):
            path = note.pdfs[name].path
            if path not in opened:
                opened[path] = self.open_pdf(members.find(path), name)
            if opened[path] is not None:
                pages[name], readers[name], sources[name] = opened[path]
        return pages, readers, sources

    def _recordings(self, members, note: nb.Note) -> list[dict]:
        """The note's recordings as recording blocks, each stored
        (``store_recording``). A note Gamma's export wrote names in its copy
        (``Note.private``) the recording each file is a segment of: those
        files become that recording again, its title, times and segments as
        they were, the files the note holds."""
        copies = (note.private or {}).get("recordings")
        copies = copies if isinstance(copies, dict) else {}
        out, grouped = [], {}
        for ref in note.recordings:
            if ref.path is not None and not ref.path.lower().endswith(".m4a"):
                self.warn(note.title, f"Recording {ref.name} is not an .m4a file: left out")
                continue
            made = self.store_recording(members, ref, note.title, note.created)
            if made is None:
                continue
            copy = copies.get(posixpath.basename(ref.path or ""))
            block = copy.get("block") if isinstance(copy, dict) else None
            joined = _copied_recording(copy, made) if isinstance(block, int) else None
            if joined is None:
                out.append(made)
            elif block in grouped:
                grouped[block]["properties"]["recording"]["segments"] += joined["properties"]["recording"]["segments"]
            else:
                grouped[block] = joined
                out.append(joined)
        return out


def _ms(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 1 << 53 else None


def _copied_recording(copy: dict, made: dict) -> dict | None:
    """The recording block ``made`` of one file, as Gamma's copy says the
    recording it is a segment of was: its title, its start, state and
    length, and this segment's start and length (the file, its size and
    type are ``made``'s). None when the copy does not read."""
    rec, segment, content = copy.get("recording"), copy.get("segment"), copy.get("content")
    if not isinstance(rec, dict) or not isinstance(segment, dict) or not isinstance(content, str):
        return None
    started, dur = _ms(rec.get("started")), _ms(rec.get("dur"))
    seg_started, seg_dur = _ms(segment.get("started")), _ms(segment.get("dur"))
    if None in (started, dur, seg_started, seg_dur) or len(content) > 500:
        return None
    ours = made["properties"]["recording"]["segments"][0]
    return {"content": content, "properties": {"recording": {
        "started": started, "state": "done", "dur": dur,
        "segments": [{**ours, "started": seg_started, "dur": seg_dur}]}}}


def _missing(path: str) -> str:
    if path.startswith("PDFs/"):
        return f"Missing PDF {path[len('PDFs/'):]}: its pages are blank paper"
    if path.lower().startswith("recordings/"):
        return f"Missing recording {path}: left out"
    return f"Missing picture {path}: left out"


def _ordered(note: nb.Note, plan: nmap.NotePlan, notes: list, markup: list, recordings: list) -> list[dict]:
    """The page's blocks: its notes, then its sheets or its PDF's
    handwriting and boxes, then its recordings; in the order of Gamma's
    copy when the note is one Gamma's export wrote and the copy names as
    many notes, sheets and recordings as the note brings (a PDF's
    handwriting and boxes go together, where the first of them was)."""
    canonical = notes + markup + recordings
    order = (note.private or {}).get("order")
    if not isinstance(order, list) or len(order) > len(canonical) + 1:
        return canonical
    parts = {"note": notes, "recording": recordings}
    if plan.kind == "notebook":
        parts["sheet"] = markup
    else:
        parts["markup"] = [markup] if markup else []
    counts = {kind: order.count(kind) for kind in parts}
    if sum(counts.values()) != len(order) or any(counts[kind] != len(items) for kind, items in parts.items()):
        return canonical
    taken = {kind: iter(items) for kind, items in parts.items()}
    out = []
    for kind in order:
        item = next(taken[kind])
        out += item if kind == "markup" else [item]
    return out
