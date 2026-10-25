"""The Notability import: an upload of Notability's "Note" or "NTB" export
(one ``.note`` or ``.ntb``, or a ``.zip`` of them, a library's export, a
zipped note folder) → one Gamma page per note, with its PDF, handwriting,
shapes, typed text, pictures and recordings (docs/dev/import_export.md
"Notability notes").

It is a source of the reviewed import (routers/imports.py): a preview,
then a commit run as a background job. The upload is untrusted:

- it is walked through the guarded zip walk (gamma/import_zip.py), with the
  central directory capped (MAX_DIRECTORY_BYTES) before zipfile reads it;
- a ``.note`` or ``.ntb`` inside it is opened only while it is read, one at
  a time, in a stack of its own, so the temporary disk a note takes is
  given back before the next and a library of thousands of notes imports
  whole;
- every member is read only after its size is checked (a PDF or picture,
  held whole in memory, up to MAX_HELD_BYTES), recordings are streamed,
  and the ink is made and stored one page at a time;
- every file goes through ``store_pdf``, ``store_file`` or
  ``store_file_stream``, so the quota and dedup apply, and the disk keeps
  ``import_staging.MIN_FREE_BYTES`` free.

Files are stored first, outside any transaction; each note's folder and
page are then written in one short transaction under the write lock, the
digest of the note's Session.plist or noteBundle checked again there
(``notability_import``, the re-import key), so an import that finds the
note there writes neither. A stopped job keeps the notes it finished. A
507 on one of a note's files (the quota, or the disk) stops the import,
since every later note would meet it too; any other refusal skips that
note.
"""

import contextlib
import io
import math
import posixpath
import shutil
import zipfile
from collections import Counter
from datetime import UTC, datetime

from fastapi import HTTPException
from PIL import Image, ImageOps

from . import config, jobs
from . import ink as inkmod
from . import notability as nb
from . import notability_map as nmap
from . import notability_ntb as ntb
from .ai_pictures import sniff
from .blocks_store import (FOLDERS, attachment_props, filing, folder_inserts, folder_paths, insert_page_tree,
                           load_props, new_block_id, write_lock)
from .import_review import destination, validate_selection
from .import_staging import MIN_FREE_BYTES
from .import_zip import (READ_ERRORS, DirectoryTooLarge, check_ratio, new_walk, open_nested, open_zip,
                         walk_zip)
from .logbuf import log
from .notebook import paper_ops
from .ops import after_commit, apply_batches
from .pdf_export import ExportPdfReader, display_size, page_frame
from .recording import mp4_duration_ms
from .storage import content_digest, display_filename, is_pdf, store_file, store_file_stream, store_pdf
from .text_box import escape_markdown

MAX_DIRECTORY_BYTES = 16 << 20   # a zip's central directory (the upload's, each .note's): about 100,000 files
MAX_HELD_BYTES = 256 << 20       # a note's PDF or picture: read whole into memory, parsed and composed
MAX_NOTE_SAMPLES = 20_000_000    # flattened samples in one note (a thousand pages of dense writing)
MAX_PICTURE_PIXELS = 40_000_000  # a picture decoded to be cropped or turned
MAX_PICTURE_USES = 4             # pictures of one note one image file is read for
MAX_TURNED_SIDE = 2048           # px: a turned picture's longer side
MAX_WARNINGS = 200
JPEG_QUALITY = 90
_PICTURE_EXTS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
_FOUND = ("SELECT id, properties, content FROM unified_blocks WHERE parent_id = 'root' "
          "AND json_extract(properties, '$.notability_import') IS NOT NULL")


class _Skip(Exception):
    """This note is left out, with the reason as a warning."""


class _Stop(Exception):
    """The import stops (a 507): this note and the rest are skipped."""

    def __init__(self, reason: str, title: str = ""):
        super().__init__(reason)
        self.title = title


def open_upload(data) -> zipfile.ZipFile:
    """The upload as a zip: a Supernote ``.note`` (``noteSN_FILE_VER_``)
    and anything that is no zip are refused (400), and so is a zip whose
    central directory is past MAX_DIRECTORY_BYTES (413) before zipfile
    reads it."""
    head = data.read(16)
    data.seek(0)
    if head.startswith(nb.SUPERNOTE_MAGIC):
        raise HTTPException(status_code=400, detail=nb.SUPERNOTE_REASON)
    if head[:2] != b"PK":
        raise HTTPException(status_code=400, detail=nb.NO_NOTES_REASON)
    try:
        return open_zip(data, max_directory=MAX_DIRECTORY_BYTES)
    except DirectoryTooLarge:
        raise HTTPException(status_code=413, detail="this zip lists more files than an import reads: refused")
    except READ_ERRORS:
        raise HTTPException(status_code=400, detail=nb.NO_NOTES_REASON)


