"""A computer's channel to this server, the computers a page or a folder is
kept on and the account's computers (gamma/computers/registry.py;
docs/research/agent-collab.md "How the server sees a vault"). Every Gamma
server answers.

The channel's routes take a token of the ``computer`` scope and nothing
else (a session or any other token: 403), and such a token opens nothing
but them (``auth.CHANNEL_PREFIX``). ``POST /announce`` says what the
computer is and keeps; ``GET /poll`` is held up to ``registry.POLL_S``, on
the event loop without a worker thread, and is the computer's heartbeat: a
GET, so a read-only hosted server lets it through. The page's route,
``GET /folders/computers`` and ``GET /computers`` (Settings → AI) are the
signed-in account's alone: a session, never a token, a guest or a share,
and only that account's computers.

The turns (step 4d of docs/research/agent-collab.md): a work folder of
this server the computer keeps while an agent is on there (the first of its
announce's assistants ``enabled``, ``registry.agent_folders``) has its
maker's chat with that agent on its pages (gamma/routers/local_agents.py),
and the comments the maker sends to it (gamma/local_agents/threads.py), run
on that computer. ``GET /chats`` lists the turns waiting and the runs asked
to stop; a run takes one with ``/chats/{turn}/claim``, keyed by the id the
computer minted for it (a retry is told it holds it; another taker 409),
and gets the message, the earlier ones, the page's path and, for a
thread's turn, the thread; it writes the agent's message with
``/chats/{turn}/write``, each write numbered (``seq``), so this server
takes only the newest one of the run that holds the turn running
(gamma/local_agents/chats.py). ``GET /threads/{id}/picture`` is a region's
picture for the thread of a turn it holds. What a turn asks never comes
here: the message says ``waiting`` while an ask waits for an answer on the
computer (step 5b). Every route answers only for the token's own account's
turns, and its co-authors'; none ever names a path of the computer but the
relative ones a message names. Each is a sync ``def``: it reads the
pages.db off the event loop.

A co-author's turns (decision 13 of
docs/research/agents-as-participants-plan.md): the people of the token's
workspace its agent serves (the announce's ``people``) have their turns run
on that computer too, the claim naming whom each is for (``for``).
``GET /people`` lists who the computer's owner may name: the workspace's
editors and owners but its own account."""

import asyncio
import json
import re
from contextlib import contextmanager
from pathlib import PurePosixPath

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import workspaces
from ..auth import _api_bearer, _off_loop, is_token, read_body, require_personal_user_id, require_ws
from ..computers import registry
from ..db import connect_pages_db
from ..integrations import resolve_token_scope

router = APIRouter(prefix="/api", tags=["computers"])

CHANNEL_ONLY = "Only a computer's token opens its channel."
MAX_ANNOUNCE = 128 * 1024  # bytes of one announce: the vaults and the models each assistant offers
MAX_PATH = 1024            # characters of a changed file's relative path
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _computer(request: Request) -> tuple[str, str, str, str]:
    """``(user_id, username, workspace, token_id)`` of the computer's token
    the request came with; 403 for a session or another token."""
    if not is_token(request) or request.state.token_scope != "computer":
        raise HTTPException(status_code=403, detail=CHANNEL_ONLY)
    return request.state.user_id, request.state.user, request.state.token_ws, request.state.token_id


@router.post("/computers/announce")
async def announce(request: Request):
    """``{computer: {id, name}, app, vaults: [{kind: "folder", folder_id,
    paused, agent?} | {kind: "file", page_id, paused}], assistants: [{name,
    label, found, ready, version, code, models?: [{id, label, efforts,
    effort, default}]}]}`` → ``{account: {id, username},
    computer: {id, name}, workspace: {id}, version, wait}``
    (``registry.announce``): what its polls name as ``v``, and the longest
    hold. 400 for a malformed body (more than ``registry.MAX_MODELS`` models
    an assistant), 413 past ``MAX_ANNOUNCE``."""
    user_id, username, ws, token_id = _computer(request)
    raw = await read_body(request, MAX_ANNOUNCE, "An announce is at most 128 KiB.")
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
    woken for it; ``{wake: [...]}`` when woken for work (4d); ``{}``
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


