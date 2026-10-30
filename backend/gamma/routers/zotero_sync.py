"""The Zotero link API (docs/dev/zotero_sync.md): link a Zotero library to a
personal workspace, run a round, read its status, its log and the
conflicts it recorded, take them, and the dialog's counts before linking.
Session-only (an integration token is refused: these are the account's),
personal accounts only, the link's owner only, answering as the clone API
does (routers/mirrors.py). Fetching a page's PDF first is open to any
member who may read the page: it only reorders the link's work.

Every route touches the database or Zotero, so each is a sync ``def``: the
threadpool runs it and the event loop stays free."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import config, ratelimit, sync_clock, workspaces, zotero_stream, zotero_sync
from ..auth import require_personal_user_id, require_user_id
from ..ops import OpError
from ..zotero_account import AccountError
from ..zotero_web import LIBRARY_RE

router = APIRouter(prefix="/api/zotero/links", tags=["zotero"])

PERSONAL_ONLY = "Sign in with a personal account to link a Zotero library."
NO_LINK = "no such Zotero link"


class LinkCreate(BaseModel):
    library: str = Field(pattern=LIBRARY_RE.pattern)
    workspace_id: str = ""      # link an existing workspace of mine instead of making a new one
    name: str = Field(default="", max_length=200)
    mode: str = Field(default="two-way", pattern="^(two-way|pull)$")
    download: str = Field(default="all", pattern="^(all|open)$")
    upload: bool = True
    auto_tags: bool = False
    send_unrecorded: bool = False
    adopt: str = Field(default="theirs", pattern="^(theirs|mine)$")


class LinkPatch(BaseModel):
    poll_s: int | None = Field(default=None, ge=0, le=sync_clock.POLL_MAX_S)
    on_change: bool | None = None
    mode: str | None = Field(default=None, pattern="^(two-way|pull)$")
    download: str | None = Field(default=None, pattern="^(all|open)$")
    upload: bool | None = None
    auto_tags: bool | None = None
    send_unrecorded: bool | None = None


class Force(BaseModel):
    direction: str = Field(pattern="^(pull|push)$")


class Fetch(BaseModel):
    page_id: str = Field(min_length=1, max_length=64)


class Resolution(BaseModel):
    choice: str = Field(pattern="^(keep|mine|theirs)$")


def _me(request: Request) -> str:
    return require_personal_user_id(request, PERSONAL_ONLY)


def _owns(user_id: str, link: dict) -> bool:
    """The link is the caller's: recorded as its owner, and still the owner
    of the workspace it keeps."""
    return link["owner"] == user_id and workspaces.role_of(link["workspace_id"], user_id) == "owner"


def _mine(request: Request, ws: str) -> dict:
    user_id = _me(request)
    link = zotero_sync.get_link(ws)
    if not link or not _owns(user_id, link):
        raise HTTPException(status_code=404, detail=NO_LINK)
    return link


def _refused(e: ValueError) -> HTTPException:
    """A refusal's answer: an AccountError's own status (409: connect
    Zotero first, or again; 502: Zotero could not be asked), 400 for the
    rest."""
    return HTTPException(status_code=e.status if isinstance(e, AccountError) else 400, detail=str(e))


def _info(link: dict) -> dict:
    """A link as the API shows it to its owner (who needs no owner id). A
    fill's count (``status.fill``) shows only while the fill goes on (its
    round runs, or is asked for)."""
    ws = link["workspace_id"]
    info = workspaces.get(ws)
    count, newest = zotero_sync.open_conflict_mark(ws)
    if link["status"].get("fill") and not link["status"].get("running") and not zotero_sync.following(link):
        link = {**link, "status": {k: v for k, v in link["status"].items() if k != "fill"}}
    return {"kind": "zotero", "workspace_id": ws, "name": info["name"] if info else "",
            "library": link["library"], "library_name": link["library_name"], "remote_name": link["library_name"],
            "remote_url": zotero_sync.remote_url(link), "mode": link["mode"], "download": link["download"],
            "upload": link["upload"], "auto_tags": link["auto_tags"], "send_unrecorded": link["send_unrecorded"],
            "poll_s": link["poll_s"], "on_change": link["on_change"], "status": link["status"],
            "created_at": link["created_at"], "conflicts_open": count, "conflicts_newest": newest,
            "pending_local": zotero_sync.pending_local(link), "detached": link["mode"] == "off",
            "interval_s": config.sync_interval_s()}


@router.get("")
def list_links(request: Request):
    """``{links: [info…]}``: the caller's."""
    user_id = _me(request)
    return {"links": [_info(link) for link in zotero_sync.list_links(user_id) if _owns(user_id, link)]}