def compose_pdf(frames: list, paper: dict, readers: dict) -> bytes:
    """The note's pages as one PDF, in the frames' order. A PDF page goes in
    by ``add_page`` from its reader (``pdf_export.ExportPdfReader``, whose
    dangling references do not break PyPDF2's clone), keeping its boxes,
    rotation, links and annotations; a page used twice is copied twice. A
    paper page is a blank page of the paper's size with the paper painted
    on it, as ``notebook_pdf`` paints a sheet."""
    from PyPDF2 import PdfWriter
    from PyPDF2.generic import DecodedStreamObject, NameObject

    from .pdf_typeset import num

    writer = PdfWriter()
    for frame in frames:
        if frame.pdf is not None:
            writer.add_page(readers[frame.pdf].pages[frame.pdf_page - 1])
            continue
        writer.add_blank_page(paper["width"], paper["height"])
        page = writer.pages[-1]  # add_blank_page's return value is not the page the writer keeps
        stream = DecodedStreamObject()
        stream.set_data(b"q 1 0 0 -1 0 %s cm\n%s\nQ" % (num(paper["height"]), paper_ops(paper)))
        page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def picture_bytes(data: bytes, media: nb.Media, animation: bytes | None = None) -> tuple[bytes, str]:
    """The picture as stored, ``(bytes, ext)``: the bytes as they came when
    nothing is to be done to them, else Pillow's. ``data`` is the picture's
    image, an animated GIF's still frame; its ``animation`` comes in its
    place when nothing is to be done and it is a GIF that opens. Something
    is to be done when the picture is cropped (FigureCropRectKey short of
    the whole image; an .ntb's crop is in ``media.crop_size`` units, scaled
    to the image's pixels), flipped, turned, or shown at an aspect over 1 %
    off the cropped image's: then the EXIF orientation is applied, the crop
    (clamped to the image), the flips, the stretch to the shown aspect and
    the turn (corners transparent), in that order. A turned picture is a PNG, the
    one format the server writes that keeps its transparent corners, its
    longer side at most MAX_TURNED_SIDE; any other keeps its pixels as PNG
    when it came as PNG, GIF or WebP or is translucent, else JPEG.
    ValueError for a file that is no PNG, JPEG, GIF or WebP, one past
    MAX_PICTURE_PIXELS that would need decoding, a stretch so long and thin
    that a side would round to no pixel, and any error Pillow raises
    decoding it."""
    ext = _PICTURE_EXTS.get(sniff(data))
    if not ext:
        raise ValueError("not a PNG, JPEG, GIF or WebP picture")
    angle = media.rotation if abs(media.rotation) > 1e-6 else 0.0
    try:
        with Image.open(io.BytesIO(data)) as opened:
            width, height = opened.size
            if (opened.getexif() or {}).get(0x0112, 1) in (5, 6, 7, 8):  # EXIF orientation: a quarter turn
                width, height = height, width
            crop = _crop_box(_in_pixels(media.crop, media.crop_size, width, height), width, height)
            cw, ch = crop[2] - crop[0], crop[3] - crop[1]
            stretch = abs((media.w / media.h) / (cw / ch) - 1) > 0.01
            if not (angle or media.flip_h or media.flip_v or stretch or crop != (0, 0, width, height)):
                if animation is not None and sniff(animation) == "image/gif" and _opens(animation):
                    return animation, ".gif"
                return data, ext
            if width * height > MAX_PICTURE_PIXELS:
                raise ValueError("too large to crop or turn")
            image = ImageOps.exif_transpose(opened) or opened
            image = image.crop(crop)
            if media.flip_h:
                image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if media.flip_v:
                image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            if stretch:
                aspect = media.w / media.h
                size = (max(1, round(ch * aspect)), ch) if ch * aspect >= cw else (cw, max(1, round(cw / aspect)))
                fit = min(1.0, (MAX_PICTURE_PIXELS / (size[0] * size[1])) ** 0.5)
                if min(size) * fit < 0.5:  # held at one pixel, the other side would be past the cap
                    raise ValueError("shown too long and thin to stretch")
                image = image.resize((max(1, round(size[0] * fit)), max(1, round(size[1] * fit))), Image.BICUBIC)
            out = io.BytesIO()
            if angle:
                c, s = abs(math.cos(angle)), abs(math.sin(angle))
                # Pillow's expanded size rounds the turned corners outward: up to 2 px past their extent
                fit = min(1.0, (MAX_TURNED_SIDE - 2) / max(image.width * c + image.height * s,
                                                           image.width * s + image.height * c))
                if fit < 1:  # the turned picture's longer side at most MAX_TURNED_SIDE
                    image = image.resize((max(1, math.floor(image.width * fit)), max(1, math.floor(image.height * fit))),
                                         Image.LANCZOS)
                image = image.convert("RGBA").rotate(-math.degrees(angle), expand=True, resample=Image.BICUBIC)
                image.save(out, "PNG", optimize=True)
                return out.getvalue(), ".png"
            translucent = "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)
            if translucent or ext != ".jpg":
                (image.convert("RGBA") if translucent else image.convert("RGB")).save(out, "PNG", optimize=True)
                return out.getvalue(), ".png"
            image.convert("RGB").save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
            return out.getvalue(), ".jpg"
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — a decompression bomb, or any file Pillow cannot decode
        raise ValueError(f"not a readable picture ({exc.__class__.__name__})") from None


