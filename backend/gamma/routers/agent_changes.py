"""An agent's changes, as the person decides on them and takes them back
(docs/dev/ai.md "Reviewing an agent's changes"): the journal's
(gamma/agent_changes.py) suggestions on a page, the states of a reply's
rows and one change read (``GET /ai/changes*``), a suggestion accepted,
a change undone or redone (``POST /ai/changes*``), and a reply's change
taken back by the revert record its action keeps (``/ai/revert``,
gamma/ai_revert.py). Reading one change and accepting are the person's,
from a signed-in session; undoing and redoing are a workspace editor's,
as the chat's writes are."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import agent_changes, ai_revert
from ..auth import TOKEN_REFUSAL, actor_of, is_token, require_ws
from .integrations import token_workspaces

router = APIRouter(prefix="/api", tags=["ai"])


class AIRevert(BaseModel):
    kind: Literal["edit", "create", "move", "delete", "pages", "trash", "made"]
    block_id: str = Field(default="", max_length=64)  # "" for "pages": a page tool's record names its pages
    revert: dict   # the action's `revert`, as the tool recorded it
    force: bool = False
    redo: bool = False  # put a reverted change back


@router.post("/ai/revert")
def ai_revert_change(payload: AIRevert, request: Request):
    """Take back one change the agent made to the notes, the pages it
    renamed, moved or labelled (``kind: "pages"``), or a folder or label it
    deleted, or with ``redo`` put a reverted one back, from its row under
    the reply (gamma/ai_revert.py): ``{page_id, noop, kept?}``. 409
    ``{detail, conflict, preview?}`` when the note changed since —
    ``preview`` is what ``force`` would do — or can't go there, and 409
    ``conflict: "kept"`` (``preview.kept``) when every page of a page
    tool's change changed since; 404 when it is gone. A workspace
    editor's, like the chat's writes; the change is theirs to make by hand
    anyway. Sync: it waits on the write lock."""
    ws = require_ws(request, write=True)
    try:
        return ai_revert.revert_change(ws, payload.kind, payload.block_id, payload.revert,
                                       force=payload.force, redo=payload.redo, actor=actor_of(request))
    except ai_revert.RevertError as e:
        return JSONResponse(status_code=e.status, content=e.body())


# What a change says of itself to the page that draws it or shows it.
_FIELDS = ("id", "at", "tool", "kind", "block_id", "page_id", "summary", "revert", "agent", "source", "state")


@router.get("/ai/changes")
def ai_changes(request: Request, page_id: str = "", ids: str = ""):
    """The account's agent changes (gamma/agent_changes.py): with
    ``page_id``, its suggestions on that page, oldest first (``{changes}``,
    each ``open``: what the page draws until the person accepts or rejects
    it); with ``ids`` (comma-separated), the states of those changes
    (``{states: {id: open|kept|undone}}``: a reply's rows). Another
    account's changes are never listed."""
    ws = require_ws(request)
    user_id = request.state.user_id or ""
    if not user_id:  # a share link's visitor has no changes of its own
        return {"changes": []} if page_id else {"states": {}}
    if page_id:
        return {"changes": [{k: c[k] for k in _FIELDS} for c in agent_changes.suggested_on(ws, user_id, page_id)]}
    return {"states": agent_changes.states(ws, user_id, ids.split(","))}


def _person_reviews(request: Request) -> None:
    """Accepting an agent's change, or reading one to show it, is the
    person's, from a signed-in session: an integration token, through which
    the changes may have been made, does neither (403)."""
    if is_token(request):
        raise HTTPException(403, TOKEN_REFUSAL)


def _readable(change: dict, user_id: str, ws: str) -> bool:
    """Whether ``user_id`` reads ``change`` in ``ws``: one made for the
    account, or one an MCP connection journaled before migration step 73
    named the account (``account`` ''), made by a connection of the
    account's that reads ``ws``, as its Changes in Settings list it."""
    if change["account"]:
        return change["account"] == user_id
    if not change["token_id"]:
        return False
    try:
        return ws in token_workspaces(user_id, change["token_id"])
    except HTTPException:  # a connection gone, or another account's
        return False


@router.get("/ai/changes/{change_id}")
def ai_change(change_id: str, request: Request):
    """One of the account's agent changes in the request's workspace, for
    the page it landed on to show its diff (a reply's row, a connection's
    row in Settings): ``{change}``, with its state. A change one of the
    account's connections made before the journal named accounts is the
    account's too (``_readable``). 404 for a change that is gone or another
    account's; 403 to an integration token."""
    ws = require_ws(request)
    _person_reviews(request)
    user_id = request.state.user_id or ""
    change = agent_changes.find(ws, change_id)
    if not change or not user_id or not _readable(change, user_id, ws):
        raise HTTPException(404, "No such change")
    return {"change": {k: change[k] for k in _FIELDS}}


class AIChangesMany(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=agent_changes.MAX_IDS)


@router.post("/ai/changes/keep")
def ai_changes_keep(payload: AIChangesMany, request: Request):
    """Accept the account's suggestions among ``ids`` (a page's or a
    reply's Accept all): ``{kept}``, the ids kept now; one undone or kept
    already is not among them."""
    ws = require_ws(request)
    _person_reviews(request)
    return {"kept": agent_changes.keep(ws, request.state.user_id or "", payload.ids)}


class AIChangeDecision(BaseModel):
    decision: Literal["keep", "undo", "redo"]
    force: bool = False


# After keep, whose path this one's matches too.
@router.post("/ai/changes/{change_id}")
def ai_change_decide(change_id: str, payload: AIChangeDecision, request: Request):
    """Accept (``keep``), reject or undo (``undo``) or redo one of the
    account's agent changes, from where it landed, its row in the chat or
    the dialog: ``{state, page_id, noop?}``, the change's state after it —
    with ``noop`` the state it had, when there was nothing to do (an accept
    of one undone or kept already). Undo and redo are ``/ai/revert``'s,
    with its 409s (what ``force`` would do) and 404; 404 too for a change
    that is gone or another account's. An accept is the person's: 403 to
    an integration token."""
    user_id = request.state.user_id or ""
    ws = require_ws(request, write=payload.decision != "keep")
    if payload.decision == "keep":
        _person_reviews(request)
    change = agent_changes.find(ws, change_id)
    if not change or not user_id or change["account"] != user_id:
        raise HTTPException(404, "No such change")
    if payload.decision == "keep":
        if agent_changes.keep(ws, user_id, [change_id]):
            return {"state": agent_changes.KEPT, "page_id": change["page_id"]}
        # undone or kept already, perhaps in another tab a moment ago: the journal's state as it is now
        state = agent_changes.states(ws, user_id, [change_id]).get(change_id, change["state"])
        return {"state": state, "page_id": change["page_id"], "noop": True}
    if payload.decision == "undo" and change["state"] == agent_changes.UNDONE:
        return {"state": agent_changes.UNDONE, "page_id": change["page_id"], "noop": True}
    try:
        result = agent_changes.undo(ws, change, actor=actor_of(request), force=payload.force,
                                    redo=payload.decision == "redo")
    except ai_revert.RevertError as e:
        return JSONResponse(status_code=e.status, content=e.body())
    return {**result, "state": agent_changes.KEPT if payload.decision == "redo" else agent_changes.UNDONE}
