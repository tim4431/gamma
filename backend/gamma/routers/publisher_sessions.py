"""Write-only credentials API; listing exposes connection metadata only."""

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from .. import publisher_sessions as sessions
from ..auth import _is_https, read_body, require_personal_user_id
from ..server_settings import LOOPBACK_HOSTS

router = APIRouter(prefix="/api/publisher-sessions", tags=["publisher sessions"])


def _user_id(request: Request) -> str:
    user_id = require_personal_user_id(request, "Publisher sessions require a personal Gamma account")
    if request.query_params.get("share"):
        raise HTTPException(403, "Publisher sessions require a personal Gamma account")
    return user_id


@router.get("")
def status(request: Request):
    return {"sessions": sessions.list_sessions(_user_id(request)),
            "publisher_roots": sessions.PUBLISHER_ROOTS}


@router.post("")
async def connect(request: Request):
    user_id = _user_id(request)
    if not _is_https(request) and request.url.hostname not in LOOPBACK_HOSTS:
        raise HTTPException(400, "Connect publisher sessions over HTTPS or localhost")
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(415, "Send publisher sessions as JSON")
    body = await read_body(request, 256 * 1024, "Publisher session is too large")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Invalid request")
        # sealed and stored in users.db: in the threadpool, off the event loop
        return await run_in_threadpool(sessions.save, user_id, payload.get("host"), payload.get("cookies"),
                                       payload.get("user_agent", ""))
    except (ValueError, TypeError):
        # Never echo validation input: it contains credentials.
        raise HTTPException(400, "Invalid publisher cookies or unsupported host") from None


@router.delete("/{host}")
def disconnect(host: str, request: Request):
    sessions.disconnect(_user_id(request), host)
    return {"ok": True}
