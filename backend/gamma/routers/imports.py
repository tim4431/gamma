"""Imports: Logseq PDF-highlight exports (PDF + EDN + optional MD), annotations
embedded in the PDF itself (e.g. saved by SumatraPDF/Acrobat/Zotero), Markdown
notes, and whole libraries — a Zotero RDF export, a zip of Markdown notes, a
Gamma export — through the reviewed import: the upload is staged and
previewed (``/import/review``), then the chosen items are imported by a
background job (``POST /api/jobs/import``, gamma/jobs.py)."""

import hashlib
import html
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from fractional_indexing import generate_key_between, generate_n_keys_between

from .. import bibtex as bibtex_mod
from .. import import_staging, jobs
from ..auth import actor_of, require_user_id, require_ws
from ..db import connect_pages_db, page_now, pdf_upload_path, ws_uploads_dir
from ..blocks_store import (FOLDERS, LABELS, STORED_COLUMNS, create_page, existing_in, filing, folder_paths,
                            last_child_position, new_block_id, page_for_doc, page_root_id, refiled, refiled_paths,
                            touch_page, write_lock)
from ..highlights import position as highlight_position
from ..logbuf import log
from ..ops import MAX_OPS, after_commit, apply_ops, commit_ops, ensure_filing, note_reload, props_patch
from ..markdown_import import MAX_MARKDOWN_BYTES, md_to_blocks
from ..markdown_zip_import import import_markdown_zip, markdown_page
from ..ink import InkError, dumps as ink_dumps, from_pdf_ink, parse_ink, pdf_position as ink_position
from ..pdf_export import (TEXT_BOX_TYPES, _resolve, annotation_key, annotation_shown, display_size,
                          drop_annotations, first_rect, page_frame, pdf_point_to_viewer, reply_parent)
from ..text_box import escape_markdown, markdown_of, measure, normalize_text_box, plain_text
from ..storage import display_filename, is_pdf, pdf_url, store_file, store_pdf
from ..logseq_import import (
    edn_highlight_position,
    edn_highlight_to_block,
    map_color,
    md_to_ordered_blocks,
    parse_edn,
    parse_logseq_md,
)
from ..zotero_import import html_note_text, plan_zotero_archive
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
# (``ops.ensure_filing``).

def _review_source(source):
    sources = {
        "zotero": (_preview_zotero, _commit_zotero),
        "markdown-zip": (_preview_markdown_zip, _commit_markdown_zip),
        "markdown-file": (_preview_markdown_file, _commit_markdown_file),
        "gamma": (_preview_gamma, _commit_gamma),
    }
    if source not in sources:
        raise HTTPException(status_code=400, detail="unsupported import source")
    return sources[source]


@router.post("/import/review")
def upload_import_review(request: Request, file: UploadFile = File(...), source: str = Form(...),
                         folder: str = Form(""), strip: bool = Form(False)):
    """Stage the upload and answer the source's preview with the review's
    ``review_id``: nothing is imported until ``POST /api/jobs/import``."""
    ws = require_ws(request, write=True)
    preview, _ = _review_source(source)
    token = import_staging.create(file, user_id=request.state.user_id, ws=ws, source=source, folder=folder, strip=strip)
    try:
        path, metadata = import_staging.get(token, request.state.user_id, ws)
        with (path / "upload").open("rb") as data:
            report = preview(ws, data, metadata)
        return {**report, "review_id": token}
    except Exception:
        import_staging.discard(token, request.state.user_id, ws)
        raise


