"""Imports: Logseq PDF-highlight exports (PDF + EDN + optional MD), annotations
embedded in the PDF itself (e.g. saved by SumatraPDF/Acrobat/Zotero), Markdown
notes, and whole libraries — a Zotero RDF export, a zip of Markdown notes,
Notability notes, GoodNotes notebooks, a Gamma export — through the reviewed
import: the upload
is staged and previewed (``/import/review``), then the chosen items are
imported by a background job (``POST /api/jobs/import``, gamma/jobs.py)."""

import copy
import hashlib
import html
import io
import json
import math
import os
import re
import shutil
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from fractional_indexing import generate_n_keys_between

from .. import bibtex as bibtex_mod
from .. import import_staging, jobs, picture, upload_gc, upload_parts
from ..auth import actor_of, require_user_id, require_ws
from ..db import connect_pages_db, page_now, safe_doc_id, ws_uploads_dir
from ..blocks_store import (FOLDERS, LABELS, STORED_COLUMNS, create_page, existing_in, filing, folder_paths,
                            insert_page_tree, last_child_position, new_block_id, page_for_doc, page_root_id, refiled,
                            refiled_paths, touch_page, write_lock)
from ..colors import DEFAULT_HIGHLIGHT, normalize_color
from ..highlights import position as highlight_position
from ..logbuf import log
from ..ops import (MAX_CONTENT, MAX_OPS, after_commit, after_commits, apply_batches, ensure_filing, note_reload,
                   props_patch)
from ..markdown_import import MAX_MARKDOWN_BYTES, TooManyBlocks, md_to_blocks
from ..markdown_zip_import import import_markdown_zip, markdown_page
from ..notability_import import import_notes
from ..goodnotes_import import import_goodnotes
from ..ink import InkError, dumps as ink_dumps, from_pdf_ink, parse_ink, pdf_position as ink_position
from ..pdf_text import GlyphPages, quote_under
from ..pdf_export import (GAMMA_ALPHA, TEXT_BOX_TYPES, _resolve, annotation_key, annotation_shown, display_size,
                          drop_annotations, first_rect, page_frame, pdf_point_to_viewer, reply_parent)
from ..pdf_image import DecodeBudget, image_file, stream_data
from ..text_box import escape_markdown, keep_on_surface, markdown_of, measure, normalize_text_box, plain_text
from ..storage import (IMAGE_MEDIA_TYPES, content_digest, display_filename, find_upload_file, pdf_url, put_upload,
                       store_file, store_file_stream, store_pdf_stream, size as stored_size, stat as stored_stat,
                       touch as stored_touch, upload_refs)
from ..logseq_import import (
    edn_highlight_position,
    edn_highlight_to_block,
    map_color,
    md_to_ordered_blocks,
    parse_edn,
    parse_logseq_md,
)
from ..zotero_export import GAMMA_NOTE_KEY, GAMMA_PAGE_KEY, IMAGE_MIME
from ..zotero_import import html_note_text, plan_zotero_archive, same_work
from ..zotero_notes import store_images
from ..import_review import destination, parse_selection, selected_warnings, validate_selection
from ..workspaces import is_guest_workspace

router = APIRouter(prefix="/api", tags=["import"])


# --- The reviewed import: upload → review → import (a background job) -----------
# Each source is a preview and a commit over a binary file (the staged
# upload, or a plain endpoint's file) and the review's metadata (filename,
# folder — the destination folder's id, "" the library's top —, strip):
# ``preview(ws, data, meta)`` and ``commit(ws, actor, data, meta, selection,
# progress)`` — ``selection`` the chosen source ids (None: everything),
# ``progress`` a background job's report (gamma/jobs.py). A preview writes
# nothing; a commit makes the folders and labels its pages need first
# (``ops.ensure_filing``). A preview may answer a ``stage`` dict, what its
# commit can reuse (a Zotero review's PDF digests): a staged review keeps
# it in the metadata its commit gets (``import_staging.update``), and no
# answer carries it.

def _review_source(source):
    sources = {
        "zotero": (_preview_zotero, _commit_zotero),
        "markdown-zip": (_preview_markdown_zip, _commit_markdown_zip),
        "markdown-file": (_preview_markdown_file, _commit_markdown_file),
        "notability": (_preview_notability, _commit_notability),
        "goodnotes": (_preview_goodnotes, _commit_goodnotes),
        "gamma": (_preview_gamma, _commit_gamma),
    }
    if source not in sources:
        raise HTTPException(status_code=400, detail="unsupported import source")
    return sources[source]


def _preview_staged(ws, user_id, token):
    """The staged upload's preview with the review's ``review_id``, its
    ``stage`` kept with the review for the commit; a preview that fails
    discards the upload."""
    try:
        path, metadata = import_staging.get(token, user_id, ws)
        preview, _ = _review_source(metadata["source"])
        with (path / "upload").open("rb") as data:
            report = preview(ws, data, metadata)
        stage = report.pop("stage", None)
        if stage:
            import_staging.update(token, user_id, ws, **stage)
        return {**report, "review_id": token}
    except Exception:
        import_staging.discard(token, user_id, ws)
        raise


@router.post("/import/review")
def upload_import_review(request: Request, file: UploadFile = File(...), source: str = Form(...),
                         folder: str = Form(""), strip: bool = Form(False)):
    """Stage the upload and answer the source's preview with the review's
    ``review_id``: nothing is imported until ``POST /api/jobs/import``."""
    ws = require_ws(request, write=True)
    _review_source(source)
    token = import_staging.create(file, user_id=request.state.user_id, ws=ws, source=source, folder=folder, strip=strip)
    return _preview_staged(ws, request.state.user_id, token)


# An upload larger than a proxy lets one request carry (Cloudflare caps a
# body at 100 MB) comes in parts, as a PDF does (routers/uploads.py): open →
# the parts, in order → finish, which answers like POST /import/review.

class ImportParts(BaseModel):
    source: str
    size: int
    name: str = ""
    folder: str = ""
    strip: bool = False


@router.post("/import/review/parts")
def open_import_review_parts(payload: ImportParts, request: Request):
    """Open an import upload in parts → ``{token, part_bytes, received: 0}``;
    the token becomes the review's ``review_id``."""
    ws = require_ws(request, write=True)
    _review_source(payload.source)
    if payload.source == "gamma" and is_guest_workspace(ws):  # refused before the bytes travel
        raise HTTPException(status_code=403, detail="a guest workspace cannot import backups")
    token = import_staging.open_parts(user_id=request.state.user_id, ws=ws, source=payload.source,
                                      folder=payload.folder, strip=payload.strip, filename=payload.name,
                                      size=payload.size)
    return {"token": token, "part_bytes": upload_parts.PART_BYTES, "received": 0}


@router.post("/import/review/parts/{token}")
def put_import_review_part(token: str, request: Request, part: UploadFile = File(...), offset: int = Form(...)):
    """One part at byte ``offset`` → ``{received}``; at any other offset a
    409 whose body carries ``received``, where the client goes on from."""
    ws = require_ws(request, write=True)
    try:
        received = import_staging.append(token, request.state.user_id, ws, offset, part.file)
    except upload_parts.OffsetMismatch as e:
        return JSONResponse({"detail": str(e), "received": e.received}, status_code=409)
    return {"received": received}


@router.post("/import/review/parts/{token}/finish")
def finish_import_review_parts(token: str, request: Request):
    """The whole upload is in: answer its preview like POST /import/review
    (400 while bytes are missing). ``DELETE /import/review/{token}`` drops
    an unfinished one."""
    ws = require_ws(request, write=True)
    import_staging.finish(token, request.state.user_id, ws)
    return _preview_staged(ws, request.state.user_id, token)


@router.delete("/import/review/{token}")
def discard_import_review(token: str, request: Request):
    ws = require_ws(request, write=True)
    import_staging.discard(token, request.state.user_id, ws)
    return {"ok": True}


@router.post("/import/review/{token}/keepalive")
def keep_import_review(token: str, request: Request):
    """The dialog still shows the review: its two hours start again, also
    once past them while no upload's sweep has removed it (404 then)."""
    ws = require_ws(request, write=True)
    import_staging.touch(token, request.state.user_id, ws)
    return {"ok": True}


class ImportJob(BaseModel):
    review_id: str
    selected: list[str]


def _selection_digest(selected) -> str:
    return hashlib.sha256("\n".join(sorted(set(selected))).encode("utf-8")).hexdigest()


@router.post("/jobs/import")
def start_import_job(payload: ImportJob, request: Request):
    """Import the selected items of a staged review as a background job
    (kind ``import``, docs/dev/tasks.md); its result is the import's report.
    Asking again for the same review answers the job already started — 409
    when it was started with another selection."""
    user_id = require_user_id(request)
    ws = require_ws(request, write=True)
    token, digest = payload.review_id, _selection_digest(payload.selected)

    def same_review(job):
        if job["params"].get("digest") != digest:
            raise HTTPException(status_code=409, detail="this review was already imported with a different selection")
        return job

    prior = jobs.latest(user_id, "import", token)
    if prior is not None and prior["state"] in ("queued", "running", "done"):
        return same_review(prior)
    path, metadata = import_staging.touch(token, user_id, ws)  # pressing Import is activity too
    upload = path / "upload"
    if not upload.exists():
        raise HTTPException(status_code=410, detail="this review was already imported; choose the file again")
    _, commit = _review_source(metadata["source"])
    selection, actor = set(payload.selected), actor_of(request)

    def run(job):
        try:
            with import_staging.claim(token, user_id, ws) as (staged, meta):
                with (staged / "upload").open("rb") as data:
                    return commit(ws, actor, data, meta, selection, job.progress)
        finally:  # done, failed or stopped, the review is over: its upload goes
            try:
                import_staging.discard(token, user_id, ws)
            except HTTPException:
                pass  # already gone (expired)

    try:
        return jobs.start("import", owner=user_id, ws=ws, key=token, run=run, title=f"Import {metadata['filename']}",
                          params={"review_id": token, "source": metadata["source"], "filename": metadata["filename"],
                                  "folder": metadata["folder"], "size": upload.stat().st_size,
                                  "selected": len(selection), "digest": digest})
    except jobs.Busy as busy:  # a second start raced this one
        return same_review(busy.job)


