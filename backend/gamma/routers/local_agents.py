"""The agents on this computer (gamma/local_agents/, docs/dev/local_agents.md
"The routes"): which of Codex CLI and Claude Code the desktop app's own
server finds here, whether each is ready, whether it is on, what it may run
and the directories offered them, for Settings; turning one on or off,
letting it ask, and the directories offered, with the desktop shell's word
for what widens what an agent reaches (gamma/routers/shell.py); and the
chat's computer side (``/chat``, gamma/local_agents/chats.py): a bucket's
conversation with the person's agent (a page's, a folder's, the library's
``home``), where the agent works (the work folder's vault, an offered
directory, one of its own: ``chats.place_of``), its turns, the answers to
what they ask, Stop, New chat and History, the command line the desktop
shell opens the conversation with in a terminal, and the source lines of
the composer's selections. Only a server that runs agents answers
(``local_agents.enabled``) for its own vaults; any other answers 404, but
for the listing, which says it runs none (``here`` false). A
signed-in account's session only: an integration token and the guest are
refused, as on the folder-links routes. The chat answers only an account
with an agent on here, or a vault's maker on that vault's files and
folders; anyone else gets 404, so nobody else learns it exists.

Every server's chat also answers for a page whose work folder a computer
of the session's account keeps with an agent there (step 4d,
``chats.remote_of``, gamma/computers/registry.py ``agent_of``): the same
routes and answers, the conversation kept here and its turns run on that
computer, which takes them over its channel (gamma/routers/computers.py);
what such a turn asks is answered on that computer, never here (409), and
the terminal and the selections' source lines are the computer's own (404,
nulls).

The asks on this computer (``/asks``, step 5b of
docs/research/agent-collab.md): what the turns of other servers' work
folders that run here wait for, read and answered by the desktop app
alone: a server that runs agents (404 elsewhere), the shell's word, never
unchecked (``shell.shell_strict``: 403, a server started without a secret too),
and a session of the account that made the vaults, which the shell's own
partition holds. No page can read or answer them."""

import asyncio
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import local_agents
from ..auth import require_personal_user_id, require_ws
from .shell import from_shell, shell_strict

router = APIRouter(prefix="/api/local-agents", tags=["local-agents"])

SESSION_ONLY = "Sign in with an account to talk to the agent on this computer."
SEE_AGENTS = "Sign in with an account to see the agents on this computer."
NOT_HERE = "no such page or folder of yours with an agent on this computer"
FAR_ASKS = "This is answered on the computer that runs the agent."   # runner.FAR_ASKS
NO_AGENTS = "Agents run on the desktop app's own server."
NONE_ON = "Turn on Codex or Claude Code in Settings → AI first."

FRESH_EVERY_S = 10   # a re-probe asked for runs at most this often on this server; else the kept answers

_fresh_lock = threading.Lock()
_fresh_at = 0.0       # when the last re-probe asked for began (monotonic)


class AgentPatch(BaseModel):
    enabled: bool | None = None
    runs: str | None = Field(default=None, max_length=16)   # "never" or "ask" (store.RUNS)


class DirEntry(BaseModel):
    id: str = Field(default="", max_length=64)       # an offered directory's, kept; "" for a new one
    path: str = Field(min_length=1, max_length=400)  # its full path on this computer, from the shell's dialog
    name: str = Field(default="", max_length=200)    # how Settings names it; "" for the directory's own


def _agents_account(request: Request) -> str:
    """The session's account, on a server that runs agents (404)."""
    if not local_agents.enabled():
        raise HTTPException(status_code=404, detail=NO_AGENTS)
    return require_personal_user_id(request, SEE_AGENTS)


def _entry(found: dict, row: dict | None, user_id: str) -> dict:
    """An agent as the listing says it: what the catalog found
    (``found``) with how it stands as a code (``catalog.code``), whether it
    is on, what it may run and whether the account ``user_id`` turned it on
    (``mine``: the one who may turn it off or narrow it), ``row`` its
    ``store.agents`` row."""
    from ..local_agents import catalog

    on = bool(row and row["enabled"])
    return {**found, "code": catalog.code(found["name"], found), "enabled": on, "runs": row["runs"] if on else "never",
            "mine": on and row["set_by"] == user_id}


def _agent_of(user_id: str, name: str) -> dict | None:
    """The ``store.agents`` row of the agent a chat request names
    (``name``), while the account turned it on; with no name, the
    account's first agent on (``store.agent_for``). None for neither."""
    from ..local_agents import store

    if not name:
        return store.agent_for(user_id)
    return store.enabled_agents()[name] if store.owns(user_id, name) else None


