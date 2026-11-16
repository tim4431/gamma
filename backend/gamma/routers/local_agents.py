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
shell opens the conversation with in a terminal, the source lines of the
composer's selections, and the CLI's own sessions of the directory the
agent works in (``/sessions``: those started in a terminal or an editor,
History's *On this computer*, opened as a conversation whose next turn
resumes the session; gamma/local_agents/sessions.py), the agent's owner
alone. Only a server that runs agents answers
(``local_agents.enabled``) for its own vaults; any other answers 404, but
for the listing, which says it runs none (``here`` false). A
signed-in account's session only: an integration token and the guest are
refused, as on the folder-links routes. The chat answers only an account
with an agent on here, or a vault's maker on that vault's files and
folders; anyone else gets 404, so nobody else learns it exists.

Every server's chat also answers for the agents of the session's
account's computers shown to the workspace, on any bucket (step 4d, step
4.2 of docs/research/agents-as-participants-plan.md: ``chats.remote_of``,
gamma/computers/registry.py ``seats``; a request names one by its id,
``computer:<computer>:<name>``): the same routes and answers, the
conversation kept here and its turns run on that computer, which takes
them over its channel (gamma/routers/computers.py), in the vault of the
work folder the bucket is in where it keeps it, else in a directory it
offers or one of its own; a turn for a computer offline is refused (409).
What such a turn asks is answered on that computer and, where it lets this
server answer (step 4.3 of the plan), here too: kept with the turn's run
for the computer to take (``chats.answer_run``), allowed once or declined;
the terminal and the selections' source lines are the computer's own (404,
nulls). The sessions its CLI keeps there (``/sessions``) are asked of that
computer over its channel, for its own account alone
(gamma/computers/sessions.py).

A co-author (decision 13 of docs/research/agents-as-participants-plan.md):
the owner of an agent here names, with the shell's word, the people of a
workspace (of this server, or of one this computer is shown on) who may use
it (``/{agent}/people``; ``/people`` lists whom); a person named gets the
same routes for the files of a synced folder alone, in a conversation of
its own that the owner reads too (``chats.readable``), its asks allowed once
at most (runner.py ``_ChatTurn``).

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
FAR_ONCE = "A turn on another computer is allowed once at most."
NO_AGENTS = "Agents run on the desktop app's own server."
NONE_ON = "Turn on Codex or Claude Code in Settings → AI → Agents first."
LENT_FOLDER = "{agent} works for you only on the files of a synced folder."   # threads.start's ``folder``

FRESH_EVERY_S = 10   # a re-probe asked for runs at most this often on this server; else the kept answers

_fresh_lock = threading.Lock()
_fresh_at = 0.0       # when the last re-probe asked for began (monotonic)
_opening = threading.Lock()   # one session opened at a time here: two at once would both bind it


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


def _entry(found: dict, row: dict | None, user_id: str, ws: str | None) -> dict:
    """An agent as the listing says it: what the catalog found
    (``found``) with how it stands as a code (``catalog.code``), whether it
    is on, what it may run and whether the account ``user_id`` turned it on
    (``mine``: the one who may turn it off or narrow it), ``row`` its
    ``store.agents`` row; ``id`` the agent's (gamma/agents.py ``here``:
    what a comment sent to it names); for its owner the people it serves
    (``people``, ``store.people``), and for a person it serves in the
    workspace ``ws`` its owner's name (``lent``: ``{by}``, else null)."""
    from .. import agents
    from ..db import account_names, connect_users_db
    from ..local_agents import catalog, store

    on = bool(row and row["enabled"])
    mine = on and row["set_by"] == user_id
    lent = None
    if on and not mine and ws and store.named(found["name"], "", ws, user_id):
        with connect_users_db() as conn:
            lent = {"by": account_names(conn, {row["set_by"]}).get(row["set_by"], "")}
    return {**found, "id": agents.here(found["name"]).id, "code": catalog.code(found["name"], found), "enabled": on,
            "runs": row["runs"] if on else "never", "mine": mine, "lent": lent,
            "people": store.people(found["name"]) if mine else []}


def _agent_of(user_id: str, name: str, ws: str) -> dict | None:
    """The ``store.agents`` row of the agent a chat request names
    (``name``), while the account turned it on or its owner lets the
    account use it in ``ws`` (``store.usable``); with no name, the
    account's first agent on (``store.agent_for``), else the first another
    account lends it (``store.lent_to``). None for neither."""
    from ..local_agents import store

    if not name:
        return store.agent_for(user_id) or next(iter(store.lent_to(ws, user_id).values()), None)
    return store.usable(user_id, ws, name)


def _ws_of(request: Request) -> str | None:
    """The session's workspace, None where it has none."""
    try:
        return require_ws(request)
    except HTTPException:
        return None


@router.get("")
def list_agents(request: Request, fresh: int = 0):
    """``{here, computer, agents: [{id, name, label, found, version, ready,
    problem, code, models, at, enabled, runs, mine}], dirs: [{id, path,
    name, added_at}]}``: ``here`` whether this server runs agents (false
    everywhere but the desktop app's own server, which then lists none),
    ``computer`` this computer's name as its channels announce it
    (``cloud_auth.device``); ``models`` what each agent offers, ``[{id,
    label, efforts, effort, default}]``; ``enabled`` whether it is on,
    ``runs`` what it may run, ``never`` or ``ask``, ``mine`` whether the
    session's account turned it on, ``people`` the people it serves for its
    owner (``[{agent, server, workspace_id, account, username, at}]``),
    ``lent`` ``{by}`` where another account's agent serves the session's
    account in its workspace; ``dirs`` the directories offered beyond
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
    ws = _ws_of(request)
    return {"here": True, "computer": name if name.isprintable() else "",
            "agents": [_entry(agent, rows.get(agent["name"]), user_id, ws) for agent in found], "dirs": store.dirs()}


@router.patch("/{agent}")
def set_agent(agent: str, body: AgentPatch, request: Request):
    """``{enabled?, runs?}``: ``agent`` turned on or off for this computer,
    and what it may run (``never`` or ``ask``) → the agent as the listing
    says it. Turning one on takes the desktop shell's word (403
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
    force = body.enabled is False and on and owner != user_id
    if body.enabled is not None and store.set_agent(agent, body.enabled, by=user_id, force=force):
        if not body.enabled:
            runner.stop_agent(agent)
        folder_links.agents_changed()
    if body.runs is not None and store.set_runs(agent, body.runs, by=user_id) and body.runs == "never":
        runner.runs_never(agent)
    return _entry(catalog.cached(agent), store.agents().get(agent), user_id, _ws_of(request))


