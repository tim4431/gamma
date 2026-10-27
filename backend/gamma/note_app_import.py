"""What the imports of other note apps' files share: Notability's
(gamma/notability_import.py) and GoodNotes' (gamma/goodnotes_import.py).
Each turns an upload of the app's exports into one Gamma page per note or
notebook, with its PDF or its sheets of paper, its handwriting, typed text,
pictures and recordings (docs/dev/import_export.md "Notability notes",
"GoodNotes notebooks").

Here: the upload opened as a zip (``open_upload``); one run over the notes
it holds as a reviewed import's preview or commit, and its report
(``AppImport``); the re-import key, looked for in the library, among the
notes before in the upload, and again under the write lock; a note's PDF
(the one it carries, or one composed of its pages, ``compose_pdf``), its
ink page by page, its pictures (``picture_bytes``) and its recordings
stored, then its folder and page written in one short transaction. What a
note holds and where it lands on a page is each app's own module.

The upload is untrusted:

- it is walked through the guarded zip walk (gamma/import_zip.py), with the
  central directory capped (MAX_DIRECTORY_BYTES) before zipfile reads it;
- a note packed in it (a ``.note``, ``.ntb`` or ``.goodnotes``) is opened
  only while it is read, one at a time, in a stack of its own, so the
  temporary disk a note takes is given back before the next and a library
  of thousands of notes imports whole;
- every member is read only after its size is checked (a PDF or picture,
  held whole in memory, up to MAX_HELD_BYTES), recordings are streamed,
  and the ink is made and stored one page at a time;
- every file goes through ``store_pdf``, ``store_file`` or
  ``store_file_stream``, so the quota and dedup apply, and the disk keeps
  ``import_staging.MIN_FREE_BYTES`` free.

Files are stored first, outside any transaction; each note's folder and
page are then written in one short transaction under the write lock, the
note's key (``AppImport.KEY``) checked again there, so an import that finds
the note there writes neither. A stopped job keeps the notes it finished.
A 507 on one of a note's files (the quota, or the disk) stops the import,
since every later note would meet it too; any other refusal skips that
note.
"""

import contextlib
import io
import math
import posixpath
import shutil
from collections import Counter
from datetime import UTC, datetime

from fastapi import HTTPException
from PIL import Image, ImageOps

from . import config, jobs
from . import ink as inkmod
from . import notability as nb
from . import notability_map as nmap
from .ai_pictures import sniff
from .blocks_store import (FOLDERS, filing, folder_inserts, folder_paths, insert_page_tree, load_props,
                           new_block_id, write_lock)
from .import_review import destination, validate_selection
from .import_staging import MIN_FREE_BYTES
from .import_zip import READ_ERRORS, DirectoryTooLarge, check_ratio, new_walk, open_nested, open_zip, walk_zip
from .logbuf import log
from .notebook import paper_ops
from .ops import after_commit, apply_batches
from .pdf_export import ExportPdfReader, display_size, page_frame
from .recording import mp4_duration_ms
from .storage import is_pdf, store_file, store_file_stream
from .text_box import escape_markdown

MAX_DIRECTORY_BYTES = 16 << 20   # a zip's central directory (the upload's, each note's): about 100,000 files
MAX_HELD_BYTES = 256 << 20       # a note's PDF or picture: read whole into memory, parsed and composed
MAX_NOTE_SAMPLES = 20_000_000    # flattened samples in one note (a thousand pages of dense writing)
MAX_PICTURE_PIXELS = 40_000_000  # a picture decoded to be cropped or turned
MAX_PICTURE_USES = 4             # pictures of one note one image file is read for
MAX_TURNED_SIDE = 2048           # px: a turned picture's longer side
MAX_WARNINGS = 200
JPEG_QUALITY = 90
_PICTURE_EXTS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


class Skip(Exception):
    """This note is left out, with the reason as a warning."""


class Stop(Exception):
    """The import stops (a 507): this note and the rest are skipped."""

    def __init__(self, reason: str, title: str = ""):
        super().__init__(reason)
        self.title = title


