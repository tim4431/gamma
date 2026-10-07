"""Exporting pages: one driver (``_run_export``) walks the selected page
subtrees and feeds them to the format's ``_Builder`` — Markdown, an Obsidian
vault, a Logseq graph, a Zotero RDF library, a scoped Gamma backup, a BibTeX
bibliography, the notes typeset as a PDF document, or the annotated PDFs
themselves. Most builders produce a zip; a bare .md (nothing to bundle), the
.bib, the notes PDF and one page's annotated PDF are single files. The same
builders serve the downloads
(``/pages/{id}/export``, ``/folders/{id}/export``: the share view, scripts)
and the background job the web app starts (``POST /api/jobs/export``)."""

import base64
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from starlette.background import BackgroundTask

from .. import bibtex as bibtex_mod
from .. import ink as inkmod
from .. import jobs, notebook
from ..auth import require_user, require_ws, resolve_ws, share_scope
from ..blocks_store import (
    BLOCK_COLUMNS, FOLDERS, LABELS, PATH_SEP, STORED_COLUMNS, TREES, assert_block_in_scope, block_to_dict,
    fetch_subtree, filing, folder_path, folder_paths, folder_subtree_ids, label_names, page_root_id, pages_in_folder,
    tree_rows)
from ..db import connect_pages_db, ws_uploads_dir
from ..db import (
    PAGES_SCHEMA,
    copy_chats,
    page_now,
    register_functions,
    safe_doc_id,
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
from ..highlights import is_highlight
from ..storage import attachment_disposition, upload_refs
from ..text_box import box_page, is_text_box, normalize_text_box
from ..obsidian_export import (APP_JSON, VaultContext, page_dir, referenced_blocks, render_vault_page, unique_name,
                               vault_name)
from ..pdf_document import render_document
from ..pdf_export import annotate_pdf, highlight_note_text, still_embedded
from ..pdf_notes import render_notes
from ..zotero_export import (
    IMAGE_MIME,
    MD_IMAGE_RE,
    build_rdf,
    highlight_memo_html,
    note_html,
    strip_image_md,
)

router = APIRouter(prefix="/api", tags=["export"])


def _write_zip(dest, entries, assets, uploads_dir, files=(), blobs=(), progress=jobs.no_progress) -> None:
    """entries: list of (arcname, text). assets: set of upload filenames, written
    once under assets/ (deduped by content-addressed name). files: (arcname,
    disk path) pairs; blobs: (arcname, bytes) pairs. ``progress`` hears the
    files packed so far (the phase "packing")."""
    stored = [(f"assets/{name}", uploads_dir / name) for name in sorted(assets)] + list(files)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for arcname, text in entries:
            z.writestr(arcname, text)
        for n, (arcname, path) in enumerate(stored):
            progress(phase="packing", done=n, total=len(stored), unit="files")
            if path.is_file():
                z.write(path, arcname)
        for arcname, data in blobs:
            z.writestr(arcname, data)
    progress(phase="packing", done=len(stored), total=len(stored), unit="files")


def _graph_page_parts(page, uploads_dir, include_pdf):
    """One page in Logseq file-graph layout → (text entries, disk files,
    blobs, image-asset names). The PDF is renamed sha → page stem so the
    ``hls__<stem>`` page / ``<stem>.edn`` / ``<stem>.pdf`` naming convention
    Logseq's annotation system keys on actually holds. Spaces are replaced so
    inline ``![](../assets/<stem>.pdf)`` links stay valid Markdown."""
    stem = slugify(page.get("content"), page["id"]).replace(" ", "_")
    doc_id = (page.get("properties") or {}).get("doc_id")
    pdf_path = None
    if doc_id:
        try:
            pdf_path = uploads_dir / f"{safe_doc_id(doc_id)}.pdf"
        except ValueError:
            pdf_path = None
    has_pdf = bool(include_pdf and pdf_path and pdf_path.is_file())

    md, assets = collect_and_rewrite(
        render_graph_page_md(page, stem, has_pdf), include_pdf=False, prefix="../assets/"
    )
    entries = [(f"pages/{stem}.md", md)]
    files, blobs = [], []
    if has_pdf:
        highlights = collect_highlights(page)
        entries.append((f"pages/hls__{stem}.md", render_hls_md(stem, highlights)))
        entries.append((f"assets/{stem}.edn", render_edn(highlights)))
        files.append((f"assets/{stem}.pdf", pdf_path))
        blobs.extend(render_area_images(pdf_path, stem, highlights))
    return entries, files, blobs, assets


def _children_by_id(blocks) -> dict:
    """``{parent_id: [children in sibling order]}`` — what highlight_note_text
    walks for an annotation's nested notes."""
    children_by_id: dict = {}
    for b in sorted(blocks, key=lambda b: b["position"] or ""):
        children_by_id.setdefault(b["parent_id"], []).append(b)
    return children_by_id


def _collect_marks(blocks) -> list[dict]:
    """Highlight blocks (``kind`` ``highlight``: not the link regions,
    Gamma navigation aids, nor ink) → annotate_pdf marks (position/color/popup
    note). Skips annotations that came from the PDF itself and are STILL
    embedded in it (``still_embedded``)."""
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


def _collect_ink(blocks, uploads_dir) -> list[dict]:
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
        ink_file = inkmod.read_upload(uploads_dir, url)
        if not ink_file:
            continue
        groups.append({"ink": ink_file, "note": highlight_note_text(b, children_by_id), "id": b["id"]})
    return groups


def _collect_text_boxes(blocks, page_id) -> tuple[list[dict], set]:
    """Text boxes on the PDF's pages → ``annotate_pdf``'s text boxes (the
    normalized box, the text, the page, the block id, when it last
    changed) and its ``replaced`` keys. A box under a sheet is on the sheet
    whatever its ``pdf_page`` says, and an empty one draws nothing. Unlike
    the other marks, a box that came from the PDF and is still embedded in
    it is written too, and its original leaves the copy (``replaced``, the
    box's ``imported_annot``): the page shows the box as Gamma has it, so an
    edit made here reaches the export."""
    on_sheets = {b["id"] for sheet in notebook.sheets_of(blocks, page_id) for b in sheet["blocks"]}
    boxes, replaced = [], set()
    for b in blocks:
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


# Pasted images above this size stay attachments only — a data URI this big
# would bloat the note beyond what Zotero's editor handles gracefully.
_EMBED_IMAGE_CAP = 4_000_000


def _walk_tree(node):
    yield node
    for child in node.get("children") or []:
        yield from _walk_tree(child)


def _image_resolver(uploads_dir):
    """``resolve_image`` for note_html: upload filename → (mime, base64)."""
    def resolve(filename):
        mime = IMAGE_MIME.get(filename.rsplit(".", 1)[-1].lower())
        path = uploads_dir / filename
        if not mime or not path.is_file() or path.stat().st_size > _EMBED_IMAGE_CAP:
            return None
        return mime, base64.b64encode(path.read_bytes()).decode("ascii")
    return resolve


# --- format builders --------------------------------------------------------
# One export = one builder. The driver (_run_export) walks the selected pages
# exactly once — subtree fetch, tree build, progress report — and feeds each
# page to the mode's builder; the builder accumulates zip parts and names the
# download, and ``save`` writes it. Adding an export format = adding a
# builder here; the endpoints, the job and the zip writer stay untouched.

class _Filing:
    """The names an export writes for its pages' folders and labels, read
    from the trees once. ``scope`` is the exported folder's id (None: a page
    exported on its own): a page's folder paths are then the names below it,
    and its folders outside it are left out; ``title`` is the exported
    folder's path as people read it ("" for none)."""

    def __init__(self, conn, scope):
        paths = folder_paths(conn)
        self.title = ""
        if scope:
            top = paths.get(scope, [])
            self.title = PATH_SEP.join(top)
            inside = folder_subtree_ids(conn, scope)
            paths = {f: path[len(top):] for f, path in paths.items() if f in inside}
        self.paths = paths
        self.labels = label_names(conn)

    def folders(self, props) -> list[list[str]]:
        """The page's folder paths (names) below the exported folder — that
        folder itself, the export's top, is none —, in the page's order."""
        return [self.paths[f] for f in filing(props, FOLDERS) if self.paths.get(f)]

    def folder(self, props) -> list[str]:
        """The path a page's file goes under: its first folder below the
        exported folder ([]: the export's top)."""
        return next(iter(self.folders(props)), [])

    def tags(self, props) -> list[str]:
        """The names of the page's labels, in its order."""
        return [self.labels[i] for i in filing(props, LABELS) if i in self.labels]


class _Builder:
    """opts: {"pdf": bool, "highlights": bool, "notes": bool,
    "folder_scope": the exported folder's id | None (None: one page is
    exported), "author": the account exporting}. ``filing`` (``_Filing``)
    names the pages' folders and labels once ``begin`` has run."""
    suffix = ".zip"  # appended to the base slug for the download name
    roots_only = False  # True: the driver hands over the page's own row, not its subtree

    def __init__(self, ws, base: str, opts: dict):
        self.ws = ws
        self.base = base
        self.opts = opts
        self.uploads_dir = ws_uploads_dir(ws)
        self.entries, self.assets = [], set()
        self.files, self.blobs = [], []
        self.walked, self.skipped = 0, []  # pages the driver fed in; {title, reason} left out
        self._spool = None
        self.filing = None

    def begin(self, conn, root_ids):
        """Sees the whole export set before any page is walked (the DB
        connection is only open during the walk, not in ``save``)."""
        self.filing = _Filing(conn, self.opts.get("folder_scope"))

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
        self.files.append((arcname, path))

    def discard(self) -> None:
        """Remove what ``spool`` wrote (``save`` does it once the file is out)."""
        if self._spool is not None:
            shutil.rmtree(self._spool, ignore_errors=True)
            self._spool = None

    def save(self, dest, progress=jobs.no_progress) -> tuple[str, str]:
        """Write the export to ``dest``: ``(download name, media type)``."""
        try:
            self.finish()
            _write_zip(dest, self.entries, self.assets, self.uploads_dir, self.files, self.blobs, progress)
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
            if not name.endswith(".svg"):
                continue
            source = self.uploads_dir / (name[:-4] + ".ink")
            arc = f"{prefix}{name}"
            if not source.is_file() or (self.uploads_dir / name).is_file() \
                    or any(b[0] == arc for b in self.blobs):
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
    export is linked by relative filename, so the zip is self-contained."""

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.used = set()
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
            slug = slugify(row[0], rid)
            arcname = f"{slug}.md"
            # id suffix makes collisions near-impossible, but guard anyway.
            while arcname in self.used:
                arcname = f"{slug}-{len(self.used)}.md"
            self.used.add(arcname)
            self.filenames[rid] = arcname
        self.resolve_ref = block_ref_resolver(conn)

    def add_page(self, n, rows, page):
        md, page_assets = collect_and_rewrite(
            render_readable(page, highlights=self.opts["highlights"], notes=self.opts["notes"],
                            resolve_ref=self.resolve_ref, page_file=self.filenames.get,
                            folder=self.filing.folder(page["properties"])),
            include_pdf=self.opts["pdf"])
        self.assets |= page_assets
        self.render_ink_svgs(page_assets)
        arcname = self.filenames.get(page["id"]) \
            or f"{slugify(page.get('content'), page['id'])}.md"
        self.entries.append((arcname, md))

    def save(self, dest, progress=jobs.no_progress):
        # One page (no folder) referencing no local files is just its .md.
        if self.opts.get("folder_scope") is None and not self.assets and len(self.entries) == 1:
            Path(dest).write_bytes(self.entries[0][1].encode("utf-8"))
            self.discard()
            return f"{self.base}.md", "text/markdown; charset=utf-8"
        return super().save(dest, progress)


class _ObsidianBuilder(_Builder):
    """An Obsidian vault (``obsidian_export``): ``<dir>/<Title>.md`` per page
    (directories = the folders below the exported folder, labels as tags),
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
        self.ctx = VaultContext(block_ref_resolver(conn), include_pdf=self.opts["pdf"])
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
            pages.append((rid, row[0] or "", self.filing.folder(props)))
        self.ctx.name_pages(pages)
        # Every block that something links to needs its ^anchor written, and
        # a page may be rendered before the page that links into it — so the
        # link-bearing blocks are scanned up front (links from outside the
        # export only add harmless anchors).
        texts = (r[0] for r in conn.execute(
            "SELECT content FROM unified_blocks WHERE content LIKE '%[[%'"))
        referenced_blocks(texts, self.ctx)
        if self.opts["pdf"]:
            for rid, title, _ in pages:
                self._bundle_pdf(conn, rid, title)

    def _bundle_pdf(self, conn, rid, title):
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (rid,)).fetchone()
        try:
            doc_id = json.loads(row[0] or "{}").get("doc_id") if row else None
            path = self.uploads_dir / f"{safe_doc_id(doc_id)}.pdf" if doc_id else None
        except (TypeError, ValueError):
            path = None
        if path and path.is_file():
            arcname = self.ctx.name_pdf(rid, title, doc_id)
            if all(f[0] != arcname for f in self.files):
                self.files.append((arcname, path))

    def add_page(self, n, rows, page):
        md, page_assets = collect_and_rewrite(
            render_vault_page(page, self.ctx, tags=self.filing.tags(page["properties"]),
                              highlights=self.opts["highlights"], notes=self.opts["notes"]),
            include_pdf=self.opts["pdf"], prefix="attachments/")
        self.assets |= page_assets
        self.entries.append((self.ctx.page_file[page["id"]], md))

    def finish(self):
        # Attachments live in attachments/, not the readable export's assets/.
        self.files += [(f"attachments/{name}", self.uploads_dir / name)
                       for name in sorted(self.assets)]
        self.assets = set()
        self.entries.append((".obsidian/app.json", APP_JSON))


