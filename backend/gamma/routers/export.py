"""Exporting pages: one driver (``_run_export``) walks the selected page
subtrees and feeds them to the format's ``_Builder`` — Markdown, an Obsidian
vault, a Logseq graph, a Zotero RDF library, a zip for Notion's importer, a
scoped Gamma backup, a BibTeX bibliography, the notes typeset as a PDF document,
or the annotated PDFs themselves. Most builders produce a zip; a bare .md (nothing to bundle), the
.bib, the notes PDF and one page's annotated PDF are single files. A page's
export takes the pages inside it too when asked (``sub_pages``), laid out
as a folder's pages are. The same builders serve the downloads
(``/pages/{id}/export``, ``/folders/{id}/export``: an import by link, scripts)
and the background job the web app starts (``POST /api/jobs/export``)."""

import base64
import gzip
import json
import mimetypes
import os
import posixpath
import re
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated, Union

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from starlette.background import BackgroundTask

from .. import bibtex as bibtex_mod
from .. import ink as inkmod
from .. import comments, jobs, notability_export, notebook, notion_export, pdf_text, pdf_translated, storage
from ..auth import (link_ratelimit, require_user, require_ws, resolve_ws, share_job_owner, share_lookup,
                    share_scope)
from ..blocks_store import (
    BLOCK_COLUMNS, FOLDERS, LABELS, PATH_SEP, TREES, assert_block_in_scope, block_to_dict,
    existing_in, fetch_subtree, filing, folder_path, folder_subtree_ids, load_props, page_root_id, pages_in_folder,
    sub_pages as pages_right_inside, tree_parents, tree_rows, with_sub_pages)
from ..documents.model import SQL_IN_PLACE, is_pdf, page_document, pdf_file_hash, stored_name
from ..db import connect_pages_db
from ..db import (
    PAGES_SCHEMA,
    copy_chats,
    page_now,
    register_functions,
)
from ..logseq_graph_export import (
    CONFIG_EDN,
    collect_highlights,
    render_area_images,
    render_edn,
    render_graph_page_md,
    render_hls_md,
)
from ..markdown_export import (
    block_ref_resolver,
    build_tree,
    collect_and_rewrite,
    render_readable,
    slugify,
)
from ..logbuf import log
from ..highlights import is_highlight, is_ink, is_link
from ..storage import attachment_disposition, upload_refs
from ..picture import is_picture, normalize_picture, picture_page, stack_order
from ..text_box import box_page, is_text_box, normalize_text_box
from .uploads import _pages_upload_names
from ..obsidian_export import (APP_JSON, Filing, VaultContext, referenced_blocks, render_vault_page, unique_name,
                               vault_name)
from ..pdf_document import render_document
from ..pdf_export import annotate_pdf, highlight_note_blocks, highlight_note_text, still_embedded
from ..comments import threads_for as comment_threads
from ..pdf_notes import NOTES_AT, render_notes
from ..translate_engines import TRANSLATE_LANGS
from ..zotero_export import (
    GAMMA_NOTE_KEY,
    GAMMA_PAGE_KEY,
    IMAGE_MIME,
    MD_IMAGE_RE,
    build_rdf,
    highlight_memo_html,
    note_html,
    standalone_note_html,
    strip_image_md,
)
from .imports import ATTACHMENTS_PROP

router = APIRouter(prefix="/api", tags=["export"])

# Entries packed as they are: a PDF, and media already compressed. Deflating
# a PDF costs a second of one core per 50 MB to save a few percent.
_STORED_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".m4a",
                                ".mp3", ".ogg", ".opus", ".zip"})


def _compression(arcname: str) -> int:
    """How a zip entry is packed, by its name's extension."""
    return zipfile.ZIP_STORED if os.path.splitext(arcname)[1].lower() in _STORED_EXTENSIONS else zipfile.ZIP_DEFLATED


def _write_zip(dest, entries, assets, stored, files=(), blobs=(), progress=jobs.no_progress) -> None:
    """entries: list of (arcname, text). assets: set of upload filenames, found
    through ``stored`` (``storage.files``), written once under assets/ (deduped
    by content-addressed name). files: (arcname, disk path) pairs, a path of
    None passed over; blobs: (arcname, bytes) pairs. ``progress`` hears the
    files packed so far (the phase "packing"). PDFs and compressed media are
    stored (``_compression``), the rest deflated."""
    packed = [(f"assets/{name}", stored(name)) for name in sorted(assets)] + list(files)
    # strict_timestamps=False: a file dated before 1980 goes in as 1980 (backups.stream_zip)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as z:
        for arcname, text in entries:
            z.writestr(arcname, text, compress_type=_compression(arcname))
        for n, (arcname, path) in enumerate(packed):
            progress(phase="packing", done=n, total=len(packed), unit="files")
            if path and path.is_file():
                z.write(path, arcname, compress_type=_compression(arcname))
        for arcname, data in blobs:
            z.writestr(arcname, data, compress_type=_compression(arcname))
    progress(phase="packing", done=len(packed), total=len(packed), unit="files")


def _graph_page_parts(page, stored, include_pdf):
    """One page in Logseq file-graph layout → (text entries, ``_Builder.files``
    entries, blobs, image-asset names). The PDF is renamed sha → page stem so the
    ``hls__<stem>`` page / ``<stem>.edn`` / ``<stem>.pdf`` naming convention
    Logseq's annotation system keys on actually holds. Spaces are replaced so
    inline ``![](../assets/<stem>.pdf)`` links stay valid Markdown."""
    stem = slugify(page.get("content"), page["id"]).replace(" ", "_")
    view = page_document(page.get("properties"))
    name = view["file"] if is_pdf(view) else ""  # a duplicated page's is the file it was copied from
    pdf_path = stored(name) if name else None
    has_pdf = bool(include_pdf and pdf_path)

    md, assets = collect_and_rewrite(
        render_graph_page_md(page, stem, has_pdf), include_pdf=False, prefix="../assets/"
    )
    entries = [(f"pages/{stem}.md", md)]
    files, blobs = [], []
    if has_pdf:
        highlights = collect_highlights(page)
        entries.append((f"pages/hls__{stem}.md", render_hls_md(stem, highlights)))
        entries.append((f"assets/{stem}.edn", render_edn(highlights)))
        files.append((f"assets/{stem}.pdf", pdf_path, name))
        blobs.extend(render_area_images(pdf_path, stem, highlights))
    return entries, files, blobs, assets


def _children_by_id(blocks) -> dict:
    """``{parent_id: [children in sibling order]}`` — what
    highlight_note_blocks walks for an annotation's nested notes."""
    children_by_id: dict = {}
    for b in sorted(blocks, key=lambda b: b["position"] or ""):
        children_by_id.setdefault(b["parent_id"], []).append(b)
    return children_by_id


def _collect_marks(blocks) -> list[dict]:
    """Highlight blocks (``kind`` ``highlight``: not the link regions,
    Gamma navigation aids, nor ink) → annotate_pdf marks (position, colour,
    the popup's note). Skips annotations that came from the PDF itself and
    are STILL embedded in it (``still_embedded``)."""
    children_by_id = _children_by_id(blocks)
    marks = []
    for b in blocks:
        props = b["properties"]
        if b["kind"] != "highlight" or still_embedded(props):
            continue
        marks.append({
            "position": props["pdf_position"],
            "color": props.get("color"),
            "note": highlight_note_text(b, children_by_id),
            # For /Square annotations: the deterministic /NM key Zotero
            # requires before it will import an area annotation.
            "id": b["id"],
        })
    return marks


def _collect_cards(blocks, *, notes: bool, threads: dict) -> list[dict]:
    """Every highlight block → what ``render_notes`` prints for it, from
    ``threads`` (``{block id: [[comment, …], …]}``, the open ones): with
    ``notes`` its notes (``highlight_note_blocks``), each with the threads
    on it, the highlight's own threads with its own note (its text is its
    note, as the app calls it); the highlight's threads on their own when
    it has no note shown; and the threads on the blocks under it the cards
    do not show (all of them with the notes off, an empty note, a text
    box or a picture on the page), each with its block's text to quote, in
    reading order. Unlike the marks, a highlight still embedded in the PDF
    has its cards too: its notes and threads are the reader's own, whatever
    the file already carries."""
    children_by_id = _children_by_id(blocks)

    def under(bid):
        for child in children_by_id.get(bid, []):
            yield child
            yield from under(child["id"])

    cards = []
    for b in blocks:
        if b["kind"] != "highlight":
            continue
        shown = highlight_note_blocks(b, children_by_id) if notes else []
        ids = {bid for _depth, _md, bid in shown}
        cards.append({
            "position": b["properties"].get("pdf_position") or {}, "color": b["properties"].get("color"),
            "blocks": [(depth, md, threads.get(bid, [])) for depth, md, bid in shown],
            "threads": [] if b["id"] in ids else threads.get(b["id"], []),
            "note_threads": [(child.get("content") or "", thread) for child in under(b["id"])
                             if child["id"] not in ids for thread in threads.get(child["id"], [])],
        })
    return cards


def _collect_ink(blocks, stored) -> list[dict]:
    """Handwriting blocks → ``annotate_pdf``'s ink groups (the parsed file,
    the caption + nested notes, the block id). Same skip rule as marks for
    ink that came from the PDF and is still embedded in it."""
    children_by_id = _children_by_id(blocks)
    groups = []
    for b in blocks:
        props = b["properties"]
        url = props.get("ink_url")
        if not url or still_embedded(props):
            continue
        ink_file = inkmod.read_upload(stored, url)
        if not ink_file:
            continue
        groups.append({"ink": ink_file, "note": highlight_note_text(b, children_by_id), "id": b["id"]})
    return groups


def _on_sheets(blocks, page_id) -> set:
    """The ids of the blocks a sheet holds: placed on it, whatever their
    ``pdf_page`` says."""
    return {b["id"] for sheet in notebook.sheets_of(blocks, page_id) for b in sheet["blocks"]}


def _collect_text_boxes(blocks, page_id) -> tuple[list[dict], set]:
    """Text boxes on the PDF's pages, in document order (the order they
    stack in, ``notebook.document_order``) → ``annotate_pdf``'s text boxes
    (the normalized box, the text, the page, the block id, when it last
    changed) and its ``replaced`` keys. A box under a sheet is on the sheet
    whatever its ``pdf_page`` says, and an empty one draws nothing. Unlike
    the other marks, a box that came from the PDF and is still embedded in
    it is written too, and its original leaves the copy (``replaced``, the
    box's ``imported_annot``): the page shows the box as Gamma has it, so an
    edit made here reaches the export."""
    on_sheets = _on_sheets(blocks, page_id)
    boxes, replaced = [], set()
    for b in notebook.document_order(blocks, page_id):
        props = b["properties"]
        if not is_text_box(props):
            continue
        if still_embedded(props):
            replaced.add(props["imported_annot"])
        page = box_page(props, on_sheet=b["id"] in on_sheets)
        if page and b["content"].strip():
            boxes.append({"box": normalize_text_box(props["text_box"]), "content": b["content"],
                          "page": page, "id": b["id"], "modified": b.get("updated_at") or ""})
    return boxes, replaced


