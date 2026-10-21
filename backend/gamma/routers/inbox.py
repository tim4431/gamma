"""``/api/inbox`` — the account's Inbox (gamma/inbox.py,
docs/dev/comments.md "The inbox"): its items across every workspace and
share, the unread count the bell polls, and the marks. A signed-in
account's session only: a guest and an integration token get 403, as
for ``/api/notices`` — neither has an Inbox of its own. Sync handlers:
each reads users.db, and a listing every workspace its items name."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import inbox
from ..auth import is_token, signed_in

router = APIRouter(prefix="/api", tags=["inbox"])


def _account(request: Request) -> str:
    """The session account's id; 401 without one, 403 for a guest or a token."""
    user_id = signed_in(request)
    if request.state.is_guest or is_token(request):
        raise HTTPException(403, "the inbox follows a signed-in account")
    return user_id


@router.get("/inbox")
def list_inbox(request: Request, filter: str = "unread", before: str = "", limit: int = inbox.PAGE_SIZE):
    """``{items, unread, more}``: the account's items newest first
    (``inbox.listing``; ``filter`` unread, all or archived; ``before`` the
    ``at`` the previous listing ended on). The items that lead nowhere any
    more are archived on the way."""
    user_id = _account(request)
    try:
        return inbox.listing(user_id, filter=filter, before=before, limit=limit)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/inbox/unread")
def unread_count(request: Request):
    """``{unread}``: what the bell shows."""
    return {"unread": inbox.unread(_account(request))}


class MarkRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=inbox.MAX_MARK)
    all: bool = False
    read: bool | None = None
    archived: bool | None = None


@router.post("/inbox/mark")
def mark_items(payload: MarkRequest, request: Request):
    """Mark items (``ids``, or ``all``) read or unread (``read``), archived
    or back (``archived``) → ``{unread}``. 400 without items or a mark."""
    user_id = _account(request)
    if not payload.ids and not payload.all:
        raise HTTPException(400, "name the items, or all")
    if payload.read is None and payload.archived is None:
        raise HTTPException(400, "say read or archived")
    ids = [i for i in payload.ids if isinstance(i, str) and 0 < len(i) <= 64]
    return {"unread": inbox.mark(user_id, ids=ids, every=payload.all, read=payload.read,
                                 archived=payload.archived)}
