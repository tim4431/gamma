"""``/api/notices`` — the red dot's feed (gamma/notices.py): what the account
has not looked at yet, and the ack when it does. Guests and integration
tokens get an empty list: a notice is resolved per account, and neither
has one to remember it under."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import notices
from ..auth import is_token, signed_in

router = APIRouter(prefix="/api", tags=["notices"])


def _account(request: Request) -> str | None:
    user_id = signed_in(request)
    if request.state.is_guest or is_token(request):
        return None
    return user_id


@router.get("/notices")
def list_notices(request: Request):
    """``{notices: [{id, fingerprint, tone, pane, title}]}``, strongest
    first. Sync on purpose: the release check may hit the network when its
    cache is stale."""
    user_id = _account(request)
    if not user_id:
        return {"notices": []}
    return {"notices": notices.for_user(user_id, bool(request.state.is_admin))}


class SeenRequest(BaseModel):
    fingerprint: str


@router.post("/notices/{notice_id}/seen")
def mark_seen(notice_id: str, payload: SeenRequest, request: Request):
    """The account has looked at the pane this notice points to; it stays
    quiet until its fingerprint changes."""
    user_id = _account(request)
    if not user_id:
        raise HTTPException(403, "notices follow an account")
    try:
        notices.mark_seen(user_id, notice_id, payload.fingerprint)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}