@router.get("/computers/people")
def channel_people(request: Request):
    """``{people: [{id, username, role}]}``: the members of the token's
    workspace who may edit it (editors and owners), its own account left
    out, by name: whom the computer's owner may let use an agent there
    (Settings → AI → Agents on that computer)."""
    from ..db import connect_users_db

    user_id, _username, ws, _token_id = _computer(request)
    with connect_users_db() as conn:
        rows = conn.execute("SELECT u.id, u.username, m.role FROM workspace_members m JOIN users u ON u.id = m.user_id "
                            "WHERE m.workspace_id = ? AND m.user_id != ? ORDER BY u.username",
                            (ws, user_id)).fetchall()
    return {"people": [{"id": r[0], "username": r[1], "role": r[2]} for r in rows
                       if workspaces.at_least(r[2], "editor")]}


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


@router.get("/computers")
def account_computers(request: Request):
    """``{computers: [{id, name, online, since, assistants: [{name, label,
    found, ready, version, code, models, enabled, runs}]}]}``: the signed-in
    account's computers shown to the workspace (``registry.of_account``),
    each with its agents as it last announced them, for Settings → AI.
    ``[]`` for none; a token or a guest 403."""
    user_id = require_personal_user_id(request, "Sign in with your own account to see your computers.")
    return {"computers": registry.of_account(user_id, require_ws(request))}


@router.get("/folders/computers")
def folder_computers(request: Request):
    """``{folders: {folder id: {name, online, since, paused}}}``: the
    signed-in account's computers that keep a folder vault of the
    workspace, one per folder (``registry.of_folders``), which a synced
    folder's card and rows name. A share's reader 404, a token or a guest
    403."""
    if request.query_params.get("share"):
        raise HTTPException(status_code=404, detail="Not found")
    user_id = require_personal_user_id(request, "Sign in with your own account to see the computers you keep this on.")
    return {"folders": registry.of_folders(user_id, require_ws(request))}


# --- the turns a computer runs (step 4d) ---------------------------------------------------------

def _run_id(run: str) -> str:
    if not _RUN_ID.match(run or ""):
        raise HTTPException(status_code=400, detail="A run's id is 1 to 64 letters, digits, - or _.")
    return run


def _label(agent: str) -> str:
    from ..local_agents import catalog  # local: the agents' package is loaded where an agent is named

    return catalog.label(agent)


def _relative(path: str) -> str:
    """A changed file as a message names it: its path in the folder; 400
    for anything else."""
    parts = PurePosixPath(path)
    if not 0 < len(path) <= MAX_PATH or "\\" in path or ":" in path or parts.is_absolute() or ".." in parts.parts:
        raise HTTPException(status_code=400, detail="A changed file is named by its path in the folder.")
    return path


def _is_relative(path: str) -> bool:
    try:
        _relative(path)
    except HTTPException:
        return False
    return True


MAX_STEP_TEXT = 200    # characters of a step's path, program or status
MAX_ERROR = 2000       # characters of a turn's failure
TURN_STATES = ("running", "done", "failed", "stopped")


class ChatClaim(BaseModel):
    run: str      # the id the computer minted for its run: a retry names it again
    agent: str    # the folder's agent there (``codex``, ``claude``)


class ChatWrite(BaseModel):
    run: str
    seq: int      # above the last write taken from that run, else nothing is written
    fields: dict


@contextmanager
def _chat_refusals():
    from ..local_agents import chats  # local: the agents' package is loaded where an agent is named

    try:
        yield
    except chats.ChatError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


@router.get("/computers/chats")
def waiting_chats(request: Request):
    """``{turns: [{id, conversation_id, page_id, folder_id, at}], stops:
    [{id, run}]}``: the turns of the token's account's chat, and of the
    people its agent serves there (``registry.people_of``), that wait in
    pages of the work folders this computer keeps with an agent (not paused
    there; each a turn of that agent's; oldest first, at most
    ``chats.MAX_WAITING``; none while the account may not edit,
    ``registry.agent_folders``), and the runs of it
    whose turns were asked to stop (listed until the run writes its end)."""
    from ..local_agents import chats

    user_id, _username, ws, token_id = _computer(request)
    folders = registry.agent_folders(token_id, ws)
    taking = {w: folder["agent"] for w, folder in folders.items() if not folder["paused"]}
    people = {p for p in registry.people_of(token_id) if workspaces.at_least(workspaces.role_of(ws, p), "editor")}
    with connect_pages_db(ws) as conn:
        turns = chats.waiting_turns(conn, user_id, taking, people) if taking else []
        stops = chats.stops_of(conn, token_id)
    return {"turns": turns, "stops": stops}