def _collect_pictures(blocks, page_id, stored) -> tuple[list[dict], set]:
    """Pictures on the PDF's pages, each page's in the order they stack
    (``picture.stack_order``) → ``annotate_pdf``'s pictures (the normalized
    picture, the caption, the page, the block id, when it last changed) and
    its ``replaced`` keys, by the text boxes' rules (``_collect_text_boxes``):
    one under a sheet is on the sheet, and one imported from the PDF and
    still embedded in it is written again in place of its original. A
    picture whose value does not normalize draws nothing, and one whose file
    is missing is left out with a log line; the original either has in the
    PDF stays."""
    on_sheets = _on_sheets(blocks, page_id)
    pictures, replaced = [], set()
    for b in notebook.document_order(blocks, page_id):
        props = b["properties"]
        pic = normalize_picture(props["picture"]) if is_picture(props) else None
        if pic is None:
            continue
        page = picture_page(props, on_sheet=b["id"] in on_sheets)
        if page and not stored(pic["url"].rsplit("/", 1)[1]):
            log.info(f"[export] the picture {b['id']} is left out: its file {pic['url']} is missing")
            continue
        if still_embedded(props):
            replaced.add(props["imported_annot"])
        if page:
            pictures.append({"picture": pic, "content": b["content"], "page": page, "id": b["id"],
                             "modified": b.get("updated_at") or ""})
    return stack_order(pictures, lambda p: p["picture"]), replaced


def _picture_files(node) -> list[str]:
    """The upload a picture block places, as a file name; [] for any other
    block."""
    props = node.get("properties") or {}
    pic = normalize_picture(props.get("picture")) if is_picture(props) else None
    return [pic["url"].rsplit("/", 1)[1]] if pic else []


# Pasted images above this size stay attachments only — a data URI this big
# would bloat the note beyond what Zotero's editor handles gracefully.
_EMBED_IMAGE_CAP = 4_000_000


def _walk_tree(node):
    yield node
    for child in node.get("children") or []:
        yield from _walk_tree(child)


def _reach(conn, scope) -> set | None:
    """The pages a share's export may read from (``ShareScope.pages``, the
    allows_page rule, in one read); None for a member, who may read them all."""
    return None if scope is None else set(scope.pages(conn))


def _ref_resolver(conn, reach=None):
    """``block_ref_resolver`` as an export reads it. ``reach`` (``_reach``)
    is the one rule of every format through a share: a block on a page
    outside it resolves to nothing, as a deleted one does, so a [[ref]] or an
    ![[embed]] of it stays as typed, the id the share view's own chip shows,
    and nothing it holds reaches the file. GET /api/blocks answers 403 for
    the same block."""
    resolve_ref = block_ref_resolver(conn)
    if reach is None:
        return resolve_ref

    def resolve(block_id):
        ref = resolve_ref(block_id)
        return ref if ref and ref["page_id"] in reach else None

    return resolve


def _image_resolver(stored):
    """``resolve_image`` for note_html: upload filename → (mime, base64)."""
    def resolve(filename):
        mime = IMAGE_MIME.get(filename.rsplit(".", 1)[-1].lower())
        path = stored(filename)
        if not mime or not path or path.stat().st_size > _EMBED_IMAGE_CAP:
            return None
        return mime, base64.b64encode(path.read_bytes()).decode("ascii")
    return resolve


# --- format builders --------------------------------------------------------
# One export = one builder. The driver (_run_export) walks the selected pages
# exactly once — subtree fetch, tree build, progress report — and feeds each
# page to the mode's builder; the builder accumulates zip parts and names the
# download, and ``save`` writes it. Adding an export format = adding a
# builder here; the endpoints, the job and the zip writer stay untouched.

class _Builder:
    """opts: {"pdf": bool, "highlights": bool, "notes": bool, "comments":
    bool, "notes_at": where an annotated PDF's notes go (``NOTES_AT``),
    "folder_scope": the exported folder's id | None (None: one page is
    exported), "author": the account exporting, "share": the request's
    ShareScope | None}. ``filing`` (``obsidian_export.Filing``) names the
    pages' folders and labels once ``begin`` has run. Through a share,
    ``reach`` is what its [[refs]] and ![[embeds]] may read (``_ref_resolver``)
    and ``save`` packs only ``readable``, the uploads the share can read
    (``_can_bundle``): both hold for every format here, so a builder has no
    share rule of its own for them. ``stored`` finds an upload's file
    (``storage.files``); ``files`` holds ``(arcname, path, name)``, ``name``
    the upload it packs, "" for a file of the export's own."""
    suffix = ".zip"  # appended to the base slug for the download name
    roots_only = False  # True: the driver hands over the page's own row, not its subtree

    def __init__(self, ws, base: str, opts: dict):
        self.ws = ws
        self.base = base
        self.opts = opts
        self.stored = storage.files(ws)
        self.entries, self.assets = [], set()
        self.files, self.blobs = [], []
        self.walked, self.skipped = 0, []  # pages the driver fed in; {title, reason} left out
        self._spool = None
        self.filing = None
        self.used = set()   # the arcnames taken (``unique_name``)
        self.stems = {}     # page id → its file's path without the extension, for the pages inside it
        self.reach = None
        self.readable = set()

    def begin(self, conn, root_ids):
        """Sees the whole export set before any page is walked (the DB
        connection is only open during the walk, not in ``save``)."""
        self.filing = Filing(conn, self.opts.get("folder_scope"))
        self.reach = _reach(conn, self.opts.get("share"))
        if self.reach is not None:
            self.readable = _pages_upload_names(conn, list(self.reach))

    def add_page(self, n: int, rows, page):
        raise NotImplementedError

    def finish(self):
        """Last chance to add whole-export parts (config files, the RDF…)."""

    def spool(self, arcname: str, data: bytes) -> None:
        """A generated zip entry too big to hold until ``save`` (an annotated
        PDF): written to a temporary file now and packed from there."""
        if self._spool is None:
            self._spool = Path(tempfile.mkdtemp(prefix="gamma-export-"))
        path = self._spool / f"{len(self.files)}.part"
        path.write_bytes(data)
        self.files.append((arcname, path, ""))

    def discard(self) -> None:
        """Remove what ``spool`` wrote (``save`` does it once the file is out)."""
        if self._spool is not None:
            shutil.rmtree(self._spool, ignore_errors=True)
            self._spool = None

    def _can_bundle(self, name) -> bool:
        """Whether the export may hold the upload ``name``: through a share,
        only what the share can read (``readable``, the rule of
        ``GET /api/uploads/…?share=`` read once, since an export asks it of
        every file). A handwriting picture goes by its ink."""
        if self.opts.get("share") is None:
            return True
        drawn = name.endswith(".svg") and not self.stored(name)
        return (f"{name[:-4]}.ink" if drawn else name) in self.readable

    def _bundled(self) -> tuple[set, list]:
        """The ``assets`` and ``files`` the zip may hold (``_can_bundle``);
        what is no upload (a spooled PDF, a note) is the export's own, and
        an upload is told by the name its entry carries, not by where its
        path lies."""
        return ({name for name in self.assets if self._can_bundle(name)},
                [(arcname, path) for arcname, path, name in self.files
                 if not name or self._can_bundle(name)])

    def save(self, dest, progress=jobs.no_progress) -> tuple[str, str]:
        """Write the export to ``dest``: ``(download name, media type)``."""
        try:
            self.finish()
            assets, files = self._bundled()
            _write_zip(dest, self.entries, assets, self.stored, files, self.blobs, progress)
        finally:
            self.discard()
        return f"{self.base}{self.suffix}", "application/zip"

    def response(self) -> FileResponse:
        """The export as the answer to the request (a temporary file,
        deleted once it is sent)."""
        tmp = tempfile.NamedTemporaryFile(suffix=self.suffix, delete=False)
        tmp.close()
        try:
            name, media_type = self.save(tmp.name)
        except BaseException:
            os.unlink(tmp.name)
            raise
        return FileResponse(tmp.name, media_type=media_type,
                            headers={"Content-Disposition": attachment_disposition(name)},
                            background=BackgroundTask(os.unlink, tmp.name))

    def directory(self, page) -> str:
        """The directory a page's file goes in where each page is a file of
        its own and the folders are directories (the annotated PDFs, the
        Notability notes), the files already placed in ``stems``. A folder
        export: ``Filing.directory``. A page export mirrors no folder: the
        page's file at the top, a page inside it in its parent's directory
        when the parent's file is in the zip (``Filing.parent_dir``)."""
        return self.place(page["id"], page["properties"], self.stems)

    def place(self, page_id, props, stems) -> str:
        """``directory`` as ``name_files`` asks it, of the files it has named."""
        if self.opts.get("folder_scope") is None:
            return self.filing.parent_dir(page_id, stems)
        return self.filing.directory(page_id, props, stems)

    def file_name(self, page, ext: str) -> str:
        """The arcname of the page's own file: its title (``vault_name``)
        with ``ext``, unique in its ``directory`` (``unique_name``), and
        placed in ``stems`` so a page inside it goes in its directory."""
        name = unique_name(self.used, self.directory(page), vault_name(page.get("content") or ""), ext)
        self.stems[page["id"]] = name[:-len(ext)]
        return name

    def skip(self, page, reason: str) -> None:
        """Leave a page out of the export, saying why (the job's result lists
        it, and the bibliography review shows it beside the papers it could
        cite)."""
        self.skipped.append({"page_id": page.get("id") or "",
                             "title": (page.get("content") or "").strip() or "Untitled", "reason": reason})

    def summary(self) -> dict:
        """The job's result: how many pages went in, which were left out and why."""
        return {"pages": self.walked - len(self.skipped), "skipped": self.skipped}

    def render_ink_svgs(self, page_assets, prefix="assets/"):
        """Handwriting pictures for a rendered page: the Markdown names a
        ``<stem>.svg`` next to each ``<stem>.ink`` upload (``ink_svg_name``);
        no such file exists, so it is generated here as a zip blob."""
        for name in page_assets:
            if not name.endswith(".svg") or not self._can_bundle(name):
                continue
            source = self.stored(name[:-4] + ".ink")
            arc = f"{prefix}{name}"
            if not source or self.stored(name) or any(b[0] == arc for b in self.blobs):
                continue
            try:
                svg = inkmod.to_svg(inkmod.parse_ink(source.read_bytes()))
            except (inkmod.InkError, OSError):
                continue
            self.blobs.append((arc, svg.encode("utf-8")))