@router.get("")
def list_agents(request: Request, fresh: int = 0):
    """``{here, computer, agents: [{name, label, found, version, ready,
    problem, code, models, at, enabled, runs, mine}], dirs: [{id, path,
    name, added_at}]}``: ``here`` whether this server runs agents (false
    everywhere but the desktop app's own server, which then lists none),
    ``computer`` this computer's name as its channels announce it
    (``cloud_auth.device``); ``models`` what each agent offers, ``[{id,
    label, efforts, effort, default}]``; ``enabled`` whether it is on,
    ``runs`` what it may run, ``never`` or ``ask``, ``mine`` whether the
    session's account turned it on; ``dirs`` the directories offered beyond
    the vaults; Codex then Claude Code, as last asked (``catalog.cached``:
    nothing runs, and an answer missing or stale is asked in the
    background, ``found`` and ``ready`` null meanwhile). ``fresh=1`` asks
    each agent anew (``--version`` and its status command), at most once
    every ``FRESH_EVERY_S`` on this server; sooner, the kept answers. A
    session's alone (403 for a token or the guest)."""
    global _fresh_at
    from ..cloud_auth import device
    from ..local_agents import catalog, store

    user_id = require_personal_user_id(request, SEE_AGENTS)
    if not local_agents.enabled():
        return {"here": False, "computer": "", "agents": [], "dirs": []}
    found = None
    if fresh:
        with _fresh_lock:
            now = time.monotonic()
            due = now - _fresh_at >= FRESH_EVERY_S or not _fresh_at
            if due:
                _fresh_at = now
        if due:
            found = catalog.probes(fresh=True)
    rows = store.agents()
    found = found or [catalog.cached(name) for name in catalog.AGENTS]
    name = device()[1].strip()[:64]
    return {"here": True, "computer": name if name.isprintable() else "",
            "agents": [_entry(agent, rows.get(agent["name"]), user_id) for agent in found], "dirs": store.dirs()}


@router.patch("/{agent}")
def set_agent(agent: str, body: AgentPatch, request: Request):
    """``{enabled?, runs?}``: ``agent`` turned on or off for this computer,
    and what it may run (``never`` or ``ask``) → the agent as the listing
    says it, with ``queued``, how many waiting Sends it took when turned on
    (the Sends of work folders of other servers are those servers' to hand
    over). Turning one on takes the desktop shell's word (403
    "Only the desktop app may turn on an agent on this computer."), an
    agent that is not another account's (409) and one ready now (400 with
    its problem: this asks it afresh); the account that turns it on owns
    it. Turning it off takes its owner or the shell's word, never
    unchecked (403, ``shell_strict``); ``never`` takes its owner (403);
    ``ask`` takes its owner and the shell's word (403), on an agent that
    is on after this PATCH's ``enabled`` (400). 404 on a server that runs
    no agents and for an agent Gamma does not know; 400 for another
    ``runs``. Every open page of a work folder here hears that the agent
    changed, and the channels announce it to the other servers."""
    from .. import folder_links
    from ..local_agents import catalog, runner, store

    user_id = _agents_account(request)
    if agent not in catalog.AGENTS:
        raise HTTPException(status_code=404, detail=f"Gamma knows no agent named {agent}.")
    label = catalog.label(agent)
    if body.runs is not None and body.runs not in store.RUNS:
        raise HTTPException(status_code=400, detail="What an agent may run is never or ask.")
    row = store.agents().get(agent)
    on = bool(row and row["enabled"])
    owner = row["set_by"] if on else ""
    if body.enabled is True and not on:
        from_shell(request, "Only the desktop app may turn on an agent on this computer.")
        found = catalog.probe(agent, fresh=True)
        if not found["ready"]:
            raise HTTPException(status_code=400, detail=found["problem"] or f"{label} is not ready on this computer.")
    elif body.enabled is True and owner != user_id:
        raise HTTPException(status_code=409, detail=f"{label} was turned on by another account on this computer.")
    elif body.enabled is False and on and owner != user_id:
        shell_strict(request, f"Only the account that turned on {label}, or the desktop app, may turn it off.")
    if body.runs is not None:
        if not (body.enabled if body.enabled is not None else on):
            raise HTTPException(status_code=400, detail=f"Turn {label} on first.")
        if on and owner != user_id:
            raise HTTPException(status_code=403, detail=f"Only the account that turned on {label} may change "
                                                        "what it may run.")
        if body.runs == "ask":
            from_shell(request, "Only the desktop app may let an agent ask to run commands on this computer.")
    queued = 0
    force = body.enabled is False and on and owner != user_id
    if body.enabled is not None and store.set_agent(agent, body.enabled, by=user_id, force=force):
        if body.enabled:
            queued = runner.queue_waiting()
        else:
            runner.stop_agent(agent)
        folder_links.agents_changed()
    if body.runs is not None and store.set_runs(agent, body.runs, by=user_id) and body.runs == "never":
        runner.runs_never(agent)
    return {**_entry(catalog.cached(agent), store.agents().get(agent), user_id), "queued": queued}


