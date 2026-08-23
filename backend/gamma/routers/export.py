"""Markdown export: a page (or a folder of pages) as .md, or .zip when the
page references uploaded assets (Notion-style: bare file vs. bundle decided by
whether there's anything to bundle)."""

import base64
import os
import re
import sqlite3
import tempfile
import zipfile
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from ..auth import resolve_user, share_scope_doc
from ..blocks_store import BLOCK_COLUMNS, assert_block_in_doc, block_to_dict, fetch_subtree
from ..db import pdf_upload_path, safe_doc_id, user_db_path, user_uploads_dir
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


def _content_disposition(filename: str) -> str:
    """attachment header carrying both an ASCII fallback and a UTF-8 name."""
    ascii_name = filename.encode("ascii", "ignore").decode() or "export"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _md_response(md: str, slug: str) -> Response:
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(f"{slug}.md")},
    )


def _zip_response(entries, assets, uploads_dir, download_name: str, files=(), blobs=()) -> FileResponse:
    """entries: list of (arcname, text). assets: set of upload filenames, written
    once under assets/ (deduped by content-addressed name). files: (arcname,
    disk path) pairs; blobs: (arcname, bytes) pairs."""
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    try:
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as z:
            for arcname, text in entries:
                z.writestr(arcname, text)
            for filename in sorted(assets):
                path = uploads_dir / filename
                if path.is_file():
                    z.write(path, f"assets/{filename}")
            for arcname, path in files:
                if path.is_file():
                    z.write(path, arcname)
            for arcname, data in blobs:
                z.writestr(arcname, data)
    except Exception:
        os.unlink(tmp.name)
        raise
    return FileResponse(
        tmp.name,
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(download_name)},
        background=BackgroundTask(os.unlink, tmp.name),
    )


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


def _collect_marks(blocks) -> list[dict]:
    """Highlight blocks → annotate_pdf marks (position/color/popup note).
    Skips annotations that came from the PDF itself and are STILL embedded in
    it (annot_stripped marks ones the import removed from the file), and link
    regions (Gamma navigation aids, not annotations)."""
    children_by_id: dict = {}
    for b in sorted(blocks, key=lambda b: b["position"] or ""):
        children_by_id.setdefault(b["parent_id"], []).append(b)

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


