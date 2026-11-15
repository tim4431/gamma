"""The annotations reader: the highlights and comment threads of a page, a
folder or the library as entries an agent acts on (gamma/annotations/,
docs/dev/annotations.md "The routes").

Every route is the workspace's members' — any role — and integration
tokens' of any scope, a token in its own workspace as everywhere on the
HTTP API; a share visitor, signed in or not, is refused. Every handler is a
sync ``def``: it reads the pages.db, and the pictures are drawn, off the
event loop.
"""

from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..annotations import entries, markdown, pictures
from ..annotations.entries import AnnotationError
from ..auth import require_ws
from ..db import connect_pages_db

router = APIRouter(prefix="/api/annotations", tags=["annotations"])

FORMATS = ("json", "md")
MARKDOWN = "text/markdown; charset=utf-8"
PICTURE_CACHE = "private, max-age=86400"
AT = ("now", "before")


@contextmanager
def refusals():
    """An ``AnnotationError`` answered as an HTTPException."""
    try:
        yield
    except AnnotationError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


def member(request: Request) -> str:
    """The workspace of a member's or a token's request (``require_ws``);
    403 through a share link."""
    if request.query_params.get("share"):
        raise HTTPException(status_code=403, detail="annotations belong to the workspace's members")
    return require_ws(request)


def _format(format: str) -> str:
    if format not in FORMATS:
        raise HTTPException(status_code=400, detail="format is json or md")
    return format


@router.get("")
def read_annotations(request: Request, scope: str = "", state: str = "open", format: str = "json"):
    """The annotations of a page, a folder or the library (``scope``: a
    page's or a folder's id, a work folder's or a directory's among them,
    or ``home``; ``entries.document``, its entries with ``picture_url`` and
    ``before_url``; ``owner`` the reader's username), or with ``format=md``
    its Markdown (``text/markdown``, the pictures as their routes). 400 for
    another state or format; 404 for no such folder or live page."""
    ws = member(request)
    _format(format)
    with connect_pages_db(ws) as conn, refusals():
        doc = entries.document(ws, conn, entries.resolve_scope(conn, scope), state=state,
                               owner=request.state.user or "")
    doc["entries"] = [entries.with_urls(e) for e in doc["entries"]]
    if format == "md":
        return Response(markdown.render(doc, pictures="urls"), media_type=MARKDOWN)
    return doc


def _entry(ws: str, entry_id: str, regions: dict | None = None) -> dict:
    """The entry ``entry_id`` of ``ws`` (a thread's id, or a highlight's
    block id); with ``regions`` (a picture's read) its pictures' regions,
    and no SyncTeX lookup. 404 for one hidden, gone or on no live page."""
    with connect_pages_db(ws) as conn:
        found = entries.entry(ws, conn, entry_id, regions=regions, sources=regions is None)
    if found is None:
        raise HTTPException(status_code=404, detail="no annotation has that id")
    return found


@router.get("/{entry_id}")
def read_annotation(entry_id: str, request: Request, format: str = "json"):
    """One entry → ``{entry}``: a thread's in any state, or a highlight's
    without a thread; or its Markdown (the file's heading, then the item).
    404 for one hidden, gone or on no live page."""
    _format(format)
    found = entries.with_urls(_entry(member(request), entry_id))
    if format == "md":
        return Response(f"## {found['file']}\n\n" + markdown.render_entry(found, pictures="urls"), media_type=MARKDOWN)
    return {"entry": found}


@router.get("/{entry_id}/picture")
def annotation_picture(entry_id: str, request: Request, at: str = "now", v: str = ""):
    """The JPEG of an entry's region: ``at=now`` as it is on the current
    file, ``before`` as it was commented on. ``v`` (the picture's key) only
    busts a browser's cache. 404 when the entry has no such picture, or it
    cannot be drawn now."""
    if at not in AT:
        raise HTTPException(status_code=400, detail="at is now or before")
    return picture_of(member(request), entry_id, at)


def picture_of(ws: str, entry_id: str, at: str) -> Response:
    """The JPEG of the region of the entry ``entry_id`` of ``ws``
    (``annotation_picture``, and a computer's channel's for a thread of a
    request it holds, gamma/routers/computers.py). 400 for another ``at``,
    404 for no such entry or picture, or one that cannot be drawn now."""
    if at not in AT:
        raise HTTPException(status_code=400, detail="at is now or before")
    regions: dict = {}
    found = _entry(ws, entry_id, regions)
    key = entries.key_of(found["picture" if at == "now" else "before"])
    if not key or key not in regions:
        raise HTTPException(status_code=404, detail="this annotation has no such picture")
    page_id, region = regions[key]
    with connect_pages_db(ws) as conn:
        data = pictures.cached(ws, conn, page_id, region)
    if not data:
        raise HTTPException(status_code=404, detail="this picture cannot be drawn now")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": PICTURE_CACHE})