class _LogseqBuilder(_Builder):
    """A Logseq file graph: pages/ + assets/ + logseq/config.edn, highlights
    as native hls__ pages + EDN (see _graph_page_parts)."""
    suffix = "-logseq.zip"

    def add_page(self, n, rows, page):
        p_entries, p_files, p_blobs, p_assets = _graph_page_parts(
            page, self.uploads_dir, self.opts["pdf"])
        self.entries += p_entries
        self.files += p_files
        self.blobs += p_blobs
        self.assets |= p_assets

    def finish(self):
        self.entries.append(("logseq/config.edn", CONFIG_EDN))


class _ZoteroBuilder(_Builder):
    """A Zotero RDF library: ``<base>/<base>.rdf`` + ``<base>/files/<n>/…``.
    Highlights travel embedded inside the PDF copies (annotate_pdf — Zotero's
    "Include Annotations" convention), notes become bib:Memo items with pasted
    images embedded as data URIs, the folders below the exported folder the
    collection tree, labels tags. Images referenced anywhere in a page also
    ride as item attachments; annotation comments carry a plain "(image: …)"
    placeholder since they can't hold pictures."""
    suffix = "-zotero.zip"

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.items = []
        self.resolve_image = _image_resolver(self.uploads_dir)

    def add_page(self, n, rows, page):
        props = page.get("properties") or {}
        meta = props.get("meta") if isinstance(props.get("meta"), dict) else {}
        title = re.sub(r"\s+", " ", page.get("content") or "").strip() or "Untitled"
        include_pdf = self.opts["pdf"]

        pdf_arc = None
        doc_id = props.get("doc_id")
        if include_pdf and doc_id:
            try:
                pdf_path = self.uploads_dir / f"{safe_doc_id(doc_id)}.pdf"
            except ValueError:
                pdf_path = None
            if pdf_path and pdf_path.is_file():
                data = pdf_path.read_bytes()
                if self.opts["highlights"]:
                    marks = _collect_marks([block_to_dict(r) for r in rows])
                    for m in marks:
                        m["note"] = strip_image_md(m["note"])
                    if marks:
                        try:
                            data, _ = annotate_pdf(data, marks, author=self.ws)
                        except Exception as e:
                            log(f"zotero export: annotating '{title}' failed, exporting bare PDF: {e}")
                            data = pdf_path.read_bytes()
                pdf_arc = f"files/{n}/{slugify(title, '')}.pdf"
                self.spool(f"{self.base}/{pdf_arc}", data)

        images = []
        if include_pdf:
            seen = set()
            for node in _walk_tree(page):
                for fname in MD_IMAGE_RE.findall(node.get("content") or ""):
                    if fname in seen or not (self.uploads_dir / fname).is_file():
                        continue
                    seen.add(fname)
                    arc = f"files/{n}/{fname}"
                    self.files.append((f"{self.base}/{arc}", self.uploads_dir / fname))
                    images.append({"path": arc, "title": fname,
                                   "mime": IMAGE_MIME.get(fname.rsplit(".", 1)[-1].lower())
                                           or "application/octet-stream"})

        note_htmls = []
        if self.opts["notes"]:
            # Top-level non-highlight subtrees, one Zotero note each — the
            # inverse of the import's notes→child-blocks mapping. Writing
            # nested under highlights instead travels in the annotation popups.
            for child in page["children"]:
                cprops = child.get("properties") or {}
                if is_highlight(cprops) or cprops.get("link_url"):
                    continue
                html = note_html(child, resolve_image=self.resolve_image)
                if html:
                    note_htmls.append(html)
            # Popup comments are plain text, so a highlight whose notes carry
            # images ALSO becomes a Zotero note (page + quote header) with the
            # pictures embedded.
            for node in _walk_tree(page):
                nprops = node.get("properties") or {}
                if not is_highlight(nprops):
                    continue
                if not any(MD_IMAGE_RE.search(d.get("content") or "")
                           for d in _walk_tree(node)):
                    continue
                html = highlight_memo_html(node, resolve_image=self.resolve_image)
                if html:
                    note_htmls.append(html)

        arxiv = (meta or {}).get("arxiv_id") or ""
        self.items.append({
            # Real Zotero keys are "#item_<n>" — a distinct prefix for generated
            # ones so a re-exported import can't collide with a fresh page.
            "key": props.get("zotero_key")
                   or (f"https://arxiv.org/abs/{arxiv}" if arxiv else f"#gamma_item_{n}"),
            "title": title,
            "meta": meta or {},
            "cite_key": bibtex_mod.pinned_key(props),
            "tags": self.filing.tags(props),
            "folders": self.filing.folders(props),
            "pdf_path": pdf_arc,
            "images": images,
            "notes": note_htmls,
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
            "Collections, tags, notes and PDFs (highlights embedded) come along.\n"
        )
        self.entries += [(f"{self.base}/{self.base}.rdf", build_rdf(self.items)),
                         (f"{self.base}/README.txt", readme)]


class _GammaBuilder(_Builder):
    """A scoped account backup in the ``gamma-backup-1`` layout (/api/export's
    format): a pages.db holding just the selected page subtrees verbatim
    with their AI chats, each bucket's active conversation and its history,
    and the folder and label blocks they are filed under (every folder a
    page is in with the folders above it, every label it carries) — plus,
    on a folder export, the exported folder's whole subtree (empty
    subfolders too) with the folders above it and the chats of the folder
    views in it —, and uploads/ with just the files they reference — no
    data.db, which holds nothing that is not rebuilt. Any Gamma imports it
    through the existing ``/api/import-data?mode=merge`` — additive, deduped
    by block id / doc id / conversation / content hash, its folders mapped
    onto the workspace's at the same path (``ws_backup._merge_trees``), so
    re-importing adds nothing. Lossless by construction, which is why the
    dialog's three switches don't apply to this format."""
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
        self.db.execute(f"INSERT OR IGNORE INTO unified_blocks ({STORED_COLUMNS}) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tuple(row))

    def add_page(self, n, rows, page):
        self.page_ids.append(page["id"])
        self.filed.update(*(filing(page["properties"], tree) for tree in TREES))
        for row in rows:
            self._put(row)
            # Referenced uploads: any /api/uploads/<file> in content or
            # properties (source_url, pasted images), plus a doc_id's PDF —
            # the one reference rule (storage.upload_refs) the orphan
            # bookkeeping keeps files by.
            self.upload_names |= upload_refs(row[3] or "", row[4] or "{}")

    def finish(self):
        scope = self.opts.get("folder_scope")
        with connect_pages_db(self.ws) as src:
            subtree = folder_subtree_ids(src, scope) if scope else set()
            parents = {r[0]: r[1] for tree in TREES for r in tree_rows(src, tree)}
            blocks = set(subtree)
            for block_id in self.filed | ({scope} if scope else set()):
                while block_id in parents:  # the block and the folders above it
                    blocks.add(block_id)
                    block_id = parents[block_id]
            for row in src.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks "
                                   "WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(sorted(blocks)),)):
                self._put(row)
            # the pages' chats, and on a folder export its folder views'
            copy_chats(src, self.db, "bucket IN (SELECT value FROM json_each(?))",
                       [json.dumps(self.page_ids + sorted(subtree))])
        self.db.commit()
        pages_bytes = self.db.serialize()
        self.db.close()

        self.blobs.append(("pages.db", pages_bytes))
        self.blobs.append(("manifest.json", json.dumps({
            "format": "gamma-backup-1",  # what import-data validates
            "scope": {"folder": scope, "pages": len(self.page_ids)},
            "exported_at": page_now(),
        }, indent=2)))
        self.files += [(f"uploads/{name}", self.uploads_dir / name)
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
                    self.pages, uploads_dir=self.uploads_dir,
                    highlights=self.opts["highlights"], notes=self.opts["notes"],
                    resolve_ref=block_ref_resolver(conn))
        except Exception as e:
            log(f"notes PDF export failed for '{self.base}': {e}")
            raise HTTPException(status_code=400, detail=f"could not build the PDF: {e}")

    def save(self, dest, progress=jobs.no_progress):
        progress(phase="typesetting")
        Path(dest).write_bytes(self.render())
        return f"{self.base}{self.suffix}", "application/pdf"


class _AnnotatedPdfBuilder(_Builder):
    """Each page's PDF with its highlights (and handwriting) as standard
    annotations and, with the notes switch, its notes printed on the page
    (``annotated_page_pdf``: what /pages/{id}/export-pdf downloads). One
    page: that PDF itself. A folder: a zip of them, ``<dir>/<Title>.pdf``,
    the directories mirroring the folders below the exported folder
    (``obsidian_export.page_dir``). A page with sheets of paper and no PDF
    is exported as its sheets; a page with neither is left out, and the
    result lists it."""
    suffix = "-annotated.zip"

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.used = set()
        self.single_name = ""

    def add_page(self, n, rows, page):
        try:
            data, filename, _, _ = annotated_page_pdf(
                self.ws, [block_to_dict(r) for r in rows], page["id"], highlights=self.opts["highlights"],
                notes=self.opts["notes"], author=self.opts.get("author", ""))
        except HTTPException as e:
            self.skip(page, str(e.detail))
            return
        self.single_name = filename
        self.spool(unique_name(self.used, page_dir(self.filing.folder(page["properties"])),
                               vault_name(page.get("content") or ""), ".pdf"), data)

    def save(self, dest, progress=jobs.no_progress):
        if not self.files:
            reasons = {s["reason"] for s in self.skipped}
            raise HTTPException(status_code=400, detail=next(iter(reasons)) if len(reasons) == 1 else
                                "none of these pages has a PDF stored on the server")
        if self.opts.get("folder_scope") is None:  # one page: the PDF itself
            try:
                shutil.copyfile(self.files[0][1], dest)
            finally:
                self.discard()
            return self.single_name, "application/pdf"
        return super().save(dest, progress)


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
                    "none of these pages has paper metadata to cite" if self.opts.get("folder_scope")
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


def _export_opts(pdf=True, highlights=True, notes=True, folder_scope=None, author="") -> dict:
    return {"pdf": bool(pdf), "highlights": bool(highlights), "notes": bool(notes),
            "folder_scope": folder_scope, "author": author}


def page_builder(ws: str, block_id: str, mode: str, opts: dict, scope=None) -> _Builder:
    """One page walked through the mode's builder (named by the page's slug,
    ``builder.base``). ``scope`` is the request's share scope, None for a
    member. Raises HTTPException like the routes."""
    with connect_pages_db(ws) as conn:
        assert_block_in_scope(conn, block_id, scope)
        row = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
        if row is None or page_root_id(conn, block_id) is None:  # none, or in Recently deleted
            raise HTTPException(status_code=404, detail="page not found")
        return _run_export(conn, ws, mode, [block_id], slugify(row[0], block_id), opts)


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
                             folder=_Filing(conn, None).folder(page["properties"]))
    return md, f"{slugify(page.get('content'), page_id)}.md"


def page_notes_pdf(ws: str, page_id: str, *, highlights=True, notes=True) -> tuple[bytes, str]:
    """The page's notes typeset as their own PDF document: ``(pdf bytes,
    file name)``."""
    builder = page_builder(ws, page_id, "notes-pdf", _export_opts(highlights=highlights, notes=notes))
    return builder.render(), f"{builder.base}{builder.suffix}"


def annotated_pdf(ws: str, block_id: str, *, highlights=True, notes=False, author="",
                  scope=None) -> tuple[bytes, str, int, int]:
    """The page's PDF with its highlights, ink and text boxes as standard
    annotations and, with ``notes``, its notes painted on the pages: ``(pdf
    bytes, file name, annotations written, notes drawn)``. Both off = the
    stored PDF as is. Raises HTTPException like the routes."""
    with connect_pages_db(ws) as conn:
        assert_block_in_scope(conn, block_id, scope)
        rows = fetch_subtree(conn, block_id) if page_root_id(conn, block_id) else []
    if not rows:
        raise HTTPException(status_code=404, detail="page not found")
    return annotated_page_pdf(ws, [block_to_dict(r) for r in rows], block_id,
                              highlights=highlights, notes=notes, author=author)


def annotated_page_pdf(ws: str, blocks: list[dict], block_id: str, *, highlights=True, notes=False,
                       author="") -> tuple[bytes, str, int, int]:
    """``annotated_pdf`` of a page whose blocks (``block_to_dict``, the root
    among them) are already read — what a folder export feeds it."""
    root = next(b for b in blocks if b["id"] == block_id)
    doc_id = root["properties"].get("doc_id")
    page_sheets = [] if doc_id else notebook.sheets_of(blocks, block_id)
    if page_sheets:
        # A page without a PDF exports its sheets of paper: one PDF page
        # each, the paper painted, the text boxes typeset and the
        # handwriting drawn on it (gamma/notebook.py).
        uploads = ws_uploads_dir(ws)
        sheets, drawn = [], 0
        for sheet in page_sheets:
            boxes = notebook.sheet_text_boxes(sheet["blocks"])
            inks = [ink for b in sheet["blocks"] if (b["properties"] or {}).get("ink_url")
                    if (ink := inkmod.read_upload(uploads, b["properties"]["ink_url"])) is not None]
            drawn += len(boxes) + len(inks)
            sheets.append((sheet["paper"], boxes, inks))
        with connect_pages_db(ws) as conn:
            pdf_bytes = notebook.notebook_pdf(sheets, resolve_ref=block_ref_resolver(conn))
        return pdf_bytes, f"{slugify(root.get('content'), block_id)}.pdf", drawn, 0
    if not doc_id:
        raise HTTPException(status_code=400, detail="page has no PDF")
    try:
        pdf_path = ws_uploads_dir(ws) / f"{safe_doc_id(doc_id)}.pdf"
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid document id")
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="PDF not stored on the server")

    marks = _collect_marks(blocks)
    boxes, replaced = _collect_text_boxes(blocks, block_id)

    written = 0
    pdf_bytes = pdf_path.read_bytes()
    # A text box is part of the annotation layer and it is the user's own
    # writing on the page, so either switch writes it: the notes-only PDF
    # keeps the boxes with the painted notes. Both off: the stored file.
    if highlights or (notes and (boxes or replaced)):
        try:
            # [[refs]] in a box read as the text they name, as in the notes PDF.
            with connect_pages_db(ws) as conn:
                pdf_bytes, written = annotate_pdf(
                    pdf_bytes, marks if highlights else [], author=author,
                    ink=_collect_ink(blocks, ws_uploads_dir(ws)) if highlights else (),
                    text_boxes=boxes, replaced=replaced, resolve_ref=block_ref_resolver(conn))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"could not annotate PDF: {str(e) or type(e).__name__}") from e

    drawn = 0
    if notes:
        # Still positioned from the highlight rects, annotation layer or not.
        try:
            pdf_bytes, drawn = render_notes(pdf_bytes, marks,
                                            uploads_dir=ws_uploads_dir(ws))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"could not render notes: {e}")

    suffix = "-notes" if notes else "-annotated" if highlights else ""
    return pdf_bytes, f"{slugify(root.get('content'), block_id)}{suffix}.pdf", written, drawn


