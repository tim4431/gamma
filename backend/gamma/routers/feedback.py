"""The Sends that ask an agent to act on a work folder's comment threads
(gamma/annotations/requests.py, docs/dev/annotations.md "The routes"),
until the request layer goes (docs/research/agents-as-participants-plan.md,
step 3.7); the reader is routers/annotations.py.

Every route is the workspace's members' — any role — and integration
tokens' of any scope, a token in its own workspace as everywhere on the
HTTP API; a share visitor, signed in or not, is refused. A Send is made by
a member who may edit (or a write token): it starts work on the owner's
computer. Its state is set by an agent (a feedback or write token) or, to
withdraw it, by its requester. On the desktop app's own server a work
folder's vault may have an agent on this computer (docs/dev/local_agents.md):
it takes the Sends its maker makes in Gamma, so a token's Send there is
refused, and so is a token's change of a request it is answering. On any
server the same holds for a work folder a computer keeps with an agent
there (gamma/computers/registry.py ``agent_of``): its maker's Sends run
on that computer, taken over its channel (gamma/routers/computers.py). Every
handler is a sync ``def``: it reads the pages.db off the event loop.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import local_agents, workspaces
from ..auth import is_token, token_comments
from ..db import connect_pages_db
from ..annotations import entries, requests
from ..annotations.entries import AnnotationError
from ..integrations import display_name
from .annotations import member as _member, refusals as _refusals

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class RequestCreate(BaseModel):
    id: str = ""                    # the client's id, the same on every retry
    scope: str                      # a file's page id, or a work folder's or a directory's folder id
    note: str = ""                  # the line of instruction
    threads: list[str] | None = None  # some of the scope's open threads; omitted, every one


class RequestState(BaseModel):
    state: str
    detail: str = ""


def dialog_agent(ws: str, w: str, user_id: str) -> dict:
    """What the Feedback dialog reads beside a work folder's annotations
    (``GET /api/annotations``): on a server that runs agents ``agent``, the
    work folder's agent on this computer (``_agent_of``), and
    ``agents_found``, whether this computer has any; on any server, for a
    work folder its reader keeps on a computer with an agent there,
    ``agent`` is that one (``_computer_agent``)."""
    out: dict = {}
    if local_agents.enabled():
        from ..local_agents import catalog

        out["agent"] = _agent_of(ws, w, user_id)
        out["agents_found"] = any(catalog.cached(name)["found"] for name in catalog.AGENTS)
    if not out.get("agent"):
        far = _computer_agent(ws, w, user_id)
        if far is not None:
            out["agent"] = far
    return out


def _agent_of(ws: str, w: str, user_id: str) -> dict | None:
    """The agent here that takes ``user_id``'s Sends of the work folder
    ``w`` kept in a vault here (``runner.folder_agent``: its first agent on,
    else another account's), as the dialog says it: ``{name, label, ready,
    problem, takes, why}``, ``ready`` and ``problem`` as last asked
    (``catalog.cached``: nothing runs), ``takes`` whether a Send of
    ``user_id`` would reach it now (its own agent, the vault writing its
    directory), ``why`` in English when not; None for no agent. Until the
    dialog goes (docs/research/agents-as-participants-plan.md, step 3.7) a
    Send is taken by the first of the person's agents in the catalog's
    order."""
    from ..local_agents import catalog, runner

    found = runner.folder_agent(ws, w, user_id)
    if found is None:
        return None
    label = found["label"]
    why = ""
    if user_id != found["owner"]:
        why = f"Only the person who turned on {label} on this computer can send to it."
    elif not found["writable"]:
        why = f"The synced folder is paused on this computer: {label} starts once it syncs again."
    probe = catalog.cached(found["name"])
    return {"name": found["name"], "label": label, "ready": probe["ready"], "problem": probe["problem"],
            "takes": not why, "why": why}


def _computer_agent(ws: str, w: str, user_id: str) -> dict | None:
    """The agent of the computer that keeps the work folder ``w`` with one
    (gamma/computers/registry.py ``agent_of``: a work folder of this server
    whose Sends run on that computer), as the dialog says it to that
    computer's account alone (``user_id``'s own computer: another
    account's never answers for it): ``{name, label, computer, online,
    ready, problem, takes, why}``, ``computer`` its name, ``ready`` as the
    computer last said, ``problem`` while it is offline (a Send then waits
    for it) or, online, while the agent there is not ready (``_problem``),
    ``takes`` false with ``why`` while the folder is paused there; None for
    none."""
    from ..computers import registry

    found = registry.agent_of(ws, w, user_id)
    if found is None:
        return None
    label, computer = found["label"], found["computer"]
    why = f"The synced folder is paused on {computer}: {label} starts once it syncs again." if found["paused"] else ""
    problem = _problem(found) if found["online"] else f"{computer} is offline: this runs when it connects."
    return {"name": found["agent"], "label": label, "computer": computer, "online": found["online"],
            "ready": found["ready"], "problem": problem, "takes": not why, "why": why}


def _problem(found: dict) -> str:
    """Why the online computer's agent (``registry.agent_of``) cannot take
    a Send now, in the words the @ menu says it; "" while it is ready or
    not known to be otherwise."""
    if found["ready"] is not False:
        return ""
    label, computer = found["label"], found["computer"]
    if found["code"] == "signed_out":
        return f"{label} is not signed in on {computer}."
    if found["code"] == "missing":
        return f"{label} is not on {computer}."
    return f"{label} is not ready on {computer}."


@router.post("/requests", status_code=201)
def create_request(payload: RequestCreate, request: Request):
    """A Send → ``{request}`` (201; 200 for a repeated ``id`` of the same
    account, answered as stored). A member who may edit, or a write token.
    400 for a scope outside a work folder, nothing to send or a thread that
    is no open one in the scope; 403 for a token's Send to a work folder an
    agent on this computer answers, or a computer keeps with an agent (it
    takes the Sends made in Gamma); 404
    for no such scope; 409 for another account's id; 413 for a longer
    note."""
    ws = _member(request, write=True)
    with connect_pages_db(ws) as conn, _refusals():
        scope = entries.work_scope(conn, payload.scope)
        if is_token(request) and local_agents.enabled():
            from ..local_agents import runner

            if runner.folder_agent(ws, scope.work_folder, request.state.user_id) is not None:
                raise AnnotationError(403, "An agent on this computer takes Sends made in Gamma, not with a token.")
        if is_token(request):
            from ..computers import registry

            far = registry.agent_of(ws, scope.work_folder, request.state.user_id)   # the token's account's computer
            if far is not None:
                raise AnnotationError(403, f"{far['label']} on {far['computer']} takes Sends made in Gamma, "
                                         "not with a token.")
        out = requests.create(ws, conn, scope, by=request.state.user_id,
                              by_name=request.state.user or "", note=payload.note, threads=payload.threads,
                              request_id=payload.id, via=requests.TOKEN if is_token(request) else requests.SESSION)
    if not out["created"]:
        return JSONResponse(status_code=200, content={"request": out["request"]})
    return {"request": out["request"]}


def _local(ws: str, records: list[dict]) -> list[dict]:
    """On a server that runs agents, each record gains ``local``: whether an
    agent on this computer holds it now (so Withdraw stops it)."""
    if local_agents.enabled():
        from ..local_agents import runner

        for record in records:
            record["local"] = runner.running(ws, record["id"])
    return records


@router.get("/requests")
def list_requests(request: Request, after: int = 0, scope: str = "", state: str = "", limit: int = 50):
    """``{requests, cursor}``: the requests made or changed since the
    cursor ``after``, oldest first (``requests.listing``); ``scope`` a work
    folder's or a file's id, ``state`` one state, ``limit`` at most 100. On
    a server that runs agents a record says whether one of them holds it
    (``local``)."""
    ws = _member(request)
    with connect_pages_db(ws) as conn, _refusals():
        out = requests.listing(conn, after=after, scope=scope, state=state, limit=limit)
    _local(ws, out["requests"])
    return out


@router.get("/requests/{request_id}")
def read_request(request_id: str, request: Request):
    """``{request, entries}``: the request and the entries of its threads as
    they are now, what its file holds. 404 for no such request."""
    ws = _member(request)
    with connect_pages_db(ws) as conn:
        record = requests.get(conn, request_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no such request")
        found = requests.entries_of(ws, conn, record)
    _local(ws, [record])
    return {"request": record, "entries": [entries.with_urls(e) for e in found]}


def _not_answering_here(ws: str, request_id: str) -> None:
    """409 while an agent on this computer works on the request, or a
    computer's run holds it (gamma/routers/computers.py): that run writes
    the request's state, no one else's agent."""
    from ..local_agents import catalog

    if local_agents.enabled():
        from ..local_agents import runner, store

        if runner.running(ws, request_id):
            run = store.run_of(ws, request_id)
            label = catalog.label(run["agent"]) if run else "An agent"
            raise HTTPException(status_code=409, detail=f"{label} on this computer is answering this request.")
    with connect_pages_db(ws) as conn:
        run = requests.run_of(conn, request_id)
        state = conn.execute("SELECT state FROM feedback_requests WHERE id = ?",
                             (request_id,)).fetchone() if run and not run["ended"] else None
    if state and state[0] == "running":
        from ..computers import registry

        name = registry.computer_name(run["holder"]) or "a computer"
        raise HTTPException(status_code=409, detail=f"{catalog.label(run['agent'])} on {name} is answering this request.")


@router.post("/requests/{request_id}/state")
def set_request_state(request_id: str, payload: RequestState, request: Request):
    """Set a request's state → ``{request}``: an agent (an integration token
    that comments in the workspace, ``auth.token_comments``) any change
    ``requests.TRANSITIONS`` lets it make, the requester's session a
    withdraw (``failed``). 403 for
    anyone else, 404 for no such request, 409 for a change refused or a
    token's while an agent on this computer answers the request, 413 for a
    longer detail; 423 while a delete holds the workspace."""
    ws = _member(request)
    if is_token(request):
        if not token_comments(request):
            raise HTTPException(status_code=403, detail="this token is read-only")
        _not_answering_here(ws, request_id)
        writer, by_name = "agent", display_name(request.state.token_name)
    else:
        writer, by_name = "requester", request.state.user or ""
    workspaces.refuse_writes(ws)
    with connect_pages_db(ws) as conn, _refusals():
        by = requests.requester(conn, request_id)
        if by is None:
            raise AnnotationError(404, "no such request")
        if writer == "requester" and by != request.state.user_id:
            raise AnnotationError(403, "only an agent or the request's sender sets its state")
        return {"request": requests.set_state(ws, conn, request_id, payload.state, detail=payload.detail,
                                              by_name=by_name, writer=writer)}