@router.post("/import/logseq")
def import_logseq(
    request: Request,
    pdf: UploadFile = File(...),
    edn: UploadFile = File(...),
    md: UploadFile = File(None),
):
    # 1. Validate and store PDF (a sync def: the parsing and the writes run
    #    in the threadpool, the uploads read through ``.file``)
    ws = require_ws(request, write=True)
    try:  # spooled to disk and hashed as it is read, like POST /uploads
        digest, _, _ = store_pdf_stream(ws, pdf.file)
    except ValueError:
        raise HTTPException(status_code=400, detail="not a valid PDF")

    # 2. Parse EDN → build quote→highlight lookup
    edn_text = edn.file.read().decode("utf-8")
    try:
        parsed = parse_edn(edn_text)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid EDN: {e}")
    edn_highlights = parsed.get("highlights", []) if isinstance(parsed, dict) else []

    # Build lookup by quote text for MD matching (strip whitespace for robustness)
    edn_by_quote = {}
    for h in edn_highlights:
        quote = (h.get("content") or {}).get("text", "")
        edn_by_quote[quote.strip()] = {
            "quote": quote.strip(),
            "color": map_color((h.get("properties") or {}).get("color", "yellow")),
            "position": edn_highlight_position(h),
        }

    # 3. Build import blocks ordered by MD (if provided), EDN-only at end
    if md is not None:
        md_text = md.file.read().decode("utf-8")
        md_blocks_parsed = parse_logseq_md(md_text)
        edn_by_uuid = {
            h.get("id", ""): edn_by_quote[(h.get("content") or {}).get("text", "")]
            for h in edn_highlights
            if h.get("id") and (h.get("content") or {}).get("text", "") in edn_by_quote
        }
        import_blocks, used_quotes = md_to_ordered_blocks(md_blocks_parsed, edn_by_quote, edn_by_uuid)
        # Append EDN highlights not referenced in MD, sorted by page number
        edn_only = [h for h in edn_highlights
                    if (h.get("content") or {}).get("text", "").strip() not in used_quotes]
        edn_only.sort(key=lambda h: h.get("page") or (h.get("position") or {}).get("page") or 0)
        for h in edn_only:
            import_blocks.append(edn_highlight_to_block(h))
    else:
        import_blocks = [edn_highlight_to_block(h) for h in edn_highlights]

    # 4. The page carrying this PDF (one per PDF: looked up and created
    #    under the write lock, like every get-or-create by attachment)
    title = (pdf.filename or digest).removesuffix(".pdf")
    now = page_now()
    actor = actor_of(request)
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        row = page_for_doc(conn, digest)
        block_id = row[0] if row else create_page(conn, title, {"doc_id": digest}, actor=actor)["id"]

        # 5. Append blocks, skipping what an earlier import of these files
        #    already put there: highlights by their quote, notes by their
        #    text (as many times as they occur) — a re-run adds nothing twice.
        children = conn.execute(
            "SELECT content, json_extract(properties,'$.quote') FROM unified_blocks WHERE parent_id=?",
            (block_id,),
        ).fetchall()
        existing_quotes = {quote for _, quote in children if quote}
        existing_notes = Counter(content for content, quote in children if not quote and content)
        n = max(1, len(import_blocks))
        last_child_pos = last_child_position(conn, block_id)
        positions = generate_n_keys_between(last_child_pos, None, n=n)
        inserted = 0
        for b, pos_key in zip(import_blocks, positions):
            bprops = json.loads(b["properties"]) if isinstance(b["properties"], str) else b.get("properties", {})
            quote = bprops.get("quote", "")
            if quote and quote in existing_quotes:
                continue
            if not quote and existing_notes[b.get("content", "")] > 0:
                existing_notes[b.get("content", "")] -= 1
                continue
            conn.execute(
                f"INSERT INTO unified_blocks ({STORED_COLUMNS}) VALUES (?,?,?,?,?,?,?,?)",
                (b["id"], block_id, pos_key,
                 b.get("content", ""),
                 b["properties"] if isinstance(b["properties"], str) else json.dumps(b.get("properties", {})),
                 now, now, block_id),
            )
            if quote:
                existing_quotes.add(quote)
            inserted += 1
        # One transaction with the rows: an existing page logs a reload
        # (note_reload touches it, commits and fans out); a new page is
        # touched again once its notes are in, so the change feed lists it
        # after its creation.
        if row and inserted:
            note_reload(ws, conn, block_id, actor)
        else:
            if inserted:
                touch_page(conn, block_id, actor)
            conn.commit()

    return {"ok": True, "block_id": block_id, "doc_id": digest, "source_url": pdf_url(digest), "imported": inserted}


# --- Plain Markdown note import -----------------------------------------------

@router.post("/import/markdown")
def import_markdown(request: Request, file: UploadFile = File(...),
                          folder: str = Form("")):
    """Turn one Markdown file into a note page and nested note blocks.

    Markdown is data, not an uploaded web asset: the parsed blocks are stored in
    pages.db and no original file is served back. This also makes folder and
    single-file uploads share exactly the same import path.
    """
    ws = require_ws(request, write=True)
    raw = file.file.read(MAX_MARKDOWN_BYTES + 1)
    original = display_filename(file.filename, "note.md")
    with connect_pages_db(ws) as conn:
        result = markdown_page(ws, conn, raw, original, folder, actor=actor_of(request))
    return {"ok": True, **result}


def _markdown_zip(data, ws, folder, *, actor="", preview=False, selection=None, progress=jobs.no_progress):
    """A zip of Markdown notes → one page per .md (see markdown_zip_import)."""
    try:
        zf = zipfile.ZipFile(data)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="not a zip file")
    with zf, connect_pages_db(ws) as conn:  # the import commits page by page
        return {"ok": True, **import_markdown_zip(ws, zf, conn, folder, actor=actor, preview=preview,
                                                  selected=selection, progress=progress)}


def _preview_markdown_zip(ws, data, meta):
    return _markdown_zip(data, ws, meta.get("folder", ""), preview=True)


def _commit_markdown_zip(ws, actor, data, meta, selection, progress=jobs.no_progress):
    return _markdown_zip(data, ws, meta.get("folder", ""), actor=actor, selection=selection, progress=progress)


def _markdown_file(ws, data, meta, *, actor="", preview=False, selection=None, progress=jobs.no_progress):
    """The review flow treats a single note as a one-entry archive."""
    raw = data.read(MAX_MARKDOWN_BYTES + 1)
    if len(raw) > MAX_MARKDOWN_BYTES:
        raise HTTPException(status_code=413, detail="Markdown file exceeds 5 MB")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(display_filename(meta.get("filename"), "note.md"), raw)
    buf.seek(0)
    return _markdown_zip(buf, ws, meta.get("folder", ""), actor=actor, preview=preview, selection=selection,
                         progress=progress)


def _preview_markdown_file(ws, data, meta):
    return _markdown_file(ws, data, meta, preview=True)


def _commit_markdown_file(ws, actor, data, meta, selection, progress=jobs.no_progress):
    return _markdown_file(ws, data, meta, actor=actor, selection=selection, progress=progress)


def _notability(ws, data, meta, *, actor="", preview=False, selection=None, progress=jobs.no_progress):
    """Notability notes (one .note or .ntb, or a .zip of them) → a page each
    (gamma/notability_import.py)."""
    with connect_pages_db(ws) as conn:  # the import commits note by note
        return {"ok": True, **import_notes(ws, data, conn, meta.get("folder", ""), name=meta.get("filename", ""),
                                           actor=actor, preview=preview, selected=selection, progress=progress)}


def _preview_notability(ws, data, meta):
    return _notability(ws, data, meta, preview=True)


def _commit_notability(ws, actor, data, meta, selection, progress=jobs.no_progress):
    return _notability(ws, data, meta, actor=actor, selection=selection, progress=progress)


def _goodnotes(ws, data, meta, *, actor="", preview=False, selection=None, progress=jobs.no_progress):
    """GoodNotes notebooks (one .goodnotes, or a .zip of them) → a page each
    (gamma/goodnotes_import.py)."""
    with connect_pages_db(ws) as conn:  # the import commits notebook by notebook
        return {"ok": True, **import_goodnotes(ws, data, conn, meta.get("folder", ""), name=meta.get("filename", ""),
                                               actor=actor, preview=preview, selected=selection, progress=progress)}


def _preview_goodnotes(ws, data, meta):
    return _goodnotes(ws, data, meta, preview=True)


def _commit_goodnotes(ws, actor, data, meta, selection, progress=jobs.no_progress):
    return _goodnotes(ws, data, meta, actor=actor, selection=selection, progress=progress)


def _gamma_zip(ws, data, work):
    """Refuses a guest's workspace; spools the export to ``work``."""
    if is_guest_workspace(ws):
        raise HTTPException(status_code=403, detail="a guest workspace cannot import backups")
    path = Path(work) / "import.zip"
    with path.open("wb") as dest:
        shutil.copyfileobj(data, dest)
    return path


def _preview_gamma(ws, data, meta):
    from .. import ws_backup
    with tempfile.TemporaryDirectory(prefix="gamma-import-review-") as td:
        try:
            return {"ok": True, **ws_backup.preview_zip(ws, _gamma_zip(ws, data, td))}
        except ws_backup.BackupError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


def _commit_gamma(ws, actor, data, meta, selection, progress=jobs.no_progress):
    """A Gamma export's selected pages merged in (``restore_zip`` merge)."""
    from .. import ws_backup
    if selection is None:
        raise HTTPException(status_code=400, detail="review and select items before importing")
    with tempfile.TemporaryDirectory(prefix="gamma-import-review-") as td:
        try:
            return {"ok": True, **ws_backup.restore_zip(ws, _gamma_zip(ws, data, td), "merge", selected=selection,
                                                        by=actor, progress=progress)}
        except ws_backup.BackupError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


def _meta(file: UploadFile, folder: str = "", strip: bool = False) -> dict:
    """A plain endpoint's upload described like a staged review's metadata."""
    return {"filename": file.filename or "", "folder": folder, "strip": strip}


# The sources as plain endpoints (one request each; the web app goes
# through the review above).
@router.post("/import/markdown-zip")
def import_markdown_zip_endpoint(request: Request, file: UploadFile = File(...),
                                 folder: str = Form(""), selected: str | None = Form(None)):
    """A zip of Markdown notes → one page per .md (see markdown_zip_import):
    an Obsidian vault, Notion's Markdown & CSV export, a Gamma Markdown or
    vault export, or any zipped folder of notes. ``folder`` (a folder id)
    is the destination: the zip's folder tree is made below it."""
    ws = require_ws(request, write=True)
    return _commit_markdown_zip(ws, actor_of(request), file.file, _meta(file, folder), parse_selection(selected))


@router.post("/import/markdown-zip/preview")
def preview_markdown_zip(request: Request, file: UploadFile = File(...), folder: str = Form("")):
    return _preview_markdown_zip(require_ws(request, write=True), file.file, _meta(file, folder))


@router.post("/import/markdown-file/preview")
def preview_markdown_file(request: Request, file: UploadFile = File(...), folder: str = Form("")):
    return _preview_markdown_file(require_ws(request, write=True), file.file, _meta(file, folder))


@router.post("/import/markdown-file")
def import_reviewed_markdown_file(request: Request, file: UploadFile = File(...),
                                 folder: str = Form(""), selected: str | None = Form(None)):
    ws = require_ws(request, write=True)
    return _commit_markdown_file(ws, actor_of(request), file.file, _meta(file, folder), parse_selection(selected))


@router.post("/import/notability/preview")
def preview_notability(request: Request, file: UploadFile = File(...), folder: str = Form("")):
    return _preview_notability(require_ws(request, write=True), file.file, _meta(file, folder))


@router.post("/import/notability")
def import_notability(request: Request, file: UploadFile = File(...), folder: str = Form(""),
                      selected: str | None = Form(None)):
    """Notability notes (one .note or .ntb, or a .zip of them) → a page
    each; the web app goes through the review (``/import/review``, source
    ``notability``)."""
    ws = require_ws(request, write=True)
    return _commit_notability(ws, actor_of(request), file.file, _meta(file, folder), parse_selection(selected))


@router.post("/import/goodnotes/preview")
def preview_goodnotes(request: Request, file: UploadFile = File(...), folder: str = Form("")):
    return _preview_goodnotes(require_ws(request, write=True), file.file, _meta(file, folder))


@router.post("/import/goodnotes")
def import_goodnotes_endpoint(request: Request, file: UploadFile = File(...), folder: str = Form(""),
                              selected: str | None = Form(None)):
    """GoodNotes notebooks (one .goodnotes, or a .zip of them) → a page
    each; the web app goes through the review (``/import/review``, source
    ``goodnotes``)."""
    ws = require_ws(request, write=True)
    return _commit_goodnotes(ws, actor_of(request), file.file, _meta(file, folder), parse_selection(selected))


@router.post("/import/gamma/preview")
def preview_gamma(request: Request, file: UploadFile = File(...)):
    return _preview_gamma(require_ws(request, write=True), file.file, _meta(file))


@router.post("/import/gamma")
def import_selected_gamma(request: Request, file: UploadFile = File(...), selected: str = Form(...)):
    ws = require_ws(request, write=True)
    return _commit_gamma(ws, actor_of(request), file.file, _meta(file), parse_selection(selected))


class MarkdownBlocksRequest(BaseModel):
    text: str