# Sync on purpose: rendering + zipping runs in FastAPI's threadpool.
@router.get("/pages/{block_id}/export")
def export_page(block_id: str, request: Request, mode: str = "readable", pdf: int = 1,
                highlights: int = 1, notes: int = 1):
    """One page in any export format (see the _Builder classes): ``readable``
    Markdown (bare .md when it references no local assets, else a .zip with an
    assets/ folder; ``highlights=0``/``notes=0`` — the dialog's switches —
    leave out the quoted PDF text or your own writing), ``obsidian`` (an
    Obsidian vault zip: the page as ``<folder>/<Title>.md`` with wikilinks,
    the PDF and images under attachments/), ``notes-pdf`` (the
    notes typeset as their own PDF document — the one format a page without a
    PDF can still export as one), ``annotated-pdf`` (the page's PDF with its
    annotations, what /export-pdf answers), ``logseq-graph`` (a complete
    Logseq file graph, both switches pinned on), ``zotero-rdf`` (a one-item
    Zotero RDF library), ``bibtex`` (the page's citation entry as a .bib
    file), or ``gamma`` (a scoped account backup any Gamma imports via
    /api/import-data?mode=merge)."""
    ws = resolve_ws(request)
    opts = _export_opts(pdf, highlights, notes, author=request.state.user or "")
    return page_builder(ws, block_id, mode, opts, share_scope(request)).response()


