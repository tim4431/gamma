"""The agents on this computer (gamma/local_agents/, docs/dev/local_agents.md
"The routes"): which of Codex CLI and Claude Code the desktop app's own
server finds here and whether each is ready, for the sync panel's agent row;
and the chat's computer side (``/chat``, gamma/local_agents/chats.py): a
page's conversation with the work folder's agent, its turns, the answers to
what they ask, Stop, New chat and History, the command line the desktop
shell opens the conversation with in a terminal, and the source lines of
the composer's selections. Only a server that runs agents answers
(``local_agents.enabled``); any other answers 404. A signed-in account's
session only: an integration token and the guest are refused, as on the
folder-links routes. The chat answers only the maker of the vault of the
work folder the page is a live file of; anyone else gets 404, so nobody
else learns it exists."""

import threading
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import local_agents
from ..auth import require_personal_user_id, require_ws

router = APIRouter(prefix="/api/local-agents", tags=["local-agents"])

SESSION_ONLY = "Sign in with an account to talk to the agent on this computer."
NOT_HERE = "no such page of a work folder of yours on this computer"

FRESH_EVERY_S = 10   # a re-probe asked for runs at most this often on this server; else the kept answers

_fresh_lock = threading.Lock()
_fresh_at = 0.0       # when the last re-probe asked for began (monotonic)


@router.get("")
def list_agents(request: Request, fresh: int = 0):
    """``{agents: [{name, label, found, version, ready, problem, models,
    at}]}`` (``models`` what each offers, ``[{id, label, efforts, effort,
    default}]``), Codex then Claude Code, as last asked (``catalog.cached``: nothing runs,
    and an answer missing or stale is asked in the background, ``found``
    and ``ready`` null meanwhile). ``fresh=1`` asks each agent anew
    (``--version`` and its status command), at most once every
    ``FRESH_EVERY_S`` on this server; sooner, the kept answers."""
    global _fresh_at
    from ..local_agents import catalog

    if not local_agents.enabled():
        raise HTTPException(status_code=404, detail="Agents run on the desktop app's own server.")
    require_personal_user_id(request, "Sign in with an account to see the agents on this computer.")
    if fresh:
        with _fresh_lock:
            now = time.monotonic()
            due = now - _fresh_at >= FRESH_EVERY_S or not _fresh_at
            if due:
                _fresh_at = now
        if due:
            return {"agents": catalog.probes(fresh=True)}
    return {"agents": [catalog.cached(name) for name in catalog.AGENTS]}


@router.get("/waiting")
def waiting_sends(request: Request, link: str):
    """``{waiting: n}``: how many Sends of the vault ``link``'s work folder
    an agent turned on now would take (``runner.waiting``, the rule it
    takes them by), for the question the sync panel asks first. The vault's
    maker only; 404 for no such vault or one of another account."""
    from .. import folder_links
    from ..local_agents import runner

    if not local_agents.enabled():
        raise HTTPException(status_code=404, detail="Agents run on the desktop app's own server.")
    user_id = require_personal_user_id(request, "Sign in with an account to see the agents on this computer.")
    found = folder_links.get_link(link)
    if found is None or found["created_by"] != user_id or not folder_links.work_folder_link(found):
        raise HTTPException(status_code=404, detail="no such work folder on this computer")
    return {"waiting": len(runner.waiting(found["workspace_id"], found))}


# --- the chat's computer side ------------------------------------------------------------------

class TurnBody(BaseModel):
    page: str
    text: str = ""
    selections: list | None = None
    id: str = ""
    model: str = ""
    effort: str = ""


class PageBody(BaseModel):
    page: str


class SourcesBody(BaseModel):
    page: str
    selections: list = []


class AnswerBody(BaseModel):
    decision: str = ""
    note: str = ""


MAX_NOTE = 2000   # characters of what a decline tells the agent


def _maker(request: Request, page: str) -> tuple[str, str, dict]:
    """``(ws, the account, the vault's link)`` of the page for its vault's
    maker (``chats.vault_of``). 404 on a server that runs no agents and for
    any other page or account; 403 for a token or the guest."""
    from ..local_agents import chats

    if not local_agents.enabled():
        raise HTTPException(status_code=404, detail="Agents run on the desktop app's own server.")
    user_id = require_personal_user_id(request, SESSION_ONLY)
    ws = require_ws(request)
    link = chats.vault_of(ws, page, user_id)
    if link is None:
        raise HTTPException(status_code=404, detail=NOT_HERE)
    return ws, user_id, link


