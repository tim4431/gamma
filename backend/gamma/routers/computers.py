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

The turns (step 4d of docs/research/agent-collab.md, step 4.2 of
docs/research/agents-as-participants-plan.md): the agents a computer
announces on for the token's account are that account's in the chat of
every bucket of the workspace (gamma/routers/local_agents.py) and for the
comments it sends them (gamma/local_agents/threads.py), each conversation
with one computer's agent (``computer_chats.computer``), whose turns run on
that computer. ``GET /chats`` lists the turns waiting and the runs asked
to stop; a run takes one with ``/chats/{turn}/claim``, keyed by the id the
computer minted for it (a retry is told it holds it; another taker 409),
and gets the message, the earlier ones, what the bucket is, where its
agent works by an id (the work folder's, an offered directory's, or none:
never a path), for a thread's turn the thread, and Gamma's tools (a turn
token of this server for the account the turn is for, and this server's
MCP address, ``_tools``); it writes the agent's message with
``/chats/{turn}/write``, each write numbered (``seq``), so this server
takes only the newest one of the run that holds the turn running
(gamma/local_agents/chats.py), the turn's token deleted at its end. ``GET
/threads/{id}/picture`` is a region's picture for the thread of a turn it
holds. What a turn asks comes here only where the computer lets this
server answer it (its ``asks``, decision 7 of
docs/research/agents-as-participants-plan.md, step 4.3): the message holds
it (``asks``, as a chat turn's), answered in the chat, the answer kept with
the run and listed to the computer (``answers``); else the message says
only ``waiting`` while an ask waits for an answer on the computer. Every route
answers only for the token's own account's turns, and its co-authors';
none ever names a path of the computer but the relative ones a message
names, and those of a relayed ask (its ``cwd`` and ``paths``, outside the
directory too, which the shell's question before the computer relays asks
says). Each is a sync ``def``: it reads the pages.db off the event loop.

The sessions of the computer's agents (step 5 of
docs/research/agents-as-participants-plan.md): History's *On {computer}*
here asks the computer for the sessions its agent's CLI keeps of the
directory a far conversation works in there, and for one's turns to open
it as a conversation here (gamma/computers/sessions.py); the computer
takes the asks with ``GET /sessions`` once woken (``sessions``) and
answers each with ``POST /sessions/{id}``, every answer checked and capped
here as a turn's writes are (``_session_answer``).

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
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import agents, workspaces
from ..auth import _api_bearer, _off_loop, is_token, read_body, require_personal_user_id, require_ws
from ..computers import registry
from ..db import connect_pages_db, page_now
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
    effort, default, speeds, speed}]}], asks}`` (``asks`` whether this server may answer
    what its agents ask) → ``{account: {id, username}, computer: {id,
    name}, workspace: {id, name}, version, wait}``
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
    assistants: [{name, label, found, ready, version, code}], asks}]}``: the
    signed-in account's computers that keep the page (``registry.of_page``),
    ``asks`` whether the computer lets this server answer what its agents ask,
    ``[]`` for none. A share's reader 404, a token or a guest 403."""
    if request.query_params.get("share"):
        raise HTTPException(status_code=404, detail="Not found")
    user_id = require_personal_user_id(request, "Sign in with your own account to see the computers you keep this on.")
    return {"computers": registry.of_page(user_id, require_ws(request), page_id)}