@router.delete("/import/review/{token}")
def discard_import_review(token: str, request: Request):
    ws = require_ws(request, write=True)
    import_staging.discard(token, request.state.user_id, ws)
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
    path, metadata = import_staging.get(token, user_id, ws)
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
    pdf_bytes = pdf.file.read()
    if not is_pdf(pdf_bytes):
        raise HTTPException(status_code=400, detail="not a valid PDF")
    digest, _ = store_pdf(ws, pdf_bytes)

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
    tree-edit/autosave path."""
    require_user_id(request)
    if len(payload.text.encode("utf-8", errors="ignore")) > MAX_MARKDOWN_BYTES:
        raise HTTPException(status_code=413, detail="text exceeds 5 MB")
    return {"blocks": md_to_blocks(payload.text)}


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
_IMPORT_TYPES = _MARKUP_TYPES | _NOTE_TYPES | _AREA_TYPES | _INK_TYPES


def _ink_from_annotation(obj, pnum: int, pw: float, ph: float, contents: str):
    """One /Ink annotation → an importer record carrying the parsed ink file
    (``kind: "ink"``). A Gamma export's ``/GammaInk`` private key restores
    pressure and time; foreign ink is polylines at the annotation's width."""
    ink_list = [[float(_resolve(v)) for v in (_resolve(path) or [])]
                for path in (_resolve(obj.get("/InkList")) or [])]
    bs = _resolve(obj.get("/BS")) or {}
    try:
        width = float(_resolve(bs.get("/W", 1)))
    except (TypeError, ValueError):
        width = 1.0
    color = "#1f1f1f"
    c = _resolve(obj.get("/C"))
    try:
        if c is not None and len(c) == 3:
            color = "#%02x%02x%02x" % tuple(min(255, max(0, int(round(float(_resolve(v)) * 255)))) for v in c)
    except (TypeError, ValueError):
        pass
    try:
        alpha = min(max(float(_resolve(obj.get("/CA"))), 0.05), 1.0)
    except (TypeError, ValueError):
        alpha = 1.0
    private = _resolve(obj.get("/GammaInk"))
    try:
        ink = parse_ink(from_pdf_ink(ink_list, width, color, alpha, pnum, pw, ph,
                                     str(private) if private else None))
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


def _on_page(box: dict, disp_w: float, disp_h: float) -> dict:
    """The box kept inside the page, as the client keeps a moved box: no
    wider or taller than the page, its corner moved in."""
    w, h = min(box["w"], disp_w), min(box["h"], disp_h)
    return normalize_text_box({**box, "w": w, "h": h, "x": min(max(box["x"], 0), disp_w - w),
                               "y": min(max(box["y"], 0), disp_h - h)})


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
    /RC reduced to text. A /Text sticky note becomes a note-yellow box at
    its icon's top-left, as wide as its text."""
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
    if not md.strip():
        return None
    return {"key": key, "page": pnum, "content": md, "kind": "text_box",
            "box": _on_page(normalize_text_box(box), disp_w, disp_h)}


def _page_text_chunks(page):
    """(x, y, text) per text chunk in PDF user space — best-effort, used to
    recover the quoted text under a markup annotation."""
    chunks = []

    def visitor(text, cm, tm, font_dict, font_size):
        if text and text.strip():
            # Translation-only composition; fine for typical body text.
            chunks.append((tm[4] + cm[4], tm[5] + cm[5], text))

    try:
        page.extract_text(visitor_text=visitor)
    except Exception:
        return []
    return chunks


def _extract_pdf_annotations(reader, quotes=True):
    """The annotations of ``reader``'s pages that become blocks, as records
    in page order: ``key`` (the ``imported_annot``), ``page``, ``content``
    and the kind's fields, ``annot`` (the annotation's dictionary) and
    ``replies``. Only what the page shows counts (``annotation_shown``: no
    Hidden or NoView annotation, no review-state stamp). A reply (/IRT) is
    no block of its own but a note under the annotation it answers, in
    ``replies`` in page order: ``{key, legacy, content, annot, replies}``,
    ``legacy`` the key it had when replies were imported as annotations of
    their own, ``key`` that with its /NM (or its place in /Annots), since a
    reply shares its parent's rectangle. A reply to something not imported
    is left out. ``quotes=False`` skips reading the text under highlights,
    for a caller that only needs which annotations make blocks."""
    found, replies, records = [], [], {}
    for pnum, page in enumerate(reader.pages, start=1):
        try:
            annots = _resolve(page.get("/Annots")) or []
        except Exception:
            continue
        if not annots:
            continue
        mb = page.mediabox
        pw, ph = float(mb.width), float(mb.height)
        page_text = {} if quotes else {"chunks": []}  # read once, when a quote needs it
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
                else:
                    record = _mark_from_annotation(obj, subtype, pnum, page, pw, ph, contents, page_text)
                if record:
                    record.update(annot=obj, replies=[])
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