@router.put("/dirs")
def set_dirs(body: list[DirEntry], request: Request):
    """``[{id?, path, name}]``: the directories this computer offers its
    agents beyond its vaults, replaced whole (``store.set_dirs``: an
    ``id`` offered keeps its row; one left out goes) → ``{dirs}``. Any
    change takes an account with an agent on here, else the desktop
    shell's word (403); naming a path not offered under that id takes the
    shell's word always (403: only its native dialog names one). 400 for a
    path that is no directory here, or one no vault may have either
    (``folder_links.clean_path``: a drive's root, home, Gamma's data
    directory, a system place), for a new path inside or around a vault of
    this computer or another directory of the list (one place, one queue:
    ``runner._place``), and for more than ``store.MAX_DIRS`` or one given
    twice. A chat turn working in a directory no longer offered there stops
    (``runner.stop_dirs``)."""
    from .. import folder_links
    from ..local_agents import runner, store

    user_id = _agents_account(request)
    if store.agent_for(user_id) is None:
        from_shell(request, "Only an account with an agent on this computer, or the desktop app, may change "
                            "the directories it offers.")
    offered = {entry["id"]: entry["path"] for entry in store.dirs()}
    entries = []
    for entry in body:
        try:
            path = folder_links.clean_path(entry.path)
        except folder_links.LinkError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        entries.append({"id": entry.id, "path": path, "name": entry.name, "new": offered.get(entry.id) != path})
    for entry in entries:
        if not entry.pop("new"):
            continue
        path = entry["path"]
        from_shell(request)
        if not Path(path).is_dir():
            raise HTTPException(status_code=400, detail=f"{path} is no directory on this computer.")
        for link in folder_links.all_links():             # compared as a new vault's place is (``_apart``)
            if folder_links._within(path, link["path"]) or (
                    link["kind"] == "folder" and folder_links._within(link["path"], path)):
                raise HTTPException(status_code=400, detail=f"{path} is inside or around the synced folder "
                                                            f"{link['path']}: its agent works there through it.")
        for other in entries:
            if other is not entry and other["path"] != path and (folder_links._within(path, other["path"])
                                       or folder_links._within(other["path"], path)):
                raise HTTPException(status_code=400, detail=f"{path} is inside or around {other['path']}, "
                                                            "also offered.")
    try:
        kept = store.set_dirs(entries)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    now = {entry["id"]: entry["path"] for entry in kept}
    runner.stop_dirs([path for dir_id, path in offered.items() if now.get(dir_id) != path])
    return {"dirs": kept}


# --- the chat's computer side ------------------------------------------------------------------

class TurnBody(BaseModel):
    bucket: str
    text: str = ""
    selections: list | None = None
    id: str = ""
    # the agent picked for the conversation (``codex``, ``claude``); "" for the account's first on
    agent: str = Field(default="", max_length=32)
    model: str = ""
    effort: str = ""
    # where a conversation that starts works: an offered directory's id, "" for one of its own; none (null)
    # for the active conversation's own (the work folder a bucket is in decides for itself)
    dir: str | None = Field(default=None, max_length=64)


class BucketBody(BaseModel):
    bucket: str


class SourcesBody(BaseModel):
    bucket: str
    selections: list = []


class AnswerBody(BaseModel):
    decision: str = ""
    note: str = ""


MAX_NOTE = 2000   # characters of what a decline tells the agent


