"""Page-first endpoints (/api/pages*): create a page, look pages up by file,
make a page from a stored markdown file, attach, replace or detach a
page's PDF. A
page is a root block that may CARRY a PDF; the PDF is an action on an
existing page, not the way pages come into being. (``POST
/api/blocks/by-doc/{doc_id}`` is the lookup-or-create BY ATTACHMENT path for
PDF ingest and the extension's dedup.)

Every endpoint but ``by-docs`` (any member) needs an editor of the workspace
(``require_ws(write=True)``): a share token never creates pages or changes a
page's attachment (page properties stay the owner's, same rule as PUT
/blocks/{id} under a share).
"""

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import highlight_relocate, jobs, pdf_index, publish
from ..auth import actor_of, require_user_id, require_ws
from ..blocks_store import (
    BLOCK_COLUMNS,
    TRASH,
    attachment_file,
    attachment_props,
    block_to_dict,
    create_page,
    page_attachment,
    page_for_doc,
    pages_for_docs,
    valid_block_id,
    write_lock,
)
from ..db import connect_pages_db, connect_users_db, get_profile, safe_doc_id
from ..markdown_zip_import import markdown_page
from ..logbuf import log
from ..storage import find_upload_file
from ..ops import MAX_OPS, OpError, after_commit, after_commits, apply_batches, apply_ops, props_patch
from .imports import originals_gone

router = APIRouter(prefix="/api", tags=["pages"])

ATTACHMENT_KEYS = ("doc_id", "source_url", "original_filename")
RELOCATE = "relocate-highlights"  # the job that places a replaced PDF's highlights (_start_relocation)
NOT_STORED = "the PDF is not stored on the server"  # why no run places the highlights of a PDF shown from a URL alone
# Why a page of a Zotero link takes no PDF from a person while the link brings Zotero's (its ``zotero_pdf``
# is anything but "unavailable", gamma/zotero_sync.py): the two files would part, and Zotero's annotations
# with them. A detached link's page waits for the reattach, a clone's for its origin.
ZOTERO_PDF_COMING = "This page's PDF is in Zotero and comes from there: use Download now on the page"


class PageCreate(BaseModel):
    title: str = ""
    folders: list[str] = []   # folder ids to file it in
    id: str = ""              # a mirror bringing a page over keeps its id, /page the one its link names (409 when a live block has it)
    properties: dict = {}     # ...and its page properties


class AttachRequest(BaseModel):
    doc_id: str = ""              # content hash of an uploaded PDF, or the URL hash a proxied one gets
    source_url: str = ""          # where the viewer loads it from (not stored when it is doc_id's own upload path)
    original_filename: str = ""   # display name; becomes the title while it is still automatic


class HeldElsewhere(OpError):
    """The refusal of a ``doc_id`` another page carries already (409):
    ``page_id`` names that page, so a client can offer to open it."""

    def __init__(self, page_id: str):
        super().__init__(409, "attachment belongs to another page")
        self.page_id = page_id


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
    """A new text-only page: ``{title?, folders?, id?, properties?}`` → the
    page's block dict. Title defaults to "Untitled"; ``folders`` (folder
    ids) becomes ``properties.folders`` as given when it is a list of block
    ids (``create_page``). On a share host, 402 with ``{detail, limit, used,
    plan}`` when the owner's plan allows no more pages in the workspace (the
    path a publishing mirror creates its pages by; gamma/publish.py
    page_cap)."""
    ws = require_ws(request, write=True)
    props = dict(payload.properties or {})
    if payload.folders:
        props["folders"] = payload.folders
    if payload.id and not valid_block_id(payload.id):
        raise HTTPException(status_code=400, detail="invalid page id")
    with connect_pages_db(ws) as conn:
        # The checks and the insert as one step: two requests bringing the
        # same page id over (a mirror's retry) get one page and a 409, and
        # the plan's cap counts every page created before this one.
        write_lock(conn)
        taken = payload.id and conn.execute(
            "SELECT parent_id FROM unified_blocks WHERE id = ?", (payload.id,)).fetchone()
        if taken and taken[0] != TRASH:  # a page of that id in Recently deleted gives way (create_page)
            raise HTTPException(status_code=409, detail="a block with that id exists")
        refusal = publish.cap_refusal(ws)  # only a new page counts
        if refusal:
            return JSONResponse(status_code=402, content=refusal)
        return create_page(conn, payload.title, props, actor=actor_of(request), block_id=payload.id)