class _MarkdownBuilder(_Builder):
    """One readable .md per page plus a shared assets/ folder (deduped by
    content-hash filename). [[refs]], ![[embeds]] and internal document links
    resolve against the export set: a target page that is part of the same
    export is linked by relative filename, so the zip is self-contained. The
    files sit at the top, their folders in the front matter, but a sub-page
    whose parent is in the export sits in a directory named for the parent,
    beside the parent's file (``Filing.parent_dir``, Notion's layout), and
    names that file as ``parent:``."""

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.filenames = {}          # page id → arcname inside the zip
        self.resolve_ref = None

    def begin(self, conn, root_ids):
        super().begin(conn, root_ids)
        # Every page's filename up front, so cross-page links can be written
        # while the first page renders.
        for rid in root_ids:
            row = conn.execute("SELECT content FROM unified_blocks WHERE id = ?",
                               (rid,)).fetchone()
            if row is None:
                continue
            stem = self.filing.parent_dir(rid, self.stems) + slugify(row[0], rid)
            arcname = f"{stem}.md"
            # id suffix makes collisions near-impossible, but guard anyway.
            while arcname in self.used:
                arcname = f"{stem}-{len(self.used)}.md"
            self.used.add(arcname)
            self.filenames[rid] = arcname
            self.stems[rid] = arcname[:-3]
        self.resolve_ref = _ref_resolver(conn, self.reach)

    def add_page(self, n, rows, page):
        arcname = self.filenames.get(page["id"]) \
            or f"{slugify(page.get('content'), page['id'])}.md"
        here = arcname.rpartition("/")[0]

        def page_file(page_id):
            """An exported page's file as linked from this one (a sub-page's
            file sits in its parent's directory)."""
            name = self.filenames.get(page_id)
            return posixpath.relpath(name, here or ".") if name else None

        md, page_assets = collect_and_rewrite(
            render_readable(page, highlights=self.opts["highlights"], notes=self.opts["notes"],
                            resolve_ref=self.resolve_ref, page_file=page_file,
                            folder=self.filing.folder(page["id"], page["properties"]),
                            parent=page_file(self.filing.parents.get(page["id"])) or ""),
            include_pdf=self.opts["pdf"], prefix="../" * arcname.count("/") + "assets/")
        self.assets |= page_assets
        self.render_ink_svgs(page_assets)
        self.entries.append((arcname, md))

    def save(self, dest, progress=jobs.no_progress):
        # One page (no folder) referencing no local files is just its .md.
        if self.opts.get("folder_scope") is None and not self.assets and len(self.entries) == 1:
            Path(dest).write_bytes(self.entries[0][1].encode("utf-8"))
            self.discard()
            return f"{self.base}.md", "text/markdown; charset=utf-8"
        return super().save(dest, progress)


def _named_pages(conn, root_ids) -> list[tuple]:
    """``root_ids`` as ``obsidian_export.name_files`` takes them: (page id,
    title, properties), those no longer there left out."""
    pages = []
    for rid in root_ids:
        row = conn.execute("SELECT content, properties FROM unified_blocks WHERE id = ?",
                           (rid,)).fetchone()
        if row is None:
            continue
        try:
            props = json.loads(row[1] or "{}")
        except (TypeError, ValueError):
            props = {}
        pages.append((rid, row[0] or "", props))
    return pages


class _ObsidianBuilder(_Builder):
    """An Obsidian vault (``obsidian_export``): ``<dir>/<Title>.md`` per page
    (directories = the folders below the exported folder, a sub-page's
    file inside its parent's directory, labels as tags),
    ``attachments/`` with the images and — with the bundle switch — the PDFs
    named after their page, wikilinks / ``^id`` anchors resolved against the
    export set, and an ``.obsidian/app.json`` that marks the folder as a
    vault (the importer reads it back as one)."""
    suffix = "-obsidian.zip"

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.ctx = None

    def begin(self, conn, root_ids):
        super().begin(conn, root_ids)
        self.ctx = VaultContext(_ref_resolver(conn, self.reach), include_pdf=self.opts["pdf"])
        pages = _named_pages(conn, root_ids)
        self.ctx.name_pages(self.filing, pages)
        # Every block that something links to needs its ^anchor written, and
        # a page may be rendered before the page that links into it — so the
        # link-bearing blocks are scanned up front (links from outside the
        # export only add harmless anchors; a share scans its own pages).
        texts = (r[0] for r in conn.execute(
            "SELECT content, page_id FROM unified_blocks WHERE content LIKE '%[[%'")
                 if self.reach is None or r[1] in self.reach)
        referenced_blocks(texts, self.ctx)
        if self.opts["pdf"]:
            for rid, title, _ in pages:
                self._bundle_pdf(conn, rid, title)

    def _bundle_pdf(self, conn, rid, title):
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (rid,)).fetchone()
        view = page_document(load_props(row[0]) if row else None)
        name = view["file"] if is_pdf(view) else ""
        path = self.stored(name) if name else None
        if path:
            arcname = self.ctx.name_pdf(rid, title, view["hash"])
            if all(f[0] != arcname for f in self.files):
                self.files.append((arcname, path, name))

    def add_page(self, n, rows, page):
        md, page_assets = collect_and_rewrite(
            render_vault_page(page, self.ctx, tags=self.filing.tags(page["properties"]),
                              highlights=self.opts["highlights"], notes=self.opts["notes"]),
            include_pdf=self.opts["pdf"], prefix="attachments/")
        self.assets |= page_assets
        self.entries.append((self.ctx.page_file[page["id"]], md))

    def finish(self):
        # Attachments live in attachments/, not the readable export's assets/.
        self.files += [(f"attachments/{name}", self.stored(name), name)
                       for name in sorted(self.assets)]
        self.assets = set()
        self.entries.append((".obsidian/app.json", APP_JSON))


class _LogseqBuilder(_Builder):
    """A Logseq file graph: pages/ + assets/ + logseq/config.edn, highlights
    as native hls__ pages + EDN (see _graph_page_parts)."""
    suffix = "-logseq.zip"

    def add_page(self, n, rows, page):
        p_entries, p_files, p_blobs, p_assets = _graph_page_parts(
            page, self.stored, self.opts["pdf"])
        self.entries += p_entries
        self.files += p_files
        self.blobs += p_blobs
        self.assets |= p_assets

    def finish(self):
        self.entries.append(("logseq/config.edn", CONFIG_EDN))


# A block under the import's "Attachments" heading that is one of Zotero's
# other attachments again: a file chip or a web link, one markdown link
# (imports._markdown_link) — its label, and its upload or its URL.
_CHIP_RE = re.compile(r"\[([^\]\n]*)\]\((?:/api/uploads/([0-9a-fA-F]+\.[A-Za-z0-9]+)|(https?://[^\s)]+))\)")


class _ZoteroBuilder(_Builder):
    """A Zotero RDF library: ``<base>/<base>.rdf`` + ``<base>/files/<n>/…``.
    What is drawn on the page travels inside the PDF copies (annotate_pdf —
    Zotero's "Include Annotations" convention): highlights, area notes and
    handwriting with the highlights switch, text boxes and pictures with
    either switch, as the annotated PDF writes them, in Zotero's own
    colours. A page with nothing to draw sends its stored PDF as it is.
    Notes become bib:Memo items with pasted images embedded as data URIs,
    the folders below the exported folder the collection tree, labels tags.
    Images referenced anywhere in a page, the pictures placed on it among
    them, also ride as item attachments; annotation comments carry a plain
    "(image: …)" placeholder since they can't hold pictures. The file chips
    and web links of the page's "Attachments" block go back as the
    attachments the import made them of. A note page (a Zotero standalone
    note the import made a page of) goes out as that note."""
    suffix = "-zotero.zip"

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.items = []
        self.keys = set()  # the item keys written: each names one item
        self.resolve_image = _image_resolver(self.stored)
        self.resolve_ref = None

    def begin(self, conn, root_ids):
        super().begin(conn, root_ids)
        # [[refs]] in a text box read as the text they name, as in the annotated PDF.
        self.resolve_ref = _ref_resolver(conn, self.reach)

    def _item_key(self, page, props) -> str:
        """The page's Zotero key when it came from Zotero, else its id: the
        same in every export, which is what the import upserts by, and back
        in its own library names the page itself. A key already written (a
        copied page keeps its Zotero key) goes to the page's id, as Zotero's
        translator keeps its own names unique."""
        key = props.get("zotero_key") or ""
        if not key or key in self.keys:
            key = f"{GAMMA_PAGE_KEY}{page['id']}"
        self.keys.add(key)
        return key

    def _pdf_copy(self, n, rows, page, title) -> tuple[str | None, set]:
        """The page's PDF in the zip: its path there (None: the export
        carries no files, or the page has no stored PDF) and the ids of the
        ink groups, text boxes and pictures the copy carries. With nothing
        to draw it is the stored file, packed from storage unread; else the
        copy is drawn as Zotero reads it (``zotero=True``: no /CA for Zotero
        to pale the colours by) and spooled."""
        view = page_document(page.get("properties"))
        name = view["file"] if is_pdf(view) else ""
        if not (self.opts["pdf"] and name):
            return None, set()
        pdf_path = self.stored(name)
        if not pdf_path:
            return None, set()
        pdf_arc = f"files/{n}/{slugify(title, '')}.pdf"
        highlights, notes = self.opts["highlights"], self.opts["notes"]
        blocks = [block_to_dict(r) for r in rows] if highlights or notes else []
        marks = _collect_marks(blocks) if highlights else []
        ink = _collect_ink(blocks, self.stored) if highlights else []
        boxes, replaced = _collect_text_boxes(blocks, page["id"]) if highlights or notes else ([], set())
        pictures, more = _collect_pictures(blocks, page["id"], self.stored) if highlights or notes else ([], set())
        if marks or ink or boxes or pictures or replaced or more:
            for mark in marks + ink:
                mark["note"] = strip_image_md(mark["note"])
            try:
                data, _ = annotate_pdf(pdf_path.read_bytes(), marks, author=self.opts.get("author", ""), ink=ink,
                                       text_boxes=boxes, replaced=replaced | more, resolve_ref=self.resolve_ref,
                                       pictures=pictures, stored=self.stored, zotero=True)
            except Exception as e:
                log.warning(f"[zotero export] annotating '{title}' failed, exporting bare PDF: {e}")
            else:
                self.spool(f"{self.base}/{pdf_arc}", data)
                return pdf_arc, {g["id"] for g in ink} | {b["id"] for b in boxes} | {p["id"] for p in pictures}
        self.files.append((f"{self.base}/{pdf_arc}", pdf_path, name))
        return pdf_arc, set()

    def _attachments(self, n, page) -> tuple[list, set]:
        """The item's attachments besides its PDF (``build_rdf``'s
        ``images``), their files packed: the pictures its blocks show or
        place, and the file chips and web links under its "Attachments"
        block, each file under the name its chip shows. Also the ids of
        those chips, which are no note. Files ride only when the export
        carries them."""
        out, packed, chips = [], set(), set()

        def pack(fname, arc, title):
            path = None if fname in packed else self.stored(fname)
            if not path:
                return
            packed.add(fname)
            self.files.append((f"{self.base}/{arc}", path, fname))
            out.append({"path": arc, "title": title,
                        "mime": IMAGE_MIME.get(fname.rsplit(".", 1)[-1].lower())
                                or mimetypes.guess_type(fname)[0] or "application/octet-stream"})

        if self.opts["pdf"]:
            for node in _walk_tree(page):
                for fname in [*MD_IMAGE_RE.findall(node.get("content") or ""), *_picture_files(node)]:
                    pack(fname, f"files/{n}/{fname}", fname)
        for block in page["children"]:
            if not (block.get("properties") or {}).get(ATTACHMENTS_PROP):
                continue
            for chip in block.get("children") or []:
                found = _CHIP_RE.fullmatch((chip.get("content") or "").strip())
                if not found or chip.get("children"):
                    continue
                chips.add(chip["id"])
                label, fname, url = found.groups()
                if url:
                    out.append({"url": url, "title": label or url})
                elif self.opts["pdf"]:
                    ext = os.path.splitext(fname)[1]
                    stem = label[:-len(ext)] if label.lower().endswith(ext.lower()) else label
                    # A folder each, as Zotero keeps them: two chips may show one name.
                    pack(fname, f"files/{n}/{len(out) + 1}/{slugify(stem, '')}{ext}", label or fname)
        return out, chips

    def _note_page(self, page, props, title) -> None:
        """A note page as the standalone note it came in as: its title the
        note's heading, its blocks the note. The page is nothing but that
        note, so with the notes switch off it is left out."""
        if not self.opts["notes"]:
            self.skip(page, "a note page, and notes are switched off")
            return
        children = [c for c in page["children"]
                    if not (is_highlight(c.get("properties")) or is_link(c.get("properties")))]
        self.items.append({
            "key": self._item_key(page, props),
            "title": title,
            "tags": self.filing.tags(props),
            "folders": self.filing.folders(page["id"], props),
            "memo": standalone_note_html(title, children, self.resolve_image, self.resolve_ref),
        })

    def add_page(self, n, rows, page):
        props = page.get("properties") or {}
        title = re.sub(r"\s+", " ", page.get("content") or "").strip() or "Untitled"
        if props.get("zotero_note") and props["zotero_note"] == props.get("zotero_key"):
            self._note_page(page, props, title)
            return
        meta = props.get("meta") if isinstance(props.get("meta"), dict) else {}
        # in_copy: the ink groups, text boxes and pictures the PDF copy carries
        pdf_arc, in_copy = self._pdf_copy(n, rows, page, title)
        attachments, chips = self._attachments(n, page)

        notes = []
        if self.opts["notes"]:
            # Top-level non-highlight subtrees, one Zotero note each — the
            # inverse of the import's notes→child-blocks mapping. Writing
            # nested under highlights instead travels in the annotation popups,
            # and so does an ink group's in the copy. A text box in the copy
            # is its own text there, and a picture is drawn there; one with
            # notes under it is a note too, since a Zotero text annotation
            # holds nothing but its text and a stamp is no annotation Zotero
            # keeps.
            for child in page["children"]:
                cprops = child.get("properties") or {}
                if is_highlight(cprops) or is_link(cprops):
                    continue
                if cprops.get(ATTACHMENTS_PROP):
                    # Its chips are attachments; what else is written under it, a note.
                    child = {**child, "children": [c for c in child.get("children") or [] if c["id"] not in chips]}
                    if not child["children"]:
                        continue
                if child["id"] in in_copy and not (
                        (is_text_box(cprops) or is_picture(cprops))
                        and any((d.get("content") or "").strip() for d in _walk_tree(child) if d is not child)):
                    continue
                html = note_html(child, self.resolve_image, self.resolve_ref)
                if html:
                    notes.append({"key": f"{GAMMA_NOTE_KEY}{child['id']}", "html": html})
            # Popup comments are plain text, so a highlight (or an ink group in
            # the copy) whose notes carry images ALSO becomes a Zotero note
            # (page + quote header) with the pictures embedded.
            for node in _walk_tree(page):
                nprops = node.get("properties") or {}
                if not (is_highlight(nprops) or (node["id"] in in_copy and is_ink(nprops))):
                    continue
                if not any(MD_IMAGE_RE.search(d.get("content") or "")
                           for d in _walk_tree(node)):
                    continue
                html = highlight_memo_html(node, self.resolve_image, self.resolve_ref)
                if html:
                    notes.append({"key": f"{GAMMA_NOTE_KEY}{node['id']}", "html": html})

        self.items.append({
            "key": self._item_key(page, props),
            "title": title,
            "meta": meta or {},
            "cite_key": bibtex_mod.pinned_key(props),
            "tags": self.filing.tags(props),
            "folders": self.filing.folders(page["id"], props),
            "pdf_path": pdf_arc,
            "images": attachments,
            "notes": notes,
            "rdf": props.get("zotero_rdf"),
            "url": str(props.get("web_url") or ""),
        })

    def finish(self):
        # Zotero's import wizard can't read a .zip (it reports "unsupported
        # format") — people try exactly that, so the how-to rides along.
        readme = (
            "Import into Zotero\n"
            "==================\n\n"
            f"1. Extract this zip somewhere (keep {self.base}.rdf and files/ together).\n"
            f"2. In Zotero: File -> Import... -> \"A file\" -> pick {self.base}.rdf.\n\n"
            "Do NOT pick the .zip itself - Zotero reports 'unsupported format' for it.\n"
            "Collections, tags, notes, PDFs and other attachments come along. Highlights,\n"
            "handwriting and text boxes ride inside the PDFs: Zotero shows them, locked,\n"
            "once a PDF is open, and File -> Import Annotations... there makes them\n"
            "Zotero's own. Text boxes need Zotero 7 or later. Pictures placed on a page\n"
            "are drawn in the PDFs too, and Zotero shows them, but it imports no\n"
            "annotation for them: they stay part of the PDF.\n"
        )
        self.entries += [(f"{self.base}/{self.base}.rdf", build_rdf(self.items)),
                         (f"{self.base}/README.txt", readme)]