@router.post("/computers/chats/{turn_id}/claim")
def claim_chat(turn_id: str, payload: ChatClaim, request: Request):
    """``{run, agent}`` → ``{turn: {id, conversation_id, page_id, text,
    selections, model, effort, for}, history, page: {path, title}, seen}``
    (``chats.claim``): the turn taken for the run ``run`` (it and its
    message ``running``) in one write, or held by that run already (a
    retry: answered again). 400 for a run's id of another shape; 403 while
    the account may not edit; 404 for no turn for an agent of the token's
    account's in a page of a work folder this computer keeps with an agent;
    409 when another run holds it, it is no longer queued, the folder's
    agent there is another, the turn is another agent's (left queued), it
    is a co-author's the agent no longer serves (``registry.serves``) or
    the folder is paused there; 423 while a delete holds the workspace."""
    from ..local_agents import chats

    user_id, _username, ws, token_id = _computer(request)
    run = _run_id(payload.run)
    folders = registry.agent_folders(token_id, ws)
    workspaces.refuse_writes(ws)
    with _chat_refusals():
        with connect_pages_db(ws) as conn:
            row = chats.run_of(conn, turn_id)
            page = conn.execute("SELECT work_folder FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                                (row["bucket"],)).fetchone() if row else None
        if row is None or row["owner"] != user_id:
            raise chats.ChatError(404, chats.NO_TURN)
        if not workspaces.at_least(workspaces.role_of(ws, user_id), "editor"):   # agent_folders then has none
            raise chats.ChatError(403, f"Only an owner or an editor of this workspace can talk to "
                                       f"{_label(payload.agent) if payload.agent in registry.AGENTS else 'an agent'}.")
        if not page or page[0] not in folders:
            raise chats.ChatError(404, chats.NO_TURN)
        folder = folders[page[0]]
        label = _label(folder["agent"])
        if payload.agent != folder["agent"]:
            raise chats.ChatError(409, f"This synced folder's agent is {label}.")
        if not registry.serves(token_id, ws, row["user_id"]):
            raise chats.ChatError(409, f"The owner of {label} no longer lets this person use it.")
        if folder["paused"]:
            raise chats.ChatError(409, f"The synced folder is paused: {label} starts once it syncs again.")
        return chats.claim(ws, turn_id, user_id=user_id, holder=token_id, run=run, agent=folder["agent"],
                           computer=registry.computer_name(token_id))


def _step(raw) -> dict:
    """A step of the agent's message as a run writes it, kept as the
    chat's messages keep one (runner._step): ``{id, kind, status}``, with
    ``path`` (its path in the folder, else "outside") for a read or an
    edit, ``program`` for a command, ``tool`` for a call of Gamma's tools.
    400 for another shape."""
    from ..local_agents import events

    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="A step is an object.")
    kind = raw.get("kind") if raw.get("kind") in events.KINDS else "other"
    step = {"id": events.one_line(str(raw.get("id") or ""), 64), "kind": kind,
            "status": events.one_line(str(raw.get("status") or ""), MAX_STEP_TEXT)}
    if kind in ("read", "edit"):
        path = str(raw.get("path") or "")
        try:
            step["path"] = events.one_line(_relative(path), MAX_STEP_TEXT) if path != "outside" else "outside"
        except HTTPException:
            step["path"] = "outside"
    elif kind == "run":
        step["program"] = events.one_line(str(raw.get("program") or ""), MAX_STEP_TEXT)
    elif kind == "gamma":
        step["tool"] = raw["tool"] if events.GAMMA_TOOL.match(str(raw.get("tool") or "")) else ""
    return step