def open_upload(data, refused: str, refuse=None):
    """The upload as a zip. Anything that is no zip is refused (400, with
    ``refused`` as the reason), and so is one ``refuse(first bytes)`` names
    a reason for, and a zip whose central directory is past
    MAX_DIRECTORY_BYTES (413) before zipfile reads it."""
    head = data.read(16)
    data.seek(0)
    reason = refuse(head) if refuse is not None else None
    if reason:
        raise HTTPException(status_code=400, detail=reason)
    if head[:2] != b"PK":
        raise HTTPException(status_code=400, detail=refused)
    try:
        return open_zip(data, max_directory=MAX_DIRECTORY_BYTES)
    except DirectoryTooLarge:
        raise HTTPException(status_code=413, detail="this zip lists more files than an import reads: refused")
    except READ_ERRORS:
        raise HTTPException(status_code=400, detail=refused)


def compose_pdf(frames: list, papers: list, readers: dict) -> bytes:
    """The note's pages as one PDF, in the frames' order. A PDF page goes in
    by ``add_page`` from its reader (``pdf_export.ExportPdfReader``, whose
    dangling references do not break PyPDF2's clone), keeping its boxes,
    rotation, links and annotations; a page used twice is copied twice. A
    paper page (a frame without a PDF) is a blank page of its paper's size
    (``papers``, one per frame) with the paper painted on it, as
    ``notebook_pdf`` paints a sheet."""
    from PyPDF2 import PdfWriter
    from PyPDF2.generic import DecodedStreamObject, NameObject

    from .pdf_typeset import num

    writer = PdfWriter()
    for frame, paper in zip(frames, papers):
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
    is to be done when the picture is cropped (``media.crop`` short of the
    whole image; a crop given in ``media.crop_size`` units scaled to the
    image's pixels), flipped, turned, or shown at an aspect over 1 % off
    the cropped image's: then the EXIF orientation is applied, the crop
    (clamped to the image), the flips, the stretch to the shown aspect and
    the turn (corners transparent), in that order. A turned picture is a
    PNG, the one format the server writes that keeps its transparent
    corners, its longer side at most MAX_TURNED_SIDE; any other keeps its
    pixels as PNG when it came as PNG, GIF or WebP or is translucent, else
    JPEG. ValueError for a file that is no PNG, JPEG, GIF or WebP, one past
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
    """A crop (x, y, w, h in source pixels) as a box clamped to the image;
    the whole image when there is none or it holds nothing."""
    if crop is None:
        return 0, 0, width, height
    x0, y0 = max(0, round(crop[0])), max(0, round(crop[1]))
    x1, y1 = min(width, round(crop[0] + crop[2])), min(height, round(crop[1] + crop[3]))
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else (0, 0, width, height)


def markup(plan, groups: list, boxes: list) -> list[dict]:
    """The pages' ink groups and boxes as blocks: on a PDF page at the
    page's top level (``pdf_page`` on a box), page by page; in a notebook
    under each page's sheet, which starts folded. ``plan`` is a planner's:
    its ``kind``, ``pages``, and ``papers`` (a sheet's paper each, or None)
    or else ``paper``."""
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


def note_dirs(bundles: list[nb.Bundle]) -> dict[str, list[str]]:
    """Each note's folder path below the destination: the directories
    above its bundle path, less one top directory every note shares (a
    zipped folder, or the folder picked files are packed into)."""
    dirs = {b.path: [d.strip() for d in b.path.split("/")[:-1] if d.strip()] for b in bundles}
    tops = {tuple(d[:1]) for d in dirs.values()}
    if len(tops) == 1 and all(dirs.values()):
        dirs = {path: d[1:] for path, d in dirs.items()}
    return dirs


def source_entries(bundles: list[nb.Bundle], entries: list) -> list[dict]:
    """The review's source column: each note, and each other file of the
    upload, marked not imported (a note's own files are not listed)."""
    out = [{"path": b.path, "size": b.size, "directory": False, "status": "source"} for b in bundles]
    inside = {id(e) for b in bundles if b.members is not None for e in b.members.entries.values()}
    inside.update(id(b.packed) for b in bundles if b.packed is not None)
    out += [{"path": e.path, "size": e.size, "directory": False, "status": "not_imported"}
            for e in entries if id(e) not in inside]
    return out


def open_pdf(entry, name: str, tag: str = "import") -> tuple | None:
    """``(display sizes of its pages, reader, bytes)`` of a note's PDF; None
    when it does not open or has no page. Skip past MAX_HELD_BYTES, checked
    before a byte is read."""
    if entry.size > MAX_HELD_BYTES:
        raise Skip(f"its PDF {name} is over {MAX_HELD_BYTES >> 20} MB")
    try:
        data = entry.read()
        if not is_pdf(data):
            return None
        reader = ExportPdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            reader.decrypt("")
        sizes = [display_size(*page_frame(page)) for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 — any PDF PyPDF2 cannot read becomes paper
        log.info(f"[{tag}] {name}: unreadable PDF ({exc!r})")
        return None
    return (sizes, reader, data) if sizes else None


class AppImport:
    """One run of an app's import over one upload: the report, and what
    each note needs to know of the library and of the notes before it. A
    subclass names its key and reads a note (``note``)."""

    KEY = ""   # the page property holding a note's re-import key
    TAG = ""   # the log's name for the import

    def __init__(self, ws, conn, folder, actor, preview, progress):
        self.ws, self.conn, self.actor, self.preview = ws, conn, actor, preview
        self.progress = progress or jobs.no_progress
        self.folder = folder
        self.under, self.under_path = "", []  # the destination folder and its names (``run``)
        self.report = {"pages_created": 0, "pages_skipped": 0, "pdfs_stored": 0, "strokes_imported": 0,
                       "text_boxes_imported": 0, "pictures_imported": 0, "recordings_imported": 0,
                       "pages": [], "skipped": [], "warnings": [], "entries": [], "folder": folder}
        self.selection = None  # the note at hand: its warnings name it
        self.library = {}  # key → (page id, title, properties) of the notes imported before
        self.seen = {}     # key → the page entry of a note earlier in this upload
        self.paths = {}    # folder id → its names from the top
        self.dirs = {}     # bundle path → its folder path below the destination

    def warn(self, title: str, reason: str) -> None:
        if len(self.report["warnings"]) < MAX_WARNINGS:
            self.report["warnings"].append({"title": title, "reason": reason, "selection_id": self.selection})

    def run_upload(self, data, selected, *, refused: str, find, nested, refuse=None) -> dict:
        """Run over the upload ``data``: opened (``open_upload``: ``refused``
        and ``refuse`` its reasons), walked with ``nested`` saying which zips
        open in place, its notes the ones ``find(entries)`` names (none: a
        400 with ``refused``), ``selected`` the ones imported."""
        zf = open_upload(data, refused, refuse)
        with zf, contextlib.ExitStack() as stack:
            walk = new_walk(stack, max_directory=MAX_DIRECTORY_BYTES)
            entries: list = []
            walk_zip(zf, "", entries, walk, nested=nested)
            check_ratio(walk)
            bundles = find(entries)
            if not bundles:
                raise HTTPException(status_code=400, detail=refused)
            return self.run(bundles, entries, walk["left_out"], selected)

    def run(self, bundles, entries, left_out, selected) -> dict:
        self.under, self.under_path = destination(self.conn, self.folder)
        validate_selection(selected, (b.path for b in bundles))
        report = self.report
        report["entries"] = source_entries(bundles, entries)
        for path, reason in left_out:
            self.warn(path, reason)
        found = (f"SELECT id, properties, content FROM unified_blocks WHERE parent_id = 'root' "
                 f"AND json_extract(properties, '$.{self.KEY}') IS NOT NULL")
        for page_id, raw, title in self.conn.execute(found):
            props = load_props(raw)
            self.library.setdefault(props.get(self.KEY), (page_id, title, props))
        self.paths = folder_paths(self.conn)
        self.dirs = note_dirs(bundles)
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
                    self.note(bundle, note_stack)
            except Stop as stop:
                stopped = str(stop)
                report["skipped"].append({"title": stop.title or bundle.path, "reason": stopped})
            finally:
                self.selection = None
        log.info(f"[{self.TAG}] {report['pages_created']} pages, {report['pages_skipped']} skipped, "
                 f"{len(report['skipped'])} not imported, {report['strokes_imported']} strokes, "
                 f"{report['pdfs_stored']} PDFs{' (preview)' if self.preview else ''}")
        return report

    def note(self, bundle: nb.Bundle, stack: contextlib.ExitStack) -> None:
        raise NotImplementedError

    # --- one note ------------------------------------------------------------------------

    def open_packed(self, bundle: nb.Bundle, stack: contextlib.ExitStack, members_of, unknown: str) -> nb.Members:
        """A note's files: an unzipped note's from the upload's own walk; a
        packed one's from its zip, opened here (its temporary file closes
        with ``stack``) and walked with nothing opened inside it, its files
        those ``members_of(entries)`` gives (Skip with ``unknown`` when
        None)."""
        if bundle.members is not None:
            return bundle.members
        entry = bundle.packed
        try:
            walk = new_walk(stack, max_directory=MAX_DIRECTORY_BYTES)
            inner = open_nested(entry, walk)
            if inner is None:
                raise Skip(walk["left_out"][-1][1] if walk["left_out"] else "not a readable zip")
            entries: list = []
            walk_zip(inner, "", entries, walk, entry.depth + 1, nested=lambda _path: False)
            check_ratio(walk)
        except READ_ERRORS:
            raise Skip("not a readable zip") from None
        for path, reason in walk["left_out"]:
            self.warn(bundle.path, f"{path}: {reason}")
        members = members_of(entries)
        if members is None:
            raise Skip(unknown)
        return members

    def skip(self, bundle: nb.Bundle, key: str, start: int) -> bool:
        """A note already in the library, or earlier in this upload, is
        skipped: its entry names the page it is, filed where it is now."""
        earlier = self.seen.get(key)
        found = self.library.get(key)
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

    def skipped(self, title: str, reason: str) -> None:
        self.report["skipped"].append({"title": title, "reason": reason})

    def entry(self, bundle: nb.Bundle, title: str, dirs: list, kind: str, missing: bool) -> dict:
        """The report's entry of a note about to be previewed or written."""
        shown = [self.under_path + dirs] if self.under_path or dirs else []
        return {"id": new_block_id(), "title": title, "folders": shown, "selection_ids": [bundle.path],
                "source_path": bundle.path, "source_paths": [bundle.path], "kind": kind, "created": False,
                "action": "create", "warnings": [], "missing": missing}

    def done(self, entry: dict, key: str, start: int, written: bool) -> None:
        """The note previewed or written: its entry, with the warnings
        since ``start``, goes in the report and stands for its key."""
        entry["warnings"] = self.report["warnings"][start:]
        self.report["pages"].append(entry)
        self.seen[key] = entry
        if written:
            self.report["pages_created"] += 1

    def commit_safely(self, title: str, commit) -> bool | None:
        """``commit()``, a note's files and page written: a 507 stops the
        import; any other refusal or failure skips the note (None)."""
        try:
            return commit()
        except HTTPException as exc:
            if exc.status_code == 507:
                raise Stop(str(exc.detail), title) from None
            self.skipped(title, str(exc.detail))
        except Skip as exc:
            self.skipped(title, str(exc))
        except Exception as exc:  # noqa: BLE001 — one note's failure is that note's
            log.warning(f"[{self.TAG}] {title!r} failed: {exc!r}")
            self.skipped(title, str(exc) or exc.__class__.__name__)
        return None

    def write(self, title: str, key: str, props: dict, tree: list, dirs: list, entry: dict) -> bool:
        """The note's folder and its page in one short transaction under the
        write lock, its key looked for again there: two imports of one
        note make one page, and the one that finds it there writes nothing,
        no folder either. The folder at ``dirs`` below the destination is
        made where missing (``folder_inserts``, read under the lock too, so
        two imports filing into one new folder make it once; its batch goes
        through ``ops.apply_batches``)."""
        conn = self.conn
        write_lock(conn)
        try:
            row = conn.execute("SELECT id, content, properties FROM unified_blocks WHERE parent_id = 'root' "
                               f"AND json_extract(properties, '$.{self.KEY}') = ?", (key,)).fetchone()
            if row:
                paths = folder_paths(conn)  # with the folder the other import made
                conn.rollback()
                found = load_props(row[2])
                entry.update(id=row[0], title=row[1], action="skip", kind="pdf" if found.get("doc_id") else "page",
                             folders=[paths[f] for f in filing(found, FOLDERS) if f in paths])
                self.report["pages_skipped"] += 1
                return False
            path = [d.strip() for d in dirs if d.strip()]
            folder_ops, folder_ids = folder_inserts(conn, [path], self.under)
            filed = folder_ids.get(tuple(path)) or self.under
            if filed:
                props[FOLDERS] = [filed]
            results = apply_batches(conn, [(FOLDERS, folder_ops)], actor=self.actor) if folder_ops else []
            insert_page_tree(conn, entry["id"], title, props, tree, self.actor)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        for result in results:
            after_commit(self.ws, conn, result)
        return True

    def room(self, nbytes: int) -> None:
        """507 when storing ``nbytes`` would leave the disk with less than
        MIN_FREE_BYTES free."""
        if shutil.disk_usage(config.DATA_DIR).free - nbytes < MIN_FREE_BYTES:
            raise HTTPException(status_code=507, detail="the server's disk has no room for this note")

    # --- its files ----------------------------------------------------------------------------

    def open_pdf(self, entry, name: str) -> tuple | None:
        return open_pdf(entry, name, self.TAG)

    def store_ink(self, plan, strokes_of, title: str) -> list[list[dict]]:
        """The ink groups of each page of ``plan`` (a planner's: its
        ``kind``, ``pages`` with their ``frame``, ``left_out``): the strokes
        ``strokes_of(page, left_out, left)`` makes in drawing order, packed
        into files (``ink.group_files``), each checked as an upload is and
        stored. Only the page at hand is ever held as samples; past
        MAX_NOTE_SAMPLES in the note the rest of its strokes are left out,
        and never made."""
        left = [MAX_NOTE_SAMPLES]
        out = []
        for k, page in enumerate(plan.pages, 1):
            frame = page.frame
            space = ({"kind": "canvas", "width": frame.width, "height": frame.height} if plan.kind == "notebook"
                     else {"kind": "pdf-page", "page": k, "width": frame.width, "height": frame.height})
            groups = []
            for file in inkmod.group_files(strokes_of(page, plan.left_out, left), space):
                parsed = inkmod.parse_ink(inkmod.dumps(file))  # the bytes form: MAX_BYTES is checked too
                data = inkmod.dumps(parsed)
                self.room(len(data))
                stored, _ = store_file(self.ws, data, ".ink")
                group = {"ink_url": f"/api/uploads/{stored}", "ink_strokes": len(parsed.strokes)}
                if space["kind"] == "pdf-page":
                    group["pdf_position"] = inkmod.pdf_position(parsed)
                groups.append(group)
                self.progress(item=title)
            out.append(groups)
        return out

    def store_picture(self, members, media: nb.Media, frame, title: str, uses: Counter, convert=None) -> dict | None:
        """A picture as a fixed-width text box holding its image; None, with
        a warning, when it cannot be stored. An animated GIF's animation is
        read beside its still frame when the note has it within
        MAX_HELD_BYTES (``picture_bytes`` chooses). ``convert(data, media)``,
        when given, turns a file that is no picture into one first (a
        sticker's PDF). ``uses`` counts the note's pictures by image file:
        one file is read for MAX_PICTURE_USES of them at most, so a note
        that names one file over and over is not read and decoded each
        time."""
        entry = members.find(media.picture)
        name = posixpath.basename(media.picture)
        if entry is None:
            self.warn(title, f"Missing picture {media.picture}: left out")
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
            data = entry.read()
            if convert is not None:
                data = convert(data, media)
            data, ext = picture_bytes(data, media, animation)
        except (ValueError, *READ_ERRORS) as exc:
            self.warn(title, f"Picture {name} is left out: {exc}")
            return None
        self.room(len(data))
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

    def store_recording(self, members, ref: nb.RecordingRef, title: str, created=None) -> dict | None:
        """A recording as a recording block with one segment, started when
        the recording was made (else ``created``, else now); None, with a
        warning, when it cannot be stored: no MPEG-4 audio, no length, a
        file that does not read. No stroke is placed on it: neither app
        stores a time for a stroke's samples."""
        if ref.path is None:
            return None
        entry = members.find(ref.path)
        try:
            with entry.open() as f:
                duration = mp4_duration_ms(f)
            if duration is None:
                self.warn(title, f"Recording {ref.name} has no readable length: left out")
                return None
            self.room(entry.size)
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
        started = round((ref.created or created or datetime.now(UTC)).timestamp() * 1000)
        return {"content": ref.name or "Recording",
                "properties": {"recording": {"started": started, "state": "done", "dur": duration,
                                             "segments": [{"url": f"/api/uploads/{stored}", "started": started,
                                                           "dur": duration, "bytes": entry.size,
                                                           "mime": "audio/mp4"}]}}}