def _mark_from_annotation(obj, subtype, pnum, page, pw, ph, contents, page_text):
    """A markup, square or circle annotation → a highlight record, or None
    without a rectangle. ``page_text`` caches the page's text for the quote
    (``_page_text_chunks``, read once per page under ``"chunks"``)."""
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
    quote = ""
    if subtype in _MARKUP_TYPES:
        if "chunks" not in page_text:
            page_text["chunks"] = _page_text_chunks(page)
        picked = [t for (x, y, t) in page_text["chunks"]
                  if any(qx1 - 2 <= x <= qx2 + 2 and qy1 - 3 <= y <= qy2 + 3
                         for (qx1, qy1, qx2, qy2) in quads)]
        quote = re.sub(r"\s+", " ", " ".join(picked)).strip()[:1000]
    color = "rgba(255, 226, 143, 0.65)"
    c = _resolve(obj.get("/C"))
    try:
        # /CA is the annotation's own opacity — honoring it makes a
        # Gamma export → re-import round-trip the exact shade.
        alpha = 0.45
        ca = _resolve(obj.get("/CA"))
        if ca is not None:
            alpha = min(max(float(ca), 0.05), 1.0)
        if c is not None and len(c) == 3:
            color = (f"rgba({int(float(_resolve(c[0])) * 255)}, {int(float(_resolve(c[1])) * 255)}, "
                     f"{int(float(_resolve(c[2])) * 255)}, {round(alpha, 3)})")
    except Exception:
        pass
    # Flipped to top-left origin (what the viewer stores), in the page's points
    position = highlight_position(pnum, pw, ph, [(q[0], ph - q[3], q[2], ph - q[1]) for q in quads],
                                  area=subtype in _AREA_TYPES)
    return {"key": annotation_key(pnum, subtype, quads[0]), "content": contents,
            "quote": quote, "color": color, "position": position}