def _seat(request: Request, bucket: str, dir: str | None = None, agent: str = "") -> tuple[str, str, dict | None,
                                                                                          dict | None, dict | None]:
    """``(ws, the account, the place, the far seat, the active conversation)``
    of the bucket for the session's account, one of the place and the far
    seat None: where the account's agent here works (``chats.place_of``, on
    a server that runs agents, for the active conversation's ``dir``, or
    the ``dir`` a turn names), else, for a file of a work folder no vault
    here keeps, the computer of the account's that keeps it with an agent
    (``chats.remote_of``). With ``agent`` (the one picked in the chat), an
    active conversation of another agent counts as none: a conversation
    keeps the agent it began with, and the next turn starts another
    (``chats.begin``). 404 for any other bucket or account; 403 for a token
    or the guest."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    user_id = require_personal_user_id(request, SESSION_ONLY)
    ws = require_ws(request)
    with connect_pages_db(ws) as conn:
        conversation = chats.active(conn, bucket, user_id) if bucket else None
    if agent and conversation is not None and conversation["agent"] != agent:
        conversation = None
    chosen = dir if dir is not None else (conversation or {}).get("dir", "")
    place = chats.place_of(ws, bucket, user_id, chosen) if local_agents.enabled() else None
    far = chats.remote_of(ws, bucket, user_id) if place is None or place["link"] is None else None
    if far is not None:
        place = None
    if place is None and far is None:
        raise HTTPException(status_code=404, detail=NOT_HERE)
    return ws, user_id, place, far, conversation


def _cwd(ws: str, place: dict, conversation: dict | None) -> str:
    """The directory the conversation's agent works in, while it is the
    place's (a conversation of its own's: ``chats.own_dir``); "" for none."""
    from ..local_agents import chats

    if conversation is None or conversation["dir"] != place["dir"]:
        return ""
    return place["path"] if place["kind"] != "own" else str(chats.own_dir(ws, conversation["id"]))


def _conversation_of(request: Request, conversation_id: str, *, seated: bool = True) -> tuple[str, dict, dict | None]:
    """``(ws, the conversation, the far seat)`` for its account, with
    ``seated`` while its bucket is one ``_seat`` answers it (to talk in
    it again); without, whatever became of its agents (to keep or delete
    it), the far seat None. 404 otherwise."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    user_id = require_personal_user_id(request, SESSION_ONLY)
    ws = require_ws(request)
    with connect_pages_db(ws) as conn:
        conversation = chats.get(conn, conversation_id)
    if conversation is None or conversation["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="No conversation has that id.")
    if not seated:
        return ws, conversation, None
    _, _, _, far, _ = _seat(request, conversation["bucket"], conversation["dir"])
    return ws, conversation, far


def _far_turn(request: Request, turn_id: str) -> tuple[str, dict, dict]:
    """``(ws, the conversation, the turn's row)`` of a turn that runs on
    another computer (``chats.run_of``), for the account whose turn and
    conversation it is, whatever became of the page's seat since (the
    agent turned off, the computer gone, the page deleted): such a turn
    stays stoppable. 404 otherwise."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    user_id = require_personal_user_id(request, SESSION_ONLY)
    ws = require_ws(request)
    with connect_pages_db(ws) as conn:
        row = chats.run_of(conn, turn_id)
        conversation = chats.get(conn, row["conversation_id"]) if row is not None else None
    if row is None or row["user_id"] != user_id or conversation is None or conversation["user_id"] != user_id:
        raise HTTPException(status_code=404, detail=chats.NO_TURN)
    return ws, conversation, row


def _local_turn(turn_id: str) -> dict | None:
    """The turn this computer recorded (``store.get_turn``), on a server
    that runs agents; None otherwise."""
    from ..local_agents import store

    return store.get_turn(turn_id) if local_agents.enabled() else None


def _claimed_here(request: Request, turn_id: str) -> bool:
    """Whether the turn is one of another server's page that this computer
    claimed for a vault the account made (``store.claimed_of``): what it
    asks is answered in the desktop app's window (``/asks``), never on this
    server's chat route."""
    from .. import folder_links
    from ..local_agents import store

    row = store.claimed_of(turn_id) if local_agents.enabled() else None
    if row is None:
        return False
    link = folder_links.get_link(row["link_id"])
    return link is not None and link["created_by"] == require_personal_user_id(request, SESSION_ONLY)


def _refused(e) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail)


def _computer(ws: str, user_id: str, place: dict, conversation: dict | None, agent: str = "") -> dict:
    """What the strip says: ``{agent, label, ready, problem, models, folder,
    paused, turn, session, folder_id, dir, grants}``, ``agent`` the one
    picked (``agent``) or the account's first here (``_agent_of``; null for
    none),
    ``ready``, ``problem`` and ``models`` (what it offers, ``[{id, label,
    efforts, effort, default}]``) as last asked (``catalog.cached``: nothing
    runs; ``ready`` null while it is asked), ``folder`` the work folder's
    name ("" outside one kept here), ``paused`` whether its vault is paused
    or choosing its files, ``turn`` where the conversation's turn stands in
    the runner (``runner.chat_state``) or null, ``session`` whether a
    terminal can continue the conversation (``_session_of``; the id itself
    goes only to the shell, ``terminal``; never in a vault another account
    made), ``folder_id`` the work folder's
    (W, what the changed files' paths are relative to; null outside one),
    ``dir`` where the agent works (``{kind: "vault", name, path}``, the path
    for the vault's maker alone, ``{kind: "offered", id, name}``, or null
    for a directory of its own), ``grants`` the commands allowed in the
    conversation in that directory (``store.grants_of``: ``[{command, cwd,
    host}]``)."""
    from .. import folder_links
    from ..local_agents import catalog, chats, runner, store

    row = _agent_of(user_id, agent)
    name = row["agent"] if row else None
    found = catalog.cached(name) if name else {"ready": False, "problem": "", "models": []}
    link = place["link"]
    reply = chats.busy(conversation)
    cwd = _cwd(ws, place, conversation)
    if link is not None:
        shown = {"kind": "vault", "name": place["name"],
                 **({"path": place["path"]} if link["created_by"] == user_id else {})}
    else:
        shown = {"kind": "offered", "id": place["dir"], "name": place["name"]} if place["kind"] == "offered" else None
    return {"agent": name, "label": catalog.label(name) if name else "", "ready": found["ready"],
            "problem": found["problem"], "models": found.get("models") or [],
            "folder": place["name"] if link is not None else "",
            "paused": link is not None and (bool(link["status"].get(folder_links.PAUSED)) or folder_links.choosing(link)),
            "turn": runner.chat_state(ws, reply["turn_id"]) if reply else None,
            "session": (link is None or link["created_by"] == user_id) and bool(
                _session_of(place, cwd, name, conversation)),
            "folder_id": place["dir"] if link is not None else None, "dir": shown,
            "grants": store.grants_of(conversation["id"], dir=cwd) if cwd else []}