# --- the people an agent serves (a co-author's use) -------------------------------------------------

class PersonBody(BaseModel):
    server: str = Field(default="", max_length=400)            # "" this server, else a channel's remote_url
    workspace_id: str = Field(min_length=1, max_length=64)     # the workspace there
    account: str = Field(min_length=1, max_length=128)         # the person's account there


def _co_authors_here(user_id: str) -> list[dict]:
    """The workspaces of this server the account edits with others in them
    who edit too: ``[{server: "", workspace_id, name, people: [{id,
    username, role}]}]``, the account itself left out."""
    from .. import workspaces
    from ..db import connect_users_db

    with connect_users_db() as conn:
        rows = conn.execute(
            "SELECT w.id, w.name, u.id, u.username, m.role FROM workspace_members me "
            "JOIN workspaces w ON w.id = me.workspace_id JOIN workspace_members m ON m.workspace_id = w.id "
            "JOIN users u ON u.id = m.user_id WHERE me.user_id = ? AND me.role IN ('owner', 'editor') "
            "AND m.user_id != ? ORDER BY w.name, u.username", (user_id, user_id)).fetchall()
    places: dict[str, dict] = {}
    for ws, name, person, username, role in rows:
        if workspaces.at_least(role, "editor"):
            places.setdefault(ws, {"server": "", "workspace_id": ws, "name": name or "", "origin": "", "people": [],
                                   "error": ""})["people"].append({"id": person, "username": username, "role": role})
    return list(places.values())


def _co_authors_there(link: dict) -> dict:
    """The workspace of the account's channel ``link`` on another server,
    with the people who edit it there (``channel.members``): ``{server,
    workspace_id, name, origin, people, error}``, ``name`` the workspace's
    as that server last named it ("" before it did), ``error`` why that server
    did not answer ("" when it did)."""
    from .. import gamma_sync, remote_servers
    from ..computers import channel

    place = {"server": link["remote_url"], "workspace_id": link["workspace_id"], "name": link["workspace_name"],
             "origin": remote_servers.origin_of(link["remote_url"]), "people": [], "error": ""}
    try:
        place["people"] = channel.members(link)
    except channel.Older:
        place["error"] = "That server is older than this app: it cannot name its people."
    except gamma_sync.SyncError as e:
        place["error"] = f"That server could not be reached: {e}"
    return place


@router.get("/people")
def people_offered(request: Request):
    """``{places: [{server, workspace_id, name, origin, people: [{id,
    username, role}], error}]}``: whom the session's account may let use an
    agent it turned on here, by workspace: those of this server it edits
    with others (``server`` ""), then the workspace of each of its channels
    to another server, asked there (``origin`` that server's, ``error`` why
    it did not answer); the editors and owners of each but the account. 404
    on a server that runs no agents."""
    from ..computers import channel

    user_id = _agents_account(request)
    return {"places": _co_authors_here(user_id) + [_co_authors_there(link) for link in channel.links(user_id)]}


def _owned(user_id: str, agent: str) -> str:
    """``agent``'s label while the account owns it here; 404 for an agent
    Gamma does not know, 403 for one it does not own."""
    from ..local_agents import catalog, store

    if agent not in catalog.AGENTS:
        raise HTTPException(status_code=404, detail=f"Gamma knows no agent named {agent}.")
    label = catalog.label(agent)
    if not store.owns(user_id, agent):
        raise HTTPException(status_code=403, detail=f"Only the account that turned on {label} may say who uses it.")
    return label


@router.post("/{agent}/people")
def name_person(agent: str, body: PersonBody, request: Request):
    """``{server, workspace_id, account}``: a person of that workspace (of
    this server, ``server`` "", or of a server one of the account's channels
    names) may use ``agent`` from now on → ``{people}``
    (``store.people``). Takes the desktop shell's word (403), whose question
    names the person and what the owner answers for (desktop/lib/keeping.js
    ``personAsk``), and the agent's owner (403); 404 on a server that runs
    no agents. 400 for a person who is not an editor or owner of that
    workspace beside the account, or past ``store.MAX_PEOPLE``; 404 for a
    server no channel of the account names; 502 for one that does not
    answer. The channels announce it, and the open pages hear the agent
    changed."""
    from .. import folder_links, workspaces
    from ..computers import channel
    from ..db import account_names, connect_users_db
    from ..local_agents import store

    _agents_account(request)
    user_id = from_shell(request, "Only the desktop app may let another person use an agent on this computer.")
    label = _owned(user_id, agent)
    if body.account == user_id and not body.server:
        raise HTTPException(status_code=400, detail=f"{label} is yours already.")
    if not body.server:
        if not (workspaces.at_least(workspaces.role_of(body.workspace_id, user_id), "editor")
                and workspaces.at_least(workspaces.role_of(body.workspace_id, body.account), "editor")):
            raise HTTPException(status_code=400, detail="Only an editor or an owner of a workspace you edit can use "
                                                        f"{label}.")
        with connect_users_db() as conn:
            username = account_names(conn, {body.account}).get(body.account, "")
    else:
        link = next((link for link in channel.links(user_id) if (link["remote_url"], link["workspace_id"])
                     == (body.server, body.workspace_id)), None)
        if link is None:
            raise HTTPException(status_code=404, detail="This computer is not shown to that workspace.")
        place = _co_authors_there(link)
        if place["error"]:
            raise HTTPException(status_code=502, detail=place["error"])
        person = next((p for p in place["people"] if p["id"] == body.account), None)
        if person is None:
            raise HTTPException(status_code=400, detail=f"Only an editor or an owner of that workspace can use {label}.")
        username = person["username"]
    try:
        if store.name_person(agent, body.server, body.workspace_id, body.account, username):
            folder_links.agents_changed()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"people": store.people(agent)}


