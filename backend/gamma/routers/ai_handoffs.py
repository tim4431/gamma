"""The AI chat's fetch handoffs (``gamma/fetch_handoff.py``): the chat card
polls a request, uploads the PDF the user downloaded, or dismisses it; Gamma
Connector opens the page itself when the card asks it to (or recognizes a tab
opened on ``/go``), takes the request (``watch``) and sends the PDF from that
tab. ``store`` puts a delivered PDF into the workspace, for the chat's "Save
to library". Personal accounts only, like the publisher sessions the
Connector also refreshes."""

import html

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import fetch_handoff as handoff
from ..ai_web import FETCH_MAX_BYTES, FetchError
from ..auth import require_personal_user_id, require_ws
from ..storage import is_pdf, pdf_url, store_pdf

router = APIRouter(prefix="/api/ai/handoffs", tags=["ai"])

GONE = "This request has expired — ask the chat again"


def _user_id(request: Request) -> str:
    user_id = require_personal_user_id(request, "Fetching in your browser needs a personal Gamma account")
    if request.query_params.get("share"):
        raise HTTPException(403, "Fetching in your browser needs a personal Gamma account")
    return user_id


def _found(req: dict | None) -> dict:
    if req is None:
        raise HTTPException(404, GONE)
    return handoff.public(req)


@router.get("/{rid}")
def status(rid: str, request: Request):
    return _found(handoff.get(_user_id(request), rid))


class WatchNote(BaseModel):
    note: str = ""            # fetch_handoff.NOTES; anything else clears it
    background: bool = False  # the tab is one the Connector keeps out of sight


@router.post("/{rid}/watch")
def watch(rid: str, request: Request, payload: WatchNote | None = None):
    payload = payload or WatchNote()
    return _found(handoff.watch(_user_id(request), rid, payload.note, payload.background))


class SkipNote(BaseModel):
    # Skipping a card the reply waits on may tell the assistant what to do
    # instead (only the model reads it), like declining an approval.
    note: str = Field(default="", max_length=handoff.MAX_NOTE)


@router.delete("/{rid}")
def dismiss(rid: str, request: Request, payload: SkipNote | None = None):
    payload = payload or SkipNote()
    return _found(handoff.dismiss(_user_id(request), rid, payload.note))


# Sync def: reading the spooled upload and extracting its text run in the
# threadpool.
@router.post("/{rid}/pdf")
def deliver(rid: str, request: Request, file: UploadFile = File(...), url: str = Form("")):
    user_id = _user_id(request)
    data = file.file.read(FETCH_MAX_BYTES + 1)
    if len(data) > FETCH_MAX_BYTES:
        raise HTTPException(413, f"The PDF is larger than {FETCH_MAX_BYTES // 1_000_000} MB")
    if not is_pdf(data):
        raise HTTPException(400, "Not a PDF (the site may have sent a sign-in page instead)")
    from_url = url if url.startswith(("https://", "http://")) and len(url) <= 2048 else ""
    try:
        return _found(handoff.deliver(user_id, rid, data, from_url))
    except FetchError as e:
        raise HTTPException(400, str(e)) from None
    except handoff.Settled as e:
        raise HTTPException(409, f"This request is {e} — nothing more to send") from None


# Sync def: hashing and writing the file run in the threadpool.
@router.post("/{rid}/store")
def store(rid: str, request: Request):
    """Put the PDF delivered for this request into the workspace's uploads
    (content-hash deduped, like ``POST /api/uploads``), for ``POST
    /api/clip`` to make the library page from — the chat's "Save to
    library" for a paper only the user's browser could get."""
    user_id = _user_id(request)
    ws = require_ws(request, write=True)
    held = handoff.held_pdf(user_id, rid)
    if held is None:
        raise HTTPException(404, "The PDF is no longer held here — save it from your browser instead")
    data, url = held
    doc_id, existed = store_pdf(ws, data)
    return {"doc_id": doc_id, "source_url": pdf_url(doc_id), "already_existed": existed, "url": url}


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}<title>{title} · Gamma</title>
<style>
:root {{ color-scheme: light dark; --fg: #1f2328; --muted: #59636e; --bg: #ffffff; --accent: #3a7bd5; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --fg: #e6edf3; --muted: #9198a1; --bg: #16181d; --accent: #5b9bf0; }}
}}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg);
  color: var(--fg); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 34rem; padding: 24px 16px; }}
h1 {{ font-size: 1.25rem; margin: 0 0 8px; overflow-wrap: anywhere; }}
p {{ margin: 0 0 16px; color: var(--muted); }}
a {{ display: inline-block; padding: 8px 14px; border-radius: 8px; background: var(--accent);
  color: #fff; text-decoration: none; font-weight: 600; overflow-wrap: anywhere; }}
</style></head>
<body><main><h1>{title}</h1><p>{text}</p>{link}</main></body></html>
"""


@router.get("/{rid}/go", response_class=HTMLResponse)
def go(rid: str, request: Request):
    """The page a card's "Open" leads to on its way to the publisher when
    the card cannot hand the tab to Gamma Connector itself (the desktop app
    opens it in the system browser; a Connector that has not answered yet).
    Its address is how the Connector knows the tab is fetching for the chat.
    The request's owner goes straight on; anyone else holding the link
    (another account, a browser signed in to no one) is asked first, so the
    link is not an open redirect."""
    found = handoff.target(rid)
    if not found:
        return HTMLResponse(_PAGE.format(refresh="", title="This link has expired",
                                         text="Ask the chat again to get a new one.", link=""), 404)
    url, host = html.escape(found["url"], quote=True), html.escape(found["host"] or found["url"])
    owner = (request.state.user_id == found["user_id"] and not request.state.is_guest)
    link = f'<a href="{url}">Continue to {host}</a>'
    if owner:
        return HTMLResponse(_PAGE.format(
            refresh=f'<meta http-equiv="refresh" content="0;url={url}">\n',
            title=f"Opening {host}…", link=link,
            text="Sign in or complete the check there. Gamma Connector sends the PDF back to "
                 "your chat as soon as it opens; without the Connector, download the PDF and "
                 "drop it on the chat's card."))
    return HTMLResponse(_PAGE.format(
        refresh="", title=f"Continue to {host}?", link=link,
        text="A Gamma chat could not download a paper from this site. After signing in or "
             "passing its check, download the PDF and drop it on the chat's card."))