def _zotero_export_zip(conn, user, roots, base: str, folder_scope: str | None,
                       include_pdf: bool, highlights: bool, notes: bool,
                       progress: dict | None = None):
    """Pages → a Zotero RDF library zip: ``<base>/<base>.rdf`` plus
    ``<base>/files/<n>/...``. Highlights travel embedded inside the PDF copies
    (annotate_pdf — Zotero's "Include Annotations" convention), notes become
    bib:Memo items (pasted images embedded as data URIs), folder labels
    (confined to ``folder_scope`` when exporting a folder) the collection
    tree. Images referenced anywhere in the page also ride as item
    attachments; annotation comments carry a plain "(image: …)" placeholder
    since they can't hold pictures. ``progress`` (the /folders/export-progress
    dict) gets ``done``/``title`` updated per page."""
    uploads_dir = user_uploads_dir(user)
    resolve_image = _image_resolver(uploads_dir)
    items, blobs = [], []
    for n, root in enumerate(roots, 1):
        rows = fetch_subtree(conn, root["id"])
        page = build_tree(rows, root["id"])
        props = page.get("properties") or {}
        meta = props.get("meta") if isinstance(props.get("meta"), dict) else {}
        title = re.sub(r"\s+", " ", page.get("content") or "").strip() or "Untitled"
        if progress is not None:
            progress["title"] = title

        pdf_arc = None
        doc_id = props.get("doc_id")
        if include_pdf and doc_id:
            try:
                pdf_path = pdf_upload_path(user, doc_id)
            except ValueError:
                pdf_path = None
            if pdf_path and pdf_path.is_file():
                data = pdf_path.read_bytes()
                if highlights:
                    marks = _collect_marks([block_to_dict(r) for r in rows])
                    for m in marks:
                        m["note"] = strip_image_md(m["note"])
                    if marks:
                        try:
                            data, _ = annotate_pdf(data, marks, author=user)
                        except Exception as e:
                            log(f"zotero export: annotating '{title}' failed, exporting bare PDF: {e}")
                            data = pdf_path.read_bytes()
                pdf_arc = f"files/{n}/{slugify(title, '')}.pdf"
                blobs.append((f"{base}/{pdf_arc}", data))

        # Pasted images referenced anywhere in the page → item attachments
        # (dedup by content-hash filename; the bundle switch governs files).
        images = []
        if include_pdf:
            seen = set()
            for node in _walk_tree(page):
                for fname in MD_IMAGE_RE.findall(node.get("content") or ""):
                    if fname in seen or not (uploads_dir / fname).is_file():
                        continue
                    seen.add(fname)
                    arc = f"files/{n}/{fname}"
                    blobs.append((f"{base}/{arc}", (uploads_dir / fname).read_bytes()))
                    images.append({"path": arc, "title": fname,
                                   "mime": IMAGE_MIME.get(fname.rsplit(".", 1)[-1].lower())
                                           or "application/octet-stream"})

        note_htmls = []
        if notes:
            # Top-level non-highlight subtrees, one Zotero note each — the
            # inverse of the import's notes→child-blocks mapping. Writing
            # nested under highlights instead travels in the annotation popups.
            for child in page["children"]:
                cprops = child.get("properties") or {}
                if cprops.get("highlight_id") or cprops.get("link_url"):
                    continue
                html = note_html(child, resolve_image=resolve_image)
                if html:
                    note_htmls.append(html)
            # Writing nested under a highlight normally travels only in the
            # annotation popup — but that comment is plain text, so a
            # highlight whose notes carry images ALSO becomes a Zotero note
            # (page + quote header) with the pictures embedded.
            for node in _walk_tree(page):
                nprops = node.get("properties") or {}
                if not nprops.get("highlight_id"):
                    continue
                if not any(MD_IMAGE_RE.search(d.get("content") or "")
                           for d in _walk_tree(node)):
                    continue
                html = highlight_memo_html(node, resolve_image=resolve_image)
                if html:
                    note_htmls.append(html)

        folders = [p.strip() for p in (props.get("folder") or "").split(",") if p.strip()]
        if folder_scope:
            folders = [p for p in folders
                       if p == folder_scope or p.startswith(folder_scope + "/")]
        arxiv = (meta or {}).get("arxiv_id") or ""
        if progress is not None:
            progress["done"] += 1
        items.append({
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

    # Zotero's import wizard can't read a .zip (it reports "unsupported
    # format") — people try exactly that, so the how-to rides along.
    readme = (
        "Import into Zotero\n"
        "==================\n\n"
        f"1. Extract this zip somewhere (keep {base}.rdf and files/ together).\n"
        f"2. In Zotero: File -> Import... -> \"A file\" -> pick {base}.rdf.\n\n"
        "Do NOT pick the .zip itself - Zotero reports 'unsupported format' for it.\n"
        "Collections, tags, notes and PDFs (highlights embedded) come along.\n"
    )
    return [(f"{base}/{base}.rdf", build_rdf(items)),
            (f"{base}/README.txt", readme)], blobs


# Sync on purpose: rendering + zipping runs in FastAPI's threadpool.
@router.get("/pages/{block_id}/export")
def export_page(block_id: str, request: Request, mode: str = "readable", pdf: int = 1,
                highlights: int = 1, notes: int = 1):
    """One page → readable Markdown: bare .md when it references no local
    assets, else a .zip of the .md plus an assets/ folder. ``highlights=0`` /
    ``notes=0`` (the export dialog's switches) leave out the quoted PDF text or
    your own writing. ``mode=logseq-graph`` instead returns a complete Logseq
    file graph (pages/ + assets/ + logseq/config.edn, highlights as native
    hls__ page + EDN) — openable by file-based Logseq directly and convertible
    by the DB version's "File to DB graph" importer; a graph is defined by both
    layers, so the two switches don't apply to it. ``mode=zotero-rdf`` returns
    a one-item Zotero RDF library (see ``/folders/export``)."""
    user = resolve_user(request)
    scope = share_scope_doc(request)
    with sqlite3.connect(user_db_path(user, "pages.db")) as conn:
        assert_block_in_doc(conn, block_id, scope)
        rows = fetch_subtree(conn, block_id)
    if not rows:
        raise HTTPException(status_code=404, detail="page not found")

    page = build_tree(rows, block_id)
    slug = slugify(page.get("content"), block_id)

    if mode == "zotero-rdf":
        with sqlite3.connect(user_db_path(user, "pages.db")) as conn:
            entries, blobs = _zotero_export_zip(
                conn, user, [{"id": block_id}], slug, None,
                bool(pdf), bool(highlights), bool(notes))
        return _zip_response(entries, set(), user_uploads_dir(user),
                             f"{slug}-zotero.zip", blobs=blobs)

    if mode == "logseq-graph":
        entries, files, blobs, assets = _graph_page_parts(page, user_uploads_dir(user), bool(pdf))
        entries.append(("logseq/config.edn", CONFIG_EDN))
        return _zip_response(entries, assets, user_uploads_dir(user),
                             f"{slug}-logseq.zip", files, blobs)

    md, assets = collect_and_rewrite(
        render_readable(page, highlights=bool(highlights), notes=bool(notes)),
        include_pdf=bool(pdf))
    if not assets:
        return _md_response(md, slug)
    return _zip_response([(f"{slug}.md", md)], assets, user_uploads_dir(user), f"{slug}.zip")


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
    user = resolve_user(request)
    scope = share_scope_doc(request)
    with sqlite3.connect(user_db_path(user, "pages.db")) as conn:
        assert_block_in_doc(conn, block_id, scope)
        rows = fetch_subtree(conn, block_id)
    if not rows:
        raise HTTPException(status_code=404, detail="page not found")
    blocks = [block_to_dict(r) for r in rows]
    root = next(b for b in blocks if b["id"] == block_id)
    doc_id = root["properties"].get("doc_id")
    if not doc_id:
        raise HTTPException(status_code=400, detail="page has no PDF")
    try:
        pdf_path = pdf_upload_path(user, doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid document id")
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="PDF not stored on the server")

    marks = _collect_marks(blocks)

    written = 0
    pdf_bytes = pdf_path.read_bytes()
    if highlights:
        try:
            pdf_bytes, written = annotate_pdf(pdf_bytes, marks, author=user)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"could not annotate PDF: {e}")

    drawn = 0
    if notes:
        # Still positioned from the highlight rects, annotation layer or not.
        try:
            pdf_bytes, drawn = render_notes(pdf_bytes, marks,
                                            uploads_dir=user_uploads_dir(user))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"could not render notes: {e}")

    slug = slugify(root.get("content"), block_id)
    suffix = "-notes" if notes else "-annotated" if highlights else ""
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": _content_disposition(f"{slug}{suffix}.pdf"),
            "X-Annotations-Written": str(written),
            "X-Notes-Rendered": str(drawn),
        },
    )