@router.post("/markdown-blocks")
def markdown_blocks(payload: MarkdownBlocksRequest, request: Request):
    """Parse markdown text into a ``{content, children}`` block tree — the
    editor's "paste as blocks" helper, same parser as the .md file import.
    Nothing is stored; the client inserts the tree through its normal
    tree-edit/autosave path. 413 past 5 MB or MAX_BLOCKS blocks: the paste
    stays text."""
    require_user_id(request)
    if len(payload.text.encode("utf-8", errors="ignore")) > MAX_MARKDOWN_BYTES:
        raise HTTPException(status_code=413, detail="text exceeds 5 MB")
    try:
        return {"blocks": md_to_blocks(payload.text)}
    except TooManyBlocks as exc:
        raise HTTPException(status_code=413, detail=f"text has {exc}")


# --- Annotations embedded in the PDF file itself ------------------------------
# SumatraPDF ("save annotations"), Acrobat, Preview etc. write standard PDF
# annotation objects. Convert them to Gamma blocks: markup annotations to
# highlights, and the kinds below to their own blocks.

_MARKUP_TYPES = {"/Highlight", "/Underline", "/Squiggly", "/StrikeOut"}
# Typed text and sticky notes → text boxes (gamma/text_box.py), the inverse
# of the /FreeText pdf_export.py writes.
_NOTE_TYPES = TEXT_BOX_TYPES
# Rectangle/ellipse drawings → area highlights (position carries area: true),
# the inverse of what pdf_export.py writes for Gamma's own area notes.
_AREA_TYPES = {"/Square", "/Circle"}
# Freehand drawings → handwriting groups (gamma/ink.py), the inverse of the
# /Ink annotations pdf_export.py writes.
_INK_TYPES = {"/Ink"}
# Image stamps → pictures (gamma/picture.py), the inverse of the /Stamp
# pdf_export.py writes; a rubber stamp of text or vectors makes nothing.
_PICTURE_TYPES = {"/Stamp"}
_IMPORT_TYPES = _MARKUP_TYPES | _NOTE_TYPES | _AREA_TYPES | _INK_TYPES | _PICTURE_TYPES
# The key Zotero gives an item, an annotation among them: eight characters
# of its alphabet (no 0, 1 or O).
_ZOTERO_KEY_RE = re.compile(r"^[23456789ABCDEFGHIJKLMNPQRSTUVWXYZ]{8}$")


def _alpha(obj, default: float) -> float:
    """The annotation's opacity, from 0.05 to 1: its ``/GammaAlpha`` when
    it has one (pdf_export.GAMMA_ALPHA: a Zotero RDF export's copy keeps
    Gamma's alpha there instead of in /CA, by which Zotero would pale the
    colour), else its /CA, else ``default``."""
    for key in (GAMMA_ALPHA, "/CA"):
        try:
            return min(max(float(_resolve(obj.get(key))), 0.05), 1.0)
        except (TypeError, ValueError):
            pass
    return default


def _zotero_annotation_key(obj) -> str:
    """The key of the Zotero annotation an embedded one is a copy of, which
    Zotero writes as ``/Zotero:Key`` on each annotation of its own; "" for
    any other. Its ``/NM`` is not read: Gamma's exports give a /Square, an
    /Ink and a /FreeText an ``/NM`` of Zotero's shape too
    (``pdf_export.zotero_annot_key``), naming no annotation Zotero holds."""
    key = str(_resolve(obj.get("/Zotero:Key")) or "").strip()
    return key if _ZOTERO_KEY_RE.match(key) else ""


def _ink_from_annotation(obj, pnum: int, pw: float, ph: float, contents: str):
    """One /Ink annotation → an importer record carrying the parsed ink file
    (``kind: "ink"``). A Gamma export's ``/GammaInk`` private key restores
    pressure, time and the dash (a payload from before release T is a
    version 1 file, which ``from_pdf_ink`` upgrades); foreign ink is
    polylines at the annotation's width, dashed when its border style is
    (``/BS /S /D``, whatever its ``/D`` array: the pattern follows the
    width, as Gamma draws every dash)."""
    ink_list = [[float(_resolve(v)) for v in (_resolve(path) or [])]
                for path in (_resolve(obj.get("/InkList")) or [])]
    bs = _resolve(obj.get("/BS")) or {}
    try:
        width = float(_resolve(bs.get("/W", 1)))
    except (TypeError, ValueError):
        width = 1.0
    dash = "dashed" if str(_resolve(bs.get("/S")) or "") == "/D" else None
    color = "#1f1f1f"
    c = _resolve(obj.get("/C"))
    try:
        if c is not None and len(c) == 3:
            color = "#%02x%02x%02x" % tuple(min(255, max(0, int(round(float(_resolve(v)) * 255)))) for v in c)
    except (TypeError, ValueError):
        pass
    alpha = _alpha(obj, 1.0)
    private = _resolve(obj.get("/GammaInk"))
    try:
        ink = parse_ink(from_pdf_ink(ink_list, width, color, alpha, pnum, pw, ph,
                                     str(private) if private else None, dash=dash))
    except InkError as e:
        log.warning(f"[pdf-annots] skipping unreadable ink on p.{pnum}: {e}")
        return None
    if not ink.strokes:
        return None
    first = ink_list[0][:2] if ink_list and len(ink_list[0]) >= 2 else (0, 0)
    key = f"{pnum}:/Ink:{round(first[0])}:{round(first[1])}:{len(ink.strokes)}"
    return {"key": key, "content": contents, "quote": "", "color": color,
            "position": ink_position(ink), "kind": "ink", "ink": ink}


def _pdf_hex(values) -> str | None:
    """A PDF colour array (gray, RGB or CMYK) → ``#rrggbb``; None for
    anything else, the empty array of "no colour" among them."""
    try:
        v = [min(1.0, max(0.0, float(_resolve(c)))) for c in values or ()]
    except (TypeError, ValueError):
        return None
    if len(v) == 1:
        v *= 3
    elif len(v) == 4:
        v = [(1 - c) * (1 - v[3]) for c in v[:3]]
    elif len(v) != 3:
        return None
    return "#%02x%02x%02x" % tuple(round(c * 255) for c in v)


def _da_style(da: str) -> dict:
    """The font size and colour a /DA string sets ("/Helv 12 Tf 0 0 1 rg"):
    the operand of ``Tf`` (0 means fit-to-box, so none) and the last colour
    operator's."""
    style, operands = {}, []
    for token in da.split():
        try:
            operands.append(float(token))
            continue
        except ValueError:
            pass
        n = {"g": 1, "rg": 3, "k": 4}.get(token)
        if token == "Tf" and operands and operands[-1] > 0:
            style["size"] = operands[-1]
        elif n and len(operands) >= n:
            style["color"] = _pdf_hex(operands[-n:])
        operands = []
    return style


def _newlines(text: str) -> str:
    """Line breaks as \\n: Acrobat writes \\r in /Contents."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _gamma_text_box(value):
    """``(markdown, box, text)`` from a Gamma export's /GammaTextBox, or
    None: ``text`` the /Contents that export wrote (its references read as
    their labels), else the Markdown's plain text."""
    if value is None:
        return None
    try:
        data = json.loads(str(_resolve(value)))
    except ValueError:
        return None
    if (not isinstance(data, dict) or data.get("v") != 1 or not isinstance(data.get("md"), str)
            or not isinstance(data.get("box"), dict)):
        return None
    text = data.get("text") if isinstance(data.get("text"), str) else plain_text(data["md"])
    return data["md"], data["box"], _newlines(text).strip()


def _rc_text(value) -> str:
    """/RC rich text (XHTML) → Markdown: paragraphs, list items, bold and
    italic as ``zotero_import.html_note_text`` keeps them, and the text
    between the tags escaped (``text_box.escape_markdown``, each run as if
    it began a line), so a "$" or a "*" another app typed reads as typed."""
    value = _resolve(value)
    xhtml = value.get_data().decode("utf-8", "replace") if hasattr(value, "get_data") else str(value or "")
    return html_note_text("".join(
        part if part.startswith("<") else html.escape(escape_markdown(html.unescape(part)), quote=False)
        for part in re.split(r"(<[^>]*>)", xhtml)))


def _text_area(obj, rect):
    """A /FreeText's text area: its /Rect less the /RD margins, since a
    callout's /Rect also holds its line; the /Rect itself without a usable
    /RD."""
    try:
        left, bottom, right, top = (float(_resolve(v)) for v in _resolve(obj.get("/RD")) or ())
    except (TypeError, ValueError):
        return rect
    x0, y0, x1, y1 = rect[0] + left, rect[1] + bottom, rect[2] - right, rect[3] - top
    return (x0, y0, x1, y1) if min(left, bottom, right, top) >= 0 and x1 > x0 and y1 > y0 else rect


def _zotero_rect(obj):
    """A Zotero text annotation's box before its turn (``/Zotero:Rect``,
    centred where its turned /Rect is), or None."""
    try:
        x0, y0, x1, y1 = (float(_resolve(v)) for v in _resolve(obj.get("/Zotero:Rect")) or ())
    except (TypeError, ValueError):
        return None
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)) if x0 != x1 and y0 != y1 else None


def _text_box_from_annotation(obj, subtype: str, pnum: int, page, contents: str):
    """A /FreeText or /Text annotation → an importer record carrying a text
    box (``kind: "text_box"``), placed from its /Rect through the page's
    view box and rotation, the inverse of the export's mapping, and kept
    inside the page. A Gamma export's /GammaTextBox restores the Markdown
    and the box; the place still comes from /Rect, so a move in another
    viewer holds, and /Contents wins when another viewer changed the text
    that export wrote. Text from anywhere else is plain text, escaped into
    Markdown that shows it as written (``text_box.markdown_of``). A foreign
    /FreeText is a fixed-width box over its text area (``_text_area``),
    with the /DA size and colour and the /C fill; its text is /Contents, or
    /RC reduced to text. A Zotero text annotation may be turned, which a
    box is not: it is its unturned ``/Zotero:Rect`` upright, on the same
    centre. A /Text sticky note becomes a note-yellow box at its icon's
    top-left, as wide as its text."""
    rect = first_rect(obj)
    if not rect:
        return None
    crop, rotation = page_frame(page)
    disp_w, disp_h = display_size(crop, rotation)
    # Keyed from the whole /Rect: the key these kinds had as highlight
    # blocks, so a PDF imported as such adds nothing twice.
    key = annotation_key(pnum, subtype, rect)
    x0, y0, x1, y1 = _text_area(obj, rect) if subtype == "/FreeText" else rect
    corners = [pdf_point_to_viewer(px, py, rotation, crop) for px, py in ((x0, y0), (x1, y1))]
    left, right = sorted(u * disp_w for u, _v in corners)
    top, bottom = sorted(v * disp_h for _u, v in corners)
    private = _gamma_text_box(obj.get("/GammaTextBox"))
    if private:
        md, box, written = private
        if written != contents:
            md = markdown_of(contents)
        box = {**box, "x": left, "y": top}
    else:
        md = markdown_of(contents) if contents else _rc_text(obj.get("/RC"))
        if subtype == "/Text":
            box = normalize_text_box({"x": left, "y": top, "bg": "#fff4b8"})
            box["w"], box["h"] = measure(md, box, disp_w)
        else:
            box = {"x": left, "y": top, "w": right - left, "h": bottom - top, "auto": False,
                   **_da_style(str(_resolve(obj.get("/DA")) or "")), "bg": _pdf_hex(_resolve(obj.get("/C")))}
            zotero = _zotero_rect(obj)
            if zotero:
                u, v = pdf_point_to_viewer((zotero[0] + zotero[2]) / 2, (zotero[1] + zotero[3]) / 2, rotation, crop)
                w, h = zotero[2] - zotero[0], zotero[3] - zotero[1]
                box.update(x=u * disp_w - w / 2, y=v * disp_h - h / 2, w=w, h=h)
    if not md.strip():
        return None
    return {"key": key, "page": pnum, "content": md, "kind": "text_box",
            "box": keep_on_surface(normalize_text_box(box), disp_w, disp_h)}