@router.get("/computers")
def account_computers(request: Request):
    """``{computers: [{id, name, online, since, assistants: [{name, label,
    found, ready, version, code, models, enabled, runs}], asks, dirs}]}``:
    the signed-in account's computers shown to the workspace
    (``registry.of_account``), each with its agents as it last announced
    them and the directories it offers them, for Settings → AI and a chat
    with no page open.
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
MAX_ASK_PLACE = 500    # characters of an ask's directory, host or path (runner._ChatTurn._shown_path)
TURN_STATES = ("running", "done", "failed", "stopped")


class ChatClaim(BaseModel):
    run: str      # the id the computer minted for its run: a retry names it again
    agent: str    # the turn's agent there (``codex``, ``claude``), as the listing named it


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
    """``{turns: [{id, conversation_id, bucket, agent, dir: {kind, id},
    at}], stops: [{id, run}], answers: [{id, run, ask, decision, note}],
    steers: [{id, run, steer: {id, text, selections}}]}``:
    the turns that wait for this computer's
    agents (``chats.waiting_turns``), of the token's account's
    conversations with this computer and of the people its agent serves
    there (``registry.people_of``), each of an agent it has on, none in a
    work folder paused there (oldest first, at most ``chats.MAX_WAITING``;
    none while the account may not edit, ``registry.taking``), ``dir``
    where its agent works by an id; the runs of it whose turns were asked
    to stop (listed until the run writes its end); the answers given
    here to what its running turns ask (``chats.answers_of``); and the
    messages steered into them, each handed out once (``chats.steers_of``)."""
    from ..local_agents import chats

    user_id, _username, ws, token_id = _computer(request)
    taking = registry.taking(token_id, ws)
    with connect_pages_db(ws) as conn:
        turns = []
        if taking is not None and taking["agents"]:
            people = {p for p in registry.people_of(token_id)
                      if workspaces.at_least(workspaces.role_of(ws, p), "editor")}
            paused = [w for w, held in taking["folders"].items() if held]
            turns = [turn for turn in chats.waiting_turns(conn, user_id, taking["computer_id"], people, paused)
                     if turn["agent"] in taking["agents"]]
        stops = chats.stops_of(conn, token_id)
        answers = chats.answers_of(conn, token_id)
    return {"turns": turns, "stops": stops, "answers": answers, "steers": chats.steers_of(token_id)}


@router.post("/computers/chats/{turn_id}/claim")
def claim_chat(turn_id: str, payload: ChatClaim, request: Request):
    """``{run, agent}`` → ``{turn: {id, conversation_id, bucket, title,
    text, selections, model, effort, speed, for, thread_id?, comment_id?},
    history, about, dir: {kind, id}, seen, thread?, tools: {url, token} |
    null}`` (``chats.claim``, ``_tools``): the turn taken for the run
    ``run`` (it and its message ``running``) in one write, or held by that
    run already (a retry: answered again, with a new token in place of the
    one the lost answer carried). 400 for a run's id of another shape; 403
    while the account may not edit; 404 for no turn of the token's
    account's with this computer; 409 when another run holds it, it is no
    longer queued, the turn is another agent's or that agent is off here
    (left queued), it is a co-author's the agent no longer serves
    (``registry.serves``), this computer does not keep its work folder or
    it is paused there, or it no longer offers its directory; 423 while a
    delete holds the workspace."""
    from ..local_agents import chats
    from ..work_folders.marks import Tree

    user_id, _username, ws, token_id = _computer(request)
    run = _run_id(payload.run)
    workspaces.refuse_writes(ws)
    with _chat_refusals():
        if not workspaces.at_least(workspaces.role_of(ws, user_id), "editor"):
            raise chats.ChatError(403, f"Only an owner or an editor of this workspace can talk to "
                                       f"{_label(payload.agent) if payload.agent in registry.AGENTS else 'an agent'}.")
        taking = registry.taking(token_id, ws)
        with connect_pages_db(ws) as conn:
            row = chats.run_of(conn, turn_id)
            where = chats.dir_of(Tree.read(conn), {"dir": row["dir"]}) if row else None
        if row is None or taking is None or row["owner"] != user_id or row["computer_id"] != taking["computer_id"]:
            raise chats.ChatError(404, chats.NO_TURN)
        label, name = _label(payload.agent) if payload.agent in registry.AGENTS else "the agent", taking["name"]
        if payload.agent not in taking["agents"]:
            raise chats.ChatError(409, f"{label} is not on for you on {name}.")
        if not registry.serves(token_id, ws, row["user_id"], payload.agent):
            raise chats.ChatError(409, f"The owner of {label} no longer lets this person use it.")
        if where["kind"] == "vault" and where["id"] not in taking["folders"]:
            raise chats.ChatError(409, f"{name} does not keep this synced folder.")
        if where["kind"] == "vault" and taking["folders"][where["id"]]:
            raise chats.ChatError(409, f"The synced folder is paused: {label} starts once it syncs again.")
        if where["kind"] == "offered" and where["id"] not in taking["dirs"]:
            raise chats.ChatError(409, f"{name} no longer offers that directory.")
        answer = chats.claim(ws, turn_id, user_id=user_id, holder=token_id, computer_id=taking["computer_id"],
                             run=run, agent=payload.agent, computer=name)
    answer["tools"] = _tools(request, ws, turn_id, run, answer["turn"]["for"]["id"],
                             agents.computer_agent(taking["computer_id"], payload.agent).id)
    return answer


def _mcp_url(request: Request) -> str:
    """This server's MCP endpoint as another computer reaches it: its
    public address (Settings → Administration → Server, ``public_url``),
    else the loopback address the claim came to (a server on the computer
    itself); "" for none, the transport refusing any other host
    (``server_settings.mcp_allowed_hosts``)."""
    from ..server_settings import LOOPBACK_HOSTS, public_url_settings, validate_public_url

    configured = public_url_settings()["public_url"]
    if configured:
        try:
            return validate_public_url(configured).rstrip("/") + "/mcp"
        except ValueError:
            return ""
    url = urlsplit(str(request.base_url))
    return f"{url.scheme}://{url.netloc}/mcp" if url.hostname in LOOPBACK_HOSTS else ""


def _tools(request: Request, ws: str, turn_id: str, run: str, person: str, agent_id: str) -> dict | None:
    """Gamma's tools for the far turn ``turn_id`` the run ``run`` holds
    (step 4.2 of docs/research/agents-as-participants-plan.md): ``{url,
    token}``, a turn token minted for the account ``person`` the turn is for
    (``integrations.mint_turn``: of ``ws`` alone, named with the agent's id
    ``agent_id``, so what it writes is that agent's, living
    ``chats.TOKEN_S``) in place of one an earlier claim of the run minted,
    and this server's MCP address (``_mcp_url``), which the computer takes
    only where its origin is the server it speaks to. None where this server
    has no address another computer reaches: the turn runs without them."""
    from .. import integrations
    from ..local_agents import chats

    url = _mcp_url(request)
    if not url:
        return None
    token_id, token = integrations.mint_turn(person, ws, agent_id, chats.TOKEN_S)
    try:
        old = chats.keep_token(ws, turn_id, run, token_id)
    except chats.ChatError:
        integrations.drop(token_id)   # the turn ended meanwhile
        return None
    if old:
        integrations.drop(old)
    return {"url": url, "token": token}


def _step(raw) -> dict:
    """A step of the agent's message as a run writes it, kept as the
    chat's messages keep one (runner.step_of): ``{id, kind, status}``, with
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