def _conversation_of(request: Request, conversation_id: str) -> tuple[str, dict]:
    """``(ws, the conversation)`` for the maker of its page's vault; 404
    otherwise."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    if not local_agents.enabled():
        raise HTTPException(status_code=404, detail="Agents run on the desktop app's own server.")
    user_id = require_personal_user_id(request, SESSION_ONLY)
    ws = require_ws(request)
    with connect_pages_db(ws) as conn:
        conversation = chats.get(conn, conversation_id)
    if conversation is None or conversation["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="No conversation has that id.")
    _maker(request, conversation["page_id"])
    return ws, conversation


def _refused(e) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail)


def _computer(ws: str, link: dict, conversation: dict | None) -> dict:
    """What the strip says: ``{agent, label, ready, problem, models, folder,
    paused, turn, session}``, ``agent`` the work folder's agent here (null for none),
    ``ready``, ``problem`` and ``models`` (what it offers, ``[{id, label,
    efforts, effort, default}]``) as last asked (``catalog.cached``: nothing
    runs; ``ready`` null while it is asked), ``folder`` the work folder's
    name, ``paused`` whether the vault is paused or choosing its files,
    ``turn`` where the conversation's turn stands in the runner
    (``runner.chat_state``) or null, ``session`` whether a terminal can
    continue the conversation (``_session_of``; the id itself goes only to
    the shell, ``terminal``)."""
    from .. import folder_links
    from ..db import connect_pages_db
    from ..local_agents import catalog, chats, runner, store

    row = store.agent_of(link["id"])
    name = row["agent"] if row and row["agent"] in catalog.AGENTS else None
    found = catalog.cached(name) if name else {"ready": False, "problem": "", "models": []}
    with connect_pages_db(ws) as conn:
        named = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (link["folder_id"],)).fetchone()
    reply = chats.busy(conversation)
    return {"agent": name, "label": catalog.label(name) if name else "", "ready": found["ready"],
            "problem": found["problem"], "models": found.get("models") or [],
            "folder": (named[0] if named else "") or folder_links.dest_of(link).name,
            "paused": bool(link["status"].get(folder_links.PAUSED)) or folder_links.choosing(link),
            "turn": runner.chat_state(ws, reply["turn_id"]) if reply else None,
            "session": bool(_session_of(link, name, conversation))}


@router.get("/chat")
def read_chat(request: Request, page: str = "", version: str = ""):
    """``{computer, conversation}``: the strip (``_computer``) and the page's
    active conversation of the account (``{id, title, agent, messages,
    version}``, null for none); with ``version`` the one stored,
    ``{computer, same: true}``, so a poll reads little."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    ws, user_id, link = _maker(request, page)
    with connect_pages_db(ws) as conn:
        conversation = chats.active(conn, page, user_id)
    computer = _computer(ws, link, conversation)
    if version and conversation is not None and str(conversation["version"]) == version:
        return {"computer": computer, "same": True}
    return {"computer": computer, "conversation": chats.shown(conversation)}