@router.delete("/{agent}/people")
def unname_person(agent: str, request: Request, workspace_id: str, account: str, server: str = ""):
    """A person may no longer use ``agent`` → ``{people}``: their turns of
    it running here stop and those waiting fail when taken
    (``runner.stop_person``), and the channels announce it, so another
    server fails the ones it holds for it. Takes the agent's owner, or the
    desktop shell's word (``shell_strict``: the person at the computer
    narrows an agent whose owner is signed out); 404 for an agent Gamma
    does not know or that is off."""
    from .. import folder_links
    from ..local_agents import catalog, runner, store

    user_id = _agents_account(request)
    if agent not in catalog.AGENTS or agent not in store.enabled_agents():
        raise HTTPException(status_code=404, detail=f"No agent named {agent} is on here.")
    if not store.owns(user_id, agent):
        shell_strict(request, f"Only the account that turned on {catalog.label(agent)}, or the desktop app, may say "
                              "who uses it.")
    if store.unname_person(agent, server, workspace_id, account):
        runner.stop_person(agent, server, workspace_id, account)
        folder_links.agents_changed()
    return {"people": store.people(agent)}


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
    (``runner.stop_dirs``), and the channels announce the list again
    (gamma/computers/channel.py ``state_of``'s ``dirs``)."""
    from .. import folder_links
    from ..computers import channel
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
    channel.nudge()   # the other servers hear the directories offered by id and name
    return {"dirs": kept}


# --- the chat's computer side ------------------------------------------------------------------

class TurnBody(BaseModel):
    bucket: str
    text: str = ""
    selections: list | None = None
    id: str = ""
    # the agent picked for the conversation: this computer's by its name (``codex``, ``claude``), another
    # computer's by its id (``computer:<computer>:<name>``, gamma/agents.py); "" for the account's first
    agent: str = Field(default="", max_length=160)
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


def _named(agent: str) -> tuple[str, str]:
    """``(name, computer)`` of the agent a chat request names: this
    computer's by its name (``codex``) or by its id, another computer's by
    its id (``computer:<computer>:<name>``, gamma/agents.py), ``computer``
    "" for this computer's own."""
    from .. import agents

    if agents.kind_of(agent) != "computer":
        return agent, ""
    computer, name = agents.parts(agent)
    return name, "" if computer == agents.here_id() else computer


def _seat(request: Request, bucket: str, dir: str | None = None, agent: str = "", thread: str = "",
          lent: str = "") -> tuple[str, str, dict | None, dict | None, dict | None]:
    """``(ws, the account, the place, the far seat, the active conversation)``
    of the bucket for the session's account, one of the place and the far
    seat None. The agent is the one ``agent`` names (the one picked in the
    chat, ``_named``), else the active conversation's while its computer
    still offers it: another computer's is its far seat
    (``chats.remote_of``, that computer's agent there); this computer's
    works where ``chats.place_of`` says (on a server that runs agents, for
    the active conversation's ``dir``, or the ``dir`` a turn names). With no
    agent named or kept, a work folder a vault here keeps
    has this computer's agent, else a computer that keeps the bucket's work
    folder has its (the first far seat's), else this computer's, else the
    first far seat's. With
    ``thread`` the conversation is the account's of that comment thread on
    the bucket, else a co-author's there with the account's agent
    (``chats.of_thread`` ``owner``), and with ``lent`` the chat conversation
    of that id of a co-author the account's agent serves on the bucket
    (``chats.lent_one``), in place of the active one. With ``agent``, a
    conversation of another agent or of another computer counts as none: a
    conversation keeps the agent it began with, and the next turn starts
    another (``chats.begin``). 404 for any other bucket or account; 403 for
    a token or the guest."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    user_id = require_personal_user_id(request, SESSION_ONLY)
    ws = require_ws(request)
    with connect_pages_db(ws) as conn:
        if thread or lent:
            conversation = (chats.of_thread(conn, thread, user_id, owner=True) if thread
                            else chats.lent_one(conn, lent, user_id))
            conversation = conversation if conversation is not None and conversation["bucket"] == bucket else None
        else:
            conversation = chats.active(conn, bucket, user_id) if bucket else None
    name, computer = _named(agent)
    if agent and conversation is not None and (conversation["agent"], conversation["computer"]) != (name, computer):
        conversation = None
    if computer:
        far = chats.remote_of(ws, bucket, user_id, computer, name)
        if far is None:
            raise HTTPException(status_code=404, detail=NOT_HERE)
        return ws, user_id, None, far, conversation
    if not agent and conversation is not None and conversation["computer"]:
        far = chats.remote_of(ws, bucket, user_id, conversation["computer"], conversation["agent"])
        if far is not None:
            return ws, user_id, None, far, conversation
    chosen = dir if dir is not None else (conversation or {}).get("dir", "")
    place = chats.place_of(ws, bucket, user_id, chosen) if local_agents.enabled() else None
    far = chats.remote_of(ws, bucket, user_id) if not name and (place is None or place["link"] is None) else None
    if far is not None and (far["folder_id"] or place is None):
        place = None
    else:
        far = None
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


def _conversation_of(request: Request, conversation_id: str, *, seated: bool = True,
                     owner: bool = False) -> tuple[str, dict, dict | None]:
    """``(ws, the conversation, the far seat)`` for its account, or with
    ``owner`` for its agent's owner too (``chats.readable``: to read, stop
    and answer a co-author's), with ``seated`` while its bucket is one
    ``_seat`` answers it (to talk in it again); without, whatever became of
    its agents (to keep or delete it), the far seat None. 404 otherwise."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    user_id = require_personal_user_id(request, SESSION_ONLY)
    ws = require_ws(request)
    with connect_pages_db(ws) as conn:
        conversation = chats.get(conn, conversation_id)
    if conversation is None or not (conversation["user_id"] == user_id
                                    or owner and chats.readable(conversation, user_id)):
        raise HTTPException(status_code=404, detail="No conversation has that id.")
    if not seated:
        return ws, conversation, None
    _, _, _, far, _ = _seat(request, conversation["bucket"], conversation["dir"])
    return ws, conversation, far


def _far_turn(request: Request, turn_id: str) -> tuple[str, dict, dict]:
    """``(ws, the conversation, the turn's row)`` of a turn that runs on
    another computer (``chats.run_of``), for the account whose turn and
    conversation it is, or its agent's owner (``chats.readable``), whatever
    became of the page's seat since (the agent turned off, the computer
    gone, the page deleted): such a turn stays stoppable. 404 otherwise."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    user_id = require_personal_user_id(request, SESSION_ONLY)
    ws = require_ws(request)
    with connect_pages_db(ws) as conn:
        row = chats.run_of(conn, turn_id)
        conversation = chats.get(conn, row["conversation_id"]) if row is not None else None
    if row is None or conversation is None or row["user_id"] != conversation["user_id"] or not chats.readable(
            conversation, user_id):
        raise HTTPException(status_code=404, detail=chats.NO_TURN)
    return ws, conversation, row


def _local_turn(turn_id: str) -> dict | None:
    """The turn this computer recorded (``store.get_turn``), on a server
    that runs agents; None otherwise."""
    from ..local_agents import store

    return store.get_turn(turn_id) if local_agents.enabled() else None


def _claimed_here(request: Request, turn_id: str) -> bool:
    """Whether the turn is one of another server that this computer claimed
    over a channel the account made (``store.claimed_of``): what it asks is
    answered in the desktop app's window (``/asks``), never on this
    server's chat route."""
    from ..computers import channel
    from ..local_agents import store

    row = store.claimed_of(turn_id) if local_agents.enabled() else None
    if row is None:
        return False
    link = channel.get(row["link_id"])
    return link is not None and link["created_by"] == require_personal_user_id(request, SESSION_ONLY)


def _refused(e) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail)