def _gamma_picture(value):
    """``(picture, caption, text)`` from a Gamma export's /GammaPicture, the
    picture normalized, or None: ``text`` the /Contents that export wrote
    (the caption's plain text on one line, its references read as their
    labels), else the caption's plain text."""
    if value is None:
        return None
    try:
        data = json.loads(str(_resolve(value)))
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("v") != 1 or not isinstance(data.get("caption"), str):
        return None
    placed = picture.normalize_picture(data.get("picture"))
    text = data["text"] if isinstance(data.get("text"), str) else picture.caption_text(plain_text(data["caption"]))
    return (placed, data["caption"], text) if placed else None


def _appearance(obj):
    """An annotation's normal appearance (/AP /N), the form a viewer draws:
    the one /AS names when /N holds several states; None without one."""
    ap = _resolve(obj.get("/AP"))
    shown = _resolve(ap.get("/N")) if hasattr(ap, "get") else None
    if shown is not None and not hasattr(shown, "get_data") and hasattr(shown, "get"):
        shown = _resolve(shown.get(str(_resolve(obj.get("/AS")) or "")))
    return shown if hasattr(shown, "get_data") else None


def _appearance_images(form) -> tuple[list, bool]:
    """The image XObjects an appearance's resources name, each once, with
    the orientation a Gamma export drew it through (the
    ``/GammaOrientation`` of the form holding it, else 1), its nested forms
    walked; and whether any of them names a font, so draws text."""
    images, text, seen, todo = {}, False, set(), [(form, 1)]
    while todo and len(seen) < 64:
        held, turn = todo.pop()
        if id(held) in seen:
            continue
        seen.add(id(held))
        turn = int(_resolve(held.get("/GammaOrientation")) or turn)
        resources = _resolve(held.get("/Resources")) or {}
        text = text or bool(_resolve(resources.get("/Font")))
        for ref in (_resolve(resources.get("/XObject")) or {}).values():
            xobject = _resolve(ref)
            kind = str(xobject.get("/Subtype", ""))
            if kind == "/Image" and not xobject.get("/ImageMask"):
                images.setdefault(id(xobject), (xobject, turn))
            elif kind == "/Form":
                todo.append((xobject, turn))
    return list(images.values()), text


# --- where an appearance draws its image ------------------------------------------
# Matrices are PDF's six numbers (a b c d e f): a point (x, y) maps to
# (a x + c y + e, b x + d y + f).

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_APPEARANCE_BYTES = 1 << 20  # the most appearance content one PDF's walk reads for where its images are
_NO_ROTATE = 16              # /F: the annotation stays upright when the page turns


def _times(m, n):
    """The matrix that maps a point through ``m``, then through ``n``."""
    a, b, c, d, e, f = m
    p, q, r, s, t, u = n
    return (a * p + b * r, a * q + b * s, c * p + d * r, c * q + d * s, e * p + f * r + t, e * q + f * s + u)


def _apply(m, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _matrix(value):
    """A /Matrix, or a cm's operands, as six floats; the identity for none
    or a bad one."""
    try:
        found = tuple(float(_resolve(v)) for v in _resolve(value) or ())
    except (TypeError, ValueError):
        return _IDENTITY
    return found if len(found) == 6 and all(map(math.isfinite, found)) else _IDENTITY


class _Drawn:
    """Where the appearances of one PDF draw their images: ``matrix(form,
    image)`` is the matrix ``form``'s content draws ``image`` through (the
    image fills the unit square of its space), from the content's q, Q, cm
    and Do, a nested form followed through its /Matrix; None when the
    content does not draw it, or cannot be read. A nested form a Gamma
    export marked ``/GammaOrientation`` counts as the image: its unit
    square shows the image as the file ``image_file`` reads back shows it.
    Each answer is worked out once, however many stamps share the form,
    and ``_APPEARANCE_BYTES`` of content are read for them all: past that,
    an image is taken to fill its form's /BBox."""

    def __init__(self):
        self.found, self.left = {}, _APPEARANCE_BYTES

    def matrix(self, form, image):
        at = (id(form), id(image))
        if at not in self.found:
            try:
                found = self._walk(form, image, set())
            except Exception:  # content PyPDF2 cannot parse, or a filter not undone
                found = None
            self.found[at] = (form, image, found)  # the objects kept with it, so their ids name them
        return self.found[at][2]

    def _walk(self, form, image, searched):
        from PyPDF2.generic import ContentStream, DecodedStreamObject

        searched.add(id(form))
        if self.left <= 0:
            return None
        content = DecodedStreamObject()
        content.set_data(stream_data(form, self.left))
        self.left -= len(content.get_data())
        xobjects = _resolve((_resolve(form.get("/Resources")) or {}).get("/XObject")) or {}
        ctm, saved = _IDENTITY, []
        for operands, op in ContentStream(content, None).operations:
            if op == b"q":
                saved.append(ctm)
            elif op == b"Q" and saved:
                ctm = saved.pop()
            elif op == b"cm":
                ctm = _times(_matrix(operands), ctm)
            elif op == b"Do" and operands:
                held = _resolve(xobjects.get(str(operands[0])))
                if held is image:
                    return ctm
                if (hasattr(held, "get") and str(held.get("/Subtype", "")) == "/Form" and id(held) not in searched
                        and len(searched) < 64):
                    inner = self._walk(held, image, searched)
                    if inner is not None:
                        here = _times(_matrix(held.get("/Matrix")), ctm)
                        return here if held.get("/GammaOrientation") is not None else _times(inner, here)
        return None


def _image_corners(obj, form, image, rect, crop, rotation, drawn):
    """Where a stamp shows the top-left, top-right and bottom-left corners
    of ``image``, in points of the page as shown: as a viewer draws an
    annotation (PDF 32000 12.5.5), the form's /BBox through its /Matrix
    fitted to /Rect, the image where the form's content draws it
    (``drawn``, a ``_Drawn``; over the whole /BBox when it cannot tell),
    then the page's turn, which the annotation takes unless it is NoRotate
    (then it stays upright, pinned at its /Rect's top-left)."""
    disp_w, disp_h = display_size(crop, rotation)
    matrix = _matrix(form.get("/Matrix"))
    try:
        bx0, by0, bx1, by1 = (float(_resolve(v)) for v in _resolve(form.get("/BBox")) or ())
    except (TypeError, ValueError):
        bx0, by0, bx1, by1 = 0, 0, 1, 1
    bx0, bx1, by0, by1 = min(bx0, bx1), max(bx0, bx1), min(by0, by1), max(by0, by1)
    xs, ys = zip(*(_apply(matrix, x, y) for x in (bx0, bx1) for y in (by0, by1)))
    sx = (rect[2] - rect[0]) / (max(xs) - min(xs)) if max(xs) > min(xs) else 1
    sy = (rect[3] - rect[1]) / (max(ys) - min(ys)) if max(ys) > min(ys) else 1
    shown = _times(matrix, (sx, 0, 0, sy, rect[0] - min(xs) * sx, rect[1] - min(ys) * sy))
    shown = _times(drawn.matrix(form, image) or (bx1 - bx0, 0, 0, by1 - by0, bx0, by0), shown)
    upright = rotation and int(_resolve(obj.get("/F")) or 0) & _NO_ROTATE
    pin = pdf_point_to_viewer(rect[0], rect[3], rotation, crop)

    def on_page(x, y):
        px, py = _apply(shown, x, y)
        if upright:
            return pin[0] * disp_w + px - rect[0], pin[1] * disp_h + rect[3] - py
        u, v = pdf_point_to_viewer(px, py, rotation, crop)
        return u * disp_w, v * disp_h

    return on_page(0, 1), on_page(1, 1), on_page(0, 0)


def _frame_of(corners) -> dict:
    """The picture whose frame shows an image with these corners (top-left,
    top-right, bottom-left, points of the page as shown): the frame's sides
    from the image's, its turn from the top side, mirrored (``flip_h``)
    when the corners run the other way round. A skew is lost."""
    (ax, ay), (bx, by), (cx, cy) = corners
    rx, ry, dx, dy = bx - ax, by - ay, cx - ax, cy - ay
    flip = rx * dy - ry * dx < 0
    if flip:  # the image's top-left shows at the frame's top-right
        rx, ry = -rx, -ry
    w, h = math.hypot(rx, ry), math.hypot(dx, dy)
    mx, my = (bx + cx) / 2, (by + cy) / 2  # the centre: midway between two opposite corners
    return {"x": mx - w / 2, "y": my - h / 2, "w": w, "h": h, "angle": math.degrees(math.atan2(ry, rx)),
            "flip_h": flip}


# A url for normalizing a picture whose file is not read yet (``_PictureFiles``).
_UNREAD = "/api/uploads/unread.png"


def _picture_from_annotation(obj, pnum: int, page, contents: str, drawn):
    """An image stamp → an importer record carrying a picture (``kind:
    "picture"``), or None for a stamp whose appearance draws anything but
    one image and no text (a rubber stamp's words and vectors). That image
    is the picture's file, read only for the records an import adds
    (``_PictureFiles``): ``image`` holds it and the orientation it is drawn
    through, and ``picture`` has no ``url`` until then. A Gamma export's
    /GammaPicture restores the picture (its size, crop, turn and flips) and
    its Markdown caption, and names the file it was drawn from in
    ``original``; its place comes from the centre of /Rect through the
    page's view box and rotation, the inverse of the export's mapping, so a
    move in another viewer holds, and /Contents wins when another viewer
    changed the text that export wrote. Any other image stamp is the whole
    image where its appearance shows it (``_image_corners`` through
    ``drawn``, the PDF's ``_Drawn``: on a turned page it turns with the
    page, as viewers draw it), its /Contents the caption. Either is kept on
    the page (``picture.keep_on_surface``)."""
    rect = first_rect(obj)
    form = _appearance(obj)
    if not rect or form is None:
        return None
    images, text = _appearance_images(form)
    if len(images) != 1 or text:
        return None
    crop, rotation = page_frame(page)
    disp_w, disp_h = display_size(crop, rotation)
    private, original = _gamma_picture(obj.get("/GammaPicture")), None
    if private:
        placed, caption, written = private
        if written != picture.caption_text(contents):
            caption = markdown_of(contents)
        original = placed["url"]
        corners = [pdf_point_to_viewer(px, py, rotation, crop) for px, py in ((rect[0], rect[1]), (rect[2], rect[3]))]
        mx, my = (sum(u for u, _v in corners) * disp_w / 2, sum(v for _u, v in corners) * disp_h / 2)
        placed = {**placed, "x": mx - placed["w"] / 2, "y": my - placed["h"] / 2}
    else:
        placed = _frame_of(_image_corners(obj, form, images[0][0], rect, crop, rotation, drawn))
        caption = markdown_of(contents) if contents else ""
    shown = picture.keep_on_surface(picture.normalize_picture({**placed, "url": _UNREAD}), disp_w, disp_h)
    del shown["url"]
    return {"key": annotation_key(pnum, "/Stamp", rect), "page": pnum, "content": caption, "kind": "picture",
            "picture": shown, "image": images[0], "original": original}


class _PictureFiles:
    """The files of a PDF's image stamps (``pdf_image.image_file``), read
    for the records an import adds and for no others: once per image
    however many stamps draw it, and within one ``DecodeBudget`` for them
    all, so a small file with thousands of stamps cannot hold the import
    for minutes. A stamp whose image cannot be read, or is past the
    budget, makes no block (logged), and its annotation stays in the file
    for a later import to try again."""

    def __init__(self):
        self.budget, self.by_image = DecodeBudget(), {}

    def read(self, record) -> bool:
        """Whether ``record``'s picture has its file now: ``file`` as
        ``(bytes, extension)`` (None for one that cannot be read), and the
        picture's ``url`` named by its content hash, as ``store_file`` will
        store it."""
        if "file" not in record:
            image, orientation = record["image"]
            at = (id(image), orientation)
            if at not in self.by_image:  # the image is kept with it, so its id names it while this lives
                self.by_image[at] = (image, image_file(image, orientation, self.budget))
            record["file"] = found = self.by_image[at][1]
            if found:
                record["picture"]["url"] = f"/api/uploads/{content_digest(found[0])}{found[1]}"
            else:
                log.info(f"[pdf-annots] the picture of the stamp {record['key']} is left out: its image did not read")
        return record["file"] is not None


def _picture_url(ws: str, record) -> str:
    """The file an imported picture shows: the one a Gamma export drew it
    from when this workspace has it and it is the size of the image read
    back (a round trip keeps the file as it was, a GIF's animation with
    it), else that image, stored. The storage quota is the one refusal
    storing it meets: a full workspace (507) stops the import, as it stops
    the handwriting's files."""
    name = (record.get("original") or "").rsplit("/", 1)[-1]
    path = find_upload_file(name, ws) if name else None
    data, ext = record["file"]
    if path is not None:
        try:
            if picture.image_size(path.read_bytes()) == picture.image_size(data):
                return record["original"]
        except OSError:
            pass
    filename, _ = store_file(ws, data, ext)
    return f"/api/uploads/{filename}"


def _extract_pdf_annotations(reader, glyphs=None):
    """The annotations of ``reader``'s pages that become blocks, as records
    in page order: ``key`` (the ``imported_annot``), ``page``, ``content``
    and the kind's fields, ``annot`` (the annotation's dictionary),
    ``zotero_key`` (``_zotero_annotation_key``) and ``replies``. Only
    what the page shows counts (``annotation_shown``: no Hidden or NoView
    annotation, no review-state stamp). A reply (/IRT) is no block of its
    own but a note under the annotation it answers, in ``replies`` in
    page order: ``{key, legacy, content, annot, replies}``,
    ``legacy`` the key it had when replies were imported as annotations of
    their own, ``key`` that with its /NM (or its place in /Annots), since a
    reply shares its parent's rectangle. A reply to something not imported
    is left out. ``glyphs`` is a ``pdf_text.GlyphPages`` over the same
    file, read for the text under each highlight; None (a caller that only
    needs which annotations make blocks) leaves the quotes empty."""
    found, replies, records, drawn = [], [], {}, _Drawn()
    for pnum, page in enumerate(reader.pages, start=1):
        try:
            annots = _resolve(page.get("/Annots")) or []
        except Exception:
            continue
        if not annots:
            continue
        mb = page.mediabox
        pw, ph = float(mb.width), float(mb.height)
        for index, ref in enumerate(annots):
            try:
                obj = ref.get_object()
                subtype = str(obj.get("/Subtype", ""))
                if subtype not in _IMPORT_TYPES or not annotation_shown(obj):
                    continue
                contents = _newlines(str(_resolve(obj.get("/Contents")) or "")).strip()
                parent = reply_parent(obj)
                if parent is not None:
                    # A comment in its parent's thread: kept as written, like
                    # the comment on a highlight.
                    text, rect = contents or _rc_text(obj.get("/RC")), first_rect(obj)
                    if text and rect:
                        legacy = annotation_key(pnum, subtype, rect)
                        nm = str(_resolve(obj.get("/NM")) or "").strip()
                        replies.append((parent, {"key": f"{legacy}:{nm or f'#{index}'}", "legacy": legacy,
                                                 "content": text, "annot": obj, "replies": [],
                                                 "at": (pnum, index)}))
                    continue
                if subtype in _INK_TYPES:
                    record = _ink_from_annotation(obj, pnum, pw, ph, contents)
                elif subtype in _NOTE_TYPES:
                    record = _text_box_from_annotation(obj, subtype, pnum, page, contents)
                elif subtype in _PICTURE_TYPES:
                    record = _picture_from_annotation(obj, pnum, page, contents, drawn)
                else:
                    record = _mark_from_annotation(obj, subtype, pnum, pw, ph, contents, glyphs)
                if record:
                    record.update(annot=obj, replies=[], zotero_key=_zotero_annotation_key(obj))
                    found.append(record)
                    records[id(obj)] = record
            except Exception as e:
                log.warning(f"[pdf-annots] skipping annotation on p.{pnum}: {e}")
    # A reply may answer one listed after it, or another reply.
    while replies:
        left = []
        for parent, reply in replies:
            if id(parent) in records:
                records[id(parent)]["replies"].append(reply)
                records[id(reply["annot"])] = reply
            else:
                left.append((parent, reply))
        if len(left) == len(replies):
            break
        replies = left
    for record in records.values():
        record["replies"].sort(key=lambda reply: reply["at"])
    return found


def _mark_from_annotation(obj, subtype, pnum, pw, ph, contents, glyphs):
    """A markup, square or circle annotation → a highlight record, or None
    without a rectangle. The quote is the text under the quads
    (``pdf_text.quote_under`` over ``glyphs``, a ``GlyphPages``; empty when
    None, or for an area mark)."""
    # Quad rects in PDF space (origin bottom-left)
    quads = []
    qp = _resolve(obj.get("/QuadPoints"))
    rect = _resolve(obj.get("/Rect"))
    if qp:
        nums = [float(_resolve(v)) for v in qp]
        for i in range(0, len(nums) - 7, 8):
            xs, ys = nums[i:i + 8:2], nums[i + 1:i + 8:2]
            quads.append((min(xs), min(ys), max(xs), max(ys)))
    elif rect:
        r = [float(_resolve(v)) for v in rect]
        quads.append((min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])))
    if not quads:
        return None
    quote = quote_under(glyphs.page(pnum), quads) if glyphs is not None and subtype in _MARKUP_TYPES else ""
    color = DEFAULT_HIGHLIGHT
    c = _resolve(obj.get("/C"))
    try:
        # The annotation's own opacity (``_alpha``; Zotero's carry none) —
        # honoring it makes a Gamma export → re-import round-trip the exact
        # shade. Stored as the viewer stores a colour, #rrggbbaa
        # (gamma/colors.py).
        alpha = _alpha(obj, 0.45)
        if c is not None and len(c) == 3:
            # Rounded, not truncated: an app that writes 0.831 means 212.
            r, g, b = (round(float(_resolve(v)) * 255) for v in c)
            color = normalize_color(f"rgba({r}, {g}, {b}, {round(alpha, 3)})") or DEFAULT_HIGHLIGHT
    except Exception:
        pass
    # Flipped to top-left origin (what the viewer stores), in the page's points
    position = highlight_position(pnum, pw, ph, [(q[0], ph - q[3], q[2], ph - q[1]) for q in quads],
                                  area=subtype in _AREA_TYPES)
    return {"key": annotation_key(pnum, subtype, quads[0]), "content": contents,
            "quote": quote, "color": color, "position": position}