def _far_computer(ws: str, far: dict, conversation: dict | None) -> dict:
    """What the strip says for a page whose agent runs on another computer
    (``far``, ``chats.remote_of``): ``_computer``'s fields as that computer
    last announced them (``ready``, the ``models`` it offers; ``problem``
    while it is online and its agent is not ready, in the words the @ menu
    says it), ``turn`` where the conversation's turn stands here
    (``{turn_id, state, ahead: false}``, ``computer_chat_runs``), no
    ``session``, no ``grants`` (it never asks), ``folder_id`` the work
    folder's, ``dir`` that work folder by its name, and ``computer`` its
    name, ``online``, ``since`` when it went offline (null while online),
    ``remote: true``."""
    from ..computers import registry
    from ..db import connect_pages_db
    from ..local_agents import chats
    from .feedback import _problem  # local: the feedback routes reach the agents' package

    assistants, seen_at = registry.said_of(far["token_id"])
    said = next((a for a in assistants if isinstance(a, dict) and a.get("name") == far["agent"]), {})
    reply = chats.busy(conversation)
    with connect_pages_db(ws) as conn:
        named = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (far["folder_id"],)).fetchone()
        row = chats.run_of(conn, reply["turn_id"]) if reply else None
    folder = (named[0] if named else "") or ""
    return {"agent": far["agent"], "label": far["label"], "ready": far["ready"],
            "problem": _problem(far) if far["online"] else "", "models": said.get("models") or [],
            "folder": folder, "paused": far["paused"],
            "turn": {"turn_id": row["turn_id"], "state": row["state"], "ahead": False}
            if row and row["state"] in chats.BUSY else None,
            "session": False, "folder_id": far["folder_id"], "dir": {"kind": "vault", "name": folder}, "grants": [],
            "computer": far["computer"], "online": far["online"],
            "since": None if far["online"] else (seen_at or None), "remote": True}


@router.get("/chat")
def read_chat(request: Request, bucket: str = "", version: str = "", agent: str = ""):
    """``{computer, conversation}``: the strip (``_computer``, or
    ``_far_computer`` for an agent on another computer) and the bucket's
    active conversation of the account (``{id, title, agent, messages,
    version}``, null for none, and for one of another agent than the one
    picked, ``agent``: ``_seat``); with ``version`` the one stored,
    ``{computer, same: true}``, so a poll reads little."""
    from ..local_agents import chats

    ws, user_id, place, far, conversation = _seat(request, bucket, agent=agent)
    computer = (_computer(ws, user_id, place, conversation, agent) if place is not None
                else _far_computer(ws, far, conversation))
    if version and conversation is not None and str(conversation["version"]) == version:
        return {"computer": computer, "same": True}
    return {"computer": computer, "conversation": chats.shown(conversation)}


@router.post("/chat/turns", status_code=202)
def start_turn(body: TurnBody, request: Request):
    """A turn: the person's message (``text``, its ``selections`` ``[{text,
    page, box}]``) and the agent's queued one written in the bucket's active
    conversation (one is started when it has none, or when it works
    elsewhere than the turn: ``chats.begin``), the turn queued where the
    agent works (``chats.place_of``: the work folder's vault, else the
    offered directory ``dir`` names, else one of its own; ``dir`` left out,
    the active conversation's) with the ``model`` and ``effort`` asked for
    ("" or none: the agent's default) → 202 ``{conversation, turn: {id,
    state}}``, the turn's agent the one picked (``agent``, an agent the
    account turned on here), else the account's first (``_agent_of``); an
    active conversation of another agent is archived and a new one begun. A
    repeated ``id`` (the person's message's) answers the turn it made. 400
    for no text or too long a one, a ``dir`` no directory offered here has,
    and a model or an effort the agent did not list
    (``catalog.refused_choice``); 409 while a turn of the conversation is
    queued or running, while the account has no agent on here, or not the
    one picked, the folder is paused or chooses its files, or the agent is
    not ready (its problem); 403 when the account is no editor or the
    server or the workspace takes no writes. For an agent on another
    computer (``_far_turn_started``) the turn waits here for it, ``agent``
    unread: that computer's agent is the page's."""
    from .. import folder_links
    from ..local_agents import catalog, chats, runner

    ws, user_id, place, far, _ = _seat(request, body.bucket, body.dir, body.agent)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Write a message first.")
    if len(text) > chats.MAX_TEXT:
        raise HTTPException(status_code=400, detail=f"A message is at most {chats.MAX_TEXT} characters.")
    if body.id and not chats.MESSAGE_ID.match(body.id):
        raise HTTPException(status_code=400, detail="That message id is not one Gamma makes.")
    if far is not None:
        return _far_turn_started(ws, user_id, far, body, text)
    if body.dir and place["kind"] == "own":
        raise HTTPException(status_code=400, detail="No directory offered on this computer has that id.")
    row = _agent_of(user_id, body.agent)
    if row is None:
        raise HTTPException(status_code=409, detail=f"Turn on {catalog.label(body.agent)} in Settings → AI first."
                            if body.agent in catalog.AGENTS else NONE_ON)
    name, label = row["agent"], catalog.label(row["agent"])
    link = place["link"]
    if link is not None and (link["status"].get(folder_links.PAUSED) or folder_links.choosing(link)):
        raise HTTPException(status_code=409,
                            detail=f"The synced folder is paused on this computer: {label} starts once it syncs again.")
    found = catalog.cached(name)
    if found["ready"] is False:
        raise HTTPException(status_code=409, detail=found["problem"] or f"{label} is not ready on this computer.")
    model, effort = body.model.strip(), body.effort.strip()
    refusal = catalog.refused_choice(name, found.get("models") or [], model, effort)
    if refusal:
        raise HTTPException(status_code=400, detail=refusal)
    _may_write(ws, user_id, label)
    try:
        conversation, reply, new = chats.begin(ws, bucket=body.bucket, dir=place["dir"], user_id=user_id,
                                               link_id=link["id"] if link is not None else "", agent=name,
                                               label=label, text=text, selections=chats.selections_of(body.selections),
                                               message_id=body.id, model=model, effort=effort)
    except chats.ChatError as e:
        raise _refused(e) from None
    if new:
        runner.queue_chat(ws, place, reply["turn_id"], conversation_id=conversation["id"], bucket=body.bucket,
                          agent=name, user_id=user_id, model=model, effort=effort)
    return {"conversation": chats.shown(conversation), "turn": {"id": reply["turn_id"], "state": reply["state"]}}


