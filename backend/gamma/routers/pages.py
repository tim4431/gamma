"""Page-first endpoints (/api/pages*): create a page, look pages up by file
or by id, make a page from a stored markdown file or from a stored file of
any other format (``by-file``), attach, replace or detach a page's document,
and put a work folder's file in the library or take it out. A page is a
root block that may CARRY a document (a PDF, a picture, a text file, a
recording, any stored file: docs/dev/documents.md); the document is an
action on an existing page, not the way pages come into being. (``POST
/api/blocks/by-doc/{doc_id}`` is the lookup-or-create BY ATTACHMENT path for
PDF ingest and the extension's dedup; ``by-file`` is the same for a stored
file named with its extension.)

Every endpoint but ``by-docs`` and ``summaries`` (any member, or for
``summaries`` a share, which reads the pages it reaches), ``family`` (any
member, or a share that reaches the page) and ``POST /pages`` needs an
editor of the workspace (``require_ws(write=True)``): a share token never
changes a page's attachment (page properties stay the owner's, same rule
as PUT /blocks/{id} under a share). ``POST /pages`` also takes an edit
share's token, for a page inside a page the share reaches and nothing else.
"""

import json
import os
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import config, folder_links, highlight_relocate, jobs, linked_files, pdf_meta, publish, storage, upload_gc
from ..auth import (DISK_CLIENT, actor_of, link_ratelimit, require_user_id, require_ws, require_ws_writer, resolve_ws,
                    share_scope, writer_client)
from ..blocks_store import (
    BLOCK_COLUMNS,
    FOLDERS,
    PARENT_PAGE,
    TRASH,
    assert_block_in_scope,
    block_to_dict,
    create_page,
    filing,
    get_or_create_doc_page,
    load_props,
    page_ancestors,
    page_for_file,
    page_parents,
    page_root_id,
    pages_for_docs,
    pages_for_files,
    sub_pages,
    valid_block_id,
    write_lock,
)
from ..db import connect_pages_db, connect_users_db, get_profile, safe_doc_id
from ..documents import text_anchors
from ..documents.formats import SNIFF_BYTES, stored_format
from ..documents.model import (FORMAT_EXT, document, document_props, document_title, file_hash, in_place, is_listed,
                               is_pdf, own_hash, page_document, parse_upload_url, stored_name, upload_url)
from ..json_response import OrjsonResponse
from ..markdown_zip_import import markdown_page
from ..logbuf import log
from ..storage import UNSTORED, find_upload_file
from ..ops import MAX_OPS, OpError, after_commit, after_commits, apply_batches, apply_ops, props_patch
from ..search import index as text_index
from ..work_folders.library import work_folder_of
from ..work_folders.marks import Tree, filing_targets
from .blocks import SUMMARY_COLUMNS, op_refusal, page_preview, page_summary, summaries
from .imports import originals_gone

router = APIRouter(prefix="/api", tags=["pages"])

# What an attach replaces and a detach removes: the page's document, and the
# name of the Markdown file a page was imported from (markdown_zip_import).
ATTACHMENT_KEYS = ("doc", "original_filename")
SHARE_PAGES_PER_5_MIN = 30  # pages a link visitor may make through an edit share, per IP
RELOCATE = "relocate-highlights"  # the job that places a replaced file's highlights (_start_relocation)
NOT_STORED = "the PDF is not stored on the server"  # why no run places the highlights of a PDF shown from a URL alone
LISTED = "the file is listed without its bytes"  # why no run places the ranges of a text file that turned listed
# Why a page of a Zotero link takes no PDF from a person while the link brings Zotero's (its ``zotero_pdf``
# is anything but "unavailable", gamma/zotero_sync.py): the two files would part, and Zotero's annotations
# with them. A detached link's page waits for the reattach, a clone's for its origin.
ZOTERO_PDF_COMING = "This page's PDF is in Zotero and comes from there: use Download now on the page"


class PageCreate(BaseModel):
    title: str = ""
    folders: list[str] = []   # folder ids to file it in
    parent_page: str = ""     # a live page to make it a sub-page of (its folders are then ignored)
    id: str = ""              # a mirror bringing a page over keeps its id, /page the one its link names (409 when a live block has it)
    properties: dict = {}     # ...and its page properties


class AttachRequest(BaseModel):
    doc_id: str = ""              # content hash of an uploaded PDF, or the URL hash a proxied one gets
    source_url: str = ""          # where the viewer loads it from (not stored when it is doc_id's own upload path)
    file: str = ""                # a stored file of any format by its name, <hash><ext> (alone: no doc_id, no source_url)
    original_filename: str = ""   # display name; becomes the title while it is still automatic
    # PUT only: how long the replaced stored file is kept once nothing needs it
    # (upload_gc.retain; a sync sending each new build keeps a day of the ones before)
    keep_old_days: int | None = Field(default=None, ge=1, le=30)


class HeldElsewhere(OpError):
    """The refusal of a ``doc_id`` another page carries already (409):
    ``page_id`` names that page, so a client can offer to open it."""

    def __init__(self, page_id: str, detail: str = "attachment belongs to another page"):
        super().__init__(409, detail)
        self.page_id = page_id

    def answer(self) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": self.detail, "page_id": self.page_id})