def _strip_embedded_annotations(pdf_path) -> tuple[int, set]:
    """Rewrite the stored PDF without the annotations the import turns into
    blocks (``_extract_pdf_annotations``, folded replies included), with
    their threads and /Popup windows (``pdf_export.drop_annotations``), so
    the viewer's canvas doesn't paint them under Gamma's own. Everything
    else stays: links, kinds not imported, hidden ones, and those that make
    no block (a note with no text). Returns the number of annotations
    removed and the keys of the blocks they make (a reply's legacy key too).

    Note the file keeps its content-hash name even though its bytes change —
    the name is only a key (``doc_id`` property), never re-derived."""
    from PyPDF2 import PdfReader, PdfWriter

    reader = PdfReader(str(pdf_path))
    doomed, keys = set(), set()

    def take(record):
        doomed.add(id(record["annot"]))
        keys.update(k for k in (record["key"], record.get("legacy")) if k)
        for reply in record["replies"]:
            take(reply)

    for record in _extract_pdf_annotations(reader, quotes=False):
        take(record)
    # Off the reader's pages, before the copy: PdfWriter.append clones each
    # page's /Annots as the reader holds them.
    removed = sum(drop_annotations(page, doomed) for page in reader.pages) if doomed else 0
    if not removed:
        return 0, set()
    writer = PdfWriter()
    writer.append(reader)
    # Atomic swap so a concurrent download never sees a half-written file.
    fd, tmp_name = tempfile.mkstemp(suffix=".pdf", dir=str(pdf_path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            writer.write(f)
        os.replace(tmp_name, str(pdf_path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return removed, keys


def _imported_blocks(conn, block_id: str) -> list:
    """``(id, imported_annot, annot_stripped)`` of the blocks an import made
    under ``block_id``, at any depth: a reply's note sits under its
    parent's block, and a block may have been moved in since."""
    return conn.execute(
        "WITH RECURSIVE sub(id) AS (SELECT id FROM unified_blocks WHERE parent_id = ? "
        "UNION ALL SELECT b.id FROM unified_blocks b JOIN sub ON b.parent_id = sub.id) "
        "SELECT b.id, json_extract(b.properties, '$.imported_annot'), "
        "json_extract(b.properties, '$.annot_stripped') FROM unified_blocks b JOIN sub ON b.id = sub.id "
        "WHERE json_extract(b.properties, '$.imported_annot') IS NOT NULL", (block_id,)).fetchall()


class PdfAnnotsRequest(BaseModel):
    block_id: str
    doc_id: str
    # Settings → "Embedded PDF annotations": strip them from the stored file
    # after importing (the alternative is hiding them viewer-side).
    strip: bool = False


def import_embedded_annotations(ws: str, block_id: str, pdf_path, strip: bool, actor: str = "") -> dict:
    """Extract the annotations embedded in the stored PDF and add the missing
    ones as highlight, handwriting and text-box blocks under ``block_id``,
    each reply a note under the block of the annotation it answers
    (idempotent via the stable ``imported_annot`` key: a reply is there
    under its own key or the legacy one, from before replies were notes),
    then optionally strip the originals from the file. ``found`` counts the
    annotations that make blocks, replies included. Shared by the per-paper
    endpoint below and the Zotero library import."""
    from PyPDF2 import PdfReader
    reader = PdfReader(str(pdf_path))
    found = _extract_pdf_annotations(reader)
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
                if f.get("kind") == "ink":
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
                insert(f, block_id, pos, props)
        for f in found:
            if f.get("replies"):
                add_replies(made.get(id(f)) or existing[f["key"]], f["replies"])
        if inserted:
            note_reload(ws, conn, page_id, actor)  # logs the reload, touches the page, commits: one transaction

    # Strip AFTER the blocks are committed: if the rewrite fails the file is
    # untouched and the import still stands; a re-run can strip again.
    stripped = 0
    if strip:
        keys = set()
        try:
            stripped, keys = _strip_embedded_annotations(pdf_path)
        except Exception as e:
            log.warning(f"[pdf-annots] could not strip annotations from {pdf_path.name}: {e}")
        if stripped:
            # The embedded originals are gone from the file, so PDF export must
            # start writing these blocks again (it skips imported ones only
            # while the original annotation still lives in the PDF).
            with connect_pages_db(ws) as conn:
                ids = [bid for bid, key, done in _imported_blocks(conn, block_id) if key in keys and not done]
            for i in range(0, len(ids), MAX_OPS):
                commit_ops(ws, page_id, [{"op": "set", "id": bid, "props": {"annot_stripped": True}}
                                         for bid in ids[i:i + MAX_OPS]], actor=actor)
    return {"found": count(found), "imported": inserted, "stripped": stripped}


# Sync endpoint: PyPDF2 parsing is CPU-bound; the threadpool keeps the loop free.
@router.post("/import/pdf-annotations")
def import_pdf_annotations(payload: PdfAnnotsRequest, request: Request):
    ws = require_ws(request, write=True)
    try:
        pdf_path = pdf_upload_path(ws, payload.doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid document id")
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not stored on the server")
    try:
        result = import_embedded_annotations(ws, payload.block_id, pdf_path, payload.strip,
                                             request.state.user_id or "")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"could not read PDF annotations: {e}")
    return {"ok": True, **result}


# --- Zotero library import ----------------------------------------------------
# A zip of Zotero's File → Export Library → "Zotero RDF" (with "Export Files",
# "Export Notes" and "Include Annotations"). Items become pages, collections
# folders (below the destination folder), tags labels, item notes child
# blocks; reader annotations
# arrive embedded in the exported PDF copies and go through
# import_embedded_annotations above. Idempotent: pages are keyed by the file
# hash and by properties.zotero_key (export bytes change between exports —
# Zotero re-embeds annotations — so the item key is what survives a re-export).


def _zotero_existing_page(conn, item):
    row = page_for_doc(conn, item["digest"], "id, properties, content")
    if row is None and item["key"]:
        row = conn.execute(
            "SELECT id, properties, content FROM unified_blocks WHERE parent_id='root' "
            "AND json_extract(properties,'$.zotero_key') = ?", (item["key"],)).fetchone()
    return row


# New pages a Zotero import writes in one transaction: few commits, and each
# hold of the write lock stays short (their files are stored before it).
ZOTERO_PAGES_PER_COMMIT = 50


def _zotero_paths(item, under_path):
    """The folder paths (names from the top) an item is filed in: its
    collections' below the destination (``under_path``), else the
    destination itself."""
    return [under_path + path for path in item["folders"]] or ([under_path] if under_path else [])


def _zotero_prepare(conn, ws, zf, item, dest, report) -> dict:
    """What the item makes of the library: the page it merges into (found
    by PDF, else by Zotero key) or a new one, with the properties and the
    notes it adds — filed in its collections' folders and labelled with its
    tags, ``dest``'s ids for them (``_commit_zotero``) after a merged page's
    own. Its PDF is stored here, outside any transaction, so a long import
    never holds the workspace's write lock over file writes."""
    digest = item["digest"]
    row = _zotero_existing_page(conn, item)

    created = row is None
    if created:
        block_id, old = new_block_id(), {}
    else:
        block_id, old = row[0], json.loads(row[1] or "{}")
    props = dict(old)

    if props.get("doc_id") and digest and props["doc_id"] != digest:
        report["warnings"].append({"title": item["title"], "reason": "Existing page keeps its current PDF and annotations; the different exported PDF will not replace it."})

    # Attach the file only when the page doesn't already have one — a page
    # found by zotero_key keeps its existing PDF (and the highlights tied to it).
    if digest and not props.get("doc_id"):
        _, already_existed = store_pdf(ws, zf.read(item["pdf_entry"]))
        if not already_existed:
            report["pdfs_stored"] += 1
        props["doc_id"] = digest

    if item["meta"]["title"] and not props.get("meta"):
        props["meta"] = item["meta"]
        # A Better BibTeX key travels with the record, so the .tex files that
        # already cite this paper keep working (zotero_import._citation_key).
        if item.get("cite_key") and not props.get("cite_key"):
            props["cite_key"] = item["cite_key"]
        if not props.get("bibtex"):
            props["bibtex"] = bibtex_mod.build_entry(item["meta"], props.get("cite_key") or "")
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

    todo = item["notes"]
    if todo and not created:
        existing = {r[0] for r in conn.execute(
            "SELECT json_extract(properties,'$.zotero_note') FROM unified_blocks WHERE parent_id=?",
            (block_id,)).fetchall() if r[0]}
        todo = [n for n in todo if n["key"] not in existing]
    return {"item": item, "row": row, "block_id": block_id, "old": old, "props": props,
            "todo": todo, "created": created,
            "folders": [dest["paths"][f] for f in filing(props, FOLDERS) if f in dest["paths"]]}


def _zotero_done(prep, report, uploads):
    """Count a written item into the report. Returns (block_id, pdf_path)
    when its PDF's embedded annotations should be imported afterwards, else
    None."""
    item, props, created = prep["item"], prep["props"], prep["created"]
    report["pages_created" if created else "pages_merged"] += 1
    report["notes_imported"] += len(prep["todo"])
    report["pages"].append({"id": prep["block_id"], "title": item["title"] if created else prep["row"][2],
                            "created": created, "kind": "pdf" if props.get("doc_id") else "page",
                            "folders": prep["folders"]})
    doc_id = props.get("doc_id")
    if doc_id:
        pdf_path = uploads / f"{doc_id}.pdf"
        if pdf_path.exists():
            return prep["block_id"], pdf_path
    return None


def _zotero_merge(conn, ws, prep, report, uploads, actor):
    """An item that merges into an existing page: an op batch by ``actor``
    (``apply_ops``), which the page's open tabs and the workspace's mirrors
    see."""
    block_id = prep["block_id"]
    patch = props_patch(prep["old"], prep["props"])
    batch = ([{"op": "set", "id": block_id, "props": patch}] if patch else []) + [
        {"op": "insert", "id": new_block_id(), "parent": block_id,
         "content": note["text"], "props": {"zotero_note": note["key"]}} for note in prep["todo"]]
    for i in range(0, len(batch), MAX_OPS):
        after_commit(ws, conn, apply_ops(conn, block_id, batch[i:i + MAX_OPS], actor=actor))
    return _zotero_done(prep, report, uploads)


def _zotero_write_new(conn, staged, report, uploads, actor):
    """Write new items' pages and notes, ZOTERO_PAGES_PER_COMMIT of them in
    one short transaction, each page touched by ``actor`` (``touch_page``,
    what the change feed lists). Each page is looked for again under the
    lock: an item another import (or an earlier item of this batch) made a
    page for is not written but returned, to merge into that page. Returns
    ``(annotation jobs, items to merge)``."""
    write_lock(conn)
    written, retry = [], []
    try:
        now = page_now()
        pos = last_child_position(conn, "root")
        for prep in staged:
            item = prep["item"]
            if _zotero_existing_page(conn, item) is not None:
                retry.append(item)
                continue
            pos = generate_key_between(pos, None)
            conn.execute(
                f"INSERT INTO unified_blocks ({STORED_COLUMNS}) VALUES (?,'root',?,?,?,?,?,?)",
                (prep["block_id"], pos, item["title"], json.dumps(prep["props"]), now, now, prep["block_id"]))
            todo = prep["todo"]
            positions = generate_n_keys_between(None, None, n=len(todo)) if todo else []
            for note, note_pos in zip(todo, positions):
                conn.execute(
                    f"INSERT INTO unified_blocks ({STORED_COLUMNS}) VALUES (?,?,?,?,?,?,?,?)",
                    (new_block_id(), prep["block_id"], note_pos, note["text"],
                     json.dumps({"zotero_note": note["key"]}), now, now, prep["block_id"]))
            touch_page(conn, prep["block_id"], actor, now=now)
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


def _zotero_plan(zf):
    try:
        return plan_zotero_archive(zf)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"could not read the Zotero export: {exc}")


# Sync endpoints: zip + PyPDF2 work is CPU-bound; the threadpool keeps the loop free.
@router.post("/import/zotero/preview")
def preview_zotero(request: Request, file: UploadFile = File(...), folder: str = Form("")):
    return _preview_zotero(require_ws(request, write=True), file.file, _meta(file, folder))


def _preview_zotero(ws, data, meta):
    """What importing the Zotero export would make of the library, without
    storing a file, making a folder or changing a page (merges within the
    zip simulated). A page's ``folders`` are the paths (names from the top)
    it would be filed in."""
    with _open_zotero_zip(data) as zf:
        plan = _zotero_plan(zf)
    pages, by_digest, by_key = {}, {}, {}
    # Simulate merges within this ZIP too, without storing files or changing pages.
    with connect_pages_db(ws) as conn:
        _, under_path = destination(conn, meta.get("folder", ""))
        paths = folder_paths(conn)
        for row in conn.execute("SELECT id, properties, content FROM unified_blocks WHERE parent_id='root'"):
            props = json.loads(row[1] or "{}")
            target = {"id": row[0], "title": row[2], "props": props, "exists": True,
                      "folders": [paths[f] for f in filing(props, FOLDERS) if f in paths]}
            if props.get("doc_id"):
                by_digest.setdefault(props["doc_id"], target)
            if props.get("zotero_key"):
                by_key.setdefault(props["zotero_key"], target)
        for index, item in enumerate(plan["items"]):
            target = by_digest.get(item["digest"]) or by_key.get(item["key"])
            if target is None:
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
            page = {"key": item["key"], "title": target["title"],
                    "folders": folders, "kind": kind, "action": "merge" if target["exists"] else "create",
                    "existing_id": target["id"] if target["exists"] else None, "source_path": item["pdf_entry"],
                    "notes": len(item["notes"]), "tags": item["tags"], "warnings": warnings,
                    "selection_ids": [item["selection_id"]],
                    "missing": not bool(item["pdf_entry"]) or any("PDF missing from ZIP" in w["reason"] for w in warnings),
                    "source_paths": [item["pdf_entry"]] if item["pdf_entry"] else []}
            if prior:
                page["warnings"] = prior["warnings"] + warnings
                page["notes"] += prior["notes"]
                page["source_path"] = prior["source_path"] or item["pdf_entry"]
                page["selection_ids"] = prior["selection_ids"] + page["selection_ids"]
                page["source_paths"] = list(dict.fromkeys(prior["source_paths"] + page["source_paths"]))
                page["missing"] = prior["missing"] or page["missing"]
            pages[target["id"]] = page
            if item["digest"] and not props.get("doc_id"):
                props["doc_id"] = item["digest"]
                by_digest[item["digest"]] = target
            old_key = props.get("zotero_key")
            if old_key and by_key.get(old_key) is target:
                del by_key[old_key]
            props["zotero_key"] = item["key"]
            if item["key"]:
                by_key[item["key"]] = target
    return {"ok": True, "manifest": plan["manifest"], "entries": plan["entries"],
            "pages": list(pages.values()), "warnings": plan["warnings"], "folder": meta.get("folder", "")}


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
    (the staged new pages are written first)."""
    strip = bool(meta.get("strip"))
    with _open_zotero_zip(data) as zf:
        plan = _zotero_plan(zf)
        validate_selection(selection, (i["selection_id"] for i in plan["items"]))
        items = [i for i in plan["items"] if selection is None or i["selection_id"] in selection]
        uploads = ws_uploads_dir(ws)
        uploads.mkdir(parents=True, exist_ok=True)
        report = {"items": len(items), "pages_created": 0, "pages_merged": 0,
                  "pdfs_stored": 0, "annotations_imported": 0, "notes_imported": 0,
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
                except HTTPException as e:  # per-file quota (413/507) skips the item
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