class _GammaBuilder(_Builder):
    """A scoped account backup in the ``gamma-backup-1`` layout (/api/export's
    format): a pages.db holding just the selected page subtrees verbatim
    with their comments and their AI chats, each bucket's active
    conversation and its history, and the folder and label blocks they are filed under (every folder a
    page is in with the folders above it, every label it carries) — plus,
    on a folder export, the exported folder's whole subtree (empty
    subfolders too) with the folders above it and the chats of the folder
    views in it —, and uploads/ with just the files they reference — no
    data.db, which holds nothing that is not rebuilt. Any Gamma imports it
    through the existing ``/api/import-data?mode=merge`` — additive, deduped
    by block id / the PDF a page owns / conversation / content hash, its
    folders mapped onto the workspace's at the same path
    (``ws_backup._merge_trees``), so re-importing adds nothing. Lossless by
    construction, which is why the dialog's three switches don't apply to
    this format.

    Through a share token (``opts["share"]``, the request's ShareScope:
    another Gamma importing a shared page or folder by its link) it carries
    only what the share shows (``_shared_trees``): the exported folder's
    subtree with that folder at the top level, none of the folders above or
    beside it, and of the chats only each page's active conversation."""
    suffix = "-gamma.zip"

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.db = sqlite3.connect(":memory:")
        register_functions(self.db)
        for stmt in PAGES_SCHEMA:
            self.db.execute(stmt)
        self.page_ids = []
        self.filed = set()  # the folder and label ids the pages carry
        self.upload_names = set()

    def _put(self, row):
        """Copy a block row (``BLOCK_COLUMNS``, the stored columns); one met
        twice (a shared subtree) is kept once."""
        self.db.execute(f"INSERT OR IGNORE INTO unified_blocks ({BLOCK_COLUMNS}) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tuple(row))

    def add_page(self, n, rows, page):
        self.page_ids.append(page["id"])
        self.filed.update(*(filing(page["properties"], tree) for tree in TREES))
        for row in rows:
            self._put(row)
            # Referenced uploads: any /api/uploads/<file> in content or
            # properties (pasted images, file chips), plus the file of a
            # block's document (doc) — the one reference rule
            # (storage.upload_refs) the orphan bookkeeping keeps files by.
            # Through a share, only what the pages show (held=False): the
            # older PDF a pending highlight holds is no file the share
            # serves (routers/uploads.py).
            self.upload_names |= upload_refs(row[3] or "", row[4] or "{}", held=not self.opts.get("share"))

    def _shared_trees(self, src, share, subtree: set) -> dict:
        """The folder and label blocks a share-scoped export carries, as
        ``{id: the parent it gets}`` (None: its own): a folder export the
        exported folder's subtree (``subtree``), that folder at the top; a
        page through a folder share its folders inside the shared folder
        with the ones between, the shared folder at the top; a folder
        share's pages their labels, flat, as its listing shows them
        (``routers/blocks._trees``); a page share, which shows no folder or
        label, none."""
        if not share.folder:
            return {}
        if subtree:
            kept = dict.fromkeys(subtree)
            kept[self.opts["folder_scope"]] = FOLDERS
        else:
            reach, parents = folder_subtree_ids(src, share.folder), tree_parents(src, FOLDERS)
            kept = {}
            for block_id in self.filed & reach:
                while block_id in reach and block_id not in kept:  # up to the shared folder
                    kept[block_id] = None
                    block_id = parents.get(block_id)
            if kept:
                kept[share.folder] = FOLDERS
        kept.update(dict.fromkeys(existing_in(src, LABELS, sorted(self.filed)), LABELS))
        return kept

    def _confine(self, kept: dict) -> None:
        """Re-root the kept tree blocks (``_shared_trees``) in the export
        and file its pages only under them."""
        for block_id, parent in kept.items():
            if parent:
                self.db.execute("UPDATE unified_blocks SET parent_id = ? WHERE id = ?", (parent, block_id))
        for page_id in self.page_ids:
            raw = self.db.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()[0]
            props = json.loads(raw or "{}")
            filed = {tree: [i for i in filing(props, tree) if i in kept] for tree in TREES if tree in props}
            if all(ids == props[tree] for tree, ids in filed.items()):
                continue
            for tree, ids in filed.items():
                if ids:
                    props[tree] = ids
                else:
                    props.pop(tree)
            self.db.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?", (json.dumps(props), page_id))

    def finish(self):
        scope = self.opts.get("folder_scope")
        share = self.opts.get("share")
        with connect_pages_db(self.ws) as src:
            subtree = folder_subtree_ids(src, scope) if scope else set()
            if share is not None:
                kept = self._shared_trees(src, share, subtree)
                blocks = set(kept)
            else:
                parents = {r[0]: r[1] for tree in TREES for r in tree_rows(src, tree)}
                blocks = set(subtree)
                for block_id in self.filed | ({scope} if scope else set()):
                    while block_id in parents:  # the block and the folders above it
                        blocks.add(block_id)
                        block_id = parents[block_id]
            for row in src.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks "
                                   "WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(sorted(blocks)),)):
                self._put(row)
            if share is not None:
                self._confine(kept)
                # what the share reads of the chats: each page's active conversation
                copy_chats(src, self.db, "bucket IN (SELECT value FROM json_each(?))",
                           [json.dumps(self.page_ids)], tables=("chats",))
            else:
                # the pages' chats, and on a folder export its folder views'
                copy_chats(src, self.db, "bucket IN (SELECT value FROM json_each(?))",
                           [json.dumps(self.page_ids + sorted(subtree))])
            # the threads shown on the pages; their authors' names travel with them (author_name)
            comments.copy_rows(self.db, comments.rows_of_pages(src, self.page_ids))
        # A PDF read from a file on this computer travels as bytes: where the
        # export lands it is a page like any other, with no path to read from.
        self.db.execute(f"UPDATE unified_blocks SET properties = json_set(properties, '$.doc.at', 'store') "
                        f"WHERE {SQL_IN_PLACE}")
        # Stamped as the rows are, of this release: one that predates their
        # shapes refuses the file ("made by a newer Gamma") rather than
        # merging pages it cannot read, and a newer one runs only its own
        # steps on them (ws_backup._normalize_copies).
        from .. import migrations  # local: only the stamp is read here

        self.db.execute(f"PRAGMA user_version = {migrations.SCHEMA_VERSION}")
        self.db.commit()
        pages_bytes = self.db.serialize()
        self.db.close()

        self.blobs.append(("pages.db", pages_bytes))
        self.blobs.append(("manifest.json", json.dumps({
            "format": "gamma-backup-1",  # what import-data validates
            "scope": {"folder": scope, "pages": len(self.page_ids)},
            "exported_at": page_now(),
        }, indent=2)))
        self.files += [(f"uploads/{name}", self.stored(name), name)
                       for name in sorted(self.upload_names)]


class _NotesPdfBuilder(_Builder):
    """The notes themselves as a PDF document (``pdf_document``): title,
    metadata, the block tree typeset as nested bullets with quotes, code,
    images and math. Its download isn't a zip — one PDF holds every
    selected page, each starting on a fresh sheet — so it overrides
    ``save`` instead of accumulating zip parts."""
    suffix = "-notes.pdf"

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.pages = []

    def add_page(self, n, rows, page):
        self.pages.append(page)

    def render(self) -> bytes:
        try:
            # The walk's connection is closed by the time render() runs,
            # so [[ref]]/![[embed]] resolution opens its own (read-only use).
            with connect_pages_db(self.ws) as conn:
                return render_document(
                    self.pages, stored=self.stored,
                    highlights=self.opts["highlights"], notes=self.opts["notes"],
                    resolve_ref=_ref_resolver(conn, self.reach))
        except Exception as e:
            log.warning(f"[notes pdf export] '{self.base}' failed: {e}")
            raise HTTPException(status_code=400, detail=f"could not build the PDF: {jobs.error_text(e)}")

    def save(self, dest, progress=jobs.no_progress):
        progress(phase="typesetting")
        Path(dest).write_bytes(self.render())
        return f"{self.base}{self.suffix}", "application/pdf"


class _AnnotatedPdfBuilder(_Builder):
    """Each page's PDF with its highlights (and handwriting) as standard
    annotations and, with the notes switch, its notes in a column beside
    each page (``annotated_page_pdf``: what /pages/{id}/export-pdf downloads). One
    PDF: that PDF itself. A folder: a zip of them, ``<dir>/<Title>.pdf``,
    the directories mirroring the folders below the exported folder
    (``obsidian_export.page_dir``), a sub-page's PDF inside its parent's
    directory when the parent's is in the zip (``directory``). A page with
    the pages inside it: a zip of their PDFs, the page's at the top. A page
    with sheets of paper and no PDF is exported as its sheets; a page with
    neither is left out, and the result lists it."""
    suffix = "-annotated.zip"

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.single_name = ""

    def add_page(self, n, rows, page):
        # The translations go by page: a page's export carries its own, not
        # that of a page inside it, and a folder's those of its papers the
        # browser laid out (``start_export_job``).
        translation = (self.opts.get("translations") or {}).get(page["id"])
        try:
            data, filename, _, _ = annotated_page_pdf(
                self.ws, [block_to_dict(r) for r in rows], page["id"], highlights=self.opts["highlights"],
                notes=self.opts["notes"], comments=self.opts["comments"], notes_at=self.opts["notes_at"],
                author=self.opts.get("author", ""), reach=self.reach, translation=translation)
        except HTTPException as e:
            self.skip(page, str(e.detail))
            return
        self.single_name = filename
        self.spool(self.file_name(page, ".pdf"), data)

    def save(self, dest, progress=jobs.no_progress):
        if not self.files:
            reasons = {s["reason"] for s in self.skipped}
            raise HTTPException(status_code=400, detail=next(iter(reasons)) if len(reasons) == 1 else
                                "none of these pages has a PDF stored on the server")
        if self.opts.get("folder_scope") is None and len(self.files) == 1:  # one page's PDF: the PDF itself
            try:
                shutil.copyfile(self.files[0][1], dest)
            finally:
                self.discard()
            return self.single_name, "application/pdf"
        return super().save(dest, progress)


class _NotabilityBuilder(_Builder):
    """Each page as a Notability note (``notability_export``): its PDF or
    its sheets of paper, its handwriting, boxes, pictures, notes and
    recordings, which Gamma's Notability import brings back as they were.
    One note: the .note itself. A folder, or a page with the pages inside
    it: a zip of them, ``<dir>/<Title>.note``, filed as the annotated PDFs
    are (``directory``): the directories mirroring the page's folders
    below the exported folder, a sub-page's note inside its parent's
    directory. What a page loses on the
    way (its highlights, a recording that is no .m4a, …) is listed under
    its title in the result's ``warnings``; a page that cannot be a note (a
    PDF page whose file is gone) is left out."""
    suffix = "-notability.zip"

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.single_name = ""
        self.warnings = []
        self.resolve_ref = None

    def begin(self, conn, root_ids):
        super().begin(conn, root_ids)
        self.resolve_ref = _ref_resolver(conn, self.reach)  # the walk's connection: open while the pages are written

    def add_page(self, n, rows, page):
        if self._spool is None:
            self._spool = Path(tempfile.mkdtemp(prefix="gamma-export-"))
        path = self._spool / f"{len(self.files)}.part"
        try:
            warnings = notability_export.write_page([block_to_dict(r) for r in rows], page["id"], self.stored,
                                                    path, self.resolve_ref)
        except notability_export.ExportError as e:
            path.unlink(missing_ok=True)
            self.skip(page, str(e))
            return
        title = (page.get("content") or "").strip() or "Untitled"
        self.warnings += [{"page_id": page["id"], "title": title, "reason": reason} for reason in warnings]
        name = self.file_name(page, ".note")
        self.single_name = name.rsplit("/", 1)[-1]
        self.files.append((name, path, ""))

    def save(self, dest, progress=jobs.no_progress):
        if not self.files:
            reasons = {s["reason"] for s in self.skipped}
            self.discard()
            raise HTTPException(status_code=400, detail=next(iter(reasons)) if len(reasons) == 1 else
                                "none of these pages could be written as a note")
        if self.opts.get("folder_scope") is None and len(self.files) == 1:  # one page's note: the .note itself
            try:
                shutil.copyfile(self.files[0][1], dest)
            finally:
                self.discard()
            return self.single_name, "application/octet-stream"
        return super().save(dest, progress)

    def summary(self) -> dict:
        return {**super().summary(), "warnings": self.warnings}


class _NotionBuilder(_Builder):
    """A zip for Notion's importer (``notion_export``), a zip however few
    pages it holds: ``<dir>/<Title>.md`` per page, filed as the annotated
    PDFs are (``directory``), each page's images in a directory named for
    it. Links resolve against the export set, so every page is named before
    the first one renders. The PDFs stay out whatever the ``pdf`` switch
    says, since Notion drops them; what a page loses is listed under its
    title in the result's ``warnings``, an upload a share cannot read
    (``_can_bundle``) among them."""
    suffix = "-notion.zip"

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.ctx = None
        self.sizes = {}              # arcname of a bundled file → its size

    def begin(self, conn, root_ids):
        super().begin(conn, root_ids)
        self.ctx = notion_export.NotionContext(_ref_resolver(conn, self.reach), self._bundle,
                                               self.opts["highlights"], self.opts["notes"])
        self.ctx.name_pages(self.filing, _named_pages(conn, root_ids), self.place)

    def _bundle(self, name, stem) -> int | None:
        """The upload ``name`` beside the page file ``stem``: its size, None
        for a file that is gone, which ``_write_zip`` would pass over without
        a word, or that the export may not hold. A handwriting picture has no
        file: it is rendered here."""
        arcname = f"{stem}/{name}"
        if arcname not in self.sizes and not self._can_bundle(name):
            self.sizes[arcname] = None
        if arcname not in self.sizes:
            path = self.stored(name)
            if path:
                self.files.append((arcname, path, name))
                self.sizes[arcname] = path.stat().st_size
            else:
                self.render_ink_svgs([name], prefix=f"{stem}/")
                if self.blobs and self.blobs[-1][0] == arcname:
                    self.sizes[arcname] = len(self.blobs[-1][1])
        return self.sizes.get(arcname)

    def add_page(self, n, rows, page):
        text = notion_export.render_notion_page(page, self.ctx, self.filing.tags(page["properties"]))
        if text is not None:
            self.entries.append((self.ctx.files[page["id"]], text))

    def save(self, dest, progress=jobs.no_progress):
        if self.walked and not self.entries:         # every page was left out
            raise HTTPException(status_code=400, detail="nested too deep to write as a Notion page")
        return super().save(dest, progress)

    def finish(self):
        if len(self.entries) + len(self.files) + len(self.blobs) > notion_export.MAX_FILES:
            self.ctx.warnings.append({"page_id": "", "title": self.base, "reason": (
                f"over {notion_export.MAX_FILES:,} files, which Notion's importer is likely to fail on")})

    def summary(self) -> dict:
        return {**super().summary(), "warnings": self.ctx.warnings}


class _BibtexBuilder(_Builder):
    """One ``.bib`` bibliography: the citation entry of every page that has
    paper metadata — ``properties.bibtex``, the entry the metadata lookup
    cached, rebuilt from ``properties.meta`` for a page that has a record but
    no rendering. Clashing citation keys are suffixed a, b, … and a key
    pinned on a page (``properties.cite_key``) keeps its exact spelling, so
    the .tex files already citing it go on working (``gamma/bibtex.py``).
    Entries are sorted by key: an unchanged library re-exports
    byte-identically, so a bibliography kept in a repository — or refreshed
    from a share link — only shows a diff when the metadata changed. A page
    without metadata is left out and the finished export lists it; a set
    where no page has any fails with the reason."""
    suffix = ".bib"
    roots_only = True  # a bibliography reads page properties, never the notes

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.records = []

    def add_page(self, n, rows, page):
        props = page.get("properties") or {}
        text = bibtex_mod.page_entry(props)
        if not text:
            self.skip(page, "page has no paper metadata")
            return
        self.records.append({"text": text, "key": bibtex_mod.entry_key(text), "pinned": bool(bibtex_mod.pinned_key(props)),
                             "page_id": page["id"], "title": (page.get("content") or "").strip()})

    def keyed_records(self) -> list[dict]:
        """The records as the file will hold them: sorted by key, then title,
        with the keys made unique. One source for the download and for the
        export dialog's review, so the review cannot disagree with the file.
        (Not ``entries`` — that is the base class's list of zip parts.)"""
        return bibtex_mod.unique_keys(sorted(self.records, key=lambda r: (r["key"], r["title"])))

    def text(self) -> str:
        """The .bib file itself."""
        return bibtex_mod.bibliography(self.keyed_records(), self.filing.title)

    def preview(self) -> dict:
        """What the export dialog reviews (GET /api/bibliography): a record per
        citable page, the pages left out with the reason, and the file's own
        text. An empty bibliography is data here rather than the refusal
        ``save`` raises, so the dialog can show the pages it could not cite
        instead of an error."""
        return {"entries": self.keyed_records(), "skipped": self.skipped, "text": self.text()}

    def save(self, dest, progress=jobs.no_progress):
        try:
            if not self.records:
                raise HTTPException(status_code=400, detail=(
                    "none of these pages has paper metadata to cite" if self.opts.get("folder_scope") or self.walked > 1
                    else "this page has no paper metadata to cite"))
            # newline="": write the text as it is, so the file a Windows
            # server serves is the same bytes as a Linux one and as the
            # dialog's preview.
            Path(dest).write_text(self.text(), encoding="utf-8", newline="")
        finally:
            self.discard()
        return f"{self.base}.bib", "application/x-bibtex; charset=utf-8"


_BUILDERS = {
    "readable": _MarkdownBuilder,
    "bibtex": _BibtexBuilder,
    "obsidian": _ObsidianBuilder,
    "logseq-graph": _LogseqBuilder,
    "zotero-rdf": _ZoteroBuilder,
    "gamma": _GammaBuilder,
    "notes-pdf": _NotesPdfBuilder,
    "annotated-pdf": _AnnotatedPdfBuilder,
    "notability": _NotabilityBuilder,
    "notion": _NotionBuilder,
}


def _run_export(conn, ws, mode: str, root_ids, base: str, opts: dict, progress=jobs.no_progress) -> _Builder:
    """The shared export driver: one pass over the selected pages, each handed
    to the mode's builder. ``progress`` (a job's report) hears each page."""
    cls = _BUILDERS.get(mode)
    if cls is None:
        raise HTTPException(status_code=400, detail=f"unknown export mode: {mode}")
    builder = cls(ws, base, opts)
    try:
        builder.begin(conn, root_ids)
        for n, root_id in enumerate(root_ids, 1):
            if cls.roots_only:
                # The page's own row is all this format reads (a bibliography):
                # one query instead of a subtree walk per page, which is what
                # makes a whole library's .bib answer a plain GET.
                row = conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?",
                                   (root_id,)).fetchone()
                rows = [row] if row is not None else []
                page = block_to_dict(row) if row is not None else None
            else:
                rows = fetch_subtree(conn, root_id)
                page = build_tree(rows, root_id)
            if page is None:
                continue
            progress(done=n - 1, total=len(root_ids), unit="pages", item=(page.get("content") or "").strip())
            builder.add_page(n, rows, page)
            builder.walked += 1
        progress(done=len(root_ids), total=len(root_ids), unit="pages", item="")
    except BaseException:
        builder.discard()
        raise
    return builder