def _page(conn, page_id: str) -> dict:
    """The page ``page_id`` as a block dict; OpError 404 when there is none
    or it is in Recently deleted, 400 for a block that is no page."""
    row = conn.execute(
        f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    if not row or row[1] == TRASH:
        raise OpError(404, "page not found")
    if row[1] != "root":
        raise OpError(400, "not a page (only root blocks carry attachments)")
    return block_to_dict(row)


def _load_page(conn, page_id: str):
    try:
        return _page(conn, page_id)
    except OpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.post("/pages")
def create_page_endpoint(payload: PageCreate, request: Request):
    """A new text-only page: ``{title?, folders?, parent_page?, id?,
    properties?}`` → the page's block dict. Title defaults to "Untitled";
    ``folders`` (folder ids) becomes ``properties.folders`` as given when it
    is a list of block ids (``create_page``). ``parent_page`` makes it a
    sub-page of that page, which must be live (400 otherwise), and files it
    nowhere of its own: ``folders`` are ignored. A ``parent_page`` inside
    ``properties`` (a publishing mirror's page) is stored as
    ``page_root_props`` leaves it, live or not. On a share host, 402 with
    ``{detail, limit, used, plan}`` when the owner's plan allows no more
    pages in the workspace (the path a publishing mirror creates its pages
    by; gamma/publish.py page_cap).

    Through an edit share's token (``?share=``, ``require_ws_writer``) it
    makes a page inside a page the share reaches, and nothing else: a
    ``parent_page`` the share reaches is required (403 otherwise), no
    ``properties`` (403: a page's settings stay the owner's), ``folders``
    are ignored as for any sub-page, and the page is written under the
    share editor's name (``actor_of``). An ``id`` the link names must be
    new to the workspace: one that names a page in Recently deleted is
    refused (409) as any taken id, rather than giving way, since that page
    need not be one the share reaches.

    400 for ``properties`` that keep a document in the flat keys of old
    (``doc_id``, ``source_url``, ``linked_file``) or carry a ``doc`` that
    does not check (``documents.model.document_props``)."""
    ws = require_ws_writer(request)
    scope = share_scope(request)
    if scope is not None:
        link_ratelimit(request, "pages", SHARE_PAGES_PER_5_MIN, 300)
        if not payload.parent_page:
            raise HTTPException(status_code=403, detail="through a share link a page is made inside a shared page only")
        if payload.properties:
            raise HTTPException(status_code=403, detail="share editors cannot set a page's properties")
    try:
        props = dict(document_props(payload.properties or {}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if payload.parent_page:
        props[PARENT_PAGE] = payload.parent_page
        props.pop(FOLDERS, None)
    elif payload.folders:
        props[FOLDERS] = payload.folders
    if payload.id and not valid_block_id(payload.id):
        raise HTTPException(status_code=400, detail="invalid page id")
    with connect_pages_db(ws) as conn:
        # The checks and the insert as one step: two requests bringing the
        # same page id over (a mirror's retry) get one page and a 409, and
        # the plan's cap counts every page created before this one.
        write_lock(conn)
        if scope is not None and not scope.allows_page(conn, payload.parent_page):
            raise HTTPException(status_code=403, detail="not accessible via this share link")
        if payload.parent_page and page_root_id(conn, payload.parent_page) != payload.parent_page:
            raise HTTPException(status_code=400, detail="parent_page names no page")
        taken = payload.id and conn.execute(
            "SELECT parent_id FROM unified_blocks WHERE id = ?", (payload.id,)).fetchone()
        # a page of that id in Recently deleted gives way (create_page), but not to a share link
        if taken and (taken[0] != TRASH or scope is not None):
            raise HTTPException(status_code=409, detail="a block with that id exists")
        refusal = publish.cap_refusal(ws)  # only a new page counts
        if refusal:
            return JSONResponse(status_code=402, content=refusal)
        try:  # judged by the work-folder rule as the writer the request names (blocks_store.create_page)
            return create_page(conn, payload.title, props, actor=actor_of(request), client=writer_client(request),
                               block_id=payload.id)
        except OpError as e:
            return op_refusal(e)


@router.get("/pages/{page_id}/family")
def page_family(page_id: str, request: Request):
    """Where a page stands among pages: ``{"ancestors": [{id, title}],
    "sub_pages": [{id, title, updated_at, format?}]}`` — its effective
    ancestors top first (blocks_store.page_ancestors; under a share only
    those the share reaches) and the pages right inside it, in library
    order (``sub_pages``, ``format`` the format of the document one
    carries, ``page_document``); "Untitled" for an empty title. Any member,
    or a share that reaches the page (403 otherwise, as for its subtree);
    404 for a block that is no live page."""
    scope = share_scope(request)
    with connect_pages_db(resolve_ws(request)) as conn:
        assert_block_in_scope(conn, page_id, scope)
        if page_root_id(conn, page_id) != page_id:
            raise HTTPException(status_code=404, detail="page not found")
        ancestors = [a for a in page_ancestors(conn, page_id) if scope is None or scope.allows_page(conn, a)]
        kids = sub_pages(conn, page_id)
        rows = {r[0]: r for r in conn.execute(
            "SELECT id, content, updated_at, properties FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(ancestors + kids),))}

    def sub_page(i):
        _, title, updated_at, props = rows[i]
        entry = {"id": i, "title": title or "Untitled", "updated_at": updated_at}
        view = page_document(load_props(props))
        if view:
            entry["format"] = view["format"]
        return entry

    return {"ancestors": [{"id": i, "title": rows[i][1] or "Untitled"} for i in ancestors],
            "sub_pages": [sub_page(i) for i in kids]}


class DocsLookup(BaseModel):
    doc_ids: list[str] = []   # PDFs and markdown files by hash
    files: list[str] = []     # stored files of any format by name, <hash><ext>


@router.post("/pages/by-docs")
def pages_by_docs(payload: DocsLookup, request: Request):
    """Which pages these stored files became: ``{doc_ids: [<hash>, ...],
    files: ["<hash><ext>", ...]}`` → ``{"pages": {hash: {id, title}},
    "files": {name: {id, title}}}`` — a hash matches the page owning it as
    its PDF (``doc.hash``) or the note page made from it (a markdown file's
    ``markdown_import``), a name the library page owning that stored file as
    its document, of any format, or the note page made from a markdown one
    (``pages_for_files``); hashes and names with no page are absent. The
    file chips ask this once per page render, by name, to show the "open
    page" button and label the menu "Open page" / "Add to library". Any
    member."""
    ws = require_ws(request)
    ids = [d for d in dict.fromkeys(payload.doc_ids[:500]) if d]
    names = [n for n in dict.fromkeys(payload.files[:500]) if n]
    with connect_pages_db(ws) as conn:
        return {"pages": pages_for_docs(conn, ids), "files": pages_for_files(conn, names)}


class SummariesLookup(BaseModel):
    ids: list[str] = []


MAX_SUMMARIES = 500  # the pages one POST /pages/summaries names; 413 past it


@router.post("/pages/summaries")
def page_summaries(payload: SummariesLookup, request: Request):
    """The pages ``ids`` as the library listing has them: ``{ids: [page
    id, …]}`` → ``{"pages": [page_summary]}`` (routers/blocks.py, its
    preview and parent included), in the order asked, for the live pages
    among them: an unknown id, a block that is no page and a page in
    Recently deleted are left out, and through a share link every page it
    does not reach. The open tabs, the recents and the focused page
    ask this for the pages the listing does not carry (a
    work folder's files). Any member, or a share; 413 past
    ``MAX_SUMMARIES``. A read, though a POST (hosted.POST_READS); the
    lookup is ``blocks.summaries``."""
    if len(payload.ids) > MAX_SUMMARIES:
        raise HTTPException(status_code=413, detail=f"at most {MAX_SUMMARIES} pages in one request")
    ids = [i for i in dict.fromkeys(payload.ids) if i]
    scope = share_scope(request)
    with connect_pages_db(resolve_ws(request)) as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN")  # one read snapshot for the rows, their previews and parents
        found = summaries(conn, ids)
        if scope is not None:
            found = {i: r for i, r in found.items() if scope.allows_page(conn, i)}
        parents = page_parents(conn) if found else {}
        pages = [page_summary(found[i], page_preview(conn, i), parents.get(i, "")) for i in ids if i in found]
        conn.rollback()
    return OrjsonResponse({"pages": pages})


class FromFile(BaseModel):
    filename: str          # a stored upload, ``<hash>.md`` / ``.markdown``
    original: str = ""     # the chip's display name (the page title when there is no front-matter title)
    folder: str = ""       # a folder id: files the new page; ignored when the page exists


@router.post("/pages/from-file")
def page_from_file(payload: FromFile, request: Request):
    """"Open as page" on a markdown file chip: the stored upload becomes a
    note page through the same importer as POST /import/markdown (title from
    front matter else the file name, blocks from the body), filed in
    ``folder``. Idempotent: a page already made from this file (its
    ``markdown_import`` is the file's hash) is returned instead of a second
    copy. The file stays what it was — the page is a copy, edits to it never
    touch the file. → ``{page, created}``. 400 for anything but a stored
    markdown name, 404 when the file is not in this workspace."""
    ws = require_ws(request, write=True)
    name = (payload.filename or "").strip().lower()
    m = re.fullmatch(r"([0-9a-f]{8,64})\.(md|markdown)", name)
    if not m:
        raise HTTPException(status_code=400, detail="filename must be a stored <hash>.md upload")
    path = find_upload_file(name, ws)
    if not path:
        raise HTTPException(status_code=404, detail="file not found")
    with connect_pages_db(ws) as conn:
        write_lock(conn)  # the lookup and the import as one step: one page per file
        hit = pages_for_docs(conn, [m.group(1)]).get(m.group(1))
        if hit:
            row = conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?", (hit["id"],)).fetchone()
            return {"page": block_to_dict(row), "created": False}
        original = m.group(0)
        # the chip's text is the display name; the stored name is a hash, so
        # take the title from the request's ``original`` when given
        result = markdown_page(ws, conn, path.read_bytes(), payload.original or original, payload.folder,
                               actor=actor_of(request))
        row = conn.execute(f"SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?", (result["block_id"],)).fetchone()
    return {"page": block_to_dict(row), "created": True, "imported": result["imported"]}


MARKDOWN_IS_A_NOTE = "A Markdown file becomes a note (POST /pages/from-file)"
NAME_THE_FILE = "Attach a stored file by its name (file)"  # a non-PDF upload's address is no PDF to show from there


class StoredFile:
    """A stored file a request names (``<hash><ext>``, an upload of any
    format), read for what it is: ``hash`` and ``ext`` its stored name's,
    ``format`` settled on the server (``formats.stored_format`` over the
    shown ``name``, the extension and the file's first bytes), ``size`` its
    bytes. HTTPException 400 for a name that is no stored file's or a
    Markdown file (``MARKDOWN_IS_A_NOTE``: a note is made from it, never a
    document), 404 for one the workspace does not hold. A file whose bytes
    are a PDF under another extension (an arXiv download named by its id)
    is stored again as ``<hash>.pdf`` and read as that PDF: a PDF document
    is always ``.pdf``; for a synced vault's write (``vault``) it stays the
    binary the vault's file class read it as (gamma/vault/kinds.py
    ``file_format``: a PDF is a PDF by its name there). A name only a file
    on this computer holds (a work folder's file a note links, served where
    it is, gamma/linked_files.py) has its bytes stored first, as *Save a
    copy to library* stores them: the page made of it carries the bytes
    whatever becomes of the file; 409 ``FILE_CHANGED`` when the file there
    is other bytes by now."""

    def __init__(self, ws: str, name: str, shown: str = "", *, vault: bool = False):
        parsed = parse_upload_url(upload_url(name))
        if not parsed:
            raise HTTPException(status_code=400, detail="file must be a stored <hash><ext> upload")
        self.hash, self.ext = parsed
        path = find_upload_file(name, ws)
        if not path:
            raise HTTPException(status_code=404, detail="file not found")
        try:
            with open(path, "rb") as f:
                head = f.read(SNIFF_BYTES)
        except OSError:
            raise HTTPException(status_code=404, detail="file not found")
        self.format, _language = stored_format(shown, self.ext, head)
        if self.format == "markdown":
            raise HTTPException(status_code=400, detail=MARKDOWN_IS_A_NOTE)
        if self.format == "pdf" and self.ext != ".pdf":
            if vault:
                self.format = "binary"
            else:
                with open(path, "rb") as f:
                    self.hash, _existed, _size = storage.store_pdf_stream(ws, f)
                self.ext = ".pdf"
        if not storage.exists(ws, stored_name(self.hash, self.ext)):
            try:
                linked_files.store_linked(ws, stored_name(self.hash, self.ext), str(path))
            except folder_links.LinkError:
                raise HTTPException(status_code=409, detail=FILE_CHANGED)
        self.size = storage.size(ws, stored_name(self.hash, self.ext))


class ByFile(BaseModel):
    file: str              # a stored upload of any format, ``<hash><ext>``
    name: str = ""         # the file's shown name (the chip's text, the upload's name): the page's title and doc.name
    folder: str = ""       # a folder id: files a new page; ignored when the page exists


@router.post("/pages/by-file")
def page_by_file(payload: ByFile, request: Request):
    """"Add to library" on a file chip of any format, and the library's
    upload of any file: the library page whose document is the stored file
    ``file``, made when there is none (``get_or_create_doc_page``: one page
    per stored file, by hash and extension). Its doc is ``{format, name,
    hash, ext, at: "store", size}``, the format settled here from the name,
    the extension and the file's first bytes (``StoredFile``,
    ``formats.stored_format``), titled by ``name``, filed in ``folder`` (a
    library folder: one in a work folder is refused as ``by-doc`` refuses
    it, 403 ``locked``); no ``auto_title`` and no manifest for a document
    that is no PDF. A ``.pdf``, and a file whose bytes are a PDF under
    another extension (stored again as ``<hash>.pdf``), goes the ``by-doc``
    way. → ``{page, created}``. 400 for a name that is no stored file's or a
    Markdown file (``MARKDOWN_IS_A_NOTE``: ``POST /pages/from-file`` makes
    its note), 404 when the file is not in this workspace. Workspace
    editors."""
    ws = require_ws(request, write=True)
    stored = StoredFile(ws, (payload.file or "").strip(), payload.name)
    with connect_pages_db(ws) as conn:
        write_lock(conn)  # the lookup and the create as one step: one page per file
        created = page_for_file(conn, stored.hash, stored.ext) is None
        try:
            page = get_or_create_doc_page(conn, stored.hash, "", None, payload.name, folder=payload.folder.strip(),
                                          ws=ws, actor=actor_of(request), format=stored.format, ext=stored.ext,
                                          size=stored.size)
        except OpError as e:
            return op_refusal(e)
    return {"page": page, "created": created}


def _attachment_body(ws: str, payload: AttachRequest, vault: bool = False) -> tuple[str, str, str, str, int | None]:
    """The checked ``(hash, source_url, format, ext, size)`` of an attach or
    replace body. ``file`` names a stored file of any format (``StoredFile``:
    its format settled here, as a synced vault's file class settles it when
    the write is the vault's, ``vault``) and comes alone; else ``doc_id``
    and/or ``source_url`` name a PDF as they always did, ``doc_id``
    validated in shape only (like ``by-doc``, a URL-opened PDF's id is the
    URL hash and the proxy fetches the file lazily). A ``source_url`` that
    is a stored file's address of another extension than ``.pdf`` is
    refused (``NAME_THE_FILE``): it is no PDF to show from an address, and
    an older server would have made one."""
    file = (payload.file or "").strip()
    doc_id = (payload.doc_id or "").strip()
    source_url = (payload.source_url or "").strip()
    if file:
        if doc_id or source_url:
            raise HTTPException(status_code=400, detail="file names the stored file: no doc_id or source_url with it")
        stored = StoredFile(ws, file, payload.original_filename, vault=vault)
        return stored.hash, "", stored.format, stored.ext, stored.size
    if doc_id:
        try:
            doc_id = safe_doc_id(doc_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid doc_id")
    if not doc_id and not source_url:
        raise HTTPException(status_code=400, detail="doc_id or source_url required")
    own = parse_upload_url(source_url)
    if own and own[1] != ".pdf":
        raise HTTPException(status_code=400, detail=NAME_THE_FILE)
    return doc_id, source_url, "pdf", ".pdf", storage.size(ws, stored_name(doc_id, ".pdf")) if doc_id else None


def _held_elsewhere(conn, hash: str, ext: str):
    """The 409 for a stored file a page carries already as its document
    (``page_for_file``: by hash and extension, any format; ``{"detail",
    "page_id"}``, so the client can offer to open that page), else None.
    Callers ask only for a file the page itself does not carry, so any page
    found is another. A page that is no library document (one reading those
    bytes from a file on this computer, a work folder's file not in the
    library) refuses nothing (``page_for_file`` leaves it out)."""
    other = page_for_file(conn, hash, ext) if hash else None
    if other:
        return JSONResponse(status_code=409, content={
            "detail": "attachment belongs to another page", "page_id": other[0]})
    return None


def _zotero_marked(page: dict) -> bool:
    """Whether the page, which has no PDF, carries the mark of the one its
    Zotero item holds (a ``zotero_pdf`` whose state is not ``unavailable``)."""
    props = page.get("properties") or {}
    mark = props.get("zotero_pdf")
    return not page_document(props) and isinstance(mark, dict) and mark.get("state") != "unavailable"


def _zotero_keeps(ws: str) -> bool:
    """Whether something still brings the workspace's Zotero PDFs: its
    Zotero link, detached included (a reattach brings them, and a PDF
    attached meanwhile would go over Zotero's file then), or the origin a
    whole-workspace clone copies (the attach would reach it). Not once the
    link is removed, or in a workspace whose pages came with a backup."""
    with connect_users_db() as conn:
        return conn.execute("SELECT 1 FROM zotero_links WHERE workspace_id = ? UNION ALL SELECT 1 FROM mirrors "
                            "WHERE workspace_id = ? AND page_filter IS NULL LIMIT 1", (ws, ws)).fetchone() is not None


def _zotero_pdf_refusal(ws: str, page: dict) -> str | None:
    """``ZOTERO_PDF_COMING`` while something brings the PDF the page's Zotero
    item holds (``_zotero_keeps``): a person's PDF is refused on it; "" when
    the page carries a mark nothing honours any more (the attach goes, and
    takes the mark with it); None when it carries none."""
    if not _zotero_marked(page):
        return None
    return ZOTERO_PDF_COMING if _zotero_keeps(ws) else ""


def _attachment_op(page: dict, hash: str, url: str, name: str, *, at: str | None = None,
                   format: str = "pdf", ext: str | None = None, size: int | None = None) -> tuple[dict, str, dict, bool]:
    """The op that makes the file ``hash`` / ``url`` named ``name``, of
    ``format`` with the stored extension ``ext`` (a PDF's by default) and
    ``size`` bytes, the page's document (``documents.model.document``),
    whatever it carried before, with the page's title and properties after
    it. ``at`` says where its bytes are read from; by default a page that
    read its document in place (``in_place``) goes on reading the new one
    there, unless it now comes from an address. While the title is still
    automatic ("Untitled"/empty) it becomes the file name (or URL tail,
    ``document_title``), and a PDF's is marked ``auto_title``, the marker
    metadata may replace (same semantics as get_or_create_doc_page; an
    explicit rename clears it); another format's title is never looked up,
    so it carries no marker. Any other title stays. When the file changes
    (another own file or address than the page showed), a failed metadata
    lookup's ``meta_error`` goes: that lookup read the file the page
    carried, and the new one is looked up again. OpError 400 for a hash
    that can be no stored file's. → ``(op, title, properties, file
    changed)``; an op whose ``props`` patch is empty and that has no
    ``content`` changes nothing."""
    before = page_document(page["properties"])
    props = {k: v for k, v in page["properties"].items() if k not in ATTACHMENT_KEYS}
    try:
        props["doc"] = document(hash, url, name, at=at or ("path" if in_place(before) and not url else None),
                                format=format, ext=ext, size=size)
    except ValueError:
        raise OpError(400, "invalid doc_id")
    after = page_document(props)
    moved = before is None or (own_hash(before), before["url"]) != (own_hash(after), after["url"])
    if moved:
        props.pop("meta_error", None)
    content = page["content"]
    if not content.strip() or content.strip() == "Untitled":
        content = document_title(name, url, hash) or content
        if format == "pdf":
            props["auto_title"] = content
    op = {"op": "set", "id": page["id"], "props": props_patch(page["properties"], props)}
    if content != page["content"]:
        op["content"] = content
    return op, content, props, moved


@router.post("/pages/{page_id}/attachment")
def attach_pdf(page_id: str, payload: AttachRequest, request: Request):
    """Attach a document to an existing page that has none. Body: a PDF's
    ``doc_id`` and/or ``source_url``, or ``file`` (a stored file of any
    format by its name, its format settled here), plus an optional
    ``original_filename`` (``_attachment_body``). 409 when the page already
    carries an attachment (``PUT`` replaces one), when another page already
    carries this file (``_held_elsewhere``), or while something brings the
    PDF its Zotero item holds (``_zotero_pdf_refusal``: a page whose mark
    nothing honours any more takes the PDF, and loses the mark). An
    automatic title becomes the file name (``_attachment_op``). Returns the
    updated block."""
    ws = require_ws(request, write=True)
    hash, source_url, format, ext, size = _attachment_body(ws, payload)
    with connect_pages_db(ws) as conn:
        # The checks below and the write as one step: two pages attaching the
        # same file at once can't both pass "no other page carries it".
        write_lock(conn)
        page = _load_page(conn, page_id)
        if page_document(page["properties"]):
            raise HTTPException(status_code=409, detail="page already has an attachment")
        refusal = _zotero_pdf_refusal(ws, page)
        if refusal:
            raise HTTPException(status_code=409, detail=refusal)
        conflict = _held_elsewhere(conn, hash, ext)
        if conflict:
            return conflict
        try:
            op, content, props, _moved = _attachment_op(page, hash, source_url, payload.original_filename,
                                                        format=format, ext=ext, size=size)
        except OpError as e:
            raise HTTPException(status_code=e.status, detail=e.detail)
        if refusal == "":  # a Zotero mark nothing honours any more goes with the attach
            props.pop("zotero_pdf", None)
            op["props"] = props_patch(page["properties"], props)
        try:
            result = after_commit(ws, conn, apply_ops(conn, page_id, [op], actor=actor_of(request),
                                                      client=writer_client(request)))
        except OpError as e:
            return op_refusal(e)
    return {**page, "content": content, "properties": props, "updated_at": result["at"]}


@router.put("/pages/{page_id}/attachment")
def replace_pdf(page_id: str, payload: AttachRequest, request: Request):
    """Make this file the page's attachment, in place of the one it carries
    (a new version of the file: a paper compiled again from its TeX, a
    figure drawn again). Body and 409s as ``POST``'s; a page without an
    attachment gets this one as ``POST`` would give it. The page keeps its
    format (``keep_format``: a body whose settled format differs from the
    page's document's is a 400, "This page's document is {format}; detach it
    first"), its id, title, notes and highlights; a synced vault's write
    (``writer_client`` the vault's, a work folder of another computer
    sending its file's new bytes) may change the format, as the vault's own
    server lets it (gamma/vault/local_remote.py ``replace_doc``: a file
    whose name says nothing and whose bytes sniff otherwise now), and reads
    a PDF under another name as the binary that vault read. The same
    attachment again changes nothing. The work, described there, is
    ``replace_attachment``'s, which the Zotero link and a vault call too.

    Returns the updated block, with the relocation job as ``job``, a text
    file's ranges placed at once as ``relocation`` (``{format: "text",
    highlights, moved, kept, approx, lost, changed, min_match,
    exact_chars}``), or, when no run started or the one made at once
    refused, the reason as ``relocation_error``: the account's background
    tasks are full (the Document menu's Find highlights in this PDF places
    the highlights later), the new PDF is shown from a URL alone
    (``NOT_STORED``: no file to read), or the text run's own (the file
    changed while it was read, it is no text). The replacement stands
    either way."""
    ws = require_ws(request, write=True)
    client = writer_client(request)
    hash, source_url, format, ext, size = _attachment_body(ws, payload, vault=client == DISK_CLIENT)
    try:
        done = replace_attachment(ws, page_id, hash, source_url, payload.original_filename,
                                  actor=actor_of(request), client=client, owner=request.state.user_id,
                                  keep_old_days=payload.keep_old_days, format=format, ext=ext, size=size,
                                  keep_format=client != DISK_CLIENT)
    except HeldElsewhere as e:
        return e.answer()
    except OpError as e:
        return op_refusal(e)
    return replaced_answer(done)


def replaced_answer(done: dict) -> dict:
    """What ``PUT /pages/{id}/attachment`` answers for ``replace_attachment``'s
    result: the updated block, with the relocation run as ``job``, a text
    file's run made at once as ``relocation`` (its counts), or why none
    started or the run refused as ``relocation_error``; the page as it was
    when nothing changed. The desktop app's own server answers a PDF sent
    from disk the same way (gamma/folder_links.py ``LocalSink``)."""
    if not done["results"]:
        return done["page"]
    answer = {**done["page"], "content": done["content"], "properties": done["props"],
              "updated_at": done["results"][0]["at"]}
    if done["job"]:
        answer["job"] = done["job"]
    elif done.get("relocation"):
        answer["relocation"] = done["relocation"]
    elif done["relocation_error"]:
        answer["relocation_error"] = done["relocation_error"]
    return answer


def replace_attachment(ws: str, page_id: str, hash: str, url: str = "", name: str = "", *, at: str | None = None,
                       actor: str, client: str = "", owner, check_held: bool = True, props: dict | None = None,
                       check=None, rows=None, check_zotero: bool = True,
                       keep_old_days: int | None = None, format: str = "pdf", ext: str | None = None,
                       size: int | None = None, keep_format: bool = False) -> dict | None:
    """Make the file ``hash`` / ``url`` named ``name``, of ``format`` with
    the stored extension ``ext`` and ``size`` bytes (a PDF's by default:
    the callers that name a PDF pass nothing), the page's document, in
    place of what it carries (or as its first), in one transaction under
    the write lock, then start placing its highlights. ``at`` says where its
    bytes are read from (``_attachment_op``: by default a page that read its
    document in place goes on doing so). PUT /pages/{id}/attachment and the
    Zotero link (gamma/zotero_local.py, ``check_held=False``: linked items
    may share one PDF, and ``page_for_file`` then answers the oldest page)
    both go through here, and so do a synced vault's (gamma/vault/, a file
    of any format, whose format may change when its name's says nothing and
    its bytes sniff otherwise) and gamma-sync's new builds of a file.

    In the transaction: ``check(conn)``, a caller's compare-and-set, runs
    first (False: nothing is written, and the answer is None); the page is
    read (OpError 404 when it is gone or trashed, 400 for a block that is
    no page; unless ``check_zotero`` is False, 409 while something brings
    its Zotero item's PDF, ``_zotero_pdf_refusal``, and a mark nothing
    honours any more is dropped with the write); with ``keep_format``, a
    page whose document is of another format than ``format`` is refused
    (OpError 400 "This page's document is {format}; detach it first": PUT
    keeps a page's format, a page with none takes any); unless ``check_held``
    is False, a file another page owns is refused (``HeldElsewhere``,
    409; a page reading those bytes from a file on this computer is passed
    by, ``page_for_file``); the attachment op (``_attachment_op``: an
    automatic title becomes the file name, a failed lookup's ``meta_error``
    goes when the file changes) is written, with ``props`` (a page property
    patch the caller adds, such as dropping ``zotero_pdf``) merged into it.
    When the file changes (whatever the page showed it from: its own file,
    or only an address or the file a copy shows, as on a duplicated
    document page), the blocks imported from the old file's embedded
    annotations are marked gone from the file (``imports.originals_gone``),
    so PDF export draws them again. When the stored file changes
    (``file_hash``: a duplicated page's is the file it shows), the outgoing
    and the incoming documents are both PDFs, and the page has highlights
    placed on its PDF, each of them that names no file yet gets the
    outgoing file's hash as ``pdf_position.doc_id`` (the file its
    rectangles were measured on; ``storage.UNSTORED`` for a page that
    showed its file from a URL alone), and a lost one's ``closest`` (a
    place in the outgoing file) goes; a picture replaced by a picture
    writes no stamp (its regions read as drawn on another version by their
    own ``hash``, docs/dev/highlight_relocation.md), and a change of format
    leaves the old format's positions as they are. ``rows(conn)`` runs
    last, for a caller's own rows in the same transaction. The batches are
    logged under ``actor`` and ``client``. After the commit (inside the
    connection's block) the room hears the batches, and when the stored
    file changed the old file's search rows and manifest go; unless
    something else references it the file is purged after 30 days
    (gamma/upload_gc.py), or ``keep_old_days`` (1 to 30) after
    (``upload_gc.retain``: a sync sending each new build keeps a day of the
    builds before, gamma/gamma_sync.py ``KEEP_OLD_DAYS``). The same
    attachment again writes no op (only ``rows``) and answers ``results``
    [].

    Then, when highlights were stamped and the new file is stored, the
    ``relocate-highlights`` job owned by ``owner`` starts
    (``_start_relocation``): each highlight is found again in the new file
    by its text and moved there; until it is done they sit where they were.
    A PDF replaced by a PDF starts one. A text file replaced by a text file
    whose page has ranges in it (``text_anchors.page_ranges``: no stamp, a
    range names the version it was measured on itself) has them placed at
    once, before this returns, when the new file is at most
    ``TEXT_INLINE_BYTES`` (``_relocate_text``), else by the same job; a
    file that turned listed (no bytes to read) starts nothing, and its
    ranges read as pending until the next replacement.

    → ``{page (as read), content, props (the page's title and properties
    after), results (the batches' results), placed ([(block id, props)] of
    the highlights stamped), incoming, outgoing (the stored files' hashes,
    "" for none), job (the run, or None), relocation (the counts of a text
    run made at once, or None), relocation_error (why no run started or the
    run made at once refused; "" when one did or none was needed)}``."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)  # the checks and the write as one step
        try:
            if check is not None and not check(conn):
                conn.rollback()
                return None
            page = _page(conn, page_id)
            refusal = _zotero_pdf_refusal(ws, page) if check_zotero else None
            if refusal:
                raise OpError(409, refusal)
            if refusal == "":  # a Zotero mark nothing honours any more goes with the replacement
                props = {**(props or {}), "zotero_pdf": None}
            old_doc = page_document(page["properties"])
            old = own_hash(old_doc)
            if keep_format and old_doc is not None and old_doc["format"] != format:
                raise OpError(400, f"This page's document is {old_doc['format']}; detach it first")
            if check_held and hash and hash != old:
                other = page_for_file(conn, hash, ext or FORMAT_EXT.get(format, ""))
                if other:
                    raise HeldElsewhere(other[0])
            had = old_doc is not None
            op, content, after, moved = _attachment_op(page, hash, url, name, at=at, format=format, ext=ext, size=size)
            if props:
                for key, value in props.items():
                    if value is None:
                        after.pop(key, None)
                    else:
                        after[key] = value
                op["props"] = props_patch(page["properties"], after)
            outgoing = file_hash(old_doc)
            incoming = file_hash(page_document(after))
            if not op["props"] and "content" not in op:
                if rows is not None:
                    rows(conn)
                    conn.commit()
                else:
                    conn.rollback()  # nothing to write: let go of the lock
                return {"page": page, "content": page["content"], "props": page["properties"], "results": [],
                        "placed": [], "incoming": incoming, "outgoing": outgoing, "job": None,
                        "relocation": None, "relocation_error": ""}
            marks = originals_gone(conn, page_id) if had and moved else []
            swapped = had and moved and not (outgoing and outgoing == incoming)  # another file, not another address
            pdfs = is_pdf(old_doc) and format == "pdf"  # the highlights' file is a PDF before and after
            texts = old_doc is not None and old_doc["format"] == format == "text"  # a text file before and after
            placed = highlight_relocate.page_highlights(conn, page_id) if swapped and pdfs else []
            ranges = text_anchors.page_ranges(conn, page_id) if swapped and texts else []
            stamps = []
            for bid, p in placed:
                # the file its rects were measured on, where it names none, and no closest place a lost one kept:
                # that is a place in the outgoing file (the run finds the new file's)
                stamped = {k: v for k, v in p["pdf_position"].items() if k != "closest"}
                if not stamped.get("doc_id"):
                    stamped["doc_id"] = outgoing or UNSTORED
                if stamped != p["pdf_position"]:
                    stamps.append({"op": "set", "id": bid, "props": {"pdf_position": stamped}})
            batches = [(page_id, [op])]
            for extra in (marks, stamps):
                batches += [(page_id, extra[i:i + MAX_OPS]) for i in range(0, len(extra), MAX_OPS)]
            results = apply_batches(conn, batches, actor=actor, client=client)
            if rows is not None:
                rows(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        after_commits(ws, conn, results)
        if old and old != hash:
            text_index.purge_doc(ws, None, conn, old)
    if keep_old_days and outgoing and outgoing != incoming:
        replaced = stored_name(outgoing, old_doc["ext"])
        try:  # after the commit: retain takes the purge's lock, which never waits on the write lock
            upload_gc.retain(ws, replaced, keep_old_days * 86400)
        except Exception as e:  # noqa: BLE001 — a shorter life missed is no failed replacement
            log.warning(f"[uploads] {page_id}: could not shorten the life of {replaced}: {e}")
    job, relocation, reason = None, None, ""
    if placed:
        job, reason = (_start_relocation(owner, ws, page["id"], content, incoming, outgoing=outgoing)
                       if incoming else (None, NOT_STORED))
    elif ranges:
        if not incoming:
            reason = LISTED
        elif (page_document(after)["size"] or 0) > text_anchors.TEXT_INLINE_BYTES:
            job, reason = _start_relocation(owner, ws, page["id"], content, incoming, outgoing=outgoing,
                                            format="text")
        else:
            relocation, reason = _relocate_text(owner, ws, page["id"], incoming)
    return {"page": page, "content": content, "props": after, "results": results, "placed": placed,
            "incoming": incoming, "outgoing": outgoing, "job": job, "relocation": relocation,
            "relocation_error": "" if job or relocation else reason}


def _relocate_text(owner: str, ws: str, page_id: str, new_doc: str) -> tuple[dict | None, str]:
    """The page's text ranges placed in its file ``new_doc`` at once
    (documents/text_anchors.relocate, by ``owner``'s thresholds), for a
    file of at most ``TEXT_INLINE_BYTES``: a file of a thousand lines with
    ten ranges takes about 25 ms, its write included, short enough for the
    replacement's own thread (a vault's round waits for it), and no
    background task, whose cap a formatter rewriting a repository's files
    would reach. The counts are logged, the answer a vault drops. →
    ``(result, "")``, or ``(None, why)`` when the run refused (the ranges
    stay as they were, pending until the next replacement)."""
    limits = highlight_relocate.thresholds(get_profile(owner)[0])
    try:
        result = text_anchors.relocate(ws, page_id, new_doc, actor=owner, **limits)
    except ValueError as e:
        log.warning(f"[relocate] {page_id}: {e}")
        return None, str(e)
    except Exception as e:  # noqa: BLE001 — the replacement is committed; a failed run leaves the ranges pending
        log.exception(f"[relocate] {page_id}: the text ranges could not be placed")
        return None, f"the highlights could not be placed: {e}"
    log.info(f"[relocate] {page_id}: {result['highlights']} ranges, {result['moved']} moved, {result['kept']} kept, "
             f"{result['approx']} approx, {result['lost']} lost, {result['changed']} changed")
    return result, ""


def _start_relocation(owner: str, ws: str, page_id: str, title: str, new_doc: str, *,
                      outgoing: str | None = None, quiet: bool = True,
                      format: str = "pdf") -> tuple[dict | None, str]:
    """Start the ``relocate-highlights`` job that places the page's
    highlights in its PDF ``new_doc`` (gamma/highlight_relocate.py
    ``relocate``), or, for ``format`` ``text``, the ranges of its text
    file (documents/text_anchors.py ``relocate``; the job's ``format``
    param says which), owned by the account ``owner`` (the request's, or a
    Zotero link's owner). One run per
    page and file: its key is ``<page id>:<new_doc>``, and a start while a
    run for the same page and file is going answers that run. A
    replacement (``outgoing``: the file it replaced) first stops the same
    account's run for the outgoing file, whose result would be for a file
    the page no longer shows; that run never holds up the new one, and one
    past its last stop finishes on its own and has its write refused.
    → ``(job, "")``, or ``(None, why)`` when no run started: the account's
    jobs are full, or the last run for this file is still stopping. A
    replacement is ``quiet`` (its answer says why as ``relocation_error``
    and the replacement stands); a start by hand raises that as its 429 or
    409.

    The run goes by its owner's thresholds as they are when it starts
    (``highlight_relocate.thresholds`` of the account's profile: Settings'
    relocateMinMatch and relocateExactChars), which its params record as
    ``min_match`` (a fraction) and ``exact_chars``, and its result echoes:
    a setting changed later leaves the runs already started as they
    are. However a run ends, written or not, the workspace's Zotero link
    is told (``zotero_sync_pdfs.relocation_ended``) once the job reads as
    ended: a PDF of the page that waited for its highlights to be placed,
    and the annotations held for them, are decided again, and no round in
    between finds the job still running and parks the PDF again for
    ``PLACING_RECHECK_S``."""
    name = " ".join((title or "").split())[:80] or "Untitled"
    limits = highlight_relocate.thresholds(get_profile(owner)[0])
    params = {"page_id": page_id, "title": name, "doc_id": new_doc, "format": format, **limits}
    place = text_anchors.relocate if format == "text" else highlight_relocate.relocate
    if outgoing is not None:
        previous = jobs.latest(owner, RELOCATE, f"{page_id}:{outgoing}")
        if previous and previous["state"] in jobs.ACTIVE:
            try:
                jobs.cancel(previous["id"])
            except HTTPException:
                pass  # past its last stop: it finishes, and its write is refused (the page carries another file)

    def run(job):
        return place(ws, page_id, new_doc, actor=owner, progress=job.progress, **limits)

    def ended(_state):
        from ..zotero_sync_pdfs import relocation_ended  # local: the Zotero engine imports this router
        relocation_ended(ws, page_id)

    try:
        return jobs.start(RELOCATE, owner=owner, ws=ws, key=f"{page_id}:{new_doc}", params=params, run=run,
                          ended=ended, title=f"Placing the highlights of “{name}”", writes=True), ""
    except jobs.Busy as busy:
        if not busy.job.get("stopping"):
            return busy.job, ""
        reason = "the last run for this file is still stopping — try again in a moment"
    except HTTPException as e:
        if not quiet:
            raise
        reason = str(e.detail)
    if not quiet:
        raise HTTPException(status_code=409, detail=reason)
    log.warning(f"[relocate] {page_id}: the job did not start ({reason})")
    return None, reason


class RelocateRequest(BaseModel):
    page_id: str


@router.post("/jobs/relocate-highlights")
def relocate_highlights(payload: RelocateRequest, request: Request):
    """Start the ``relocate-highlights`` job for the page by hand (the
    Document menu's "Find highlights in this PDF", a background task's Try
    again): every highlight placed on its PDF is found again by its text
    and moved there, the ones not found marked ``lost``, each read from
    the file its rectangles were measured on while that file is stored
    (``_start_relocation``), and one known by its page and quote alone is
    placed where its quote is, dashed. A text file's page has its ranges
    found again in its file the same way, by their quotes, always as the
    job (one task row), never at once. Session accounts only, like every
    job start (a token gets 403), and workspace editors. → the job, the
    running one when a run for this page and file is going already; 400 for
    a page without a PDF or a text file, with one shown from a URL alone
    (``NOT_STORED``) or listed without its bytes, or without highlights on
    it, 404 for no such page, 429 when the account's jobs are full, 409
    while the last run for this file is still stopping."""
    require_user_id(request)
    ws = require_ws(request, write=True)
    with connect_pages_db(ws) as conn:
        page = _load_page(conn, payload.page_id)
        view = page_document(page["properties"])
        text = view is not None and view["format"] == "text"
        placed = (text_anchors.page_ranges(conn, page["id"]) if text
                  else highlight_relocate.page_highlights(conn, page["id"], unplaced=True))
    if not (is_pdf(view) or text):
        raise HTTPException(status_code=400, detail="the page has no PDF or text file")
    new_doc = file_hash(view)
    if not new_doc:
        raise HTTPException(status_code=400, detail=LISTED if text else NOT_STORED)
    if not placed:
        raise HTTPException(status_code=400, detail="the page has no highlights placed on its "
                                                    + ("file" if text else "PDF"))
    job, _reason = _start_relocation(request.state.user_id, ws, page["id"], page["content"], new_doc,
                                     quiet=False, format=view["format"])
    return job


@router.delete("/pages/{page_id}/attachment")
def detach_pdf(page_id: str, request: Request):
    """Remove the page's document (``ATTACHMENT_KEYS``: its ``doc``, of any
    format, stored, read in place or at an address, and the
    ``original_filename`` a Markdown import left). Highlight blocks keep
    their positions; the file stays on disk, and unless something else
    references it the orphan bookkeeping purges it after 30 days
    (gamma/upload_gc.py) — a re-attach before then finds it. → ``{"ok",
    "block"}``."""
    ws = require_ws(request, write=True)
    with connect_pages_db(ws) as conn:
        write_lock(conn)  # the check and the write as one step, like attach_pdf
        page = _load_page(conn, page_id)
        props = dict(page["properties"])
        if not page_document(props):
            raise HTTPException(status_code=404, detail="page has no attachment")
        for key in ATTACHMENT_KEYS:
            props.pop(key, None)
        try:
            result = after_commit(ws, conn, apply_ops(
                conn, page_id, [{"op": "set", "id": page_id, "props": props_patch(page["properties"], props)}],
                actor=actor_of(request), client=writer_client(request)))
        except OpError as e:
            return op_refusal(e)
        text_index.purge_unused(ws, conn)
    return {"ok": True, "block": {**page, "properties": props, "updated_at": result["at"]}}


# --- a work folder's file and the library ---------------------------------------
# (docs/dev/work_folders.md "Add to library, Save a copy, Remove"). A file is
# in the library or not (``library``, work_folders/library.py); these three
# are the person's ways in and out, ops on the page like any other, so a NAS
# serves them too. A file's bytes come from this computer's path (its
# ``linked_files`` row: the vault's round reads it there) or are stored
# already.

NOT_HERE = "Its bytes are on another computer."
FILE_CHANGED = "The file changed: try again after the next sync."
TOO_LARGE = "This file is too large to keep in Gamma."  # a listed file (documents.model.is_listed) has no bytes to store


class LibraryFiling(BaseModel):
    folder: str = ""  # a library folder (a filing target) to file it in as well; "" for none


def _work_file(conn, page_id: str) -> dict:
    """The live page ``page_id`` (``_load_page``: 404, 400 for no page),
    400 unless it is a work folder's file."""
    page = _load_page(conn, page_id)
    if not work_folder_of(page["properties"]):
        raise HTTPException(status_code=400, detail="not a work folder's file")
    return page


def _in_library(page: dict) -> bool:
    return page["properties"].get("library") is True


def _stem(page: dict) -> str:
    """The file's name without its extension ("main" of ``main.pdf``), ""
    while the vault has written none."""
    name = page["properties"].get("file_name")
    return (os.path.splitext(name)[0] or name) if isinstance(name, str) else ""


def _library_folder(conn, folder: str) -> None:
    """400 for a ``folder`` that is no filing target (``marks.filing_targets``:
    a folder in no work folder)."""
    if folder and folder not in filing_targets(Tree.read(conn)):
        raise HTTPException(status_code=400, detail="that is no folder of the library")


def _held_by_library(conn, page: dict) -> None:
    """``HeldElsewhere`` (409, with its ``page_id``) when another library
    page owns the file's document (``page_for_file``: the library rule's,
    by hash and extension, any format)."""
    view = page_document(page["properties"])
    other = page_for_file(conn, own_hash(view), view["ext"], "id, content") if view else None
    if other and other[0] != page["id"]:
        raise HeldElsewhere(other[0], f"Already in the library as “{other[1] or 'Untitled'}”.")


def _store_bytes(ws: str, page: dict) -> None:
    """The file's document stored here when it is not yet, from the path
    this server reads it at (``linked_files.store_page_bytes``: the bytes
    its hash names, under the document's extension). 409 ``TOO_LARGE`` for
    a listed file (``is_listed``: too large to keep, it has no bytes here
    or anywhere in Gamma), ``NOT_HERE`` when no path here holds it (a vault
    of another computer), ``FILE_CHANGED`` when the file there is other
    bytes by now. A note, or a document stored already, needs nothing."""
    view = page_document(page["properties"])
    if view is None:
        return
    if is_listed(view):
        raise HTTPException(status_code=409, detail=TOO_LARGE)
    if not view["file"] or storage.exists(ws, view["file"]):
        return
    try:
        stored = config.folder_links_enabled() and linked_files.store_page_bytes(
            ws, page["id"], view["file"], own_hash(view))
    except folder_links.LinkError:
        raise HTTPException(status_code=409, detail=FILE_CHANGED)
    if not stored:
        raise HTTPException(status_code=409, detail=NOT_HERE)


def _summary(conn, page_id: str) -> dict:
    """The page as the library listing has it (``page_summary``)."""
    row = conn.execute(f"SELECT {SUMMARY_COLUMNS} FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    return page_summary(row, page_preview(conn, page_id), page_parents(conn).get(page_id, ""))


def _addable(conn, page_id: str, folder: str) -> dict:
    """The file an Add takes, checked: a work folder's file not in the
    library yet (400), ``folder`` a library folder (400), its PDF owned by
    no library page (``HeldElsewhere``)."""
    page = _work_file(conn, page_id)
    if _in_library(page):
        raise HTTPException(status_code=400, detail="the file is in the library already")
    _library_folder(conn, folder)
    _held_by_library(conn, page)
    return page


@router.post("/pages/{page_id}/library")
def add_file_to_library(page_id: str, payload: LibraryFiling, request: Request):
    """*Add to library…* on a work folder's file: it becomes a library
    document as well, the same page at its path. Its document's bytes, of
    any format, are stored first (``_store_bytes``), then one op on the page
    sets ``library`` and files it in its directory and in ``folder`` (a
    library folder) when one is given, and a PDF's title turns automatic
    (``auto_title``), so its record may title it as an upload's does; the
    PDF's manifest is scheduled (``pdf_meta.schedule``). → ``{page}``, its
    ``page_summary``. 400 for a page that is no file or is in the library
    already, or a ``folder`` that is no library folder; 409 with ``page_id``
    when a library page owns those bytes already ("Already in the library
    as …"), 409 when the bytes cannot be had (``TOO_LARGE``, ``NOT_HERE``,
    ``FILE_CHANGED``). Workspace editors."""
    ws = require_ws(request, write=True)
    folder = payload.folder.strip()
    try:
        with connect_pages_db(ws) as conn:  # the refusals before a file is read
            page = _addable(conn, page_id, folder)
        _store_bytes(ws, page)
        with connect_pages_db(ws) as conn:
            write_lock(conn)  # the checks again and the write as one step
            page = _addable(conn, page_id, folder)
            view = page_document(page["properties"])
            props = {"library": True, FOLDERS: filing(page["properties"], FOLDERS)[:1] + ([folder] if folder else [])}
            if is_pdf(view):
                props["auto_title"] = page["content"]
            after_commit(ws, conn, apply_ops(conn, page_id, [{"op": "set", "id": page_id, "props": props}],
                                             actor=actor_of(request)))
            out = _summary(conn, page_id)
    except HeldElsewhere as e:
        return e.answer()
    except OpError as e:
        return op_refusal(e)
    if is_pdf(view):
        pdf_meta.schedule(ws, own_hash(view))
    return {"page": out}


@router.delete("/pages/{page_id}/library")
def remove_file_from_library(page_id: str, request: Request):
    """*Remove from library* on a work folder's file in the library: one op
    on the page drops ``library`` (and with it the library folders it was
    filed in, ``work_folders.filing.root_patch``) and ``auto_title``, and
    gives it its file's name for a title, as the vault titles it: a PDF's
    stem (``_stem``: a paper's own title is the record's), any other file's
    whole ``file_name`` (gamma/vault/kinds.py ``FILE``). Its stored bytes
    stay while the page shows them. → ``{page}``. 400 for a page that is no
    file or not in the library. Workspace editors."""
    ws = require_ws(request, write=True)
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        page = _work_file(conn, page_id)
        if not _in_library(page):
            raise HTTPException(status_code=400, detail="the file is not in the library")
        op = {"op": "set", "id": page_id, "props": {"library": None, "auto_title": None}}
        name = page["properties"].get("file_name")
        title = _stem(page) if is_pdf(page_document(page["properties"])) else name if isinstance(name, str) else ""
        if title and title != page["content"]:
            op["content"] = title
        try:
            after_commit(ws, conn, apply_ops(conn, page_id, [op], actor=actor_of(request)))
        except OpError as e:
            return op_refusal(e)
        return {"page": _summary(conn, page_id)}


@router.post("/pages/{page_id}/library-copy")
def copy_file_to_library(page_id: str, payload: LibraryFiling, request: Request):
    """*Save a copy to library…* on a work folder's file with a document (a
    PDF, a figure, a data file: every format but a note, whose content is
    its blocks): its bytes as they are now stored (``_store_bytes``) and a
    library page made of them (``get_or_create_doc_page``: titled by the
    file's name, filed in ``folder``, a library folder), with no blocks,
    comments or stamps of the file's: it is annotated apart, a figure's
    version kept apart. A library page that owns those bytes already (the
    file once added, an earlier copy) answers instead. → ``{page,
    existed}``; a PDF's manifest is scheduled for a page made. 400 for a
    page that is no file with a document, or a ``folder`` that is no
    library folder; 409 when the bytes cannot be had (a listed file among
    them, ``TOO_LARGE``). Workspace editors."""
    ws = require_ws(request, write=True)
    folder = payload.folder.strip()
    with connect_pages_db(ws) as conn:
        page = _work_file(conn, page_id)
        view = page_document(page["properties"])
        if view is None:
            raise HTTPException(status_code=400, detail="only a work folder's file with a document is copied to the library")
        _library_folder(conn, folder)
    _store_bytes(ws, page)
    doc_id = own_hash(view)
    with connect_pages_db(ws) as conn:
        write_lock(conn)  # whether the file has a library page, and its making, as one step
        _library_folder(conn, folder)
        existed = page_for_file(conn, doc_id, view["ext"]) is not None
        try:
            block = get_or_create_doc_page(conn, doc_id, _stem(page), original_filename=view["name"], folder=folder,
                                           ws=ws, actor=actor_of(request), format=view["format"], ext=view["ext"],
                                           size=view["size"])
        except OpError as e:
            return op_refusal(e)
        out = _summary(conn, block["id"])
    if not existed and is_pdf(view):
        pdf_meta.schedule(ws, doc_id)
    return {"page": out, "existed": existed}
