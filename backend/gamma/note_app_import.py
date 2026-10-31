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
ink page by page, its pictures (picture blocks, ``store_picture``) and its
recordings stored, then its folder and page written in one short
transaction. What a note holds and where it lands on a page is each app's
own module.

The upload is untrusted:

- it is walked through the guarded zip walk (gamma/import_zip.py), with the
  central directory capped (MAX_DIRECTORY_BYTES) before zipfile reads it;
- a note packed in it (a ``.note``, ``.ntb`` or ``.goodnotes``) is opened
  only while it is read, one at a time, in a stack of its own, so the
  temporary disk a note takes is given back before the next and a library
  of thousands of notes imports whole;
- every member is read only after its size is checked (a PDF or picture,
  held whole in memory, up to MAX_HELD_BYTES), a picture is never decoded
  (only its header's size is read; a GoodNotes sticker's PDF is rendered
  through PyMuPDF, at most ``goodnotes_import.MAX_STICKER_SIDE`` px),
  recordings are streamed, and the ink is made and stored one page at a
  time;
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
import posixpath
import shutil
from collections import Counter
from datetime import UTC, datetime

from fastapi import HTTPException

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
from .picture import image_size
from .recording import mp4_duration_ms
from .storage import is_pdf, store_file, store_file_stream

MAX_DIRECTORY_BYTES = 16 << 20   # a zip's central directory (the upload's, each note's): about 100,000 files
MAX_HELD_BYTES = 256 << 20       # a note's PDF or picture: read whole into memory, parsed and composed
MAX_NOTE_SAMPLES = 20_000_000    # flattened samples in one note (a thousand pages of dense writing)
MAX_WARNINGS = 200
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


def markup(plan, groups: list, boxes: list) -> list[dict]:
    """The pages' ink groups, boxes and pictures as blocks: on a PDF page at
    the page's top level (``pdf_page`` on a box or a picture), page by
    page; in a notebook under each page's sheet, which starts folded.
    ``plan`` is a planner's: its ``kind``, ``pages``, and ``papers`` (a
    sheet's paper each, or None) or else ``paper``; ``boxes`` each page's
    ``{content, text_box}`` and ``{content, picture}``."""
    tree = []
    for k, page in enumerate(plan.pages, 1):
        inks = [{"content": "", "properties": group} for group in (groups[k - 1] if groups else [])]
        placed = [{"content": box["content"], "properties": {key: v for key, v in box.items() if key != "content"}}
                  for box in boxes[k - 1]]
        if plan.kind == "notebook":
            sheet = plan.papers[k - 1] if plan.papers else plan.paper
            tree.append({"id": new_block_id(), "content": "", "properties": {"sheet": sheet, "collapsed": True},
                         "children": inks + placed})
        elif plan.kind in ("original", "composed"):
            tree += inks + [{**block, "properties": {**block["properties"], "pdf_page": k}} for block in placed]
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

    def store_picture(self, members, media: nb.Media, frame, title: str, left_out: Counter, files: dict,
                      convert=None) -> dict | None:
        """A picture as a picture block's ``{content, picture}``
        (``notability_map.picture_block``); None, with a warning, when its
        file cannot be stored. The file is stored as it came, never decoded:
        an animated GIF's animation in place of its still frame when the
        note has it within MAX_HELD_BYTES, it is a GIF and it stores
        (``_animation``). Only the still's size is read, from its header
        (``picture.image_size``), for the crop. ``convert(data, media)``,
        when given, turns a file that is no picture into one first (a
        sticker's PDF); it is handed any one of the pictures that show the
        file, so what it makes must not hang on which. ``files``
        holds, by member, what the note's files were found to be and stored
        as (a still apart from an animation), so each is read and stored
        once however many pictures show it, whatever animations they name:
        a still is read again at most once, to be stored for a picture shown
        without the animation an earlier one showed."""
        key, data = ("still", media.picture), None
        if key not in files:
            data, pixels = self._read_still(members, media, title, convert)
            files[key] = None if data is None else {"pixels": pixels, "url": None}
        still = files[key]
        if still is None:
            return None
        url = self._animation(members, media.animation, files) if media.animation else None
        if url is None and still["url"] is None:
            if data is None:  # read before, for a picture shown with its animation
                data, _ = self._read_still(members, media, title, convert)
            still["url"] = None if data is None else self._stored_still(data, media.picture, title)
            if still["url"] is None:
                files[key] = None  # nor tried again for the note's later pictures
                return None
        return nmap.picture_block(media, frame, url or still["url"], still["pixels"], left_out)

    def _read_still(self, members, media: nb.Media, title: str, convert=None) -> tuple:
        """``(bytes, pixel size as shown)`` of a picture's file, through
        ``convert`` when given; ``(None, None)``, with a warning, when it is
        missing, past MAX_HELD_BYTES, does not read or convert, or is no
        PNG, JPEG, GIF or WebP whose size reads."""
        entry = members.find(media.picture)
        name = posixpath.basename(media.picture)
        if entry is None:
            self.warn(title, f"Missing picture {media.picture}: left out")
            return None, None
        if entry.size > MAX_HELD_BYTES:
            self.warn(title, f"Picture {name} is over {MAX_HELD_BYTES >> 20} MB: left out")
            return None, None
        try:
            data = entry.read()
            if convert is not None:
                data = convert(data, media)
        except READ_ERRORS:
            self.warn(title, f"Picture {name} could not be read: left out")
            return None, None
        except ValueError as exc:
            self.warn(title, f"Picture {name} is left out: {exc}")
            return None, None
        pixels = image_size(data)
        if pixels is None or not all(pixels):
            self.warn(title, f"Picture {name} is left out: not a readable PNG, JPEG, GIF or WebP picture")
            return None, None
        return data, pixels

    def _stored_still(self, data: bytes, path: str, title: str) -> str | None:
        """The upload url of a picture's file once stored; None, with a
        warning, when it is refused."""
        try:
            return self._upload(data, path)
        except HTTPException as exc:
            if exc.status_code == 507:
                raise
            self.warn(title, f"Picture {posixpath.basename(path)} is left out: {exc.detail}")
            return None

    def _animation(self, members, path: str, files: dict) -> str | None:
        """The upload url of an animated GIF's animation, read and stored
        once however many of the note's pictures show it (``files``, by its
        member); None when the note does not have it within MAX_HELD_BYTES,
        it does not read, is no GIF whose size reads or is refused, and the
        still frame is shown."""
        key = ("animation", path)
        if key in files:
            return files[key]
        files[key] = None
        moving = members.find(path)
        if moving is None or moving.size > MAX_HELD_BYTES:
            return None
        try:
            data = moving.read()
            if sniff(data) == "image/gif" and image_size(data) is not None:
                files[key] = self._upload(data, path)
        except READ_ERRORS:
            pass
        except HTTPException as exc:
            if exc.status_code == 507:
                raise
        return files[key]

    def _upload(self, data: bytes, path: str) -> str:
        """The upload url of a picture's checked bytes, stored
        (``store_file``: its HTTPException, 507 when the disk has no room)."""
        self.room(len(data))
        stored, _ = store_file(self.ws, data, _PICTURE_EXTS[sniff(data)])
        self.progress(item=posixpath.basename(path))
        return f"/api/uploads/{stored}"

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
