"""Publishing a page to the free share host — the wire around
``gamma/publish.py`` (docs/dev/mirror.md "Publishing"):

- on the share host, ``POST /api/auth/cloud/exchange`` with ``Authorization:
  Bearer <Gamma Cloud access token>`` and ``{server?}`` → ``{token,
  workspace_id, username, url}``, a write token on the person's workspace
  there;
- on the share host, ``GET /api/publish/limit`` (the mirror's token) →
  ``{used, max, plan}``, the plan's page cap on the person's workspace
  there, and ``GET /api/pages/resolve-public?host=&path=`` (no auth) →
  ``{share, page_id}`` for a page host's pretty address;
- on the publishing server, ``POST / DELETE / GET
  /api/pages/{id}/publish`` for a page of the request's workspace.

Sync ``def`` throughout: every call waits on another server.
"""

import socket

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import cloud_auth, publish, ratelimit
from ..auth import note_share_miss, require_personal_user, require_ws

router = APIRouter(tags=["publish"])


class ExchangeBody(BaseModel):
    server: str = Field(default="", max_length=200)   # the calling server's name, for the token's name


class PublishBody(BaseModel):
    audience: str | None = Field(default=None, pattern="^(anyone|users|list)$")
    role: str | None = Field(default=None, pattern="^(view|edit)$")


def _refused(e: publish.PublishError) -> JSONResponse:
    return JSONResponse({"detail": e.message, **e.extra}, status_code=e.status)


@router.post("/api/auth/cloud/exchange")
def cloud_exchange(request: Request, payload: ExchangeBody | None = None):
    """The share host's half: a Gamma Cloud access token for a write token
    on the person's default personal workspace here. 403 unless this server
    accepts published pages; 401 when the account server does not know the
    token."""
    ratelimit.check(f"cloud-exchange:ip:{ratelimit.client_ip(request)}", 20, 600)
    if not publish.this_is_share_host():
        raise HTTPException(403, "This server does not accept published pages.")
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or len(token) > 512:
        raise HTTPException(401, "A Gamma Cloud access token is required.", headers={"WWW-Authenticate": "Bearer"})
    try:
        return publish.exchange(token, (payload or ExchangeBody()).server, cloud_auth.callback_base(request))
    except publish.PublishError as e:
        return _refused(e)


def _caller(request: Request, write: bool) -> tuple[str, str]:
    user = require_personal_user(request, "Sign in with your own account to publish.")
    return user, require_ws(request, write=write)


def _server_name(request: Request) -> str:
    return cloud_auth.server_name(cloud_auth.server_url(request)) or socket.gethostname()[:80]


@router.post("/api/pages/{page_id}/publish")
def publish_page(page_id: str, request: Request, payload: PublishBody | None = None):
    """Publish the page to the share host: ``{audience?, role?}`` (the share
    there; default anyone / view) → ``{url, share, mirror: {ws, status,
    page_filter, conflicts_open, pending_local}}``. 409 with a message when
    publishing is not possible here (no Gamma Cloud identity, no share host,
    a workspace that is a copy of another server, a share host itself);
    409 with ``limit: {used, max, plan}`` too when the person's plan allows
    no more published pages there. The answer also carries ``public_url``,
    the pretty address when the share host has page hosts (else ``url``)."""
    user, ws = _caller(request, write=True)
    payload = payload or PublishBody()
    try:
        return publish.publish(user, ws, page_id, audience=payload.audience, role=payload.role,
                               server_name=_server_name(request))
    except publish.PublishError as e:
        return _refused(e)


@router.delete("/api/pages/{page_id}/publish")
def unpublish_page(page_id: str, request: Request):
    """Stop publishing: the share there stops, the copy there is deleted,
    the page here stays → ``{published: false, mirror}``."""
    user, ws = _caller(request, write=True)
    try:
        return publish.unpublish(user, ws, page_id)
    except publish.PublishError as e:
        return _refused(e)


@router.get("/api/pages/{page_id}/publish")
def publication(page_id: str, request: Request):
    """``{published, can_publish, reason?, url?, public_url?, share?,
    status?, mirror?, limit?, error?}`` — any member of the workspace."""
    user, ws = _caller(request, write=False)
    try:
        return publish.state(user, ws, page_id)
    except publish.PublishError as e:
        return _refused(e)


@router.get("/api/publish/limit")
def publish_limit(request: Request):
    """The share host's half: ``{used, max, plan}`` — the root pages of the
    request's workspace (a publishing mirror's token names it) and the cap
    its owner's plan puts on them (``max`` null = none)."""
    if not publish.this_is_share_host():
        raise HTTPException(404, "This server does not accept published pages.")
    return publish.page_cap(require_ws(request))


@router.get("/api/pages/resolve-public")
def resolve_public(request: Request, host: str = "", path: str = ""):
    """A page host's pretty address (``host`` the hostname the browser
    shows, ``path`` its ``/<slug>-<id>``) → ``{share, page_id}``: the share
    token the share view then opens with, audience and role its own. 404
    for anything else. No auth; per IP, and misses count as unknown share
    links do."""
    ratelimit.check(f"resolve-public:ip:{ratelimit.client_ip(request)}", 120, 300)
    try:
        return publish.resolve_public(host[:300], path[:300])
    except publish.PublishError as e:
        note_share_miss(request)
        return _refused(e)