def _strip_embedded_annotations(ws: str, pdf_path, keep=frozenset()) -> tuple[int, set]:
    """Rewrite the stored PDF without the annotations the import turns into
    blocks (``_extract_pdf_annotations``, folded replies included), with
    their threads and /Popup windows (``pdf_export.drop_annotations``), so
    the viewer's canvas doesn't paint them under Gamma's own. Everything
    else stays: links, kinds not imported, hidden ones, those that make
    no block (a note with no text), and the annotations whose keys are in
    ``keep`` (a picture the import left out: its image did not read, or
    was past the decode budget). No picture's image is read here. Returns
    the number of annotations removed and the keys of the blocks they make
    (a reply's legacy key too).

    Note the file keeps its content-hash name even though its bytes change —
    the name is only a key (``doc_id`` property), never re-derived.
    ``pdf_path`` is the stored file's path."""
    from PyPDF2 import PdfReader, PdfWriter

    reader = PdfReader(os.fspath(pdf_path))
    doomed, keys = set(), set()

    def take(record):
        doomed.add(id(record["annot"]))
        keys.update(k for k in (record["key"], record.get("legacy")) if k)
        for reply in record["replies"]:
            take(reply)

    for record in _extract_pdf_annotations(reader):
        if record["key"] not in keep:
            take(record)
    # Off the reader's pages, before the copy: PdfWriter.append clones each
    # page's /Annots as the reader holds them.
    removed = sum(drop_annotations(page, doomed) for page in reader.pages) if doomed else 0
    if not removed:
        return 0, set()
    writer = PdfWriter()
    writer.append(reader)
    out = io.BytesIO()
    writer.write(out)
    # Stored whole, so a concurrent download never sees a half-written file.
    put_upload(ws, pdf_path.name, out.getvalue())
    return removed, keys


def _imported_blocks(conn, block_id: str) -> list:
    """``(id, imported_annot, annot_stripped)`` of the blocks an import made
    under ``block_id``, at any depth: a reply's note sits under its
    parent's block, and a block may have been moved in since. Each block's
    properties are read as the walk reaches it: joining the walk back to
    the table had SQLite read every block of the workspace instead."""
    return conn.execute(
        "WITH RECURSIVE sub(id, properties) AS (SELECT id, properties FROM unified_blocks WHERE parent_id = ? "
        "UNION ALL SELECT b.id, b.properties FROM unified_blocks b JOIN sub ON b.parent_id = sub.id) "
        "SELECT id, json_extract(properties, '$.imported_annot'), json_extract(properties, '$.annot_stripped') "
        "FROM sub WHERE json_extract(properties, '$.imported_annot') IS NOT NULL", (block_id,)).fetchall()


def originals_gone(conn, page_id: str) -> list[dict]:
    """The ops marking the page's blocks imported from embedded annotations
    ``annot_stripped``, for when the page's PDF is replaced by another file
    (PUT /pages/{id}/attachment): the originals left with the old file, so
    PDF export draws these blocks again (``pdf_export.still_embedded``). An
    import of the new file takes the mark off the ones that file holds
    again (``import_embedded_annotations``)."""
    return [{"op": "set", "id": bid, "props": {"annot_stripped": True}}
            for bid, _key, done in _imported_blocks(conn, page_id) if not done]


def _record_keys(records) -> set:
    """The ``imported_annot`` keys of these annotation records and of their
    replies, a reply's legacy key too."""
    keys = set()
    for record in records:
        keys.update(k for k in (record["key"], record.get("legacy")) if k)
        keys |= _record_keys(record.get("replies") or ())
    return keys


class PdfAnnotsRequest(BaseModel):
    block_id: str
    doc_id: str
    # Settings → "Embedded PDF annotations": strip them from the stored file
    # after importing (the alternative is hiding them viewer-side).
    strip: bool = False