def _computer(ws: str, user_id: str, place: dict, conversation: dict | None, agent: str = "") -> dict:
    """What the strip says: ``{agent, label, ready, problem, models, folder,
    paused, turn, session, folder_id, dir, grants, lent}``, ``agent`` the one
    picked (``agent``) or the account's first here (``_agent_of``; null for
    none), ``lent`` ``{by}`` where it is another account's that serves this
    one (a co-author's: no terminal, no grants, each ask allowed once at
    most), else null,
    ``ready``, ``problem`` and ``models`` (what it offers, ``[{id, label,
    efforts, effort, default}]``) as last asked (``catalog.cached``: nothing
    runs; ``ready`` null while it is asked), ``folder`` the work folder's
    name ("" outside one kept here), ``paused`` whether its vault is paused
    or choosing its files, ``turn`` where the conversation's turn stands in
    the runner (``runner.chat_state``) or null, ``session`` whether a
    terminal can continue the conversation (``_session_of``; the id itself
    goes only to the shell, ``terminal``; never in a vault another account
    made, nor for a co-author's conversation), ``folder_id`` the work folder's
    (W, what the changed files' paths are relative to; null outside one),
    ``dir`` where the agent works (``{kind: "vault", name, path}``, the path
    for the vault's maker alone, ``{kind: "offered", id, name}``, or null
    for a directory of its own), ``grants`` the commands allowed in the
    conversation in that directory (``store.grants_of``: ``[{command, cwd,
    host}]``)."""
    from .. import folder_links
    from ..db import account_names, connect_users_db
    from ..local_agents import catalog, chats, runner, store

    row = _agent_of(user_id, agent, ws)
    name = row["agent"] if row else None
    found = catalog.cached(name) if name else {"ready": False, "problem": "", "models": []}
    link = place["link"]
    reply = chats.busy(conversation)
    cwd = _cwd(ws, place, conversation)
    lent = None
    if row is not None and row["set_by"] != user_id:
        with connect_users_db() as conn:
            lent = {"by": account_names(conn, {row["set_by"]}).get(row["set_by"], "")}
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
            "session": lent is None and not chats.lent(conversation) and (
                link is None or link["created_by"] == user_id) and bool(_session_of(place, cwd, name, conversation)),
            "folder_id": place["dir"] if link is not None else None, "dir": shown,
            "grants": store.grants_of(conversation["id"], dir=cwd) if cwd and lent is None else [], "lent": lent}


def _far_computer(ws: str, far: dict, conversation: dict | None) -> dict:
    """What the strip says for a conversation whose agent runs on another
    computer (``far``, ``chats.remote_of``): ``_computer``'s fields as that
    computer last announced them (``ready``, the ``models`` it offers;
    ``problem`` while it is online and its agent is not ready, in the words
    the @ menu says it), ``turn`` where the conversation's turn stands here
    (``{turn_id, state, ahead: false}``, ``computer_chat_runs``), no
    ``session``, no ``grants`` (what it asks is allowed once at most),
    ``folder_id`` the work
    folder's where that computer keeps the one the bucket is in (null
    otherwise), ``dir`` where its agent works (that work folder by its
    name, an offered directory by its id and name, null for one of its
    own), ``dirs`` the directories it offers (``[{id, name}]``, which a new
    conversation picks among), ``lent`` ``{by}`` for a co-author's (the
    agent's owner by name), else null, and ``computer`` its name,
    ``online``, ``since`` when it went offline (null while online),
    ``remote: true``."""
    from ..computers import registry
    from ..db import connect_pages_db
    from ..local_agents import chats

    assistants, _seen = registry.said_of(far["token_id"])
    said = next((a for a in assistants if isinstance(a, dict) and a.get("name") == far["agent"]), {})
    reply = chats.busy(conversation)
    with connect_pages_db(ws) as conn:
        named = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (far["folder_id"],)).fetchone() \
            if far["folder_id"] else None
        row = chats.run_of(conn, reply["turn_id"]) if reply else None
    folder = (named[0] if named else "") or ""
    kept = (conversation or {}).get("dir") or ""
    offered = next((d for d in far["dirs"] if d.get("id") == kept), None) if not far["folder_id"] else None
    shown = ({"kind": "vault", "name": folder} if far["folder_id"]
             else {"kind": "offered", "id": kept, "name": offered["name"]} if offered else None)
    return {"agent": far["agent"], "label": far["label"], "ready": far["ready"],
            "problem": chats.far_problem(far) if far["online"] else "", "models": said.get("models") or [],
            "folder": folder, "paused": far["paused"],
            "turn": {"turn_id": row["turn_id"], "state": row["state"], "ahead": False}
            if row and row["state"] in chats.BUSY else None,
            "session": False, "folder_id": far["folder_id"] or None, "dir": shown, "dirs": far["dirs"],
            "grants": [], "lent": {"by": far["lent_by"]} if far.get("lent_by") else None,
            "computer": far["computer"], "online": far["online"], "since": far["since"], "remote": True}