def _may_write(ws: str, user_id: str, label: str) -> None:
    """403 when the account is no editor of the workspace, or the server or
    the workspace takes no writes."""
    from .. import hosted, workspaces

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


def _far_turn_started(ws: str, user_id: str, far: dict, body: TurnBody, text: str) -> dict:
    """``start_turn`` for an agent on another computer (``far``): the same
    checks against what it last announced (paused there; its agent known
    not ready while it is online; the model and the effort among the ones
    it offers), the turn written here to wait for it (``chats.begin``
    ``remote``, the conversation working in the work folder) and the
    computer woken (``chat``): it takes the turn over its channel, now or
    when it connects."""
    from ..computers import registry
    from ..local_agents import catalog, chats
    from .feedback import _problem  # local: the feedback routes reach the agents' package

    name, label, computer = far["agent"], far["label"], far["computer"]
    if far["paused"]:
        raise HTTPException(status_code=409,
                            detail=f"The synced folder is paused on {computer}: {label} starts once it syncs again.")
    if far["online"] and far["ready"] is False:
        raise HTTPException(status_code=409, detail=_problem(far))
    assistants, _seen = registry.said_of(far["token_id"])
    said = next((a for a in assistants if isinstance(a, dict) and a.get("name") == name), {})
    model, effort = body.model.strip(), body.effort.strip()
    refusal = catalog.refused_choice(name, said.get("models") or [], model, effort)
    if refusal:
        raise HTTPException(status_code=400, detail=refusal.replace("on this computer", f"on {computer}"))
    _may_write(ws, user_id, label)
    try:
        conversation, reply, new = chats.begin(ws, bucket=body.bucket, dir=far["folder_id"], user_id=user_id,
                                               link_id="", agent=name, label=label, text=text,
                                               selections=chats.selections_of(body.selections), message_id=body.id,
                                               model=model, effort=effort, remote=True, computer=far["computer"])
    except chats.ChatError as e:
        raise _refused(e) from None
    if new:
        registry.wake(far["token_id"], "chat")
    return {"conversation": chats.shown(conversation), "turn": {"id": reply["turn_id"], "state": reply["state"]}}