def _opens(data: bytes) -> bool:
    """Whether Pillow reads the image's header."""
    try:
        with Image.open(io.BytesIO(data)):
            return True
    except Exception:  # noqa: BLE001 — any file Pillow cannot open
        return False


def _in_pixels(crop, size, width: int, height: int):
    """A crop given in ``size`` units (an .ntb's logical asset size, not
    always the image's pixels) as the image's pixels."""
    if crop is None or size is None or not (size[0] > 0 and size[1] > 0):
        return crop
    sx, sy = width / size[0], height / size[1]
    return crop[0] * sx, crop[1] * sy, crop[2] * sx, crop[3] * sy


def _crop_box(crop, width: int, height: int) -> tuple[int, int, int, int]:
    """FigureCropRectKey (x, y, w, h in source pixels) as a box clamped to
    the image; the whole image when there is none or it holds nothing."""
    if crop is None:
        return 0, 0, width, height
    x0, y0 = max(0, round(crop[0])), max(0, round(crop[1]))
    x1, y1 = min(width, round(crop[0] + crop[2])), min(height, round(crop[1] + crop[3]))
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else (0, 0, width, height)


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
    zf = open_upload(data)
    with zf, contextlib.ExitStack() as stack:
        walk = new_walk(stack, max_directory=MAX_DIRECTORY_BYTES)
        entries: list = []
        walk_zip(zf, "", entries, walk, nested=nb.nested_zip)
        check_ratio(walk)
        bundles = nb.find_bundles(entries, name)
        if not bundles:
            raise HTTPException(status_code=400, detail=nb.NO_NOTES_REASON)
        run = _Import(ws, conn, folder, actor, preview, progress or jobs.no_progress)
        return run.run(bundles, entries, walk["left_out"], selected)