@router.get("/chat")
def read_chat(request: Request, bucket: str = "", version: str = "", agent: str = "", thread: str = "",
              conversation: str = ""):
    """``{computer, conversation}``: the strip (``_computer``, or
    ``_far_computer`` for an agent on another computer) and the bucket's
    active conversation of the account (``{id, title, agent, thread_id,
    messages, version}``, null for none, and for one of another agent or
    another computer than the one picked, ``agent``: ``_seat``), or with
    ``thread`` the account's
    conversation of that comment thread on the bucket (the chat's *Open in
    chat*), or with ``conversation`` a co-author's chat conversation with
    the account's agent (History, Background tasks), its turns answered and
    stopped as a chat's; a co-author's carries ``person``, the co-author's
    username. With ``version`` the one stored, ``{computer, same: true}``,
    so a poll reads little."""
    from ..db import account_names, connect_users_db
    from ..local_agents import chats

    ws, user_id, place, far, found = _seat(request, bucket, agent=agent, thread=thread, lent=conversation)
    computer = (_computer(ws, user_id, place, found, _named(agent)[0]) if place is not None
                else _far_computer(ws, far, found))
    if version and found is not None and str(found["version"]) == version:
        return {"computer": computer, "same": True}
    shown = chats.shown(found)
    if found is not None and found["user_id"] != user_id:
        with connect_users_db() as conn:
            shown["person"] = account_names(conn, {found["user_id"]}).get(found["user_id"], "")
    return {"computer": computer, "conversation": shown}


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
    one picked (one another account lends it counts: ``_agent_of``), one
    lent on a page of no synced folder, the folder is paused or chooses its
    files, or the agent is not ready (its problem); 403 when the account is no editor or the
    server or the workspace takes no writes. For an agent on another
    computer (``agent`` its id, or with none the bucket's first far seat:
    ``_seat``, ``_far_turn_started``) the turn waits here for it to take,
    ``dir`` one of the directories it offers."""
    from .. import folder_links
    from ..local_agents import catalog, chats, runner

    ws, user_id, place, far, conversation = _seat(request, body.bucket, body.dir, body.agent)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Write a message first.")
    if len(text) > chats.MAX_TEXT:
        raise HTTPException(status_code=400, detail=f"A message is at most {chats.MAX_TEXT} characters.")
    if body.id and not chats.MESSAGE_ID.match(body.id):
        raise HTTPException(status_code=400, detail="That message id is not one Gamma makes.")
    if far is not None:
        return _far_turn_started(ws, user_id, far, body, text, conversation)
    if body.dir and place["kind"] == "own":
        raise HTTPException(status_code=400, detail="No directory offered on this computer has that id.")
    named = _named(body.agent)[0]
    row = _agent_of(user_id, named, ws)
    if row is None:
        raise HTTPException(status_code=409, detail=f"Turn on {catalog.label(named)} in Settings → AI → Agents first."
                            if named in catalog.AGENTS else NONE_ON)
    name, label = row["agent"], catalog.label(row["agent"])
    if row["set_by"] != user_id and place["kind"] != "vault":
        raise HTTPException(status_code=409, detail=LENT_FOLDER.format(agent=label))
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
                                               message_id=body.id, model=model, effort=effort, owner=row["set_by"])
    except chats.ChatError as e:
        raise _refused(e) from None
    if new:
        runner.queue_chat(ws, place, reply["turn_id"], conversation_id=conversation["id"], bucket=body.bucket,
                          agent=name, user_id=user_id, owner=row["set_by"], model=model, effort=effort)
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


def _far_turn_started(ws: str, user_id: str, far: dict, body: TurnBody, text: str, conversation: dict | None) -> dict:
    """``start_turn`` for an agent on another computer (``far``): refused
    while that computer is offline (no turn is queued for one away), and
    the same checks against what it last announced (the work folder paused
    there; its agent known not ready; the model and the effort among the
    ones it offers; the directory among those it offers, ``far_dir``), the
    turn written here to wait for it (``chats.begin`` ``remote``, the
    conversation with that computer's agent) and the computer woken
    (``chat``): it takes the turn over its channel."""
    from ..computers import registry
    from ..local_agents import catalog, chats

    name, label, computer = far["agent"], far["label"], far["computer"]
    if not far["online"]:
        raise HTTPException(status_code=409, detail=chats.OFFLINE.format(computer=computer))
    if far["paused"]:
        raise HTTPException(status_code=409,
                            detail=f"The synced folder is paused on {computer}: {label} starts once it syncs again.")
    if far["ready"] is False:
        raise HTTPException(status_code=409, detail=chats.far_problem(far))
    assistants, _seen = registry.said_of(far["token_id"])
    said = next((a for a in assistants if isinstance(a, dict) and a.get("name") == name), {})
    model, effort = body.model.strip(), body.effort.strip()
    refusal = catalog.refused_choice(name, said.get("models") or [], model, effort)
    if refusal:
        raise HTTPException(status_code=400, detail=refusal.replace("on this computer", f"on {computer}"))
    _may_write(ws, user_id, label)
    try:
        dir = chats.far_dir(far, body.dir, conversation)
        conversation, reply, new = chats.begin(ws, bucket=body.bucket, dir=dir, user_id=user_id, link_id="",
                                               agent=name, label=label, text=text,
                                               selections=chats.selections_of(body.selections), message_id=body.id,
                                               model=model, effort=effort, remote=True, computer=computer,
                                               computer_id=far["computer_id"], owner=far["user_id"])
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
    (``_far_turn``), and its agent's owner's (a co-author's turn). 404 for
    a turn of no conversation the account reads."""
    from ..computers import registry
    from ..local_agents import chats, runner

    turn = _local_turn(turn_id)
    if turn is None:
        ws, _, row = _far_turn(request, turn_id)
        stopped = chats.stop_run(ws, turn_id, online=registry.is_online)
        if stopped and row["holder"]:
            registry.wake(row["holder"], "chat")
        return {"stopped": stopped}
    ws, conversation, _ = _conversation_of(request, turn["conversation_id"], owner=True)
    if turn["ws"] != ws:
        raise HTTPException(status_code=404, detail="No turn has that id.")
    return {"stopped": runner.stop_chat(ws, turn_id, conversation["id"])}


@router.post("/chat/turns/{turn_id}/asks/{ask_id}")
def answer_ask(turn_id: str, ask_id: str, body: AnswerBody, request: Request):
    """``{decision: once | chat | deny, note?}``: the person's answer to
    what the turn's agent asks (an agent message's ``asks``) → ``{answered:
    true}``; ``chat`` allows that command again in this conversation,
    ``note`` (at most ``MAX_NOTE`` characters) is what a decline tells the
    agent (``runner.answer_chat``); a co-author's turn is answered by that
    person or the agent's owner, ``once`` or ``deny`` alone. A turn that
    runs on another computer that relays its asks (its message's ``asks``)
    is answered the same way, ``once`` or ``deny`` alone, the answer kept
    with its run and the computer woken to take it (``chats.answer_run``);
    the first answer, here or on that computer, is the one. 404 for a turn
    of no conversation the account reads or no such ask; 409 for one
    answered already or no longer waited for, and a turn that ended, and,
    on this computer, for a turn of another server's it runs (``FAR_ASKS``:
    answered in the desktop app's window and on that server); 400 for
    another decision, too long a note, or ``chat`` on what is not a command,
    in a co-author's turn or in one on another computer."""
    from ..computers import registry
    from ..local_agents import chats, events, runner

    def noted() -> str:
        if body.decision not in events.DECISIONS:
            raise HTTPException(status_code=400, detail="Answer once, chat or deny.")
        note = body.note.strip()
        if len(note) > MAX_NOTE:
            raise HTTPException(status_code=400, detail=f"A note is at most {MAX_NOTE} characters.")
        return note

    turn = _local_turn(turn_id)
    if turn is None:
        try:
            ws, conversation, _ = _far_turn(request, turn_id)
        except HTTPException:
            if _claimed_here(request, turn_id):
                raise HTTPException(status_code=409, detail=FAR_ASKS) from None
            raise
        note = noted()
        if body.decision == "chat":
            raise HTTPException(status_code=400, detail=runner.ONCE_ONLY if chats.lent(conversation) else FAR_ONCE)
        try:
            holder = chats.answer_run(ws, turn_id, ask_id, body.decision, note,
                                      require_personal_user_id(request, SESSION_ONLY))
        except chats.ChatError as e:
            raise _refused(e) from None
        registry.wake(holder, "chat")
        return {"answered": True}
    ws, _, _ = _conversation_of(request, turn["conversation_id"], owner=True)
    if turn["ws"] != ws:
        raise HTTPException(status_code=404, detail="No turn has that id.")
    note = noted()
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
        raise HTTPException(status_code=409, detail=f"Turn on {label} in Settings → AI → Agents first.")
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


# --- the CLI's own sessions --------------------------------------------------------------------

class SessionBody(BaseModel):
    bucket: str
    agent: str = Field(max_length=160)   # as a turn names it (``TurnBody.agent``)
    dir: str = Field(max_length=64)
    session: str = Field(max_length=128)


OLDER = "{computer} needs a newer Gamma to open its sessions here."
UNANSWERED = "{computer} did not answer: try again."


def _session_place(request: Request, agent: str, dir: str) -> tuple[str, str, dict]:
    """``(ws, the account, the place)`` of the CLI's sessions the request
    names: ``agent`` (``codex``, ``claude``) the account turned on here, the
    directory ``dir`` as the chat's picker names it (``chats.place_named``:
    a work folder whose vault the account made, an offered directory). 404
    for any other agent, account (a co-author, another account) or
    directory; 403 for a token or the guest; 404 on a server that runs no
    agents."""
    from ..local_agents import catalog, chats, store

    user_id = _agents_account(request)
    ws = require_ws(request)
    place = chats.place_named(ws, user_id, dir) if agent in catalog.AGENTS and store.owns(user_id, agent) else None
    if place is None:
        raise HTTPException(status_code=404, detail=NOT_HERE)
    return ws, user_id, place


def _far_session_place(request: Request, agent: str, computer_id: str, dir: str) -> tuple[str, str, dict, dict]:
    """``(ws, the account, the computer, the directory)`` of the sessions
    the request names of the agent ``agent`` of another computer of the
    account's (``registry.own_computer``: its own, never one another
    account lends it), the directory ``dir`` by its id as a far
    conversation keeps it (``chats.far_dir``): a work folder that computer
    keeps (``{kind: "vault", id}``) or a directory it offers (``{kind:
    "offered", id}``). 404 for any other agent, computer or directory (a
    path among them); 403 for a token or the guest."""
    from ..computers import registry

    user_id = require_personal_user_id(request, SESSION_ONLY)
    ws = require_ws(request)
    found = registry.own_computer(user_id, ws, computer_id) if agent in registry.AGENTS else None
    where = None if found is None or agent not in found["agents"] else (
        {"kind": "vault", "id": dir} if dir in found["folders"] else
        {"kind": "offered", "id": dir} if dir in found["dirs"] else None)
    if where is None:
        raise HTTPException(status_code=404, detail=NOT_HERE)
    return ws, user_id, found, where


@router.get("/sessions")
async def agent_sessions(request: Request, agent: str = "", dir: str = ""):
    """``{sessions: [{id, title, at, created, source, ours, busy}]}``: the
    sessions the CLI of ``agent`` keeps whose directory is the one ``dir``
    names (``_session_place``), the most recently used first
    (``sessions.listed``, ``shown``: ``source`` what each was started from,
    ``ours`` whether a conversation of Gamma's continues it, ``busy``
    whether another process has it open). 409 when Codex is not here or
    cannot list them. For an agent of another computer of the account's
    (``agent`` its id, ``_far_session_place``), asked of that computer over
    its channel (gamma/computers/sessions.py ``listing``), ``ours`` for a
    conversation of the account's here with that agent, and ``away`` with
    no session while it cannot answer: ``offline``, ``older`` (a release
    before this), ``waiting`` (no answer yet: History opened again reads it
    once it came); a refusal there is its status and its words."""
    from ..local_agents import chats, sessions

    name, computer = _named(agent)
    if not computer:
        return await asyncio.to_thread(_local_sessions, request, name, dir)
    from ..computers import registry, sessions as asked

    ws, user_id, found, where = await asyncio.to_thread(_far_session_place, request, name, computer, dir)
    if not found["online"]:
        return {"sessions": [], "away": "offline"}
    if not registry.answers(found["token_id"]):
        return {"sessions": [], "away": "older"}
    answer = await asked.listing(found["token_id"], name, where)
    if answer is None:
        return {"sessions": [], "away": "waiting"}
    if "error" in answer:
        raise HTTPException(status_code=answer["status"], detail=answer["error"])
    rows = answer["sessions"]
    titles = await asyncio.to_thread(chats.titles, ws, [row["conversation"] for row in rows if row["conversation"]],
                                     user_id=user_id, computer=computer, agent=name)
    return {"sessions": sessions.shown(rows, titles)}


def _local_sessions(request: Request, agent: str, dir: str) -> dict:
    """``agent_sessions`` for this computer's agent: ``ours`` for a session
    a conversation of this workspace continues, titled as it is there; one
    bound to a conversation the workspace does not have (gone, another
    workspace's, another server's over the channel) is listed as any other,
    to be opened (``_local_open``)."""
    from ..local_agents import chats, sessions

    ws, _, place = _session_place(request, agent, dir)
    try:
        rows = sessions.listed(agent, place["path"])
    except chats.ChatError as e:
        raise _refused(e) from None
    return {"sessions": sessions.shown(rows, chats.titles(ws, [row["conversation"] for row in rows
                                                               if row["conversation"]]))}


@router.post("/sessions/open")
async def open_session(body: SessionBody, request: Request):
    """A session of the CLI's (``agent``, ``dir``: ``_session_place``)
    opened in the chat of ``bucket``, whose agent works in that directory →
    ``{conversation}``: a new conversation of the bucket, its active one
    archived, holding the session's turns (the person's text, the agent's
    text and its steps; ``sessions.read``, ``messages``) and bound to it, so
    its next turn resumes it (``chats.adopt``). A session a conversation of
    the account's in this bucket continues already is that conversation,
    made the bucket's active one (``chats.open_one``); one bound to a
    conversation the workspace does not have is opened as any other, that
    conversation letting go of it (``store.unbound``). 400 for a bucket
    whose agent works elsewhere; 404 for no such session of that directory;
    409 while another process holds it (``sessions.held``: why), while a
    conversation elsewhere in the workspace continues it, while the bucket's active
    conversation's turn is queued or running, and when Codex is not here
    or cannot read it; 403 when the account is no editor or the workspace
    takes no writes. For an agent of another computer of the account's
    (``_far_open``) the same, that computer reading the session and binding
    it to the conversation made here; 409 too while that computer is
    offline or older (``OLDER``), or when it does not answer in time
    (``UNANSWERED``)."""
    name, computer = _named(body.agent)
    if computer:
        return await _far_open(request, body, name, computer)
    return await asyncio.to_thread(_local_open, request, body, name)


def _local_open(request: Request, body: SessionBody, agent: str) -> dict:
    """``open_session`` for this computer's agent."""
    from ..local_agents import catalog, chats, events, sessions, store

    ws, user_id, place = _session_place(request, agent, body.dir)
    seat = chats.place_of(ws, body.bucket, user_id, body.dir)
    if seat is None or (seat["kind"], seat["dir"]) != (place["kind"], place["dir"]):
        raise HTTPException(status_code=400, detail="This chat's agent works in another directory.")
    if not events.SESSION.match(body.session):
        raise HTTPException(status_code=404, detail=sessions.NO_SESSION)
    label = catalog.label(agent)
    _may_write(ws, user_id, label)
    with _opening:
        bound = store.bound(agent, [body.session]).get(body.session)
        here = bool(bound) and bool(chats.titles(ws, [bound]))
        try:
            if here:
                return {"conversation": chats.shown(_continued(ws, user_id, body.bucket, bound, label))}
            held = sessions.held(agent, body.session)
            if held:
                raise HTTPException(status_code=409, detail=held)
            read = sessions.read(agent, place["path"], body.session)
            conversation = chats.adopt(ws, bucket=body.bucket, dir=place["dir"], user_id=user_id, agent=agent,
                                       label=label, title=read["title"],
                                       messages=sessions.messages(agent, read["turns"], place["path"]))
        except chats.ChatError as e:
            raise _refused(e) from None
        if bound:   # a conversation this workspace does not have lets go of it
            store.unbound(agent, body.session)
        link = place["link"]
        store.ran_conversation(conversation["id"], link_id=link["id"] if link is not None else "", agent=agent,
                               session=body.session, cwd=place["path"])
    return {"conversation": chats.shown(conversation)}


async def _far_open(request: Request, body: SessionBody, agent: str, computer_id: str) -> dict:
    """``open_session`` for the agent of another computer of the account's
    (``_far_session_place``), on a bucket whose far seat is that computer's
    agent working in that directory (``_far_open_seat``): that computer
    asked to open it for a conversation of a new id
    (gamma/computers/sessions.py ``opening``), which it binds to the
    session there before it answers with its turns, and the conversation
    made here with them (``chats.adopt``, of that computer). A session it
    names as a conversation's already is that conversation where this
    server has it (``_far_continued``: the account's here with that agent,
    else 409); one this server does not have (one of that computer's own
    server, or one gone here) is opened again, bound to the new one in its
    place (``rebind``)."""
    from ..blocks_store import new_block_id
    from ..computers import registry, sessions as asked
    from ..local_agents import catalog, chats, events, sessions

    ws, user_id, found, where = await asyncio.to_thread(_far_session_place, request, agent, computer_id, body.dir)
    label, computer = catalog.label(agent), found["name"]
    await asyncio.to_thread(_far_open_seat, ws, user_id, body.bucket, agent, computer_id, where, label)
    if not events.SESSION.match(body.session):
        raise HTTPException(status_code=404, detail=sessions.NO_SESSION)
    if not found["online"]:
        raise HTTPException(status_code=409, detail=chats.OFFLINE.format(computer=computer))
    if not registry.answers(found["token_id"]):
        raise HTTPException(status_code=409, detail=OLDER.format(computer=computer))
    conversation_id, rebind = new_block_id(), ""
    while True:
        answer = await asked.opening(found["token_id"], agent, where, body.session, conversation_id, rebind)
        if answer is None:
            raise HTTPException(status_code=409, detail=UNANSWERED.format(computer=computer))
        if "error" in answer:
            raise HTTPException(status_code=answer["status"], detail=answer["error"])
        if answer.get("busy"):
            raise HTTPException(status_code=409, detail=events.BUSY.format(agent=label))
        if "conversation" not in answer:
            break
        continued = await asyncio.to_thread(_far_continued, ws, user_id, body.bucket, answer["conversation"],
                                            computer_id, agent, label)
        if continued is not None:
            return {"conversation": chats.shown(continued)}
        if rebind:   # named again once rebound: never asked a third time
            raise HTTPException(status_code=409, detail="Another chat of Gamma's continues this session.")
        rebind = answer["conversation"]
    try:
        conversation = await asyncio.to_thread(
            chats.adopt, ws, bucket=body.bucket, dir=where["id"], user_id=user_id, agent=agent, label=label,
            title=answer["title"], messages=answer["messages"], conversation_id=conversation_id, computer=computer_id)
    except chats.ChatError as e:
        raise _refused(e) from None
    return {"conversation": chats.shown(conversation)}


def _far_open_seat(ws: str, user_id: str, bucket: str, agent: str, computer_id: str, where: dict, label: str) -> None:
    """404 for a bucket whose chat has no seat of that computer's agent
    (``chats.remote_of``); 400 for one whose far conversation works
    elsewhere than ``where`` (``chats.far_dir``); ``_may_write``'s 403;
    ``chats.adopt``'s 409 while the bucket's active conversation's turn is
    queued or running, before that computer is asked (it binds the session
    before it answers)."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    seat = chats.remote_of(ws, bucket, user_id, computer_id, agent)
    if seat is None:
        raise HTTPException(status_code=404, detail=NOT_HERE)
    try:
        dir = chats.far_dir(seat, where["id"], None)
    except chats.ChatError:
        dir = None
    if dir != where["id"]:
        raise HTTPException(status_code=400, detail="This chat's agent works in another directory.")
    _may_write(ws, user_id, label)
    with connect_pages_db(ws) as conn:
        working = chats.busy(chats.active(conn, bucket, user_id))
    if working is not None:
        raise HTTPException(status_code=409, detail=f"{label} is still working: stop it first.")


def _far_continued(ws: str, user_id: str, bucket: str, conversation_id: str, computer_id: str, agent: str,
                   label: str) -> dict | None:
    """``_continued`` for a conversation another computer names as its
    session's, where this server has it: 409 for one of another computer
    or agent, and as ``_continued`` refuses (another account's, a co-author's
    among them; a thread's; another bucket's); None where this server has
    no such conversation."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    with connect_pages_db(ws) as conn:
        found = chats.get(conn, conversation_id)
    if found is None:
        return None
    if (found["computer"], found["agent"]) != (computer_id, agent):
        raise HTTPException(status_code=409, detail="Another chat of Gamma's continues this session.")
    return _continued(ws, user_id, bucket, conversation_id, label)


def _continued(ws: str, user_id: str, bucket: str, conversation_id: str, label: str) -> dict:
    """The conversation ``conversation_id`` that continues a session
    already, opened in the chat of ``bucket``: the bucket's active one, an
    archived one made so (``chats.open_one``). 409 for one elsewhere (of
    another bucket or account, a comment thread's, none), which says
    where."""
    from ..db import connect_pages_db
    from ..local_agents import chats

    with connect_pages_db(ws) as conn:
        found = chats.get(conn, conversation_id)
        about = chats.about(conn, found["bucket"]) if found is not None else None
    if found is None or found["user_id"] != user_id or found["thread_id"]:
        raise HTTPException(status_code=409, detail="Another chat of Gamma's continues this session.")
    if found["bucket"] != bucket:
        where = (about or {}).get("title") or (about or {}).get("name") or ""
        raise HTTPException(status_code=409, detail=(
            f"The chat of {where} continues this session: open it there." if where
            else "The library's chat continues this session: open it there." if (about or {}).get("kind") == "library"
            else "Another chat of Gamma's continues this session."))
    return found if found["archived_at"] is None else chats.open_one(ws, found, label)


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
    """``{conversations: [{id, title, agent, updated_at, turns}], active,
    lent}``: the bucket's archived conversations of the account, newest
    first, and its active one as they are listed (null for none), whichever
    agent it is of: the chat's History lists it while another agent is
    picked; ``lent`` the chat conversations there of the co-authors the
    account's agents serve, active or archived, each with its ``person``
    (the co-author's username; ``chats.lent_history``), which the account
    reads, stops, answers and exports but never opens as its own or
    deletes. On every server and whatever became of its agents (one turned
    off, a computer gone): a session's alone."""
    from ..db import account_names, connect_pages_db, connect_users_db
    from ..local_agents import chats

    user_id = require_personal_user_id(request, SESSION_ONLY)
    ws = require_ws(request)
    with connect_pages_db(ws) as conn:
        conversation = chats.active(conn, bucket, user_id) if bucket else None
    lent = chats.lent_history(ws, bucket, user_id) if bucket else []
    with connect_users_db() as conn:
        names = account_names(conn, {c["user_id"] for c in lent}) if lent else {}
    return {"conversations": chats.history(ws, bucket, user_id) if bucket else [],
            "active": chats.listed(conversation),
            "lent": [{**{k: v for k, v in c.items() if k != "user_id"}, "person": names.get(c["user_id"], "")}
                     for c in lent]}


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
    """A conversation of the account's, active or archived, or of a
    co-author's with its agent (``chats.readable``), as a file to keep
    (gamma/chat_export.py, as ``GET /api/chat-history/{id}/export`` exports
    Gamma AI's), whatever became of its agent."""
    from fastapi.responses import Response

    from ..chat_export import export_file
    from ..db import connect_pages_db
    from ..local_agents import catalog
    from ..storage import attachment_disposition

    ws, conversation, _ = _conversation_of(request, conversation_id, seated=False, owner=True)
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