@router.post("/chat/turns/{turn_id}/stop")
def stop_turn(turn_id: str, request: Request):
    """``{stopped}``: the turn's stop set (``runner.stop_chat``: a queued
    one leaves the queue and reads stopped); false for a turn that ended.
    For a turn that runs on another computer (``chats.stop_run``): a queued
    one, a running one while that computer is away, or one Stopped again,
    reads stopped now; a running one's stop is set and the computer woken
    to hear it. It is the account's to stop though its page's seat is gone
    (``_far_turn``). 404 for a turn of no conversation of the account's."""
    from ..computers import registry
    from ..local_agents import chats, runner

    turn = _local_turn(turn_id)
    if turn is None:
        ws, _, row = _far_turn(request, turn_id)
        stopped = chats.stop_run(ws, turn_id, online=registry.is_online)
        if stopped and row["holder"]:
            registry.wake(row["holder"], "chat")
        return {"stopped": stopped}
    ws, conversation, _ = _conversation_of(request, turn["conversation_id"])
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
    waited for, and a turn that ended, and for a turn that runs on another
    computer (``FAR_ASKS``: what it asks is answered there, in its desktop
    app's window); 400 for another decision, too long a note, or ``chat``
    on what is not a command."""
    from ..local_agents import chats, events, runner

    turn = _local_turn(turn_id)
    if turn is None:
        if not _claimed_here(request, turn_id):
            _far_turn(request, turn_id)
        raise HTTPException(status_code=409, detail=FAR_ASKS)
    ws, _, _ = _conversation_of(request, turn["conversation_id"])
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


@router.delete("/chat/grants/{conversation_id}")
def forget_grants(conversation_id: str, request: Request):
    """Allowed in this chat taken back: the conversation's commands ask
    again (``store.forget_grants``) → ``{forgotten: true}``. A conversation
    whose agent runs on another computer has none here."""
    from ..local_agents import store

    _, conversation, far = _conversation_of(request, conversation_id)
    if far is None:
        store.forget_grants(conversation["id"])
    return {"forgotten": True}


MAX_ASKS_WAIT = 20   # seconds the desktop app's read of the asks is held at most


def _shell(request: Request, refused: str) -> str:
    """The account of the desktop app's request: a server that runs agents
    (404), the shell's word, a server without a secret refusing it too
    (403, ``shell_strict``), a session."""
    if not local_agents.enabled():
        raise HTTPException(status_code=404, detail=NO_AGENTS)
    return shell_strict(request, refused)


@router.get("/asks")
async def computer_asks(request: Request, after: int = -1, wait: float = 0):
    """``{version, asks: [...]}``: what the turns of other servers' work
    folders running here wait for, of the vaults the account made, oldest
    first (``runner.computer_asks``: ``{id, turn_id, link_id, agent, label,
    kind, command?, lines?, inner?, shell?, marked?, background?, dir,
    folder, file, host?, paths?, diff?, reason?, server: {origin, account},
    at, expires_at}``, every string marked where it would not show), the
    desktop app's alone. Held up to ``wait`` seconds (at most
    ``MAX_ASKS_WAIT``) while ``version`` is ``after``; a read is what tells
    the runner the app is there to answer (``runner.wait_asks``). 404 on a
    server that runs no agents; 403 without the shell's word."""
    from ..local_agents import runner

    user_id = await asyncio.to_thread(
        _shell, request, "Only the desktop app reads what waits for an answer on this computer.")
    version = await asyncio.to_thread(runner.wait_asks, after, max(0.0, min(wait, MAX_ASKS_WAIT)))
    asks = await asyncio.to_thread(runner.computer_asks, user_id)
    return {"version": version, "asks": asks}


@router.post("/asks/{ask_id}")
def answer_computer_ask(ask_id: str, body: AnswerBody, request: Request):
    """``{decision: once | deny}``: the desktop app's answer to an ask on
    this computer (``runner.answer_computer``), given to the agent before
    this returns, never waiting on the other server → ``{answered: true,
    version}``, the asks' version with it gone. 404 on a server that runs no
    agents, and for no such ask of the account's vaults; 403 without the
    shell's word; 400 for another decision; 409 for one answered,
    withdrawn or declined meanwhile, or a turn that ended."""
    from ..local_agents import chats, runner

    user_id = _shell(request, "Only the desktop app answers on this computer.")
    try:
        version = runner.answer_computer(ask_id, body.decision, user_id)
    except chats.ChatError as e:
        raise _refused(e) from None
    return {"answered": True, "version": version}


@router.get("/chat/terminal")
def terminal(request: Request, bucket: str = ""):
    """``{cwd, argv, agent, label}``: how the desktop shell opens the
    bucket's active conversation in a terminal (Continue in a terminal),
    asked by the shell itself, never taken from a page: ``cwd`` the
    directory its agent works in (``_cwd``), ``argv`` the agent's command
    line (``catalog.command``) resuming the conversation's session (Codex's
    ``resume <thread>``, Claude Code's ``--resume <session>``), the session
    the conversation's agent ran in last, in that directory as it is now,
    and of the form a session has (``events.SESSION``). 404 for a
    conversation with no such session, for one whose agent runs on another
    computer, and in a vault another account made (its directory is its
    maker's to name, as the strip's ``dir`` says it); 409 while a turn of it is queued or running (two
    writers on one session are unsafe), and when the account no longer has
    the conversation's agent on here or it is not on this computer."""
    from ..local_agents import catalog, chats, store

    ws, user_id, place, _, conversation = _seat(request, bucket)
    if place is None:
        raise HTTPException(status_code=404, detail="This conversation's agent runs on another computer.")
    if conversation is None or (place["kind"] == "vault" and place["link"]["created_by"] != user_id):
        raise HTTPException(status_code=404, detail="This conversation has no session to continue.")
    name, label = conversation["agent"], catalog.label(conversation["agent"])
    if not store.owns(user_id, name):
        raise HTTPException(status_code=409, detail=f"Turn on {label} in Settings → AI first.")
    if chats.busy(conversation) is not None:
        raise HTTPException(status_code=409, detail=f"{label} is still working: stop it first.")
    cwd = _cwd(ws, place, conversation)
    session = _session_of(place, cwd, name, conversation)
    if not session:
        raise HTTPException(status_code=404, detail="This conversation has no session to continue.")
    argv = catalog.command(name)
    if argv is None:
        raise HTTPException(status_code=409, detail=f"{label} is not on this computer.")
    resume = ["resume", session] if name == "codex" else ["--resume", session]
    return {"cwd": cwd, "argv": argv + resume, "agent": name, "label": label}


