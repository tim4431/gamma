"""``/api/notices`` — the red dot's feed (gamma/notices.py): what the account
has not looked at yet, and the ack when it does. Guests and integration
tokens get an empty list: a notice is resolved per account, and neither
has one to remember it under."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import notices
from ..auth import require_user

router = APIRouter(prefix="/api", tags=["notices"])


def _account(request: Request) -> str | None:
    user = require_user(request)
    if request.state.is_guest or getattr(request.state, "auth", "session") == "token":
        return None
    return user


@router.get("/notices")
def list_notices(request: Request):
    """``{notices: [{id, fingerprint, tone, pane, title}]}``, strongest
    first. Sync on purpose: the release check may hit the network when its
    cache is stale."""
    user = _account(request)
    if not user:
        return {"notices": []}
    return {"notices": notices.for_user(user, bool(request.state.is_admin))}


class SeenRequest(BaseModel):
    fingerprint: str


@router.post("/notices/{notice_id}/seen")
async def mark_seen(notice_id: str, payload: SeenRequest, request: Request):
    """The account has looked at the pane this notice points to; it stays
    quiet until its fingerprint changes."""
    user = _account(request)
    if not user:
        raise HTTPException(403, "notices follow an account")
    try:
        notices.mark_seen(user, notice_id, payload.fingerprint)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}