class DocsLookup(BaseModel):
    doc_ids: list[str] = []


@router.post("/pages/by-docs")
def pages_by_docs(payload: DocsLookup, request: Request):
    """Which pages these stored files became: ``{doc_ids: [<hash>, ...]}`` →
    ``{"pages": {hash: {id, title}}}`` — a hash matches the page carrying it
    as its PDF (``doc_id``) or the note page made from it (a markdown file's
    ``markdown_import``); hashes with no page are absent. The file chips ask
    this once per page render to show the "open page" button and label the
    menu "Open page" / "Open as page". Any member."""
    ws = require_ws(request)
    ids = [d for d in dict.fromkeys(payload.doc_ids[:500]) if d]
    with connect_pages_db(ws) as conn:
        return {"pages": pages_for_docs(conn, ids)}


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


def _attachment_body(payload: AttachRequest) -> tuple[str, str]:
    """The checked ``(doc_id, source_url)`` of an attach or replace body:
    ``doc_id`` is validated in shape only (like ``by-doc``, a URL-opened
    PDF's id is the URL hash and the proxy fetches the file lazily), and
    one of the two is required."""
    doc_id = (payload.doc_id or "").strip()
    source_url = (payload.source_url or "").strip()
    if doc_id:
        try:
            doc_id = safe_doc_id(doc_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid doc_id")
    if not doc_id and not source_url:
        raise HTTPException(status_code=400, detail="doc_id or source_url required")
    return doc_id, source_url


def _held_elsewhere(conn, doc_id: str):
    """The 409 for a ``doc_id`` a page carries already (``{"detail",
    "page_id"}``, so the client can offer to open that page), else None.
    Callers ask only for a ``doc_id`` the page itself does not carry, so
    any page found is another."""
    other = page_for_doc(conn, doc_id) if doc_id else None
    if other:
        return JSONResponse(status_code=409, content={
            "detail": "attachment belongs to another page", "page_id": other[0]})
    return None


def _zotero_marked(page: dict) -> bool:
    """Whether the page, which has no PDF, carries the mark of the one its
    Zotero item holds (a ``zotero_pdf`` whose state is not ``unavailable``)."""
    props = page.get("properties") or {}
    mark = props.get("zotero_pdf")
    return not page_attachment(props) and isinstance(mark, dict) and mark.get("state") != "unavailable"


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


def _attachment_op(page: dict, doc_id: str, source_url: str, original_filename: str) -> tuple[dict, str, dict, bool]:
    """The op that makes ``doc_id`` / ``source_url`` / ``original_filename``
    the page's attachment, whatever it carried before, with the page's
    title and properties after it. While the title is still automatic
    ("Untitled"/empty) it becomes the file name (or URL tail) and is marked
    ``auto_title``, the marker metadata may replace (same semantics as
    get_or_create_doc_page; an explicit rename clears it). Any other title
    stays. When the file changes (another ``doc_id`` or address than the
    page showed, ``page_attachment``), a failed metadata lookup's
    ``meta_error`` goes: that lookup read the file the page carried, and
    the new one is looked up again. → ``(op, title, properties, file
    changed)``; an op whose ``props`` patch is empty and that has no
    ``content`` changes nothing."""
    before = page_attachment(page["properties"])
    props = {k: v for k, v in page["properties"].items() if k not in ATTACHMENT_KEYS}
    attachment, auto = attachment_props(doc_id, source_url, original_filename)
    props.update(attachment)
    after = page_attachment(props)
    moved = before is None or (before["id"], before["url"]) != (after["id"], after["url"])
    if moved:
        props.pop("meta_error", None)
    content = page["content"]
    if not content.strip() or content.strip() == "Untitled":
        content = auto or content
        props["auto_title"] = content
    op = {"op": "set", "id": page["id"], "props": props_patch(page["properties"], props)}
    if content != page["content"]:
        op["content"] = content
    return op, content, props, moved


@router.post("/pages/{page_id}/attachment")
def attach_pdf(page_id: str, payload: AttachRequest, request: Request):
    """Attach a PDF to an existing page that has none. Body: ``doc_id``
    and/or ``source_url``, plus an optional ``original_filename``
    (``_attachment_body``). 409 when the page already carries an attachment
    (``PUT`` replaces one), when another page already carries this
    ``doc_id`` (``_held_elsewhere``), or while something brings the PDF its
    Zotero item holds (``_zotero_pdf_refusal``: a page whose mark nothing
    honours any more takes the PDF, and loses the mark). An automatic title
    becomes the file name (``_attachment_op``). Returns the updated block."""
    ws = require_ws(request, write=True)
    doc_id, source_url = _attachment_body(payload)
    with connect_pages_db(ws) as conn:
        # The checks below and the write as one step: two pages attaching the
        # same PDF at once can't both pass "no other page carries it".
        write_lock(conn)
        page = _load_page(conn, page_id)
        if page_attachment(page["properties"]):
            raise HTTPException(status_code=409, detail="page already has an attachment")
        refusal = _zotero_pdf_refusal(ws, page)
        if refusal:
            raise HTTPException(status_code=409, detail=refusal)
        conflict = _held_elsewhere(conn, doc_id)
        if conflict:
            return conflict
        op, content, props, _moved = _attachment_op(page, doc_id, source_url, payload.original_filename)
        if refusal == "":  # a Zotero mark nothing honours any more goes with the attach
            props.pop("zotero_pdf", None)
            op["props"] = props_patch(page["properties"], props)
        result = after_commit(ws, conn, apply_ops(conn, page_id, [op], actor=actor_of(request)))
    return {**page, "content": content, "properties": props, "updated_at": result["at"]}


@router.put("/pages/{page_id}/attachment")
def replace_pdf(page_id: str, payload: AttachRequest, request: Request):
    """Make this PDF the page's attachment, in place of the one it carries
    (a new version of the file: a paper compiled again from its TeX). Body
    and 409s as ``POST``'s; a page without an attachment gets this one as
    ``POST`` would give it. The page keeps its id, title, notes and
    highlights. The same attachment again changes nothing. The work,
    described there, is ``replace_attachment``'s, which the Zotero link
    calls too.

    Returns the updated block, with the relocation job as ``job``, or,
    when no run started, the reason as ``relocation_error``: the account's
    background tasks are full (the Document menu's Find highlights in this
    PDF places the highlights later), or the new PDF is shown from a URL
    alone (``NOT_STORED``: no file to read). The replacement stands
    either way."""
    ws = require_ws(request, write=True)
    doc_id, source_url = _attachment_body(payload)
    try:
        done = replace_attachment(ws, page_id, doc_id, source_url, payload.original_filename,
                                  actor=actor_of(request), owner=request.state.user_id)
    except HeldElsewhere as e:
        return JSONResponse(status_code=409, content={"detail": e.detail, "page_id": e.page_id})
    except OpError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    if not done["results"]:
        return done["page"]
    answer = {**done["page"], "content": done["content"], "properties": done["props"],
              "updated_at": done["results"][0]["at"]}
    if done["job"]:
        answer["job"] = done["job"]
    elif done["relocation_error"]:
        answer["relocation_error"] = done["relocation_error"]
    return answer


def replace_attachment(ws: str, page_id: str, doc_id: str, source_url: str = "", original_filename: str = "", *,
                       actor: str, client: str = "", owner, check_held: bool = True, props: dict | None = None,
                       check=None, rows=None, check_zotero: bool = True) -> dict | None:
    """Make ``doc_id`` / ``source_url`` the page's PDF, in place of what it
    carries (or as its first), in one transaction under the write lock,
    then start placing its highlights. PUT /pages/{id}/attachment and the
    Zotero link (gamma/zotero_local.py, ``check_held=False``: linked items
    may share one PDF, and ``page_for_doc`` then answers the oldest page)
    both go through here.

    In the transaction: ``check(conn)``, a caller's compare-and-set, runs
    first (False: nothing is written, and the answer is None); the page is
    read (OpError 404 when it is gone or trashed, 400 for a block that is
    no page; unless ``check_zotero`` is False, 409 while something brings
    its Zotero item's PDF, ``_zotero_pdf_refusal``, and a mark nothing
    honours any more is dropped with the write); unless ``check_held`` is
    False, a ``doc_id`` another page carries is refused (``HeldElsewhere``,
    409); the attachment op (``_attachment_op``: an automatic title becomes
    the file name, a failed lookup's ``meta_error`` goes when the file
    changes) is written, with ``props`` (a page property patch the caller
    adds, such as dropping ``zotero_pdf``) merged into it. When the file
    changes (whatever the page showed it from: a ``doc_id``, or only a
    ``source_url`` as on a duplicated document page), the blocks imported
    from the old file's embedded annotations are marked gone from the file
    (``imports.originals_gone``), so PDF export draws them again. When the
    stored file changes (``attachment_file``: a duplicated page's
    ``/api/uploads/<doc id>.pdf`` address is its stored file) and the page
    has highlights placed on its PDF, each of them that names no file yet
    gets the outgoing file's id as ``pdf_position.doc_id`` (the file its
    rectangles were measured on; ``highlight_relocate.UNSTORED`` for a page
    that showed its file from a URL alone), and a lost one's ``closest`` (a
    place in the outgoing file) goes. ``rows(conn)`` runs last, for a
    caller's own rows in the same transaction. The batches are logged under
    ``actor`` and ``client``. After the commit (inside the connection's
    block) the room hears the batches, and when the stored file changed the
    old file's search rows and manifest go; unless something else
    references it the file is purged after 30 days (gamma/upload_gc.py).
    The same attachment again writes no op (only ``rows``) and answers
    ``results`` [].

    Then, when highlights were stamped and the new file is stored, the
    ``relocate-highlights`` job owned by ``owner`` starts
    (``_start_relocation``): each highlight is found again in the new file
    by its text and moved there; until it is done they sit where they were.

    → ``{page (as read), content, props (the page's title and properties
    after), results (the batches' results), placed ([(block id, props)] of
    the highlights stamped), incoming, outgoing (the stored files' doc ids,
    "" for none), job (the run, or None), relocation_error (why no run
    started; "" when one did or none was needed)}``."""
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
            old = str(page["properties"].get("doc_id") or "")
            if check_held and doc_id and doc_id != old:
                other = page_for_doc(conn, doc_id)
                if other:
                    raise HeldElsewhere(other[0])
            had = page_attachment(page["properties"]) is not None
            op, content, after, moved = _attachment_op(page, doc_id, source_url, original_filename)
            if props:
                for key, value in props.items():
                    if value is None:
                        after.pop(key, None)
                    else:
                        after[key] = value
                op["props"] = props_patch(page["properties"], after)
            outgoing = attachment_file(page_attachment(page["properties"]))
            incoming = attachment_file(page_attachment(after))
            if not op["props"] and "content" not in op:
                if rows is not None:
                    rows(conn)
                    conn.commit()
                else:
                    conn.rollback()  # nothing to write: let go of the lock
                return {"page": page, "content": page["content"], "props": page["properties"], "results": [],
                        "placed": [], "incoming": incoming, "outgoing": outgoing, "job": None,
                        "relocation_error": ""}
            marks = originals_gone(conn, page_id) if had and moved else []
            swapped = had and moved and not (outgoing and outgoing == incoming)  # another file, not another address
            placed = highlight_relocate.page_highlights(conn, page_id) if swapped else []
            stamps = []
            for bid, p in placed:
                # the file its rects were measured on, where it names none, and no closest place a lost one kept:
                # that is a place in the outgoing file (the run finds the new file's)
                stamped = {k: v for k, v in p["pdf_position"].items() if k != "closest"}
                if not stamped.get("doc_id"):
                    stamped["doc_id"] = outgoing or highlight_relocate.UNSTORED
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
        if old and old != doc_id:
            pdf_index.purge_unused(ws, conn)
    job, reason = None, ""
    if placed:
        job, reason = (_start_relocation(owner, ws, page["id"], content, incoming, outgoing=outgoing)
                       if incoming else (None, NOT_STORED))
    return {"page": page, "content": content, "props": after, "results": results, "placed": placed,
            "incoming": incoming, "outgoing": outgoing, "job": job, "relocation_error": "" if job else reason}


def _start_relocation(owner: str, ws: str, page_id: str, title: str, new_doc: str, *,
                      outgoing: str | None = None, quiet: bool = True) -> tuple[dict | None, str]:
    """Start the ``relocate-highlights`` job that places the page's
    highlights in its PDF ``new_doc`` (gamma/highlight_relocate.py
    ``relocate``), owned by the account ``owner`` (the request's, or a
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
    is told (``zotero_sync_pdfs.relocation_ended``): a PDF of the page
    that waited for its highlights to be placed, and the annotations held
    for them, are decided again."""
    name = " ".join((title or "").split())[:80] or "Untitled"
    limits = highlight_relocate.thresholds(get_profile(owner)[0])
    params = {"page_id": page_id, "title": name, "doc_id": new_doc, **limits}
    if outgoing is not None:
        previous = jobs.latest(owner, RELOCATE, f"{page_id}:{outgoing}")
        if previous and previous["state"] in jobs.ACTIVE:
            try:
                jobs.cancel(previous["id"])
            except HTTPException:
                pass  # past its last stop: it finishes, and its write is refused (the page carries another file)

    def run(job):
        try:
            return highlight_relocate.relocate(ws, page_id, new_doc, actor=owner, progress=job.progress, **limits)
        finally:
            from ..zotero_sync_pdfs import relocation_ended  # local: the Zotero engine imports this router
            try:
                relocation_ended(ws, page_id)
            except Exception as e:  # noqa: BLE001 — the run's own outcome stands
                log.warning(f"[relocate] {page_id}: the Zotero link was not told the run ended: {e}")

    try:
        return jobs.start(RELOCATE, owner=owner, ws=ws, key=f"{page_id}:{new_doc}", params=params, run=run,
                          title=f"Placing the highlights of “{name}”"), ""
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
    (``_start_relocation``). Session accounts only, like every job start
    (a token gets 403), and workspace editors. → the job, the running one
    when a run for this page and file is going already; 400 for a page
    without a PDF, with one shown from a URL alone (``NOT_STORED``), or
    without highlights on it, 404 for no such page, 429 when the account's
    jobs are full, 409 while the last run for this file is still
    stopping."""
    require_user_id(request)
    ws = require_ws(request, write=True)
    with connect_pages_db(ws) as conn:
        page = _load_page(conn, payload.page_id)
        placed = highlight_relocate.page_highlights(conn, page["id"])
    attachment = page_attachment(page["properties"])
    if not attachment:
        raise HTTPException(status_code=400, detail="the page has no PDF")
    new_doc = attachment_file(attachment)
    if not new_doc:
        raise HTTPException(status_code=400, detail=NOT_STORED)
    if not placed:
        raise HTTPException(status_code=400, detail="the page has no highlights placed on its PDF")
    job, _reason = _start_relocation(request.state.user_id, ws, page["id"], page["content"], new_doc,
                                     quiet=False)
    return job


@router.delete("/pages/{page_id}/attachment")
def detach_pdf(page_id: str, request: Request):
    """Remove the page's PDF attachment (``doc_id`` / ``source_url`` /
    ``original_filename``). Highlight blocks keep their ``pdf_position``;
    the file stays on disk, and unless something else references it the
    orphan bookkeeping purges it after 30 days (gamma/upload_gc.py) — a
    re-attach before then finds it. → ``{"ok", "block"}``."""
    ws = require_ws(request, write=True)
    with connect_pages_db(ws) as conn:
        write_lock(conn)  # the check and the write as one step, like attach_pdf
        page = _load_page(conn, page_id)
        props = dict(page["properties"])
        if not page_attachment(props):
            raise HTTPException(status_code=404, detail="page has no attachment")
        for key in ATTACHMENT_KEYS:
            props.pop(key, None)
        result = after_commit(ws, conn, apply_ops(
            conn, page_id, [{"op": "set", "id": page_id,
                             "props": props_patch(page["properties"], props)}], actor=actor_of(request)))
        pdf_index.purge_unused(ws, conn)
    return {"ok": True, "block": {**page, "properties": props, "updated_at": result["at"]}}