def _session_of(place: dict, cwd: str, name: str | None, conversation: dict | None) -> str:
    """The session of the conversation a terminal can continue: the one the
    agent ``name`` ran it in last, in its directory ``cwd`` (``_cwd``) as
    it is now and of the place's vault ("" none), of the form a session
    has (``events.SESSION``); "" for none."""
    from ..local_agents import events, store

    ran = store.conversation_of(conversation["id"]) if conversation is not None and name and cwd else None
    link_id = place["link"]["id"] if place["link"] is not None else ""
    if (ran is None or ran["agent"] != name or ran["cwd"] != cwd or ran["link_id"] != link_id
            or not events.SESSION.match(ran["session"] or "")):
        return ""
    return ran["session"]


@router.post("/chat/sources")
def selection_sources(body: SourcesBody, request: Request):
    """``{sources: [{file, line, end?} | null]}``: the line of the work
    folder's sources each of the page's ``selections`` (``[{text, page,
    box}]``, at most ``chats.MAX_SELECTIONS``) maps to, in their order
    (``chats.sources_of``; null where nothing answers), for the composer's
    chips. Runs no process. Nulls for a bucket that is no file of a work
    folder kept here, and for an agent on another computer. 400 for more
    selections."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    ws, _, place, _, _ = _seat(request, body.bucket)
    if len(body.selections) > chats.MAX_SELECTIONS:
        raise HTTPException(status_code=400, detail=f"At most {chats.MAX_SELECTIONS} selections at a time.")
    if place is None or place["link"] is None or place["about"]["kind"] != "page":
        return {"sources": [None] * len(body.selections)}   # no sources, or on the other computer's disk
    kept = [(chats.selections_of([item]) or [None])[0] for item in body.selections]
    with connect_pages_db(ws) as conn:
        return {"sources": chats.sources_of(ws, conn, body.bucket, kept)}


@router.post("/chat/new")
def new_chat(body: BucketBody, request: Request):
    """New chat: the bucket's active conversation archived →
    ``{conversation: null}``. 409 while its turn is queued or running."""
    from ..local_agents import catalog, chats

    ws, user_id, _, far, conversation = _seat(request, body.bucket)
    label = (far["label"] if far is not None else catalog.label(conversation["agent"]) if conversation
             else "The agent")
    try:
        chats.new(ws, body.bucket, user_id, label)
    except chats.ChatError as e:
        raise _refused(e) from None
    return {"conversation": None}


@router.get("/chat/history")
def chat_history(request: Request, bucket: str = ""):
    """``{conversations: [{id, title, agent, updated_at, turns}], active}``:
    the bucket's archived conversations of the account, newest first, and
    its active one as they are listed (null for none), whichever agent it
    is of: the chat's History lists it while another agent is picked. The
    account's own, on every server and whatever became of its agents (one
    turned off, a computer gone): a session's alone."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    user_id = require_personal_user_id(request, SESSION_ONLY)
    ws = require_ws(request)
    with connect_pages_db(ws) as conn:
        conversation = chats.active(conn, bucket, user_id) if bucket else None
    return {"conversations": chats.history(ws, bucket, user_id) if bucket else [],
            "active": chats.listed(conversation)}


@router.post("/chat/history/{conversation_id}/open")
def open_conversation(conversation_id: str, request: Request):
    """An archived conversation becomes its bucket's active one, the active
    one archived → ``{conversation}``. 409 while the active one's turn is
    queued or running."""
    from ..local_agents import catalog, chats

    ws, conversation, _ = _conversation_of(request, conversation_id)
    try:
        opened = chats.open_one(ws, conversation, catalog.label(conversation["agent"]))
    except chats.ChatError as e:
        raise _refused(e) from None
    return {"conversation": chats.shown(opened)}


@router.get("/chat/history/{conversation_id}/export")
def export_conversation(conversation_id: str, request: Request):
    """A conversation of the account's, active or archived, as a file to
    keep (gamma/chat_export.py, as ``GET /api/chat-history/{id}/export``
    exports Gamma AI's), whatever became of its agent."""
    from fastapi.responses import Response

    from ..chat_export import export_file
    from ..db import connect_pages_db
    from ..local_agents import catalog
    from ..storage import attachment_disposition

    ws, conversation, _ = _conversation_of(request, conversation_id, seated=False)
    label = catalog.label(conversation["agent"])
    with connect_pages_db(ws) as conn:
        data, name, media_type = export_file(ws, conn, conversation["messages"],
                                             conversation["title"] or f"Chat with {label}", conversation["bucket"],
                                             agent=label)
    return Response(data, media_type=media_type, headers={"Content-Disposition": attachment_disposition(name)})


@router.delete("/chat/history/{conversation_id}")
def delete_conversation(conversation_id: str, request: Request):
    """An archived conversation deleted → ``{deleted: true}``, whatever
    became of its agent; 409 for the bucket's active one."""
    from ..local_agents import chats

    ws, conversation, _ = _conversation_of(request, conversation_id, seated=False)
    try:
        chats.delete(ws, conversation, here=local_agents.enabled())
    except chats.ChatError as e:
        raise _refused(e) from None
    return {"deleted": True}
