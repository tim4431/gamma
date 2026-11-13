"""Feedback on a work folder's files and the Sends that ask an agent to act
on it (gamma/feedback/, docs/dev/feedback.md "The routes").

Every route is the workspace's members' — any role — and integration
tokens' of any scope, a token in its own workspace as everywhere on the
HTTP API; a share visitor, signed in or not, is refused. A Send is made by
a member who may edit (or a write token): it starts work on the owner's
computer. Its state is set by an agent (a feedback or write token) or, to
withdraw it, by its requester. Every handler is a sync ``def``: it reads
the pages.db, and the pictures are drawn, off the event loop.
"""

from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from .. import workspaces
from ..auth import is_token, require_ws
from ..db import connect_pages_db
from ..feedback import entries, markdown, pictures, requests
from ..feedback.entries import FeedbackError
from ..integrations import display_name

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

FORMATS = ("json", "md")
MARKDOWN = "text/markdown; charset=utf-8"
PICTURE_CACHE = "private, max-age=86400"


class RequestCreate(BaseModel):
    id: str = ""                    # the client's id, the same on every retry
    scope: str                      # a file's page id, or a work folder's or a directory's folder id
    note: str = ""                  # the line of instruction
    threads: list[str] | None = None  # some of the scope's open threads; omitted, every one


class RequestState(BaseModel):
    state: str
    detail: str = ""


@contextmanager
def _refusals():
    try:
        yield
    except FeedbackError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


def _member(request: Request, *, write: bool = False) -> str:
    """The workspace of a member's or a token's request (``require_ws``);
    403 through a share link."""
    if request.query_params.get("share"):
        raise HTTPException(status_code=403, detail="feedback belongs to the workspace's members")
    return require_ws(request, write=write)


def _format(format: str) -> str:
    if format not in FORMATS:
        raise HTTPException(status_code=400, detail="format is json or md")
    return format


@router.get("")
def read_feedback(request: Request, scope: str = "", state: str = "open", format: str = "json"):
    """The feedback of a work folder, a directory or a file
    (``entries.document``, its entries with ``picture_url`` and
    ``before_url``; ``owner`` the requester's username), or with
    ``format=md`` its Markdown (``text/markdown``, the pictures as their
    routes). 400 for a library page or folder, another state or format;
    404 for no such folder or live page."""
    ws = _member(request)
    _format(format)
    with connect_pages_db(ws) as conn, _refusals():
        doc = entries.document(ws, conn, entries.resolve_scope(conn, scope), state=state,
                               owner=request.state.user or "")
    doc["entries"] = [entries.with_urls(e) for e in doc["entries"]]
    if format == "md":
        return Response(markdown.render(doc, pictures="urls"), media_type=MARKDOWN)
    return doc


def _entry(request: Request, thread_id: str, regions: dict | None = None) -> tuple[str, dict]:
    """The thread's entry in the request's workspace; with ``regions`` (a
    picture's read) its pictures' regions, and no SyncTeX lookup. 404 for
    a thread hidden, gone or on no work folder's file."""
    ws = _member(request)
    with connect_pages_db(ws) as conn:
        found = entries.entry(ws, conn, thread_id, regions=regions, sources=regions is None)
    if found is None:
        raise HTTPException(status_code=404, detail="no feedback item has that id")
    return ws, found


@router.get("/threads/{thread_id}")
def read_thread(thread_id: str, request: Request, format: str = "json"):
    """One thread's entry in any state → ``{entry}``, or its Markdown. 404
    for a thread hidden, gone or not on a work folder's file."""
    _format(format)
    _, found = _entry(request, thread_id)
    found = entries.with_urls(found)
    if format == "md":
        return Response(f"## {found['file']}\n\n" + markdown.render_entry(found, pictures="urls"), media_type=MARKDOWN)
    return {"entry": found}