# Sync on purpose: PyPDF2 rewriting is CPU-bound; the threadpool keeps the loop free.
@router.get("/pages/{block_id}/export-pdf")
def export_page_pdf(block_id: str, request: Request, notes: int = 0, highlights: int = 1):
    """The page's PDF with its highlights burned in as standard /Highlight
    annotations (notes become the annotation popup text), so they survive in
    any external PDF viewer. ``notes=1`` additionally paints every non-empty
    note onto the page itself, in the nearest free space with a leader line
    back to its highlight — readable without opening popups, and printable.
    ``highlights=0`` skips the annotation layer, so ``highlights=0&notes=1``
    gives a clean PDF carrying only the written notes: the notes painted
    and the text boxes, the writing already placed on the page."""
    ws = resolve_ws(request)
    pdf_bytes, filename, written, drawn = annotated_pdf(
        ws, block_id, highlights=bool(highlights), notes=bool(notes),
        author=request.state.user or "", scope=share_scope(request))
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
                  highlights: int = 1, notes: int = 1):
    """Every page filed in the folder ``folder_id`` (or a folder below it),
    in any export format (see the _Builder classes): ``readable`` (one .md
    per page +
    a shared assets/ folder), ``obsidian`` (a vault: subfolders as
    directories, attachments/), ``notes-pdf`` (every page's notes in one PDF
    document, each starting on a fresh sheet), ``annotated-pdf`` (each
    page's annotated PDF, the subfolders as directories), ``logseq-graph``
    (a complete Logseq file graph), ``zotero-rdf`` (a Zotero RDF library —
    subfolders become collections), ``bibtex`` (one .bib with every paper's
    citation entry) or ``gamma`` (a scoped account backup any Gamma imports
    via /api/import-data?mode=merge). The web app exports through a job
    instead (``POST /api/jobs/export``); this is the share view's and the
    scripts' download — with a folder share token, a ``mode=bibtex`` URL is
    the stable bibliography link a LaTeX editor refreshes from. 404 for an
    id that is no folder or a folder without pages, 403 for a folder outside
    the request's share."""
    scope = share_scope(request)
    ws = resolve_ws(request)
    opts = _export_opts(pdf, highlights, notes, folder_scope=folder_id, author=request.state.user or "")
    with connect_pages_db(ws) as conn:
        path = _export_folder(conn, folder_id, scope)
        ids = pages_in_folder(conn, folder_id)
        if not ids:
            raise HTTPException(status_code=404, detail="no pages in that folder")
        builder = _run_export(conn, ws, mode, ids, _folder_base(path), opts)
    return builder.response()


