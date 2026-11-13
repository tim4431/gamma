"""A computer's channel to this server, and the computers a page is kept on
(gamma/computers/registry.py; docs/research/agent-collab.md "How the server
sees a vault"). Every Gamma server answers.

The channel's two routes take a token of the ``computer`` scope and
nothing else (a session or any other token: 403), and such a token opens
nothing but them (``auth.CHANNEL_PATHS``). ``POST /announce`` says what the
computer is and keeps; ``GET /poll`` is held up to ``registry.POLL_S``, on
the event loop without a worker thread, and is the computer's heartbeat: a
GET, so a read-only hosted server lets it through. The page's route is
the signed-in account's alone: a session, never a token, a guest or a
share, and only that account's computers."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request

from ..auth import _api_bearer, _off_loop, is_token, read_body, require_personal_user_id, require_ws
from ..computers import registry
from ..integrations import resolve_token_scope

router = APIRouter(prefix="/api", tags=["computers"])

CHANNEL_ONLY = "Only a computer's token opens its channel."
MAX_ANNOUNCE = 64 * 1024   # bytes of one announce


def _computer(request: Request) -> tuple[str, str, str, str]:
    """``(user_id, username, workspace, token_id)`` of the computer's token
    the request came with; 403 for a session or another token."""
    if not is_token(request) or request.state.token_scope != "computer":
        raise HTTPException(status_code=403, detail=CHANNEL_ONLY)
    return request.state.user_id, request.state.user, request.state.token_ws, request.state.token_id


@router.post("/computers/announce")
async def announce(request: Request):
    """``{computer: {id, name}, app, vaults: [{kind: "folder", folder_id,
    paused} | {kind: "file", page_id, paused}], assistants: [{name, label,
    found, ready, version, code}]}`` → ``{account: {id, username},
    computer: {id, name}, workspace: {id}, version, wait}``
    (``registry.announce``): what its polls name as ``v``, and the longest
    hold. 400 for a malformed body, 413 past ``MAX_ANNOUNCE``."""
    user_id, username, ws, token_id = _computer(request)
    raw = await read_body(request, MAX_ANNOUNCE, "An announce is at most 64 KiB.")
    try:
        body = json.loads(raw or b"null")
    except ValueError:
        raise HTTPException(status_code=400, detail="The announce is no JSON.")
    try:
        return await asyncio.to_thread(registry.announce, user_id, username, ws, token_id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/computers/poll")
async def poll(request: Request, v: str = "", wait: float = registry.POLL_S):
    """Held up to ``wait`` seconds (at most ``registry.POLL_S``; 0 answers
    at once): ``{announce: true}`` at once when this server does not know
    the computer or ``v`` is not its last announce's version, or when it is
    woken for it; ``{wake: [...]}`` when woken for work (4c, 4d); ``{}``
    when the hold ends. After the hold the token is read again, off the
    event loop: one revoked meanwhile, or whose account lost the workspace,
    is 401 and the computer is forgotten here."""
    _user_id, _username, _ws, token_id = _computer(request)
    held = registry.hold(token_id, v)
    if held is None:
        return {"announce": True}
    live, waiter = held
    try:
        await asyncio.wait_for(waiter[1].wait(), timeout=max(0.0, min(wait, registry.POLL_S)))
    except asyncio.TimeoutError:
        pass
    finally:
        due = registry.release(token_id, live, waiter)
    if due:
        await asyncio.to_thread(registry.seen, token_id)
    found = await _off_loop(resolve_token_scope, _api_bearer(request))
    if not found or found.scope != "computer" or found.token_id != token_id:
        await asyncio.to_thread(registry.forget, token_id)
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return registry.answer(waiter[2])


@router.get("/pages/{page_id}/computer")
def page_computers(page_id: str, request: Request):
    """``{computers: [{id, name, online, since, vault: {kind, name, paused},
    assistants: [{name, label, found, ready, version, code}]}]}``: the
    signed-in account's computers that keep the page (``registry.of_page``),
    ``[]`` for none. A share's reader 404, a token or a guest 403."""
    if request.query_params.get("share"):
        raise HTTPException(status_code=404, detail="Not found")
    user_id = require_personal_user_id(request, "Sign in with your own account to see the computers you keep this on.")
    return {"computers": registry.of_page(user_id, require_ws(request), page_id)}