def _notes_place(notes_at: str) -> str:
    """``notes_at`` as given; 400 when it is no place notes go."""
    if notes_at not in NOTES_AT:
        raise HTTPException(status_code=400, detail=f"notes_at must be one of: {', '.join(NOTES_AT)}")
    return notes_at


def _export_opts(pdf=True, highlights=True, notes=True, folder_scope=None, author="", comments=False,
                 notes_at="beside", share=None) -> dict:
    """The switches as the builders read them; ``share`` the request's
    ShareScope (None for a member), which confines the export to what the
    share shows (``_Builder``)."""
    notes_at = _notes_place(notes_at)
    return {"pdf": bool(pdf), "highlights": bool(highlights), "notes": bool(notes), "comments": bool(comments),
            "notes_at": notes_at, "folder_scope": folder_scope, "author": author, "share": share}


def _page_ids(conn, page_id: str, sub_pages: bool) -> list[str]:
    """The pages a page's export covers: the page, and with ``sub_pages``
    every page inside it, each after its parent (``with_sub_pages``). A
    share that reaches the page reaches every page inside it
    (``ShareScope.allows_page``), so they need no check of their own."""
    return with_sub_pages(conn, [page_id]) if sub_pages else [page_id]


def page_builder(ws: str, block_id: str, mode: str, opts: dict, scope=None, sub_pages=False) -> _Builder:
    """One page walked through the mode's builder (named by the page's slug,
    ``builder.base``), with ``sub_pages`` the pages inside it too
    (``_page_ids``). ``scope`` is the request's share scope, None for a
    member. Raises HTTPException like the routes."""
    with connect_pages_db(ws) as conn:
        assert_block_in_scope(conn, block_id, scope)
        row = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
        if row is None or page_root_id(conn, block_id) is None:  # none, or in Recently deleted
            raise HTTPException(status_code=404, detail="page not found")
        return _run_export(conn, ws, mode, _page_ids(conn, block_id, sub_pages), slugify(row[0], block_id), opts)