class _Import:
    """One run of the import over one upload: the report, and what each
    note needs to know of the library and of the notes before it."""

    def __init__(self, ws, conn, folder, actor, preview, progress):
        self.ws, self.conn, self.actor, self.preview, self.progress = ws, conn, actor, preview, progress
        self.under, self.under_path = destination(conn, folder)
        self.report = {"pages_created": 0, "pages_skipped": 0, "pdfs_stored": 0, "strokes_imported": 0,
                       "text_boxes_imported": 0, "pictures_imported": 0, "recordings_imported": 0,
                       "pages": [], "skipped": [], "warnings": [], "entries": [], "folder": folder}
        self.selection = None  # the note at hand: its warnings name it
        self.library = {}  # digest → (page id, title, properties) of the notes imported before
        self.seen = {}     # digest → the page entry of a note earlier in this upload
        self.paths = {}    # folder id → its names from the top
        self.dirs = {}     # bundle path → its folder path below the destination
        self.opened = {}   # the note at hand's PDFs: member path → (sizes, reader, bytes), or None (_open_pdf)

    def warn(self, title: str, reason: str) -> None:
        if len(self.report["warnings"]) < MAX_WARNINGS:
            self.report["warnings"].append({"title": title, "reason": reason, "selection_id": self.selection})

    def run(self, bundles, entries, left_out, selected) -> dict:
        validate_selection(selected, (b.path for b in bundles))
        report = self.report
        report["entries"] = _source_entries(bundles, entries)
        for path, reason in left_out:
            self.warn(path, reason)
        for page_id, raw, title in self.conn.execute(_FOUND):
            props = load_props(raw)
            self.library.setdefault(props.get("notability_import"), (page_id, title, props))
        self.paths = folder_paths(self.conn)
        self.dirs = _note_dirs(bundles)
        chosen = [b for b in bundles if selected is None or b.path in selected]
        stopped = ""
        for n, bundle in enumerate(chosen):
            self.progress(done=n, total=len(chosen), unit="items", item=posixpath.basename(bundle.path))
            if stopped:
                report["skipped"].append({"title": bundle.path, "reason": stopped})
                continue
            self.selection = bundle.path
            try:
                with contextlib.ExitStack() as note_stack:  # a packed note's temporary file
                    self._note(bundle, note_stack)
            except _Stop as stop:
                stopped = str(stop)
                report["skipped"].append({"title": stop.title or bundle.path, "reason": stopped})
            finally:
                self.selection = None
        log.info(f"[notability] {report['pages_created']} pages, {report['pages_skipped']} skipped, "
                 f"{len(report['skipped'])} not imported, {report['strokes_imported']} strokes, "
                 f"{report['pdfs_stored']} PDFs{' (preview)' if self.preview else ''}")
        return report

    # --- one note ------------------------------------------------------------------------

    def _note(self, bundle: nb.Bundle, stack: contextlib.ExitStack) -> None:
        start = len(self.report["warnings"])
        self.opened = {}
        try:
            members = self._open(bundle, stack)
            name, data = self._document(members)
            digest = content_digest(data)
            if self._skip(bundle, digest, start):
                return
            if name == "Session.plist":
                note = nb.read_note(members, data, path=bundle.path, digest=digest, ink=not self.preview)
            else:
                note = ntb.read_ntb(members, data, path=bundle.path, digest=digest, ink=not self.preview,
                                    pdf_pages={} if self.preview else self._ntb_pages(members))
            del data
        except (_Skip, nb.NoteError) as exc:
            self.warn(bundle.path, f"Not imported: {exc}")
            return
        except HTTPException as exc:
            if exc.status_code == 507:
                raise _Stop(str(exc.detail)) from None
            self.warn(bundle.path, f"Not imported: {exc.detail}")
            return
        title = note.title
        for warning in note.warnings:
            self.warn(title, warning)
        for path in note.missing:
            if self.preview or not path.startswith("PDFs/"):  # a commit's frames name a missing PDF
                self.warn(title, _missing(path))
        dirs = self.dirs.get(bundle.path) or ([note.subject] if note.subject else [])
        shown = [self.under_path + dirs] if self.under_path or dirs else []
        entry = {"id": new_block_id(), "title": title, "folders": shown, "selection_ids": [bundle.path],
                 "source_path": bundle.path, "source_paths": [bundle.path],
                 "kind": "pdf" if any(e.pdf in note.pdfs for e in note.layout) else "page",
                 "created": False, "action": "create", "warnings": [], "missing": bool(note.missing)}
        if self.preview:
            self._left_out(title, note.left_out, note.classes)
            for name in dict.fromkeys(e.pdf for e in note.layout if e.pdf in note.pdfs):
                if members.find(note.pdfs[name].path).size > MAX_HELD_BYTES:
                    self.warn(title, f"Not imported: its PDF {name} is over {MAX_HELD_BYTES >> 20} MB")
            entry["warnings"] = self.report["warnings"][start:]
            self.report["pages"].append(entry)
            self.seen[note.digest] = entry
            return
        try:
            written = self._commit(bundle, members, note, dirs, entry)
        except HTTPException as exc:
            if exc.status_code == 507:
                raise _Stop(str(exc.detail), title) from None
            self._skipped(title, str(exc.detail))
            return
        except _Skip as exc:
            self._skipped(title, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — one note's failure is that note's
            log.warning(f"[notability] {bundle.path!r} failed: {exc!r}")
            self._skipped(title, str(exc) or exc.__class__.__name__)
            return
        entry["warnings"] = self.report["warnings"][start:]
        self.report["pages"].append(entry)
        self.seen[note.digest] = entry
        if written:
            self.report["pages_created"] += 1

    def _skipped(self, title: str, reason: str) -> None:
        self.report["skipped"].append({"title": title, "reason": reason})

    def _open(self, bundle: nb.Bundle, stack: contextlib.ExitStack) -> nb.Members:
        """A note's files: an unzipped note's from the upload's own walk; a
        packed one's from its zip, opened here (its temporary file closes
        with ``stack``) and walked with nothing opened inside it."""
        if bundle.members is not None:
            return bundle.members
        entry = bundle.packed
        try:
            if entry.head(16).startswith(nb.SUPERNOTE_MAGIC):
                raise _Skip(nb.SUPERNOTE_REASON)
            walk = new_walk(stack, max_directory=MAX_DIRECTORY_BYTES)
            inner = open_nested(entry, walk)
            if inner is None:
                raise _Skip(walk["left_out"][-1][1] if walk["left_out"] else "not a readable zip")
            entries: list = []
            walk_zip(inner, "", entries, walk, entry.depth + 1, nested=lambda _path: False)
            check_ratio(walk)
        except READ_ERRORS:
            raise _Skip("not a readable zip") from None
        for path, reason in walk["left_out"]:
            self.warn(bundle.path, f"{path}: {reason}")
        members = nb.note_members(entries)
        if members is None:
            raise _Skip("not a Notability note")
        return members

    def _document(self, members: nb.Members) -> tuple[str, bytes]:
        """``(its name, its bytes)`` of the file the note is: a .note's
        Session.plist, else an .ntb's noteBundle, its size checked against
        MAX_SESSION_BYTES before it is read."""
        name = "Session.plist" if members.find("Session.plist") is not None else "noteBundle"
        entry = members.find(name)
        if entry.size > nb.MAX_SESSION_BYTES:
            raise _Skip(f"its {name} is over {nb.MAX_SESSION_BYTES >> 20} MB")
        try:
            return name, entry.read()
        except READ_ERRORS:
            raise _Skip(f"its {name} could not be read") from None

    def _ntb_pages(self, members: nb.Members) -> dict:
        """The display sizes of an .ntb's PDF's pages, which place the items
        on them: the PDF opened once for the note (``_pdf_sources`` takes it
        from ``self.opened``). Empty when the note has several, or its one is
        past MAX_HELD_BYTES (``_pdf_sources`` then skips the note)."""
        found = ntb.pdf_assets(members)
        if len(found) != 1 or members.find(found[0]).size > MAX_HELD_BYTES:
            return {}
        key = found[0]
        if key not in self.opened:
            self.opened[key] = self._open_pdf(members.find(key), posixpath.basename(key))
        return {posixpath.basename(key): self.opened[key][0]} if self.opened[key] else {}

    def _skip(self, bundle: nb.Bundle, digest: str, start: int) -> bool:
        """A note already in the library, or earlier in this upload, is
        skipped: its entry names the page it is, filed where it is now."""
        earlier = self.seen.get(digest)
        found = self.library.get(digest)
        if earlier is None and found is None:
            return False
        if earlier is not None:
            self.warn(earlier["title"], f"The same note as {earlier['source_path']}")
            page_id, title, folders, kind = earlier["id"], earlier["title"], earlier["folders"], earlier["kind"]
        else:
            page_id, title, props = found
            folders = [self.paths[f] for f in filing(props, FOLDERS) if f in self.paths]
            kind = "pdf" if props.get("doc_id") else "page"
        self.report["pages_skipped"] += 1
        self.report["pages"].append({"id": page_id, "title": title, "folders": folders,
                                     "selection_ids": [bundle.path], "source_path": bundle.path,
                                     "source_paths": [bundle.path], "kind": kind, "created": False,
                                     "action": "skip", "warnings": self.report["warnings"][start:],
                                     "missing": False})
        return True

    def _left_out(self, title: str, counts: Counter, classes: Counter) -> None:
        for reason in nb.left_out_reasons(counts, classes):
            self.warn(title, reason)

    # --- the commit ------------------------------------------------------------------------

    def _commit(self, bundle, members, note: nb.Note, dirs: list, entry: dict) -> bool:
        """Store the note's files, then write its page; False when another
        import wrote the same note meanwhile (the entry then names that
        page)."""
        title = note.title
        pdf_pages, readers, sources = self._pdf_sources(members, note)
        plan = nmap.plan_note(note, pdf_pages)
        for warning in plan.warnings:
            self.warn(title, warning)
        props = {"notability_import": note.digest}
        if plan.kind in ("original", "composed"):
            data = sources[plan.pdf] if plan.kind == "original" else compose_pdf(
                [page.frame for page in plan.pages], plan.paper, readers)
            readers.clear()
            sources.clear()
            self._room(len(data))
            doc_id, existed = store_pdf(self.ws, data)
            del data
            if not existed:
                self.report["pdfs_stored"] += 1
            props.update(attachment_props(doc_id, "", f"{title}.pdf")[0])
            self.progress(item=title)
        groups = self._store_ink(note, plan) if plan.kind != "note" else []
        boxes, counts, uses = [], Counter(), Counter()
        for page in plan.pages:
            found = []
            for i in page.boxes:
                media = note.media[i]
                box = (self._store_picture(members, media, page.frame, title, uses) if media.kind == "picture"
                       else nmap.text_box(media, page.frame, plan.left_out))
                box = nmap.copied_box(note, media, box, page.frame)
                if box is not None:
                    found.append(box)
                    counts["pictures" if media.kind == "picture" else "boxes"] += 1
            boxes.append(found)
        recordings = self._recordings(members, note)
        self._left_out(title, note.left_out + plan.left_out, note.classes)
        tree = _ordered(note, plan, list(plan.flow), _markup(plan, groups, boxes), recordings)
        written = self._write(note, props, tree, dirs, entry)
        if written:
            self.report["strokes_imported"] += sum(g["ink_strokes"] for page in groups for g in page)
            self.report["text_boxes_imported"] += counts["boxes"]
            self.report["pictures_imported"] += counts["pictures"]
            self.report["recordings_imported"] += len(recordings)
            entry["created"] = True
            entry["kind"] = "pdf" if props.get("doc_id") else "page"
        return written

    def _write(self, note: nb.Note, props: dict, tree: list, dirs: list, entry: dict) -> bool:
        """The note's folder and its page in one short transaction under the
        write lock, the digest looked for again there: two imports of one
        note make one page, and the one that finds it there writes nothing,
        no folder either. The folder at ``dirs`` below the destination is
        made where missing (``folder_inserts``, read under the lock too, so
        two imports filing into one new folder make it once; its batch goes
        through ``ops.apply_batches``)."""
        conn = self.conn
        write_lock(conn)
        try:
            row = conn.execute("SELECT id, content, properties FROM unified_blocks WHERE parent_id = 'root' "
                               "AND json_extract(properties, '$.notability_import') = ?", (note.digest,)).fetchone()
            if row:
                paths = folder_paths(conn)  # with the folder the other import made
                conn.rollback()
                props = load_props(row[2])
                entry.update(id=row[0], title=row[1], action="skip", kind="pdf" if props.get("doc_id") else "page",
                             folders=[paths[f] for f in filing(props, FOLDERS) if f in paths])
                self.report["pages_skipped"] += 1
                return False
            path = [d.strip() for d in dirs if d.strip()]
            folder_ops, folder_ids = folder_inserts(conn, [path], self.under)
            filed = folder_ids.get(tuple(path)) or self.under
            if filed:
                props[FOLDERS] = [filed]
            results = apply_batches(conn, [(FOLDERS, folder_ops)], actor=self.actor) if folder_ops else []
            insert_page_tree(conn, entry["id"], note.title, props, tree, self.actor)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        for result in results:
            after_commit(self.ws, conn, result)
        return True

    def _room(self, nbytes: int) -> None:
        """507 when storing ``nbytes`` would leave the disk with less than
        MIN_FREE_BYTES free."""
        if shutil.disk_usage(config.DATA_DIR).free - nbytes < MIN_FREE_BYTES:
            raise HTTPException(status_code=507, detail="the server's disk has no room for this note")

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
                opened[path] = self._open_pdf(members.find(path), name)
            if opened[path] is not None:
                pages[name], readers[name], sources[name] = opened[path]
        return pages, readers, sources

    def _open_pdf(self, entry, name: str) -> tuple | None:
        """``(display sizes of its pages, reader, bytes)`` of a bundled PDF;
        None when it does not open or has no page. _Skip past
        MAX_HELD_BYTES, checked before a byte is read."""
        if entry.size > MAX_HELD_BYTES:
            raise _Skip(f"its PDF {name} is over {MAX_HELD_BYTES >> 20} MB")
        try:
            data = entry.read()
            if not is_pdf(data):
                return None
            reader = ExportPdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted:
                reader.decrypt("")
            sizes = [display_size(*page_frame(page)) for page in reader.pages]
        except Exception as exc:  # noqa: BLE001 — any PDF PyPDF2 cannot read becomes paper
            log.info(f"[notability] {name}: unreadable PDF ({exc!r})")
            return None
        return (sizes, reader, data) if sizes else None

    def _store_ink(self, note: nb.Note, plan: nmap.NotePlan) -> list[list[dict]]:
        """The ink groups of each page: its strokes made in drawing order
        (``page_strokes``), packed into files (``ink.group_files``), each
        checked as an upload is and stored. Only the page at hand is ever
        held as samples; past MAX_NOTE_SAMPLES in the note the rest of its
        strokes are left out, and never made."""
        left = [MAX_NOTE_SAMPLES]
        out = []
        for k, page in enumerate(plan.pages, 1):
            frame = page.frame
            space = ({"kind": "canvas", "width": frame.width, "height": frame.height} if plan.kind == "notebook"
                     else {"kind": "pdf-page", "page": k, "width": frame.width, "height": frame.height})
            groups = []
            for file in inkmod.group_files(nmap.page_strokes(note, page, plan.left_out, left), space):
                parsed = inkmod.parse_ink(inkmod.dumps(file))  # the bytes form: MAX_BYTES is checked too
                data = inkmod.dumps(parsed)
                self._room(len(data))
                stored, _ = store_file(self.ws, data, ".ink")
                group = {"ink_url": f"/api/uploads/{stored}", "ink_strokes": len(parsed.strokes)}
                if space["kind"] == "pdf-page":
                    group["pdf_position"] = inkmod.pdf_position(parsed)
                groups.append(group)
                self.progress(item=note.title)
            out.append(groups)
        return out

    def _store_picture(self, members, media: nb.Media, frame, title: str, uses: Counter) -> dict | None:
        """A picture as a fixed-width text box holding its image; None, with
        a warning, when it cannot be stored. An animated GIF's animation is
        read beside its still frame when the note has it within
        MAX_HELD_BYTES (``picture_bytes`` chooses). ``uses`` counts the note's
        pictures by image file: one file is read for MAX_PICTURE_USES of
        them at most, so a note that names one file over and over is not
        read and decoded each time."""
        entry = members.find(media.picture)
        name = posixpath.basename(media.picture)
        if entry is None:
            self.warn(title, _missing(media.picture))
            return None
        if entry.size > MAX_HELD_BYTES:
            self.warn(title, f"Picture {name} is over {MAX_HELD_BYTES >> 20} MB: left out")
            return None
        uses[media.picture] += 1
        if uses[media.picture] > MAX_PICTURE_USES:
            if uses[media.picture] == MAX_PICTURE_USES + 1:
                self.warn(title, f"Picture {name} is shown more than {MAX_PICTURE_USES} times: the rest are left out")
            return None
        moving, animation = members.find(media.animation) if media.animation else None, None
        if moving is not None and moving.size <= MAX_HELD_BYTES:
            with contextlib.suppress(*READ_ERRORS):  # one that does not read leaves the still frame
                animation = moving.read()
        try:
            data, ext = picture_bytes(entry.read(), media, animation)
        except (ValueError, *READ_ERRORS) as exc:
            self.warn(title, f"Picture {name} is left out: {exc}")
            return None
        self._room(len(data))
        try:
            stored, _ = store_file(self.ws, data, ext)
        except HTTPException as exc:
            if exc.status_code == 507:
                raise
            self.warn(title, f"Picture {name} is left out: {exc.detail}")
            return None
        self.progress(item=name)
        caption = " ".join(media.caption.translate({ord(c): None for c in "[]|"}).split())
        return {"content": f"![{escape_markdown(caption)}](/api/uploads/{stored})",
                "text_box": nmap.picture_box(media, frame)}

    def _recordings(self, members, note: nb.Note) -> list[dict]:
        """The note's recordings as recording blocks, each stored
        (``_store_recording``). A note Gamma's export wrote names in its copy
        (``Note.private``) the recording each file is a segment of: those
        files become that recording again, its title, times and segments as
        they were, the files the note holds."""
        copies = (note.private or {}).get("recordings")
        copies = copies if isinstance(copies, dict) else {}
        out, grouped = [], {}
        for ref in note.recordings:
            made = self._store_recording(members, ref, note)
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

    def _store_recording(self, members, ref: nb.RecordingRef, note: nb.Note) -> dict | None:
        """A recording as a recording block with one segment; None, with a
        warning, when it cannot be stored. No stroke is placed on it: the
        note stores no time for a stroke's samples."""
        if ref.path is None:
            return None
        title = note.title
        if not ref.path.lower().endswith(".m4a"):
            self.warn(title, f"Recording {ref.name} is not an .m4a file: left out")
            return None
        entry = members.find(ref.path)
        try:
            with entry.open() as f:
                duration = mp4_duration_ms(f)
            if duration is None:
                self.warn(title, f"Recording {ref.name} has no readable length: left out")
                return None
            self._room(entry.size)
            with entry.open() as f:
                stored, _ = store_file_stream(self.ws, f, ".m4a")
        except ValueError:
            self.warn(title, f"Recording {ref.name} is not an .m4a file: left out")
            return None
        except READ_ERRORS:
            self.warn(title, f"Recording {ref.name} could not be read: left out")
            return None
        except HTTPException as exc:
            if exc.status_code == 507:
                raise
            self.warn(title, f"Recording {ref.name} is left out: {exc.detail}")
            return None
        self.progress(item=ref.name)
        started = round((ref.created or note.created or datetime.now(UTC)).timestamp() * 1000)
        return {"content": ref.name or "Recording",
                "properties": {"recording": {"started": started, "state": "done", "dur": duration,
                                             "segments": [{"url": f"/api/uploads/{stored}", "started": started,
                                                           "dur": duration, "bytes": entry.size,
                                                           "mime": "audio/mp4"}]}}}


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


def _markup(plan: nmap.NotePlan, groups: list, boxes: list) -> list[dict]:
    """The pages' ink groups and boxes as blocks: on a PDF page at the
    page's top level (``pdf_page`` on a box), page by page; in a notebook
    under each page's sheet, which starts folded."""
    tree = []
    for k, page in enumerate(plan.pages, 1):
        inks = [{"content": "", "properties": group} for group in (groups[k - 1] if groups else [])]
        if plan.kind == "notebook":
            placed = [{"content": box["content"], "properties": {"text_box": box["text_box"]}} for box in boxes[k - 1]]
            sheet = plan.papers[k - 1] if plan.papers else plan.paper
            tree.append({"id": new_block_id(), "content": "", "properties": {"sheet": sheet, "collapsed": True},
                         "children": inks + placed})
        elif plan.kind in ("original", "composed"):
            tree += inks + [{"content": box["content"], "properties": {"text_box": box["text_box"], "pdf_page": k}}
                            for box in boxes[k - 1]]
    return tree


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


def _note_dirs(bundles: list[nb.Bundle]) -> dict[str, list[str]]:
    """Each note's folder path below the destination: the directories
    above its bundle path, less one top directory every note shares (a
    zipped folder, or the folder picked files are packed into)."""
    dirs = {b.path: [d.strip() for d in b.path.split("/")[:-1] if d.strip()] for b in bundles}
    tops = {tuple(d[:1]) for d in dirs.values()}
    if len(tops) == 1 and all(dirs.values()):
        dirs = {path: d[1:] for path, d in dirs.items()}
    return dirs


def _source_entries(bundles: list[nb.Bundle], entries: list) -> list[dict]:
    """The review's source column: each note, and each other file of the
    upload, marked not imported (a note's own files are not listed)."""
    out = [{"path": b.path, "size": b.size, "directory": False, "status": "source"} for b in bundles]
    inside = {id(e) for b in bundles if b.members is not None for e in b.members.entries.values()}
    inside.update(id(b.packed) for b in bundles if b.packed is not None)
    out += [{"path": e.path, "size": e.size, "directory": False, "status": "not_imported"}
            for e in entries if id(e) not in inside]
    return out