def import_embedded_annotations(ws: str, block_id: str, pdf_path, strip: bool, actor: str = "") -> dict:
    """Extract the annotations embedded in the stored PDF and add the missing
    ones as highlight, handwriting, text-box and picture blocks under ``block_id``,
    each reply a note under the block of the annotation it answers
    (idempotent via the stable ``imported_annot`` key: a reply is there
    under its own key or the legacy one, from before replies were notes),
    then optionally strip the originals from the file. ``annot_stripped``
    then follows the file on every block an import made: set where this
    import's strip removed the original, cleared where the page's own file,
    as this import read it, holds the original again (a replacement file
    carrying the old one's annotations, ``originals_gone``). A block made
    from a copy of a Zotero annotation names it (``zotero_annotation``, its
    ``/Zotero:Key``); a block there already is left as it is. ``found``
    counts the annotations that make blocks, replies included. Shared by
    the per-paper endpoint below and the Zotero library import.
    ``pdf_path``: the stored file's path (``storage.find_upload_file``)."""
    from PyPDF2 import PdfReader
    read_as = stored_stat(ws, pdf_path.name)  # the file ``found`` describes; a strip rewrites it
    reader = PdfReader(os.fspath(pdf_path))
    with GlyphPages(os.fspath(pdf_path)) as glyphs:
        found = _extract_pdf_annotations(reader, glyphs)
    # The pictures' files are read before the write lock, and only for the
    # stamps this import adds (``_PictureFiles``); one that does not read
    # makes no block, and its annotation stays in the file (``left``).
    files, left = _PictureFiles(), set()
    if any(f.get("kind") == "picture" for f in found):
        with connect_pages_db(ws) as conn:
            known = {key for _bid, key, _stripped in _imported_blocks(conn, block_id)}
        for f in found:
            if f.get("kind") == "picture" and f["key"] not in known and not files.read(f):
                left.add(f["key"])
        found = [f for f in found if f["key"] not in left]
    if not found:
        return {"found": 0, "imported": 0, "stripped": 0}

    def count(records):
        return sum(1 + count(r.get("replies") or ()) for r in records)

    now = page_now()
    inserted = 0
    with connect_pages_db(ws) as conn:
        # The page the annotations land in (block_id may be a block inside
        # it): its room is told to reload and its log gets the entry.
        page_id = page_root_id(conn, block_id)
        if not page_id:
            raise HTTPException(status_code=404, detail="page block not found")
        # Idempotent: each embedded annotation carries a stable key, looked
        # up under the write lock (two imports of one PDF add each once)
        write_lock(conn)
        existing = {}
        for bid, key, _stripped in _imported_blocks(conn, block_id):
            existing.setdefault(key, bid)
        made = {}  # id(record) → the block made for it now

        def insert(record, parent, position, props):
            nonlocal inserted
            bid = new_block_id()
            conn.execute(
                f"INSERT INTO unified_blocks ({STORED_COLUMNS}) VALUES (?,?,?,?,?,?,?,?)",
                (bid, parent, position, record["content"], json.dumps(props), now, now, page_id),
            )
            made[id(record)] = bid
            inserted += 1

        def add_replies(parent, replies):
            new = [r for r in replies if r["key"] not in existing and r["legacy"] not in existing]
            if new:
                positions = generate_n_keys_between(last_child_position(conn, parent), None, n=len(new))
                for reply, pos in zip(new, positions):
                    insert(reply, parent, pos, {"imported_annot": reply["key"]})
            for reply in replies:
                bid = made.get(id(reply)) or existing.get(reply["key"]) or existing.get(reply["legacy"])
                if bid and reply["replies"]:
                    add_replies(bid, reply["replies"])

        todo = [f for f in found if f["key"] not in existing]
        if todo:
            positions = generate_n_keys_between(last_child_position(conn, block_id), None, n=len(todo))
            for f, pos in zip(todo, positions):
                if f.get("kind") == "picture":
                    # One whose block went since the files were read is read now.
                    if not files.read(f):
                        left.add(f["key"])
                        continue
                    props = {"picture": {**f["picture"], "url": _picture_url(ws, f)}, "pdf_page": f["page"],
                             "imported_annot": f["key"]}
                elif f.get("kind") == "ink":
                    # The strokes live in an .ink upload like any drawn group.
                    filename, _ = store_file(ws, ink_dumps(f["ink"]), ".ink")
                    props = {
                        "ink_url": f"/api/uploads/{filename}",
                        "pdf_position": f["position"], "ink_strokes": len(f["ink"].strokes),
                        "color": f["color"], "imported_annot": f["key"],
                    }
                elif f.get("kind") == "text_box":
                    props = {"text_box": f["box"], "pdf_page": f["page"], "imported_annot": f["key"]}
                else:
                    props = {
                        "color": f["color"], "quote": f["quote"], "pdf_position": f["position"],
                        "imported_annot": f["key"],
                    }
                if f.get("zotero_key"):
                    props["zotero_annotation"] = f["zotero_key"]
                insert(f, block_id, pos, props)
        for f in found:
            parent = made.get(id(f)) or existing.get(f["key"])
            if f.get("replies") and parent:  # none for a picture left out
                add_replies(parent, f["replies"])
        if inserted:
            note_reload(ws, conn, page_id, actor)  # logs the reload, touches the page, commits: one transaction

    # Strip AFTER the blocks are committed: if the rewrite fails the file is
    # untouched and the import still stands; a re-run can strip again.
    stripped, gone = 0, set()
    if strip:
        try:
            stripped, gone = _strip_embedded_annotations(ws, pdf_path, keep=left)
        except Exception as e:
            log.warning(f"[pdf-annots] could not strip annotations from {pdf_path.name}: {e}")
    # PDF export skips an imported block while its original is in the file
    # (``pdf_export.still_embedded``), so the mark follows the file: on where
    # this strip removed the original, off where the file holds it. What the
    # file holds counts only when the import did not strip it, the file is
    # the page's own PDF (an older one of the page holds what the page's
    # file no longer does) and nothing rewrote it since it was read (another
    # import's strip). The check and the marks are one step under the write
    # lock, so a strip committing its marks lands after them, not between.
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            row = conn.execute("SELECT doc_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
            as_read = (not strip and row is not None and row[0] == pdf_path.stem
                       and stored_stat(ws, pdf_path.name) == read_as)
            held = _record_keys(found) if as_read else set()
            marks = [{"op": "set", "id": bid, "props": {"annot_stripped": True if key in gone else None}}
                     for bid, key, done in _imported_blocks(conn, block_id)
                     if (key in gone and not done) or (key in held and done)]
            results = apply_batches(conn, [(page_id, marks[i:i + MAX_OPS])
                                           for i in range(0, len(marks), MAX_OPS)], actor=actor)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        for result in results:
            after_commit(ws, conn, result)
    return {"found": count(found), "imported": inserted, "stripped": stripped}


# Sync endpoint: PyPDF2 parsing is CPU-bound; the threadpool keeps the loop free.
@router.post("/import/pdf-annotations")
def import_pdf_annotations(payload: PdfAnnotsRequest, request: Request):
    ws = require_ws(request, write=True)
    try:
        name = f"{safe_doc_id(payload.doc_id)}.pdf"
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid document id")
    pdf_path = find_upload_file(name, ws)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail="PDF not stored on the server")
    try:
        result = import_embedded_annotations(ws, payload.block_id, pdf_path, payload.strip,
                                             actor_of(request))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"could not read PDF annotations: {e}")
    return {"ok": True, **result}


# --- Zotero library import ----------------------------------------------------
# A zip of Zotero's File → Export Library → "Zotero RDF" (with "Export Files",
# "Export Notes" and "Include Annotations"). Items become pages, collections
# folders (below the destination folder), tags labels, item notes block
# trees on their pages, standalone notes pages of their own; reader
# annotations arrive embedded in the exported PDF copies and go through
# import_embedded_annotations above. Idempotent: pages are keyed by the file
# hash and by properties.zotero_key (export bytes change between exports —
# Zotero re-embeds annotations — so the item key is what survives a re-export).


def _work(meta, title) -> dict:
    """The record ``same_work`` compares for a page or an item: its paper
    record, else (none, or one without a title: a note) its title."""
    meta = meta if isinstance(meta, dict) else {}
    return meta if meta.get("title") else {**meta, "title": title}


def _zotero_key_merges(item, props, title) -> bool:
    """Whether ``item`` merges into a page carrying its Zotero key (the
    page's ``props`` and ``title``). A key naming the work itself (a URL,
    ``urn:isbn:…``) or a page Gamma exported always does. Zotero names
    other items ``#item_<itemID>``, a number local to one Zotero database,
    which an item of another library carries for another work: a ``#`` key
    merges only into a page of the same work (``same_work``: the DOI, else
    the title), as the page's record has it or as the Zotero record it was
    imported with (``zotero_rdf``'s ``work``) had it, so a record edited in
    Gamma still knows its item."""
    key = item["key"]
    if not key.startswith("#") or key.startswith(GAMMA_PAGE_KEY):
        return True
    work = _work(item["meta"], item["title"])
    kept = props.get("zotero_rdf")
    kept = kept.get("work") if isinstance(kept, dict) else None
    return same_work(_work(props.get("meta"), title), work) or (isinstance(kept, dict) and same_work(kept, work))


def _zotero_existing_page(conn, item):
    """The page an item merges into: an item Gamma exported, the page it was
    exported from when it is in this workspace, which other pages may share
    a PDF with; else the page holding its PDF; else one its Zotero key made
    that it may merge into (``_zotero_key_merges``)."""
    row = None
    if item["key"].startswith(GAMMA_PAGE_KEY):
        row = conn.execute("SELECT id, properties, content FROM unified_blocks WHERE parent_id='root' AND id = ?",
                           (item["key"][len(GAMMA_PAGE_KEY):],)).fetchone()
    if row is None:
        row = page_for_doc(conn, item["digest"], "id, properties, content")
    if row is None and item["key"]:
        rows = conn.execute(  # idx_ub_zotero_key's expression and condition (db.PAGES_SCHEMA)
            "SELECT id, properties, content FROM unified_blocks "
            "WHERE parent_id = 'root' AND json_extract(properties, '$.zotero_key') = ?", (item["key"],)).fetchall()
        row = next((r for r in rows if _zotero_key_merges(item, json.loads(r[1] or "{}"), r[2])), None)
    return row


# New pages a Zotero import writes in one transaction: few commits, and each
# hold of the write lock stays short (their files are stored before it).
ZOTERO_PAGES_PER_COMMIT = 50


def _zotero_paths(item, under_path):
    """The folder paths (names from the top) an item is filed in: its
    collections' below the destination (``under_path``), else the
    destination itself."""
    return [under_path + path for path in item["folders"]] or ([under_path] if under_path else [])


# An item's other attachments sit on its page under this block, as in
# Zotero's item tree: file chips and links, each marked with its Zotero key.
ATTACHMENTS_HEADING = "## Attachments"
ATTACHMENTS_PROP = "zotero_attachments"
ATTACHMENT_KEY = "zotero_attachment"


def _zotero_store_file(ws, zf, extra) -> str:
    """Store one of an item's files as ``POST /upload-file`` would (under
    its content hash and its name's extension, with the same refusals),
    streamed from the zip so a large one is never in memory. → the stored
    name. Raises ValueError for a file that is not what its extension says
    and HTTPException (507) past the storage quota."""
    with zf.open(extra["entry"]) as src:
        return store_file_stream(ws, src, extra["ext"])[0]


def _link_target(url: str) -> str:
    """``url`` as a markdown link's target: no space or parenthesis bare."""
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _markdown_link(text: str, url: str) -> str:
    """``[text](url)``, the shape a file chip and a link block hold: no
    brackets in the text (the frontend's file-chip markdown drops them too)."""
    text = re.sub(r"[\[\]]", "", text or "").strip() or url
    return f"[{text}]({_link_target(url)})"


def _zotero_extras(conn, ws, zf, item, block_id, created, doc_id, held, report) -> tuple[list, str | None]:
    """The item's ``extras`` as the blocks its page gets under
    "Attachments": a file chip per file, stored here (outside any
    transaction, like its PDF), and a link per web link. One the page holds
    already is left out: by its Zotero key, else a link by its URL in a
    block of the page, a file by its stored name: one a block of the page
    names (``storage.upload_refs``: the page's own PDF, an image, a
    picture's file) or one ``held`` names, the files this import is about
    to give the page (``_zotero_prepare``: its notes' pictures, the files
    the pictures of the PDF it attaches were drawn from). So a Gamma
    export's note images and pictures, which ride along as attachments
    too, come back as no chip. A file that cannot be stored is a warning
    and the rest go on. Returns ``(blocks, heading)``, each block
    ``{content, properties}``; ``heading`` is the page's "Attachments"
    block to add them under, None to make one."""
    keys, heading, names, named = set(), None, set(held), []
    if not created:
        for bid, content, props, key, is_heading in conn.execute(
                f"SELECT id, content, properties, json_extract(properties,'$.{ATTACHMENT_KEY}'), "
                f"json_extract(properties,'$.{ATTACHMENTS_PROP}') FROM unified_blocks WHERE page_id = ?",
                (block_id,)):
            named.append(content or "")
            names |= upload_refs(content, props)
            if key:
                keys.add(key)
            if is_heading and heading is None:
                heading = bid
    text = "\n".join(named)
    blocks = []
    for extra in item["extras"]:
        if extra["key"] in keys or (doc_id and extra.get("digest") == doc_id):
            continue
        if "url" in extra:
            if _link_target(extra["url"]) in text:
                continue
            content = _markdown_link(extra["title"], extra["url"])
            text += "\n" + content  # two links to one URL make one link
        else:
            try:
                name = _zotero_store_file(ws, zf, extra)
            except Exception as e:
                reason = e.detail if isinstance(e, HTTPException) else str(e)
                report["warnings"].append({"title": item["title"], "path": extra["entry"],
                                           "reason": f"Attachment not imported: {reason}"})
                continue
            if name in names:
                continue
            names.add(name)  # two attachments of one file make one chip
            content = _markdown_link(extra["name"], f"/api/uploads/{name}")
        blocks.append({"content": content, "properties": {ATTACHMENT_KEY: extra["key"]}})
    return blocks, heading