def page_markdown(ws: str, page_id: str, *, highlights=True, notes=True) -> tuple[str, str]:
    """One page as readable Markdown, unbundled — upload references stay
    ``/api/uploads/…`` links: ``(markdown, file name)``. For callers that
    hand over the text itself (the MCP export); downloads bundle through
    ``_MarkdownBuilder``."""
    with connect_pages_db(ws) as conn:
        page = build_tree(fetch_subtree(conn, page_id), page_id) if page_root_id(conn, page_id) else None
        if page is None:
            raise HTTPException(status_code=404, detail="page not found")
        md = render_readable(page, highlights=highlights, notes=notes, resolve_ref=block_ref_resolver(conn),
                             folder=Filing(conn, None).folder(page_id, page["properties"]))
    return md, f"{slugify(page.get('content'), page_id)}.md"


def page_notes_pdf(ws: str, page_id: str, *, highlights=True, notes=True) -> tuple[bytes, str]:
    """The page's notes typeset as their own PDF document: ``(pdf bytes,
    file name)``."""
    builder = page_builder(ws, page_id, "notes-pdf", _export_opts(highlights=highlights, notes=notes))
    return builder.render(), f"{builder.base}{builder.suffix}"


def page_export_file(ws: str, page_id: str, mode: str, *, highlights=True, notes=True, author="",
                     sub_pages=False) -> tuple[bytes, str, str]:
    """One page in the export ``mode``, with ``sub_pages`` the pages inside
    it too, as its download holds it: ``(bytes, file name, media type)``.
    For callers that hand over the file itself (the MCP export)."""
    builder = page_builder(ws, page_id, mode, _export_opts(highlights=highlights, notes=notes, author=author),
                           sub_pages=sub_pages)
    tmp = tempfile.NamedTemporaryFile(suffix=builder.suffix, delete=False)
    tmp.close()
    try:
        name, media_type = builder.save(tmp.name)
        return Path(tmp.name).read_bytes(), name, media_type
    finally:
        os.unlink(tmp.name)


def page_gamma_zip(ws: str, page_id: str, dest, props: dict) -> None:
    """A page and the pages inside it as a Gamma export at ``dest``, the
    page's own properties patched by ``props`` first (a None value drops the
    key), and the folders and labels it carries those the pages are filed
    in once patched: what another workspace of this server merges in to
    take the page across (``linked_files.add_to_library``), where a folder
    the page leaves would be made, empty. Raises HTTPException like the
    routes."""
    builder = page_builder(ws, page_id, "gamma", _export_opts(), sub_pages=True)
    row = builder.db.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    patched = json.loads(row[0] or "{}")
    for key, value in props.items():
        if value is None:
            patched.pop(key, None)
        else:
            patched[key] = value
    builder.db.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?", (json.dumps(patched), page_id))
    builder.filed = {block_id for (raw,) in builder.db.execute(
        "SELECT properties FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(builder.page_ids),))
        for tree in TREES for block_id in filing(json.loads(raw or "{}"), tree)}
    builder.save(dest)


def annotated_pdf(ws: str, block_id: str, *, highlights=True, notes=False, comments=False, notes_at="beside",
                  author="", scope=None) -> tuple[bytes, str, int, int]:
    """The page's PDF with its highlights, ink, text boxes and pictures as
    standard annotations; with ``notes``, each highlight's notes as a card beside the
    pages or on them (``notes_at``), and with ``comments`` its open comment
    threads as cards beside the pages: ``(pdf bytes, file name, annotations
    written, cards drawn)``. All three off = the stored PDF as is. Raises
    HTTPException like the routes."""
    with connect_pages_db(ws) as conn:
        assert_block_in_scope(conn, block_id, scope)
        rows = fetch_subtree(conn, block_id) if page_root_id(conn, block_id) else []
        reach = _reach(conn, scope)
    if not rows:
        raise HTTPException(status_code=404, detail="page not found")
    return annotated_page_pdf(ws, [block_to_dict(r) for r in rows], block_id, highlights=highlights,
                              notes=notes, comments=comments, notes_at=notes_at, author=author, reach=reach)


def _translated(pdf_bytes: bytes, translation, doc_id: str) -> bytes:
    """The page's PDF with the translation the viewer showed written in place
    of the original text (gamma/pdf_translated.py). An HTTPException, which
    the annotated-PDF builder turns into the page's reason for being left
    out (an export of the page alone fails with it): 409 when the page
    shows another file than the one translated (its PDF was replaced
    meanwhile), 400 when the file cannot be written."""
    if translation.doc_id != doc_id:
        raise HTTPException(status_code=409, detail="the page's PDF changed after it was translated")
    try:
        return pdf_translated.write(pdf_bytes, [p.model_dump() for p in translation.pages], lang=translation.lang)
    except Exception as e:
        raise HTTPException(status_code=400,
                            detail=f"could not write the translation: {str(e) or type(e).__name__}") from e


def annotated_page_pdf(ws: str, blocks: list[dict], block_id: str, *, highlights=True, notes=False,
                       comments=False, notes_at="beside", author="", translation=None,
                       reach=None) -> tuple[bytes, str, int, int]:
    """``annotated_pdf`` of a page whose blocks (``block_to_dict``, the root
    among them) are already read — what a folder export feeds it.
    ``translation`` (an ``ExportTranslation``) is the translated view the
    viewer shows of the page's PDF, written into the file before its
    annotations (``_translated``). ``reach`` is what a share's [[refs]] may
    read (``_ref_resolver``)."""
    root = next(b for b in blocks if b["id"] == block_id)
    view = page_document(root["properties"])
    doc_id = pdf_file_hash(view)
    stored = storage.files(ws)
    page_sheets = [] if doc_id else notebook.sheets_of(blocks, block_id)
    if page_sheets:
        # A page without a PDF exports its sheets of paper: one PDF page
        # each, the paper painted, the text boxes typeset and the
        # handwriting drawn on it (gamma/notebook.py).
        sheets, pictures, drawn = [], [], 0
        for sheet in page_sheets:
            boxes = notebook.sheet_text_boxes(sheet["blocks"])
            inks = [ink for b in sheet["blocks"] if is_ink(b["properties"])
                    if (ink := inkmod.read_upload(stored, b["properties"]["ink_url"])) is not None]
            pictures.append(notebook.sheet_pictures(sheet["blocks"]))
            drawn += len(boxes) + len(inks) + len(pictures[-1])
            sheets.append((sheet["paper"], boxes, inks))
        with connect_pages_db(ws) as conn:
            pdf_bytes = notebook.notebook_pdf(sheets, resolve_ref=_ref_resolver(conn, reach), pictures=pictures,
                                              stored=stored)
        return pdf_bytes, f"{slugify(root.get('content'), block_id)}.pdf", drawn, 0
    if not doc_id:
        raise HTTPException(status_code=400, detail="page has no PDF")
    pdf_path = stored(stored_name(doc_id, ".pdf"))
    if not pdf_path:
        raise HTTPException(status_code=404, detail="PDF not stored on the server")

    marks = _collect_marks(blocks)
    boxes, replaced = _collect_text_boxes(blocks, block_id)
    pictures, more = _collect_pictures(blocks, block_id, stored)
    replaced |= more

    written = 0
    pdf_bytes = pdf_path.read_bytes()
    if translation is not None:
        pdf_bytes = _translated(pdf_bytes, translation, doc_id)
    # A text box or a picture is part of the annotation layer and it is the
    # user's own work on the page, so any switch writes it: the notes-only
    # PDF keeps them with the cards. All off: the stored file.
    cards = notes or comments
    if highlights or (cards and (boxes or pictures or replaced)):
        try:
            # [[refs]] in a box read as the text they name, as in the notes PDF.
            with connect_pages_db(ws) as conn:
                pdf_bytes, written = annotate_pdf(
                    pdf_bytes, marks if highlights else [], author=author,
                    ink=_collect_ink(blocks, stored) if highlights else (),
                    text_boxes=boxes, replaced=replaced, resolve_ref=_ref_resolver(conn, reach),
                    pictures=pictures, stored=stored)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"could not annotate PDF: {str(e) or type(e).__name__}") from e

    drawn = 0
    if cards:
        # Still positioned from the highlight rects, annotation layer or not;
        # [[refs]] read as the text they name, as in the notes PDF. The
        # threads are the open ones, as the viewer's margin shows them.
        try:
            with connect_pages_db(ws) as conn:
                threads = {}
                for thread in comment_threads(conn, [block_id], state="open") if comments else ():
                    threads.setdefault(thread["block_id"], []).append(thread["comments"])
                pdf_bytes, drawn = render_notes(
                    pdf_bytes, _collect_cards(blocks, notes=notes, threads=threads), notes_at=notes_at,
                    stored=stored, resolve_ref=_ref_resolver(conn, reach))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"could not render notes: {jobs.error_text(e)}")

    suffix = "-notes" if notes else "-comments" if comments else "-annotated" if highlights else ""
    return pdf_bytes, f"{slugify(root.get('content'), block_id)}{suffix}.pdf", written, drawn