@router.post("/chat/turns", status_code=202)
def start_turn(body: TurnBody, request: Request):
    """A turn: the person's message (``text``, its ``selections`` ``[{text,
    page, box}]``) and the agent's queued one written in the page's active
    conversation (one is started when it has none), the turn queued for the
    vault's agent with the ``model`` and ``effort`` asked for ("" or none:
    the agent's default) → 202 ``{conversation, turn: {id, state}}``. A
    repeated ``id`` (the person's message's) answers the turn it made. 400
    for no text or too long a one, and a model or an effort the agent did
    not list (``catalog.refused_choice``); 409 while a turn of the conversation is queued
    or running, while the folder has no agent, is paused or chooses its
    files, or the agent is not ready (its problem); 403 when the maker is
    no editor or the server or the workspace takes no writes."""
    from .. import folder_links, hosted, workspaces
    from ..local_agents import catalog, chats, runner, store

    ws, user_id, link = _maker(request, body.page)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Write a message first.")
    if len(text) > chats.MAX_TEXT:
        raise HTTPException(status_code=400, detail=f"A message is at most {chats.MAX_TEXT} characters.")
    if body.id and not chats.MESSAGE_ID.match(body.id):
        raise HTTPException(status_code=400, detail="That message id is not one Gamma makes.")
    row = store.agent_of(link["id"])
    if row is None or row["agent"] not in catalog.AGENTS:
        raise HTTPException(status_code=409, detail="No agent is on for this work folder on this computer.")
    name, label = row["agent"], catalog.label(row["agent"])
    if link["status"].get(folder_links.PAUSED) or folder_links.choosing(link):
        raise HTTPException(status_code=409,
                            detail=f"The work folder is paused on this computer: {label} starts once it syncs again.")
    found = catalog.cached(name)
    if found["ready"] is False:
        raise HTTPException(status_code=409, detail=found["problem"] or f"{label} is not ready on this computer.")
    model, effort = body.model.strip(), body.effort.strip()
    refusal = catalog.refused_choice(name, found.get("models") or [], model, effort)
    if refusal:
        raise HTTPException(status_code=400, detail=refusal)
    if not workspaces.at_least(workspaces.role_of(ws, user_id), "editor"):
        raise HTTPException(status_code=403, detail=f"Only an owner or an editor of this workspace can talk to {label}.")
    refused = hosted.read_only()
    if not refused:
        try:
            workspaces.refuse_writes(ws)
        except HTTPException as e:
            refused = str(e.detail)
    if refused:
        raise HTTPException(status_code=403, detail=refused)
    try:
        conversation, reply, new = chats.begin(ws, page_id=body.page, user_id=user_id, link_id=link["id"],
                                               agent=name, label=label, text=text,
                                               selections=chats.selections_of(body.selections), message_id=body.id,
                                               model=model, effort=effort)
    except chats.ChatError as e:
        raise _refused(e) from None
    if new:
        runner.queue_chat(ws, link["id"], reply["turn_id"], conversation_id=conversation["id"], page_id=body.page,
                          agent=name, user_id=user_id, model=model, effort=effort)
    return {"conversation": chats.shown(conversation), "turn": {"id": reply["turn_id"], "state": reply["state"]}}


@router.post("/chat/turns/{turn_id}/stop")
def stop_turn(turn_id: str, request: Request):
    """``{stopped}``: the turn's stop set (``runner.stop_chat``: a queued
    one leaves the queue and reads stopped); false for a turn that ended.
    404 for a turn of no conversation of the account's."""
    from ..local_agents import runner, store

    turn = store.get_turn(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="No turn has that id.")
    ws, conversation = _conversation_of(request, turn["conversation_id"])
    if turn["ws"] != ws:
        raise HTTPException(status_code=404, detail="No turn has that id.")
    return {"stopped": runner.stop_chat(ws, turn_id, conversation["id"])}


@router.post("/chat/turns/{turn_id}/asks/{ask_id}")
def answer_ask(turn_id: str, ask_id: str, body: AnswerBody, request: Request):
    """``{decision: once | chat | deny, note?}``: the person's answer to
    what the turn's agent asks (an agent message's ``asks``) → ``{answered:
    true}``; ``chat`` allows that command again in this conversation,
    ``note`` (at most ``MAX_NOTE`` characters) is what a decline tells the
    agent (``runner.answer_chat``). 404 for a turn of no conversation of
    the account's or no such ask; 409 for one answered already or no longer
    waited for, and a turn that ended; 400 for another decision, too long a
    note, or ``chat`` on what is not a command."""
    from ..local_agents import chats, events, runner, store

    turn = store.get_turn(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="No turn has that id.")
    ws, _ = _conversation_of(request, turn["conversation_id"])
    if turn["ws"] != ws:
        raise HTTPException(status_code=404, detail="No turn has that id.")
    if body.decision not in events.DECISIONS:
        raise HTTPException(status_code=400, detail="Answer once, chat or deny.")
    note = body.note.strip()
    if len(note) > MAX_NOTE:
        raise HTTPException(status_code=400, detail=f"A note is at most {MAX_NOTE} characters.")
    try:
        runner.answer_chat(ws, turn_id, ask_id, body.decision, note)
    except chats.ChatError as e:
        raise _refused(e) from None
    return {"answered": True}


