"""Link previews (/api/link-preview): what a web address's chip and link card
say — its title and author — read server-side by gamma/link_preview.py
through the SSRF guard, in the reader's language, and cached in-process."""

import urllib.parse

from fastapi import APIRouter, HTTPException, Request

from .. import link_preview
from ..auth import require_user

router = APIRouter(prefix="/api", tags=["links"])


@router.get("/link-preview")
def link_preview_route(request: Request, url: str):
    require_user(request)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="not an http(s) URL")
    return link_preview.preview(url, request.headers.get("accept-language", "")[:200])
