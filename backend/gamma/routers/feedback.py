"""Feedback on a work folder's files and the Sends that ask an agent to act
on it (gamma/feedback/, docs/dev/feedback.md "The routes").

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
handler is a sync ``def``: it reads the pages.db, and the pictures are
drawn, off the event loop.
"""

from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from .. import local_agents, workspaces
from ..auth import is_token, require_ws
from ..db import connect_pages_db
from ..feedback import entries, markdown, pictures, requests
from ..feedback.entries import FeedbackError
from ..integrations import display_name

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

FORMATS = ("json", "md")
MARKDOWN = "text/markdown; charset=utf-8"
PICTURE_CACHE = "private, max-age=86400"


class RequestCreate(BaseModel):
    id: str = ""                    # the client's id, the same on every retry
    scope: str                      # a file's page id, or a work folder's or a directory's folder id
    note: str = ""                  # the line of instruction
    threads: list[str] | None = None  # some of the scope's open threads; omitted, every one


class RequestState(BaseModel):
    state: str
    detail: str = ""


@contextmanager
def _refusals():
    try:
        yield
    except FeedbackError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


def _member(request: Request, *, write: bool = False) -> str:
    """The workspace of a member's or a token's request (``require_ws``);
    403 through a share link."""
    if request.query_params.get("share"):
        raise HTTPException(status_code=403, detail="feedback belongs to the workspace's members")
    return require_ws(request, write=write)


def _format(format: str) -> str:
    if format not in FORMATS:
        raise HTTPException(status_code=400, detail="format is json or md")
    return format


@router.get("")
def read_feedback(request: Request, scope: str = "", state: str = "open", format: str = "json"):
    """The feedback of a work folder, a directory or a file
    (``entries.document``, its entries with ``picture_url`` and
    ``before_url``; ``owner`` the requester's username), or with
    ``format=md`` its Markdown (``text/markdown``, the pictures as their
    routes). 400 for a library page or folder, another state or format;
    404 for no such folder or live page. On a server that runs agents the
    JSON has ``agent``, the work folder's agent on this computer for the
    Feedback dialog (``_agent_of``), and ``agents_found``, whether this
    computer has any; on any server, for a work folder its reader keeps on
    a computer with an agent there, ``agent`` is that one
    (``_computer_agent``). Never the files' document, which stays as it
    is."""
    ws = _member(request)
    _format(format)
    with connect_pages_db(ws) as conn, _refusals():
        doc = entries.document(ws, conn, entries.resolve_scope(conn, scope), state=state,
                               owner=request.state.user or "")
    doc["entries"] = [entries.with_urls(e) for e in doc["entries"]]
    if format == "md":
        return Response(markdown.render(doc, pictures="urls"), media_type=MARKDOWN)
    if local_agents.enabled():
        from ..local_agents import catalog

        doc["agent"] = _agent_of(ws, doc["work_folder"]["id"], request.state.user_id)
        doc["agents_found"] = any(catalog.cached(name)["found"] for name in catalog.AGENTS)
    if not doc.get("agent"):
        far = _computer_agent(ws, doc["work_folder"]["id"], request.state.user_id)
        if far is not None:
            doc["agent"] = far
    return doc


def _agent_of(ws: str, w: str, user_id: str) -> dict | None:
    """The agent of the work folder ``w``'s vault here, as the dialog says
    it: ``{name, label, ready, problem, takes, why}``, ``ready`` and
    ``problem`` as last asked (``catalog.cached``: nothing runs), ``takes``
    whether a Send of ``user_id`` would reach it now (the vault's maker,
    the vault writing its directory), ``why`` in English when not; None
    for no agent."""
    from ..local_agents import catalog, runner

    found = runner.folder_agent(ws, w)
    if found is None:
        return None
    label = found["label"]
    why = ""
    if user_id != found["maker"]:
        why = f"Only the person who keeps this folder on this computer can send to {label}."
    elif not found["writable"]:
        why = f"The work folder is paused on this computer: {label} starts once it syncs again."
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
    why = f"The work folder is paused on {computer}: {label} starts once it syncs again." if found["paused"] else ""
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