@router.get("/chat/terminal")
def terminal(request: Request, page: str = ""):
    """``{cwd, argv, agent, label}``: how the desktop shell opens the page's
    active conversation in a terminal (Continue in a terminal), asked by the
    shell itself, never taken from a page: ``cwd`` the vault's directory,
    ``argv`` the agent's command line (``catalog.command``) resuming the
    conversation's session (Codex's ``resume <thread>``, Claude Code's
    ``--resume <session>``), the session the conversation's agent ran in
    last, in the vault's directory as it is now, and of the form a session
    has (``events.SESSION``). 404 for a conversation with no such session;
    409 while a turn of it is queued or running (two writers on one session
    are unsafe), and when the folder has no agent or it is not on this
    computer."""
    from ..db import connect_pages_db
    from ..local_agents import catalog, chats, events, store

    ws, user_id, link = _maker(request, page)
    row = store.agent_of(link["id"])
    if row is None or row["agent"] not in catalog.AGENTS:
        raise HTTPException(status_code=409, detail="No agent is on for this work folder on this computer.")
    name, label = row["agent"], catalog.label(row["agent"])
    with connect_pages_db(ws) as conn:
        conversation = chats.active(conn, page, user_id)
    if chats.busy(conversation) is not None:
        raise HTTPException(status_code=409, detail=f"{label} is still working: stop it first.")
    session = _session_of(link, name, conversation)
    if not session:
        raise HTTPException(status_code=404, detail="This conversation has no session to continue.")
    argv = catalog.command(name)
    if argv is None:
        raise HTTPException(status_code=409, detail=f"{label} is not on this computer.")
    resume = ["resume", session] if name == "codex" else ["--resume", session]
    return {"cwd": link["path"], "argv": argv + resume, "agent": name, "label": label}


def _session_of(link: dict, name: str | None, conversation: dict | None) -> str:
    """The session of the conversation a terminal can continue: the one the
    vault's agent ``name`` ran it in last, in the vault's directory as it is
    now, of the form a session has (``events.SESSION``); "" for none."""
    from ..local_agents import events, store

    ran = store.conversation_of(conversation["id"]) if conversation is not None and name else None
    if (ran is None or ran["agent"] != name or ran["cwd"] != link["path"] or ran["link_id"] != link["id"]
            or not events.SESSION.match(ran["session"] or "")):
        return ""
    return ran["session"]


@router.post("/chat/sources")
def selection_sources(body: SourcesBody, request: Request):
    """``{sources: [{file, line, end?} | null]}``: the line of the work
    folder's sources each of the page's ``selections`` (``[{text, page,
    box}]``, at most ``chats.MAX_SELECTIONS``) maps to, in their order
    (``chats.sources_of``; null where nothing answers), for the composer's
    chips. Runs no process. 400 for more selections."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    ws, _, _ = _maker(request, body.page)
    if len(body.selections) > chats.MAX_SELECTIONS:
        raise HTTPException(status_code=400, detail=f"At most {chats.MAX_SELECTIONS} selections at a time.")
    kept = [(chats.selections_of([item]) or [None])[0] for item in body.selections]
    with connect_pages_db(ws) as conn:
        return {"sources": chats.sources_of(ws, conn, body.page, kept)}


@router.post("/chat/new")
def new_chat(body: PageBody, request: Request):
    """New chat: the page's active conversation archived → ``{conversation:
    null}``. 409 while its turn is queued or running."""
    from ..local_agents import catalog, chats, store

    ws, user_id, link = _maker(request, body.page)
    row = store.agent_of(link["id"])
    try:
        chats.new(ws, body.page, user_id, catalog.label(row["agent"]) if row else "The agent")
    except chats.ChatError as e:
        raise _refused(e) from None
    return {"conversation": None}


@router.get("/chat/history")
def chat_history(request: Request, page: str = ""):
    """``{conversations: [{id, title, agent, updated_at, turns}]}``: the
    page's archived conversations of the account, newest first."""
    from ..local_agents import chats

    ws, user_id, _ = _maker(request, page)
    return {"conversations": chats.history(ws, page, user_id)}


@router.post("/chat/history/{conversation_id}/open")
def open_conversation(conversation_id: str, request: Request):
    """An archived conversation becomes its page's active one, the active
    one archived → ``{conversation}``. 409 while the active one's turn is
    queued or running."""
    from ..local_agents import catalog, chats

    ws, conversation = _conversation_of(request, conversation_id)
    try:
        opened = chats.open_one(ws, conversation, catalog.label(conversation["agent"]))
    except chats.ChatError as e:
        raise _refused(e) from None
    return {"conversation": chats.shown(opened)}


@router.delete("/chat/history/{conversation_id}")
def delete_conversation(conversation_id: str, request: Request):
    """An archived conversation deleted → ``{deleted: true}``; 409 for the
    page's active one."""
    from ..local_agents import chats

    ws, conversation = _conversation_of(request, conversation_id)
    try:
        chats.delete(ws, conversation)
    except chats.ChatError as e:
        raise _refused(e) from None
    return {"deleted": True}
