"""Exporting pages: one driver (``_run_export``) walks the selected page
subtrees and feeds them to the format's ``_Builder`` — Markdown, an Obsidian
vault, a Logseq graph, a Zotero RDF library, a scoped Gamma backup, the notes
typeset as a PDF document, or the annotated PDFs themselves. Most builders
produce a zip; a bare .md (nothing to bundle), the notes PDF and one page's
annotated PDF are single files. The same builders serve the downloads
(``/pages/{id}/export``, ``/folders/export``: the share view, scripts) and
the background job the web app starts (``POST /api/jobs/export``)."""

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

from .. import ink as inkmod
from .. import jobs, notebook
from ..auth import require_user, require_ws, resolve_ws, share_scope
from ..blocks_store import (
    BLOCK_COLUMNS, TRASH, assert_block_in_scope, block_to_dict, fetch_subtree, page_root_id)
from ..db import connect_pages_db
from ..db import (
    PAGES_SCHEMA,
    connect_data_db,
    page_now,
    pdf_upload_path,
    safe_doc_id,
    ws_uploads_dir,
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
    build_tree,
    collect_and_rewrite,
    render_readable,
    slugify,
)
from ..logbuf import log
from ..storage import attachment_disposition, upload_refs
from ..obsidian_export import APP_JSON, VaultContext, page_dir, referenced_blocks, render_vault_page, vault_name
from ..pdf_document import render_document
from ..pdf_export import annotate_pdf, highlight_note_text
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
    """Highlight blocks → annotate_pdf marks (position/color/popup note).
    Skips annotations that came from the PDF itself and are STILL embedded in
    it (annot_stripped marks ones the import removed from the file), and link
    regions (Gamma navigation aids, not annotations)."""
    children_by_id = _children_by_id(blocks)
    marks = []
    for b in blocks:
        props = b["properties"]
        if not props.get("highlight_id") or not props.get("pdf_position"):
            continue
        if props.get("imported_annot") and not props.get("annot_stripped"):
            continue
        if props.get("link_url") or props.get("link_page_id"):
            continue
        marks.append({
            "position": props["pdf_position"],
            "color": props.get("color"),
            "note": highlight_note_text(b, children_by_id),
            # For /Square annotations: the deterministic /NM key Zotero
            # requires before it will import an area annotation.
            "id": props["highlight_id"],
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
        if not url or (props.get("imported_annot") and not props.get("annot_stripped")):
            continue
        ink_file = inkmod.read_upload(uploads_dir, url)
        if not ink_file:
            continue
        groups.append({"ink": ink_file, "note": highlight_note_text(b, children_by_id), "id": b["id"]})
    return groups


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

class _Builder:
    """opts: {"pdf": bool, "highlights": bool, "notes": bool,
    "folder_scope": path | None (None: one page is exported), "author": the
    account exporting}."""
    suffix = ".zip"  # appended to the base slug for the download name

    def __init__(self, ws, base: str, opts: dict):
        self.ws = ws
        self.base = base
        self.opts = opts
        self.uploads_dir = ws_uploads_dir(ws)
        self.entries, self.assets = [], set()
        self.files, self.blobs = [], []
        self.walked, self.skipped = 0, []  # pages the driver fed in; {title, reason} left out
        self._spool = None

    def begin(self, conn, root_ids):
        """Sees the whole export set before any page is walked (the DB
        connection is only open during the walk, not in ``save``)."""

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
        """Leave a page out of the export, saying why (the job's result lists it)."""
        self.skipped.append({"title": (page.get("content") or "").strip() or "Untitled", "reason": reason})

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
        self.resolve_ref = _block_ref_resolver(conn)

    def add_page(self, n, rows, page):
        md, page_assets = collect_and_rewrite(
            render_readable(page, highlights=self.opts["highlights"], notes=self.opts["notes"],
                            resolve_ref=self.resolve_ref, page_file=self.filenames.get,
                            folder_scope=self.opts.get("folder_scope")),
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
    (directories = folder labels relative to the exported folder),
    ``attachments/`` with the images and — with the bundle switch — the PDFs
    named after their page, wikilinks / ``^id`` anchors resolved against the
    export set, and an ``.obsidian/app.json`` that marks the folder as a
    vault (the importer reads it back as one)."""
    suffix = "-obsidian.zip"

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.ctx = None

    def begin(self, conn, root_ids):
        self.ctx = VaultContext(_block_ref_resolver(conn), include_pdf=self.opts["pdf"])
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
            pages.append((rid, row[0] or "", props.get("folder") or ""))
        self.ctx.name_pages(pages, self.opts.get("folder_scope"))
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
            path = pdf_upload_path(self.ws, doc_id) if doc_id else None
        except (TypeError, ValueError):
            path = None
        if path and path.is_file():
            arcname = self.ctx.name_pdf(rid, title, doc_id)
            if all(f[0] != arcname for f in self.files):
                self.files.append((arcname, path))

    def add_page(self, n, rows, page):
        md, page_assets = collect_and_rewrite(
            render_vault_page(page, self.ctx, highlights=self.opts["highlights"],
                              notes=self.opts["notes"]),
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
    images embedded as data URIs, folder labels (confined to the exported
    folder) the collection tree. Images referenced anywhere in a page also
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
                pdf_path = pdf_upload_path(self.ws, doc_id)
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
                if cprops.get("highlight_id") or cprops.get("link_url"):
                    continue
                html = note_html(child, resolve_image=self.resolve_image)
                if html:
                    note_htmls.append(html)
            # Popup comments are plain text, so a highlight whose notes carry
            # images ALSO becomes a Zotero note (page + quote header) with the
            # pictures embedded.
            for node in _walk_tree(page):
                nprops = node.get("properties") or {}
                if not nprops.get("highlight_id"):
                    continue
                if not any(MD_IMAGE_RE.search(d.get("content") or "")
                           for d in _walk_tree(node)):
                    continue
                html = highlight_memo_html(node, resolve_image=self.resolve_image)
                if html:
                    note_htmls.append(html)

        folders = [p.strip() for p in (props.get("folder") or "").split(",") if p.strip()]
        scope = self.opts.get("folder_scope")
        if scope:
            folders = [p for p in folders if p == scope or p.startswith(scope + "/")]
        arxiv = (meta or {}).get("arxiv_id") or ""
        self.items.append({
            # Real Zotero keys are "#item_<n>" — a distinct prefix for generated
            # ones so a re-exported import can't collide with a fresh page.
            "key": props.get("zotero_key")
                   or (f"https://arxiv.org/abs/{arxiv}" if arxiv else f"#gamma_item_{n}"),
            "title": title,
            "meta": meta or {},
            "tags": [t.strip() for t in (props.get("category") or "").split(",") if t.strip()],
            "folders": folders,
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
    format): a pages.db holding just the selected page subtrees verbatim, a
    data.db with their AI chats (plus, on a folder export, the folder view's
    own chat buckets), and uploads/ with just the files they reference. Any
    Gamma imports it through the existing ``/api/import-data?mode=merge`` —
    additive, deduped by block id / doc id / content hash, so re-importing
    adds nothing. Lossless by construction, which is why the dialog's three
    switches don't apply to this format."""
    suffix = "-gamma.zip"

    def __init__(self, ws, base, opts):
        super().__init__(ws, base, opts)
        self.db = sqlite3.connect(":memory:")
        for stmt in PAGES_SCHEMA:
            self.db.execute(stmt)
        self.page_ids = []
        self.upload_names = set()

    def add_page(self, n, rows, page):
        self.page_ids.append(page["id"])
        for row in rows:
            # A page can sit in several exported folders only once — roots are
            # distinct — but keep the guard for shared subtrees.
            self.db.execute(
                f"INSERT OR IGNORE INTO unified_blocks ({BLOCK_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", tuple(row))
            # Referenced uploads: any /api/uploads/<file> in content or
            # properties (source_url, pasted images), plus a doc_id's PDF —
            # the one reference rule (storage.upload_refs) the orphan
            # bookkeeping keeps files by.
            self.upload_names |= upload_refs(row[3] or "", row[4] or "{}")

    def finish(self):
        self.db.commit()
        pages_bytes = self.db.serialize()
        self.db.close()

        chat_keys = list(self.page_ids)
        scope = self.opts.get("folder_scope")
        data_bytes = None
        with connect_data_db(self.ws) as src:
            marks = ",".join("?" for _ in chat_keys)
            rows = src.execute(
                f"SELECT block_id, messages, updated_at FROM chats WHERE block_id IN ({marks})",
                chat_keys).fetchall() if chat_keys else []
            if scope:
                rows += src.execute(
                    "SELECT block_id, messages, updated_at FROM chats "
                    "WHERE block_id = ? OR substr(block_id, 1, ?) = ?",
                    (f"home:{scope}", len(f"home:{scope}/"), f"home:{scope}/")).fetchall()
        if rows:
            out = sqlite3.connect(":memory:")
            out.execute("CREATE TABLE chats (block_id TEXT PRIMARY KEY, "
                        "messages TEXT NOT NULL, updated_at TEXT NOT NULL)")
            out.executemany("INSERT OR IGNORE INTO chats VALUES (?, ?, ?)", rows)
            out.commit()
            data_bytes = out.serialize()
            out.close()

        self.blobs.append(("pages.db", pages_bytes))
        if data_bytes:
            self.blobs.append(("data.db", data_bytes))
        self.blobs.append(("manifest.json", json.dumps({
            "format": "gamma-backup-1",  # what import-data validates
            "scope": {"folder": scope, "pages": len(self.page_ids)},
            "exported_at": page_now(),
        }, indent=2)))
        self.files += [(f"uploads/{name}", self.uploads_dir / name)
                       for name in sorted(self.upload_names)]


def _block_ref_resolver(conn):
    """id → {content, page_title, page_id} for [[refs]], ``![[embeds]]`` and
    internal document links — walks the parent chain for the root page, with a
    per-render cache (the same ref often appears many times). A block in
    Recently deleted resolves to nothing, like a deleted one."""
    cache = {}

    def resolve(block_id):
        if block_id in cache:
            return cache[block_id]
        row = conn.execute(
            "SELECT content, parent_id FROM unified_blocks WHERE id = ?",
            (block_id,)).fetchone()
        result = None
        if row is not None:
            content, parent = row
            page_id, title = block_id, ""
            for _ in range(64):                  # parent chain → the page block
                if not parent or parent in ("root", TRASH):
                    break
                up = conn.execute(
                    "SELECT content, parent_id FROM unified_blocks WHERE id = ?",
                    (parent,)).fetchone()
                if up is None:
                    break
                page_id, title, parent = parent, (up[0] or ""), up[1]
            if page_id == block_id:              # the ref IS a page block
                title = content or ""
            if parent != TRASH:
                result = {"content": content or "", "page_title": title.strip(),
                          "page_id": page_id}
        cache[block_id] = result
        return result

    return resolve


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
                    resolve_ref=_block_ref_resolver(conn))
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
    the directories mirroring the folder labels below the exported folder
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
        directory = page_dir((page.get("properties") or {}).get("folder"), self.opts.get("folder_scope"))
        stem = vault_name(page.get("content") or "") or "Untitled"
        name, count = stem, 1
        while f"{directory}{name}.pdf".lower() in self.used:
            count += 1
            name = f"{stem} {count}"
        self.used.add(f"{directory}{name}.pdf".lower())
        self.single_name = filename
        self.spool(f"{directory}{name}.pdf", data)

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


_BUILDERS = {
    "readable": _MarkdownBuilder,
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
        md = render_readable(page, highlights=highlights, notes=notes,
                             resolve_ref=_block_ref_resolver(conn))
    return md, f"{slugify(page.get('content'), page_id)}.md"


def page_notes_pdf(ws: str, page_id: str, *, highlights=True, notes=True) -> tuple[bytes, str]:
    """The page's notes typeset as their own PDF document: ``(pdf bytes,
    file name)``."""
    builder = page_builder(ws, page_id, "notes-pdf", _export_opts(highlights=highlights, notes=notes))
    return builder.render(), f"{builder.base}{builder.suffix}"


def annotated_pdf(ws: str, block_id: str, *, highlights=True, notes=False, author="",
                  scope=None) -> tuple[bytes, str, int, int]:
    """The page's PDF with its highlights (and ink) as standard annotations
    and, with ``notes``, its notes painted on the pages: ``(pdf bytes, file
    name, annotations written, notes drawn)``. Both off = the stored PDF as
    is. Raises HTTPException like the routes."""
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
        # each, the paper painted and the handwriting drawn on it
        # (gamma/notebook.py).
        uploads = ws_uploads_dir(ws)
        sheets, groups = [], 0
        for sheet in page_sheets:
            inks = [ink for b in sheet["blocks"] if (b["properties"] or {}).get("ink_url")
                    if (ink := inkmod.read_upload(uploads, b["properties"]["ink_url"])) is not None]
            groups += len(inks)
            sheets.append((sheet["paper"], inks))
        return notebook.notebook_pdf(sheets), f"{slugify(root.get('content'), block_id)}.pdf", groups, 0
    if not doc_id:
        raise HTTPException(status_code=400, detail="page has no PDF")
    try:
        pdf_path = pdf_upload_path(ws, doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid document id")
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="PDF not stored on the server")

    marks = _collect_marks(blocks)

    written = 0
    pdf_bytes = pdf_path.read_bytes()
    if highlights:
        try:
            pdf_bytes, written = annotate_pdf(pdf_bytes, marks, author=author,
                                              ink=_collect_ink(blocks, ws_uploads_dir(ws)))
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
    Zotero RDF library), or ``gamma`` (a scoped account backup any Gamma
    imports via /api/import-data?mode=merge)."""
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
    gives a clean PDF carrying only the written notes."""
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


def _page_in_folder(props: dict, name: str) -> bool:
    raw = props.get("folder") or ""
    for path in (p.strip() for p in raw.split(",")):
        if path and (path == name or path.startswith(name + "/")):
            return True
    return False


def _folder_name(name: str) -> str:
    name = (name or "").strip().strip("/")
    if not name:
        raise HTTPException(status_code=400, detail="folder name required")
    return name


def _folder_pages(conn, name: str) -> list[str]:
    """The ids of the pages filed in folder ``name`` or below it."""
    roots = conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE parent_id = 'root'").fetchall()
    return [b["id"] for b in (block_to_dict(r) for r in roots) if _page_in_folder(b["properties"], name)]


def _folder_base(name: str) -> str:
    return slugify(name.replace("/", "-"), "")


@router.get("/folders/export")
def export_folder(request: Request, name: str, mode: str = "readable", pdf: int = 1,
                  highlights: int = 1, notes: int = 1):
    """Every page tagged into folder ``name`` (or a subfolder of it), in any
    export format (see the _Builder classes): ``readable`` (one .md per page +
    a shared assets/ folder), ``obsidian`` (a vault: subfolders as
    directories, attachments/), ``notes-pdf`` (every page's notes in one PDF
    document, each starting on a fresh sheet), ``annotated-pdf`` (each
    page's annotated PDF, the subfolders as directories), ``logseq-graph``
    (a complete Logseq file graph), ``zotero-rdf`` (a Zotero RDF library —
    subfolders become collections), or ``gamma`` (a scoped account backup
    any Gamma imports via /api/import-data?mode=merge). The web app exports
    through a job instead (``POST /api/jobs/export``); this is the share
    view's and the scripts' download."""
    name = _folder_name(name)
    # A page share never reaches a whole folder; a folder share exports its
    # own folder or a subfolder of it.
    scope = share_scope(request)
    if scope is not None and not scope.allows_folder(name):
        raise HTTPException(status_code=403, detail="not accessible via this share link")
    ws = resolve_ws(request)
    opts = _export_opts(pdf, highlights, notes, folder_scope=name, author=request.state.user or "")
    with connect_pages_db(ws) as conn:
        ids = _folder_pages(conn, name)
        if not ids:
            raise HTTPException(status_code=404, detail="no pages in that folder")
        builder = _run_export(conn, ws, mode, ids, _folder_base(name), opts)
    return builder.response()


class ExportJob(BaseModel):
    page_id: str = ""   # a page …
    folder: str = ""    # … or a folder label path (its pages and its subfolders')
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
    folder = _folder_name(payload.folder) if payload.folder else ""
    page_id = payload.page_id
    with connect_pages_db(ws) as conn:
        if folder:
            if not _folder_pages(conn, folder):
                raise HTTPException(status_code=404, detail="no pages in that folder")
            title, base = folder, _folder_base(folder)
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
            ids = _folder_pages(conn, folder) if folder else [page_id]
            if not ids:
                raise HTTPException(status_code=404, detail="the folder holds no pages any more")
            if not folder and page_root_id(conn, page_id) is None:
                raise HTTPException(status_code=404, detail="the page was deleted")
            builder = _run_export(conn, ws, mode, ids, base, opts, job.progress)
        name, media_type = builder.save(job.artifact_path, job.progress)
        job.set_artifact(name, media_type)
        return builder.summary()

    return jobs.start("export", owner=user, ws=ws, run=run, artifact=True, title=f"Export of {title}",
                      params={"page_id": page_id if not folder else "", "folder": folder, "name": title,
                              "mode": mode, "pdf": payload.pdf, "highlights": payload.highlights,
                              "notes": payload.notes})