# Per-user progress of a running /folders/export, for the frontend's percent
# display (same in-memory pattern as auth.py's backup _export_progress).
_folder_export_progress: dict[str, dict] = {}


@router.get("/folders/export-progress")
def folder_export_progress(request: Request):
    if share_scope_doc(request) is not None:
        raise HTTPException(status_code=403, detail="not accessible via this share link")
    user = resolve_user(request)
    return _folder_export_progress.get(user) or {"active": False, "total": 0, "done": 0}


def _page_in_folder(props: dict, name: str) -> bool:
    raw = props.get("folder") or ""
    for path in (p.strip() for p in raw.split(",")):
        if path and (path == name or path.startswith(name + "/")):
            return True
    return False


@router.get("/folders/export")
def export_folder(request: Request, name: str, mode: str = "readable", pdf: int = 1,
                  highlights: int = 1, notes: int = 1):
    """Every page tagged into folder ``name`` (or a subfolder of it) → a single
    .zip: one .md per page at the root, a shared assets/ folder (deduped).
    ``mode=zotero-rdf`` instead builds a Zotero RDF library (metadata + PDFs
    with highlights embedded + notes; subfolders become collections) —
    importable by Zotero itself and by Gamma's own Zotero import."""
    name = (name or "").strip().strip("/")
    if not name:
        raise HTTPException(status_code=400, detail="folder name required")
    # A share link is scoped to one document, never a whole folder.
    if share_scope_doc(request) is not None:
        raise HTTPException(status_code=403, detail="not accessible via this share link")
    user = resolve_user(request)
    folder_slug = slugify(name.replace("/", "-"), "")
    with sqlite3.connect(user_db_path(user, "pages.db")) as conn:
        roots = conn.execute(
            f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE parent_id = 'root'"
        ).fetchall()
        matches = [block_to_dict(r) for r in roots]
        matches = [b for b in matches if _page_in_folder(b["properties"], name)]
        if not matches:
            raise HTTPException(status_code=404, detail="no pages in that folder")

        prog = {"active": True, "total": len(matches), "done": 0, "title": ""}
        _folder_export_progress[user] = prog
        try:
            if mode == "zotero-rdf":
                entries, blobs = _zotero_export_zip(
                    conn, user, matches, folder_slug, name,
                    bool(pdf), bool(highlights), bool(notes), progress=prog)
                return _zip_response(entries, set(), user_uploads_dir(user),
                                     f"{folder_slug}-zotero.zip", blobs=blobs)

            entries, assets, used = [], set(), set()
            files, blobs = [], []
            for root in matches:
                rows = fetch_subtree(conn, root["id"])
                page = build_tree(rows, root["id"])
                prog["title"] = (page.get("content") or "").strip()
                if mode == "logseq-graph":
                    p_entries, p_files, p_blobs, p_assets = _graph_page_parts(
                        page, user_uploads_dir(user), bool(pdf))
                    entries += p_entries
                    files += p_files
                    blobs += p_blobs
                    assets |= p_assets
                    prog["done"] += 1
                    continue
                md, page_assets = collect_and_rewrite(
                    render_readable(page, highlights=bool(highlights), notes=bool(notes)),
                    include_pdf=bool(pdf))
                assets |= page_assets
                slug = slugify(page.get("content"), root["id"])
                arcname = f"{slug}.md"
                # id suffix makes collisions near-impossible, but guard anyway.
                while arcname in used:
                    arcname = f"{slug}-{len(used)}.md"
                used.add(arcname)
                entries.append((arcname, md))
                prog["done"] += 1
        finally:
            prog["active"] = False

    if mode == "logseq-graph":
        entries.append(("logseq/config.edn", CONFIG_EDN))
        return _zip_response(entries, assets, user_uploads_dir(user),
                             f"{folder_slug}-logseq.zip", files, blobs)
    return _zip_response(entries, assets, user_uploads_dir(user), f"{folder_slug}.zip")