@router.post("", status_code=201)
def create_link(payload: LinkCreate, request: Request):
    """Link a library the caller's Zotero key reads: ``{library,
    workspace_id?, name?, mode, download, upload, auto_tags,
    send_unrecorded, adopt}`` → the link's info; its first round runs in
    the background."""
    user_id = _me(request)
    try:
        link = zotero_sync.create_link(user_id, payload.library, workspace_id=payload.workspace_id,
                                       name=payload.name, mode=payload.mode, download=payload.download,
                                       upload=payload.upload, auto_tags=payload.auto_tags,
                                       send_unrecorded=payload.send_unrecorded, adopt=payload.adopt)
    except ValueError as e:  # (a second link of the library too, the index's refusal as the check's)
        raise _refused(e) from None
    zotero_sync.sync_in_background(link["workspace_id"])
    zotero_stream.refresh()  # a Live link's key joins the stream's subscriptions now, not at its next check
    return _info(link)


@router.get("/preview")
def preview(request: Request, library: str = "", workspace_id: str = ""):
    """What linking ``library`` would move, for the dialog: ``{items, notes,
    attachments, pdfs, pdf_bytes, linked_files, partial}``, and into
    ``workspace_id`` ``{matches, to_send, to_send_bytes, collections_to_add,
    tags_to_add, notes_to_send, highlights_to_send, highlights_new}`` too
    (``zotero_sync.preview``). 429 while the account's last count still
    runs, or past 30 counts in 10 minutes; 503 when Zotero kept it waiting."""
    user_id = _me(request)
    ratelimit.check(f"zotero-preview:account:{user_id}", 30, 600)
    try:
        return zotero_sync.preview(user_id, library, workspace_id)
    except ValueError as e:
        raise _refused(e) from None


@router.get("/{ws}")
def get_link(ws: str, request: Request):
    return _info(_mine(request, ws))


@router.patch("/{ws}")
def update_link(ws: str, payload: LinkPatch, request: Request):
    """The cadence (``poll_s``: 5 Live, 0 only by hand; ``on_change``), the
    direction and what travels."""
    _mine(request, ws)
    try:
        link = zotero_sync.set_settings(ws, **payload.model_dump())
    except ValueError as e:
        raise _refused(e) from None
    zotero_stream.refresh()  # Live turned on or off
    return _info(link)


@router.post("/{ws}/sync")
def run_sync(ws: str, request: Request, wait: int = 0):
    """One round now: ``wait=1`` runs it inline and answers with its status;
    otherwise it runs in the background and the status before it is
    returned. Sync now lifts a wait on Zotero's full storage: the PDFs it
    held are tried again."""
    link = _mine(request, ws)
    zotero_sync.lift_quota(ws)
    if wait:
        return {"status": zotero_sync.sync_workspace(ws), "conflicts_open": zotero_sync.open_conflict_mark(ws)[0]}
    zotero_sync.sync_in_background(ws)
    return {"status": link["status"], "conflicts_open": zotero_sync.open_conflict_mark(ws)[0]}