def _stamped_pictures(pdf_path) -> set:
    """The stored files the pictures of a Gamma export's image stamps in
    the PDF at ``pdf_path`` were drawn from (each /GammaPicture's ``url``,
    the ``original`` of ``_picture_from_annotation``): the files an import
    of its annotations gives those pictures when the workspace has them
    (``_picture_url``). No image is read; empty for a PDF that does not
    read."""
    from PyPDF2 import PdfReader

    try:
        records = _extract_pdf_annotations(PdfReader(os.fspath(pdf_path)))
    except Exception as e:
        log.warning(f"[zotero] could not read the pictures of {pdf_path.name}: {e}")
        return set()
    return {record["original"].rsplit("/", 1)[-1] for record in records if record.get("original")}


# A note's picture larger than this (decoded) stays out: a placeholder and a warning.
NOTE_IMAGE_MAX_BYTES = 25 * 1024 * 1024
# The extension a note's picture is stored under, by its type: the first
# zotero_export.IMAGE_MIME names for it (.jpg, as POST /upload-image stores one).
_NOTE_IMAGE_EXT = {mime: f".{ext}" for ext, mime in reversed(IMAGE_MIME.items())}


def _zotero_note_images(ws, item, blocks, report) -> set:
    """Store the pictures the block trees ``blocks`` (an item's notes, or a
    standalone note's body) hold as data URIs, as ``POST /upload-image``
    stores one, outside any transaction; the blocks then name the stored
    files instead (``zotero_notes.store_images``, in place). A picture past
    NOTE_IMAGE_MAX_BYTES, of a type Gamma does not store, or past the
    storage quota is a placeholder and a warning, and its note comes in all
    the same. Returns the stored names."""
    names = set()

    def store(mime, data):
        ext = _NOTE_IMAGE_EXT.get(mime)
        if ext is None:
            reason = f"{mime} is not a picture Gamma stores"
        elif len(data) > NOTE_IMAGE_MAX_BYTES:
            reason = f"larger than {NOTE_IMAGE_MAX_BYTES >> 20} MB"
        else:
            try:
                name, _ = store_file(ws, data, ext)
            except HTTPException as e:  # the quota (507)
                reason = e.detail
            else:
                names.add(name)
                return f"/api/uploads/{name}"
        report["warnings"].append({"title": item["title"], "reason": f"Note image not imported: {reason}"})
        return None

    for block in blocks:
        store_images(block, store)
    return names


def _pdf_stored(ws, digest, size) -> bool:
    """Whether the workspace stores the PDF ``digest`` already, as ``size``
    bytes, so the import need not stream it from the zip again. Such a
    file is re-dated, as storing it again would (``storage``'s dedup),
    under the upload GC's guard, so its purge keeps the file the page about
    to be written names."""
    name = f"{digest}.pdf"
    if stored_size(ws, name) != size:
        return False
    with upload_gc.guard(ws):
        return stored_touch(ws, name)


def _zotero_prepare(conn, ws, zf, item, dest, report) -> dict:
    """What the item makes of the library: the page it merges into
    (``_zotero_existing_page``) or a new one, with the properties, the
    block trees and the attachments it adds — filed in its collections'
    folders and labelled with its tags, ``dest``'s ids for them
    (``_commit_zotero``) after a merged page's own. A standalone note makes
    a note page: ``zotero_key`` and ``zotero_note`` its key, its body the
    note below its first block (the page's title); a merge into one
    refiles and relabels it only. Its files are stored here, outside any
    transaction, so a long import never holds the workspace's write lock
    over file writes: its PDF (unless the workspace stores it already), the
    pictures of the notes it adds, its other attachments."""
    digest = item["digest"]
    row = _zotero_existing_page(conn, item)

    created = row is None
    if created:
        block_id, old = new_block_id(), {}
    else:
        block_id, old = row[0], json.loads(row[1] or "{}")
    props = dict(old)
    note_page = bool(item.get("standalone_note"))
    pictures = set()  # the files the pictures of the PDF it attaches show

    if note_page:
        if created:
            props["zotero_key"] = props["zotero_note"] = item["key"]
    else:
        if props.get("doc_id") and digest and props["doc_id"] != digest:
            report["warnings"].append({"title": item["title"], "reason": "Existing page keeps its current PDF and annotations; the different exported PDF will not replace it."})

        # Attach the file only when the page doesn't already have one — a page
        # found by zotero_key keeps its existing PDF (and the highlights tied to it).
        if digest and not props.get("doc_id"):
            if not _pdf_stored(ws, digest, zf.getinfo(item["pdf_entry"]).file_size):
                with zf.open(item["pdf_entry"]) as src:  # streamed, like its other files
                    _, already_existed, _ = store_pdf_stream(ws, src)
                if not already_existed:
                    report["pdfs_stored"] += 1
            props["doc_id"] = digest
            # Its annotations, imported once the page is written
            # (``_zotero_done``), bring its pictures in with the files they
            # were drawn from: no chip of their own (``_zotero_extras``).
            if any(extra.get("ext") in IMAGE_MEDIA_TYPES for extra in item["extras"]) and (
                    path := find_upload_file(f"{digest}.pdf", ws)) is not None:
                pictures = _stamped_pictures(path)

        sets_meta = bool(item["meta"]["title"]) and not props.get("meta")
        if sets_meta:
            props["meta"] = item["meta"]
            # A Better BibTeX key travels with the record, so the .tex files that
            # already cite this paper keep working (zotero_import._citation_key).
            if item.get("cite_key") and not props.get("cite_key"):
                props["cite_key"] = item["cite_key"]
            if not props.get("bibtex"):
                props["bibtex"] = bibtex_mod.build_entry(item["meta"], props.get("cite_key") or "")
        # What Zotero holds beyond the record, for a later Zotero RDF export:
        # with the record it came with, or onto a page of the same work
        # that keeps none yet.
        if item.get("rdf") and (sets_meta or (not props.get("zotero_rdf")
                                              and same_work(props.get("meta"), item["meta"]))):
            props["zotero_rdf"] = item["rdf"]
        props["zotero_key"] = item["key"]

    under = dest["under"]
    # filed like any refiling (refiled): a merged page's own folders first,
    # a folder above a new one giving way; labelled on top of its own labels
    folders = filing(props, FOLDERS)
    for folder_id in [dest["folders"][tuple(path)] for path in item["folders"]] or ([under] if under else []):
        folders = refiled(conn, folders, folder_id)
    if folders:
        props[FOLDERS] = existing_in(conn, FOLDERS, folders)
    labels = [dest["labels"][tag] for tag in item["tags"]]
    if labels:
        props[LABELS] = existing_in(conn, LABELS, filing(props, LABELS) + labels)

    # The block trees the page gets, copies: storing their pictures rewrites
    # them, and an item may be prepared again (_zotero_write_new's retry).
    if note_page:
        notes, blocks = 0, copy.deepcopy(item["note"]) if created else []
    else:
        todo = item["notes"]
        if todo and not created:
            # A note is there under its key, or — a note Gamma exported — as the
            # block it was written from.
            existing = set()
            for bid, note_key in conn.execute(
                    "SELECT id, json_extract(properties,'$.zotero_note') FROM unified_blocks WHERE page_id=?",
                    (block_id,)):
                existing.add(f"{GAMMA_NOTE_KEY}{bid}")
                if note_key:
                    existing.add(note_key)
            todo = [n for n in todo if n["key"] not in existing]
        notes = len(todo)
        blocks = [{"content": n["text"], "properties": {"zotero_note": n["key"]},
                   "children": copy.deepcopy(n.get("children") or [])} for n in todo]
    images = _zotero_note_images(ws, item, blocks, report)
    # Cut where the editor's save stops: a merge's op batch would refuse a
    # longer block, and the item with it.
    pending = list(blocks)
    while pending:
        block = pending.pop()
        if len(block.get("content") or "") > MAX_CONTENT:
            block["content"] = block["content"][:MAX_CONTENT]
            report["warnings"].append({"title": item["title"],
                                       "reason": f"Note text cut: a block longer than {MAX_CONTENT:,} characters"})
        pending += block.get("children") or []
    extras, heading = _zotero_extras(conn, ws, zf, item, block_id, created, props.get("doc_id"), images | pictures,
                                     report)
    return {"item": item, "row": row, "block_id": block_id, "old": old, "props": props,
            "blocks": blocks, "notes": notes, "extras": extras, "heading": heading, "created": created,
            "folders": [dest["paths"][f] for f in filing(props, FOLDERS) if f in dest["paths"]]}


def _zotero_sources(item):
    """The ZIP members an item's page comes from, named as the review's
    source column names them: its PDF first, then its other attachments."""
    paths = ([item["pdf_entry"]] if item["pdf_entry"] else []) + [
        extra["entry"] for extra in item["extras"] if "entry" in extra]
    return [path.replace("\\", "/") for path in paths]


def _zotero_done(prep, report, uploads):
    """Count a written item into the report. Returns (block_id, pdf_path)
    when its PDF's embedded annotations should be imported afterwards, else
    None."""
    item, props, created = prep["item"], prep["props"], prep["created"]
    report["pages_created" if created else "pages_merged"] += 1
    report["notes_imported"] += prep["notes"]
    report["attachments_imported"] += len(prep["extras"])
    report["pages"].append({"id": prep["block_id"], "title": item["title"] if created else prep["row"][2],
                            "created": created, "kind": "pdf" if props.get("doc_id") else "page",
                            "folders": prep["folders"], "source_paths": _zotero_sources(item)})
    doc_id = props.get("doc_id")
    if doc_id:
        pdf_path = uploads / f"{doc_id}.pdf"
        if pdf_path.exists():
            return prep["block_id"], pdf_path
    return None


def _attachments_block(extras) -> dict:
    """The page's "Attachments" block holding the chips and links ``extras``."""
    return {"content": ATTACHMENTS_HEADING, "properties": {ATTACHMENTS_PROP: True}, "children": extras}


def _insert_ops(parent: str, blocks: list) -> list:
    """Insert ops writing the block trees ``blocks`` (``{content,
    properties?, children?}``) after ``parent``'s children: each block after
    its parent and its earlier siblings, appended to its parent, so the ops
    apply in order however a long batch is cut (MAX_OPS)."""
    ops, queue = [], [(parent, block) for block in blocks]
    for parent_id, block in queue:  # grows as it is read: breadth first
        block_id = new_block_id()
        ops.append({"op": "insert", "id": block_id, "parent": parent_id, "content": block.get("content", ""),
                    "props": block.get("properties") or {}})
        queue.extend((block_id, child) for child in block.get("children") or [])
    return ops