def _ask(raw) -> dict:
    """An ask of the agent's message as a run writes it, kept as the chat's
    messages keep one (runner.py ``_ChatTurn._asked``'s ``shown``): ``{id,
    kind, once, command?, command_shown?, marked?, lines?, inner?, shell?,
    background?, cwd?, host?, paths?, diff?, reason?, at, expires_at}``, a
    command or a file change alone, allowed once or declined (``once``: no
    grant is kept for a turn of another computer). 400 for another shape,
    and for a command, a place or a diff longer than the computer shows
    whole: what runs is never cut."""
    from ..local_agents import events, runner

    if (not isinstance(raw, dict) or not _RUN_ID.match(str(raw.get("id") or ""))
            or raw.get("kind") not in ("command", "files")):
        raise HTTPException(status_code=400, detail="An ask is a command or a file change, with its id.")
    ask = {"id": raw["id"], "kind": raw["kind"], "once": True}
    # a marked character reads ⟨U+202E⟩ in ``command_shown`` (events.shown_text)
    limits = {"command": runner.FAR_MAX_COMMAND, "inner": runner.FAR_MAX_COMMAND,
              "command_shown": 10 * runner.FAR_MAX_COMMAND, "shell": 64, "cwd": MAX_ASK_PLACE, "host": MAX_ASK_PLACE,
              "diff": events.MAX_DIFF, "reason": events.MAX_REASON, "at": 64, "expires_at": 64}
    for key, limit in limits.items():
        if key in raw:
            if not isinstance(raw[key], str) or len(raw[key]) > limit:
                raise HTTPException(status_code=400, detail=f"An ask's {key} is text the computer shows whole.")
            ask[key] = raw[key]
    for key in ("marked", "lines"):
        if key in raw:
            if not isinstance(raw[key], int) or isinstance(raw[key], bool) or raw[key] < 0:
                raise HTTPException(status_code=400, detail=f"An ask's {key} is a count.")
            ask[key] = raw[key]
    if raw.get("background") is True:
        ask["background"] = True
    if "paths" in raw:
        paths = raw["paths"]
        if not isinstance(paths, list) or len(paths) > runner.FAR_MAX_PATHS or not all(
                isinstance(p, str) and len(p) <= MAX_ASK_PLACE for p in paths):
            raise HTTPException(status_code=400, detail="An ask's paths are the files it changes.")
        ask["paths"] = paths
    return ask