@router.post("/{ws}/fetch")
def fetch(ws: str, payload: Fetch, request: Request):
    """``{page_id}``: the page's PDF in Zotero first, then the page (someone
    opened it). Any member who may read the page."""
    user_id = require_user_id(request)
    if not zotero_sync.get_link(ws) or not workspaces.role_of(ws, user_id):
        raise HTTPException(status_code=404, detail=NO_LINK)
    if not zotero_sync.fetch_page(ws, payload.page_id):
        raise HTTPException(status_code=404, detail="no such page")
    return {"queued": True}


@router.post("/{ws}/detach")
def detach(ws: str, request: Request):
    """Detach: no round runs; the link and its rows are kept for a relink."""
    _mine(request, ws)
    link = zotero_sync.detach_link(ws)
    zotero_stream.refresh()
    return _info(link)


@router.post("/{ws}/relink")
def relink(ws: str, request: Request):
    """Link a detached workspace again; a round runs in the background."""
    _mine(request, ws)
    try:
        link = zotero_sync.relink_link(ws)
    except ValueError as e:
        raise _refused(e) from None
    zotero_sync.sync_in_background(ws)
    zotero_stream.refresh()
    return _info(link)


@router.post("/{ws}/force")
def force(ws: str, payload: Force, request: Request):
    """``pull``: take Zotero's version of everything both sides have;
    ``push``: Gamma's, the trash included (``zotero_sync.force_sync``): what
    one side holds deleted goes to the other's Recently deleted or Zotero
    trash, except that a note or annotation a pull takes off its page keeps
    no copy here (Zotero's trash holds it). A push still takes Zotero's PDF
    whose annotations Gamma may not write and Zotero's note Gamma reads lossily;
    a PDF that may not go (or come) stays as it is on both sides. The
    records, notes and annotations it replaces are kept only in resolved
    conflict rows (``…/conflicts?resolved=1``), counted in a log line per
    round; a page's PDF replaced here is kept in none, and Zotero's PDF a
    push writes over is kept here in an open conflict. Runs in the
    background."""
    _mine(request, ws)
    try:
        zotero_sync.force_sync(ws, payload.direction)
    except ValueError as e:
        raise _refused(e) from None
    return _info(_mine(request, ws))


@router.delete("/{ws}")
def delete_link(ws: str, request: Request):
    """Forget the link. The workspace stays, as an ordinary one; Zotero's
    library is not touched."""
    _mine(request, ws)
    zotero_sync.remove_link(ws)
    zotero_stream.refresh()
    return {"ok": True}


@router.get("/{ws}/log")
def sync_log(ws: str, request: Request, limit: int = 50):
    """What the last rounds did, newest first: ``{changes: [{id, at,
    page_id, title, action, stats, changes, exists}]}``."""
    _mine(request, ws)
    return {"changes": zotero_sync.list_log(ws, limit)}


@router.get("/{ws}/conflicts")
def list_conflicts(ws: str, request: Request, resolved: int = 0, page: str = ""):
    """``{conflicts: [{id, page_id, page_title, block_id, kind, mine, theirs,
    result, at, resolved, base}]}``; one page's with ``?page=``."""
    _mine(request, ws)
    return {"conflicts": zotero_sync.list_conflicts(ws, resolved=bool(resolved), page_id=page)}


@router.post("/{ws}/conflicts/{conflict_id}")
def resolve_conflict(ws: str, conflict_id: int, payload: Resolution, request: Request):
    """``{choice: keep | mine | theirs}``: the chosen side written first, then
    the row resolved; a choice the conflict does not offer, or a write the
    block refuses, is a 409 and leaves it open."""
    _mine(request, ws)
    try:
        out = zotero_sync.resolve_conflict(ws, conflict_id, payload.choice)
    except OpError as e:
        raise HTTPException(status_code=409, detail=f"that could not be written: {e}")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not out:
        raise HTTPException(status_code=404, detail="no such conflict")
    return out