def _zotero_merge(conn, ws, prep, report, uploads, actor):
    """An item that merges into an existing page: an op batch by ``actor``,
    which the page's open tabs and the workspace's mirrors see, in MAX_OPS
    pieces when it is longer, all in one transaction (``apply_batches``):
    an item that fails leaves its page as it was. A note half written
    would stay so, as a later import takes the note its key finds for the
    whole note."""
    block_id = prep["block_id"]
    patch = props_patch(prep["old"], prep["props"])
    batch = ([{"op": "set", "id": block_id, "props": patch}] if patch else []) + _insert_ops(block_id, prep["blocks"])
    if prep["extras"]:
        if prep["heading"] is None:
            batch += _insert_ops(block_id, [_attachments_block(prep["extras"])])
        else:
            batch += _insert_ops(prep["heading"], prep["extras"])
    if batch:
        try:
            results = apply_batches(conn, [(block_id, batch[i:i + MAX_OPS])
                                           for i in range(0, len(batch), MAX_OPS)], actor=actor)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        after_commits(ws, conn, results)
    return _zotero_done(prep, report, uploads)


def _tree_uploads(title, props, blocks) -> set:
    """The stored files a new page (its ``title`` and ``props``) and its
    block trees name (``storage.upload_refs``)."""
    names, pending = upload_refs(title, props), list(blocks)
    while pending:
        block = pending.pop()
        names |= upload_refs(block.get("content", ""), block.get("properties"))
        pending += block.get("children") or []
    return names


def _zotero_write_new(conn, staged, report, uploads, actor):
    """Write new items' pages with their block trees (``insert_page_tree``):
    the notes, then the "Attachments" block. ZOTERO_PAGES_PER_COMMIT of
    them go in one short transaction, each page touched by ``actor``
    (``touch_page``, what the change feed lists), and the stored files they
    name claimed from the upload GC's orphans (``upload_gc.claim``), as
    ``create_page`` claims its PDF. Each page is looked for again under the
    lock: an item another import (or an earlier item of this batch) made a
    page for is not written but returned, to merge into that page. Returns
    ``(annotation jobs, items to merge)``."""
    write_lock(conn)
    written, retry = [], []
    try:
        for prep in staged:
            item = prep["item"]
            if _zotero_existing_page(conn, item) is not None:
                retry.append(item)
                continue
            blocks = prep["blocks"] + ([_attachments_block(prep["extras"])] if prep["extras"] else [])
            insert_page_tree(conn, prep["block_id"], item["title"], prep["props"], blocks, actor)
            upload_gc.claim(conn, _tree_uploads(item["title"], prep["props"], blocks))
            written.append(prep)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return [job for job in (_zotero_done(prep, report, uploads) for prep in written) if job], retry


def _open_zotero_zip(data):
    try:
        return zipfile.ZipFile(data)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="not a zip file — zip the exported folder and upload that")


def _zotero_plan(zf, digests=None):
    try:
        return plan_zotero_archive(zf, digests)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"could not read the Zotero export: {exc}")


# Sync endpoints: zip + PyPDF2 work is CPU-bound; the threadpool keeps the loop free.
@router.post("/import/zotero/preview")
def preview_zotero(request: Request, file: UploadFile = File(...), folder: str = Form("")):
    report = _preview_zotero(require_ws(request, write=True), file.file, _meta(file, folder))
    report.pop("stage", None)  # no review to keep it for
    return report


def _preview_zotero(ws, data, meta):
    """What importing the Zotero export would make of the library, without
    storing a file, making a folder or changing a page (merges within the
    zip simulated, as ``_zotero_existing_page`` finds them). A page's
    ``folders`` are the paths (names from the top) it would be filed in,
    ``attachments`` how many of its items' other attachments go under its
    "Attachments" block (ones a merged page holds already counted too, as
    its ``notes`` are); a standalone note's is a page with no PDF to miss.
    ``stage`` is the plan's PDF digests (``zotero_digests``), which spare a
    review's commit hashing the PDFs again."""
    with _open_zotero_zip(data) as zf:
        plan = _zotero_plan(zf)
    pages, by_digest, by_key, by_id = {}, {}, {}, {}
    # Simulate merges within this ZIP too, without storing files or changing pages.
    with connect_pages_db(ws) as conn:
        _, under_path = destination(conn, meta.get("folder", ""))
        paths = folder_paths(conn)
        for row in conn.execute("SELECT id, properties, content FROM unified_blocks WHERE parent_id='root'"):
            props = json.loads(row[1] or "{}")
            target = {"id": row[0], "title": row[2], "props": props, "exists": True,
                      "folders": [paths[f] for f in filing(props, FOLDERS) if f in paths]}
            by_id[row[0]] = target
            if props.get("doc_id"):
                by_digest.setdefault(props["doc_id"], target)
            if props.get("zotero_key"):
                by_key.setdefault(props["zotero_key"], []).append(target)
        for index, item in enumerate(plan["items"]):
            # _zotero_existing_page's order: the page Gamma exported it from, the PDF, the key
            target = ((by_id.get(item["key"][len(GAMMA_PAGE_KEY):]) if item["key"].startswith(GAMMA_PAGE_KEY)
                       else None) or by_digest.get(item["digest"])
                      or next((t for t in by_key.get(item["key"], ())
                               if _zotero_key_merges(item, t["props"], t["title"])), None))
            created = target is None
            if created:
                target = {"id": f"new:{index}", "title": item["title"], "props": {}, "exists": False, "folders": []}
            props = target["props"]
            folders = target["folders"]
            for path in _zotero_paths(item, under_path):
                folders = refiled_paths(folders, path)
            target["folders"] = folders
            kind = "pdf" if props.get("doc_id") or item["digest"] else "page"
            warnings = list(item["warnings"])
            if props.get("doc_id") and item["digest"] and props["doc_id"] != item["digest"]:
                warning = {"title": item["title"], "reason": "Existing page keeps its current PDF and annotations; the different exported PDF will not replace it."}
                warnings.append(warning)
                plan["warnings"].append(warning)
            prior = pages.get(target["id"])
            sources = _zotero_sources(item)
            page = {"key": item["key"], "title": target["title"],
                    "folders": folders, "kind": kind, "action": "merge" if target["exists"] else "create",
                    "existing_id": target["id"] if target["exists"] else None, "source_path": item["pdf_entry"],
                    "notes": len(item["notes"]), "attachments": len(item["extras"]),
                    "tags": item["tags"], "warnings": warnings,
                    "selection_ids": [item["selection_id"]],
                    "missing": not item["standalone_note"] and (
                        not item["pdf_entry"] or any("PDF missing from ZIP" in w["reason"] for w in warnings)),
                    "source_paths": sources}
            if prior:
                page["warnings"] = prior["warnings"] + warnings
                page["notes"] += prior["notes"]
                page["attachments"] += prior["attachments"]
                page["source_path"] = prior["source_path"] or item["pdf_entry"]
                page["selection_ids"] = prior["selection_ids"] + page["selection_ids"]
                page["source_paths"] = list(dict.fromkeys(prior["source_paths"] + page["source_paths"]))
                page["missing"] = prior["missing"] or page["missing"]
            pages[target["id"]] = page
            if item["digest"] and not props.get("doc_id"):
                props["doc_id"] = item["digest"]
                by_digest[item["digest"]] = target
            if not item["standalone_note"] and item["meta"]["title"] and not props.get("meta"):
                props["meta"] = item["meta"]  # what a later item's key is matched with
            old_key = props.get("zotero_key")
            if item["key"] != old_key and (created or not item["standalone_note"]):  # a note page keeps its key
                if old_key:
                    by_key[old_key] = [t for t in by_key.get(old_key, []) if t is not target]
                props["zotero_key"] = item["key"]
                if item["key"]:
                    by_key.setdefault(item["key"], []).append(target)
    return {"ok": True, "manifest": plan["manifest"], "entries": plan["entries"],
            "pages": list(pages.values()), "warnings": plan["warnings"], "folder": meta.get("folder", ""),
            "stage": {"zotero_digests": plan["digests"]}}


@router.post("/import/zotero")
def import_zotero(request: Request, file: UploadFile = File(...),
                  strip: bool = Form(False), folder: str = Form(""), selected: str | None = Form(None)):
    ws = require_ws(request, write=True)
    return _commit_zotero(ws, actor_of(request), file.file, _meta(file, folder, strip), parse_selection(selected))


def _commit_zotero(ws, actor, data, meta, selection, progress=jobs.no_progress):
    """Import the Zotero export's selected items (None: all): the folders
    of their collections (below the destination folder) and the labels of
    their tags made first (``ops.ensure_filing``), then pages upserted item
    by item, new ones written ZOTERO_PAGES_PER_COMMIT at a time, then the
    annotations embedded in their PDFs. A stopped job keeps what it wrote
    (the staged new pages are written first). A review's commit hashes no
    PDF its preview hashed (``meta["zotero_digests"]``, its ``stage``)."""
    strip = bool(meta.get("strip"))
    with _open_zotero_zip(data) as zf:
        plan = _zotero_plan(zf, meta.get("zotero_digests"))
        validate_selection(selection, (i["selection_id"] for i in plan["items"]))
        items = [i for i in plan["items"] if selection is None or i["selection_id"] in selection]
        uploads = ws_uploads_dir(ws)
        report = {"items": len(items), "pages_created": 0, "pages_merged": 0,
                  "pdfs_stored": 0, "annotations_imported": 0, "notes_imported": 0, "attachments_imported": 0,
                  "pages": [], "skipped": [], "warnings": selected_warnings(plan["warnings"], selection)}
        annot_jobs = []
        with connect_pages_db(ws) as conn:
            under, _ = destination(conn, meta.get("folder", ""))
            folder_ids, label_ids = ensure_filing(
                ws, conn, paths=[path for item in items for path in item["folders"]],
                labels=[tag for item in items for tag in item["tags"]], under=under, actor=actor)
            dest = {"under": under, "folders": folder_ids, "labels": label_ids, "paths": folder_paths(conn)}
            staged = []  # new pages, written ZOTERO_PAGES_PER_COMMIT at a time

            def skip(item, reason):
                report["skipped"].append({"title": item["title"], "reason": reason})

            def flush():
                if not staged:
                    return
                batch = staged[:]
                staged.clear()
                try:
                    written, retry = _zotero_write_new(conn, batch, report, uploads, actor)
                except Exception as e:
                    log.warning(f"[zotero] {len(batch)} new page(s) failed: {e}")
                    for prep in batch:
                        skip(prep["item"], str(e))
                    return
                annot_jobs.extend(written)
                for item in retry:  # made meanwhile: merge into that page
                    run(item)

            def run(item):
                try:
                    prep = _zotero_prepare(conn, ws, zf, item, dest, report)
                    if prep["created"]:
                        staged.append(prep)
                        if len(staged) >= ZOTERO_PAGES_PER_COMMIT:
                            flush()
                        return
                    flush()  # the pages before it first: the report keeps the export's order
                    job = _zotero_merge(conn, ws, prep, report, uploads, actor)
                    if job:
                        annot_jobs.append(job)
                except HTTPException as e:  # the quota (507) skips the item
                    skip(item, str(e.detail))
                except Exception as e:
                    log.warning(f"[zotero] item '{item['title'][:80]}' failed: {e}")
                    skip(item, str(e))

            try:
                for n, item in enumerate(items):
                    progress(done=n, total=len(items), unit="items", item=item["title"])
                    run(item)
            finally:
                flush()  # a stop keeps the pages whose files are already stored

    # Annotations after the pages are committed — import_embedded_annotations
    # opens its own connections.
    for n, (block_id, pdf_path) in enumerate(annot_jobs):
        progress(phase="annotations", done=n, total=len(annot_jobs), unit="files")
        try:
            result = import_embedded_annotations(ws, block_id, pdf_path, strip, actor)
            report["annotations_imported"] += result["imported"]
        except Exception as e:
            log.warning(f"[zotero] annotations for {pdf_path.name} failed: {e}")
            report["warnings"].append({"title": pdf_path.name, "reason": f"annotations: {e}"})

    log.info(f"[zotero] import: {report['items']} items, "
             f"{report['pages_created']} new, {report['pages_merged']} merged, "
             f"{report['annotations_imported']} annotations, {len(report['skipped'])} skipped")
    return {"ok": True, **report}