def _entry(ws: str, thread_id: str, regions: dict | None = None) -> dict:
    """The thread's entry in ``ws``; with ``regions`` (a picture's read)
    its pictures' regions, and no SyncTeX lookup. 404 for a thread hidden,
    gone or on no work folder's file."""
    with connect_pages_db(ws) as conn:
        found = entries.entry(ws, conn, thread_id, regions=regions, sources=regions is None)
    if found is None:
        raise HTTPException(status_code=404, detail="no feedback item has that id")
    return found


@router.get("/threads/{thread_id}")
def read_thread(thread_id: str, request: Request, format: str = "json"):
    """One thread's entry in any state → ``{entry}``, or its Markdown. 404
    for a thread hidden, gone or not on a work folder's file."""
    _format(format)
    found = entries.with_urls(_entry(_member(request), thread_id))
    if format == "md":
        return Response(f"## {found['file']}\n\n" + markdown.render_entry(found, pictures="urls"), media_type=MARKDOWN)
    return {"entry": found}


@router.get("/threads/{thread_id}/picture")
def thread_picture(thread_id: str, request: Request, at: str = "now", v: str = ""):
    """The JPEG of a thread's region: ``at=now`` as it is on the current
    file, ``before`` as it was commented on. ``v`` (the picture's key) only
    busts a browser's cache. 404 when the entry has no such picture, or it
    cannot be drawn now."""
    if at not in ("now", "before"):
        raise HTTPException(status_code=400, detail="at is now or before")
    return picture_of(_member(request), thread_id, at)


def picture_of(ws: str, thread_id: str, at: str) -> Response:
    """The JPEG of the region of the thread ``thread_id`` of ``ws``
    (``thread_picture``, and a computer's channel's for a thread of a
    request it holds, gamma/routers/computers.py). 400 for another ``at``,
    404 for no such entry or picture, or one that cannot be drawn now."""
    if at not in ("now", "before"):
        raise HTTPException(status_code=400, detail="at is now or before")
    regions: dict = {}
    found = _entry(ws, thread_id, regions)
    key = entries.key_of(found["picture" if at == "now" else "before"])
    if not key or key not in regions:
        raise HTTPException(status_code=404, detail="this feedback item has no such picture")
    page_id, region = regions[key]
    with connect_pages_db(ws) as conn:
        data = pictures.cached(ws, conn, page_id, region)
    if not data:
        raise HTTPException(status_code=404, detail="this picture cannot be drawn now")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": PICTURE_CACHE})


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
        scope = entries.resolve_scope(conn, payload.scope)
        if is_token(request) and local_agents.enabled():
            from ..local_agents import runner

            if runner.folder_agent(ws, scope.work_folder) is not None:
                raise FeedbackError(403, "An agent on this computer takes Sends made in Gamma, not with a token.")
        if is_token(request):
            from ..computers import registry

            far = registry.agent_of(ws, scope.work_folder, request.state.user_id)   # the token's account's computer
            if far is not None:
                raise FeedbackError(403, f"{far['label']} on {far['computer']} takes Sends made in Gamma, "
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
    of the feedback or write scope) any change ``requests.TRANSITIONS``
    lets it make, the requester's session a withdraw (``failed``). 403 for
    anyone else, 404 for no such request, 409 for a change refused or a
    token's while an agent on this computer answers the request, 413 for a
    longer detail; 423 while a delete holds the workspace."""
    ws = _member(request)
    if is_token(request):
        if request.state.token_scope not in ("feedback", "write"):
            raise HTTPException(status_code=403, detail="this token is read-only")
        _not_answering_here(ws, request_id)
        writer, by_name = "agent", display_name(request.state.token_name)
    else:
        writer, by_name = "requester", request.state.user or ""
    workspaces.refuse_writes(ws)
    with connect_pages_db(ws) as conn, _refusals():
        by = requests.requester(conn, request_id)
        if by is None:
            raise FeedbackError(404, "no such request")
        if writer == "requester" and by != request.state.user_id:
            raise FeedbackError(403, "only an agent or the request's sender sets its state")
        return {"request": requests.set_state(ws, conn, request_id, payload.state, detail=payload.detail,
                                              by_name=by_name, writer=writer)}
