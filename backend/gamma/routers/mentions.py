"""What the note editor's @ menu and its reminders read (gamma/mentions.py,
docs/dev/mentions.md): the people of a workspace, whom a note can mention,
and an account's reminders with the ones it has dealt with."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .. import mentions, workspaces
from ..auth import require_user_id, require_ws
from ..db import REMINDERS_DONE_PREF_KEY, get_pref

router = APIRouter(prefix="/api", tags=["mentions"])


@router.get("/people")
def list_people(request: Request):
    """``{people: [{username, role}]}`` — the explicit members of the
    request's workspace, owners first (any member; not a share link's
    visitor, who is no member)."""
    ws = require_ws(request)
    return {"people": [{"username": m["username"], "role": m["role"]} for m in workspaces.members(ws)]}


def _done(user_id: str) -> list[str]:
    value, _ = get_pref(user_id, REMINDERS_DONE_PREF_KEY)
    return list(value) if isinstance(value, dict) else []


@router.get("/reminders")
def list_reminders(request: Request):
    """``{reminders: [...], done: [key]}``: the account's reminders across
    its workspaces (mentions.reminders_for) and the keys of those it has
    dismissed. Sync: one notes-index query per workspace."""
    user_id = require_user_id(request)
    return {"reminders": mentions.reminders_for(user_id, request.state.user), "done": _done(user_id)}


class DoneRequest(BaseModel):
    keys: list[str] = Field(max_length=100)
    done: bool = True


@router.post("/reminders/done")
def mark_done(payload: DoneRequest, request: Request):
    """Dismiss reminders (``done: false`` brings them back): ``{done: [key]}``."""
    user_id = require_user_id(request)
    keys = [k for k in payload.keys if isinstance(k, str) and 0 < len(k) <= 200]
    return {"done": mentions.set_done(user_id, keys, payload.done)}