@router.get("/threads/{thread_id}/picture")
def thread_picture(thread_id: str, request: Request, at: str = "now", v: str = ""):
    """The JPEG of a thread's region: ``at=now`` as it is on the current
    file, ``before`` as it was commented on. ``v`` (the picture's key) only
    busts a browser's cache. 404 when the entry has no such picture, or it
    cannot be drawn now."""
    if at not in ("now", "before"):
        raise HTTPException(status_code=400, detail="at is now or before")
    regions: dict = {}
    ws, found = _entry(request, thread_id, regions)
    key = entries.key_of(found["picture" if at == "now" else "before"])
    if not key or key not in regions:
        raise HTTPException(status_code=404, detail="this feedback item has no such picture")
    page_id, region = regions[key]
    with connect_pages_db(ws) as conn:
        data = pictures.cached(ws, conn, page_id, region)
    if not data:
        raise HTTPException(status_code=404, detail="this picture cannot be drawn now")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": PICTURE_CACHE})


@router.post("/requests", status_code=201)
def create_request(payload: RequestCreate, request: Request):
    """A Send → ``{request}`` (201; 200 for a repeated ``id`` of the same
    account, answered as stored). A member who may edit, or a write token.
    400 for a scope outside a work folder, nothing to send or a thread that
    is no open one in the scope; 404 for no such scope; 409 for another
    account's id; 413 for a longer note."""
    ws = _member(request, write=True)
    with connect_pages_db(ws) as conn, _refusals():
        out = requests.create(ws, conn, entries.resolve_scope(conn, payload.scope), by=request.state.user_id,
                              by_name=request.state.user or "", note=payload.note, threads=payload.threads,
                              request_id=payload.id)
    if not out["created"]:
        return JSONResponse(status_code=200, content={"request": out["request"]})
    return {"request": out["request"]}


@router.get("/requests")
def list_requests(request: Request, after: int = 0, scope: str = "", state: str = "", limit: int = 50):
    """``{requests, cursor}``: the requests made or changed since the
    cursor ``after``, oldest first (``requests.listing``); ``scope`` a work
    folder's or a file's id, ``state`` one state, ``limit`` at most 100."""
    ws = _member(request)
    with connect_pages_db(ws) as conn, _refusals():
        return requests.listing(conn, after=after, scope=scope, state=state, limit=limit)


@router.get("/requests/{request_id}")
def read_request(request_id: str, request: Request):
    """``{request, entries}``: the request and the entries of its threads as
    they are now, what its file holds. 404 for no such request."""
    ws = _member(request)
    with connect_pages_db(ws) as conn:
        record = requests.get(conn, request_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no such request")
        found = requests.entries_of(ws, conn, record)
    return {"request": record, "entries": [entries.with_urls(e) for e in found]}


@router.post("/requests/{request_id}/state")
def set_request_state(request_id: str, payload: RequestState, request: Request):
    """Set a request's state → ``{request}``: an agent (an integration token
    of the feedback or write scope) any change ``requests.TRANSITIONS``
    lets it make, the requester's session a withdraw (``failed``). 403 for
    anyone else, 404 for no such request, 409 for a change refused, 413
    for a longer detail; 423 while a delete holds the workspace."""
    ws = _member(request)
    if is_token(request):
        if request.state.token_scope not in ("feedback", "write"):
            raise HTTPException(status_code=403, detail="this token is read-only")
        writer, by_name = "agent", display_name(request.state.token_name)
    else:
        writer, by_name = "requester", request.state.user or ""
    workspaces.refuse_writes(ws)
    with connect_pages_db(ws) as conn, _refusals():
        by = requests.requester(conn, request_id)
        if by is None:
            raise FeedbackError(404, "no such request")
        if writer == "requester" and by != request.state.user_id:
            raise FeedbackError(403, "only an agent or the request's sender sets its state")
        return {"request": requests.set_state(ws, conn, request_id, payload.state, detail=payload.detail,
                                              by_name=by_name, writer=writer)}