# Sync on purpose: rendering + zipping runs in FastAPI's threadpool.
@router.get("/pages/{block_id}/export")
def export_page(block_id: str, request: Request, mode: str = "readable", pdf: int = 1,
                highlights: int = 1, notes: int = 1, comments: int = 0, notes_at: str = "beside",
                sub_pages: int = 0):
    """One page in any export format (see the _Builder classes): ``readable``
    Markdown (bare .md when it references no local assets, else a .zip with an
    assets/ folder; ``highlights=0``/``notes=0`` — the dialog's switches —
    leave out the quoted PDF text or your own writing), ``obsidian`` (an
    Obsidian vault zip: the page as ``<folder>/<Title>.md`` with wikilinks,
    the PDF and images under attachments/), ``notes-pdf`` (the
    notes typeset as their own PDF document — the one format a page without a
    PDF can still export as one), ``annotated-pdf`` (the page's PDF with its
    annotations, what /export-pdf answers, ``comments`` and ``notes_at``
    as there), ``logseq-graph`` (a complete
    Logseq file graph, both switches pinned on), ``zotero-rdf`` (a one-item
    Zotero RDF library), ``bibtex`` (the page's citation entry as a .bib
    file), ``notion`` (a zip for Notion's importer: the page as ``<Title>.md``
    with its images, always a zip), or ``gamma`` (a scoped account backup any
    Gamma imports via /api/import-data?mode=merge). ``sub_pages=1`` exports every page inside
    it too, laid out as a folder export lays out its pages: a format that
    writes one file for one page writes a zip for several, named for the
    page."""
    ws = resolve_ws(request)
    scope = share_scope(request)
    opts = _export_opts(pdf, highlights, notes, author=request.state.user or "", comments=comments,
                        notes_at=notes_at, share=scope)
    return page_builder(ws, block_id, mode, opts, scope, sub_pages=bool(sub_pages)).response()


# Sync on purpose: pypdf rewriting is CPU-bound; the threadpool keeps the loop free.
@router.get("/pages/{block_id}/export-pdf")
def export_page_pdf(block_id: str, request: Request, notes: int = 0, highlights: int = 1, comments: int = 0,
                    notes_at: str = "beside", sub_pages: int = 0):
    """The page's PDF with its highlights burned in as standard /Highlight
    annotations (notes become the annotation popup text), so they survive in
    any external PDF viewer. ``notes=1`` also prints every highlight's notes
    as a card (``gamma/pdf_notes.py``): in a column beside each page, level
    with the highlight (``notes_at=beside``), or in the page's own free
    space with a line back to it (``notes_at=page``). Readable without
    opening popups, and printable. ``comments=1`` prints each highlight's
    open comment threads as cards beside the page, as the viewer's margin
    shows them. ``highlights=0`` skips the annotation layer, so
    ``highlights=0&notes=1`` gives a clean PDF carrying only the written
    notes: the cards, and the text boxes and pictures already placed on the
    page. ``sub_pages=1`` on a page with pages inside it answers what
    ``/export?mode=annotated-pdf&sub_pages=1`` does (a zip of their PDFs,
    or the one PDF among them), without the counts."""
    ws = resolve_ws(request)
    scope = share_scope(request)
    if sub_pages:
        with connect_pages_db(ws) as conn:
            inside = pages_right_inside(conn, block_id)
        if inside:
            opts = _export_opts(highlights=highlights, notes=notes, author=request.state.user or "",
                                comments=comments, notes_at=notes_at, share=scope)
            return page_builder(ws, block_id, "annotated-pdf", opts, scope, sub_pages=True).response()
    pdf_bytes, filename, written, drawn = annotated_pdf(
        ws, block_id, highlights=bool(highlights), notes=bool(notes), comments=bool(comments),
        notes_at=_notes_place(notes_at), author=request.state.user or "", scope=scope)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": attachment_disposition(filename),
            "X-Annotations-Written": str(written),
            "X-Notes-Rendered": str(drawn),
        },
    )


def _export_folder(conn, folder_id: str, scope=None) -> list[str]:
    """The path (names from the top) of the folder ``folder_id`` an export
    is asked for: 403 when a share-scoped request (``scope``; a page share
    never reaches a folder, a folder share its own folder and the ones below
    it) reaches outside it, 404 when it is no folder."""
    if scope is not None and not scope.allows_folder(conn, folder_id):
        raise HTTPException(status_code=403, detail="not accessible via this share link")
    path = folder_path(conn, folder_id)
    if not path:
        raise HTTPException(status_code=404, detail="folder not found")
    return path


def _folder_base(path: list[str]) -> str:
    return slugify("-".join(path), "")


@router.get("/folders/{folder_id}/export")
def export_folder(folder_id: str, request: Request, mode: str = "readable", pdf: int = 1,
                  highlights: int = 1, notes: int = 1, comments: int = 0, notes_at: str = "beside"):
    """Every page filed in the folder ``folder_id`` (or a folder below it),
    in any export format (see the _Builder classes): ``readable`` (one .md
    per page +
    a shared assets/ folder), ``obsidian`` (a vault: subfolders as
    directories, attachments/), ``notes-pdf`` (every page's notes in one PDF
    document, each starting on a fresh sheet), ``annotated-pdf`` (each
    page's annotated PDF, the subfolders as directories), ``logseq-graph``
    (a complete Logseq file graph), ``zotero-rdf`` (a Zotero RDF library —
    subfolders become collections), ``bibtex`` (one .bib with every paper's
    citation entry), ``notion`` (a zip for Notion's importer: subfolders as
    directories) or ``gamma`` (a scoped account backup any Gamma imports
    via /api/import-data?mode=merge). The web app, a share view's too,
    exports through a job instead (``POST /api/jobs/export``); this is
    another Gamma's import by link and the scripts' download — with a
    folder share token, a ``mode=bibtex`` URL is the stable bibliography
    link a LaTeX editor refreshes from. 404 for an
    id that is no folder or a folder without pages, 403 for a folder outside
    the request's share."""
    scope = share_scope(request)
    ws = resolve_ws(request)
    opts = _export_opts(pdf, highlights, notes, folder_scope=folder_id, author=request.state.user or "",
                        comments=comments, notes_at=notes_at, share=scope)
    with connect_pages_db(ws) as conn:
        path = _export_folder(conn, folder_id, scope)
        ids = pages_in_folder(conn, folder_id)
        if not ids:
            raise HTTPException(status_code=404, detail="no pages in that folder")
        builder = _run_export(conn, ws, mode, ids, _folder_base(path), opts)
    return builder.response()


@router.get("/bibliography")
def bibliography_preview(request: Request, page_id: str = "", folder: str = "", sub_pages: int = 0):
    """The bibliography a ``mode=bibtex`` export would write, as data for the
    Export dialog to review before it downloads anything: ``entries`` (one
    per citable page — its page id and title, the citation key the file will
    use, whether that key is pinned on the page, and the entry itself),
    ``skipped`` (the pages left out, with the reason) and ``text`` (the file).
    Name a ``page_id`` (``sub_pages=1``: with the pages inside it) or a
    ``folder`` (a folder id). Same builder as the
    download, so the review is what the file will be; page properties are
    all it reads, so a whole library answers in one query per page."""
    scope = share_scope(request)
    ws = resolve_ws(request)
    if folder:
        opts = _export_opts(folder_scope=folder)
        with connect_pages_db(ws) as conn:
            path = _export_folder(conn, folder, scope)
            builder = _run_export(conn, ws, "bibtex", pages_in_folder(conn, folder), _folder_base(path), opts)
    elif page_id:
        builder = page_builder(ws, page_id, "bibtex", _export_opts(), scope, sub_pages=bool(sub_pages))
    else:
        raise HTTPException(status_code=400, detail="name a page or a folder")
    return builder.preview()


# The translated view the viewer shows of a page's PDF, for its annotated PDF
# (frontend/src/pdf/translateExport.js builds it, gamma/pdf_translated.py
# writes it). Boxes are [x0, y0, x1, y1] in the viewer's units: pdf.js's page
# at scale 1, the view box with /Rotate applied, top-left origin.
_Box = tuple[float, float, float, float]
# Over every page (``_translation_bounds``): a 500-page book translated
# whole, with room to spare.
TRANSLATION_BLOCKS_MAX = 100_000    # blocks
TRANSLATION_ITEMS_MAX = 1_000_000   # their line boxes and their parts, each
TRANSLATION_CHARS_MAX = 5_000_000   # characters of text
TRANSLATION_FORMULA_AREA_MAX = 20 * 612 * 792  # the formulas' boxes: twenty Letter pages of them