def _turn_fields(raw: dict) -> dict:
    """The agent message's fields a run writes, as kept: ``text`` (made
    inert again, ``prompt.inert``, at most ``chats.MAX_TEXT``), ``steps``,
    ``changes`` (paths in the folder: one named otherwise, which the
    computer's own file system may allow, left out), ``changed``, ``built``, ``error``,
    ``fresh``, ``asks`` (what the agent asks that this server may answer:
    ``_ask``, at most ``runner.MAX_ASKS``), ``waiting`` (an ask of the turn
    waits for an answer on the computer alone), ``steers`` (the messages
    steered in that the agent took, ``chats.steered_of``), ``state``
    (running or an end), ``model``, ``effort``, ``speed``; ``ended_at`` is this server's clock
    (``chats.write_run``), any other field left out. 400 for one of another
    shape."""
    from ..local_agents import chats, prompt, runner, store

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
    choice("speed", 40)
    if "steps" in raw:
        if not isinstance(raw["steps"], list):
            raise HTTPException(status_code=400, detail="steps is a list.")
        out["steps"] = [_step(step) for step in raw["steps"][:store.MAX_STEPS]]
    if "asks" in raw:
        if not isinstance(raw["asks"], list):
            raise HTTPException(status_code=400, detail="asks is a list.")
        out["asks"] = [_ask(ask) for ask in raw["asks"][:runner.MAX_ASKS]]
    if "steers" in raw:
        if not isinstance(raw["steers"], list):
            raise HTTPException(status_code=400, detail="steers is a list.")
        out["steers"] = chats.steered_of(raw["steers"])
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


MAX_SESSION_ANSWER = 3 * 1024 * 1024   # bytes of a computer's answer of its sessions (sessions.FAR_MAX_BYTES's)


@router.get("/computers/sessions")
def session_asks(request: Request):
    """``{asks: [{id, kind: list | open, agent, dir: {kind, id},
    session?, conversation_id?, rebind?}]}``: what this server asks of the
    sessions of the computer's agents (gamma/computers/sessions.py
    ``taken``: History's *On {computer}* of the token's account), each
    handed out once."""
    from ..computers import sessions

    _user_id, _username, _ws, token_id = _computer(request)
    return {"asks": sessions.taken(token_id)}


@router.post("/computers/sessions/{ask_id}")
async def session_answer(ask_id: str, request: Request):
    """The computer's answer to its ask ``ask_id`` (``_session_answer``) →
    ``{ok}``, handed to the route that waits for it. 400 for an answer of
    another shape; 404 for no ask of this computer's that waits (one asked
    of another token, answered already, or given up); 413 past
    ``MAX_SESSION_ANSWER``."""
    from ..computers import sessions

    _user_id, _username, _ws, token_id = _computer(request)
    raw = await read_body(request, MAX_SESSION_ANSWER, "An answer of sessions is at most 3 MiB.")
    try:
        body = json.loads(raw or b"null")
    except ValueError:
        raise HTTPException(status_code=400, detail="The answer is no JSON.")
    ask = sessions.pending(token_id, ask_id)
    if ask is None:
        raise HTTPException(status_code=404, detail="No ask of this computer's has that id.")
    if not sessions.answered(token_id, ask_id, await asyncio.to_thread(_session_answer, ask, body)):
        raise HTTPException(status_code=404, detail="No ask of this computer's has that id.")
    return {"ok": True}