@router.get("/bibliography")
def bibliography_preview(request: Request, page_id: str = "", folder: str = ""):
    """The bibliography a ``mode=bibtex`` export would write, as data for the
    Export dialog to review before it downloads anything: ``entries`` (one
    per citable page — its page id and title, the citation key the file will
    use, whether that key is pinned on the page, and the entry itself),
    ``skipped`` (the pages left out, with the reason) and ``text`` (the file).
    Name a ``page_id`` or a ``folder`` (a folder id). Same builder as the
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
        builder = page_builder(ws, page_id, "bibtex", _export_opts(), scope)
    else:
        raise HTTPException(status_code=400, detail="name a page or a folder")
    return builder.preview()


class ExportJob(BaseModel):
    page_id: str = ""   # a page …
    folder: str = ""    # … or a folder id (its pages and its subfolders')
    mode: str = "readable"
    pdf: bool = True
    highlights: bool = True
    notes: bool = True


@router.post("/jobs/export")
def start_export_job(payload: ExportJob, request: Request):
    """A page or a folder in any export format (``mode``, as for the
    downloads above) as a background job whose file is the download (kind
    ``export``, docs/dev/tasks.md). Any member of the workspace may export.
    A folder's pages are read again when the job runs, so a queued export
    holds what the folder holds then; its result counts the pages and lists
    the ones left out (an annotated-PDF export skips pages without a PDF)."""
    user = require_user(request)
    ws = require_ws(request)
    mode = payload.mode
    if mode not in _BUILDERS:
        raise HTTPException(status_code=400, detail=f"unknown export mode: {mode}")
    folder, page_id = payload.folder, payload.page_id
    with connect_pages_db(ws) as conn:
        if folder:
            path = _export_folder(conn, folder)
            if not pages_in_folder(conn, folder):
                raise HTTPException(status_code=404, detail="no pages in that folder")
            title, base = PATH_SEP.join(path), _folder_base(path)
        elif page_id:
            row = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
            if row is None or page_root_id(conn, page_id) is None:  # none, or in Recently deleted
                raise HTTPException(status_code=404, detail="page not found")
            title, base = (row[0] or "").strip() or "Untitled", slugify(row[0], page_id)
        else:
            raise HTTPException(status_code=400, detail="name a page or a folder to export")
    opts = _export_opts(payload.pdf, payload.highlights, payload.notes, folder_scope=folder or None, author=user)

    def run(job):
        with connect_pages_db(ws) as conn:
            ids = pages_in_folder(conn, folder) if folder else [page_id]
            if not ids:
                raise HTTPException(status_code=404, detail="the folder holds no pages any more")
            if not folder and page_root_id(conn, page_id) is None:
                raise HTTPException(status_code=404, detail="the page was deleted")
            builder = _run_export(conn, ws, mode, ids, base, opts, job.progress)
        name, media_type = builder.save(job.artifact_path, job.progress)
        job.set_artifact(name, media_type)
        return builder.summary()

    return jobs.start("export", owner=request.state.user_id, ws=ws, run=run, artifact=True, title=f"Export of {title}",
                      params={"page_id": page_id if not folder else "", "folder": folder, "name": title,
                              "mode": mode, "pdf": payload.pdf, "highlights": payload.highlights,
                              "notes": payload.notes})