class _Finite(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class TranslatedFormula(_Finite):
    """A formula of the original inside a translation: the crop of page
    ``page`` at ``box``, set on the line at ``base`` (its baseline); its
    extracted ``text`` stands in where no crop can be drawn."""
    page: int = Field(ge=1)
    box: _Box
    base: float
    text: str = Field(default="", max_length=2_000)


class TranslatedBlock(_Finite):
    """One translated paragraph: the boxes of its original lines, its font
    size, and its translation as text and formulas (none: its lines go,
    nothing is written). ``box`` is the room a one-line block has past its
    line, ``foot`` where its text stops when the next block starts inside its
    box, ``centred``, ``justified``, ``bold``, ``italic`` and ``indent`` (of
    its first line) how the overlay sets it, and ``face`` the name of the
    font most of it is set in, which it is set in again when that font draws
    the translation."""
    lines: list[_Box] = Field(min_length=1, max_length=1_000)
    size: float = Field(gt=0, le=1_000)
    parts: list[Union[Annotated[str, Field(max_length=100_000)], TranslatedFormula]] = Field(
        default_factory=list, max_length=10_000)
    box: Union[_Box, None] = None
    foot: Union[float, None] = None
    centred: bool = False
    justified: bool = False
    bold: bool = False
    italic: bool = False
    indent: float = Field(default=0, ge=0, le=10_000)
    face: Union[str, None] = Field(default=None, max_length=256)


class TranslatedPage(_Finite):
    page: int = Field(ge=1)
    blocks: list[TranslatedBlock] = Field(max_length=10_000)


class ExportTranslation(_Finite):
    doc_id: str = Field(min_length=1, max_length=128)   # the stored file it was made on
    lang: str                                           # its language (TRANSLATE_LANGS)
    pages: list[TranslatedPage] = Field(min_length=1, max_length=pdf_text.MAX_PAGES)

    @field_validator("lang")
    @classmethod
    def _known_language(cls, lang):
        if lang not in TRANSLATE_LANGS:
            raise ValueError("unknown language")
        return lang


class PaperTranslation(ExportTranslation):
    """One paper's translation in a folder's annotated-PDF export, and the
    page whose PDF it is written into (frontend/src/pdf/folderTranslation.js
    lays it out in the browser, with no viewer)."""
    page_id: str = Field(min_length=1, max_length=128)


# The papers a folder's export may carry a translation for: the browser's
# memory of the papers it left translated holds 300 (translateRecall.RECALL_MAX).
TRANSLATED_PAPERS_MAX = 1_000
_PAPER_TRANSLATIONS = TypeAdapter(list[PaperTranslation])


def _translation_bounds(translations: list[ExportTranslation]) -> None:
    """413 for translations past the totals above, a folder's papers all
    together, in one sentence (a model validator's 422 would echo the whole
    translation back)."""
    blocks = [block for translation in translations for page in translation.pages for block in page.blocks]
    parts = [part for block in blocks for part in block.parts]
    formulas = [part for part in parts if not isinstance(part, str)]
    lines = sum(len(block.lines) for block in blocks)
    chars = sum(len(part) if isinstance(part, str) else len(part.text) for part in parts)
    area = sum(max(0.0, f.box[2] - f.box[0]) * max(0.0, f.box[3] - f.box[1]) for f in formulas)
    if (len(blocks) > TRANSLATION_BLOCKS_MAX or max(lines, len(parts)) > TRANSLATION_ITEMS_MAX
            or chars > TRANSLATION_CHARS_MAX or area > TRANSLATION_FORMULA_AREA_MAX):
        raise HTTPException(status_code=413, detail=(
            f"a translation holds at most {TRANSLATION_BLOCKS_MAX} blocks, {TRANSLATION_ITEMS_MAX} line boxes "
            f"and as many parts, {TRANSLATION_CHARS_MAX} characters, and formulas over twenty pages"))


class ExportJob(BaseModel):
    page_id: str = ""   # a page …
    folder: str = ""    # … or a folder id (its pages and its subfolders')
    mode: str = "readable"
    pdf: bool = True
    highlights: bool = True
    notes: bool = True
    comments: bool = False      # an annotated PDF's open comment threads beside its pages
    notes_at: str = "beside"    # where an annotated PDF's notes go (NOTES_AT)
    sub_pages: bool = False     # a page's: the pages inside it too (a folder's pages always bring theirs)
    # a page's annotated PDF: the translation the viewer shows of its PDF …
    translation: ExportTranslation | None = None
    # … a folder's: those of its papers this browser left translated …
    translations: list[PaperTranslation] = Field(default_factory=list, max_length=TRANSLATED_PAPERS_MAX)
    # … or the ones an earlier export of that page or folder kept (its job's
    # id): its retry
    translation_of: str = ""


# The exports a link visitor (no account) may start through a share in five
# minutes, per IP; a share's own limits are an account's (jobs.share_owner).
LINK_EXPORTS_PER_5_MIN = 30

# The layout a translated export was sent, kept with its job (jobs.kept) so
# Background tasks can start it again: a page's one translation, or the list
# of a folder's papers'.
KEPT_TRANSLATION = "translation.json.gz"
KEPT_TRANSLATIONS = "translations.json.gz"


def _kept_translations(request: Request, ws: str, page_id: str, folder: str, job_id: str) -> dict:
    """The translations an earlier annotated-PDF export of this page or
    folder was sent, kept with its job, by the page each goes into: one of
    this account's exports, in this workspace, of the same page or folder.
    404 once they are not (the job was removed, or ran before exports kept
    them)."""
    job = jobs.get(job_id)
    data = None
    if (job is not None and job["kind"] == "export" and job["owner"] == request.state.user_id
            and job["workspace"] == ws and (job["params"].get("page_id") or "") == page_id
            and (job["params"].get("folder") or "") == folder):
        data = jobs.kept(job_id, KEPT_TRANSLATIONS if folder else KEPT_TRANSLATION)
    try:
        if data is not None and folder:
            return {t.page_id: t for t in _PAPER_TRANSLATIONS.validate_json(gzip.decompress(data))}
        if data is not None:
            return {page_id: ExportTranslation.model_validate_json(gzip.decompress(data))}
    except (OSError, ValueError):  # a damaged file (pydantic's ValidationError is a ValueError)
        pass
    raise HTTPException(status_code=404, detail=(
        "that export's translation is no longer kept — export the folder again" if folder else
        "that export's translation is no longer kept — export the page again from the viewer"))


@router.post("/jobs/export")
def start_export_job(payload: ExportJob, request: Request):
    """A page or a folder in any export format (``mode``, as for the
    downloads above) as a background job whose file is the download (kind
    ``export``, docs/dev/tasks.md). Any member of the workspace may export.
    A folder's pages are read again when the job runs, so a queued export
    holds what the folder holds then, and so are the pages inside a page
    (``sub_pages``); its result counts the pages and lists the ones left
    out (an annotated-PDF export skips pages without a PDF). A page's
    annotated PDF may carry ``translation``, the translated view the viewer
    shows of the page's PDF, which is written in place of the original text
    of that page's PDF (not of the pages inside it); a folder's annotated
    PDF ``translations``, one per paper by its ``page_id``, each written into
    that page's PDF as the folder's export reaches it (one for a page the
    folder no longer holds is passed by). The params name the language as
    ``translation`` ("" without one); 400 for ``translation`` with a folder,
    ``translations`` without one, or either with another format; 413 past
    ``_translation_bounds`` (a folder's papers together). The job keeps
    them for as long as it is listed (``KEPT_TRANSLATION``,
    ``KEPT_TRANSLATIONS``), so ``translation_of``, that job's id in their
    place, starts the same export again (Background tasks' retry; 404 once
    they are gone, ``_kept_translations``). A page whose translation cannot
    be written is left out like any page whose annotated PDF fails
    (``_translated``).

    With ``?share=`` it is a share view's export: of what the share reaches
    (403 outside it), held to the share as its downloads are (``opts["share"]``),
    and the share's job (``auth.share_job_owner``), which any visitor the
    share admits starts and follows by its id, the downloads' proxy cut
    gone. It takes no translation (400); a link visitor starts at most
    ``LINK_EXPORTS_PER_5_MIN``. The run checks again that the share stands
    and still reaches the page or folder."""
    scope = share_scope(request)
    token = request.query_params.get("share") or ""
    if scope is None:
        user, ws, owner = require_user(request), require_ws(request), request.state.user_id
    else:
        owner, ws, user = share_job_owner(request), resolve_ws(request), request.state.user or ""
        link_ratelimit(request, "export", LINK_EXPORTS_PER_5_MIN, 300)
    mode = payload.mode
    if mode not in _BUILDERS:
        raise HTTPException(status_code=400, detail=f"unknown export mode: {mode}")
    folder, page_id = payload.folder, payload.page_id
    # page id → the translation written into its PDF
    translations = {page_id: payload.translation} if payload.translation is not None else {}
    translations.update((t.page_id, t) for t in payload.translations)
    if scope is not None and (translations or payload.translation_of):
        raise HTTPException(status_code=400, detail="a share view's export carries no translation")
    if translations or payload.translation_of:
        if mode != "annotated-pdf" or (payload.translation is not None and (folder or not page_id)) \
                or (payload.translations and not folder) or not (folder or page_id):
            raise HTTPException(status_code=400, detail=(
                "a translation goes only with a page's annotated PDF, and translations with a folder's"))
        if translations and payload.translation_of:
            raise HTTPException(status_code=400, detail="send a translation or name the export it came from, not both")
        if not translations:
            translations = _kept_translations(request, ws, "" if folder else page_id, folder, payload.translation_of)
        _translation_bounds(list(translations.values()))
    with connect_pages_db(ws) as conn:
        if folder:
            path = _export_folder(conn, folder, scope)
            if not pages_in_folder(conn, folder):
                raise HTTPException(status_code=404, detail="no pages in that folder")
            title, base = PATH_SEP.join(path), _folder_base(path)
        elif page_id:
            assert_block_in_scope(conn, page_id, scope)
            row = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
            if row is None or page_root_id(conn, page_id) is None:  # none, or in Recently deleted
                raise HTTPException(status_code=404, detail="page not found")
            title, base = (row[0] or "").strip() or "Untitled", slugify(row[0], page_id)
        else:
            raise HTTPException(status_code=400, detail="name a page or a folder to export")
    opts = _export_opts(payload.pdf, payload.highlights, payload.notes, folder_scope=folder or None, author=user,
                        comments=payload.comments, notes_at=payload.notes_at, share=scope)
    if translations:
        opts["translations"] = translations

    def run(job):
        if scope is not None and share_lookup(token) is None:
            raise HTTPException(status_code=410, detail="the share link was stopped")
        with connect_pages_db(ws) as conn:
            if scope is not None:  # moved out of the shared folder meanwhile, say
                if folder and not scope.allows_folder(conn, folder):
                    raise HTTPException(status_code=403, detail="not accessible via this share link")
                if not folder:
                    assert_block_in_scope(conn, page_id, scope)
            ids = pages_in_folder(conn, folder) if folder else _page_ids(conn, page_id, payload.sub_pages)
            if not ids:
                raise HTTPException(status_code=404, detail="the folder holds no pages any more")
            if not folder and page_root_id(conn, page_id) is None:
                raise HTTPException(status_code=404, detail="the page was deleted")
            builder = _run_export(conn, ws, mode, ids, base, opts, job.progress)
        name, media_type = builder.save(job.artifact_path, job.progress)
        job.set_artifact(name, media_type)
        return builder.summary()

    kept = None
    if translations and folder:
        kept = {KEPT_TRANSLATIONS: gzip.compress(_PAPER_TRANSLATIONS.dump_json(list(translations.values())), 6)}
    elif translations:
        kept = {KEPT_TRANSLATION: gzip.compress(translations[page_id].model_dump_json().encode(), 6)}
    # one language: every paper's is the settings' (translateRecall.openKey)
    lang = next(iter(translations.values())).lang if translations else ""
    return jobs.start("export", owner=owner, ws=ws, run=run, artifact=True, title=f"Export of {title}",
                      kept=kept, params={"page_id": page_id if not folder else "", "folder": folder, "name": title,
                              "mode": mode, "pdf": payload.pdf, "highlights": payload.highlights,
                              "notes": payload.notes, "comments": payload.comments,
                              "notes_at": payload.notes_at, "sub_pages": payload.sub_pages and not folder,
                              "translation": lang})