def _stamp(raw) -> str:
    return raw if isinstance(raw, str) and len(raw) <= 64 else ""


def _session_answer(ask: dict, raw) -> dict:
    """A computer's answer to ``ask`` as kept (gamma/local_agents/sessions.py
    ``answer_far``): ``{error, status}`` (404 or 409) for a refusal; to a
    listing, ``{sessions: [{id, title, at, created, source, busy,
    conversation}]}``, at most ``sessions.MAX_LISTED``, one of another shape
    left out; to an opening, ``{busy: true}``, ``{conversation}`` (the
    conversation of Gamma's it names as the session's) or ``{title,
    messages}``, the session's turns as this server keeps a conversation's
    messages (``_session_message``). 400 for another shape."""
    from ..local_agents import chats, events, sessions

    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="An answer is an object.")
    if "error" in raw:
        return {"error": events.one_line(str(raw["error"]), MAX_ERROR) or "The computer refused it.",
                "status": 404 if raw.get("status") == 404 else 409}
    if ask["kind"] == "list":
        if not isinstance(raw.get("sessions"), list):
            raise HTTPException(status_code=400, detail="sessions is a list.")
        rows = []
        for row in raw["sessions"][:sessions.MAX_LISTED]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not events.SESSION.match(row["id"]):
                continue
            conversation = row.get("conversation")
            rows.append({"id": row["id"], "title": chats.title_of(str(row.get("title") or "")),
                         "at": _stamp(row.get("at")), "created": _stamp(row.get("created")),
                         "source": row.get("source") if row.get("source") in sessions.SOURCES else "other",
                         "busy": row.get("busy") is True,
                         "conversation": conversation if isinstance(conversation, str)
                         and chats.MESSAGE_ID.match(conversation) else ""})
        return {"sessions": rows}
    if raw.get("busy") is True:
        return {"busy": True}
    if "conversation" in raw:
        if not isinstance(raw["conversation"], str) or not chats.MESSAGE_ID.match(raw["conversation"]):
            raise HTTPException(status_code=400, detail="conversation is a conversation's id.")
        return {"conversation": raw["conversation"]}
    if not isinstance(raw.get("messages"), list) or len(raw["messages"]) > 2 * sessions.MAX_TURNS:
        raise HTTPException(status_code=400, detail="messages is a list of a session's turns.")
    return {"title": chats.title_of(str(raw.get("title") or "")),
            "messages": [_session_message(m, ask["agent"]) for m in raw["messages"]]}


def _session_message(raw, agent: str) -> dict:
    """A message of a session's turn as a computer sent it, kept as the
    chat's messages keep one (``sessions.user_message``, ``agent_message``):
    its text made inert (``prompt.inert``), its steps as a turn's writes
    keep them (``_step``), its state an end. 400 for another shape."""
    from ..local_agents import chats, events, prompt, sessions, store

    if not isinstance(raw, dict) or raw.get("role") not in ("user", "agent") or not isinstance(raw.get("text"), str):
        raise HTTPException(status_code=400, detail="A message is the person's or the agent's, with its text.")
    text, at = prompt.inert(raw["text"].strip())[:chats.MAX_TEXT], _stamp(raw.get("at")) or page_now()
    if raw["role"] == "user":
        return sessions.user_message(text, at)
    steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
    error = raw.get("error") if isinstance(raw.get("error"), str) else ""
    return sessions.agent_message(agent, text=text, steps=[_step(step) for step in steps[:store.MAX_STEPS]],
                                  state=raw.get("state") if raw.get("state") in chats.ENDED else "done",
                                  error=events.one_line(prompt.inert(error), sessions.MAX_ERROR), at=at,
                                  ended_at=_stamp(raw.get("ended_at")) or at)


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