def _turn_fields(raw: dict) -> dict:
    """The agent message's fields a run writes, as kept: ``text`` (made
    inert again, ``prompt.inert``, at most ``chats.MAX_TEXT``), ``steps``,
    ``changes`` (paths in the folder: one named otherwise, which the
    computer's own file system may allow, left out), ``changed``, ``built``, ``error``,
    ``fresh``, ``waiting`` (an ask of the turn waits for an answer on the
    computer, which alone shows it: never ``asks``), ``state`` (running or
    an end), ``model``, ``effort``; ``ended_at`` is this server's clock
    (``chats.write_run``), any other field left out. 400 for one of another
    shape."""
    from ..local_agents import chats, prompt, store

    out: dict = {}

    def text(key: str, limit: int) -> None:
        if key in raw:
            if not isinstance(raw[key], str):
                raise HTTPException(status_code=400, detail=f"{key} is text.")
            out[key] = prompt.inert(raw[key].strip())[:limit]

    def flag(key: str) -> None:
        if key in raw:
            if not isinstance(raw[key], bool):
                raise HTTPException(status_code=400, detail=f"{key} is true or false.")
            out[key] = raw[key]

    def choice(key: str, limit: int) -> None:
        if key in raw:
            if raw[key] is not None and not isinstance(raw[key], str):
                raise HTTPException(status_code=400, detail=f"{key} is text or null.")
            out[key] = (raw[key] or "").strip()[:limit] or None

    text("text", chats.MAX_TEXT)
    text("error", MAX_ERROR)
    flag("built")
    flag("fresh")
    flag("waiting")
    choice("model", 100)
    choice("effort", 40)
    if "steps" in raw:
        if not isinstance(raw["steps"], list):
            raise HTTPException(status_code=400, detail="steps is a list.")
        out["steps"] = [_step(step) for step in raw["steps"][:store.MAX_STEPS]]
    if "changes" in raw:
        if not isinstance(raw["changes"], list) or not all(isinstance(p, str) for p in raw["changes"]):
            raise HTTPException(status_code=400, detail="changes is a list of paths.")
        out["changes"] = [path for path in raw["changes"][:store.MAX_CHANGES] if _is_relative(path)]
    if "changed" in raw:
        if not isinstance(raw["changed"], int) or isinstance(raw["changed"], bool) or raw["changed"] < 0:
            raise HTTPException(status_code=400, detail="changed is a count.")
        out["changed"] = raw["changed"]
    if "state" in raw:
        if raw["state"] not in TURN_STATES:
            raise HTTPException(status_code=400, detail="state is running, done, failed or stopped")
        out["state"] = raw["state"]
    return out


@router.get("/computers/threads/{thread_id}/picture")
def thread_picture(thread_id: str, request: Request, at: str = "now"):
    """The JPEG of a thread's region (``at`` now or before), as ``GET
    /api/annotations/{id}/picture`` answers it, for the thread of a turn a
    run of this computer holds running (``chats.holds_thread``): what the
    computer puts beside the thread in its prompt. 400 for another ``at``;
    404 for any other thread."""
    from ..local_agents import chats
    from .annotations import picture_of  # local: the annotations' routes reach the agents' package

    user_id, _username, ws, token_id = _computer(request)
    if at not in ("now", "before"):
        raise HTTPException(status_code=400, detail="at is now or before")
    with connect_pages_db(ws) as conn:
        if not chats.holds_thread(conn, token_id, user_id, thread_id):
            raise HTTPException(status_code=404, detail="No thread of a turn on this computer has that id.")
    return picture_of(ws, thread_id, at)


@router.post("/computers/chats/{turn_id}/write")
def write_chat(turn_id: str, payload: ChatWrite, request: Request):
    """``{run, seq, fields}`` → ``{ok}``: the agent message's fields
    (``_turn_fields``) written by the run ``run`` that holds the turn
    running, when ``seq`` is above the last one written for that run (else
    ``{ok, stale: true}``, nothing written: a retry or a late write); an
    end (``state`` done, failed or stopped) ends the turn. 400 for a
    field of another shape; 404 for no turn this computer holds or held; 409 when
    another run holds it, it was stopped here or its conversation is gone
    (the run stops)."""
    from ..local_agents import chats

    user_id, _username, ws, token_id = _computer(request)
    run = _run_id(payload.run)
    fields = _turn_fields(payload.fields)
    workspaces.refuse_writes(ws)
    with _chat_refusals():
        written = chats.write_run(ws, turn_id, user_id=user_id, holder=token_id, run=run, seq=payload.seq,
                                  fields=fields)
    return {"ok": True} if written else {"ok": True, "stale": True}
